# The S22 as an OpenUnum device: design (draft 2026-11-12, for corpunum-82)

Goal (owner idea, 11-12): the rig's main agent and Unum use the phone as a device with tools and skills,
installation-specific: take photos, listen to the room, speak through the speaker, take tasks, back up the
rig's internet, and call or text the owner. The phone side is this repo. The rig-side OpenUnum device tools
are s-openunum's part.

## 1. Build on s22d, don't add a daemon

`s22d` (Rust, native root) already is the phone's capability API: typed get/set/action capabilities
(`/v1/device`, `DEVICE_API.md`), risk tiers, owner confirmation on the screen, and an audit log
(`/srv/s22/state/audit/s22d.jsonl`, numbers masked). Camera, SMS, calls, Wi-Fi, mobile data, volume, torch
and haptics are there today, so the work is:

1. a second, remote listener with real authentication (today it is loopback only);
2. a per-capability on/off switch file;
3. the missing capabilities;
4. the uplink failover.

## 2. Capabilities

| Capability | Status | Tier | Notes |
|---|---|---|---|
| `camera.capture` (rear/front) | exists | reversible | ISP JPEG; the file is deleted after the task unless `keep: true` |
| `audio.listen {seconds ≤ 120, summary?}` | NEW | sensitive | mic → parakeet ASR on the phone → transcript (+ optional summary by the rig). Raw audio is never stored, the transcript only if `keep`. A screen banner and LED show while it records. Off by default. |
| `speak {text, lang?, volume?}` | NEW in s22d (touchd `tts` exists) | reversible | temporary speech level, then back to mute (the owner's mute rule) |
| `task.submit {text}` | NEW | reversible | posts an agent-marked turn to the phone's own OpenUnum (local Gemma), returns the turn id; `task.get` polls |
| `sms.send` / `call.dial` (owner) | exist | confirm off-allowlist | for "notify the owner"; allowlisted numbers need no tap |
| `uplink.status` / `uplink.share {on}` | NEW | reversible | see §5 |
| `device.status`, battery, thermal, location* | exist (*GNSS: later) | read | |

Every capability has a switch in `/srv/s22/state/device-switches.json`
(`{"audio.listen": false, "camera.capture": true, ...}`), shown as toggles in the touch shell's settings.
A disabled capability answers 403 `capability_off`, final. The switch file is native-root-only (0600), so
nothing in the chroot and nothing remote can flip it. The owner flips it on the screen; the rig asks via
corpunum-82.

## 3. API and transport

- s22d's same routes on a second listener: `<phone tailnet IP>:8767`, bound to tailscale0 only.
- **Auth, two factors, both required:**
  1. Peer identity from the tailnet. s22d asks tailscaled's LocalAPI `whois` for the TCP peer and accepts
     only the rig's node key (pinned in `/srv/s22/state/device-peers.json`).
  2. A bearer token per peer (`Authorization: Bearer`, 32 random bytes; the phone stores only a hash),
     minted on the phone and delivered to the rig once by corpunum-82, like the touchd pairing.
  Rotation: `s22d peer rotate <name>`. Revocation: delete the entry.
- Remote callers are never "the owner". They get the agent tier, the same as the phone's own agent:
  owner-only things (model switch, pairing, capability switches) stay native/touch only.
- The loopback listener and `/run/s22d.sock` are unchanged. The chroot cannot reach the remote listener's
  owner paths, because there are none.
- Rate limits per capability (camera cooldown exists; `audio.listen` at most 1 at a time, 10 an hour).

## 4. Privacy

- Capture only on request, never ambient or background. One recording or photo per call; no streaming
  mode in v1.
- Nothing kept beyond the task: photos and transcripts live in `/run/s22d/tmp` (tmpfs). The caller fetches
  the result in the response (base64 or a one-time download URL valid 5 min), then it is deleted.
  `keep: true` copies to the gallery or `/srv/s22/state/device-keep/` and is logged as such.
- Every capture is audited in `/srv/s22/state/audit/device-captures.jsonl` (append-only, 0600):
  ts, peer, capability, duration, kept?, sha256 of the artifact, never the content. The touch shell has
  "Privacy log" to read it, and `audio.listen` shows a live banner on the screen.
- The microphone capability ships OFF; the owner enables it on the phone.

## 5. Internet backup (rig uplink failover)

The phone is on the rig's USB most of the time, so the robust path is **USB networking**, not Wi-Fi hotspot
(AP mode on this Wi-Fi chip is unproven and shares the BT/Wi-Fi rail).

- Phone: a USB NCM gadget with `usb0 192.168.77.1/30`. When `uplink.share on`, the phone enables NAT
  (iptables MASQUERADE) from usb0 to rmnet (LTE). Checked 11-12: the kernel has
  `CONFIG_USB_CONFIGFS_NCM`/`RNDIS`, `CONFIG_NF_NAT` and `IP_NF_TARGET_MASQUERADE`, but configfs is not
  mounted and no iptables/nft userspace is installed. Phase 4 therefore starts with a proof: bring the
  gadget up beside Samsung's USB stack without breaking charging or the current USB role, then install
  the NAT userspace. Mobile data comes up through netpolicy's existing `failover` mode.
- Rig: s-infra or the owner configure a second default route via usb0 with a higher metric (for example
  NetworkManager `ipv4.route-metric 900`), so it is used only when the main uplink is gone. The rig's
  watcher calls `uplink.share on` when its main uplink fails (ping check), and `off` when it returns.
  Rig-side changes need sudo, so they go through corpunum-82.
- Guards: share only while the battery is above 30 % or the phone is on USB power; a data cap per day
  (default 2 GB, from rmnet counters), then `uplink.share` turns itself off and texts the owner.
- Tailscale keeps working over the backup link, so the rig and phone keep talking.

## 6. Rig side (s-openunum)

OpenUnum "device" tools on the rig generated from the phone's `/v1/capabilities` (as the phone's
`s22-device` plugin already does), with installation config holding the phone URL and token reference.
Owner-facing skills on top: "photo of …", "what's happening in the room", "say … on the phone",
"text me when …".

## 7. Phases (each its own hub item)

1. Remote listener + peer auth + capability switch file + audit file (no new capability yet).
2. `speak` and `task.submit` through s22d.
3. `audio.listen` (mic path, banner, off by default).
4. Uplink failover: phone gadget/NAT + data cap, then the rig route via corpunum-82.
5. Rig device tools + skills (s-openunum).

## Open questions for the owner

- Mic: is "off until enabled on the phone" right, or should it be on with the banner?
- Uplink data cap per day (2 GB default)?
- Keep the photo/transcript in the response only, or also save it to the rig when asked?
