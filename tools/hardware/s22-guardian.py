#!/usr/bin/env python3
"""s22-guardian: thermal, runaway-process and resource guard for the S22 phone.

Native (Alpine) root, as root. Supervised by s22-keepalive (service
"guardian"). One pass every INTERVAL_S (default 20 s); a pass only reads /proc
and /sys unless something is wrong, so it costs well under 1 % of a core.

Thermal (max of the BIG/MID/LITTLE/G3D/ISP zones), with hysteresis:
  level 1  >= warn_c (60)      log + alert
  level 2  >= hot_c (70)       hold + stop the :8090 GPU model ("llama"), renice
                               background processes to 19
  level 3  >= critical_c (80)  also hold + stop the other non-essential services
                               (unumsearch); OpenUnum, phoned, modem, sshd,
                               tailscale, keepalive and the UI are never touched
  Levels 2 and 3 need confirm_passes (2) consecutive passes, so a one-second
  spike of a single busy core does not stop services. The level only goes up while hot; everything is restored (holds removed,
  nice values put back) once the max drops below restore_c (55).
  A hold is a file /run/s22-hold/<service>: s22-keepalive leaves held
  services alone, so it does not restart what the guardian stopped.

Runaway processes: a process that is not allowlisted and not a kernel thread,
using > runaway_cpu_pct of one core continuously for runaway_s (30 min), or an
orphan (PPID 1) doing so for orphan_s (10 min), is sent SIGTERM, then SIGKILL,
by its exact PID. If it survives SIGKILL in state R or D (the close_range
kernel spin seen 2026-10-10), the guardian alerts and recommends
`s22-reboot recovery`; it never reboots by itself.

Memory / disk floors: MemAvailable < mem_alert_mb alerts; < mem_shed_mb also
holds + stops the GPU model. Free space under disk_floors_mb (per mount)
alerts, at most every disk_every_s.

Outputs:
  /run/s22-guardian/status.json             full status
  $CHROOT/run/s22-touch/guardian.env        key=value lines for the touch
                                            shell's status strip
  /srv/s22/state/guardian/guardian.jsonl    event log (rotated at 1 MiB)
  alerts (rate-limited): a card on the phone (s22-ui card) and a report in the
  work log of project unumS22linux in the phone's OpenUnum (loopback; the
  rig's OpenUnum refuses work-log reports from non-loopback non-admin callers).

  s22-guardian serve     run the loop
  s22-guardian --once    one pass, print the status (no actions unless --act)
  s22-guardian release   remove every guardian hold and restore nice values
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

STATE = Path('/srv/s22/state/guardian')
RUN = Path('/run/s22-guardian')
HOLD_DIR = Path('/run/s22-hold')
CHROOT = Path('/mnt/omarchy-trial')
LOCK = Path('/run/s22-guardian.lock')
LOG_MAX = 1 << 20
HZ = os.sysconf('SC_CLK_TCK')

DEFAULTS = {
    'interval_s': 20,
    'zones': ['BIG', 'MID', 'LITTLE', 'G3D', 'ISP'],
    'warn_c': 60, 'hot_c': 70, 'critical_c': 80, 'restore_c': 55, 'confirm_passes': 2,
    # services the guardian may stop: keepalive name -> ps needle
    'hot_stop': {'llama': 'llama-server -m /models/Qwen3.5-0.8B-Q4_0.gguf'},
    'critical_stop': {'unumsearch': 'unumsearch --config /srv/s22/unumsearch/config.toml serve'},
    'background': ['unumsearch --config', 'llama-server -m'],
    'runaway_cpu_pct': 80, 'runaway_s': 1800, 'orphan_s': 600,
    # cmdline substrings never treated as runaway (compositor, UI, agent, radio, network)
    'allowlist': ['sway', 'Hyprland', 'quickshell', 'squeekboard', 'node src/server.mjs',
                  'llama-server', 's22-keepalive', 's22-guardian', 's22-touchd', 's22-phoned',
                  's22-buttons', 's22-converse', 's22-assistant', 'start-persistent-desktop',
                  'sshd', 'tailscaled', 'cbd', 'rild', 'unumsearch', 'seatd', 'dbus-daemon',
                  'native-guardian', 'foot', 'pi'],
    'mem_alert_mb': 400, 'mem_shed_mb': 250,
    # free-space floors in MB; the native root overlay (/) is small and normally ~20 MB free
    'disk_floors_mb': {'/': 10, '/srv/s22': 3072}, 'disk_every_s': 21600,
    'alert_every_s': 900,
    'card': True,
    # the phone's own OpenUnum (loopback; the rig's work log needs an admin login)
    'worklog_url': 'http://127.0.0.1:18880/api/projects/worklog/report',
    'worklog_project': 'unumS22linux',
}


def load_config(state: Path = STATE) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        cfg.update(json.loads((state / 'config.json').read_text()))
    except (OSError, ValueError):
        pass
    return cfg


# ------------------------------------------------------------------ readers

def read_temps(base: Path = Path('/sys/class/thermal'), zones=DEFAULTS['zones']) -> dict[str, float]:
    out = {}
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return out
    for z in entries:
        if not z.name.startswith('thermal_zone'):
            continue
        try:
            t = (z / 'type').read_text().strip()
            if t in zones:
                out[t] = int((z / 'temp').read_text()) / 1000
        except (OSError, ValueError):
            pass
    return out


def proc_table(proc: Path = Path('/proc')) -> dict[int, dict]:
    """pid -> {ppid, state, ticks, cmd, kthread}."""
    out = {}
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            raw = (d / 'stat').read_text()
            comm = raw[raw.index('(') + 1:raw.rindex(')')]
            f = raw[raw.rindex(')') + 2:].split()
            cmd = (d / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()
        except (OSError, ValueError):
            continue
        ppid = int(f[1])
        flags = int(f[6])
        out[int(d.name)] = {'ppid': ppid, 'state': f[0], 'ticks': int(f[11]) + int(f[12]),
                            'comm': comm, 'cmd': cmd or f'[{comm}]',
                            'kthread': bool(flags & 0x00200000) or ppid == 2 or int(d.name) == 2}
    return out


def mem_available_mb(path: Path = Path('/proc/meminfo')) -> float | None:
    try:
        for line in path.read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        return None
    return None


def disk_free_mb(path: str) -> float | None:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    return st.f_bavail * st.f_frsize / (1 << 20)


def pids_matching(needle: str, table: dict[int, dict]) -> list[int]:
    me = os.getpid()
    return [p for p, r in table.items() if needle in r['cmd'] and p != me and not r['kthread']]


# ------------------------------------------------------------------ the guard

class Guardian:
    def __init__(self, cfg: dict, state: Path = STATE, run: Path = RUN, hold_dir: Path = HOLD_DIR,
                 chroot: Path = CHROOT, notify=None, killer=os.kill, renicer=None, clock=time.time):
        self.cfg, self.state, self.run_dir, self.hold_dir, self.chroot = cfg, state, run, hold_dir, chroot
        self.notify = notify or self.default_notify
        self.kill, self.clock = killer, clock
        self.renice = renicer or self.default_renice
        self.level = 0
        self.held: set[str] = set()
        self.reniced: dict[int, int] = {}       # pid -> original nice
        self.prev: dict[int, tuple[float, int]] = {}  # pid -> (time, ticks)
        self.busy_since: dict[int, float] = {}
        self.killed: dict[int, float] = {}
        self.stuck: set[int] = set()
        self.last_alert: dict[str, float] = {}
        self.shed_mem = False
        for d in (state, run):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        try:      # nice values changed by a previous instance (restored on release)
            self.reniced = {int(k): v for k, v in json.loads((run / 'reniced.json').read_text()).items()}
        except (OSError, ValueError):
            pass

    def save_reniced(self) -> None:
        try:
            (self.run_dir / 'reniced.json').write_text(json.dumps(self.reniced))
        except OSError:
            pass

    # -- plumbing
    def log(self, **rec) -> None:
        rec['t'] = round(self.clock(), 3)
        path = self.state / 'guardian.jsonl'
        try:
            if path.exists() and path.stat().st_size > LOG_MAX:
                os.replace(path, path.with_suffix('.jsonl.1'))
            with open(path, 'a') as f:
                f.write(json.dumps(rec, sort_keys=True) + '\n')
        except OSError:
            pass

    def alert(self, key: str, title: str, body: str, force: bool = False) -> bool:
        now = self.clock()
        if not force and now - self.last_alert.get(key, -1e9) < self.cfg['alert_every_s']:
            return False
        self.last_alert[key] = now
        self.log(event='alert', key=key, title=title, body=body)
        try:
            self.notify(title, body)
        except Exception as e:  # noqa: BLE001 - alerts must never kill the loop
            self.log(event='notify_error', error=f'{type(e).__name__}: {e}')
        return True

    def default_notify(self, title: str, body: str) -> None:
        if self.cfg.get('card'):
            subprocess.run(['chroot', str(self.chroot), '/usr/local/bin/s22-ui', 'card', title[:80], body[:400],
                            '--ttl', '600'], capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
        url = self.cfg.get('worklog_url')
        if url:
            data = json.dumps({'project': self.cfg['worklog_project'], 'agent': 's22-guardian',
                               'status': 'progress', 'title': title, 'summary': body}).encode()
            req = urllib.request.Request(url, data=data, headers={'content-type': 'application/json'})
            urllib.request.urlopen(req, timeout=10).read()

    def default_renice(self, pid: int, value: int | None) -> int | None:
        """Set nice (None = only read); returns the previous value."""
        try:
            old = os.getpriority(os.PRIO_PROCESS, pid)
            if value is not None:
                os.setpriority(os.PRIO_PROCESS, pid, value)
            return old
        except OSError:
            return None

    # -- service holds
    def hold(self, name: str, needle: str, table: dict) -> list[int]:
        try:
            self.hold_dir.mkdir(parents=True, exist_ok=True)
            (self.hold_dir / name).write_text('s22-guardian\n')
        except OSError as e:
            self.log(event='hold_error', service=name, error=str(e))
            return []
        self.held.add(name)
        pids = pids_matching(needle, table)
        for pid in pids:
            try:
                self.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        self.log(event='hold', service=name, stopped=pids)
        return pids

    def release_all(self) -> None:
        for name in sorted(self.held | {p.name for p in self.hold_dir.glob('*')
                                        if self._ours(p)}):
            try:
                (self.hold_dir / name).unlink()
            except OSError:
                pass
            self.log(event='release', service=name)
        self.held.clear()
        for pid, nice in list(self.reniced.items()):
            self.renice(pid, nice)
            self.log(event='renice_restore', pid=pid, nice=nice)
        self.reniced.clear()
        self.save_reniced()

    @staticmethod
    def _ours(p: Path) -> bool:
        try:
            return p.read_text().strip() == 's22-guardian'
        except OSError:
            return False

    # -- thermal
    def thermal(self, temps: dict[str, float], table: dict) -> None:
        tmax = max(temps.values()) if temps else 0.0
        c = self.cfg
        want = 3 if tmax >= c['critical_c'] else 2 if tmax >= c['hot_c'] else 1 if tmax >= c['warn_c'] else 0
        # One busy X2 core spikes BIG from ~45 to 80 C within a second (seen on
        # the phone 2026-10-10), so stopping services (level >= 2) needs the
        # level on confirm_passes consecutive passes; logging/alerts do not wait.
        self.want_hist = (getattr(self, 'want_hist', []) + [want])[-int(c.get('confirm_passes', 2)):]
        if want >= 2 and len(self.want_hist) >= int(c.get('confirm_passes', 2)):
            want = min(self.want_hist)
        elif want >= 2:
            want = 1
        if want > self.level:
            self.level = want
            self.log(event='thermal_level', level=want, tmax=tmax, temps=temps)
            if want >= 1:
                self.alert(f'thermal{want}', f'S22 hot: {tmax:.0f} °C (level {want})',
                           f'zones {temps}. ' + {1: 'Watching.', 2: 'GPU model stopped, background reniced.',
                                                 3: 'Non-essential services stopped.'}[want], force=True)
        elif self.level and tmax < c['restore_c']:
            self.log(event='thermal_restore', from_level=self.level, tmax=tmax)
            self.level = 0
            self.release_all()
            self.alert('thermal0', f'S22 cooled: {tmax:.0f} °C', 'Services released.', force=True)
            return
        if (self.level == 0 and not self.shed_mem and tmax < c['restore_c']
                and (self.reniced or any(self._ours(p) for p in self.hold_dir.glob('*')))):
            self.log(event='stale_holds_released', tmax=tmax)     # e.g. after a guardian restart
            self.release_all()
        if self.level >= 2:
            for name, needle in c['hot_stop'].items():
                if name not in self.held or pids_matching(needle, table):
                    self.hold(name, needle, table)
            for needle in c['background']:
                for pid in pids_matching(needle, table):
                    if pid not in self.reniced:
                        old = self.renice(pid, 19)
                        if old is not None:
                            self.reniced[pid] = old
                            self.save_reniced()
        if self.level >= 3:
            for name, needle in c['critical_stop'].items():
                if name not in self.held or pids_matching(needle, table):
                    self.hold(name, needle, table)

    # -- runaway processes
    def allowed(self, rec: dict) -> bool:
        cmd = rec['cmd']
        head = cmd.split(' ')[0].rsplit('/', 1)[-1] if cmd else ''
        for a in self.cfg['allowlist']:
            if ' ' in a or '/' in a:
                if a in cmd:
                    return True
            elif head == a or rec['comm'] == a or f'/{a} ' in f'{cmd} ' or f' {a} ' in f' {cmd} ':
                return True
        return False

    def runaway(self, table: dict, now: float) -> list[int]:
        c = self.cfg
        acted = []
        seen = set()
        for pid, rec in table.items():
            seen.add(pid)
            prev = self.prev.get(pid)
            self.prev[pid] = (now, rec['ticks'])
            if rec['kthread'] or pid in (1, os.getpid()) or prev is None:
                continue
            dt = now - prev[0]
            if dt <= 0:
                continue
            pct = (rec['ticks'] - prev[1]) * 100 / HZ / dt
            if pct < c['runaway_cpu_pct'] or self.allowed(rec):
                self.busy_since.pop(pid, None)
                continue
            since = self.busy_since.setdefault(pid, prev[0])
            limit = c['orphan_s'] if rec['ppid'] == 1 else c['runaway_s']
            if now - since < limit:
                continue
            if pid in self.killed:
                if now - self.killed[pid] > 15 and pid not in self.stuck:
                    try:
                        self.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                    if rec['state'] in ('R', 'D') and now - self.killed[pid] > 45:
                        self.stuck.add(pid)
                        self.alert(f'stuck{pid}', f'S22: unkillable process {pid}',
                                   f'{rec["cmd"][:200]} spins in state {rec["state"]} after SIGKILL '
                                   '(kernel close_range spin?). Recommend: s22-reboot recovery.', force=True)
                continue
            self.killed[pid] = now
            try:
                self.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            acted.append(pid)
            self.log(event='runaway_term', pid=pid, cpu_pct=round(pct, 1), busy_s=round(now - since),
                     orphan=rec['ppid'] == 1, cmd=rec['cmd'][:200])
            self.alert(f'runaway{pid}', f'S22: stopped runaway process {pid}',
                       f'{rec["cmd"][:200]} used {pct:.0f} % of a core for {(now - since) / 60:.0f} min.', force=True)
        for pid in list(self.prev):
            if pid not in seen:
                for d in (self.prev, self.busy_since, self.killed):
                    d.pop(pid, None)
                self.stuck.discard(pid)
                self.reniced.pop(pid, None)
        return acted

    # -- floors
    def floors(self, mem_mb: float | None, disks: dict[str, float | None], table: dict) -> None:
        c = self.cfg
        if mem_mb is not None:
            if mem_mb < c['mem_shed_mb'] and not self.shed_mem:
                self.shed_mem = True
                for name, needle in c['hot_stop'].items():
                    self.hold(name, needle, table)
                self.alert('mem_shed', f'S22 memory low: {mem_mb:.0f} MB',
                           'GPU model stopped to free memory.', force=True)
            elif mem_mb < c['mem_alert_mb']:
                self.alert('mem', f'S22 memory low: {mem_mb:.0f} MB available', 'Watching.')
            elif self.shed_mem and mem_mb > c['mem_alert_mb'] * 2 and self.level < 2:
                self.shed_mem = False
                self.release_all()
                self.log(event='mem_restore', mem_mb=mem_mb)
        for path, free in disks.items():
            floor = c['disk_floors_mb'].get(path)
            if free is not None and floor is not None and free < floor:
                if self.clock() - self.last_alert.get(f'disk{path}', -1e9) >= c['disk_every_s']:
                    self.alert(f'disk{path}', f'S22 disk {path} low: {free:.0f} MB free',
                               'Clean up logs/models.', force=True)

    # -- one pass
    def check_once(self, act: bool = True, now: float | None = None, temps=None, table=None,
                   mem=None, disks=None) -> dict:
        now = self.clock() if now is None else now
        temps = read_temps(zones=self.cfg['zones']) if temps is None else temps
        table = proc_table() if table is None else table
        mem = mem_available_mb() if mem is None else mem
        disks = {d: disk_free_mb(d) for d in self.cfg['disk_floors_mb']} if disks is None else disks
        acted = []
        if act:
            self.thermal(temps, table)
            acted = self.runaway(table, now)
            self.floors(mem, disks, table)
        tmax = max(temps.values()) if temps else None
        status = {'t': round(now), 'level': self.level, 'tmax': tmax, 'temps': temps,
                  'mem_available_mb': round(mem) if mem is not None else None,
                  'disk_free_mb': {k: (round(v) if v is not None else None) for k, v in disks.items()},
                  'held': sorted(self.held), 'reniced': sorted(self.reniced), 'runaway_terminated': acted,
                  'stuck': sorted(self.stuck)}
        self.write_status(status)
        return status

    def write_status(self, status: dict) -> None:
        try:
            tmp = self.run_dir / 'status.json.tmp'
            tmp.write_text(json.dumps(status, sort_keys=True) + '\n')
            os.replace(tmp, self.run_dir / 'status.json')
        except OSError:
            pass
        env = self.chroot / 'run/s22-touch'
        if env.is_dir():
            msg = 'stuck process' if status['stuck'] else ('hot' if status['level'] >= 2 else '')
            lines = [f'g_level={status["level"]}', f'g_tmax={status["tmax"] or 0:.0f}',
                     f'g_mem={status["mem_available_mb"] or 0}', f'g_msg={msg}']
            try:
                tmp = env / 'guardian.env.tmp'
                tmp.write_text('\n'.join(lines) + '\n')
                os.replace(tmp, env / 'guardian.env')
            except OSError:
                pass

    def serve(self) -> None:
        self.log(event='start', pid=os.getpid(), cfg=self.cfg)
        while True:
            try:
                self.check_once()
            except Exception as e:  # noqa: BLE001 - keep guarding
                self.log(event='pass_error', error=f'{type(e).__name__}: {e}')
            time.sleep(self.cfg['interval_s'])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', nargs='?', default='serve', choices=['serve', 'release'])
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--act', action='store_true', help='with --once: apply actions')
    a = ap.parse_args(argv)
    g = Guardian(load_config())
    if a.cmd == 'release':
        g.release_all()
        print('released')
        return 0
    if a.once:
        print(json.dumps(g.check_once(act=a.act), indent=1, sort_keys=True))
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('s22-guardian already running', file=sys.stderr)
        return 1
    g.serve()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
