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

The Arch package-verification public key is not a private credential. Device
SSH known-host entries are also public keys, but they identify a particular
phone and are now kept local instead of published. Provision a locally
verified known-host file before using SSH helpers; never disable host-key
checking to compensate. Existing local copies were preserved. Do not publish
SSH private keys or reuse the device's identity on another phone.

## Privacy review, 2026-09-21

Reviewed the public master snapshot, all32 reachable master commits, tracked
file names, network-address occurrences, and OCR text from41 screenshots.
Gitleaks8.30.1 reported nine candidate matches: eight artifact checksums and
one compositor workspace token, not an API credential. No live credential,
home/WAN address, or Tailscale address was identified by these checks.
This is a scoped audit, not a guarantee that every possible disclosure is absent.

Found and redacted device/chip serial identifiers in six evidence files.
Removed hardcoded device targeting from two helpers; they now require the
local `S22_ADB_SERIAL` environment variable. Removed five device-specific
known-host files from the public tip while preserving workstation copies.
Generated USB-link addresses, localhost, documentation/test addresses and
public service addresses remain deliberately: they are not personal network
coordinates. Project paths and upstream public identities are not credentials.

After explicit owner approval, the public master history was rewritten to
remove the same identifiers and device known-host files from past commits,
and replace personal author/committer metadata with the owner's GitHub
no-reply identity. A complete pre-cleanup Git bundle is retained privately.
The active development checkout and its unfinished edits were not rewritten.
The remote replacement uses an exact-old-head lease, not an unconditional
force-push. No other public branches, tags, forks or pull requests were listed
at the time of the audit.

**Existing clones must not merge or re-push the old history.** Preserve local
edits, then transplant reviewed, sanitized changes onto the rewritten public
history. Merely merging the old branch would restore the removed data. Device
identity files remain local and should not be staged again.

This cannot erase someone else's clone or guarantee removal from GitHub's
cached old-commit views. GitHub documents that residual cached references can
require [Support cleanup](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
Private runtime state, credentials, personal files and new raw agent transcripts
were not added in this publication update. The finding is limited to the
scanned public Git contents, not a universal security certification.

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
