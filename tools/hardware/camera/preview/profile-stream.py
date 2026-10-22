#!/usr/bin/env python3
"""profile-stream: time today's camera path (s22-camera.py Session) on the phone.

It imports the reviewed client (s22-camera.py, not reimplemented), wraps its
RealSys so every open/ioctl/close/select/dma-heap/mmap call is timed, and runs
ONE continuous stream of --frames frames with the same ioctl sequence and the
same teardown as `s22-camera capture`. It writes no frame to disk.

Per frame it records the VC0 dequeue time, the 6 MB copy time (the client
copies every frame into Python bytes) and, from the leader's meta plane, the
CSI frame-start timestamp (ktime_get_ns, CLOCK_MONOTONIC) that the driver tags
into shot.dm.sensor.timeStamp. The offset of that field is found by scanning
the first done leader meta plane for a u64 within 2 s before "now".

Last stdout line: one JSON object (timings, fps, CPU, teardown errors).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load_client(path):
    spec = importlib.util.spec_from_file_location("s22_camera_client", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cpu_ticks_self():
    with open("/proc/self/stat") as f:
        parts = f.read().rsplit(")", 1)[1].split()
    return int(parts[11]) + int(parts[12])          # utime + stime (clock ticks)


def cpu_ticks_system():
    with open("/proc/stat") as f:
        vals = [int(v) for v in f.readline().split()[1:]]
    idle = vals[3] + vals[4]
    return sum(vals), idle


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--client", default=os.path.join(HERE, "..", "s22-camera.py"))
    p.add_argument("--sensor", choices=("rear", "front"), default="front")
    p.add_argument("--frames", type=int, default=30, help="frames in ONE continuous stream")
    p.add_argument("--buffers", type=int, default=3, help="VC0 dma-bufs")
    p.add_argument("--leader-bufs", type=int, default=2, help="leader (shot) buffers")
    p.add_argument("--no-copy", action="store_true", help="do not copy frames out (requeue only)")
    p.add_argument("--cis-exposure-us", type=int, default=0)
    p.add_argument("--cis-again", type=float, default=0.0)
    p.add_argument("--deadline", type=float, default=120.0)
    args = p.parse_args()
    cam = load_client(args.client)

    timings = {}
    t_origin = time.monotonic_ns()

    class TimedSys(cam.RealSys):
        def _t(self, key, func, *a):
            t0 = time.monotonic_ns()
            try:
                return func(*a)
            finally:
                rec = timings.setdefault(key, [0, 0.0, 0.0])
                dt = (time.monotonic_ns() - t0) / 1e6
                rec[0] += 1
                rec[1] += dt
                rec[2] = max(rec[2], dt)

        def open(self, path):
            return self._t("open " + path, super().open, path)

        def close(self, fd):
            return self._t("close", super().close, fd)

        def ioctl(self, fd, request, buf):
            name = IOCTL_NAMES.get(request, hex(request))
            if request == cam.VIDIOC_S_CTRL:
                cid = struct.unpack_from("<I", buf)[0]
                name += {cam.CID_IS_S_STREAM: " IS_S_STREAM=%d" % struct.unpack_from("<i", buf, 4)[0]
                         }.get(cid, " %#x" % cid)
            return self._t(name, super().ioctl, fd, request, buf)

        def heap_alloc(self, size):
            return self._t("dma_heap alloc", super().heap_alloc, size)

        def mmap(self, fd, size):
            return self._t("mmap", super().mmap, fd, size)

        def dmabuf_sync(self, fd, flags):
            return self._t("DMA_BUF_SYNC", super().dmabuf_sync, fd, flags)

    IOCTL_NAMES = {cam.VIDIOC_S_INPUT: "S_INPUT", cam.VIDIOC_S_PARM: "S_PARM",
                   cam.VIDIOC_S_CTRL: "S_CTRL", cam.VIDIOC_G_CTRL: "G_CTRL",
                   cam.VIDIOC_S_FMT: "S_FMT", cam.VIDIOC_REQBUFS: "REQBUFS",
                   cam.VIDIOC_QBUF: "QBUF", cam.VIDIOC_DQBUF: "DQBUF",
                   cam.VIDIOC_STREAMON: "STREAMON", cam.VIDIOC_STREAMOFF: "STREAMOFF",
                   cam.VIDIOC_S_EXT_CTRLS: "S_EXT_CTRLS"}

    sysif = TimedSys()
    ns = argparse.Namespace(sensor=args.sensor, width=None, height=None, fps=None, bayer=None,
                            leader=None, vc0=None, cis_i2c_bus=None, cis_i2c_extended=False,
                            cis_test_0be4=False, skip_node_check=False, allow_fw_stall=True)
    preset = cam.apply_sensor_preset(ns)
    info = cam.prechecks(sysif, ns)
    w, h, fps = preset["geometry"]
    session = cam.Session(sysif, w, h, fps, cam.PIX_SBGGR16, vc_bufs=args.buffers, leader_bufs=args.leader_bufs,
                          leader=ns.leader, vc0=ns.vc0, position=preset["position"],
                          device=preset["device"], cis_exposure_us=args.cis_exposure_us,
                          cis_again=args.cis_again)
    frames, leader_done = [], []
    ts_off = {"offset": None}
    phase = {}
    result = {"sensor": args.sensor, "geometry": [w, h, fps], "mcu_fw": info.get("mcu_fw"),
              "frames_requested": args.frames, "copy": not args.no_copy,
              "buffers": [args.leader_bufs, args.buffers]}
    out_t, cap_t = cam.V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, cam.V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE

    def scan_ts(meta: bytes, now_ns: int):
        for off in range(0, len(meta) - 8, 8):
            v = struct.unpack_from("<Q", meta, off)[0]
            if now_ns - 2_000_000_000 < v <= now_ns:
                return off
        return None

    with cam._Alarm(args.deadline + 5, True) as backstop:
        try:
            t0 = time.monotonic_ns()
            session.start()
            phase["start_ms"] = (time.monotonic_ns() - t0) / 1e6
            deadline = time.monotonic() + args.deadline
            got = 0
            first = True
            c_self0 = c_sys0 = None
            while got < args.frames:
                if time.monotonic() > deadline:
                    raise cam.DeadlineExceeded("deadline")
                r, wr = sysif.select([session.vfd], [session.lfd], 20.0 if first else 5.0)
                if not r and not wr:
                    raise cam.CameraError("no frame within timeout")
                if session.lfd in wr:
                    done = session._dqbuf(session.lfd, out_t, session.lplanes)
                    if done is not None:
                        now = time.monotonic_ns()
                        meta = session.lbufs[done["index"]][1].read(cam.SIZE_OF_META_PLANE)
                        if ts_off["offset"] is None:
                            ts_off["offset"] = scan_ts(meta, now)
                        ts = (struct.unpack_from("<Q", meta, ts_off["offset"])[0]
                              if ts_off["offset"] is not None else None)
                        leader_done.append((now, ts))
                        session._queue_leader(done["index"])
                if session.vfd in r:
                    done = session._dqbuf(session.vfd, cap_t, session.vplanes)
                    if done is None:
                        continue
                    t_dq = time.monotonic_ns()
                    if first:
                        phase["first_frame_ms"] = (t_dq - t0) / 1e6
                        c_self0, c_sys0 = cpu_ticks_self(), cpu_ticks_system()
                        t_steady0 = t_dq
                        first = False
                    idx = done["index"]
                    copy_ms = None
                    if not args.no_copy and not done["flags"] & cam.V4L2_BUF_FLAG_ERROR:
                        tc = time.monotonic_ns()
                        data = session.vbufs[idx][0].read(session.image_len)
                        copy_ms = (time.monotonic_ns() - tc) / 1e6
                        del data
                    session._queue_vc(idx)
                    frames.append({"t": t_dq, "seq": done["sequence"], "flags": done["flags"],
                                   "copy_ms": copy_ms})
                    got += 1
            t_end = time.monotonic_ns()
            c_self1, c_sys1 = cpu_ticks_self(), cpu_ticks_system()
        finally:
            backstop.rearm(30)
            t1 = time.monotonic_ns()
            session.stop()
            phase["stop_ms"] = (time.monotonic_ns() - t1) / 1e6
    result["teardown_errors"] = session.teardown_errors
    result.update(phase)
    hz = os.sysconf("SC_CLK_TCK")
    if len(frames) > 1:
        span = (t_end - t_steady0) / 1e9
        dts = [(b["t"] - a["t"]) / 1e6 for a, b in zip(frames, frames[1:])]
        dts_sorted = sorted(dts)
        result["steady"] = {
            "frames": len(frames), "seconds": round(span, 3),
            "fps": round((len(frames) - 1) / span, 2) if span else None,
            "dt_ms_p50": round(dts_sorted[len(dts) // 2], 2),
            "dt_ms_max": round(dts_sorted[-1], 2),
            "seq_first_last": [frames[0]["seq"], frames[-1]["seq"]],
            "error_flag_frames": sum(1 for f in frames if f["flags"] & cam.V4L2_BUF_FLAG_ERROR),
            "process_cpu_pct_of_one_core": round(100.0 * (c_self1 - c_self0) / hz / span, 1),
            "system_cpu_busy_pct_all_cores": round(
                100.0 * (1 - (c_sys1[1] - c_sys0[1]) / max(1, c_sys1[0] - c_sys0[0])), 1),
        }
        copies = sorted(f["copy_ms"] for f in frames if f["copy_ms"] is not None)
        if copies:
            result["steady"]["copy_ms_p50"] = round(copies[len(copies) // 2], 2)
    # frame-start -> dequeue latency: match each VC0 dequeue to the nearest leader done
    if ts_off["offset"] is not None and leader_done:
        lat = []
        for f in frames:
            cands = [ts for (tn, ts) in leader_done if ts and ts <= f["t"]]
            if cands:
                lat.append((f["t"] - max(cands)) / 1e6)
        lat.sort()
        if lat:
            result["frame_start_to_dqbuf_ms"] = {"p50": round(lat[len(lat) // 2], 2),
                                                 "max": round(lat[-1], 2)}
        result["dm_timestamp_offset"] = ts_off["offset"]
    result["calls"] = {k: {"n": v[0], "total_ms": round(v[1], 2), "max_ms": round(v[2], 2)}
                       for k, v in sorted(timings.items(), key=lambda kv: -kv[1][1])}
    result["ok"] = not session.teardown_errors and len(frames) == args.frames
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
