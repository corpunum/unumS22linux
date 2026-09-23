# S22 Luna execution correction — 2026-09-23

Read with `docs/S22_LUNA_DRIVER_COMPLETION_MISSION.md`. This correction takes precedence only for model-verification procedure, worker write scope, publication of unmerged work, and the immediate execution order. Preserve every device-safety, privacy, recovery, authorization and architecture boundary in the original mission. The target remains SM-S901B/DS r0s, not g0s.

## Why this correction exists

The owner's latest screenshot reports two local commits (`5928e980...` and `295b869`), host tests, no new kernel build or live driver trial, and no push because Luna identity/review could not be verified. It also reports a dirty original checkout ahead 44/behind 56, work in `/tmp/s22-luna-coordinator-20260923`, and a nearly full phone root overlay. Those are reported observations, not newly reproduced results.

At the remote check preceding this correction, `master` remained at `790cb0ab4af121e8ed79346723b845124eb3ece4`, with no published work branch. The screenshot's implementation commit could not be fetched remotely. Preserve that work and make the source reviewable; do not recreate it from a summary.

## 1. Model selection: evidence, not self-description

Use the requested `gpt-6-luna` coordinator and Luna workers, with the owner's Max setting when supported by the actual installed client/model. Inspect supported reasoning values rather than assuming that a UI label maps to a particular string. Do not switch to Sol, Spark, another family, or a local model.

A generated sentence such as "I am GPT-5" or a worker's generic "GPT-6" response is not sufficient evidence of the configured execution model. Do not use such sentences as a project-wide stop condition or ask agents repeatedly to identify themselves.

Do one bounded setup pass, targeting approximately five minutes rather than an open-ended identity investigation:

- Check the installed CLI version, effective model/provider configuration and available session/turn metadata. In the interactive client, `/status` is the native model/status check; `/model` is the native selection mechanism. Preserve the existing account and approved provider route. Do not print credentials or publish raw session logs.
- Configure actual spawning, not just a worker prompt mentioning Luna. Current Codex supports `agents.default_subagent_model`, `agents.default_subagent_reasoning_effort`, and `agents.max_concurrent_threads_per_session`; custom agent files can override model/effort. Check the installed schema and effective per-worker overrides.
- Classify evidence honestly: `runtime_reported` when execution metadata names the model; `explicitly_configured` when a supported explicit Luna selection is accepted but runtime identity is not exposed; `unresolved` when there is only a prose request, conflicting configuration or a known fallback. Configuration is not independent backend attestation. Do not mislabel it as such.
- An accepted explicit Luna configuration, with no known overriding selection or fallback, is sufficient to proceed with host work and independent review while recording the visibility limitation. A missing model field or vendor session UUID is not a reason to discard useful work. Use actual process/task handles where available; distinguish locally assigned labels from runtime IDs.
- If native spawning cannot honor Luna selection, inspect the installed `codex exec --help` and use separate explicitly model-selected Luna CLI workers in isolated worktrees, within existing permissions. Capture bounded, sanitized execution receipts. Do not guess flags, start extra paid provider routes, or bypass restrictions. If neither route works, report the exact error once and continue safe coordinator work; do not knowingly substitute another model.

If execution metadata really identifies a non-Luna coordinator, use a supported model change/handoff while preserving all work. A textual self-description alone does not establish that mismatch. Independent review means a separate actual review execution of the exact patch, not an author calling its own check independent.

## 2. Workers must implement, not merely scout

Start up to four Luna implementation workers, using the already assigned lanes where possible. Check existing jobs before duplicating them. Give each its own worktree, file scope and explicit permission to edit and run host tests within that scope. Use a supported workspace-write profile where the parent environment permits it; do not use read-only explorer roles for implementation. Reviewers may remain read-only. Do not broaden global permissions or change unrelated project configuration.

The four initial deliverables are:

1. **Deployment/build:** implement explicit runtime checks in both host and embedded remote deployment code, optimization-mode negative tests, and integrated AVB verification. Do not stop at identifying assertions.
2. **Bluetooth:** review the exact HCI lifecycle repair, add meaningful executable regression coverage where supported, inspect module/build compatibility, and prepare or verify the isolated HCI candidate and smallest authorized runtime test. Do not rebuild unchanged artifacts needlessly or call source-string tests runtime proof.
3. **Audio:** inspect and reuse the new DMA classifier; implement synchronized capture and cleanup/classification improvements that can distinguish where stream progress stops. Return code and tests, not another list of mixer controls.
4. **NPU:** inspect and retain the new readiness gate, but move on to actual request/session ownership and boot-unwind repair with targeted race/error coverage. Keep unsafe BOOTUP disabled. Another refusal gate is not a substitute for fixing ownership.

