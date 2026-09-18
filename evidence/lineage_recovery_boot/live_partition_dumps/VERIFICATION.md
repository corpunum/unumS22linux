# Live Partition Verification (pulled via root ADB in recovery, 2026-09-18)

Confirms exactly what was and wasn't touched by tonight's flash gate, verified against
the actual live block devices, not just tool-reported success.

| Partition | Live hash | Matches | Conclusion |
|---|---|---|---|
| boot | 0962dbdd67b748858189b46d464820ec7d1f3ea843cc7c3d033b69c40931b44e | stock FYI3 boot.img | UNTOUCHED |
| vendor_boot | 383b6f6789e655b070929914db5639e6d9bacc78823ada7bf553962b5b4888be | stock FYI3 vendor_boot.img | UNTOUCHED |
| dtbo | 2bd5faf44c0e4c59dea12e70bda4fa23b75df859ff7328a136bb15c72e2f7bd3 | stock FYI3 dtbo.img | UNTOUCHED |
| vbmeta | 5fe0620f8155e941fa729657d20f2b96b52051da669d23515ca92c986add67f4 | stock FYI3 vbmeta.img | UNTOUCHED |
| vbmeta_system | fb34e62e8bae5b280914da99d4cf2bb316f3645ab941c62b28f7909abad6cf54 | stock FYI3 vbmeta_system.img | UNTOUCHED |
| recovery | b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b5 | Lineage build 20260915 recovery.img | INTENTIONALLY REPLACED, persists correctly |
| misc | f229c619f741a4d8c3c1f8e2b8503b65c3b180cbf856b6cf2d33295eb20e9c28 | neither factory nor our BCB write | DRIFTED — expected, bootloader clears/updates the BCB command field after consuming it once; not a concern |

userdata, super, EFS/sec_efs, and radio partitions were NOT read or touched — out of
scope and/or explicitly off-limits per project safety rules.
