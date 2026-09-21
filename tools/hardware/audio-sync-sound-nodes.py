#!/usr/bin/env python3
"""Synchronize validated ALSA character nodes from sysfs (opt-in writes).

Default mode is a plan. ``--apply`` creates only missing nodes below the
specified sound device directory. It never opens PCM/control nodes and never
writes mixer or sysfs controls. Existing wrong-type, symlinked, or mismatched
nodes abort the operation.
"""
from __future__ import annotations

import argparse
import grp
import json
import os
from pathlib import Path
import re
import stat

NAME_RE = re.compile(r"^(?:controlC\d+|pcmC\d+D\d+[cp]|timer)$")

def uevent_identity(path: Path, name: str) -> tuple[int, int]:
    try:
        data = (path / "uevent").read_text().splitlines()
        values = dict(line.split("=", 1) for line in data if "=" in line)
        if values.get('DEVNAME') != 'snd/'+name:
            raise ValueError('DEVNAME does not match selected ALSA node')
        return int(values["MAJOR"]), int(values["MINOR"])
    except (OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"missing/invalid uevent identity for {path}: {exc}")

def entries(sysfs: Path, devices_root: Path, only: set[str]) -> list[tuple[str, int, int]]:
    result = []
    try:
        names = sorted(os.listdir(sysfs))
    except OSError as exc:
        raise SystemExit(f"cannot list sysfs sound class: {exc}")
    for name in names:
        if name not in only or not NAME_RE.fullmatch(name):
            continue
        node = sysfs / name
        try:
            resolved = node.resolve(strict=True)
            devices_root = devices_root.resolve(strict=True)
            resolved.relative_to(devices_root)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"sysfs entry does not resolve below /sys/devices: {node}: {exc}")
        if not resolved.is_dir():
            raise SystemExit(f"resolved sysfs entry is not a directory: {resolved}")
        try:
            raw = (node / "dev").read_text().strip()
            major, minor = (int(part, 10) for part in raw.split(":", 1))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"missing/invalid dev metadata for {node}: {exc}")
        if major != 116 or minor < 0:
            raise SystemExit(f"negative device identity for {node}")
        if (major, minor) != uevent_identity(resolved, name):
            raise SystemExit(f"class/dev and resolved uevent identity differ for {node}")
        result.append((name, major, minor))
    if {name for name, _, _ in result} != only:
        raise SystemExit('selected sound node is missing from sysfs')
    return result

def target_state(path: Path, major: int, minor: int) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(info.st_mode):
        return "symlink"
    if not stat.S_ISCHR(info.st_mode):
        return "wrong-type"
    if info.st_rdev != os.makedev(major, minor):
        return f"wrong-rdev:{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"
    return "correct"

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sysfs-root", type=Path, default=Path("/sys/class/sound"))
    parser.add_argument("--devices-root", type=Path, default=Path("/sys/devices"))
    parser.add_argument("--dev-root", type=Path, default=Path("/dev/snd"))
    parser.add_argument("--only", action="append",
                        help="exact node basename (repeatable; default: controlC0)")
    parser.add_argument("--apply", action="store_true", help="create missing character nodes")
    args = parser.parse_args()
    args.only = args.only or ['controlC0']
    if args.apply:
        if os.geteuid()!=0 or args.sysfs_root!=Path('/sys/class/sound') or args.devices_root!=Path('/sys/devices') or args.dev_root!=Path('/dev/snd'):
            raise SystemExit('apply requires native root and exact sound sysfs/device roots')
        if Path('/proc/1/comm').read_text().strip()!='native-guardian' or Path('/proc/sys/kernel/osrelease').read_text().strip()!='5.10.260-g4e5c5ad7d950':
            raise SystemExit('unexpected native kernel/session')
    if any(not NAME_RE.fullmatch(name) for name in args.only):
        raise SystemExit("--only contains unsafe node name")
    wanted = entries(args.sysfs_root, args.devices_root, set(args.only))
    try:
        destination = args.dev_root.lstat()
    except OSError as exc:
        raise SystemExit(f"sound destination is absent: {args.dev_root}: {exc}")
    if not stat.S_ISDIR(destination.st_mode) or destination.st_uid != 0 or stat.S_ISLNK(destination.st_mode):
        raise SystemExit("sound destination must be a root-owned directory")
    if destination.st_mode & 0o022:
        raise SystemExit("sound destination is group/other writable")
    states = [{"name": name, "major": major, "minor": minor,
               "state": target_state(args.dev_root / name, major, minor)}
              for name, major, minor in wanted]
    bad = [item for item in states if item["state"] not in ("missing", "correct")]
    if bad:
        raise SystemExit("refusing unsafe existing sound node: " + repr(bad))
    missing = [item for item in states if item["state"] == "missing"]
    if args.apply:
        try:
            audio_gid = grp.getgrnam("audio").gr_gid
        except KeyError:
            audio_gid = 0
        directory=os.open(args.dev_root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            current=os.fstat(directory)
            if (current.st_dev,current.st_ino)!=(destination.st_dev,destination.st_ino) or current.st_mode & 0o022:
                raise SystemExit('sound directory changed')
            for item in missing:
                name=item['name']; device=os.makedev(item['major'],item['minor'])
                # mknod refuses an existing entry, including a raced-in symlink.
                os.mknod(name,stat.S_IFCHR|0o600,device,dir_fd=directory)
                os.chown(name,0,audio_gid,dir_fd=directory,follow_symlinks=False)
                # The held directory is root-owned and non-writable to other
                # users; avoid requiring newer fchmodat2 on this 5.10 kernel.
                os.chmod(name,0o660 if audio_gid else 0o600,dir_fd=directory)
                actual=os.stat(name,dir_fd=directory,follow_symlinks=False)
                if not stat.S_ISCHR(actual.st_mode) or actual.st_rdev!=device:
                    raise SystemExit('created sound node identity mismatch')
        finally:os.close(directory)
    print(json.dumps({"apply": args.apply,
                      "created": [item["name"] for item in missing] if args.apply else [],
                      "nodes": states, "pcm_opened": [], "mixer_writes": []},
                     sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
