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

## Resume preflight (2026-10-15 08:02 UTC)

Read git log -8, 49da8ea stat, this file, MOBILE-DATA-HANDOFF and both external
briefs before proceeding. Actual assigned source checkout is the separate git
store `/home/corpunum/.openunum/worktrees/s22-mobile-data`, clean at a16a58b;
remote matches. Initial project checkout was fast-forwarded from 20acf12 to
remote a16a58b only; no source implementation was re-applied.

Live phoned listener PID **2203**, executable `/srv/s22/hardware/bin/s22-phoned`,
SHA256 `11ce72e9c4e113e0a1304917f7890171d7c2ca3735cf291291488953c56e8762`
(still 20acf12). ONLINE, IPC open, SMS ready, PB_INIT_COMPLETE, EGPRS Vodafone GR,
no calls. Uptime 47403s (a different boot than previous checkpoint; no reboot by
this session). Mute check level/left/right=0. rmnet0–7 DOWN; ecm0/wlan0 UP and
tailscale0 UP/LOWER_UP. Defaults unchanged: ecm0 via 10.55.0.1 and wlan0 via
192.168.50.1 metric 600. No link or route mutation.

Downloaded both retained IPC logs to private untracked `out/mobile-data-20261015/`
for row-by-row inspection: **15,016** rows, times 1792014424.907–1792051238.916.
TX counts: (08,05) GET=2448, (08,03) GET=1224, (07,06) GET=1224; zero data-command
TX in this covered interval. No experimental command to replay. This is bounded
retention evidence, not all-time absence. No SET/GET/deploy/restart/reboot in this
preflight. Stock JSON schema and validated IP decoder work proceeds on host;
all phone transmissions remain coordinator-only.

## S2 evidence pass: JSON wire formats (2026-10-17, written BEFORE any new SET)

Blob: `rootfs/cellular-reuse-20260921/vendor/lib64/libsec-ril.so` (S901B FYI3, BuildID
`3d512d374e3729cac2eacd1190bd665b`). Method: `aarch64-linux-gnu-objdump -d -C` plus a small
annotator that resolves `adrp`+`add` pairs to `.rodata` C strings (vaddr == file offset for the
first two LOAD segments). Every key name below is a string literal passed to
`Json::Value::operator[](char const*)` in the named function.

**Why JSON.** Live GETs already proved this CP answers `(08,0A)` and `(0D,04)` with JSON
(`{"mode_select":75}`, `{"num_act":0,"records":[]}`). Each legacy `IpcProtocol41Data::IpcTx*`
builder first asks `JsonUtil::IsSupportJsonCmd(group,index)` and, if true, delegates to
`IpcProtocol41JsonData::IpcTx*`. The binary layouts recorded earlier in this file are the
*legacy* path and are NOT what this CP expects.

