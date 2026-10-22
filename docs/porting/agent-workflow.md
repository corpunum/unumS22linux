# Working with coding agents

Part of the [porting guide](../PORTING.md). The owner of this project does not write code. AI
agents did the engineering: Claude, GPT models (Luna, Astra, Sol) through
[OpenUnum](https://corpunum.com), and a local Qwen, some of it running on the phone itself
([README](../../README.md#the-story)). This chapter is what made that work, and what didn't.

## Roles

**One coordinator owns the phone.** Only the coordinator connects to the device, changes hardware
state, deploys, flashes or reboots. Parallel workers read sources, write patches and run host tests
in their own git worktrees and hand back commits; they never touch the phone. Hardware experiments
that might share clocks, power or memory (GPU, audio, Bluetooth, Wi-Fi) are never run at the same
time ([S22_LUNA_DRIVER_COMPLETION_MISSION.md](../S22_LUNA_DRIVER_COMPLETION_MISSION.md#1-mission-and-operating-rules)).

**Workers get a bounded job.** Each worker gets: the files it owns, its dependencies, acceptance
criteria and the commands to run. One shared task board tracks who does what, in which worktree,
with which result commit. Workers that loop without new evidence are stopped
([S22_LUNA_DRIVER_COMPLETION_MISSION.md](../S22_LUNA_DRIVER_COMPLETION_MISSION.md#11-mandatory-luna-only-parallel-execution);
the boards: [S22_LUNA_TASK_BOARD_2026-10-02.md](../S22_LUNA_TASK_BOARD_2026-10-02.md) and earlier).

**The author is never the only reviewer of a risky change.** An independent agent reviews it
first ([S22_LUNA_DRIVER_COMPLETION_MISSION.md](../S22_LUNA_DRIVER_COMPLETION_MISSION.md#11-mandatory-luna-only-parallel-execution)).
46 written reviews are in [docs/reviews/](../reviews/).

**The owner decides what can break things.** Flashing, formatting, enabling a radio, powering a new
chip, talking to the mobile network: each one was asked for and approved as a specific, named step
(for example the NPU sequence "owner-approved single sequence, stopped at first failure",
[evidence/npu-20261007](../../evidence/npu-20261007/README.md); GNSS power-on "the owner gave
permission", [evidence/gnss-20261022](../../evidence/gnss-20261022/README.md)).

## The adversarial review loop

Before the first native boot, the boot plan went through rounds between two different models,
each told to **find mistakes, not to agree**, each with root ADB on the phone (still on the safe
Lineage recovery) to check claims for real:

1. model A reviews the plan and the code;
2. model B cross-checks A's findings, including A's own proposals;
3. corrections are applied;
4. repeat until a round finds nothing new.

It caught real errors in both directions: no devtmpfs in the kernel, an invalid watchdog test, a
file-descriptor bug in our init, and one reviewer's own wrong proposal about power-off, which the
other refuted from the kernel config. Nothing was flashed during the loop
([EXPERIMENTS.md](../../EXPERIMENTS.md), "Opus 5 independent review" and "3-round Opus<->Astra
adversarial review loop"). The same pattern was used later for the camera client
("GO-WITH-CHANGES", [CAMERA.md](../../tools/hardware/camera/CAMERA.md#independent-review-2026-10-08-go-with-changes-applied))
and the 46 written reviews in [docs/reviews/](../reviews/) (audio, Bluetooth, NPU, camera and host
tooling).

Tips from doing this:

- Our recommendation (not what every later round did): use **different model families** for
  author and reviewer; they make different mistakes. The early boot loop paired Opus with Astra;
  the later driver waves deliberately used Luna for all roles
  ([S22_LUNA_DRIVER_COMPLETION_MISSION.md](../S22_LUNA_DRIVER_COMPLETION_MISSION.md#11-mandatory-luna-only-parallel-execution)).
- Give reviewers access to the real thing (live read-only commands, the actual binaries and the
  exact kernel source), not just your summary.
- A local model prompted directly with an open-ended architecture question and a big token
  budget got stuck in reasoning loops and produced nothing; the same question run through
  OpenUnum's multi-turn orchestration converged on an answer
  ([EXPERIMENTS.md](../../EXPERIMENTS.md), "cross-model consultation round 2").

## Evidence rules

These are what keep a project run by agents honest:

- **Acceptance means measured device output**, checked against an expected value. Not an exit code,
  not "the service is running", not a model's opinion
  ([CONTRIBUTING.md](../../CONTRIBUTING.md#3-how-fixes-are-verified),
  [tools/gpu-compat/README.md](../../tools/gpu-compat/README.md#running-the-already-staged-control-tests)).
  Example: GPU compute counted only with the exact checksum, the right device name, the same boot and
  no new kernel GPU messages.
- **Write failures down too.** [EXPERIMENTS.md](../../EXPERIMENTS.md) keeps every failed attempt;
  each device session gets an `evidence/<topic>-<date>/README.md` with method, numbers, raw logs and
  a "Not verified" list (for example [evidence/camera-develop-20261022](../../evidence/camera-develop-20261022/README.md)).
- **Say what was injected.** Taps injected into evdev are not a finger; a synthetic test is not a
  physical one ([evidence/fastshell-20261021](../../evidence/fastshell-20261021/README.md)).
- **Correct earlier claims in place** when they turn out wrong, and keep the correction visible
  ([EXPERIMENTS.md](../../EXPERIMENTS.md), "Provenance correction").
- **Hardware-free tests run in CI** from a fixed allowlist, with no secrets, no network to the phone,
  and no vendor files: `python3 -I -B tools/hardware/run-host-regressions.py --mode both` (and
  `tools/hardware/test-host-regression-runner.py`, which tests the runner itself)
  ([tools/hardware/README.md](../../tools/hardware/README.md)).

## Making device tests safe

- **Bounded.** Every trial has a deadline, a cleanup step and a stated rollback, and runs once; no
  automatic retry ([tools/gpu-compat/README.md](../../tools/gpu-compat/README.md)).
- **Serialized.** A host-side guard allows one trial at a time, and a trial that was interrupted
  blocks the next one until someone records what happened
  ([`tools/hardware/device-trial-guard.py`](../../tools/hardware/device-trial-guard.py)).
- **Before/after health.** Capture the same health checks before and after (boot ID, kernel log,
  network, modem, services) ([evidence/gnss-20261022](../../evidence/gnss-20261022/README.md)).
- **Dead-man switch for anything that can cut your connection.** Before a test that takes Wi-Fi or
  USB away, arm a timer that restores the links unless you cancel it. Ours runs detached from the
  SSH session and uses wall-clock time so it also fires after a suspend:
  `s22-net-deadman arm SECONDS` ([`tools/hardware/network/s22-net-deadman.sh`](../../tools/hardware/network/s22-net-deadman.sh),
  [evidence/network-independence-20261022](../../evidence/network-independence-20261022/README.md)).
- **Kill switches.** Long-running services that change behaviour have a one-command off:
  `s22-netpolicy off`, `s22-autosleep off` ([STATUS.md](../../STATUS.md)).
- **Watch for self-inflicted bugs.** `pkill -f <pattern>` run over `ssh sh -c` killed its own shell,
  because the remote command line contained the pattern. Stop processes by exact PID
  ([evidence/modem-20261006](../../evidence/modem-20261006/README.md)).

## How we prompted

The mission files in `docs/` are real prompts we gave to coordinator agents. They have the same
shape, and it worked:

1. **Target and boundaries first:** exact device, what must be preserved, what must never be touched.
2. **"Reconcile reality" before acting:** read the newest status, check the live phone, and treat
   older documents as history ([S22_LUNA_DRIVER_COMPLETION_MISSION.md](../S22_LUNA_DRIVER_COMPLETION_MISSION.md#2-first-pass-reconcile-reality)).
3. **Workstreams** with files, the current blocker, and what counts as done.
4. **Honest status levels:** `source_checked`, `host_tested`, `built`, `device_tested`,
   `physically_accepted`, `reboot_verified`, `sustained_verified`; a new kernel or image does not
   inherit old acceptance (same file, section 2).
5. **Ask for implementation, not another report.** Workers were told to make concrete changes with
   tests, and to queue anything that needs owner approval while continuing other work (same file,
   section 1).

For contributors working without the phone, [CONTRIBUTING.md](../../CONTRIBUTING.md#working-with-your-agent-on-this-repo)
describes the proposal format: analysis with sources, the change with host tests, and a device test
plan with pass criteria and rollback.
