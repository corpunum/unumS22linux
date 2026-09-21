#!/usr/bin/env python3
"""Copy only the isolated runtime into a new, explicitly named phone directory."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'rootfs/gpu-compat-20260921/runtime'
SSH = ROOT / 'tools/s22-ssh'

def main():
    p=argparse.ArgumentParser()
    p.add_argument('variant', choices=['base','opencl-real','opencl-headless','vulkan-headless','llama-vulkan'])
    supplement=p.add_mutually_exclusive_group()
    supplement.add_argument('--add-binary', choices=['opencl-capabilities','vulkan-hal-probe','llama-completion'])
    supplement.add_argument('--add-library', choices=['libgralloctypes.so','bridge-v2/libvulkan.so','bridge-v3/libvulkan.so','bridge-v4/libvulkan.so'])
    args=p.parse_args()
    dest='/srv/s22/gpu-compat-20260921/'+args.variant
    if args.add_binary or args.add_library:
        name=args.add_binary or args.add_library
        subdir='system/bin' if args.add_binary else 'vendor/lib64'
        target=dest+'/'+subdir+'/'+name
        expected=json.loads((ROOT/'rootfs/gpu-compat-20260921'/('manifest-'+args.variant+'.json')).read_text())
        digest=hashlib.sha256((SOURCE/subdir/name).read_bytes()).hexdigest()
        archive=subprocess.Popen(['tar','-cf','-','-C',str(SOURCE/subdir),name],stdout=subprocess.PIPE)
        command='test ! -e '+shlex.quote(target)+' && mkdir -p '+shlex.quote(str(Path(target).parent))+' && tar -xf - -C '+shlex.quote(dest+'/'+subdir)+' && chown 0:0 '+shlex.quote(target)+' && chmod 755 '+shlex.quote(target)
        result=subprocess.run([str(SSH),command],stdin=archive.stdout,capture_output=True,text=True,timeout=30)
        archive.stdout.close()
        if result.returncode or archive.wait(timeout=10):
            raise RuntimeError(result.stderr)
        check=subprocess.run([str(SSH),'sha256sum '+shlex.quote(target)],capture_output=True,text=True,timeout=15)
        assert check.returncode==0 and check.stdout.split()[0]==digest
        expected[subdir+'/'+name]=digest
        (ROOT/'rootfs/gpu-compat-20260921'/('manifest-'+args.variant+'.json')).write_text(json.dumps(expected,indent=2)+'\n')
        print(json.dumps(dict(added=target,sha256=digest,existing_files_unchanged=True)))
        return
    hashes={str(f.relative_to(SOURCE)):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in SOURCE.rglob('*') if f.is_file() and not f.is_symlink()}
    command='test ! -e '+shlex.quote(dest)+' && mkdir -p '+shlex.quote(dest)+' && tar -xf - -C '+shlex.quote(dest)
    archive=subprocess.Popen(['tar','-cf','-','-C',str(SOURCE),'.'],stdout=subprocess.PIPE)
    sent=subprocess.run([str(SSH),command],stdin=archive.stdout,capture_output=True,text=True,timeout=120)
    archive.stdout.close()
    code=archive.wait(timeout=10)
    if sent.returncode or code:
        raise RuntimeError('Staging failed; preserve partial target for inspection: '+sent.stderr)
    # Native Python performs only in-process hash checks and test-directory permissions.
    script='''import hashlib,json,os,pathlib,stat
root=pathlib.Path(DEST)
expected=EXPECTED
for name,wanted in expected.items():
 p=root/name
 assert hashlib.sha256(p.read_bytes()).hexdigest()==wanted, name
for p in [root]+list(root.rglob('*')):
 os.chown(p,0,0,follow_symlinks=False)
 if not p.is_symlink():
  os.chmod(p,0o755 if p.is_dir() or p.stat().st_mode & 0o111 else 0o644)
for name in ['data','data/local','data/local/tmp','cache','tmp']:
 os.chown(root/name,1000,1000)
 os.chmod(root/name,0o700)
print(json.dumps(dict(verified_files=len(expected),variant=root.name,devices_exposed=False)))
'''.replace('DEST',repr(dest)).replace('EXPECTED',repr(hashes))
    checked=subprocess.run([str(SSH),'python3 -c '+shlex.quote(script)],capture_output=True,text=True,timeout=30)
    if checked.returncode:
        raise RuntimeError(checked.stderr)
    (ROOT/'rootfs/gpu-compat-20260921'/('manifest-'+args.variant+'.json')).write_text(json.dumps(hashes,indent=2)+'\n')
    print(checked.stdout.strip())

if __name__=='__main__':
    main()
