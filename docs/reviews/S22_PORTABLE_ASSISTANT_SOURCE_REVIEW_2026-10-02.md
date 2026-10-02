# S22 portable-assistant source review — 2026-10-02

## Scope and reviewed revision

Independent source review of the frozen cellular collector at author commit
827e2d64bbde2cb22f5133f358205920660b3530 in
/home/corpunum/s22-workers/cellular-assistant-20261002. The author worktree
was clean at that SHA. This section covers only that cellular wave; button and
NPU waves will be appended after their authors provide frozen revisions.

No phone, stream, SSH, ADB, modem, NPU, kernel build, module, package, or
service operation was performed. No subscriber identifiers, carrier data, or
private modem traces were collected or published. This is a host source and
fixture review, not device acceptance.

## Findings

1. **Driver binding can be reported from a dangling or out-of-namespace link.**
   tools/hardware/cellular-readiness-evidence.py::_driver_binding classifies
   any driver symlink whose text ends in cp_interface as
   bound_cp_interface; it does not resolve the link, verify the target exists,
   or establish that it is the expected sysfs driver directory. The supplied
   FakeSysfs.cpif_device() fixture creates the drivers parent but not the
   cp_interface target, so the passing “bound” fixtures actually contain a
   dangling link. Report this case as unknown (and distinguish a verified
   binding from an unbound device); constrain a positive classification to the
   expected sysfs namespace.

2. **Per-module lookup errors disappear from an otherwise ok inventory.**
   _module_inventory() records present and absent, but drops unknown results
   returned by _lstat_status() and still returns status: "ok".
   A consumer cannot tell whether an omitted module name was not checked,
   unreadable, or absent from both arrays. Preserve a per-name unknown result
   or mark the inventory incomplete when any lookup is unknown. Module
   presence remains inventory only and must not be treated as modem or service
   readiness.

Neither finding promotes SIM/data/IMS/voice readiness: every higher-layer
readiness field remains fixed at unknown. They do, however, weaken the
collector's inventory evidence and the supplied fixture's binding assertion;
correct these semantics before relying on positive binding/module inventory
claims.

## Confirmed source boundaries

- The collector opens only the fixed modem_state and operstate text paths,
  with reads capped at 129 bytes and 33 bytes respectively. It scans only the
  fixed sysfs module root, /sys/class/net, and /sys/class/misc; directory
  enumeration is capped at 256 entries. The RMNET list is limited to
  rmnet0–rmnet7, module names are a fixed tuple, and UMTS class names are
  filtered by a bounded pattern.
- It does not open /dev/umts_*, read /proc or modem traces, issue AT/QMI,
  send network traffic, write sysfs, load firmware, or make calls. Generic
  mif matches and WLAN interfaces are not treated as CPIF/RMNET evidence.
- The readiness map initializes SIM presence and registration, data session,
  forced route, DNS/HTTPS, IMS, incoming/outgoing voice, two-way audio, and the
  portable-assistant cellular mission to unknown. CP ONLINE is separately
  represented as an observed kernel state, and RMNET up remains only a link
  state.
- The pinned local kernel checkout was at the cited commit
  4e5c5ad7d950e4de0688b5663965f2075654b2ad. Its modem_state_string[] matches
  the collector vocabulary; modem_state_show() formats mc->phone_state with a
  newline and the neighboring do_cp_crash attribute is write-only. The
  collector correctly avoids that write-only attribute. This validates parser
  vocabulary and attribute semantics, not CP operation or telephony readiness.
- The report's supplied sample is explicitly time-stamped and redacted; the
  fixture checks the reported INIT, eight RMNET down values, and WLAN exclusion
  without embedding subscriber or carrier data. This review did not
  independently recapture that sample.

## Executed evidence

Ran only the frozen author's hardware-free test file with bytecode disabled:

    python3 -B tools/hardware/test-cellular-readiness-evidence.py
      7 tests passed
    python3 -B -O tools/hardware/test-cellular-readiness-evidence.py
      7 tests passed
    PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-cellular-readiness-evidence.py
      7 tests passed

These results establish fixture/parser behavior and optimized-interpreter
stability. They do not validate live /sys, binding-target existence in the
fixture, CP boot, SIM registration, data routing, IMS, calls, or audio.

## Review disposition

