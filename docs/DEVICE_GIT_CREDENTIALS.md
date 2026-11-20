# Device git: credentials and the push path (S22 and Ally)

Status: design plus keys generated on the devices (2026-11-21). Builds on `device-git-self-improve-20261013.md`
(openunum-briefs), whose rule 4 ("never push anywhere") changes here to "push only `device/*` over a per-repo deploy key".
Hub item: U-551cc9ec.

## Goal
The agent on each device can publish its own `device/<name>` commits to GitHub without the rig. Nothing it does can
change `main`, rewrite history, or touch another repo. Changes reach `main` only through a reviewed PR.

## Credential: one deploy key per repo, generated on the device
- No PAT: fine-grained PATs cannot be created through the API.
- Per device, per repo: `ssh-keygen -t ed25519 -N ""`. The private key never leaves the device. Only the `.pub` goes to
  the rig, which adds it with `gh repo deploy-key add <pub> --repo corpunum/<repo> --title deploy-<device>-<repo> --allow-write`.
- GitHub refuses one public key on two repos and a deploy key is bound to one repo, so a key per (device, repo) is the
  natural unit. Compromise of one key reaches one repo only, and is revoked with `gh repo deploy-key delete`.
- Private key location (mode 600, dir 700, owner = the agent user):
  - S22: `/var/lib/unum-git/deploy_<repo>` inside the chroot (owner uid 1500, `unumagent`, whose passwd home is `/root`
    and shell nologin, so the key is outside any home). Git uses it through
    `GIT_SSH_COMMAND="ssh -i /var/lib/unum-git/deploy_<repo> -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"`.
  - Ally: `~/.config/unum-git/deploy_<repo>`, same git setting.
- Pin github.com's host key in a device `known_hosts` (fetched once by the rig, not trusted on first use by the agent).
- The agent never prints or reads the private key; the device-git tool is the only code that sets `GIT_SSH_COMMAND`.

## Limiting a deploy key to `device/*` (GitHub cannot do this per key)
A write deploy key can push to any branch, so the limit is enforced by repository rulesets, which deploy keys cannot
bypass unless listed as a bypass actor (leave the bypass list empty):
1. Ruleset "main via PR": target default branch; require a pull request (1 approval, the owner or the orchestrator
   review), block force pushes, block deletion.
2. Ruleset "only device/*": target `~ALL` branches excluding `refs/heads/device/**`; restrict creations, updates and
   deletions. Add the same restriction for tags (target all tags).
3. Ruleset "device history": on `device/**`, block force pushes and deletion, so the audit trail stays intact. The agent
   rebases locally onto a new `base` by pushing a fresh branch name `device/<name>-<date>` (never a force push).
Verification the rig runs once per repo: push `device/test-<ts>` (must work), push to `main` and to `foo/x` (both
refused), force push to an existing `device/*` (refused), then delete the test branch with the rig's own credentials.

## Variants for the rig's own pushes (conflict found 2026-11-21)
The "only device/*" ruleset above also blocks the rig's direct pushes to `main` (unumship, the main-branch policy),
because a ruleset targets refs and actors, not the credential type. Two ways out:

**A. Bypass for the rig, same repo.** Ruleset 2 (restrict create/update/delete outside `device/**`) and the main PR rule
get a bypass list: the repository-admin role (the owner account the rig pushes with) and the unumship GitHub App,
mode "always". Deploy keys are not admins and not that app, so they stay blocked; the bypass list is the only thing
that must stay short and reviewed.
- Pros: one repo, no mirror, device branches are visible next to main, normal PRs from `device/*` to `main`, nothing
  new to sync.
- Cons: the safety rests on the bypass list; anyone added to it (or a token of the admin account) removes the fence.
  Rig pushes to main stay unreviewed, as today. Rulesets must be verified per repo, including after org or role
  changes.

**B. Separate devices repo.** The device keys get write on `corpunum/openunum-devices` only; no ruleset is needed on
`openunum` and the rig's rules there are untouched. The rig pulls `device/*` from it and lands changes on `openunum`.
- Pros: strongest isolation: a leaked key cannot touch `openunum` at all, whatever the rulesets say; no change to the
  main-branch policy or unumship.
- Cons: a repo that is a fork of your own repo cannot be created inside the same account, so it is a plain mirror
  (shared history, no GitHub "PR into openunum": the rig cherry-picks and opens the PR from its own branch); one more
  repo to keep in sync (the rig pushes new `base` into it); another private repo to protect; `unumS22linux` is public
  and small, so the same trick has little value there (use variant A for it).