Each worker returns changed paths, patch/commit, exact tests and outcomes, remaining uncertainty and any specific blocker. A read-only report may inform the implementation but does not satisfy an implementation assignment. If the worker cannot write, identify the permission/profile problem instead of repeatedly issuing the same assignment.

Run an independent Luna review in the next available slot. Limit concurrency to real resources; serialize heavy ThinLTO builds. Only the coordinator integrates changes, pushes shared refs or accesses the live phone. Workers get sanitized captured evidence and never run competing device experiments.

## 3. Preserve work and separate publication from promotion

First inspect current worktrees, active jobs, local refs and uncommitted changes. Anchor the existing work with a durable named branch; preserve any uncommitted/untracked material privately outside a disposable directory when needed. Do not move an active worker's tree, hard-reset, force-push, indiscriminately stash, delete or overwrite the original checkout.

Inspect merge bases and patch equivalence behind the reported ahead/behind counts. Divergence alone does not say which changes are missing. Determine whether relevant local-only changes explain the running kernel or tools before selecting a build baseline. Do not blindly merge, rebase or cherry-pick all 44 commits, and do not accidentally publish private history. This investigation must not discard the two new commits or become a reason to hide their source.

Reuse the isolated implementation branch when its ancestry and contents are appropriate. Otherwise preserve it and construct a clean review branch from a reconciled public baseline with only the intended patches. Suggested new remote branch: `s22/luna-driver-completion-20260923`; check whether it exists before choosing a name. Fetch newer documentation without resetting implementation work.

Under the owner's existing source commit/push authorization, publish sanitized code, tests and concise receipts to that **unmerged review branch**. Check the proposed diff and newly reachable history for credentials, private logs, firmware, model bytes and generated images; do not use broad add/push-all commands. Run applicable host checks and report failures honestly. Do not alter CI configuration merely to obtain a green badge or permit public jobs to reach the phone.

Pending independent Luna review does NOT prohibit publishing work marked `WIP / not device-tested`. Publishing source for review, merging it into the trusted baseline, and deploying it are different actions. Verify the remote branch SHA after pushing. Keep high-risk changes unmerged and undeployed until their technical review and hardware gates are satisfied. Do not represent an unreviewed branch as accepted or ready to flash.

## 4. Address the real operational constraints

The reported nearly full root overlay and unproven Tailscale peer reachability are actionable concerns, not reasons to stop host-side development.

The coordinator should collect bounded, read-only mount, free-byte/inode and directory-usage evidence for the actual native and Arch namespaces, streaming results to the host rather than filling the phone with more logs. Determine whether the constraint is an overlay upper layer, tmpfs/RAM, persistent filesystem or a different namespace. Do not confuse host free space with phone space, or capacity of one mount with another.

Before any package installation or image staging, establish headroom on the actual destination. Identify only demonstrably disposable caches or reproducible staging files for an authorized, reversible cleanup/relocation plan. Preserve active firmware roots, working rootfs, package database, rollback images and private identity data. Never apply blanket cache deletion or remount/resize a filesystem as a guess. Continue builds and host tests while this is unresolved.

Check independent rescue reachability from the actual recovery host, not merely whether a Tailscale process exists. Keep the existing partition/readback/rollback, physical-rescue and experiment-authorization gates. A staged rollback file cannot rescue a kernel that fails before remote access. No raw HCI retry on the known-broken kernel, unsafe NPU BOOTUP, blind ABOX unbind, speaker gain experiment, or unattended risky reboot is authorized by this correction.

Once the specific gates are genuinely met, run the already authorized bounded device experiment through the coordinator rather than repeatedly re-planning it. Otherwise identify the one precise owner action needed and continue independent work. Do not treat every host patch or source publication as if it were a flash operation.

## 5. Required next checkpoint

Continue implementation after initial reconciliation. The next checkpoint must contain an actual remote review-branch SHA, available diffs for the two preserved commits, workers that really ran and their model-evidence level, code implemented, tests actually executed, and the exact pending review/deployment conditions. If a push or launch fails, provide the concrete error and preserved local ref rather than claiming it succeeded.

For the device, distinguish no change from no regression proven: report current baseline checks, storage/rescue findings, and any newly executed hardware test with its receipt. Host tests and documentation changes must not be presented as a newly working driver.

Keep the original full acceptance matrix and remaining cellular, camera, GPU, power, input and userspace workstreams. This correction changes how work proceeds; it does not reduce the goal of a fully usable standalone S22.

## Documentation consulted

Current official documentation, checked 2026-09-23; the installed client's actual supported interface still governs:

- https://developers.openai.com/codex/subagents
- https://developers.openai.com/codex/config-reference
- https://developers.openai.com/codex/cli/reference
