# Contributing

This is an experimental native-Linux port for one phone: the Galaxy S22 **SM-S901B/DS (r0s, Exynos 2200)**.
Only that model has been tested. Never flash anything from this repo onto another model, including the S22+ or Ultra.

## Working with your agent on this repo

Most open problems can be worked on **without the phone**. You (or your coding agent) read the evidence and the code,
then propose a fix. The owner runs it on the device and posts the measured result. Look for issues labelled
[`help wanted`](https://github.com/corpunum/unumS22linux/labels/help%20wanted) (and
[`good first issue`](https://github.com/corpunum/unumS22linux/labels/good%20first%20issue)). Each one lists:
- what was tried;
- the exact symptom;
- where the code lives;
- what a proposal should contain;
- how it will be verified.

### 1. Read the evidence first

| Where | What it holds |
|---|---|
| [README.md](README.md) | The current status table. |
| [STATUS.md](STATUS.md) | Newest sections first: what works, the measured numbers, and what is not verified. |
| [EXPERIMENTS.md](EXPERIMENTS.md) | The chronological log, failed attempts included. Search it before proposing something that may already have been tried. |
| `evidence/<topic>-<date>/README.md` | One folder per device session: the method, numbers, raw logs and a "Not verified" list. |
| `tools/hardware/**` | Device code. Most areas have their own notes, e.g. [GNSS.md](tools/hardware/gnss/GNSS.md), [CAMERA.md](tools/hardware/camera/CAMERA.md), the [phoned README](tools/hardware/phoned/README.md). |
| `docs/`, `docs/research/` | Older audits, including the stock-vendor analysis notes. |

Claims in this repo carry their evidence. Treat a claim with no log, measurement or file behind it as unverified,
and say so in your proposal.

**Vendor files.** Vendor binaries, firmware and the owner's backups are **not** in the repo, and must never be
posted. The analysis notes name files, symbols, offsets and hashes. If you need something from a vendor file,
write a script that takes the file path as an argument and prints only what you need. The owner runs it locally.

### 2. What a good proposal contains

Use the [proposal issue template](.github/ISSUE_TEMPLATE/agent-proposal.yml), or comment on the help-wanted issue:
- **Analysis.** The root cause or reasoning, with sources (kernel file and line, public docs, vendor symbol and offset). Mark guesses as hypotheses.
- **The change.** A patch or script, with host-side tests where possible. The host CI must keep passing:
  `python3 -I -B tools/hardware/test-host-regression-runner.py`.
- **A device test plan.** Exact commands, expected output, pass/fail criteria, duration and rollback. Say what the test touches (audio level, radio, modem, Wi-Fi, suspend, NPU).

Pull requests are welcome too. Keep them small and focused on one problem.

### 3. How fixes are verified

The owner, or the owner's own agent working under these rules:
1. reviews the proposal;
2. runs it on the phone over SSH;
3. records the result in a new `evidence/<topic>-<date>/` folder and in EXPERIMENTS.md;
4. replies in the issue with the numbers.

An issue is closed only when the device result meets its stated pass criteria. A build that compiles, a running
service or a model's opinion is not acceptance.

### 4. Safety rules (non-negotiable)

- **Partitions.** Never flash or write any partition from a proposal. Never touch EFS, IMEI, PIT, MISC, the bootloader or TrustZone.
  The modem's NV writes go only to a private EFS copy.
- **Kernel changes** are source patches. Deploying a kernel or a RECOVERY image is the owner's decision, and the
  hash-verified Lineage recovery rollback stays ready.
- **Telephony.** No calls or SMS to real numbers without the owner, and never emergency numbers.
- **Privacy.** Never publish phone numbers, IMEI, SIM IDs, IP or Tailscale addresses, network names, locations,
  secrets, or images of people. Evidence is redacted before it is committed.
- **Known crashers.** Some operations are known to crash or reboot the phone. Never propose them as-is:
  - a runtime rebind of `0.abox-tplg` (kernel panic);
  - NPU models with a `DSP_BINARY` section (DNC page fault, then a watchdog reboot);
  - ABOX graph PCM periods above ~960 frames (Calliope crash);
  - mmap on a calliope PCM (leaks the device until reboot, see #73).
- **Cutting network links.** A test that can take away the phone's network must arm the dead-man first:
  `s22-net-deadman arm SECONDS`.

## License

See [LICENSE](LICENSE) and [NOTICE](NOTICE).
