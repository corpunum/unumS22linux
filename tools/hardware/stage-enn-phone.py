#!/usr/bin/env python3
"""Stage one reviewed ENN diagnostic root on the phone; never execute it."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "rootfs/npu-compat-20260921"
DEST = "/srv/s22/npu-compat-20260921"
SSH = ROOT / "tools/s22-ssh"


def main() -> int:
    global SOURCE, DEST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="print hashes and do not contact the phone")
    parser.add_argument("--init-only", action="store_true", help="stage the separate initialization probe root")
    args = parser.parse_args()
    if args.init_only:
        SOURCE = ROOT / "rootfs/npu-init-20260921"
        DEST = "/srv/s22/npu-init-20260921"
    probe = "bin/enn-init-probe" if args.init_only else "bin/enn-dlopen-probe"
    manifest = json.loads((SOURCE / "manifests/files.json").read_text())
    expected = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    local = {
        str(path.relative_to(SOURCE)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in SOURCE.rglob("*")
        if path.is_file() and path != SOURCE / "manifests/files.json"
    }
    if any(path.is_symlink() for path in SOURCE.rglob("*")):
        raise SystemExit("symlinks are forbidden in the NPU stage")
    if local != expected:
        raise SystemExit("local NPU stage differs from manifests/files.json")
    summary = {
        "destination": DEST,
        "files": len(expected),
        "manifest_sha256": hashlib.sha256((SOURCE / "manifests/files.json").read_bytes()).hexdigest(),
        "probe_sha256": expected[probe],
        "execution": False,
    }
    if args.plan:
        print(json.dumps(summary, indent=2))
        return 0

    # Refuse to overwrite a phone stage.  The archive is made only from this
    # reviewed NPU directory; stock images and userdata are never read here.
    archive = subprocess.Popen(
        ["tar", "-cf", "-", "-C", str(SOURCE), "."], stdout=subprocess.PIPE
    )
    remote_prepare = (
        "test ! -e " + shlex.quote(DEST)
        + " && mkdir -p " + shlex.quote(DEST)
        + " && tar -xf - -C " + shlex.quote(DEST)
    )
    sent = subprocess.run(
        [str(SSH), remote_prepare], stdin=archive.stdout,
        capture_output=True, text=True, timeout=120
    )
    assert archive.stdout is not None
    archive.stdout.close()
    archive_status = archive.wait(timeout=10)
    if sent.returncode or archive_status:
        raise RuntimeError("NPU stage transfer failed: " + sent.stderr)

    check_script = """
import hashlib,json,os,pathlib,stat
root=pathlib.Path(DEST)
expected=EXPECTED
actual={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file() and p != root/'manifests/files.json'}
assert actual == expected, (set(expected)-set(actual), set(actual)-set(expected))
for p in [root]+list(root.rglob('*')):
    assert not p.is_symlink(), p
    os.chown(p,0,0,follow_symlinks=False)
    if not p.is_symlink():
        os.chmod(p,0o755 if p.is_dir() or str(p.relative_to(root)) in {'bin/enn-dlopen-probe','bin/enn-init-probe','system/bin/linker64'} else 0o644)
for p in [root]+list(root.rglob('*')):
    info=p.lstat()
    assert info.st_uid == 0 and info.st_gid == 0, p
    assert not info.st_mode & 0o022, p
print(json.dumps({'verified_files':len(actual),'destination':str(root),'execution':False}))
""".replace("DEST", repr(DEST)).replace("EXPECTED", repr(expected))
    checked = subprocess.run(
        [str(SSH), "python3 -c " + shlex.quote(check_script)],
        capture_output=True, text=True, timeout=45
    )
    if checked.returncode:
        raise RuntimeError("NPU stage verification failed: " + checked.stderr)
    print(checked.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
