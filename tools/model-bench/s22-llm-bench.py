#!/usr/bin/env python3
"""s22-llm-bench: reproducible on-phone LLM benchmark for the S22's local model (stdlib only).

Runs ON THE PHONE (native Alpine root, as root). Brings the 2026-10-11 benchmark harness (which lived only on
the rig, ~/s22-bench/phone/bench.py) into the repo, adds the prefill-thread (-tb) dimension and a "turn"
workload shaped like the real local chat turn, and fixes the clock-cap restore incident of that run (it saved
the proxy's 1.728 GHz cap as "original" and restored it). Every result is one JSON line.

  s22-llm-bench plan    [--matrix FILE] [--configs a,b] [--workloads bench,turn]   print the jobs, run nothing
  s22-llm-bench run     [--matrix FILE] [--configs a,b] [--workloads bench,turn] [--out results.jsonl]
  s22-llm-bench report  results.jsonl [--max-temp 66]      best config + the s22-llama-ondemand setting

Workloads
  bench  llama-bench -p 512 -n 64 -t <threads>[,<threads_batch>]: prefill and decode tok/s per thread count.
  turn   llama-server with the config's -t/-tb/cpus/cap: one warm-up of a stable ~700-token prefix (the lean
         profile's size), then TURNS requests that share it, each with a new ~SUFFIX-token user message and
         MAX_TOKENS of answer: prompt ms (= time to first token), prompt and decode tok/s from llama-server's
         own timings, peak temperature. This is what the owner waits for in "Local (Gemma E2B)".

Safety (all enforced, not advisory)
  * refuses to start while cpu4/cpu7 run below cpuinfo_max_freq (the local model's own cap is active, or a
    previous run did not restore): unload the local model first; restores exactly what it wrote;
  * holds /run/s22-hold/llama for the run (s22-llama-ondemand answers 503 and OpenUnum falls back to the
    cloud model, so two llama processes never compete) and removes it only if the content is still its own;
  * waits for both CPU zones below --cool (50 C) before every job, aborts a job at --abort-temp (66 C) or
    MemAvailable < 450 MB, kills only the exact PID it started.

Configs (JSON, --matrix): {"name": {"cpus": [4,5,6], "threads": 2, "threads_batch": 3 | null,
  "cap_khz": 1800000 | null, "extra": [...]}}. The built-in matrix covers the production cool profile and the
  -tb candidates; "binary" overrides the llama-server/llama-bench names (e.g. a KleidiAI build).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

EXEC = ['/usr/bin/python3', '/srv/s22/hardware/bin/s22-gpu-exec-isolated',
        '/srv/s22/gpu-compat-20260921/llama-vulkan', '--bridge-v4']
MODEL = '/models/gemma-4-E2B-it-qat-q4_0-heretic-Q4_0_PURE.gguf'        # inside the isolated root
SERVER_BIN = '/system/bin/llama-server-b30266'
BENCH_BIN = '/system/bin/llama-bench-b30266'
PORT = 8197                         # not 8090/8091 (local model) nor 8095 (another phone service)
HOLD = Path('/run/s22-hold/llama')
CPUFREQ = '/sys/devices/system/cpu/cpu%d/cpufreq/%s'
ZONES = {'BIG': '/sys/class/thermal/thermal_zone0/temp', 'MID': '/sys/class/thermal/thermal_zone1/temp'}
MIN_MEMAVAIL_MB = 450

MATRIX = {
    # production cool profile (s22-llama-ondemand): cores 4-6, 2 threads, cpu4 cluster capped at 1.8 GHz
    'cool2':      {'cpus': [4, 5, 6], 'threads': 2, 'threads_batch': None, 'cap_khz': 1800000},
    'cool2-tb3':  {'cpus': [4, 5, 6], 'threads': 2, 'threads_batch': 3, 'cap_khz': 1800000},
    'cool3':      {'cpus': [4, 5, 6], 'threads': 3, 'threads_batch': None, 'cap_khz': 1800000},
    'mid2-tb3':   {'cpus': [4, 5, 6], 'threads': 2, 'threads_batch': 3, 'cap_khz': None},
    'big2-tb4':   {'cpus': [4, 5, 6, 7], 'threads': 2, 'threads_batch': 4, 'cap_khz': 1800000},
    # the same llama.cpp f2cc7282 with Arm KleidiAI Q4_0 kernels, CPU-only (staged next to the production
    # binaries under these names; see README "Local model speed")
    'cool2-kleidi':     {'cpus': [4, 5, 6], 'threads': 2, 'threads_batch': None, 'cap_khz': 1800000,
                         'server_binary': '/system/bin/llama-server-kleidi',
                         'bench_binary': '/system/bin/llama-bench-kleidi'},
    'cool2-tb3-kleidi': {'cpus': [4, 5, 6], 'threads': 2, 'threads_batch': 3, 'cap_khz': 1800000,
                         'server_binary': '/system/bin/llama-server-kleidi',
                         'bench_binary': '/system/bin/llama-bench-kleidi'},
}
WORKLOADS = ('bench', 'turn')


# ---------------------------------------------------------------- pure helpers (host-tested)

def load_matrix(path: str | None) -> dict:
    m = json.loads(Path(path).read_text()) if path else dict(MATRIX)
    for name, c in m.items():
        if not c.get('cpus') or int(c.get('threads', 0)) < 1:
            raise ValueError(f'config {name}: cpus and threads >= 1 are required')
        tb = c.get('threads_batch')
        if tb is not None and not 1 <= int(tb) <= len(c['cpus']):
            raise ValueError(f'config {name}: threads_batch must be 1..len(cpus)')
    return m


def bench_argv(c: dict, model: str = MODEL, pp: int = 512, tg: int = 64) -> list[str]:
    ts = sorted({int(c['threads']), int(c.get('threads_batch') or c['threads'])})
    return EXEC + [c.get('bench_binary', BENCH_BIN), '-m', model, '-t', ','.join(map(str, ts)),
                   '-p', str(pp), '-n', str(tg), '-r', '2', '-o', 'jsonl', '-dev', 'none'] + list(c.get('extra', []))


def server_argv(c: dict, model: str = MODEL, ctx: int = 8192, port: int = PORT) -> list[str]:
    argv = EXEC + [c.get('server_binary', SERVER_BIN), '-m', model, '-dev', 'none', '-ngl', '0',
                   '-t', str(c['threads']), '-c', str(ctx), '-np', '1', '-fa', 'on', '--cache-ram', '0',
                   '--cache-reuse', '256', '--host', '127.0.0.1', '--port', str(port), '--no-webui', '--jinja']
    if c.get('threads_batch'):
        argv += ['-tb', str(c['threads_batch'])]
    return argv + list(c.get('extra', []))


def caps_ok(read=None, cpus=(4, 7)) -> tuple[bool, dict]:
    """Every policy max at its hardware max (nobody holds a cap): (ok, {cpu: (scaling, cpuinfo)})."""
    read = read or (lambda p: Path(p).read_text().strip())
    seen = {}
    for cpu in cpus:
        try:
            seen[cpu] = (int(read(CPUFREQ % (cpu, 'scaling_max_freq'))), int(read(CPUFREQ % (cpu, 'cpuinfo_max_freq'))))
        except (OSError, ValueError):
            return False, seen
    return all(s >= h for s, h in seen.values()), seen


def system_info(text: str) -> str | None:
    """llama.cpp's 'system_info:' line (CPU features: NEON, MATMUL_INT8, REPACK, KLEIDIAI ...)."""
    for line in str(text).splitlines():
        if 'system_info' in line or 'MATMUL_INT8' in line:
            return line.strip()[-400:]
    return None