The evidence boundary is appropriately conservative about cellular service,
and protected NV/EFS and no-identifier publication constraints remain intact.
The two inventory-status findings above should be corrected before describing
the collector as proving a positive cp_interface binding or a complete
module inventory. No hardware gate is cleared by this source review.

## Cellular follow-up review — 2026-10-02

Reviewed frozen author follow-up commit
4c733d2bd3e8d55cf2cd9ed556b4b5ca7b15a697, parent
827e2d64bbde2cb22f5133f358205920660b3530. The author worktree was clean.
Collector and test-file SHA-256 values matched the supplied frozen hashes:

- cellular-readiness-evidence.py:
  c1ae33ba03d3121b517d92963013eb9415b02b46fcabc2c2e116c656e8a2ec84
- test-cellular-readiness-evidence.py:
  afabb542e5275e09d9ef4862e96941767251ab328d23b042b1953e45f822f12b

The driver classifier now resolves links strictly, requires a real driver
directory in the injected sysfs driver's namespace, and compares the resolved
target to the exact cp_interface entry. Dangling links and a same-basename
target outside that namespace return unknown; a different valid driver in the
namespace returns bound_other. Its fixtures now create the target directory
and explicitly cover those distinctions, including a non-symlink entry.

The module inventory now records each fixed module name as listed,
not_listed, or unknown. Lookup failures retain only a sanitized error class;
any unknown per-module result produces partial inventory status. An unavailable
module root marks each expected module unknown with a root-level reason. This
closes the earlier silent-omission issue without treating module presence as
CP or service readiness.

Executed only the frozen follow-up test file with bytecode disabled:

    python3 -B tools/hardware/test-cellular-readiness-evidence.py
      12 tests passed
    python3 -B -O tools/hardware/test-cellular-readiness-evidence.py
      12 tests passed
    PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-cellular-readiness-evidence.py
      12 tests passed

The new dangling-link, out-of-namespace, valid-other-driver, non-symlink, and
per-module permission-error fixtures exercise the two prior findings. These
are host fixture results only. No phone access, CP boot, SIM query, data
session, IMS, call, or audio test occurred. The corrected inventory semantics
remove the two previously reported source blockers; cellular service and
portable-assistant readiness remain unaccepted.

## Physical-button source review — 2026-10-02

Reviewed frozen author commit dcfa9754e01d344c2d88b40bcb9471ef92471bd9
in /home/corpunum/s22-workers/buttons-assistant-20261002, based on
2524ff036a6190bb4a22dc94606a2da5a739ebe4. Its worktree was clean and its
change is exactly the five reported files. No phone, SSH, ADB, event-node
open, physical key press, config change, service, or package operation was
performed by this reviewer.

### Source behavior and evidence boundary

- Candidate selection requires valid EV and key capability bitmaps, EV_KEY,
  at least one of POWER/VOLUMEUP/VOLUMEDOWN, a present event path, and a
  character-node rdev matching sysfs. The collector opens only candidates,
  with O_RDONLY, O_NONBLOCK, and O_CLOEXEC, then checks the opened descriptor
  again with fstat for character type and the exact major/minor pair before
  reading it.
- The stored event list is filtered to the three target EV_KEY codes and
  SYN_REPORT/SYN_DROPPED integrity records. Other key codes and event types
  are discarded; there is no ioctl, write, injection, or EVIOCGRAB path.
  Event rows retain the device path/name and kernel timestamp as metadata.
  The separate inventory still includes each input node's full raw key
  capability bitmap and decoded capability codes; that describes supported
  inputs, not keypresses. Thus the event stream is limited to the three
  buttons, while the complete JSON is not limited to those three capability
  codes.
- The analyzer rechecks the advertised target capability from raw EV/key
  bitmaps, requires the opened-node identity fields to agree, associates events
  with one device, and counts transitions only inside SYN_REPORT frames. It
  ignores records after SYN_DROPPED until the next SYN_REPORT and keeps that
  source incomplete. An observed sequence is explicitly not proof of human
  origin, built-in-switch origin, compositor dispatch, or audio effect.
- The collector's six incomplete-evidence routes are fail-closed: candidate
  open failure, read error/EOF, partial input_event record, raw-record limit,
  SYN_DROPPED, and an unterminated event frame. The source marks affected
  evidence incomplete and prevents it from becoming a press/release result.
  Acquired descriptors are closed directly on descriptor-verification
  failures and from a finally block after accepted descriptors enter the
  handle list. This is source-reviewed lifecycle ownership, not a live evdev
  run.
