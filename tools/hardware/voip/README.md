# S22 interim voice channel (SIP/RTP, audio fully in software)

Carrier call audio is blocked (ABOX DSP DMA), so the agent talks to the owner
over VoIP. No speaker or microphone is involved. Far-end RTP audio is decoded
into WAV files or pipes for ASR, and the agent's TTS WAV is encoded into RTP as
the call's "mic". Owner-approved 2026-10-08.

- `s22-sip.py`: SIP UA written with the Python standard library only (3.12 to 3.14, no audioop, no pip).
- `test_s22_sip.py`: hardware-free tests (`python3 -I -B test_s22_sip.py`, about 5 s, uses 127.0.0.1 UDP).

## Options compared (checked 2026-10-08)

| Option | aarch64 availability | Fits "audio in software"? | Cost / risk |
|---|---|---|---|
| **pure-python stdlib UA (chosen)** | nothing to install; works in the chroot *and* the busybox outer userland | yes: WAV/FIFO in, WAV/pipe out, no sound device | G.711 + UDP only (no SRTP/TLS/ICE/Opus); fine over Tailscale or a plain-UDP provider |
| baresip 4.6.0 | **ALARM `extra`** (`pacman -S baresip`, pulls `libre`, openssl, alsa-lib) | yes: `aufile` (WAV src/sink), `ausine`, `aubridge`, `sndfile` (record), `ctrl_tcp`/`ctrl_dbus` control | best fallback when Opus, SRTP, TLS or ICE are needed (for example a provider that requires TLS). Feeding TTS turn by turn means switching `ausrc` per utterance over ctrl_tcp (netstring JSON) |
| pjsua / pjproject 2.17 | AUR only; build on the device (about 15 to 30 min, 8 cores) | yes: `--null-audio --play-file --rec-file --auto-answer` | building is extra work, and `--play-file` is fixed at startup, so a voice loop needs pjsua2 bindings (more building) |
| linphone-cli / liblinphone | not in ALARM, and AUR is heavy (bctoolbox, belle-sip, mediastreamer2…) | possible (`linphonec` file soundcard) | high build effort; skip |
| sipexer | not packaged (Go) | **no**: signalling and testing only, no media | none |
| Telegram calls (tdlib + tgcalls / pytgcalls / ntgcalls) | tdlib not packaged; ntgcalls/pytgcalls ship as pip wheels only | in principle, but WebRTC stack | **ruled out for now**: needs pip (forbidden on the phone), a WebRTC build and a Telegram *user* account (MTProto userbot, not the Bot API). Revisit only if a prebuilt aarch64 ntgcalls binary can be vendored without pip |

## Reaching the owner

1. **Direct over Tailscale (recommended first; no account, no third party).** The owner installs
   Tailscale and **Linphone** (free, open source, Android/iOS) on their phone. Linphone can dial and
   receive `sip:user@IP` with no account. In Linphone settings, set a fixed SIP port (Network → SIP port 5060),
   keep G.711 (PCMU/PCMA) enabled, and set media encryption to *None* or *optional* (never mandatory).
   Agent → owner: `call sip:owner@<owner-phone-tailscale-ip>:5060`. Owner → agent: dial
   `sip:agent@<s22-tailscale-ip>`. Android may sleep a SIP app that has no push account, so
   calling *from* the owner's phone is the most reliable direction.
2. **Self-hosted PBX on the rig (optional).** Ubuntu's `asterisk` package on the rig, bound to its
   Tailscale IP, with two PJSIP endpoints (`s22`, `owner`). Use it only if direct IP calls prove
   unreliable on the owner's phone. It adds voicemail and Linphone/Zoiper registration (both register with
   username and password). `s22-sip.py listen --domain <rig-ts-ip> --user s22` registers to it.