def bench_rows(stdout: str) -> list[dict]:
    rows = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            rows.append({k: r.get(k) for k in ('n_prompt', 'n_gen', 'n_threads', 'avg_ts', 'stddev_ts', 'cpu_info')})
    return rows


def summarize(records: list[dict]) -> dict:
    """config -> {pp, tg, turn_prompt_ms, turn_decode_tps, peak_c, ok}. pp = prefill at the config's prefill
    thread count, tg = decode at its decode thread count (from the bench rows)."""
    out: dict = {}
    for r in records:
        c = r.get('config', {})
        s = out.setdefault(r['name'], {'ok': True, 'peak_c': 0.0})
        s['ok'] = s['ok'] and r.get('status') == 'ok'
        s['peak_c'] = max(s['peak_c'], float(r.get('peak_c') or 0))
        if r.get('workload') == 'bench':
            t_pp = int(c.get('threads_batch') or c.get('threads', 0))
            t_tg = int(c.get('threads', 0))
            for row in r.get('rows', []):
                if row.get('n_prompt') and row.get('n_threads') == t_pp:
                    s['pp'] = row.get('avg_ts')
                if row.get('n_gen') and row.get('n_threads') == t_tg:
                    s['tg'] = row.get('avg_ts')
        elif r.get('workload') == 'turn' and r.get('turns'):
            s['turn_prompt_ms'] = statistics.median(t['prompt_ms'] for t in r['turns'])
            s['turn_decode_tps'] = statistics.median(t['predicted_per_second'] for t in r['turns'])
            s['answer_sha'] = r.get('answer_sha')
    base = out.get('cool2', {}).get('answer_sha')
    for s in out.values():                         # greedy answer differs from production: check quality first
        s['answer_differs'] = bool(base and s.get('answer_sha') and s['answer_sha'] != base)
    return out


