#!/usr/bin/env python3
"""Start the loopback-only, unprivileged Pi browser terminal on native Alpine.

Tailscale Serve is configured separately. This never exposes the model API,
starts an inference request, restarts the desktop, or opens physical devices.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time
import urllib.request

BASE = Path('/srv/s22/agent-web')
ARCH = Path('/mnt/omarchy-trial')
PRIVATE = BASE / 'private'
TTYD = '/opt/s22-pi-web/ttyd'
TTYD_SHA = 'b38acadd89d1d396a0f5649aa52c539edbad07f4bc7348b27b4f4b7219dd4165'
PORT = 8093
TMUX = '/opt/s22-pi-web/tmux-musl/tmux'
TMUX_SHA = 'b676aa136782b513866867ce896cf66a1ee7edbb7dac211fda045b39487bf03b'
MUSL = '/opt/s22-pi-web/tmux-musl/lib/ld-musl-aarch64.so.1'
MUSL_SHA = '32377e6d71725bb019e9ff6d5e9f16b4d5156d6f2c36504191c2d6a7c4d4a44d'
TMUX_LIBS = {
    MUSL: MUSL_SHA,
    '/opt/s22-pi-web/tmux-musl/lib/libncursesw.so.6': 'a9e06c751f47179afa780c9285e5e22ed8883d6ee1379de25a4fc9eb90054345',
    '/opt/s22-pi-web/tmux-musl/lib/libevent_core-2.1.so.7': '8625e2b9987b48665c79d4bee9c1a1337bceb06dbd18fba6b16700a9a55c3eff',
}
TMUX_SOCKET = '/home/alarm/.pi/agent/web-sessions/web-musl.tmux'
if not __debug__:
    raise RuntimeError('Run this reviewed helper without Python optimization')


def user_command(argv):
    return ['chroot', str(ARCH), '/usr/bin/setpriv', '--reuid=1000', '--regid=1000',
            '--clear-groups', '--no-new-privs', '--bounding-set=-all', '--inh-caps=-all',
            '--ambient-caps=-all', '--reset-env', '/usr/bin/env',
            'HOME=/home/alarm', 'USER=alarm', 'LOGNAME=alarm', 'TERM=xterm-256color',
            'PATH=/usr/local/bin:/usr/bin:/bin', *argv]


def command():
    return user_command([TTYD,
            '--interface', '127.0.0.1', '--port', str(PORT), '--writable',
            '--check-origin', '--max-clients', '1', '--auth-header', 'Tailscale-User-Login',
            '--client-option', 'titleFixed=S22 Pi agent',
            '--client-option', 'fontSize=16', '--client-option', 'disableLeaveAlert=false',
            '/usr/local/bin/pi-web-session'])


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preflight():
    assert os.geteuid() == 0
    assert Path('/proc/1/comm').read_text().strip() == 'native-guardian'
    ready = json.loads(Path('/run/s22-persistent-ready.json').read_text())
    assert ready['model_profile'] == 'qwen4b'
    assert ARCH.stat().st_ino == Path('/srv/s22/arch').stat().st_ino
    assert ARCH.stat().st_dev == Path('/srv/s22/arch').stat().st_dev
    assert digest(ARCH / TTYD.lstrip('/')) == TTYD_SHA
    assert digest(ARCH / TMUX.lstrip('/')) == TMUX_SHA
    for name, expected in TMUX_LIBS.items():
        assert digest(ARCH / name.lstrip('/')) == expected
    for path in [ARCH / TTYD.lstrip('/'), ARCH / TMUX.lstrip('/'),
                 *(ARCH / name.lstrip('/') for name in TMUX_LIBS),
                 ARCH / 'usr/local/bin/pi-web-session', BASE / 'start-agent-web.py']:
        info = path.stat()
        assert not path.is_symlink() and info.st_uid == 0 and not info.st_mode & 0o022, path
    assert (ARCH / 'home/alarm/.pi/agent/web-sessions').stat().st_uid == 1000
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open('http://127.0.0.1:8089/health', timeout=4) as response:
        assert json.load(response)['status'] == 'ok'


def running():
    record = PRIVATE / 'process.json'
    if not record.exists():
        return None
    old = json.loads(record.read_text())
    path = Path('/proc') / str(old['pid'])
    if not path.exists():
        return None
    stat = (path / 'stat').read_text().rsplit(')', 1)[1].split()
    if stat[0] == 'Z':
        return None
    if stat[19] != old['start_ticks']:
        return None
    if digest(path / 'exe') != TTYD_SHA:
        raise RuntimeError('PID is no longer the exact reviewed ttyd')
    fields = dict(line.split(':', 1) for line in (path / 'status').read_text().splitlines() if ':' in line)
    assert set(fields['Uid'].split()) == {'1000'}
    assert all(int(fields[k].strip(), 16) == 0 for k in ['CapEff','CapPrm','CapBnd'])
    assert fields['NoNewPrivs'].strip() == '1'
    return old['pid']


def prepare_ptmx():
    """Create the normal virtual PTY multiplexer beside the Arch pts mount."""
    link = ARCH / 'dev/ptmx'
    native = Path('/dev/ptmx').stat()
    assert stat.S_ISCHR(native.st_mode) and native.st_rdev == os.makedev(5, 2)
    assert native.st_mode & 0o777 == 0o666
    if not link.is_symlink():
        info = link.stat()
        assert stat.S_ISCHR(info.st_mode) and info.st_rdev == native.st_rdev
        assert info.st_mode & 0o777 == 0o666
        return
    assert link.is_symlink() and os.readlink(link) == 'pts/ptmx'
    # A bind-mounted ptmx cannot find its sibling pts on this kernel. A normal
    # char 5:2 node in the existing dev tmpfs can. This does not change global
    # devpts permissions, expose physical devices, or add an owned mount.
    staged = ARCH / 'dev/ptmx.pi-web-next'
    os.mknod(staged, stat.S_IFCHR | 0o600, native.st_rdev)
    os.chmod(staged, 0o666)
    os.replace(staged, link)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--start', action='store_true')
    action.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    if not args.stop:
        preflight()
    elif os.geteuid() != 0:
        raise RuntimeError('Root required for verified cleanup')
    os.umask(0o077)
    PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    assert PRIVATE.stat().st_uid == 0 and PRIVATE.stat().st_mode & 0o777 == 0o700
    with (PRIVATE / 'start.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pid = running()
        if args.stop:
            if pid:
                os.kill(pid, signal.SIGTERM)
                for _ in range(50):
                    if running() is None:
                        break
                    time.sleep(.1)
                else:
                    raise RuntimeError('ttyd did not stop; preserve evidence, no blind kill')
            sock = ARCH / TMUX_SOCKET.lstrip('/')
            if sock.exists():
                assert sock.is_socket() and sock.stat().st_uid == 1000
                assert digest(ARCH / TMUX.lstrip('/')) == TMUX_SHA
                result = subprocess.run(user_command([MUSL,
                         '--library-path','/opt/s22-pi-web/tmux-musl/lib',TMUX,
                         '-f','/dev/null','-S',TMUX_SOCKET,'kill-server']),
                         capture_output=True, text=True, timeout=5)
                if result.returncode and 'no server running' not in result.stderr:
                    raise RuntimeError('Could not stop the dedicated web tmux server')
            print(json.dumps(dict(stopped_pid=pid)))
            return
        if not args.start or pid:
            print(json.dumps(dict(preflight='ok', pid=pid, backend='127.0.0.1:8093')))
            return
        prepare_ptmx()
        os.setpriority(os.PRIO_PROCESS, 0, 10)
        with (PRIVATE / 'ttyd.log').open('ab', buffering=0) as log:
            child = subprocess.Popen(command(), stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT,
                                     cwd='/', start_new_session=True, close_fds=True)
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for _ in range(40):
                if child.poll() is not None:
                    raise RuntimeError('ttyd exited; inspect private log, no retry')
                try:
                    # Test the loopback route without starting a terminal session.
                    req = urllib.request.Request('http://127.0.0.1:8093/',
                          headers={'Tailscale-User-Login':'local-readiness-check'})
                    with op.open(req, timeout=.5) as response:
                        assert response.status == 200
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('loopback web readiness timeout')
            stat = (Path('/proc') / str(child.pid) / 'stat').read_text().rsplit(')', 1)[1].split()
            (PRIVATE / 'process.json').write_text(json.dumps(dict(pid=child.pid,start_ticks=stat[19]))+'\n')
            assert running() == child.pid
        except BaseException:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
            raise
        print(json.dumps(dict(pid=child.pid, backend='127.0.0.1:8093', uid=1000,
                              capabilities=0, no_new_privs=True, inference_started=False)))


if __name__ == '__main__':
    main()