- The host tests cover event ordering, repeats, one-sided transitions,
  SYN_DROPPED, invalid/malformed records, partial frames/records, flood-limit
  status, EOF/open-error status, capability violations, private-key filtering,
  descriptor type/device-number rejection, and bounded arguments. Some
  quarantine states (read-error, flood-limit, and partial-record status) are
  supplied directly to the analyzer rather than fault-injected through the
  collector's syscall loop. The collector-level fake-node case checks read-only
  flags, no ioctl/write, target-only event output, and EOF invalidation.

Pinned kernel source at 4e5c5ad7d950e4de0688b5663965f2075654b2ad confirms the
input capability bitmap printer emits native unsigned-long words in
most-significant-word-first order, within a PAGE_SIZE sysfs buffer. The button
report's test vectors decode the target's 64-bit examples to volume-up 115 and
volume-down/power 114/116. This supports the parser format; it is not a button
event from the handset.

### Boundedness, compatibility, and hardware evidence

The requested event duration is limited to 300 seconds and raw records to
100,000 (default 4,096). The monotonic capture deadline is set after capability
inventory and candidate opens, so seconds bounds the event-read window rather
than total command runtime. Capability and general sysfs reads use Path.read_text
without an application byte cap; the pinned kernel sysfs attributes are
PAGE_SIZE-bounded, but the Python helper does not impose a byte budget on an
arbitrary injected filesystem.

The wrapper's --events result changed from a generic events list to a nested
button_event_evidence report. Repository search found prior CLI invocation
documentation, tests, and test-runner entries, but no committed code consumer
parsing the old event schema. The new button document describes the wrapper.
This source review does not establish compatibility with files installed on
the phone.

A separate coordinator-supplied static read at 2026-10-02 12:05:02 UTC reports
sysfs event0 dev 13:64 matching a character /dev/input/event0 with rdev 13:64,
and event1 dev 13:65 matching a character /dev/input/event1 with rdev 13:65.
It supplements the author's earlier time-stamped snapshot, which did not
include major/minor values; it does not revise that earlier observation. This
review did not collect it or open either node. It supports the pre-open
selection baseline only; the collector's post-open fstat check, actual evdev
press/release, physical ownership, and downstream effects remain untested.

Executed only the frozen author's two host test files with bytecode disabled:

    input-power-readiness.py tests:       6 passed under each of 3 modes
    button-event-evidence.py tests:      13 passed under each of 3 modes
    modes: python3 -B, python3 -B -O, and PYTHONOPTIMIZE=1 python3 -B

These are synthetic host results. No physical press/release, Linux event
capture, DPMS result, suspend/resume, long-press behavior, cold boot, or volume
audio path was accepted. The supplied report keeps the already-deployed Lua
DPMS binding separate from physical key evidence; it records no active audio
daemon/volume route and does not authorize config changes.

## Integration CI allowlist review — 2026-10-02

Reviewed frozen integration commit 8f74fd80fe4a6b88524350ab48c453ad771cf896
(parent 22cbb3653c6dcf2c122cfb24b4696e1bb0e6c9be). The exact two-file diff
adds test-button-event-evidence.py and test-cellular-readiness-evidence.py to
the reviewed-path tuple and explicit HOST_TESTS tuple in
tools/hardware/run-host-regressions.py, and to
EXPECTED_HOST_TEST_PATHS in tools/hardware/test-host-regression-runner.py.
There is no dynamic test discovery or change to live-device exclusion policy.

Executed only the host-regression policy test file with bytecode disabled:

    python3 -B tools/hardware/test-host-regression-runner.py
      7 tests passed
    python3 -B -O tools/hardware/test-host-regression-runner.py
      7 tests passed
    PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-host-regression-runner.py
      7 tests passed

The policy tests cover explicit unique local allowlists, child-environment
credential/device-override filtering, optimization-aware skips, path traversal
and duplicate rejection, live-device-script substitution rejection, symlink
escape rejection, and explicit execution/failure aggregation. This is a
bounded static wiring review and policy-test result, not a run of the full
hardware host suite.