def recommend(summary: dict, baseline: str = 'cool2', max_temp: float = 66.0, min_decode_frac: float = 0.95):
    """The config with the shortest turn prompt time (else the highest prefill) that stayed under max_temp,
    finished every job, and decodes at least min_decode_frac of the baseline. -> (name or None, reason)."""
    base = summary.get(baseline, {})
    base_tg = base.get('turn_decode_tps') or base.get('tg') or 0
    ok = {}
    for name, s in summary.items():
        if not s.get('ok') or s.get('peak_c', 0) >= max_temp:
            continue
        tg = s.get('turn_decode_tps') or s.get('tg') or 0
        if base_tg and tg < min_decode_frac * base_tg:
            continue
        if s.get('turn_prompt_ms') is None and s.get('pp') is None:
            continue
        ok[name] = s
    if not ok:
        return None, 'no config finished under the temperature limit'
    if all(s.get('turn_prompt_ms') is not None for s in ok.values()):
        best = min(ok, key=lambda n: ok[n]['turn_prompt_ms'])
        return best, f"shortest turn prompt time {ok[best]['turn_prompt_ms']:.0f} ms"
    best = max(ok, key=lambda n: ok[n].get('pp') or 0)
    return best, f"highest prefill {ok[best].get('pp')} tok/s"


def ondemand_setting(c: dict) -> str:
    """What to set on the phone for s22-llama-ondemand (it pins cores 4-6, -t 2, cap 1.8 GHz itself)."""
    tb = c.get('threads_batch')
    if c.get('cpus') != [4, 5, 6] or int(c.get('threads', 0)) != 2 or c.get('cap_khz') != 1800000:
        return 'needs a cool-profile change in s22-llama-ondemand (COOL_CPUS/COOL_THREADS/MID_CAP_KHZ), review first'
    if not tb or int(tb) == 2:
        return 'keep the default (no -tb): rm -f /srv/s22/state/llama-ondemand/threads-batch'
    return f'echo {int(tb)} > /srv/s22/state/llama-ondemand/threads-batch   (read at the next model load)'


# ---------------------------------------------------------------- device side

def temps() -> dict:
    out = {}
    for name, path in ZONES.items():
        try:
            out[name] = int(Path(path).read_text()) / 1000
        except (OSError, ValueError):
            out[name] = -1.0
    return out


def memavail_mb() -> int:
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) // 1024
    return -1


def wait_cool(target: float, timeout_s: float = 900) -> float:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if max(temps().values()) < target:
            return round(time.time() - t0, 1)
        time.sleep(5)
    raise RuntimeError(f'did not cool below {target} C in {timeout_s} s')


class Hold:
    """/run/s22-hold/llama for the run; removed only if it still holds our marker."""

    def __init__(self, path: Path = HOLD):
        self.path, self.mark = path, f's22-llm-bench {os.getpid()}\n'

    def __enter__(self):
        if self.path.exists():
            raise RuntimeError(f'{self.path} is already held: {self.path.read_text()[:80]!r}')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.mark)
        return self

    def __exit__(self, *exc):
        try:
            if self.path.read_text() == self.mark:
                self.path.unlink()
        except OSError:
            pass


