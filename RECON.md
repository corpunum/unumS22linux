# RECON — S22 mobile data step 4: initial-attach APN + PDP + data call

Status: reconnaissance complete by coordinator (retry worker killed after 5th stall, session 97aee47c).
Gate: deploy + any SET frame happen ONLY after Claude reviews this file.

## Verified facts (commands run this run; evidence in-line)

1. libsec-ril.so initial-attach APN path. strings -n 8 /srv/s22/cellular-loader-20260921/vendor/lib64/libsec-ril.so | grep -iE "initial.?attach|SetInitialAttach" ->
   _ZN15DataCallManager21DoSetInitialAttachApnEP7Request,
   _ZN15DataCallManager23SkipSetInitialAttachApnEP7Request,
   _ZN22LteEpdgDataCallManager21DoSetInitialAttachApnEP7Request,
   plus InitialAttachApnInfo C1/C2 ctors + vtable.
   -> The stock RIL blob performs initial-attach APN inside DataCallManager (it sends the IPC SET to the CP itself). phoned bypasses the blob and speaks IPC directly, so phoned must reproduce exactly the frame DataCallManager would send.
2. Blob also carries DATA_CALL_STATUS, MODEM_SILENT_RESET, MODEM_CRASH, MODEM_STATE_CRASH_* strings -> DATA_CALL status reporting exists at CP level; a rejected SET answers with an error code (GEN_PHONE_RES), not silence.
3. phoned frame format (tools/hardware/phoned/s22-phoned.py): HDR = struct.Struct("<HBBBBB") -> len(incl. header), mseq, aseq, group, index, type on /dev/umts_ipc0 (kernel adds/strips SIPC5 link header). Types: EXEC=1 GET=2 SET=3 CFRM=4 EVENT=5; replies INDI=1 RESP=2 NOTI=3.
4. Accept/reject contract: GEN_PHONE_RES = (0x80, 1), GEN_OK = 0x8000 — a RESP/NOTI on (0x80,1) with code 0x8000 = accepted; non-zero = rejection. Live ipc.jsonl already shows GEN_PHONE_RES replies wrapping DISP_RSSI_INFO replies.
5. Current SET path is fire-and-forget: request() defaults want_resp = (typ == GET) -> SET frames never wait for the reply. The three new commands must capture the reply (want_resp=True or parse the logged RESP) so accept-vs-reject is logged per frame.
6. No DATA_CALL ids exist in phoned yet: grep for DATA_CALL, (0x0B, (11, in s22-phoned.py -> zero matches. Implementation must add group/index ids + payload packers.
7. Side-effect boundary (live, this run): SET TX to modem = ZERO — grep -c set-attach-apn/define-pdp/data-call in /srv/s22/state/phoned/ipc.jsonl{,.1} = 0,0. Nothing sent, nothing to undo. Modem at act:egprs, reg_status:home_3gpp, service_domain alternating cs/ps, rssi 26-27, PLMN 20205 (Vodafone GR), rmnet0-7 DOWN, ecm0 UP. phoned PID 2192 serving. /proc/uptime 27965s -> no re-boot spent (0/2).

## Hypotheses to probe at deploy (NOT yet verified)

- IPC 4.1 group 0x0B (11) is the DATA_CALL group (libsamsung-ipc IPC 4.0 convention); exact index for SET_INITIAL_ATTACH_APN / DEFINE_PDP_CONTEXT / DATA_CALL unknown on this platform. Probe order: send one candidate SET frame, read the (0x80,1) reply code before sending the next.
- Candidate payload layouts (IPC 4.x convention, confirm against reply codes):
  - SET_INITIAL_ATTACH_APN: apn_type(1B) + pdp_type(1B) + apn(NUL-terminated) — pdp_type 0 = IP.
  - DEFINE_PDP_CONTEXT: cid + apn + auth/pdp-type fields.
  - DATA_CALL: apn_type + pdp_type + cid + auth_type + NUL-terminated user/password.

## Deploy + SET sequence (after Claude review)

1. Backup /srv/s22/state/phoned + binary; one-line rollback = restore backup + exact-PID kill of node on :18880 (keepalive restarts in ~20 s).
2. ONE command at a time, >=30 s between: set-attach-apn -> watch registration (moves to lte and stays 5 min?) -> define-pdp -> data-call on.
3. Rails: the instant modem leaves ONLINE or SMS stops -> data-call off. Re-boot = last resort only (max 2, 0 spent).

## Restart-resume disassembly follow-up (2026-10-14)

Stock exported-builder evidence (same firmware blob, addresses from `aarch64-linux-gnu-objdump -T`; disassembled around builder addresses):

- `IpcTxSetLteAttachProfile` `0x3bee18`, size `0x2c4`; legacy `0x3bebac`, size `0x26c`. JSON support check `(0d,14)`; legacy frame `(0d,14)` SET type 3. API takes APN and profile fields; no complete JSON key schema recovered.
- `IpcTxSetDataProfile` `0x3be8bc`, size `0x2f0`. JSON support check `(0d,1b)`; legacy `(0d,1b)` SET type 3. Full legacy payload and field semantics not recovered.
- `IpcTxSetMobileDataSetting` `0x3c03bc`, size `0x148`, JSON alternative `0x4299d8`. JSON support `(0d,1d)`; legacy frame is 9 bytes, SET type 3, bool args at offsets 7 and 8.
- `IpcTxPsAttach` `0x3be108`, size `0x18c`, JSON alternative `0x4248f0`. JSON support check `(0d,03)`; API parameters include byte, two bools, detach-reason enum. Full legacy layout not established.
- `IpcTxSetDataCallEstablish` `0x3bf728`, size `0x78`: exact 8-byte frame `(02,03)` SET type 3, selector offset 7 `01` true / `02` false.
- RX `IpcRxIpConfiguration` `0x3c0b90`, size `0x364`, JSON alternative `0x4266a4`; type 3 is checked at header offset 6; legacy payload starts flags at offset 8 and conditional address parser. No full DNS/interface-to-offset mapping established.

No SET was transmitted. The live phone was checked: CP ONLINE, rmnet0-7 DOWN; ecm0, wlan0, tailscale0 UP; observed defaults ecm0 and wlan0 metric 600. This check also reaffirms safety boundary: do not guess unknown serializers and put experimental frames on a live modem. `s22-ipc-formats.md` was appended in `/home/corpunum/openunum-briefs` but that location is outside this worktree's git repository and therefore could not be committed from here. No deploy, reboot, route change, or commit/push of that external file occurred. Deployment/command sequence remains blocked pending exact wire layouts and validated implementation; no PR updated.

## Sender completion checkpoint (2026-10-14 UTC)

Owner now explicitly authorizes bounded experimental data SETs; earlier
read-only-only constraints above are historical, not current authorization.
Reviewed WIP f8ff242. Retained command allowlist so ipc-send cannot bypass call,
SMS, SIM or radio-mode gates; exact data-call selectors are enforced. Added
strict GEN wire-type validation, sequence-exhaustion protection and GET ACK vs
RESP handling; aligned Unix HTTP timeout with sender bound. Tests now cover CLI
and HTTP on/off, unrelated sequence/inner-command/type refusal, timeout retention
without retry, cooldown/offline/busy gates and bounded decoded notifications.
Experimental tests added to the explicit hardware-free host regression allowlist.

Pre-deploy live state: phoned PID 2210 from netstat :8095 (not node :18880), modem
ONLINE, IPC open, SMS ready, SIM PB_INIT_COMPLETE, EGPRS Vodafone GR; rmnet0–7
DOWN. ecm0 and wlan0 UP; tailscale0 has UP flag; defaults ecm0 and wlan0 metric
600. Mute check: level=0 and both Digital PCM Volume controls=0. No modem frame
from this checkpoint. Host focused suite: 43 tests passed (Python unittest).

## Wrap-up / no replay checkpoint (2026-10-14 14:55 UTC)

Owner requested wrap-up instead of further mutations. Durable continuation is
`MOBILE-DATA-HANDOFF.md`. Assigned checkout remains clean at saved `49da8ea`
before this documentation addition; `f8ff242` and `49da8ea` were already committed
but remote branch was still `0e770d1`. No implementation was re-applied.

Resolved live executable from `/proc/2210/cmdline`:
`/srv/s22/hardware/bin/s22-phoned`, SHA256
`11ce72e9c4e113e0a1304917f7890171d7c2ca3735cf291291488953c56e8762`,
matching **20acf12** (not the new sender). No experimental sender deployed.
Mute check level/left/right all 0; CP ONLINE, IPC open, SMS ready, EGPRS Vodafone
GR; rmnet0–7 DOWN and rmnet0 RX/TX zero. ecm0/wlan0 UP, tailscale0 UP flag;
all routes read only and unchanged. No deploy/kill/SET/GET/call/SMS/package/reboot
occurred during this wrap-up.

Parsed both retained IPC logs (19,772 valid rows, zero malformed): no experimental
data SET in covered history; historical mode/PDP GETs remain recorded and were
not replayed. Exact TX and RX fields, log coverage, deployed hash, safety rails,
remaining deploy/variant/IP/proof work and test results are in the handoff.
Original mobile-data success/exhausted-variants criterion remains unmet.
Focused tests re-run via host verification: phoned 33 + experiments 10 passed;
one non-failing ResourceWarning for an unclosed simulated Unix socket occurred.
Do not confuse this synthetic proof with actual modem acceptance.
