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