class Cap:
    """Write the config's cluster cap, restore exactly the value read before writing."""

    def __init__(self, cap_khz, cpus):
        self.cap, self.saved = cap_khz, {}
        self.cpus = sorted({4 if c in (4, 5, 6) else c for c in cpus if c >= 4})

    def __enter__(self):
        if self.cap:
            for cpu in self.cpus:
                p = CPUFREQ % (cpu, 'scaling_max_freq')
                self.saved[p] = Path(p).read_text().strip()
                Path(p).write_text(str(self.cap))
        return self

    def __exit__(self, *exc):
        for p, v in self.saved.items():
            Path(p).write_text(v)


def guarded(argv: list[str], cpus, abort_c: float, timeout_s: float, on_start=None) -> dict:
    """Run argv pinned to cpus; sample temperature/memory every second; kill only this PID on a limit."""
    peak, minmem, status = 0.0, memavail_mb(), 'ok'
    t0 = time.time()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         preexec_fn=lambda: os.sched_setaffinity(0, set(cpus)))
    out, err = [], []
    readers = [threading.Thread(target=lambda: out.append(p.stdout.read()), daemon=True),
               threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)]
    for r in readers:
        r.start()
    result = None
    if on_start is not None:
        result = on_start(p)
    while p.poll() is None:
        t = temps()
        peak = max(peak, *t.values())
        minmem = min(minmem, memavail_mb())
        if peak > abort_c:
            status = f'aborted-thermal-{peak:.0f}'
        elif minmem < MIN_MEMAVAIL_MB:
            status = 'aborted-lowmem'
        elif time.time() - t0 > timeout_s:
            status = 'timeout'
        if status != 'ok':
            p.kill()
            break
        if result is not None and result.get('done'):
            p.terminate()
            break
        time.sleep(1)
    p.wait()
    for r in readers:
        r.join(5)
    for f in (p.stdout, p.stderr):
        f.close()
    if status == 'ok' and p.returncode and not (result or {}).get('done'):
        status = f'failed-rc-{p.returncode}'          # e.g. a binary that is not staged
    return {'status': status, 'peak_c': peak, 'memavail_min_mb': minmem, 'wall_s': round(time.time() - t0, 1),
            'stdout': ''.join(out), 'stderr': ''.join(err), 'result': result or {}}


