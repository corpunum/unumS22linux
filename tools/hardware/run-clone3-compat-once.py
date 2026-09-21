#!/usr/bin/env python3
"""One explicit, scoped runtime-compatibility experiment; never reboot/retry.

Native Alpine supervises a static filter. Arch spawning is tested only AFTER
the filter verifies clone3 returns ENOSYS. This is not a kernel-hang cure;
a userspace deadline cannot guarantee interruption of a broken kernel path.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT/'builds/runtime-compat-20260921/clone3-compat'
DEST = '/srv/s22/runtime-compat-20260921/clone3-compat'
CASES = {
    'filter': [],
    'posix-spawn': ['/usr/bin/python3', '-c',
        'import os; p=os.posix_spawn("/usr/bin/true",["true"],os.environ); '
        'pid,status=os.waitpid(p,0); assert status==0; print("POSIX_SPAWN_OK",flush=True)'],
    'subprocess': ['/usr/bin/python3', '-c',
        'import subprocess; r=subprocess.run(["/usr/bin/printf","SUBPROCESS_OK"],'
        'capture_output=True,text=True,check=True); assert r.stdout=="SUBPROCESS_OK"; print(r.stdout,flush=True)'],
    'repeat': ['/usr/bin/python3', '-c',
        'import subprocess\nfor i in range(10):\n '
        'r=subprocess.run(["/usr/bin/printf",str(i)],capture_output=True,text=True,check=True); '
        'assert r.stdout==str(i)\nprint("CAPTURED_SPAWN_10_OK",flush=True)'],
    'tmux': ['/usr/bin/python3', '-c', '''import os,pathlib,subprocess
socket='/tmp/s22-close-range-tmux-test.sock'
assert not pathlib.Path(socket).exists()
os.environ.update(HOME='/home/alarm',USER='alarm',LOGNAME='alarm',TERM='xterm-256color')
command=['/usr/lib/ld-linux-aarch64.so.1','--library-path','/opt/s22-pi-web/tmux/lib:/usr/lib',
         '/opt/s22-pi-web/tmux/tmux','-S',socket,'-f','/dev/null']
try:
 subprocess.run(command+['new-session','-d','-s','s22-runtime','/usr/bin/sleep 30'],check=True,timeout=3)
 result=subprocess.run(command+['display-message','-p','-t','s22-runtime','#{pane_pid}'],
                       capture_output=True,text=True,check=True,timeout=2)
 assert int(result.stdout.strip())>1
 print('TMUX_SESSION_OK',flush=True)
finally:
 if pathlib.Path(socket).exists():
  subprocess.run(command+['kill-server'],check=True,timeout=2)
'''],
}


def main():
    global BINARY, DEST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    parser.add_argument('case', choices=CASES)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--close-range', action='store_true', help='test the separately built close_range-only filter')
    args = parser.parse_args()
    if not re.fullmatch('[a-z0-9-]+', args.name):
        parser.error('unique lowercase name required')
    source_name = 'clone3-compat.c'
    if args.close_range:
        BINARY = ROOT/'builds/runtime-compat-20260921/close-range-compat'
        DEST = '/srv/s22/runtime-compat-20260921/close-range-compat'
        source_name = 'close-range-compat.c'
    binary = BINARY.read_bytes()
    digest = hashlib.sha256(binary).hexdigest()
    selection = ['--self-test'] if args.case == 'filter' else [
        '--', 'chroot', '/mnt/omarchy-trial', '/usr/bin/setpriv',
        '--reuid=1000', '--regid=1000', '--clear-groups', '--no-new-privs',
        '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', *CASES[args.case]]
    plan = dict(case=args.case, binary_sha256=digest,
                filtered_syscall='close_range' if args.close_range else 'clone3',
                source_sha256=hashlib.sha256((ROOT/'tools/hardware'/source_name).read_bytes()).hexdigest(),
                argv=[DEST, *selection], global_deployment=False)
    if not args.execute:
        print(json.dumps(plan, indent=2)); return
    if args.case in ('subprocess', 'repeat', 'tmux') and not args.close_range:
        raise SystemExit('Refusing repeat: captured-output legacy clone hung in the recorded first trial.')
    spec = importlib.util.spec_from_file_location('trial', ROOT/'tools/hardware/run-enn-trial.py')
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    before = trial.phone_health()
    os.umask(0o077)
    output = ROOT/'rootfs/main-driver-loop-20260921'/args.name
    output.mkdir(parents=True, exist_ok=False)
    trial_meta = '''import json,pathlib
p=pathlib.Path
tasks={}
for item in p('/proc').iterdir():
 if not item.name.isdigit(): continue
 try:
  status=dict(x.split(':',1) for x in (item/'status').read_text().splitlines() if ':' in x)
  if int(status.get('SigPnd','0').strip(),16)&256 or int(status.get('ShdPnd','0').strip(),16)&256:
   tasks[item.name]={'name':status['Name'].strip(),'state':status['State'].strip()}
 except (FileNotFoundError,ProcessLookupError): pass
print(json.dumps(tasks))
'''
    previous = trial.remote('python3 -c '+shlex.quote(trial_meta))
    if previous.returncode: raise RuntimeError('cannot inventory pending-kill tasks')
    previous = json.loads(previous.stdout)
    staged = '''import pathlib,hashlib,sys,os
p=pathlib.Path(DEST)
data=sys.stdin.buffer.read()
assert hashlib.sha256(data).hexdigest()==HASH
p.parent.mkdir(exist_ok=True)
if p.exists() or p.is_symlink():
 assert not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==HASH
else:
 with p.open('xb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
 os.chmod(p,0o755)
assert p.stat().st_uid==0 and (p.stat().st_mode&0o777)==0o755
'''.replace('DEST',repr(DEST)).replace('HASH',repr(digest))
    result = subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -c '+shlex.quote(staged)],
                            input=binary, capture_output=True, timeout=25)
    if result.returncode: raise RuntimeError(result.stderr.decode())
    before_kernel = trial.remote('dmesg')
    if before_kernel.returncode: raise RuntimeError('kernel capture failed')
    (output/'before-kernel.txt').write_text(before_kernel.stdout)
    trace = '/srv/s22/runtime-compat-20260921/'+args.name+'.strace'
    cmd = ['timeout','-k','2','8','strace','-f','-qq','-tt','-T','-o',trace,
           '-e','trace=clone,clone3,close_range,close,execve,wait4,prctl,seccomp', DEST, *selection]
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    try:
        result = trial.remote(shlex.join(cmd), timeout=16)
    except subprocess.TimeoutExpired as error:
        (output/'host-timeout.json').write_text(json.dumps(dict(
            **plan, started_at=started, timeout_seconds=16,
            outcome='unknown; no retry', before=before), indent=2)+'\n')
        trace_result = trial.remote('head -c 1048576 '+shlex.quote(trace))
        (output/'strace.txt').write_text(trace_result.stdout)
        raise RuntimeError('Device outcome unknown; inspect exact tasks, no retry') from error
    (output/'stdout.txt').write_text(result.stdout)
    (output/'stderr.txt').write_text(result.stderr)
    trace_result = trial.remote('head -c 1048576 '+shlex.quote(trace))
    (output/'strace.txt').write_text(trace_result.stdout)
    after_kernel = trial.remote('dmesg')
    (output/'after-kernel.txt').write_text(after_kernel.stdout)
    current = trial.remote('python3 -c '+shlex.quote(trial_meta))
    if current.returncode: raise RuntimeError('post-task inventory unavailable')
    current = json.loads(current.stdout)
    after = trial.phone_health()
    receipt = dict(**plan, started_at=started, returncode=result.returncode,
        elapsed_seconds=round(time.monotonic()-clock,3), before=before,after=after,
        same_boot=before['boot_id']==after['boot_id'], previous_pending_kill=previous,
        after_pending_kill=current, new_pending_kill=sorted(set(current)-set(previous)),
        strace_capture_exit=trace_result.returncode, kernel_capture_exit=after_kernel.returncode)
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2)); print(result.stdout); print(result.stderr)
    if receipt['new_pending_kill'] or not receipt['same_boot']:
        raise RuntimeError('new stuck task/boot change; do not repeat')
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
