#!/usr/bin/env python3
"""Flatten the stock vendor mixer_paths.xml into amixer -s batch files.

usage: gen-mixer-batches.py VENDOR_MIXER_PATHS_XML OUTDIR PATH...
Writes OUTDIR/defaults.amixer (top-level <ctl> defaults, applied once per
boot like the Android HAL does) and OUTDIR/<path>.amixer for each named path
(nested <path> references expanded in order).
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def quote(v: str) -> str:
    return "'" + v.replace("'", "") + "'"


def main() -> int:
    src, out, *names = sys.argv[1:]
    root = ET.parse(src).getroot()
    paths = {p.get('name'): p for p in root if p.tag == 'path'}

    def flat(el, acc):
        for c in el:
            if c.tag == 'ctl':
                acc.append((c.get('name'), c.get('value')))
            elif c.tag == 'path':
                flat(paths[c.get('name')], acc)
        return acc

    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    groups = {'defaults': [(c.get('name'), c.get('value')) for c in root if c.tag == 'ctl']}
    for n in names:
        groups[n] = flat(paths[n], [])
    for n, items in groups.items():
        lines = [f"cset name={quote(k)} {quote(v)}" for k, v in items]
        (out / f'{n}.amixer').write_text('\n'.join(lines) + '\n')
        print(n, len(lines))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