def _post(path: str, body: dict, timeout: float = 900) -> dict:
    req = urllib.request.Request(f'http://127.0.0.1:{PORT}{path}', json.dumps(body).encode(),
                                 {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def turn_driver(turns: int, suffix_tokens: int, max_tokens: int):
    words = ('battery camera message weather route reminder music photo settings network calendar volume '
             'brightness contact location storage alarm timer note email map traffic podcast speech').split()
    system = 'You are a careful on-device assistant on a phone. ' + ' '.join(
        f'Rule {i}: keep {words[i % len(words)]} answers short and check {words[(i * 7) % len(words)]} first.'
        for i in range(60))                                    # ~700 tokens, byte-stable like the lean prompt

    def drive(proc) -> dict:
        res = {'done': False, 'turns': []}

        def run():
            try:
                for _ in range(300):                           # model load (cold page cache: up to ~60 s)
                    if proc.poll() is not None:
                        return
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health', timeout=2) as r:
                            if json.load(r).get('status') == 'ok':
                                break
                    except Exception:
                        time.sleep(1)
                warm = _post('/v1/chat/completions', {'messages': [{'role': 'system', 'content': system},
                                                                   {'role': 'user', 'content': 'hi'}],
                                                      'max_tokens': 1, 'temperature': 0})
                res['warm_prompt_ms'] = warm.get('timings', {}).get('prompt_ms')
                for i in range(turns):
                    q = ' '.join(words[(i + j) % len(words)] for j in range(int(suffix_tokens * 0.75)))
                    r = _post('/v1/chat/completions', {
                        'messages': [{'role': 'system', 'content': system},
                                     {'role': 'user', 'content': f'Question {i}: summarise these topics: {q}'}],
                        'max_tokens': max_tokens, 'temperature': 0})
                    tm = r.get('timings', {})
                    res['turns'].append({k: tm.get(k) for k in ('prompt_n', 'prompt_ms', 'prompt_per_second',
                                                                  'predicted_n', 'predicted_per_second')})
                    if i == 0:                                 # greedy: a kernel change should not alter it
                        text = (r.get('choices') or [{}])[0].get('message', {}).get('content') or ''
                        res['answer_sha'] = hashlib.sha256(text.encode()).hexdigest()[:16]
            except Exception as e:                             # recorded, the guard loop stops the server
                res['error'] = f'{type(e).__name__}: {e}'
            finally:
                res['done'] = True
        threading.Thread(target=run, daemon=True).start()
        return res
    return drive


def run_job(name: str, c: dict, workload: str, a) -> dict:
    rec = {'t': round(time.time()), 'name': name, 'config': c, 'workload': workload, 'model': a.model}
    rec['cooled_s'] = wait_cool(a.cool)
    with Cap(c.get('cap_khz'), c['cpus']):
        if workload == 'bench':
            g = guarded(bench_argv(c, a.model), c['cpus'], a.abort_temp, 1800)
            rec['rows'] = bench_rows(g['stdout'])
        else:
            g = guarded(server_argv(c, a.model), c['cpus'], a.abort_temp, 1800,
                        on_start=turn_driver(a.turns, a.suffix_tokens, a.max_tokens))
            rec.update({k: g['result'].get(k) for k in ('turns', 'warm_prompt_ms', 'error', 'answer_sha')})
            if g['result'].get('error') and g['status'] == 'ok':
                g['status'] = 'error'
    rec.update(status=g['status'], peak_c=g['peak_c'], memavail_min_mb=g['memavail_min_mb'], wall_s=g['wall_s'],
               system_info=system_info(g['stderr'] + g['stdout']), stderr_tail=g['stderr'][-300:])
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['plan', 'run', 'report'])
    ap.add_argument('results', nargs='?', help='report: the results JSONL')
    ap.add_argument('--matrix')
    ap.add_argument('--configs', help='comma list (default: all in the matrix)')
    ap.add_argument('--workloads', default='bench,turn')
    ap.add_argument('--model', default=MODEL)
    ap.add_argument('--out', default='/srv/s22/state/llm-bench/results.jsonl')
    ap.add_argument('--cool', type=float, default=50.0)
    ap.add_argument('--abort-temp', type=float, default=66.0)
    ap.add_argument('--turns', type=int, default=4)
    ap.add_argument('--suffix-tokens', type=int, default=300)
    ap.add_argument('--max-tokens', type=int, default=96)
    ap.add_argument('--max-temp', type=float, default=66.0, help='report: configs that peaked at or above are out')
    a = ap.parse_args(argv)
    if a.cmd == 'report':
        if not a.results:
            ap.error('report needs the results JSONL')
        recs = [json.loads(l) for l in Path(a.results).read_text().splitlines() if l.strip()]
        summary = summarize(recs)
        best, why = recommend(summary, max_temp=a.max_temp)
        configs = {r['name']: r['config'] for r in recs}
        print(json.dumps({'summary': summary, 'best': best, 'why': why,
                          'set_on_phone': ondemand_setting(configs[best]) if best else None}, indent=2))
        return 0 if best else 1
    matrix = load_matrix(a.matrix)
    names = a.configs.split(',') if a.configs else list(matrix)
    workloads = [w for w in a.workloads.split(',') if w]
    unknown = [n for n in names if n not in matrix] + [w for w in workloads if w not in WORKLOADS]
    if unknown:
        ap.error(f'unknown config/workload: {unknown}')
    jobs = [(n, w) for n in names for w in workloads]
    if a.cmd == 'plan':
        for n, w in jobs:
            argv_ = bench_argv(matrix[n], a.model) if w == 'bench' else server_argv(matrix[n], a.model)
            print(json.dumps({'config': n, 'workload': w, 'cpus': matrix[n]['cpus'],
                              'cap_khz': matrix[n].get('cap_khz'), 'argv': argv_}))
        return 0
    ok, seen = caps_ok()
    if not ok:
        print(f'refusing: a CPU clock cap is active {seen} (local model loaded?); unload it first', file=sys.stderr)
        return 2
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with Hold():
        for n, w in jobs:
            rec = run_job(n, matrix[n], w, a)
            with open(a.out, 'a') as f:
                f.write(json.dumps(rec) + '\n')
            print(json.dumps({k: rec.get(k) for k in ('name', 'workload', 'status', 'peak_c', 'rows', 'turns')}),
                  flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
