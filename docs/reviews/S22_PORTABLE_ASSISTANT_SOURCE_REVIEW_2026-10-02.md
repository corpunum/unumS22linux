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
