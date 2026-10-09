# S22 passive audio metadata collector correction — 2026-10-02

## Scope and prior evidence

This host-only change corrects `tools/hardware/audio-control-readiness.py`.
The collector reads the local process environment's named `/proc/asound` and
`/sys/class/sound` files. It does not connect to a phone or identify which
machine is running it. On the native S22 those values are local target
observations; on a workstation they are workstation observations.

The earlier runtime note records a real Rainbow-Prince ALSA card and a
read-only `amixer controls` enumeration after `/dev/snd/controlC0` was repaired
for that boot. That history does not make a mixer utility invocation passive:
the utility opens an ALSA control interface even when it only lists metadata.
The default path now avoids both mixer executables. The separate 2026-09-27
audio trial remains a failed progress diagnostic with no physical-output
acceptance; this collector makes no route, DMA, firmware, or audio-readiness
claim. See [runtime audio recovery](../RUNTIME_AUDIO_RECOVERY_2026-09-22.md),
[the bounded audio trial](AUDIO_TRIAL_2026-09-27.md), and [the ABOX capture
profile](AUDIO_CAPTURE_PROFILE_2026-10-01.md).

## Corrected behavior

- Default execution reads only named local procfs/sysfs files. It does not
  call `shutil.which`, `amixer`, or `tinymix`.
- `--list-controls` explicitly enables only `amixer -c 0 controls` and
  no-argument `tinymix`. No `cset`, `set`, PCM playback, or PCM capture
  arguments are constructed. The JSON distinguishes an opt-in request from a
  confirmed control-node open; the listing command does not independently
  verify which node the utility opened.
- Every file read is capped at its configured byte limit plus one detection
  byte. Records report `bytes_read`, `truncated`, `available`, and any I/O
  error. Non-regular files and symlink source XML are refused.
- Optional mixer XML is capped at 256 KiB. Parsing and hashing happen only
  after a complete read. Oversize input, I/O errors, forbidden DTD/entity
  declarations, and XML parse failures are reported explicitly.
- Mixer subprocess output is drained through pipes and retained only up to
  256 KiB on stdout and 4 KiB on stderr. Excess output is discarded in
  memory; temporary output files are not used. The command deadline is 5 s,
  followed by bounded process-group termination/drain and reaping. Result
  fields separate command `available` from collector `complete`; a successful
  exit with truncated output is available but incomplete.
- The JSON names the execution scope as the local process environment and
  leaves target identity unverified. `remote_phone_access: false` means no
  separate remote phone connection; it does not mean local procfs/sysfs reads
  are unrelated to the phone when run on the phone itself.

## Interface audit

Repository search found no call sites for this script and no consumer of its
`read()` helper. `read(path, limit)` now returns a structured record instead of
`str | None`, and the JSON shape is intentionally updated: `phone_access` is
removed, procfs/sysfs values are structured records, and execution context,
control-listing intent, truncation, completeness, and error fields are
explicit. `--mixer-xml` remains supported; `--list-controls` is additive and
off by default. External consumers were not available for compatibility
verification.

## Regression evidence

The new test executes the actual helper with local synthetic files and short
Python subprocesses; it does not use `amixer`, `tinymix`, phone interfaces, or
audio hardware.

Before the fix, executable checks against the baseline helper reproduced:

- Default `main()` looked up `amixer`, and `--list-controls` was rejected as
  an unknown argument.
- A child writing 1 MiB to each output stream caused the helper to retain only
  its advertised 262,144/4,096-byte prefixes while its two temporary files
  grew to 2,097,152 bytes total.
- After the five-second timeout, the helper reaped the direct child but left
  that child's spawned descendant running.
- File and XML results did not expose truncation or complete-read status; XML
  parsing and `Path.read_bytes()` were unbounded.

After the fix, all eight helper regression cases pass under the normal
interpreter, `python3 -O`, and `PYTHONOPTIMIZE=1`. They cover passive default
behavior, fixed opt-in arguments, bounded/error-reporting file and XML reads,
oversize XML refusal, output retention with zero temporary-file bytes,
timeout cleanup of an inherited-pipe process group, non-finite budget refusal,
and collector completeness.

```sh
python3 -I -B tools/hardware/test-audio-control-readiness.py
python3 -O -I -B tools/hardware/test-audio-control-readiness.py
PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-control-readiness.py
```

These checks establish host-side collector behavior only. No phone, SSH, ADB,
service, inference, build, firmware, deployment, reboot, push, mixer, PCM, or
other hardware operation was performed. They do not establish control-node
permissions, route availability, DMA progress, audible output, or device
acceptance. The explicit listing option has not been used against hardware.

## Follow-up: exception-safe child teardown

An independent review of frozen commit `eb25aa24171cd8c4efa6ec31935e035bd79f5e06`
found that `listing()` cleaned up only `OSError`. A `KeyboardInterrupt` or
another exception from selector registration, selection, or pipe draining
closed the selector and pipes in `finally` but skipped child termination and
reaping. This is an additional lifecycle defect; it does not replace the
earlier output-disk or timeout-descendant reproductions above.

Four executable regressions were run against that frozen helper before the
fix. Injected selector `KeyboardInterrupt`, selector-registration
`RuntimeError`, pipe-drain `RuntimeError`, and process-group termination
failure each left the controlled sleeper alive when the helper propagated the
exception. The tests killed and reaped those sleepers after recording the
failure, so the baseline run left no test process behind.

The follow-up keeps selector creation before process launch, then puts every
post-spawn setup, select/drain, output-close, and reap step inside an exception
boundary. Any escaping `BaseException` triggers process-group SIGKILL, bounded
pipe closure and direct-child wait; the original exception object and type are
re-raised. If termination or reaping is incomplete, cleanup diagnostics are
attached as exception notes (or an exception attribute on Python versions
without `add_note`). Ordinary timeout results also expose `cleanup_errors` and
`cleanup_complete`, and cannot claim complete capture when teardown failed.

The helper suite now has 12 cases. All pass in normal Python, `python3 -O`, and
`PYTHONOPTIMIZE=1`, including the four exception-path regressions. These are
controlled host subprocesses only; they do not invoke the mixer utilities or
contact the S22.
