# Camera observer replay-fix review — 2026-09-27

Independent host-only review of observer commit
`7a1d74324816b8f70c4649c70bef2e1fd6df0836` in the isolated
`camera-observer-20260927` worktree.

The focused `test-camera-recovery-reboot.py` suite passed 24/24 under normal
Python, `-O`, and `PYTHONOPTIMIZE=1`. The exact change rejects completed
positive or negative observation receipts and malformed/unexpected history
before snapshots or transport; an empty observation directory with no final
receipt remains resumable. The request marker and reboot path are unchanged.

Concurrency limitation: two simultaneous read-only observers could both pass
the initial no-receipt scan and produce separate observation-window receipts.
The existing operation-lock API rejects while the global marker is pending,
so this review makes no claim that concurrent windows are serialized. No lock
or framework changes were requested or made.

No observer execution, phone access, or device mutation was performed.