**Packet builder** `JsonUtil::MakeJsonPacket(char*,grp,idx,type,Json::Value*)` @`0x409204`:
`Json::FastWriter::write(value)` (compact, keys in std::map order = sorted, ints unquoted,
trailing `\n`), collapses `\\` to `\`, `strncpy`s the text to offset 7, `strh len+7` at
offset 0, `strb` group/index/type at 4/5/6 (`0x409370-0x40937c`). No NUL terminator.

### Corrections to earlier notes
- `IpcTxSetDataCallEstablish(bool)` @`0x3bf728` stores the 64-bit constant `0x0003020300000008`
  (`0x3bf740-0x3bf748`): wire bytes `08 00 00 00 03 02 03 sel`, i.e. **group 0x03 index 0x02**
  (CDMA-data group), not `(02,03)`. `(02,03)` is CALL_RELEASE. The `data-call on|off` sender in
  master (which emits `02 03`) is wrong and is removed; it was never sent to the modem.
- `ActivatePdpContext` (`0x3a11a0`) dispatches through protocol vtable slot +128/+136
  (`0x3a1b20`/`0x3a1af8`) = `IpcTxSetPdpContext` / `...Legacy`, i.e. GSM/LTE activation is
  `(0D,04)` SET, not `(03,02)`.

### F0. JSON command-list GET `(0F,3B)` type GET (read-only)
`IpcProtocol41JsonConfig::IpcTxCfgGetSupportJsonCmdList` @`0x414a28`: key `jver` = string
`"1.0"` (`0x414ab0`, `0x414ac0`), `MakeJsonPacket(...,0x0f,0x3b,0x02)` (`0x414aec-0x414af8`).
`JsonUtil::IsSupportJsonCmd(u8,u8)` @`0x4097b0` hard-codes `(0f,3b)` as always-JSON, and the
reply is fed to `SaveSupportJsonCmdList` (`0x414bd0`), parsed as an object keyed
`net|call|disp|gprs|cfg` (`0x409748`).
Exact frame: `16 00 MM 00 0f 3b 02` + `{"jver":"1.0"}\n`.

### F1. Define PDP context `(0D,01)` SET
`IpcProtocol41JsonData::IpcTxDefinePdpContext(AccessNetworkType, apn, cid, DataProtocol,
SnssaiPdnInfo, DataProfile, bool, u8* v4, u8* v6, char*, int)` @`0x422478`:
- `cid` = cid arg (`0x42254c-0x422560`).
- `pdp_type`: DataProtocol 2→5, 3→6, 4→4 via table `0x17612c` = {5,6,4}; anything else → 2
  (`0x42257c-0x4225a8`). `DataCallSetup::ToDataProtocol` @`0x32ec3c`: "IP"/"0"→1,
  "IPV6"/"1"→2, "IPV4V6"/"2"→3. So **IPv4 "IP" → `pdp_type` 2**.
- `apn` = APN string, rejected if length > 0x65 (`0x422680-0x4226b0`, `0x422794`).
- `pdp_addrv6`/`pdp_addrv4` only if the (static-address) pointers are non-NULL (`0x4227b8`,
  `0x42286c`) — omitted for dynamic addressing.
- `5g_sa` object (snssai/psi/always_on_pdu) only if `SupportSaModemCapa()` (`0x422920`,
  `0x422d10`); the CP is on EGPRS with no SA, so omitted.
- `ap_pco` array: initialised empty (`append`+`clear`, `0x4227ac-0x4227b4`), then if the bool
  arg is false → `[0]` (`0x424148`, `0x42417c-0x424190`); if true → `[12,1]` (IPV4V6), `[1]`
  (IPV6) or `[12]` (IP) (`0x42414c-0x4241e8`). 12/1 are the 3GPP P-CSCF IPv4/IPv6 PCO ids, so
  the bool is the IMS/P-CSCF request (ASSUMED from the values; false for an internet APN).
- `MakeJsonPacket(...,0x0d,0x01,0x03)` (`0x424240-0x42424c`).
Planned frame (cid 1, IPv4, no IMS): `{"ap_pco":[0],"apn":"internet.vodafone.gr","cid":1,"pdp_type":2}\n`.

### F2. Activate / deactivate `(0D,04)` SET
`IpcProtocol41JsonData::IpcTxSetPdpContext(u8 cid, int state, user, pass, int auth, int reason)`
@`0x425274`:
- `cid` (`0x42531c-0x42532c`); `state` = jump table `0x175fbf` maps arg 0..8 identically to wire
  0..8 (`0x425348-0x4254c4`).
- `auth_type` 0 and `reason` 0 always written (`0x4254dc-0x42551c`).
- if wire `state`==1: `username`/`password` only when non-empty (≤100 chars), `auth_type` =
  {1:1, 2:2, 3:1}[auth] else 0 (`0x42555c-0x425648`). → **state 1 = activate**.
- if wire `state`==0: `reason` = table `0x1760d8`[reason+23] for reason in −23..−9, else 0
  (`0x425688-0x4256c0`). → **state 0 = deactivate**.
- `MakeJsonPacket(...,0x0d,0x04,0x03)` (`0x4256e0-0x4256ec`).
Planned frames: up `{"auth_type":0,"cid":1,"reason":0,"state":1}\n`,
down `{"auth_type":0,"cid":1,"reason":0,"state":0}\n`.

### F3 (only if F2 is refused for a data-disabled cause). Mobile-data setting `(0D,1D)` SET
`IpcProtocol41JsonData::IpcTxSetMobileDataSetting(bool data, bool roaming)` @`0x4299d8`: `mode`=1
(`0x429a6c-0x429a78`), `mobile_data_enabled`=data&1 (`0x429a94-0x429aa4`),
`roaming_data_enabled`=roaming&1 (`0x429ac0-0x429ad0`), `MakeJsonPacket(0x0d,0x1d,0x03)`.
Frame: `{"mobile_data_enabled":1,"mode":1,"roaming_data_enabled":0}\n`.

### Expected replies / notifications (JSON RX parsers)
- GEN_PHONE_RES `(80,01)` binary `grp idx type code_le16`; 0x8000 = accepted.
- `(0D,09)` IP configuration (`IpcRxIpConfiguration` @`0x4266a4`, `HandleIpAddr` @`0x421890`):
  keys `cid`, `ipv4_mtu`, `ipv6_mtu`, `psi`, `ipv4_info{addr,dns}`, `ipv6_info{addr,dns}`,
  `pcscf_addr`.
- `(0D,10)` call status (`IpcRxGprsCallStatus` @`0x427640`): `cid`, `data_call_state`,
  `data_callstate_rsn`, `throttle_timer`.
- `(0D,04)` RESP/NOTI (`IpcRxPdpContext` @`0x425a74`): `num_act`, `records[{cid,state}]`.
- `(0D,03)` PS state (`IpcRxGprsPsState` @`0x424bb8`): `state`, `transfer_state`, `suspend_reason`.
Group-0D index names agree with libsamsung-ipc `include/gprs.h:33-49` (01 DEFINE_PDP_CONTEXT,
03 PS, 04 PDP_CONTEXT, 09 IP_CONFIGURATION, 0F PIN_CTRL, 10 CALL_STATUS); the live `0d/0f`
NOTI `{"signal":6,"status":0}` matches `IpcRxDunPinControl`.

### Stored profile
No stock GET builder exists for a stored APN/PDP profile: the only GPRS JSON GETs are
`(0D,26)` PSI query, `(0D,27)` slice, `(0D,28)` QoS, `(0D,2A)` NSSAI, `(0D,2C)` TD query (all
5G-SA) plus `(0D,04)` context state (enumerated from every `MakeJsonPacket` call site). The
modem's NV profile is therefore not readable over IPC; the latest `(0D,04)` GET showed no
active context. We define cid 1 explicitly with the operator APN.

### rmnet mapping (hypothesis, verify live)
rmnetN is `ARPHRD_RAWIP` (type 519). No stock code maps CID→ifname by literal; hypothesis
cid 1 → rmnet0 is to be confirmed by the IP-configuration NOTI and interface counters.

### Send plan (one frame at a time, ≥30 s apart, stop on any error)
F0 GET → F1 define → F2 up (watch for 0D/09, 0D/10) → configure rmnet → proof → F2 down →
F2 up → F2 down. Emergency stop = F2 down.

## S2 live run (2026-10-17 UTC): mobile data works

Deploys (backup + sha256 + rollback):
- phoned 1: `8cd73c76…` (allowlist fix); backup `/srv/s22/hardware/bin/s22-phoned.pre-mobile-data.20261017T081123Z`
  (`11ce72e9…` = 20acf12). Rollback: `cp -p <backup> /srv/s22/hardware/bin/s22-phoned && kill <phoned PID>`.
- phoned 2: `d42e488a…` (mobile-data feature). The second copy landed mode 0644, so keepalive could not exec it
  and phoned (SMS) was down for ~10 min until `chmod 755`. Lesson: `install -m 755`, not `cp`.
- s22d: `3351b95a…`; backup `/srv/s22/hardware/bin/s22d.pre-mobile-data.20261017T082409Z` (`36a615c4…`);
  rollback `sh /srv/s22/hardware/install-s22d.sh --rollback`.

Frames, one at a time, ≥30 s apart; full JSON results in `/srv/s22/state/mobile-data/` on the phone:

| # | TX (full frame) | Reply |
|---|---|---|
| F0 | `160031000f3b027b226a766572223a22312e30227d0a` | RESP `{"call":[26],"cfg":[59],"disp":[6],"gprs":[255],"jver":"1.0","net":[3,4,5,10,17,55,60,69]}`. `IsSupportJsonCmd` @`0x4096cc-0x4096ec` treats 255 as "every index" → **all GPRS commands are JSON** on this CP. |
| F1 | `480036000d01037b2261705f70636f223a5b305d2c2261706e223a22696e7465726e65742e766f6461666f6e652e6772222c22636964223a312c227064705f74797065223a327d0a` | GEN `0c009e368001020d01030080` = 0x8000 accepted |
| F2 up | `34003f000d04037b22617574685f74797065223a302c22636964223a312c22726561736f6e223a302c227374617465223a317d0a` | GEN 0x8000 (`0c00a73f8001020d04030080`), then NOTI `0d/09` `{"cid":1,"ipv4_info":{"addr":"10.207.171.49","default_gateway":"","dns":["<carrier-ip>","<carrier-ip>"],"pcscf_addr":[],"subnet_mask":""},"ipv4_mtu":1500,"ipv6_mtu":0}` and NOTI `0d/10` `{"cid":1,"data_call_state":1,"data_callstate_rsn":0,"external_ps_call":0,"qci":0,"throttle_timer":0}` |
| F2 down | `340054000d04037b22617574685f74797065223a302c22636964223a312c22726561736f6e223a302c227374617465223a307d0a` | GEN 0x8000 (`0c00dc548001020d04030080`), NOTI `0d/10` `data_call_state 3` (released) |

No mobile-data-setting `(0D,1D)` frame was needed. Modem stayed ONLINE, SMS ready, EGPRS/home_3gpp throughout.

Interface: cid 1 → **rmnet0** confirmed (traffic flowed). `ip addr add <ip>/32 dev rmnet0; ip link set rmnet0 mtu 1500 up;
ip route add default dev rmnet0 metric 9000` (ecm0 metric 0 and wlan0 600 unchanged). Proof:
`curl --interface rmnet0 https://example.com` → HTTP 200 (4.9 s, then 1.8 s); public egress IP was a Vodafone GR
address. Speed (EGPRS): 1,000,000 B in 93.9 s ≈ 10.7 kB/s; later 500,000 B ≈ 9.4 kB/s.

