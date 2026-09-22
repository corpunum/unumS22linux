# Late audio startup and Bluetooth configuration checkpoint

This records measured progress, not completion of all phone drivers.

## Persistent audio control access

Recovery boot BORE763 exposed two real startup defects: the ABOX card was not
registered at the first startup check, and udev did not create its control
node on native `/dev` tmpfs. The earlier live check had reused a manually
created node and therefore did not establish persistence.

The corrected supervisor now observes late card registration for up to
90 seconds inside its ordinary supervision loop. It validates the exact
RainbowPrince sysfs ancestry and `116:114` identity, safely creates only
`controlC0`, then binds only that node into Arch. A missing card or failed
check remains optional; desktop, model and Wi-Fi startup are not held for
the polling window. No PCM node is exposed by this hook.

One software recovery reboot, BORE764, verified 95.10 seconds of uninterrupted
uptime. At 96.12 seconds, Arch automatically had the single root:audio
`0660` control node and enumerated all 1,736 controls through read-only
libasound. No manual node creation or bind occurred in this boot.
Supervisor SHA256:
`76a759049766100e57127363c3cd5141a486c061df4783560e8a3c2a918dae96`.

This fixes persistent **control access**, not the separately measured RDMA2
streaming stall or physical speaker/microphone operation. The same RECOVERY
image was used; no partition was written for these two later reboot tests.

## Bluetooth: configuration transfer acknowledged

An initial configuration trial stopped at the baud transition: it received
an unexpected `ff` byte and timed out **before sending patch or NVM bytes**.
The earlier successful baud implementation spent 5–6 ms between command write
and host rate change; the failed run took 8.64 ms. Userspace read timestamps
do not establish wire-arrival timing, so this correlation is not proof of
the failure's cause.

A separate baud-only variant precomputed termios and moved diagnostic stdout
out of that interval, preserving the protocol, identity checks and cleanup.
It passed in 3.015 seconds. The following bounded test passed in 5.131 seconds:
full 195,848-byte RAM patch, board-ID query, then 7,023 configuration bytes in
29 individually acknowledged packets. Each reply was the expected command
completion `04 0e 05 01 00 fc 00 1e`.

The image uses the exact recovered R0 configuration and the source-matched
HAL tag mutations. It retains the observed all-zero diagnostic address and
is **not a usable radio identity**. No HCI reset, registration, scanning,
pairing, or RF acceptance was performed. Bluetooth was powered off afterward;
its private power vote was released and WLAN/model/USB remained healthy.
No EFS, identity partition, or persistent radio storage was touched.

## Qwen, network and remaining gaps

Pi/Qwen completed a source review through an isolated USB SSH loopback tunnel
in 51.12 seconds, with one verified write-tool result. Normal Pi settings and
the local CPU 4B default were unchanged. Earlier connection and output-path
failures were retained and are not counted as successful reviews. A useful
review suggestion removed repeated XML path reads from the host builder;
the generated diagnostic configuration hash stayed unchanged.

The latest boot passed all 13 WLAN checks, including actual DNS and verified
HTTPS over `wlan0`. Earlier native DNS failures were intermittent and are not
declared permanently solved. The rig's Tailscale coordination recovered
without a daemon restart; the phone Pi web page returned HTTP 200 again.

NPU inference, cellular/SIM/calls, working speaker/microphone, Bluetooth HCI
and pairing, camera/GNSS and suspend remain unaccepted. The NPU timeout
candidate remains undeployed: queued POWER requests retain a raw session
pointer, and a simple caller timeout would not establish safe callback and
hardware cleanup ownership. Additional recovered audio firmware is being
prepared host-only; no playback fix is inferred from its presence.

## Evidence and validation

[Sanitized machine receipt](../evidence/main-driver-loop-20260922/late-control.json)
contains the reboot, control-only node, WLAN, Qwen and Bluetooth measurements.
Raw kernel/UART traces, boot UUIDs, network identities and vendor payloads
remain private.

Host checks passed: 11 supervisor, 8 model-profile, 13 optional-audio and
7 USB-review tests; 5 NVM parser and 3 configuration-builder checks; C baud,
806-packet patch and 29-packet configuration framing/rejection self-tests.
These checks do not substitute for the unresolved end-to-end hardware tests.
