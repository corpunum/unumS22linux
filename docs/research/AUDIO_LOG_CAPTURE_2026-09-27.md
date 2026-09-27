# S22 ABOX logging coverage preflight (2026-09-27)

## Result

Added `tools/hardware/audio-log-coverage.py`, a host-only preflight that reads
sanitized Memlogger metadata, the pinned source checkout, and retained kernel
`.config`/`.cmd` files. It neither accesses the phone nor changes logging
policy, opens a Memlogger reader, or prints log payloads. Its executable tests
are Python logic and static source-contract checks; they do not execute kernel
C.

The current read-only metadata snapshot, taken after the trial with PCM closed,
reported `abox-mem` enabled at level 2 and `abox-file` disabled at level 2.
`MEMLOG_LEVEL_DEBUG` is 5, and `memlog_write_vsprintf()` rejects a message whose
severity is above the object's configured level. Therefore current
`abox_dbg()` memory-log messages are filtered. This is current policy only: it
does not establish which settings applied during the prior stream, nor whether
any marker was captured. The parser leaves historical capture coverage
`unknown` even when a snapshot is marked as linked to a trial window.

The retained O-tree is `/tmp/s22-hci-candidate-build-20260924` with
`.config` SHA-256
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`.
It records ABOX=m, V4=y, version `0x40001`, memory logger=m,
`CONFIG_DYNAMIC_DEBUG` unset, and `CONFIG_DYNAMIC_DEBUG_CORE=y`. The exact
command records for `abox.o`, `abox_rdma.o`, and `abox_ipc.o` each define
`MODULE` but not `DEBUG` or `DYNAMIC_DEBUG_MODULE`. The source named by those
commands is at `f52cbbd7e2783d529e1e5742d94e0fd64889bbdf`; the ABOX sources,
ABOX logging header, Memlogger source/header, and `dev_printk.h` bytes checked
by the tool match the pinned audit source at
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. With this source/config/flag
combination, the ordered direct `-D`/`-U` command flags (attached or split;
last definition wins) and config predict that the ABOX objects have no
effective `dev_dbg()` enabling macro. This is not preprocessor or object
inspection: macros from forced includes or other headers have not been ruled
out. The parser therefore labels this a prediction with effective macro state
unverified; malformed/missing `-D`/`-U` operands are classified unknown. It
does not claim the `dev_dbg()` path was compiled out. The separate Memlogger
finding is precise: current ABOX
debug-level-5 messages sent to `abox-mem` are filtered by the observed level.
Taken together, current marker capture remains unproven; this says nothing
about unrelated log severities or the historical stream.

## Reader and restoration boundary

Pinned `drivers/soc/samsung/memlogger.c` stores the shared object in each
character-device file's private data. `memlog_read()` and the file object's
`to_string` sysfs show path both use the object's `read_ptr`/`remained` under
`file_mutex`, then advance/deplete that same cursor. The source does not provide
a peek/rollback interface or prove a non-consuming payload reader. Do not open
either path for this diagnostic.

The Memlogger enable/level accessors are plain, unsynchronized accesses and do
not provide an atomic pair update. This preflight performs no writes. It does
not establish safe exact rollback for changing either value; any later
approved change would require preserving and reading back both original values
for each object, and must not change a global level. A current metadata read is
not a trial-window capture.

## Run and interpretation

```sh
python3 -I -B tools/hardware/test-audio-log-coverage.py
python3 -O -I -B tools/hardware/test-audio-log-coverage.py
S22_AUDIO_LOG_SOURCE_TREE=/path/to/pinned/android_kernel_s5e9925 \
S22_AUDIO_LOG_BUILD_TREE=/path/to/retained/kernel-output \
  python3 -I -B tools/hardware/test-audio-log-coverage.py
```

The retained-tree integration run in this worktree passes 12 tests. Missing
metadata or build artifacts remain `unknown`, not negative evidence. Compile
flags are parsed observations from existing `.cmd` files, not reconstructed
build inputs or effective-preprocessor proof. The source checks are literal
static source-contract checks against pinned commit blobs and do not establish
runtime behavior.

No extra zero-stream retry, logging change, log read, gain change, or phone
operation is justified by this finding. Before another muted stream, the next
decisive step is a separately reviewed, bounded, non-consuming diagnostic that
can correlate the capture window to the actual producer boundaries: request
queue result, IPC sender boundary, and selected pointer-handler device/channel
and message type. Generic marker counts cannot be treated as RDMA2 delivery or
firmware completion. Keep amp state unchanged; coordinate any runtime action
with the device owner and independent review. A read-only current metadata
snapshot alone cannot supply the missing historical coverage.
