# Omarchy UI trial candidate

Current result (2026-09-20): the parent deployed the main payload plus
supplement to the phone's RAM-staged Arch root, and verified the real bar,
terminal and on-screen keyboard. See `docs/OMARCHY_TRIAL.md`. The package/root
details below describe the host-side build candidate, not a phone package
transaction. The public checkout excludes package archives and the duplicate
shell tree; the phone uses `/opt/omarchy-source/shell` from pinned Omarchy.
The deployed chat is `tools/linux-rootfs/s22-chat-http.py`, not the older
candidate copy here. It requires the loopback resident server.

This is a separate, non-installed copy of the pinned Omarchy shell.  Its
source hashes are recorded below.  The isolated Arch ARM root now contains
signed packages `quickshell 0.3.1-1`, `foot 1.28.0-2`, `squeekboard 1.43.1-5`,
`noto-fonts 1:2026.09.01-1`, `noto-fonts-cjk 20240730-1`, and
`noto-fonts-emoji 1:2.051-1`.  `ttf-jetbrains-mono-nerd 3.5.1-2` is also
installed for the pinned foot configuration (the regular face hashes to
`1c680e8cde9fcf8b88a5605ce8d1fb94dd3fb15841f7ca7bf4c55664855e5611`).
The OSK dependency closure additionally includes signed `libbsd 0.12.2-2`
and `libmd 1.2.0-1`; `squeekboard --help` runs successfully in the isolated
root.
The isolated root also has signed `python 3.14.7-1` and `mpdecimal 4.0.1-3`;
the candidate includes a copy of `s22-chat.py` for the parent's existing
read-only model-bench bind.

The phone-side trial should copy this directory to a temporary user-owned
location, set `OMARCHY_PATH` to the existing full `/opt/omarchy-source` and
`OMARCHY_UI_CANDIDATE` to that temporary path, then run `launch-ui.sh` from the
already-running Hyprland Wayland session.  The wrapper overlays only its
candidate shell config (with idle/lock disabled), starts `squeekboard` as a
child process, and runs Quickshell against the candidate shell while retaining
the full source path for defaults.  It does not run an installer or enable
services.  Stop it with Ctrl-C; the wrapper waits for Quickshell and then
attempts to stop the OSK child.

`package-delta.tar` is retained as the signed-package receipt (47 package
archives and 47 detached signatures; all 47 verified with the isolated root's
`pacman-key --verify`).  Do not feed it to the phone's stuck `pacman -U`.

The deployable file-only payload is `s22-ui-file-payload.tar` (136 MiB,
SHA-256 `695807f42efbafcd6db8d35ffc0c81696ffd528a78dfa2c550b6ce5192ca46c0`).
It contains only selected `usr/` runtime files, excludes package metadata,
hooks, systemd units, dbus system-service files, applications, Noto/CJK fonts,
and all Nerd-font files except `JetBrainsMonoNerdFont-Regular.ttf`. It was
subsequently deployed to RAM on the phone. To restore, verify the hash, extract it
into the staged RAM root, then run these explicit post-extraction commands as
appropriate:

```sh
fc-cache -f /usr/share/fonts
glib-compile-schemas /usr/share/glib-2.0/schemas
```

No package hooks or services are run by the archive extraction.

The first file payload omitted a package-provided GNOME library because the
phone already had the package name in its manifest but not its files.  The
small supplemental [s22-osk-supplement.tar](s22-osk-supplement.tar) supplies
only `libgnome-desktop-3.so.20` (including its symlinks) and the generated
`org.gnome.desktop.enums.xml`.  SHA-256:
`4d08785c056594ee8bdec6d5d9978ae20d06d51a029a7b7d97bff45a55185173`;
size 348160 bytes (340 KiB). Extract it after the main payload, then run the schema command
above.  Its library bytes came from the signed isolated-root package
`gnome-desktop-1:44.5-1-aarch64`; no package transaction or hook is required.

`shell-trial.json` intentionally keeps only menu, workspaces, clock, and
audio widgets to avoid startup network/service probes.  Its SHA-256 is
`7a43f9a72d3e4b968628a1e6d735a16d69abf57b2fc01d341aa7d8b094c498b9`.

The Omarchy shell expects `OMARCHY_PATH/shell`, and its source uses the normal
Quickshell module set.  The OSK requires the compositor's virtual-keyboard and
layer-shell globals; if those are absent it logs the reason and is not a
successful touch-input test.

Source hashes:

* `shell/shell.qml`: `4a4b7694e5b9e0bd952ce0efa2d6dc2cea44cdfa98cc2f442e40fe41cbc1beab`
* `shell/plugins/bar/Bar.qml`: `9874c0f36271840b43002890ae333c83097df7f4cff0bade81536a41ed83590b`
* `shell.json`: `b16dc9646bf68e81643522cef504795d97cb5016d80d8b65784bde44f2c0883f`

Package installation was performed in the isolated host-side ARM root.
The parent later staged verified file payloads on the phone without package
hooks or service activation; this did not register every file with pacman.
