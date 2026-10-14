#!/usr/bin/env python3
"""Chunked capture recorder: stop/release camera after every bounded capture."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--client', default=os.path.join(os.path.dirname(__file__), 's22-camera.py'))
    p.add_argument('--out', required=True)
    p.add_argument('--seconds', type=float, required=True)
    p.add_argument('--fps', type=float, default=5)
    p.add_argument('--chunk-frames', type=int, required=True)
    p.add_argument('--sensor', choices=('rear','front'), default='rear')
    p.add_argument('--exposure-us', type=int, default=20000)
    p.add_argument('--gain', type=float, default=8)
    p.add_argument('--mjpeg', action='store_true')
    a = p.parse_args()
    if a.seconds <= 0 or a.fps <= 0 or a.chunk_frames <= 0:
        p.error('seconds, fps and chunk-frames must be positive')
    target = int(round(a.seconds * a.fps))
    os.makedirs(a.out, exist_ok=True)
    frames = 0
    started = time.monotonic()
    for chunk in range((target + a.chunk_frames - 1) // a.chunk_frames):
        count = min(a.chunk_frames, target - frames)
        with tempfile.TemporaryDirectory(prefix='s22-safe-cap-') as d:
            cmd = [sys.executable, '-I', '-B', a.client, 'capture', '--sensor', a.sensor,
                   '--frames', str(count), '--skip', '0', '--deadline', '120',
                   '--allow-fw-stall', '--cis-exposure-us', str(a.exposure_us),
                   '--cis-again', str(a.gain), '--out', os.path.join(d, 'cap.png'),
                   '--raw', os.path.join(d, 'cap.raw')]
            t = time.monotonic()
            run = subprocess.run(cmd, text=True, capture_output=True)
            elapsed = time.monotonic() - t
            print(run.stdout.strip(), flush=True)
            if run.returncode:
                raise SystemExit(f'capture chunk {chunk} failed rc={run.returncode}: {run.stderr[-1000:]}')
            row = json.loads(run.stdout.strip().splitlines()[-1])
            if not row.get('ok') or row.get('frames') != count:
                raise SystemExit(f'incomplete capture chunk {chunk}: {row}')
            os.replace(os.path.join(d, 'cap.raw'), os.path.join(a.out, f'frame_{frames:05d}.raw'))
            # raw metadata is not needed downstream; keep full-size native Bayer data.
            frames += count
            print(json.dumps({'chunk': chunk, 'frames': frames, 'chunk_ms': round(elapsed*1000)}), flush=True)
    with open(os.path.join(a.out, 'frames.json'), 'w') as f:
        json.dump({'frames': frames, 'fps': a.fps, 'requested_seconds': a.seconds,
                   'chunk_frames': a.chunk_frames, 'sensor': a.sensor}, f, indent=2)
    # Existing converter and GStreamer pipeline are invoked only after all sessions stopped.
    # Convert raw frames by recreating PNG files from the individual captures is avoided: use
    # the regular record client only for encoding is not appropriate. Caller can mux using
    # make_mjpeg-compatible tooling; see documented note.
    print(json.dumps({'ok': True, 'frames': frames, 'requested_fps': a.fps,
                      'effective_fps': frames / (time.monotonic()-started),
                      'seconds': a.seconds, 'out': a.out, 'mjpeg_requested': a.mjpeg}))


if __name__ == '__main__':
    main()
