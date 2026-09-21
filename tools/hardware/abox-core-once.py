#!/usr/bin/env python3
"""Audit, and optionally perform one reversible ABOX core startup.

This is deliberately narrower than an audio bring-up tool. The default mode
only reads the phone. "--start" is an explicit, parent-reviewed operation:
it stages four locally verified files into a new userdata directory and the
guardian-visible firmware directory, writes ABOX "power/control" to "on"
once, observes bounded state, and restores the previous value in "finally".
It never opens ALSA, binds or unbinds a driver, reloads a module, or writes a
partition.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_ROOT = ROOT / "rootfs/bt-audio-vendor-assets/vendor/firmware"
KERNEL_DEFCONFIG = ROOT / "lineage/android_kernel_samsung_s5e9925/arch/arm64/configs/s5e9925_defconfig"
KERNEL_HEAD = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
DEST_ROOT = "/srv/s22/audio-reuse-20260921/abox-core-once"
ACTIVE_ROOT = "/proc/1/root/vendor/firmware"
ABOX_ROOT = "/sys/devices/platform/18c50000.abox"
EVIDENCE_ROOT = ROOT / "rootfs/audio-reuse-20260921/abox-core-once"

FILES = {
    "calliope_sram.bin": (
        FIRMWARE_ROOT / "calliope_sram.bin",
        165120,
        "786ae058d4c439c01edf856abb52184a92e8d25e90b1b748e380186bed84796d",
    ),
    "calliope_dram.bin": (
        FIRMWARE_ROOT / "calliope_dram.bin",
        2204800,
        "d2460b85f1e8ceabe6a23e103688932c03ca5aca6da0de5ed95ff78dfd8feccd",
    ),
    "abox_tplg.bin": (
        FIRMWARE_ROOT / "abox_tplg.bin",
        1066664,
        "3690d21b6a7e91242ee423ea655604842969607c318769fe73fbc6db826eb9da",
    ),
    "abox_tplg.conf": (
        FIRMWARE_ROOT / "abox_tplg.conf",
        109315,
        "ddc3874ec797a085c8497a240d47b995fedbcc5c876483c94e2f236ecb79f782",
    ),
}


def run(command, *, timeout=25, text=True, input=None):
    return subprocess.run(
        [str(ROOT / "tools/s22-ssh"), command],
        capture_output=True,
        timeout=timeout,
        text=text,
        input=input,
    )


def remote_python(source, data=None, timeout=25, remote_timeout=None):
    prefix = ("timeout -k 2 %s " % remote_timeout) if remote_timeout else ""
    result = run(prefix + "python3 -c " + shlex.quote(source), timeout=timeout,
                 text=False, input=data)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout.decode(errors="replace")


STATUS = r'''import json,pathlib,stat
p=pathlib.Path
def read(path):
    try:
        return p(path).read_text().strip()
    except OSError as e:
        return "<error:%s>" % e
def prop(path):
    try:
        return p(path).read_bytes().rstrip(b"\0").decode(errors="replace")
    except OSError:
        return None
def file_info(path):
    try:
        s=p(path).stat()
        return {"exists":True,"size":s.st_size,"mode":stat.S_IMODE(s.st_mode),
                "uid":s.st_uid,"gid":s.st_gid}
    except OSError:
        return {"exists":False}
abox=p(ABOX)
dt=p('/sys/firmware/devicetree/base/abox@18c50000')
core=dt/'abox-core@18c55000'
core_names=[]
if core.exists():
    for child in sorted(core.iterdir()):
        try:
            name=(child/'samsung,name').read_bytes().rstrip(b'\0').decode()
        except OSError:
            continue
        core_names.append(name)
result={
 "power_control":read(abox/'power/control'),
 "runtime_status":read(abox/'power/runtime_status'),
 "calliope_version":read(abox/'calliope_version'),
 "reset_count":read(abox/'reset_count'),
 "service":read(abox/'service'),
 "cards":read('/proc/asound/cards'),
 "pcm":read('/proc/asound/pcm'),
 "sound_class":sorted(x.name for x in p('/sys/class/sound').iterdir()) if p('/sys/class/sound').exists() else [],
 "asoc_cards":read('/sys/kernel/debug/asoc/cards'),
 "guardian_pid1":read('/proc/1/comm'),
 "firmware_search_path":read('/sys/module/firmware_class/parameters/path'),
 "debug_mode_sysfs":next((read(x) for x in [abox/'debug_mode',abox/'debug'] if (abox/x.name).exists()), None),
 "failsafe_online":read('/proc/abox/failsafe/online'),
 "dt_core_names":core_names,
 "dt_core_path":str(core),
 "dt_topology_compatible":prop(dt/'abox-tplg@0/compatible'),
 "dt_sound_compatible":prop(p('/sys/firmware/devicetree/base/sound/compatible')),
 "destination_exists":p(DEST).exists(),
 "active_files":{name:file_info(p(ACTIVE)/name) for name in NAMES},
}
print(json.dumps(result))
'''

def source_manifest():
    result = {}
    for name, (path, size, expected) in FILES.items():
        if not path.is_file():
            raise RuntimeError("missing local artifact: %s" % path)
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if len(data) != size or actual != expected:
            raise RuntimeError("local artifact mismatch: %s size=%d sha=%s" %
                               (name, len(data), actual))
        result[name] = {"path": str(path), "size": size, "sha256": expected}
    return result


def source_abi_review():
    if not KERNEL_DEFCONFIG.is_file():
        raise RuntimeError("pinned kernel defconfig is unavailable")
    config = KERNEL_DEFCONFIG.read_text()
    expected = {
        "CONFIG_SND_SOC_SAMSUNG_AUDIO": "CONFIG_SND_SOC_SAMSUNG_AUDIO=m",
        "CONFIG_SAMSUNG_PRODUCT_SHIP": "CONFIG_SAMSUNG_PRODUCT_SHIP=y",
        "CONFIG_SND_SOC_SAMSUNG_ABOX_DEBUG": "# CONFIG_SND_SOC_SAMSUNG_ABOX_DEBUG is not set",
    }
    if any(line not in config.splitlines() for line in expected.values()):
        raise RuntimeError("pinned ABOX source config is not the reviewed shipping configuration")
    return {
        "kernel_head": KERNEL_HEAD,
        "defconfig": str(KERNEL_DEFCONFIG),
        "config": expected,
        "debug_default": "DEBUG_MODE_NONE for shipping build (source review)",
        "firmware_abi": "unproven at runtime: recovered blobs are source/vendor matched, but no binary version contract is exposed before boot",
    }


def status():
    source = STATUS
    for key, value in {
        "ABOX": ABOX_ROOT,
        "DEST": DEST_ROOT,
        "ACTIVE": ACTIVE_ROOT,
        "NAMES": tuple(FILES),
    }.items():
        source = source.replace(key, repr(value))
    return json.loads(remote_python(source))


def health():
    spec = importlib.util.spec_from_file_location(
        "gpu_trial", ROOT / "tools/gpu-compat/run-trial.py")
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    return trial.phone_health()


def kernel_capture():
    result = run("dmesg", timeout=25)
    if result.returncode:
        raise RuntimeError("dmesg capture failed: %s" % result.stderr)
    return result.stdout


def kernel_delta(text, boundary):
    lines = []
    for line in text.splitlines():
        match = re.match(r"^\[\s*([0-9.]+)\]", line)
        if match and float(match.group(1)) > boundary:
            lines.append(line)
    return lines


def stage(payloads):
    stage_source = r'''import hashlib,json,os,pathlib,sys
p=pathlib.Path
root=p(DEST)
active=p(ACTIVE)
assert p('/proc/1/comm').read_text().strip()=='native-guardian'
assert p('/sys/module/firmware_class/parameters/path').read_text().strip()=='/vendor/firmware'
assert active.is_dir(), 'guardian firmware root is unavailable'
assert not root.exists(), 'destination already exists; refusing overwrite'
targets=[active/name for name in NAMES]
assert not any(x.exists() or x.is_symlink() for x in targets), 'active target exists; refusing overwrite'
data=sys.stdin.buffer.read()
offset=0
items=[]
for name,size,want in MANIFEST:
    chunk=data[offset:offset+size]
    offset += size
    assert len(chunk)==size and hashlib.sha256(chunk).hexdigest()==want, name
    items.append((name,chunk))
assert offset==len(data)
root.mkdir(parents=True,exist_ok=False)
for name,chunk in items:
    for target in (root/name,active/name):
        with target.open('xb') as f:
            f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(target,0o644)
        st=target.stat()
        assert st.st_size==len(chunk) and hashlib.sha256(target.read_bytes()).hexdigest()==hashlib.sha256(chunk).hexdigest()
        assert (st.st_mode & 0o777)==0o644 and st.st_uid==0 and st.st_gid==0
print(json.dumps({'staged':True,'destination':str(root),'active_root':str(active),
                  'files':NAMES,'permissions':'0644 root:root'}))
'''

    replacements = {
        "DEST": DEST_ROOT,
        "ACTIVE": ACTIVE_ROOT,
        "NAMES": tuple(FILES),
        "MANIFEST": tuple((name, FILES[name][1], FILES[name][2]) for name in FILES),
    }
    for key, value in replacements.items():
        stage_source = stage_source.replace(key, repr(value))
    data = b"".join(payloads)
    return json.loads(remote_python(stage_source, data=data, timeout=45))


def write_power(value):
    source = r'''import pathlib
p=pathlib.Path('/sys/devices/platform/18c50000.abox/power/control')
p.write_text(VALUE+'\n')
assert p.read_text().strip()==VALUE
print('power_control='+p.read_text().strip())
'''.replace("VALUE", repr(value))
    return remote_python(source, timeout=10, remote_timeout=5).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true",
                        help="stage four files, write power/control=on once, observe, restore")
    parser.add_argument("--accept-unproven-firmware-abi", action="store_true",
                        help="required extra acknowledgement: source review cannot prove blob ABI before boot")
    parser.add_argument("--observe-seconds", type=float, default=15.0,
                        help="bounded observation interval for --start (default: 15; max: 60)")
    args = parser.parse_args()
    if args.observe_seconds < 0 or args.observe_seconds > 60:
        parser.error("--observe-seconds must be between 0 and 60")

    manifest = source_manifest()
    abi_review = source_abi_review()
    if not args.start:
        before_health = health()
        before = status()
        print(json.dumps({"mode":"audit", "start_performed":False,
                          "source_manifest":manifest, "abi_review":abi_review,
                          "health":before_health,
                          "status":before}, indent=2))
        return

    before_health = health()
    before = status()
    if not args.accept_unproven_firmware_abi:
        raise RuntimeError("refusing activation: firmware ABI is not provable before boot; pass --accept-unproven-firmware-abi after parent review")
    if before["failsafe_online"] not in ("ONLINE", "<error:[Errno 2] No such file or directory: '/proc/abox/failsafe/online'>"):
        raise RuntimeError("ABOX failsafe is not known ONLINE; refusing activation")
    if before["debug_mode_sysfs"] not in (None, "0", "none", "NONE"):
        raise RuntimeError("ABOX debug mode is active; refusing activation")
    if before["power_control"] != "auto":
        raise RuntimeError("ABOX power/control is not untouched auto; refusing repeat start")
    if before["destination_exists"]:
        raise RuntimeError("staging destination already exists; refusing overwrite")
    required_core = {"calliope_sram.bin", "calliope_dram.bin"}
    if not required_core.issubset(set(before["dt_core_names"])):
        raise RuntimeError("DT core names do not include the exact recovered core files")
    if before["dt_topology_compatible"] is None or "samsung,abox-tplg" not in before["dt_topology_compatible"]:
        raise RuntimeError("DT topology node is not samsung,abox-tplg")
    if before["dt_sound_compatible"] is None or "samsung,rainbow-prince" not in before["dt_sound_compatible"]:
        raise RuntimeError("DT sound node is not samsung,rainbow-prince")
    if any(info.get("exists") for info in before["active_files"].values()):
        raise RuntimeError("one or more guardian firmware targets already exist")

    payloads = [FILES[name][0].read_bytes() for name in FILES]
    raw = EVIDENCE_ROOT
    raw.mkdir(parents=True, exist_ok=False)
    (raw / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (raw / "before.json").write_text(
        json.dumps({"health":before_health,"status":before}, indent=2) + "\n")
    kernel_before = kernel_capture()
    (raw / "before-kernel.txt").write_text(kernel_before)
    boundary = max((float(m.group(1)) for m in re.finditer(
        r"^\[\s*([0-9.]+)\]", kernel_before, re.M)), default=0.0)

    began = datetime.now(timezone.utc).isoformat()
    staged = stage(payloads)
    (raw / "stage.json").write_text(json.dumps(staged, indent=2) + "\n")
    changed = True
    restore_error = None
    start_error = None
    during = None
    try:
        write_power("on")
        time.sleep(args.observe_seconds)
        during = status()
    except Exception as exc:
        start_error = repr(exc)
    finally:
        try:
            write_power(before["power_control"])
        except Exception as exc:
            restore_error = repr(exc)

    after = status()
    after_health = health()
    kernel_after = kernel_capture()
    (raw / "after-kernel.txt").write_text(kernel_after)
    delta = kernel_delta(kernel_after, boundary)
    (raw / "kernel-delta.txt").write_text("\n".join(delta) + "\n")
    receipt = {
        "started_at": began,
        "mode": "start",
        "start_performed": changed and start_error is None,
        "restored_power_control": restore_error is None,
        "restore_error": restore_error,
        "start_error": start_error,
        "observe_seconds": args.observe_seconds,
        "source_manifest": manifest,
        "abi_review": abi_review,
        "before": {"health":before_health,"status":before},
        "during_active": during,
        "after": {"health":after_health,"status":after},
        "same_boot": before_health.get("boot_id") == after_health.get("boot_id"),
        "kernel_delta": delta,
        "alsa_pcm_nonempty": bool(after["pcm"].strip() and not after["pcm"].startswith("<error:")),
        "notes": [
            "No ALSA PCM was opened; cards/pcm are read-only observations.",
            "No module, bind, unbind, partition, reboot, or persistent overlay operation was used.",
            "A nonzero calliope_version and a PCM card remain separate acceptance checks.",
        ],
    }
    (raw / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    if start_error or restore_error or not receipt["same_boot"]:
        raise RuntimeError("ABOX observation did not restore cleanly; inspect receipt before any retry")


if __name__ == "__main__":
    main()