Re-activation: after the manual down, `s22-phoned mobile-data up` (new address `10.207.187.184`), then via s22d
`POST /v1/mobile-data/down` → `call_state 3`, `POST /v1/mobile-data/up` → `10.206.81.7` + curl 200, final
`POST /v1/mobile-data/down`. End state: data call released, rmnet0 DOWN, no rmnet route, modem ONLINE, muted.

Open items: radio stays on EGPRS (2G) — LTE needs the initial-attach APN `(0D,14)`/profile sync `(0D,25)`
work, not done here. DNS servers are reported but not written to resolv.conf (system DNS stays on Wi-Fi/USB).
phoned keeps data state in memory; a phoned restart while data is up loses track of it (run `down` first).

## S2b: LTE initial-attach APN from OPEN SOURCES ONLY (2026-10-17, written BEFORE the SET)

Rule for this step: no disassembly of libsec-ril; only public open-source code.

Sources fetched (git, shallow):
- Replicant libsamsung-ipc `https://git.replicant.us/replicant/hardware_replicant_libsamsung-ipc.git`
  master `81a412c` and branch `wip/herolte` `784c15b`: group 0x0D ends at `IPC_GPRS_PORT_LIST 0x0D11`
  (`include/gprs.h:33-49`). **No attach-APN / LTE profile command upstream.**
