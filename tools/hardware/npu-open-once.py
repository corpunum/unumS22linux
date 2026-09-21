#!/usr/bin/env python3
"""Plan or supervise one guarded ENN/NPU character-device open/close.

The default mode is host-only and performs no phone access.  ``--execute`` is
an explicit parent-owned action: it reads only live metadata, then runs a
single ``os.open('/dev/vertex10', O_RDONLY|O_CLOEXEC)`` followed immediately by
``os.close`` under a timeout.  It does not issue an ioctl, BOOTUP, STREAM,
firmware, model, buffer, or service operation.

This is intentionally not an inference probe.  The pinned driver has
CONFIG_NPU_USE_BOOT_IOCTL=y, so character-device open reaches the driver's
allocation/scheduler/QoS setup but does not perform the separate BOOTUP path.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
SSH = ROOT / "tools/s22-ssh"
KERNEL = ROOT / "lineage/android_kernel_samsung_s5e9925"
EVIDENCE = ROOT / "rootfs/hardware-reuse-20260921/npu-open-once"
EXPECTED_KERNEL_COMMIT = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
DEFCONFIG = KERNEL / "arch/arm64/configs/s5e9925_defconfig"
DTS = KERNEL / "arch/arm64/boot/dts/exynos/s5e9925.dts"
CONFIG_H = KERNEL / "drivers/vision/npu/core/include/npu-config.h"
BINARY_H = KERNEL / "drivers/vision/npu/core/include/npu-binary.h"
VERTEX_C = KERNEL / "drivers/vision/npu/core/npu-vertex.c"
DEVICE_C = KERNEL / "drivers/vision/npu/core/npu-device.c"
SYSTEM_C = KERNEL / "drivers/vision/npu/core/npu-system.c"
SCHEDULER_C = KERNEL / "drivers/vision/npu/core/npu-scheduler.c"
QOS_C = KERNEL / "drivers/vision/npu/core/npu-qos.c"
ASSET_ROOT = ROOT / "rootfs/npu-vendor-assets-decompressed"
RECOVERED_ASSETS = {
    "AIE.bin__3ec": ASSET_ROOT / "AIE.bin__3ec",
    "dsp_reloc_rules.bin__424": ASSET_ROOT / "dsp_reloc_rules.bin__424",
}

DEVICE = "/dev/vertex10"
EXPECTED_MAJOR = 82
EXPECTED_MINOR = 10
EXPECTED_CONFIG = {
    "CONFIG_NPU_USE_BOOT_IOCTL": "y",
    "CONFIG_NPU_USE_HW_DEVICE": "y",
    "CONFIG_DSP_USE_VS4L": "y",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def kernel_git_state() -> dict[str, object]:
    head = subprocess.run(
        ["git", "-C", str(KERNEL), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(KERNEL), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, check=True,
    ).stdout
    return {
        "expected_commit": EXPECTED_KERNEL_COMMIT,
        "commit": head,
        "clean": not status,
        "status": status,
    }


def recovered_asset_inventory() -> dict[str, object]:
    inventory = {}
    for name, path in RECOVERED_ASSETS.items():
        if not path.is_file():
            inventory[name] = {"present": False}
            continue
        inventory[name] = {
            "present": True,
            "path": str(path.relative_to(ROOT)),
            "size": path.stat().st_size,
            "sha256": sha256(path),
            "runtime_basename": name.split("__", 1)[0],
            "exact_runtime_path": False,
        }
    return inventory


def source_audit() -> dict[str, object]:
    git_state = kernel_git_state()
    if git_state["commit"] != EXPECTED_KERNEL_COMMIT or not git_state["clean"]:
        raise RuntimeError({"kernel_git_state": git_state})
    files = {
        "defconfig": DEFCONFIG,
        "dts": DTS,
        "config_header": CONFIG_H,
        "binary_header": BINARY_H,
        "vertex": VERTEX_C,
        "device": DEVICE_C,
        "system": SYSTEM_C,
        "scheduler": SCHEDULER_C,
        "qos": QOS_C,
    }
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise RuntimeError("missing pinned source files: " + ",".join(missing))
    texts = {name: path.read_text(errors="strict") for name, path in files.items()}
    config = {
        key: (f"{key}=y" in texts["defconfig"])
        for key in EXPECTED_CONFIG
    }
    required_patterns = {
        "open_close_fops": r"\.open[ \t]+=[ \t]+npu_vertex_open",
        "open_calls_device_open": r"return npu_device_open\(device\);",
        "close_calls_device_close": r"return npu_device_close\(device\);",
        "bootup_is_ioctl_separate": r"case VS4L_VERTEXIOC_BOOTUP:",
        "bootup_guarded": r"#ifdef CONFIG_NPU_USE_BOOT_IOCTL",
        "device_open_present": r"int npu_device_open\(struct npu_device \*device\)",
        "device_close_present": r"int npu_device_close\(struct npu_device \*device\)",
        "system_open": r"int npu_system_open\(struct npu_system \*system\)",
        "system_close": r"int npu_system_close\(struct npu_system \*system\)",
        "scheduler_open": r"int npu_scheduler_open\(struct npu_device \*device\)",
        "scheduler_close": r"int npu_scheduler_close\(struct npu_device \*device\)",
        "qos_open": r"int npu_qos_open\(struct npu_system \*system\)",
        "qos_close": r"int npu_qos_close\(struct npu_system \*system\)",
        "firmware_base_aie": r'#define FW_BASE_NAME\s+"AIE"',
        "firmware_vector": r'#define NPU_FW_VECTOR_NAME\s+"vectors\.bin"',
        "firmware_vendor_path": r'#define NPU_FW_PATH2\s+"/vendor/firmware/"',
        "vertex_name_npu": r'vertex_name\s*=\s*"npu"',
        "vision_major": r'#define VISION_MAJOR\s+82',
    }
    pattern_text = {
        "open_close_fops": texts["vertex"],
        "open_calls_device_open": texts["vertex"],
        "close_calls_device_close": texts["vertex"],
        "bootup_guarded": texts["vertex"] + texts["device"],
        "bootup_is_ioctl_separate": (KERNEL / "drivers/vision/vision-core/vision-ioctl.c").read_text(),
        "device_open_present": texts["device"],
        "device_close_present": texts["device"],
        "system_open": texts["system"],
        "system_close": texts["system"],
        "scheduler_open": texts["scheduler"],
        "scheduler_close": texts["scheduler"],
        "qos_open": texts["qos"],
        "qos_close": texts["qos"],
        "firmware_base_aie": texts["binary_header"],
        "firmware_vector": texts["binary_header"],
        "firmware_vendor_path": texts["binary_header"],
        "vertex_name_npu": texts["dts"],
        "vision_major": (KERNEL / "drivers/vision/vision-core/include/vision-dev.h").read_text(),
    }
    patterns = {
        key: bool(re.search(patterns, pattern_text[key]))
        for key, patterns in required_patterns.items()
    }
    if not all(config.values()) or not all(patterns.values()):
        raise RuntimeError({"config": config, "patterns": patterns})
    return {
        "kernel_source": str(KERNEL.relative_to(ROOT)),
        "kernel_git_state": git_state,
        "defconfig_sha256": sha256(DEFCONFIG),
        "dts_sha256": sha256(DTS),
        "config_header_sha256": sha256(CONFIG_H),
        "binary_header_sha256": sha256(BINARY_H),
        "source_config": config,
        "source_checks": patterns,
        "firmware_contract": {
            "base_name": "AIE.bin",
            "vector_name": "vectors.bin",
            "search_roots": ["/data/", "/vendor/firmware/"],
            "not_required_by_open_close": True,
            "recovered_assets": recovered_asset_inventory(),
        },
        "device_contract": {
            "path": DEVICE,
            "major": EXPECTED_MAJOR,
            "minor": EXPECTED_MINOR,
            "vertex_name": "npu",
        },
        "safety_boundary": {
            "allowed": ["open(O_RDONLY|O_CLOEXEC)", "close"],
            "forbidden": ["ioctl", "BOOTUP", "STREAM_ON", "firmware load", "model", "buffer"],
        },
    }


def remote(command: str, timeout: int = 25) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(SSH), command], capture_output=True, text=True, timeout=timeout)


REMOTE_METADATA = r'''
import glob,gzip,json,os,stat
out={}
for key in ["CONFIG_NPU_USE_BOOT_IOCTL","CONFIG_NPU_USE_HW_DEVICE","CONFIG_DSP_USE_VS4L"]:
    out[key]=None
try:
    text=gzip.open("/proc/config.gz","rt").read().splitlines()
    for key in out:
        out[key]=next((line.split("=",1)[1] for line in text if line.startswith(key+"=")),None)
except Exception as exc:
    out["config_error"]=type(exc).__name__+":"+str(exc)
try:
    st=os.lstat("/dev/vertex10")
    out["device"]={"char":stat.S_ISCHR(st.st_mode),"major":os.major(st.st_rdev),"minor":os.minor(st.st_rdev),"mode":oct(st.st_mode & 0o7777)}
except OSError as exc:
    out["device"]={"error":exc.errno,"message":exc.strerror}
out["boot_id"]=open("/proc/sys/kernel/random/boot_id").read().strip()
out["pid1"]=open("/proc/1/comm").read().strip()
out["guardian_root"]=os.path.isdir("/proc/1/root/vendor")
for path in ["/proc/1/root/vendor/firmware","/proc/1/root/data","/proc/1/root/vendor/firmware/AIE.bin","/proc/1/root/vendor/firmware/vectors.bin","/proc/1/root/vendor/etc/enn/custom_mode_config.json"]:
    out.setdefault("paths",{})[path]=os.path.exists(path)
fds=[]
for link in glob.glob("/proc/[0-9]*/fd/[0-9]*"):
    try:
        target=os.readlink(link)
    except OSError:
        continue
    if target == "/dev/vertex10" or target.startswith("/dev/vertex"):
        parts=link.split("/")
        fds.append({"pid":parts[2],"fd":parts[4],"target":target})
out["npu_fds"]=fds
print(json.dumps(out,sort_keys=True))
'''


REMOTE_OPEN_CLOSE = r'''
import json,os,stat
path="/dev/vertex10"
fd=None
try:
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC)
    st=os.fstat(fd)
    receipt={"stage":"opened","path":path,"fd":fd,
             "char":stat.S_ISCHR(st.st_mode),"major":os.major(st.st_rdev),
             "minor":os.minor(st.st_rdev),"mode":oct(st.st_mode & 0o7777)}
    if not receipt["char"] or receipt["major"] != 82 or receipt["minor"] != 10:
        raise RuntimeError("opened fd is not expected vertex10 rdev")
    print(json.dumps(receipt),flush=True)
finally:
    if fd is not None:
        os.close(fd)
        print(json.dumps({"stage":"closed","fd":fd}),flush=True)
'''


REMOTE_HEALTH = r'''import json,pathlib,urllib.request
p=pathlib.Path
ready=json.loads(p('/run/s22-persistent-ready.json').read_text())
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with op.open('http://127.0.0.1:8089/health',timeout=4) as f:
    health=json.load(f)
temp=p('/sys/class/power_supply/battery/temp').read_text().strip()
print(json.dumps({'boot_id':p('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'pid1':p('/proc/1/comm').read_text().strip(), 'temperature_c':int(temp)/10,
 'profile':ready.get('model_profile'), 'model':health.get('status')}))
'''


def plan() -> dict[str, object]:
    audit = source_audit()
    audit["mode"] = "plan"
    audit["phone_contact"] = False
    audit["command"] = "parent-only: --execute"
    return audit


def validate_live_metadata(metadata: dict[str, object], label: str) -> None:
    for key, expected in EXPECTED_CONFIG.items():
        if metadata.get(key) != expected:
            raise RuntimeError(f"{label} {key}={metadata.get(key)!r}, expected {expected!r}")
    if metadata.get("pid1") != "native-guardian" or not metadata.get("guardian_root"):
        raise RuntimeError(f"{label} PID1/guardian root mismatch: " + json.dumps(metadata, sort_keys=True))
    if metadata.get("npu_fds"):
        raise RuntimeError(f"{label} has existing NPU fds: " + json.dumps(metadata["npu_fds"], sort_keys=True))
    device = metadata.get("device", {})
    if device.get("char") is not True or device.get("major") != EXPECTED_MAJOR or device.get("minor") != EXPECTED_MINOR:
        raise RuntimeError(f"{label} device metadata mismatch: {device}")


def execute(deadline: int) -> int:
    audit = source_audit()
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(exist_ok=False)
    (EVIDENCE / "source-audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    before_health_result = remote("python3 -c " + shlex.quote(REMOTE_HEALTH), timeout=15)
    if before_health_result.returncode:
        raise RuntimeError("baseline phone health failed: " + before_health_result.stderr.strip())
    before_health = json.loads(before_health_result.stdout)
    if before_health.get("pid1") != "native-guardian" or before_health.get("profile") != "qwen4b" or before_health.get("model") != "ok":
        raise RuntimeError("baseline guardian/health changed: " + json.dumps(before_health, sort_keys=True))
    if float(before_health.get("temperature_c", 100)) >= 42:
        raise RuntimeError("baseline battery temperature >=42C")
    (EVIDENCE / "before-health.json").write_text(json.dumps(before_health, indent=2, sort_keys=True) + "\n")
    before_kernel = remote("dmesg", timeout=30)
    if before_kernel.returncode:
        raise RuntimeError("baseline kernel capture failed: " + before_kernel.stderr.strip())
    (EVIDENCE / "before-kernel.txt").write_text(before_kernel.stdout)
    boundary = max(map(float, re.findall(r"^\[\s*([0-9.]+)\]", before_kernel.stdout, re.M)), default=0)
    metadata_result = remote("python3 -c " + shlex.quote(REMOTE_METADATA), timeout=20)
    if metadata_result.returncode:
        raise RuntimeError("metadata guard failed: " + metadata_result.stderr.strip())
    metadata = json.loads(metadata_result.stdout)
    (EVIDENCE / "before-live-metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    validate_live_metadata(metadata, "before")
    trace = "/tmp/s22-npu-open-once.strace"
    command = "strace -f -qq -tt -T -s 160 -o " + shlex.quote(trace) + " -e trace=%file,close,fstat,ioctl,connect,clone,clone3 timeout -k 2 " + str(deadline) + " python3 -c " + shlex.quote(REMOTE_OPEN_CLOSE)
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    try:
        result = remote(command, timeout=deadline + 15)
    except subprocess.TimeoutExpired:
        (EVIDENCE / "host-timeout.txt").write_text("Remote outcome unknown; no retry or reset.\n")
        raise
    elapsed = round(time.monotonic() - started, 3)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    opened_closed = (len(events) == 2 and events[0].get('stage') == 'opened'
                     and events[0].get('char') is True
                     and (events[0].get('major'), events[0].get('minor')) == (82, 10)
                     and events[1].get('stage') == 'closed'
                     and events[0].get('fd') == events[1].get('fd'))
    (EVIDENCE / "stdout.txt").write_text(result.stdout)
    (EVIDENCE / "stderr.txt").write_text(result.stderr)
    trace_result = remote("test -f " + shlex.quote(trace) + " && head -c 8388608 " + shlex.quote(trace), timeout=20)
    (EVIDENCE / "strace.txt").write_text(trace_result.stdout)
    remote("python3 -c " + shlex.quote("from pathlib import Path; Path(" + repr(trace) + ").unlink(missing_ok=True)"), timeout=10)
    after_kernel = remote("dmesg", timeout=30)
    (EVIDENCE / "after-kernel.txt").write_text(after_kernel.stdout)
    delta = []
    for line in after_kernel.stdout.splitlines():
        match = re.match(r"^\[\s*([0-9.]+)\]", line)
        if match and float(match[1]) > boundary:
            delta.append(line)
    (EVIDENCE / "kernel-delta.txt").write_text("\n".join(delta) + "\n")
    after_health_result = remote("python3 -c " + shlex.quote(REMOTE_HEALTH), timeout=15)
    if after_health_result.returncode:
        raise RuntimeError("after phone health failed: " + after_health_result.stderr.strip())
    after_health = json.loads(after_health_result.stdout)
    (EVIDENCE / "after-health.json").write_text(json.dumps(after_health, indent=2, sort_keys=True) + "\n")
    after_metadata_result = remote("python3 -c " + shlex.quote(REMOTE_METADATA), timeout=20)
    if after_metadata_result.returncode:
        raise RuntimeError("after metadata failed: " + after_metadata_result.stderr.strip())
    after_metadata = json.loads(after_metadata_result.stdout)
    (EVIDENCE / "after-live-metadata.json").write_text(json.dumps(after_metadata, indent=2, sort_keys=True) + "\n")
    validate_live_metadata(after_metadata, "after")
    receipt = {
        "mode": "execute",
        "started_at": started_at,
        "source_audit": audit,
        "before": {"health": before_health, "metadata": metadata},
        "after": {"health": after_health, "metadata": after_metadata},
        "same_boot": before_health.get("boot_id") == after_health.get("boot_id") and metadata.get("boot_id") == after_metadata.get("boot_id"),
        "live_metadata": metadata,
        "returncode": result.returncode,
        "elapsed_seconds": elapsed,
        "kernel_capture_exit": after_kernel.returncode,
        "strace_capture_exit": trace_result.returncode,
        "kernel_delta": delta,
        "npu_fds_after": after_metadata.get("npu_fds", []),
        "opened_rdev_checked_and_closed": opened_closed,
        "ioctls_requested": False,
        "firmware_requested": False,
        "model_or_buffer_requested": False,
        "note": "Parent-authorized open/close only; not NPU readiness or inference.",
    }
    (EVIDENCE / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    if not receipt["same_boot"] or after_kernel.returncode or trace_result.returncode or after_health.get("pid1") != "native-guardian" or after_health.get("model") != "ok" or receipt["npu_fds_after"]:
        raise RuntimeError("boot, health, kernel capture, or NPU-fd guard changed; stop")
    if result.returncode == 0 and not opened_closed:
        raise RuntimeError('Missing explicit matching open/close evidence')
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="host-only source audit and command plan")
    parser.add_argument("--execute", action="store_true", help="parent-owned live metadata guard and open/close")
    parser.add_argument("--deadline", type=int, default=10, choices=range(1, 31))
    args = parser.parse_args()
    if args.plan == args.execute:
        parser.error("choose exactly one of --plan or --execute")
    if args.plan:
        print(json.dumps(plan(), indent=2, sort_keys=True))
        return 0
    return execute(args.deadline)


if __name__ == "__main__":
    raise SystemExit(main())
