# Native S22 hardware experiments

These are device-specific development tools, not a one-command hardware
installer. Read `docs/WIFI_NATIVE.md`, `docs/BT_AUDIO_NATIVE.md`, and
`docs/INPUT_POWER_NATIVE.md` first. The first Wi-Fi activation panicked; do
not autoload WLAN or repeat its old module/handshake ordering. The corrected
follow-up now has verified Wi-Fi traffic; see `docs/wifi-next-experiment.md`
and `evidence/wifi-connected-20260920/`. Optional Wi-Fi recovery-boot autostart
now passes two consecutive reboot tests; see `docs/WIFI_AUTOSTART.md`.

The Wi-Fi tools split responsibilities: exact optional-firmware completion,
fresh-state calibration/enumeration, private host-profile transfer, bounded
WLAN-only DHCP/DNS setup, and sanitized live acceptance. None is a general
installer. The responder must remain running while the loaded WLAN driver
can request its optional file. Do not rerun the one-shot activation now.
The profile installer reads credentials only with explicit `--install`,
requires owner approval, and refuses replacing an existing private profile.
Host tests use mocked interfaces/tempfiles, not actual radios or credentials.

Accepted live changes: battery telemetry UI, icon fonts, and compositor-only
DPMS, plus a visible Keyboard bar button. The native power-key binding is installed; physical key/finger input
is not yet accepted. `test-power-dpms.py` defaults to inspection and only
changes display state with `--exercise`, restoring enable in `finally`.

`wifi-stage.sh` is host-only staging. It validates module release, but that
does not prove correct initialization order or driver behavior. Firmware
and APK binaries stay local and must not be committed. `wifi-verify.sh`
does not prove connectivity merely from an enumerated interface.

The F2FS recovery scripts record the reconstruction used in this session.
They require the retained `/tmp/blockmap.txt`, the specific locally built
F2FS metadata utility path inside the scripts, Python lz4, and the matching
pristine vendor image. They only handle the captured 4KiB, four-block LZ4
cluster layout; they are not general F2FS readers and do not fully validate
inode compression metadata. Some captured map lines are corrupt and some
audio assets remain unrecovered. Do not guess those bytes or treat a
nonzero output size as proof of correct firmware. Run unprivileged against
a read-only image copy and preserve source/output hashes.
The inline decoder requires a complete, indexed word range; omitted zero
words and truncated dumps are both refused, not compacted or zero-filled.

The power UI patches reproduce changes to pinned Omarchy preimages. Their
verifier requires the locally retained candidate/original trees. Phone
backups and deployment evidence are described in the companion power notes.
The two `osk-bar-toggle*.patch` files apply after the four battery/power
patches, to their battery-only candidate JSON preimages.
The fallback input daemon was not deployed; do not run it alongside the
native Hyprland power binding.

`run-host-regressions.py` runs a fixed allowlist of synthetic host tests. It
does not discover tests or accept test paths. The suite covers the NPU
lifecycle model and preflight gates, audio DMA progress classification, input
readiness against temporary fixtures, and the Bluetooth H4/IBS bridge against
local PTYs. The bridge test needs the Ubuntu runner's existing `cc` compiler;
it does not open a physical UART. Two small assertion-based scripts run only
normally because Python `-O` would remove their checks. Other allowlisted tests
also run with `-O`. All child processes receive a clean temporary home and an
environment without inherited device overrides, credentials, or API tokens.
This CI job makes no phone connection, hardware probe, firmware/model access,
or network request from its tests; it uses no CI secrets and grants only
read-only repository permission. Tests that require a private kernel tree,
firmware/model assets, deployment transport, or live device interface are
excluded. Review any proposed addition before adding its literal path to the
runner allowlist.