- Replicant samsung-ril `https://git.replicant.us/replicant/hardware_replicant_libsamsung-ril.git` `2dd9f22`:
  no `RIL_REQUEST_SET_INITIAL_ATTACH_APN` / data-profile handling.
- libsamsung-ipc fork for Shannon LTE modems `https://github.com/manoedinata/libsamsung-ipc` (GPL-2.0, branch
  `shannon-lte-data`, commit `bba6a0d`): **defines it.**
  - `include/gprs.h:63` `#define IPC_GPRS_LTE_ATTACH_APN_INFO 0x0D14` (also `:70` `SET_APN_INFO 0x0D1B`,
    `:77` `SYNC_PROFILE_INFO 0x0D25`, names only).
  - `include/gprs.h:222-250` `struct ipc_gprs_lte_attach_apn_info_data` (packed, 194 bytes, size-checked at
    `samsung-ipc/gprs.c:34-35`; offsets asserted in `samsung-ipc/tests/shannon_data.c:116-123`).
  - `samsung-ipc/gprs.c:120-158` `ipc_gprs_lte_attach_apn_info_setup()`: type 1, profile_id arg, protocol and
    protocol_roaming = `IPC_GPRS_PROTOCOL_IPV4` 0x02 (`include/gprs.h:124`), APN strncpy, rest zero.
  - Typical usage: samsungipcd `https://github.com/manoedinata/samsungipcd` `4e7bafd` `samsung-ipc.c:319-321`
    (and `:435-437`): `setup(&attach, 1, apn, NULL, NULL)` then `ipc_client_send(..., IPC_GPRS_LTE_ATTACH_APN_INFO,
    IPC_TYPE_SET, &attach, sizeof(attach))`. README: on a Galaxy J4 the modem re-registers onto LTE a few seconds
  after this SET, answer code 0x8000.
