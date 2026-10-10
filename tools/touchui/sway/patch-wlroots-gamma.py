#!/usr/bin/env python3
"""Make wlroots 0.20.2 re-enable the S22 panel after an output power-off.

Root cause (phase 2 follow-up, 2026-10-10): exynos-drmdpu CRTC 184 has no
GAMMA_LUT property and a legacy gamma size of 0. When a wlroots output is
re-enabled, wlr_scene re-applies the (identity) gamma LUT, so the commit
carries WLR_OUTPUT_STATE_COLOR_TRANSFORM = NULL. On such a CRTC
drm_legacy_crtc_set_gamma(size=0) "resets" via the legacy API, finds a gamma
size of 0 and returns false without logging, so sway's real modeset fails
("Backend commit failed") right after the mode blob is created, with no
DRM_IOCTL_MODE_ATOMIC issued (strace). The TEST_ONLY commit passed because
it did not carry the colour transform.

Fix: resetting gamma to identity on a CRTC that has no gamma table is a
successful no-op. Upstream-equivalent source change, backend/drm/legacy.c:
    size = drm_crtc_get_gamma_lut_size(drm, crtc);
    if (size == 0) {
-       return false;
+       return true;   // nothing to reset
    }
This tool applies exactly that one instruction change (mov w0,#0 -> mov w0,#1
at the size==0 return of drm_legacy_crtc_set_gamma) to a private copy of the
Arch Linux ARM wlroots0.20 0.20.2-1 library, refusing any other input.
The stock /usr/lib copy is never modified; sway picks the copy up through
LD_LIBRARY_PATH=/opt/s22-wlroots.

usage: patch-wlroots-gamma.py SRC DST
"""
import hashlib
import os
import sys

SRC_SHA256 = '5e49d34a8221460b18bb470363b4e99ac029d585451e6aee149f8d3e31bc6c61'  # wlroots0.20 0.20.2-1 aarch64
DST_SHA256 = '7eb26dc747a0fefb827edca303e756c149e824a3c0f43711739d999eae3f512e'
OFFSET = 0x6a718                 # file offset == vaddr (first PT_LOAD at 0)
OLD = bytes.fromhex('00008052')  # mov w0, #0x0
NEW = bytes.fromhex('20008052')  # mov w0, #0x1
CONTEXT = (0x6a710, bytes.fromhex('600600b5f51340f9'))  # cbnz x0,...; ldr x21,[sp,#32]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    src, dst = sys.argv[1:]
    data = bytearray(open(src, 'rb').read())
    if hashlib.sha256(data).hexdigest() != SRC_SHA256:
        print(f'refusing: {src} is not the expected wlroots 0.20.2-1 build', file=sys.stderr)
        return 1
    off, ctx = CONTEXT
    if data[off:off + len(ctx)] != ctx or data[OFFSET:OFFSET + 4] != OLD:
        print('refusing: instruction context mismatch', file=sys.stderr)
        return 1
    data[OFFSET:OFFSET + 4] = NEW
    if hashlib.sha256(data).hexdigest() != DST_SHA256:
        print('refusing: patched digest mismatch', file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = dst + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.chmod(tmp, 0o755)
    os.replace(tmp, dst)
    print(f'patched {dst} sha256={DST_SHA256}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
