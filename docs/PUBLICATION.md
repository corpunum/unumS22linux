# Public repository scope

The 2026-09-20 update publishes project code, small driver patches, configs,
experiment history, benchmark provenance, diagnostic logs and screenshots.
It updates the previously stale README/status to the native Linux and real
Omarchy desktop milestone, and records failures as well as successes.

## Deliberately local-only

- Samsung/Lineage images, firmware, NPU vendor libraries and raw partition dumps.
- Alpine/Arch roots, downloaded package archives, model weights and compiled
  runtimes/drivers/probes. Source/build notes and hashes are published instead.
- The private Alpine backup containing device SSH host private keys.
- Raw recovery-cache forensic files, unbounded/NUL-filled boot dumps and an
  unrelated full host-kernel log. Relevant bounded experiment evidence remains.
- Cloned upstream source trees, duplicate Omarchy shell sources and full
  reference translation units. Their pinned revisions and project patches
  identify the work without vendoring a second source repository.
- Python caches and other generated build files.

These files have not been deleted from the original working directory.
The public repository is not a complete binary distribution or a clean-clone,
one-command installer. Staging helpers intentionally require hash-verified
artifacts that the original workstation already holds.

Public SSH known-host entries and the Arch package-verification public key are
not private credentials. They identify this experiment's existing phone and
package signer; they are not a universal trust setup for other users' devices.
Do not publish SSH private keys or reuse the device's identity on another phone.

## Pinned third-party sources

- [Omarchy](https://github.com/basecamp/omarchy), v4.0.4,
  `c668141e9c42b13c80c9ca4ea108e11708c5e8a5`.
- [Aquamarine](https://github.com/hyprwm/aquamarine), v0.15.1,
  `f31c47a1b9d300847d8fc3108ad959448103dfc1`.
  Apply `tools/omarchy-trial/aquamarine-s22-display-only.patch`; the successful
  matching-GCC16 build is described alongside it. This is display-only, not GPU acceleration.
- [Experimental Xclipse RADV patches](https://github.com/mxxme-dev/radv-xclipse-patches),
  `d1b295e8c61c013c454e8015983a1d6b6df82bf2`.
  `radv-xclipse-lifecycle-real-fence.patch` contains the tested lifecycle and
  honest-fence changes. GPU compute still fails; it is not an accepted driver.
- [llama.cpp](https://github.com/ggml-org/llama.cpp),
  `e613ef2c81bae98d59850d061ac29e6e3e88cb00`.
  Build/model hashes and provenance are under `evidence/model-bench-20260920/`.

Third-party source and artifacts retain their respective licenses. No vendor
firmware redistribution or project-wide relicensing is implied.
