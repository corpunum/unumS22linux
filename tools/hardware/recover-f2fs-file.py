#!/usr/bin/env python3
"""Recover one compressed F2FS inode using a bounded fsck -M block map."""
from pathlib import Path
import argparse, hashlib, os, re, struct, subprocess, tempfile
import lz4.block

def slots_for(mapfile, path):
    clean = Path(mapfile).read_text(errors="ignore").replace("\x1b[2K", "")
    for line in clean.splitlines():
        if line.startswith(path + " "):
            out=[]
            for tok in line.split()[1:]:
                if tok == "0": out.append(0)
                elif re.fullmatch(r"\d+(?:-\d+)?", tok):
                    a=tok.split("-"); out.extend(range(int(a[0]), int(a[-1])+1))
            return out
    raise RuntimeError(f"map entry not found: {path}")

def inode_dump(image, ino):
    with tempfile.TemporaryDirectory() as td:
        d=Path(td); (d/"dump.f2fs").symlink_to("/tmp/f2fs-tools.pTclc2/root/sbin/fsck.f2fs")
        p=subprocess.run([str(d/"dump.f2fs"),"-i",hex(ino),str(image)],cwd=d,input=b"N\n",stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=True)
    return p.stdout

def inode_size_from_dump(dump):
    m=re.search(rb"i_size\s+\[0x\s*[0-9a-f]+\s*:\s*(\d+)\]",dump)
    if not m: raise RuntimeError("inode size unavailable")
    return int(m.group(1))

def inline_data_from_dump(dump, size):
    """Decode a complete inline word range; never collapse missing addresses.

    This metadata tool can omit zero words. A gap is therefore ambiguous
    (zero or incomplete dump), so refuse it instead of inventing bytes.
    """
    inline=re.search(rb"i_inline\s+\[0x\s*([0-9a-f]+)\s*:",dump)
    extra=re.search(rb"i_extra_isize\s+\[0x\s*[0-9a-f]+\s*:\s*(\d+)\]",dump)
    if not inline or not extra or not (int(inline.group(1),16) & 0x2):
        return None
    extra_size=int(extra.group(1))
    # This captured 4KiB inode has 923 address words, before extra attributes.
    if extra_size % 4 or extra_size < 0 or extra_size >= 923 * 4 or size < 0:
        raise RuntimeError("invalid inline metadata geometry")
    start=(extra_size // 4) + 1
    end=start + (size + 3) // 4
    if end > 923:
        raise RuntimeError("inline size exceeds inode address area")
    words={}
    for m in re.finditer(rb"i_addr\[0x([0-9a-f]+)\]\s+\[0x\s*([0-9a-f]+)",dump):
        index,value=int(m.group(1),16),int(m.group(2),16)
        if index in words or index >= 923 or value > 0xffffffff:
            raise RuntimeError("duplicate or out-of-range inline word")
        words[index]=value
    missing=[i for i in range(start,end) if i not in words]
    if missing:
        raise RuntimeError(f"inline dump incomplete: first missing word {missing[0]:x}")
    data=b"".join(struct.pack("<I",words[i]) for i in range(start,end))
    return data[:size]

def recover(image, slots, size):
    out=bytearray(); block=4096
    with open(image,"rb") as f:
        for i in range(0,len(slots),4):
            g=slots[i:i+4]; nz=[x for x in g if x]
            if not nz: out.extend(b"\0"*16384); continue
            if g[0] == 0:
                payload=bytearray()
                for j,a in enumerate(nz):
                    f.seek(a*block); raw=f.read(block); payload.extend(raw[24:] if j==0 else raw)
                f.seek(nz[0]*block); clen=struct.unpack("<I",f.read(4))[0]
                out.extend(lz4.block.decompress(bytes(payload[:clen]),uncompressed_size=16384))
            else:
                for a in nz: f.seek(a*block); out.extend(f.read(block))
    return bytes(out[:size])

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("image",type=Path); ap.add_argument("map",type=Path); ap.add_argument("path"); ap.add_argument("inode",type=lambda x:int(x,0)); ap.add_argument("output",type=Path); args=ap.parse_args()
    args.image=args.image.resolve(); args.map=args.map.resolve()
    if os.geteuid() == 0 or args.image.stat().st_mode & 0o222:
        raise SystemExit("use an unprivileged account and a read-only image copy")
    if args.output.exists():
        raise SystemExit("refusing to overwrite an existing recovered file")
    dump=inode_dump(args.image,args.inode); size=inode_size_from_dump(dump)
    data=inline_data_from_dump(dump,size)
    slots=None if data is not None else slots_for(args.map,args.path)
    if data is None:
        data=recover(args.image,slots,size)
    if len(data)!=size or not any(data): raise SystemExit("recovery failed: size/zero validation")
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_bytes(data)
    block_note="inline" if slots is None else str(len(slots))
    print(f"path={args.path} inode=0x{args.inode:x} size={size} blocks={block_note} sha256={hashlib.sha256(data).hexdigest()}")

if __name__ == "__main__": main()
