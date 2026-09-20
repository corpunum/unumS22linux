# S22 linear scanout stride trial

Update 18:18UTC: the owner confirmed the physical panel is clean. The updated
supervisor and `/etc/s22-linear-stride-enabled` were installed persistently,
with hash readback and the old supervisor retained as `.pre-stride`. The
current session was not interrupted; a new recovery reboot with this saved
configuration is not yet tested. `S22_LINEAR_STRIDE_TRIAL=0` overrides the
marker for rollback. The sections below record the preceding trial.

On 2026-09-20, native RECOVERY BORE757 returned successfully, but the owner
reported lines/artifacts over the physical panel. The active Hyprland buffer
captured cleanly: XR24, linear modifier0, logical1080x2340, pitch4352 bytes
(1088 pixels). A clean mapped screenshot does not establish clean panel output.

## Source contract

Pinned Samsung/Lineage kernel commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`:

- [DPP configuration, lines350–459](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/gpu/drm/samsung/dpu/exynos_p1/exynos_drm_dpp.c#L350):
  `src.f_w = fb->width`; visible `src.w` independently uses the plane source
  rectangle. The pitch fields are propagated for compressed SBWCL, not
  the current uncompressed XR24 layout.
- [RDMA programming, lines248–255](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/gpu/drm/samsung/dpu/exynos_p1/cal_pm/dpp_reg.c#L248):
  full source width controls RDMA_SRC_WIDTH; visible size is separate.

The kernel is therefore told1080 pixels per row despite4352-byte storage.
This is a source-backed explanation for the physical-only corruption, pending
the owner's visual confirmation of the controlled workaround.

## Narrow diagnostic workaround

`s22-linear-stride.c` interposes only libdrm framebuffer creation, never kernel
ioctls directly. With `S22_LINEAR_STRIDE=1`, it identifies the fd's driver as
`exynos-drmdpu` and requires exactly single-plane linear XR24,1080x2340,
pitch4352,offset0. It passes framebuffer width1088 instead of1080, retaining
pitch, addresses, height and all other arguments. The compositor still sets
a1080x2340 source rectangle and panel mode. All other layouts pass unchanged.
This is deliberately not a general-purpose libdrm policy or kernel fix.

Build/test from the repo root:

```sh
mkdir -p builds/s22-linear-stride
cc -Wall -Wextra -Werror -I/usr/include/libdrm tools/omarchy-trial/test_s22_linear_stride.c -o builds/s22-linear-stride/test
builds/s22-linear-stride/test
aarch64-linux-gnu-gcc -Wall -Wextra -Werror -O2 -shared -fPIC -I/usr/include/libdrm -idirafter /usr/include tools/omarchy-trial/s22-linear-stride.c -ldl -o builds/s22-linear-stride/libs22-linear-stride.so
```

Artifact SHA256:
`fa627c94f4cb536be24567e2a955c578b741e08dcb2860a0fd1f7110a55c7fcc`.
The build uses architecture-neutral libdrm headers and the AArch64 GCC13
sysroot C runtime; it has no C++ ABI dependency.25 host selection assertions
passed. Loading with the phone's Arch dynamic linker also succeeded.

## Live trial and rollback

Only an ordinary library file was installed under
`/srv/s22/arch/opt/s22-aquamarine/libs22-linear-stride.so` and hash-verified.
The updated supervisor was staged at `/tmp/start-persistent-desktop-stride-trial.py`;
the installed `/usr/local/bin/start-persistent-desktop` was not overwritten.
Verified old supervisorPID1505 and exact command line before SIGTERM; its
owned desktop/model/seatd and mounts exited cleanly. Started the staged
supervisor with `S22_LINEAR_STRIDE_TRIAL=1`; preload applies only to the Arch
desktop environment, not native guardian/SSH or the model process.

At13:31UTC, same BORE757, uptime539.79s: model health `ok`, desktop ready,
4,915,356KiB MemAvailable, battery33.2C. Successful shim logs and DRM state
prove FB1088x2340/pitch4352, with source/CRTC1080x2340, XR24/modifier0.
The captured framebuffer remains clean. Evidence is under
`evidence/persistence-20260920/rescue-bore757-stride-*`.

Physical panel correction is **not yet confirmed**. The opt-in trial is left
running for the owner to inspect; it is not enabled by automatic recovery
startup. No reboot, flashing or partition writes occurred during this trial.
To roll back, resolve/verify the current supervisor from
`/run/s22-persistent-ready.json`, SIGTERM only that process, wait for owned
process/mount cleanup, then launch the unchanged installed supervisor without
the trial environment. Do not use stale PIDs or broad process-name kills.
