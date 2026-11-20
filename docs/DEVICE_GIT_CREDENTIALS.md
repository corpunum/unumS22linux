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

## Publish flow
1. Device agent commits on `device/<name>` with `Scope:` and `Why:` trailers, tests pass, health ok.
2. `device-git push` (tool, not raw git) pushes `device/<name>` (or a dated name after a rebase) with the repo key.
3. Rig fetches `device/*` (as today, plus GitHub as a second source), reviews each commit:
   `Scope: base` -> cherry-pick onto a branch, open a PR to `main` (CI plus review); `Scope: device` -> stays.
4. After main releases a new base the device moves `base` and rebases locally as in the original doc.

## Repos and keys (public keys, safe to publish)
| device | repo | visibility | public key |
|---|---|---|---|
| S22 | corpunum/openunum | private | `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAID8EPjwTwlSKQVdULTn4NniY1H9Nd0VgybA9KDXjRr6z deploy-s22-openunum` |
| S22 | corpunum/unumS22linux | public | `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICy9GZFJBU+wpGJsdfIWqiVPrByZCPj4lAppzQjaNEjA deploy-s22-unumS22linux` |
| Ally | corpunum/openunum | private | `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKql/jGOpAhW9MYTxUCQuI8RuNChXybjBDRWCX0KaneZ deploy-ally-openunum` |

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
