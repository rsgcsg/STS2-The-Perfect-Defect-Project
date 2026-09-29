# Managed map-boundary save/load probe, 2026-09-30

This is exact local Managed-candidate evidence, not shipped-game parity, general
checkpoint support or a public restore service. No user's game process, profile,
original recording or model was changed.

## Owning boundary

The native candidate already exposes `write_continue_save` and `load_save`.
Its MapRoom save serializes the native run. Its non-map save path reconstructs
a map boundary with manual room/coordinate changes; that does not prove rollback
of HP, RNG or pending continuations. This probe therefore refuses a non-map or
non-quiescent starting point and adds no model-visible save action.

The new Host command reuses the exact candidate audit/start/stop owner, current
public projection and `canonical-player-decision-1` normalization. A seeded
reference writes one private temporary save. Two sequential fresh processes
load independent copies, compare the complete public Snapshot/action catalog,
and take the same semantic map choice via each process's current binding.
No original native reference is replayed in a new process. Save hash/size drift,
unknown responses, public-state differences or cleanup failures cannot pass.

Native has no full-page observe opcode after saving. The probe checks the
MapRoom acknowledgement, a fresh map read and run-position/quiescence only at
that point; it does not pretend that a cached player object is re-observed.
Full public projections are compared across subsequent fresh loads. Existing
canonical normalization is reused, not an arbitrary list of ignored raw fields.

## Exact source and local execution

- Workspace at execution: `ffad673f3443cf0c6806d567905314fcb23cb018`, clean.
- Host version: `1.1.0-rc.21`; source `324aeb99239a7f37bc66ca427f990578c9adf66a`.
- Component tree: `d74d3b4a08cf9d4e67e5f181e72194d77d2b9595`.
- Source digest: `08458e528960551dec376ef8bb9eeef0ec28f1a2b56daba8145ec617705de858`.
- Candidate artifact: `dd726fba38f4fc097a57e9dd4a5fe7d220bd94ea3527e132be31a31c95963d93`.
- Candidate MVID: `145c95e9-ace0-42b5-bb46-3fef292ac645`.
- Candidate patch: `bf3ac3d1aadee5ce556687745d2d64d37d0eea47e2b67904ca7c97b080d8b89e`.
- STS2 assembly: `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`.
- Seed `M2H0ST20260929A`, Defect A0; generated at `2026-09-29T19:55:55.714Z`.

Actual command, with the private prepared-candidate path omitted:

```text
node components/host-runtime/tools/managed-exact.mjs save-roundtrip --candidate <exact-private-prepared-candidate>
```

Exit 0; status `managed_map_save_roundtrip_pass`. All three native processes
returned exit 0 and the probe removed its own temporary saves. The private save
was 37,413 bytes, SHA-256
`44a85376febc272f1f1f40744de6198913bae4dc59a89b21fa8bd2f4ee9ba42d`.
It is not committed or distributed.

| Role | Runtime instance | PID | Public map and one next-state comparison |
|---|---|---:|---|
| Seeded reference | `ed30260998e943ab8b54dd0956924fe5` | 31830 | Reference |
| Restore 1 | `e410b375a1584a57b4b70e6f69e37921` | 31890 | Equal |
| Restore 2 | `3936e732ebd249d89cf07ffd60d166c9` | 31902 | Equal |

Shared map digest:
`ce6673c1148c3a1759ed3e41f0c27d49cb7db8027439781a2a12ce2a74732155`.
Shared next public-decision digest:
`942d1c6354e1372edcd4d11ad57d51cc4fe24dfdbb677dc3a3741f5678fffff8`.
These names describe a comparison of returned observations, not proof of a
new causal successor/Commit contract. Hidden RNG equivalence, all Read payloads,
long-horizon equivalence and arbitrary combat/selector saves remain unknown.

## Source/package checks

- Focused probe plus existing semantic/repeatability/scenario tests: 49 pass.
  New fixture intentionally exercises synthetic map-to-map, not native combat.
- `npm --prefix components/host-runtime run check`: exit 0; Node 264 pass,
  four existing proprietary exact-candidate tests skipped; Python 17 pass;
  syntax, links, repository boundary and temporary installed-package smoke pass.
- Temporary packed package: 74 files, 209,552 bytes; installed smoke identified
  Host rc.21 and Connector SDK 1.1.0-rc.1 outside the workspace. This is not a
  production installation or a public package release.
- `npm run check:identity`, `npm run check:bom`, `git diff --check`,
  `npm run project:closeout`: exit 0. The first metadata script used the wrong
  report key (`host_runtime` instead of `host-runtime`) and wrote nothing;
  corrected owner-key lookup produced the identity above.
- Clean committed `check:plan` against `37c96d0b` selects full. Hosted CI must
  execute for the final PR; earlier source results do not qualify this candidate.

BOM updates only current Host source fields. Old package/consumer pins and
installed/historical identities stay unchanged. No model inference, real-user
gameplay, Steam upload, save library UI or production service change is claimed.