3. **Public providers (to call the owner's real number; costs money).** Free app-to-app accounts:
   `sip.linphone.org` (create one in the Linphone app; for UDP use `--proxy sip.linphone.org:5060`)
   and `sip2sip.info`. A Greek DID or PSTN termination needs a paid provider that offers plain UDP
   and digest auth, for example Zadarma (Greek numbers, free on-net SIP). Put the password in
   `S22_SIP_PASSWORD` (never on argv). NAT is handled with rport plus re-REGISTER with the public contact,
   CRLF keepalives and symmetric RTP latching. A TLS/SRTP-only provider needs baresip instead.

## Security

`listen --voice-loop` refuses to start without `--allow-from` (caller user, URI, IP or CIDR), because
anyone who can reach the port could talk to an agent that has tools. Bind to the Tailscale address
(`--bind 100.x.y.z`). Caller ID is spoofable on public providers, so also use `--pin 1234`. The agent
then ignores speech until the owner keys the PIN as DTMF (RFC 4733).

## Test on the rig only (no device)

    python3 -I -B tools/hardware/voip/test_s22_sip.py
    # or as two processes, which is what the device test does:
    P=tools/hardware/voip/s22-sip.py
    python3 -I -B $P tone /tmp/a.wav --freq 600 --seconds 3
    python3 -I -B $P tone /tmp/b.wav --freq 1000 --seconds 2
    python3 -I -B $P listen --bind 127.0.0.1 --sip-port 5390 --allow-from 127.0.0.1/32 \
        --play /tmp/a.wav --record /tmp/a-got.wav &
    python3 -I -B $P call sip:agent@127.0.0.1:5390 --bind 127.0.0.1 --sip-port 5392 \
        --play /tmp/b.wav --record /tmp/b-got.wav --hangup-after-play --dtmf 123

## First device test: two UAs over Tailscale, no registrar

The lead deploys `s22-sip.py` to `/mnt/omarchy-trial/opt/s22-voip/` and runs the phone side
inside the chroot (`chroot /mnt/omarchy-trial python3 /opt/s22-voip/s22-sip.py ...`).
`S22=<phone tailscale ip>` and `RIG=<rig tailscale ip>`.

1. Media only (phone answers, plays a tone and records the rig):

       phone: python3 /opt/s22-voip/s22-sip.py tone /tmp/s22-voip-a.wav --freq 600 --seconds 6
       phone: python3 /opt/s22-voip/s22-sip.py listen --bind $S22 --allow-from $RIG/32 \
                --play /tmp/s22-voip-a.wav --record /tmp/s22-voip-in.wav --summary /tmp/s22-voip-1.json
       rig:   python3 -I -B $P tone /tmp/rig-b.wav --freq 1000 --seconds 5
       rig:   python3 -I -B $P call sip:agent@$S22:5060 --bind $RIG --play /tmp/rig-b.wav \
                --record /tmp/from-s22.wav --hangup-after-play --dtmf 42

   Pass criteria: the phone log shows `received DTMF 4` and `received DTMF 2`. The summary has rx ≈ tx and lost ≈ 0.
   `/tmp/from-s22.wav` on the rig contains 600 Hz, and the phone's `in.wav` contains 1 kHz.
2. Voice loop with real ASR/TTS and a fake agent:

       phone: python3 /opt/s22-voip/s22-sip.py listen --bind $S22 --allow-from $RIG/32 \
                --voice-loop --preset s22 --chat-cmd "echo You said: {text}" --work-dir /tmp/s22-voip
       rig:   python3 -I -B $P call sip:agent@$S22:5060 --bind $RIG --play /tmp/question.wav \
                --record /tmp/answer.wav --duration 25

   `question.wav` is any spoken English WAV, for example made with the rig's TTS. Leave about 3 s of
   silence at the start so it doesn't overlap the greeting. Without `--barge-in`, speech that starts
   while the agent is talking is ignored. The transcript and timings are in
   `/tmp/s22-voip/<stamp>/turns.jsonl` on the phone.
3. Real agent: same as step 2 without `--chat-cmd`. It uses OpenUnum `127.0.0.1:18880`, session `voice-call`.
4. Owner's phone: Linphone over Tailscale. Use `--allow-from <owner-phone-ts-ip>/32 --pin <pin>`, and
   the owner dials `sip:agent@$S22`.

Running in the outer recovery userland instead of the chroot: use `--preset s22-outer` with
`--work-dir /mnt/omarchy-trial/tmp/s22-voip`. The ASR/TTS tools then run through `chroot`, with paths mapped automatically.

## Fast voice (rig streaming service, `--fast-voice`)

The command loop above costs 2 to 4 s per reply (energy VAD, phone ASR, cloud LLM, phone TTS, one WAV per turn).
`--fast-voice ws://<rig tailscale ip>:8130` replaces it with a thin bridge to the rig's `unum-voice` service
(repo corpunum/unum-voice): the phone sends every 20 ms of decoded far-end audio (raw PCM16, 8 kHz) over one WebSocket
and plays the PCM16 8 kHz audio the rig sends back. VAD, endpointing, streaming ASR (Greek and English), the LLM, TTS and
barge-in all run on the rig; a `clear` message from the rig flushes the RTP playout queue. The phone needs no models.

    rig:   UNUM_VOICE_HOST=$RIG UNUM_VOICE_TOKEN=... scripts/run-service.sh      # in unum-voice
    phone: UNUM_VOICE_TOKEN=... python3 /opt/s22-voip/s22-sip.py listen --bind $S22 --allow-from <owner-phone-ts-ip>/32 \
             --fast-voice ws://$RIG:8130 --pin <pin> --greeting "Hi, it is your phone agent. Go ahead."

* `--pin` keeps working: no audio leaves the phone until the PIN is entered. The service also needs the shared token.
* If the service cannot be reached at call start and `--preset` (or `--asr-cmd` and `--tts-cmd`) is also given, the call
  falls back to the classic voice loop. A connection lost mid-call is re-opened every second.
* At connect and every 30 s the phone sends a `snapshot` message (battery, thermal, modem/SIM/operator via s22-phoned `:8095/status`,
  network, local time; all best effort). The rig puts it in the brain's prompt as ground truth, so battery/time questions are answered from it
  and anything else live is handed to the agent instead of guessed.
* `ws://` only (no TLS in the stdlib-only client): keep it on Tailscale.
* Measured on the rig with the real `FastVoiceLoop` and a fake RTP session: 0.9 s median from the end of speech to the first reply
  audio sample (Greek and English) while the rig GPU was fully loaded by another model.
