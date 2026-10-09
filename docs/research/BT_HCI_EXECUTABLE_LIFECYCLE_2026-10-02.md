# Executed HCI lifecycle source coverage — 2026-10-02

The new `test-bt-hci-lifecycle-c.py` executes functions extracted from pinned
Lineage source `4e5c5ad7d950e4de0688b5663965f2075654b2ad`, before and after
the existing, unchanged `bt-hci-socket-restore.patch`. No phone, Bluetooth
socket, controller, module or power state was touched. This extends the
existing source-string checks; it is not a live Linux socket test.

The pinned baseline `hci_sock_create()` really returns success with no socket
allocated because its body is disabled. The baseline `hci_sock_init()` is
**not** disabled: registration is preserved. The executed negative and positive
cases distinguish those facts rather than inferring every lifecycle function
has the same disabled behavior.

## Executed paths

The extracted C includes actual create, release, destructor, cookie allocation
and release, initialization and cleanup functions. Kernel socket allocation,
locks, SKB/monitor delivery, device references and registration are explicit
host shims. Cases cover unsupported socket types, allocation failure before
publication, null release, 200 sequential raw create/close operations, RAW,
USER and MONITOR cleanup, destructor queue cleanup, device reference/flag
cleanup, and protocol/socket/proc registration error unwind and reverse cleanup.
No actual bind/ioctl, privilege boundary, concurrent Linux locking, radio,
controller attachment or pairing is accepted by these cases.

The allocation-failure audit initially suspected the cookie sentinel might
reach an invalid IDA release. The complete pinned implementation disproves
that concern: `ida_simple_remove` maps to `ida_free`, whose initial negative-ID
guard returns before XArray access. The test therefore executes the **actual
extracted `ida_free()` body** too, with XArray/bitmap/locking shims, and checks
allocation-failure and already-freed sentinel paths, one normal positive ID
release and no unallocated-ID warning. Sparse XArray value entries and complete
allocator concurrency are not modelled. No cookie repair patch is warranted;
the exploratory draft was removed before publication. The signed cookie
conversion test requires 32-bit `int`, as on the target and this host.

Initial host development failures are not hardware failures: the first C draft
used a counter named `puts`, colliding with libc; the next draft wrongly
expected baseline initialization to be disabled. Both were corrected using
the pinned source, and the final test preserves the actual baseline behavior.

## Pinned inputs and executed commands

| Input | SHA-256 |
| --- | --- |
| `net/bluetooth/hci_sock.c` (47,917 bytes) | `672c58217ea7becf77f5eed0d77d6f5c28a516b8843dfe23243900774b972316` |
| `lib/idr.c` | `a5f5799202aebdcfad0d266c241a18da3f43a04cf04bdd63f37c7536087cc4b8` |
| `include/linux/idr.h` | `485060f431354181579bbf8b9e92f276834192f94c604c69a3688dbf12c2bad9` |
| Existing restoration patch | `b342b92d262767c3dd6ed71f1037e7c63eca253efde23f285144baf2b2c44d08` |

Each public file has a 128 KiB cap, exact hash, HTTPS pinned commit URL,
five-second network timeout and redirect rejection. An explicit local tree
loads the exact committed blobs, not its working files, and cannot silently
fall back to a public download. Environmental fetch failures alone exit 77;
wrong hashes, redirects, malformed lengths and missing configured trees fail.
This does not alter other suites' public-tool/fixture procedures.

The commands below passed in normal Python, `-O`, and `PYTHONOPTIMIZE=1`, each
compiling and executing actual extracted C with host `cc` at `-O0` and `-O2`:

```sh
python3 tools/hardware/test-bt-hci-lifecycle-c.py
python3 -O tools/hardware/test-bt-hci-lifecycle-c.py
PYTHONOPTIMIZE=1 python3 tools/hardware/test-bt-hci-lifecycle-c.py
```

The same three modes passed with an explicit preserved local kernel tree.
These six invocations are host evidence. Independent review and full integrated
CI are recorded separately when completed. Existing live Bluetooth milestones
and consumed operation authorizations are unchanged.
