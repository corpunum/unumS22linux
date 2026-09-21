#!/usr/bin/env python3
"""Stage the exact four ABOX firmware files, without starting any driver.

The default operation is a local hash audit plus a read-only phone audit.
--stage performs only two classes of phone writes: new files below the fixed
userdata staging directory and absent files below /proc/1/root/vendor/firmware.
It never writes sysfs, partitions, device nodes, module controls, or init
state. A clean future boot can consume the staged files before normal probe
ordering; this tool does not arrange that boot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_ROOT = ROOT / "rootfs/bt-audio-vendor-assets/vendor/firmware"
DEST_ROOT = "/srv/s22/audio-reuse-20260921/audio-firmware-stage-20260921"
ACTIVE_ROOT = "/proc/1/root/vendor/firmware"

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


def manifest(files=FILES):
    result = {}
    for name, (path, size, expected) in files.items():
        if Path(path).is_symlink() or not Path(path).is_file():
            raise RuntimeError("missing or symlinked artifact: %s" % path)
        data = Path(path).read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if len(data) != size or actual != expected:
            raise RuntimeError("%s: expected %d/%s, got %d/%s" %
                               (name, size, expected, len(data), actual))
        result[name] = {"source": str(path), "bytes": size, "sha256": expected}
    return result


def remote(command, *, data=None, timeout=45):
    return subprocess.run(
        [str(ROOT / "tools/s22-ssh"), command],
        input=data, text=False, capture_output=True, timeout=timeout,
    )


def remote_python_script(script, *, data=None, timeout=45):
    result = remote("python3 -c " + shlex.quote(script), data=data, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout.decode(errors="replace")


def audit_script(names=tuple(FILES)):
    return r'''import json,pathlib,stat
p=pathlib.Path
active=p(ACTIVE)
dest=p(DEST)
def info(path):
    try:
        s=path.stat()
        return {"exists":True,"bytes":s.st_size,"mode":stat.S_IMODE(s.st_mode),
                "uid":s.st_uid,"gid":s.st_gid}
    except OSError:
        return {"exists":False}
print(json.dumps({
 "pid1":p('/proc/1/comm').read_text().strip(),
 "firmware_search_path":p('/sys/module/firmware_class/parameters/path').read_text().strip(),
 "destination_exists":dest.exists() or dest.is_symlink(),
 "active_root_exists":active.is_dir(),
 "active":{name:info(active/name) for name in NAMES},
}))
'''.replace("ACTIVE", repr(ACTIVE_ROOT)).replace(
        "DEST", repr(DEST_ROOT)).replace("NAMES", repr(names))


def stage_script(manifest_data, names=tuple(FILES)):
    entries = tuple((name, item["bytes"], item["sha256"])
                    for name, item in manifest_data.items())
    source = r'''import hashlib,json,os,pathlib,sys
p=pathlib.Path
active=p(ACTIVE)
dest=p(DEST)
assert p('/proc/1/comm').read_text().strip()=='native-guardian'
assert p('/sys/module/firmware_class/parameters/path').read_text().strip()=='/vendor/firmware'
assert active.is_dir(), 'guardian firmware root is absent'
assert not dest.exists() and not dest.is_symlink(), 'refusing existing userdata target'
targets=[active/name for name in NAMES]
assert not any(x.exists() or x.is_symlink() for x in targets), 'refusing existing firmware target'
blob=sys.stdin.buffer.read()
offset=0
items=[]
for name,size,want in MANIFEST:
    chunk=blob[offset:offset+size]
    offset += size
    assert len(chunk)==size and hashlib.sha256(chunk).hexdigest()==want, name
    items.append((name,chunk))
assert offset==len(blob)
dest.mkdir(parents=True,exist_ok=False)
for name,chunk in items:
    for target in (dest/name,active/name):
        with target.open('xb') as f:
            f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(target,0o644)
        st=target.stat()
        assert st.st_size==len(chunk)
        assert hashlib.sha256(target.read_bytes()).hexdigest()==hashlib.sha256(chunk).hexdigest()
        assert (st.st_mode & 0o777)==0o644 and st.st_uid==0 and st.st_gid==0
print(json.dumps({'staged':True,'destination':str(dest),'active_root':str(active),
                  'files':NAMES,'mode':'0644 root:root'}))
'''
    return (source.replace("ACTIVE", repr(ACTIVE_ROOT))
                  .replace("DEST", repr(DEST_ROOT))
                  .replace("NAMES", repr(names))
                  .replace("MANIFEST", repr(entries)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", action="store_true",
                        help="write only the new userdata and guardian firmware targets")
    args = parser.parse_args(argv)
    local = manifest()
    if not args.stage:
        remote_status = json.loads(remote_python_script(audit_script()))
        print(json.dumps({"mode":"audit","stage_performed":False,
                          "manifest":local,"remote":remote_status}, indent=2))
        return 0
    payload = b"".join(Path(item[0]).read_bytes() for item in FILES.values())
    result = json.loads(remote_python_script(stage_script(local), data=payload, timeout=90))
    print(json.dumps({"mode":"stage","stage_performed":True,
                      "manifest":local,"remote":result,
                      "constraints":["no sysfs or power writes","no module/bind/unbind",
                                     "no partition/device-node/reboot"]}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print("audio-firmware-stage: %s" % exc, file=sys.stderr)
        raise SystemExit(2)