- Supporting evidence on a JSON-era Shannon CP (S5308, Galaxy A33), public doc
  `github.com/mistrjirka/Linuxa33 docs/A33_MODEMMANAGER_SIPC_INTEGRATION_2026-09-28.md:361`: "the legacy
  daemon's 0x0d14 APN attach SET ... was accepted by the modem". So the binary layout is accepted even
  where other GPRS commands are JSON (our CP reports `gprs:[255]`).
- No GET for 0x0D14 is defined in any of these sources -> step (2) "read current value" is impossible from
  open sources; nothing is read. 0x0D14 was never sent by phoned before (ipc.jsonl, RECON above).

Frame layout (payload offsets; IPC header `len_le16 mseq aseq grp idx type`):

| off | size | field | value |
|---|---|---|---|
| 0 | 1 | type | 0x01 |
| 1 | 1 | profile_id | 0x01 |
| 2 | 1 | protocol | 0x02 (IPv4) |
| 3 | 101 | apn | `internet.vodafone.gr` NUL-padded |
| 104 | 23 | unknown1..4 | 0 |
| 127 | 32 | username | 0 |
| 159 | 32 | password | 0 |
| 191 | 1 | auth | 0 |
| 192 | 1 | protocol_roaming | 0x02 |
| 193 | 1 | unknown5 | 0 |

Exact TX (mseq shown as 00): `c9 00 00 00 0d 14 03` + `010102 696e7465726e65742e766f6461666f6e652e6772` +
zeros to offset 192 + `02 00`. Builder: `lte_attach_apn_body()` in `tools/hardware/phoned/s22-phoned.py`,
tested in `test_mobile_data.py`. IPv4 chosen because it is the only protocol value the source verified.

Plan: one `s22-phoned ipc-send 0x0d 0x14 3 <hex>` (allow-listed already in deployed phoned `d42e488a`, no
deploy). Expect GEN 0x8000 and a spontaneous re-registration (J4 behaviour). If none in ~60 s, least
invasive trigger. Watch act/band/RSRP up to 3 min. Rollback if registration lost > 2 min: one SET of the same
command with an empty APN (network-default attach). Data stays DOWN throughout.

## S2b live result (2026-10-17 UTC): on LTE

One SET only, no deploy (phoned `d42e488a` = master already allow-lists `(0D,14)`), no trigger needed.

- Before (epoch 1792228041): `ONLINE egprs band 0 home_3gpp ps rej 0`, RSRP 0, lte_sig 99, rssi_level 32, volte 2.
- TX `c90099000d1403010102696e7465726e65742e766f6461666f6e652e6772` + zeros + `0200` (194-byte payload, mseq 0x99).
- Reply GEN `0c00cf998001020d14030080` = **0x8000 accepted**. Result JSON in `/srv/s22/state/mobile-data/lte-attach-apn-*.json`.
- +10 s: `act lte`, service_domain `csps`; from +20 s steady `lte home_3gpp ps rej 0`, PCI 334, TAC 3062,
  RSRP **-99 dBm**, RSRQ -7 dB, SNR 30 dB, lte_sig 12, rssi_level 72, bars 3. Registration never dropped (3 min, 10 s polls).
- The modem's JSON `band` field stays 0 on LTE too (not populated by this CP), so band is unknown, not "0".
- Modem-reported `volte` changed 2 -> 1 and `ims_emc` 2 -> 1 after moving to LTE. No IMS/VoLTE command was sent;
  no calls, no SMS. Meaning of these values is not established.
- End state: ONLINE, SMS ready, SIM READY, data call released (call_state 3), rmnet0 DOWN, routes unchanged
  (ecm0 default, wlan0 metric 600), ecm0/wlan0/tailscale0 up. No rollback needed.

Not persisted: the profile is sent by hand; after a modem/phone restart it must be sent again (samsungipcd sends
it when the SIM is up). Follow-up: have phoned send `lte_attach_apn_body(DEFAULT_APN)` once after SIM READY.