**Recommendation:** A for `unumS22linux` (public, low risk) and B for `openunum` (private, core, and the one the rig
pushes to most). **Decided 2026-11-21 (corpunum-135):** B for `openunum` (private `corpunum/openunum-devices`, device keys only there, no rulesets on `openunum`); unumS22linux changed to B as well by corpunum-137 (11-21): on a user account a write deploy key counts as repo admin, so with an admin bypass it pushed main, foo/x, tags and force pushes; without a bypass it pushed only device/*. The variant A fence does not work, and no rulesets exist anywhere. Repos: `corpunum/openunum-devices` and `corpunum/unumS22linux-devices` (private, Actions off, branch `base` only). The orchestrator does the gh steps. Re-seed and clone wait for its word. If B is chosen
the keys already generated stay valid: they are added to the devices repo instead.

## Publish flow
1. Device agent commits on `device/<name>` with `Scope:` and `Why:` trailers, tests pass, health ok.
2. `device-git push` (tool, not raw git) pushes `device/<name>` (or a dated name after a rebase) with the repo key.
3. Rig fetches `device/*` (as today, plus GitHub as a second source), reviews each commit:
   `Scope: base` -> cherry-pick onto a branch, open a PR to `main` (CI plus review); `Scope: device` -> stays.
4. After main releases a new base the device moves `base` and rebases locally as in the original doc.

## Repos and keys (public keys, safe to publish)
| device | repo | visibility | public key |
|---|---|---|---|
| S22 | corpunum/openunum-devices (was openunum) | private | `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAID8EPjwTwlSKQVdULTn4NniY1H9Nd0VgybA9KDXjRr6z deploy-s22-openunum` |
| S22 | corpunum/unumS22linux-devices (was unumS22linux) | private | `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICy9GZFJBU+wpGJsdfIWqiVPrByZCPj4lAppzQjaNEjA deploy-s22-unumS22linux` |
| Ally | corpunum/openunum-devices (was openunum) | private | `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKql/jGOpAhW9MYTxUCQuI8RuNChXybjBDRWCX0KaneZ deploy-ally-openunum` |

The repo `unumS22linux` is public: the S22 pushes there only `device/s22*` branches, and the repo's own rule
(no hostnames, IPs or PII; gitleaks before every push) applies to the device tool too (run gitleaks in the push step).
The Ally has no unumS22linux checkout, so it gets no key for it.

## Open items
- Ally `~/openunum` has no `.git` now (the 11-12 update moved the install to `openunum.backup.*`, the loss the original
  doc's "Base changes needed" warns about). Re-create `base` and `device/ally` before the Ally can push; until the
  updater carries `.git` over, treat every update as a re-seed.
- S22: `unumS22linux` is not yet cloned on the phone (original doc step 2); the key exists but the repo does not.
- `device-git` skill/tool (status, commit with Scope, revert, `diff base`, `push`) is still to build; it owns
  `GIT_SSH_COMMAND` and the gitleaks check.
- Rotation: regenerate yearly or on any suspicion; a rotation is delete key, generate, rig adds the new public key.

## Seeded and tested 2026-11-21 (U-551cc9ec, GO from corpunum-137)
- Ally: `~/openunum` re-seeded: `git init`, remote `devices` = openunum-devices, `base` = b980fd8e (the installed
  sourceCommit), `device/ally` = base, working tree clean (90 release-excluded files marked skip-worktree; `RELEASE_METADATA.json`,
  `VERSION`, `vendor/` in `.git/info/exclude`). Key `~/.config/unum-git/deploy_openunum`. A second remote `origin` is not set.
- S22: `/opt/openunum` got remote `devices` (openunum-devices); `/opt/unumS22linux` is a clone of unumS22linux-devices
  (`origin`, branch `device/s22` = `base` 67011ab9), owned by unumagent; keys in `/var/lib/unum-git` (uid 1500).
  Git must run as the agent user (`setpriv --reuid 1500 ...`): as root git reports "dubious ownership".
  Each repo has `core.sshCommand` pinning its own key; github.com's host key was accepted on first use (TOFU), not yet pinned.
- Negative tests from the devices (no rulesets, as decided): pushes of `device/test-*`, `foo/x-*`, `main` and a tag all
  SUCCEEDED from the Ally (openunum-devices) and from the S22 (both repos). Test refs were deleted afterwards; the remotes
  hold only `base` again. So "device/* only" is the `device-git` tool's rule, not a server fence. The blast radius of a
  leaked key is its one -devices repo, whose only protected content is a mirror.
- Still open: pin the GitHub host key; the S22's `/opt/openunum` `device/s22` history is not yet pushed to
  openunum-devices; the `device-git push` tool (enforces `device/*`, gitleaks); rig fetch of `device/*` from GitHub.
