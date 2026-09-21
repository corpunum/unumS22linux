# Private Pi browser terminal on the S22

This is the actual Pi agent used by the installed Omarchy launcher, exposed
as a browser terminal. It is not a full remote Omarchy desktop and is not
an upstream Omarchy web application. The existing local Qwen3.5-4B Q4_K_M
model and Pi provider configuration are unchanged.

## Access

Connect the browser's device to the same Tailscale network, then use the
phone's private MagicDNS name on HTTP port **8092**. The actual address is
shared privately, not published in this repository. Tailscale Serve's HTTP
Host routing requires that name; the numeric Tailscale IP alone returned 404.
No public Funnel, router port forwarding, or public model API was enabled.
HTTP here travels inside the encrypted tailnet connection, but is not HTTPS
and does not supply browser secure-context features.

Click the terminal and type a normal request. `/model` opens Pi's configured
model selector. The default remains the phone's resident 4B CPU model; this
web deployment does not switch it to the experimental GPU runtime.

## Measured acceptance

- Real Chromium session reached Pi through Tailscale Serve, including the
  WebSocket and pseudo-terminal, not merely an HTTP health page.
- Typed `Reply with exactly S22_WEB_OK. Do not use tools.`. The saved Pi JSONL
  contains an **assistant** message `S22_WEB_OK`, local 4B model identity and
  `stopReason: stop`. This was not inferred from the echoed user prompt.
- The measured uncached remainder of that request took 66.15 seconds:
  954 prompt tokens in 65.21 s, then six generated tokens in 0.94 s. These
  timings are one small acceptance request, not a general model benchmark.
- Reopening the terminal restored the completed conversation.
- A second prompt was sent and the browser closed after 0.5 s. Pi retained
  the same PID and completed the answer after disconnect (4.83 s measured
  model time); a fresh browser showed the saved second assistant reply.
- Web-only stop/start cleanly stopped its own ttyd and musl tmux, then
  restarted the listener. Desktop/model readiness was byte-for-byte unchanged;
  the next browser session reopened the saved history.
- Native listener is only `127.0.0.1:8093`; the public-to-tailnet route is
  Tailscale Serve port 8092. No-identity-header loopback requests return 407.
- A genuine foreign-origin WebSocket upgrade was refused by ttyd's origin
  check; the reverse proxy returned 502 without opening a terminal.
- ttyd runs as UID/GID 1000, with effective/permitted/bounding capabilities
  all zero and `NoNewPrivs: 1`. One simultaneous browser client is allowed.

Private raw browser frames/screenshots, session evidence, binary and deployment
receipts are under ignored `rootfs/pi-web-20260921/`. Do not publish these
after personal agent use; they may contain prompts or tailnet identities.

## Implementation and boundaries

The official ttyd 1.7.7 static aarch64 release was checked against its publisher's
SHA256SUMS: `b38acadd89d1d396a0f5649aa52c539edbad07f4bc7348b27b4f4b7219dd4165`.
The launcher calls the existing `/usr/local/bin/pi` with a separate web-session
directory, so it does not concurrently write the desktop's session JSONL.

The final persistent-session layer uses the phone's existing Alpine tmux 3.7c
and exact musl/ncurses/libevent libraries in an isolated 2.25 MB userdata
directory. It does not replace Arch libraries, install privileged helpers, or
modify the package database. Browser disconnect detaches from a dedicated
tmux socket; it does not terminate Pi. The web helper checks exact hashes and
can stop only its own ttyd and named tmux server during desktop cleanup.

A prior signed ArchARM tmux trial was rejected: its forked child hung before
executing Pi, matching the earlier Arch process-spawn compatibility symptom.
The parent server was stopped and the direct launcher restored before trying
the separate native-musl build. One Arch trial child remained stuck despite
SIGKILL; its priority was lowered. No reboot was attempted to clear it. The
working musl tmux and Pi are separate processes; this unresolved kernel-level
spawn symptom must not be reported as fixed by the browser workaround.

The Arch `/dev/ptmx -> pts/ptmx` alias originally reached a mode-000 devpts
node. Binding the native multiplexer into the Arch dev directory then failed
with ENOENT: this kernel could not resolve its sibling pts mount through that
file bind. The final repair creates the standard virtual char 5:2 multiplexer
in the Arch dev tmpfs, mode 0666, beside its existing pts mount. It changes no
physical device permission or global devpts mode, adds no mount, and leaves
the existing desktop PTYs running. The failed temporary bind was removed.

Tailscale authenticates the connecting peer and supplies the identity header;
ttyd trusts that proxy header. Tailnet access controls must limit access to
trusted operators. Anyone allowed through this endpoint can ask Pi to run
commands as the alarm user. This is the existing shared Arch environment,
not a container-security boundary: proc/sys/pts and selected device directories
are shared with the desktop. No root browser shell is supplied.

The optional supervisor hook starts the web helper only after desktop/model
readiness and attempts its verified cleanup before chroot teardown. Web
failure does not restart the desktop or stop Tailscale/rescue. The helper,
launcher and session files live on userdata; the supervisor lives in the
existing persistent CACHE overlay. Cold reboot acceptance is intentionally
not claimed: no phone reboot or partition flash is part of this change.

References: [ttyd upstream](https://github.com/tsl0922/ttyd),
[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve).
