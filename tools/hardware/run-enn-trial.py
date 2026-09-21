#!/usr/bin/env python3
"""Supervise one reviewed vendor loader or no-device initialization attempt.

This host-side wrapper is preparation only until the parent invokes it.  It
does not contact the phone during import or with ``--plan``.  A real run
records stage hashes, full before/after kernel logs, health, and a bounded
strace of file/ioctl/connect activity.  It never retries or reboots.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "rootfs/npu-compat-20260921"
EVIDENCE = ROOT / "rootfs/hardware-reuse-20260921/npu-trials"
SSH = ROOT / "tools/s22-ssh"
REMOTE_STAGE = "/srv/s22/npu-compat-20260921"


def remote(command: str, timeout: int = 25) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(SSH), command], capture_output=True, text=True, timeout=timeout)


def phone_health() -> dict[str, object]:
    script = r"""import json,pathlib,urllib.request
p=pathlib.Path
ready=json.loads(p('/run/s22-persistent-ready.json').read_text())
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with op.open('http://127.0.0.1:8089/health',timeout=4) as f: health=json.load(f)
temp=p('/sys/class/power_supply/battery/temp').read_text().strip()
print(json.dumps({'boot_id':p('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'pid1':p('/proc/1/comm').read_text().strip(),
 'temperature_c':int(temp)/10,
 'profile':ready.get('model_profile'), 'model':health.get('status')}))
"""
    result = remote("python3 -c " + shlex.quote(script))
    if result.returncode:
        raise RuntimeError("phone health unavailable: " + result.stderr)
    state = json.loads(result.stdout)
    if state["pid1"] != "native-guardian" or state["profile"] != "qwen4b" or state["model"] != "ok":
        raise RuntimeError("phone baseline changed")
    if float(state["temperature_c"]) >= 42:
        raise RuntimeError("battery temperature >=42C; stop")
    return state


def stage_hash_check() -> dict[str, object]:
    manifest = json.loads((STAGE / "manifests/files.json").read_text())
    expected = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    script = """
import hashlib,json,pathlib
root=pathlib.Path(DEST)
expected=EXPECTED
actual={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file() and p != root/'manifests/files.json'}
assert actual == expected, (set(expected)-set(actual), set(actual)-set(expected))
print(json.dumps({'files':len(actual),'manifest_sha256':'MANIFEST'}))
""".replace("DEST", repr(REMOTE_STAGE)).replace("EXPECTED", repr(expected)).replace(
        "MANIFEST", hashlib.sha256((STAGE / "manifests/files.json").read_bytes()).hexdigest()
    )
    result = remote("python3 -c " + shlex.quote(script), timeout=35)
    if result.returncode:
        raise RuntimeError("remote stage hash mismatch: " + result.stderr)
    return json.loads(result.stdout)


def main() -> int:
    global STAGE, REMOTE_STAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="new lowercase trial name")
    parser.add_argument("--proc", action="store_true", help="mount read-only proc inside the chroot")
    parser.add_argument("--deadline", type=int, default=20, choices=range(1, 61), metavar="1..60")
    parser.add_argument("--plan", action="store_true", help="print local receipt plan without phone access")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--init-only", action="store_true", help="run the separately reviewed no-device initializer")
    choice.add_argument("--cellular-loader", action="store_true", help="load RIL and resolve RIL_Init without calling it")
    args = parser.parse_args()
    if args.init_only:
        STAGE = ROOT / "rootfs/npu-init-20260921"
        REMOTE_STAGE = "/srv/s22/npu-init-20260921"
    probe = "bin/enn-init-probe" if args.init_only else "bin/enn-dlopen-probe"
    if args.cellular_loader:
        STAGE = ROOT / "rootfs/cellular-loader-20260921"
        REMOTE_STAGE = "/srv/s22/cellular-loader-20260921"
        probe = "bin/cellular-dlopen-probe"
    if not re.fullmatch(r"[a-z0-9-]+", args.name):
        parser.error("name must contain only lowercase letters, digits and hyphens")
    manifest = json.loads((STAGE / "manifests/files.json").read_text())
    closure = json.loads((STAGE / "manifests/closure.json").read_text())
    plan = {
        "remote_stage": REMOTE_STAGE,
        "name": args.name,
        "deadline_seconds": args.deadline,
        "read_only_proc": args.proc,
        "devices": ["null", "zero", "random", "urandom"],
        "devices_exposed": False,
        "private_network_namespace": args.cellular_loader,
        "strace_events": ["%file", "ioctl", "connect", "clone", "clone3"],
        "manifest_sha256": hashlib.sha256((STAGE / "manifests/files.json").read_bytes()).hexdigest(),
        "closure_missing": closure["missing"],
        "closure_ambiguous": closure["ambiguous"],
        "probe_sha256": next(x["sha256"] for x in manifest["files"] if x["path"] == probe),
        "exec_calls": "dlopen and RIL_Init lookup only; no RIL call" if args.cellular_loader else "EnnInitialize once; no model, buffer, deinit or dlclose" if args.init_only else "dlopen and dlsym only; no ENN function calls",
    }
    if args.plan:
        print(json.dumps(plan, indent=2))
        return 0
    if plan["closure_missing"] or plan["closure_ambiguous"]:
        raise RuntimeError("refusing incomplete or ambiguous host closure")
    os.umask(0o077)
    raw = EVIDENCE / args.name
    raw.mkdir(parents=True, exist_ok=False)
    before = phone_health()
    kernel = remote("dmesg", timeout=30)
    if kernel.returncode:
        raise RuntimeError("kernel capture failed: " + kernel.stderr)
    (raw / "before-kernel.txt").write_text(kernel.stdout)
    boundary = max(map(float, re.findall(r"^\[\s*([0-9.]+)\]", kernel.stdout, re.M)), default=0)
    staged = stage_hash_check()
    helper = (ROOT / "tools/hardware/exec-enn-isolated.py").read_text()
    (raw / "exec-enn-isolated.py").write_text(helper)
    options = ["--proc"] if args.proc else []
    execution = ["python3", "-c", helper, REMOTE_STAGE] + options + ["/"+probe]
    trace = "/tmp/s22-npu-" + args.name + ".strace"
    wrapped = [
        "strace", "-f", "-qq", "-tt", "-T", "-s", "160", "-o", trace,
        "-e", "trace=%file,ioctl,connect,clone,clone3",
    ] + execution
    command = shlex.join(["timeout", "-k", "3", str(args.deadline)] + wrapped)
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    try:
        result = remote(command, timeout=args.deadline + 15)
    except subprocess.TimeoutExpired:
        (raw / "host-timeout.txt").write_text("Remote outcome unknown; no retry or reset.\n")
        raise
    elapsed = time.monotonic() - clock
    (raw / "stdout.txt").write_text(result.stdout)
    (raw / "stderr.txt").write_text(result.stderr)
    traced = remote("test -f " + shlex.quote(trace) + " && head -c 8388608 " + shlex.quote(trace), timeout=20)
    (raw / "strace.txt").write_text(traced.stdout)
    remote("python3 -c " + shlex.quote("from pathlib import Path; Path(" + repr(trace) + ").unlink(missing_ok=True)"), timeout=10)
    kernel_after = remote("dmesg", timeout=30)
    (raw / "after-kernel.txt").write_text(kernel_after.stdout)
    delta = []
    for line in kernel_after.stdout.splitlines():
        match = re.match(r"^\[\s*([0-9.]+)\]", line)
        if match and float(match[1]) > boundary:
            delta.append(line)
    (raw / "kernel-delta.txt").write_text("\n".join(delta) + "\n")
    after = phone_health()
    receipt = {
        **plan,
        "started_at": started,
        "returncode": result.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "before": before,
        "after": after,
        "same_boot": before["boot_id"] == after["boot_id"],
        "staged": staged,
        "kernel_capture_exit": kernel_after.returncode,
        "strace_capture_exit": traced.returncode,
        "gpu_or_npu_kernel_messages": [
            line for line in delta if re.search(r"GPU|gpu|NPU|npu|TCP|fault|timeout|reset", line)
        ],
        "helper_sha256": hashlib.sha256(helper.encode()).hexdigest(),
        "note": "No-call RIL loader without modem, binder, EFS or CP access; not cellular operation." if args.cellular_loader else "Initialization diagnostic without hardware nodes; not firmware boot or inference." if args.init_only else "No-call loader diagnostic; success does not prove ENN initialization, model load, or inference.",
    }
    (raw / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    print(result.stdout)
    print(result.stderr)
    if not receipt["same_boot"] or kernel_after.returncode:
        raise RuntimeError("boot or kernel capture changed; stop")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
