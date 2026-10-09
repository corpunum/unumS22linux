#!/usr/bin/env python3
# Count scanout framebuffer changes on plane-0 (exynos-drmdpu, debugfs) over N seconds = frames really sent to the panel.
import re, sys, time
n = float(sys.argv[1]) if len(sys.argv) > 1 else 10
end = time.time() + n
last, changes, samples = None, 0, 0
while time.time() < end:
    s = open('/sys/kernel/debug/dri/1/state').read()
    m = re.search(r'plane-0\n\tcrtc=\S+\n\tfb=(\d+)', s)
    fb = m.group(1) if m else None
    if last is not None and fb != last:
        changes += 1
    last = fb
    samples += 1
    time.sleep(0.004)
print(f'{{"seconds": {n}, "fb_changes": {changes}, "fps_lower_bound": {changes / n:.1f}, "samples": {samples}}}')
