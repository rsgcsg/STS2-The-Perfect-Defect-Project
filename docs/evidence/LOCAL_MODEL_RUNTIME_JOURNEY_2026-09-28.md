# Local model Runtime journey and status recovery, 2026-09-28

This receipt separates an actual local exercise from the later source fix. It
does not establish full-game completion, Human origin for Agent actions or
strategy quality. Raw observations, recordings, weights and private configuration
stay local; the identifiers below locate their independently verified summaries.

## Exact active combination

[PR #74](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/74)
merged normally as `6c2f4166e52dfad08a78123ac858a5b63900d9a1`. Its application
candidate `d81e69569330540d9e0067b51e674e3989cd14d3` and merge share tree
`68c32d3e2c9ba753b3b25104dbc3e2a431d14617`.
[CI 36362857063](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36362857063)
attempt 1 completed successfully with Python scope on both OSes and portable.
The tested PR merge was `f8776fdb8f639778d1adbba4a850c15bb522a634` with that same
tree. [Develop CI 36364230857](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36364230857)
reused its verified execution receipt with fresh guards; this is not a new full.

The local owner stopped the prior Workbench and activated a clean detached d81e
checkout in its own locked Python 3.11 environment. Instance
`cc261d6a7dbfa8819693295c4aa5b081` served the existing profile. Before/after
activation retained the same profile hash, 353 manifest IDs, 1,111 files and
2,324,119,497 total bytes. SQLite stat metadata changed; these measurements do
not prove a hash match for every stored byte. Old source/environment were retained.

The private Policy Runtime rc.10 archive was explicitly installed, then loaded:

| Identity | Value |
| --- | --- |
| Runtime source revision | `60d97f7235a4cf8ce4618362c0eca6802eca0e6b` |
| Component tree | `673bff7bf3435c6e51561569e5f17f0fa18b111e` |
| Source digest | `31b9c5c3bf1d9d69570475bc22e37735d6dd7781385d823f461f29c77b48fb41` |
| Archive SHA-256 | `16e825fbd74b17c6ca065619bc9a272f0bb0577d6c5277570b377de50fbaa954` |
| Runtime code SHA-256 | `74334ca24c90194b4c97b9b9ffaacb2fdc93f3ef53d879998f2f9c11b6c05600` |
| Exported policy artifact SHA-256 | `a2d8d0511acdc382e0a1cbe43d509e40d63582408ceeef3328ccb65390c4052b` |
| Policy manifest SHA-256 | `f45b4be9225ce28dd28e4706a07c8d7e868252755f4d3452803e7a0a90a4a154` |

This was a private candidate install, not package publication. Legacy tracked
rc.6 consumer configuration was unchanged. Game Mod rc.17 / Annotator rc.13
were not reinstalled or restarted. Connector reported game instance
`393e355582904af986db96dcf1efde88`, game 0.111.0 / 41cef1ea. The game Workbench
bridge resolved the new local application instance. No team upload, Gold change
or additional model training occurred.

## Explicit browser journey and sealed runs

The root used Safari to register the existing tiny B model, explicitly load it,
choose modes and stop. Registration alone did not start the model. Model artifact
ID `0ad03dfd233fbb37404ce9cf9e0790b2f651f7ee3a4683799ed9f1111df97819`
is distinct from the exported policy hash above. Its [previously recorded training](LOCAL_MODEL_REGISTRATION_2026-09-28.md)
was fixed at three steps with dev Top-1=0/5; this exercise did not tune it.

| Exercise | Exact run and result |
| --- | --- |
| Load → Shadow → Human → Stop | `run-dcf7f25a-ce13-4da6-bbd2-14f890d85e85`; seven finite candidate scores, one policy call, zero submissions, controller released; stopped/unloaded, six sealed events. Evidence content `903216582aae3dff389bc31581a7b553d72c41ddc971140f1b18302f9dc25f1c`; evaluation `f363951ff82b8a58bc3786c6ecd1fb9c5efcb6d6bb1f7d472b866d4653575c02`. |
| New load → one bounded Auto → automatic Human → explicit Stop | `run-5df8e209-d674-4221-b6c3-f0ada4d694da`; 16 calls/submissions, 16 Connector-reported native deliveries and 16 later observed snapshots. Submission limit reached at 9,659 ms; Human/released, then stopped/unloaded. 86 sealed events. Evidence content `65ca7fa1dcecffef4aafb3106a05e992bbb72a196f2aae7b4211ef9f6b8afe29`; evaluation `f5b29effb6540f929adf203c38d7c9959c0b908fbbc5ed97c800d4f995f289e9`. |

Root and a separate read-only reviewer ran the existing agent-run verifier on
both sealed runs: pass, no findings. Each decision's finite scores, ordered
candidate IDs and digest matched its complete menu. Adapter startup attestation
and exact Runtime/model identities were present. Later observed snapshots are
not relabelled as Human canonical causal successors.

Auto selected `end_turn`, then alternated eight `open_combat_draw_pile` and seven
`return_native_information` actions. This repetitive navigation is preserved;
it is not evidence of effective battle progress. The 16-submission / 32-call /
60-second limits are bounded experiment settings, not full-game limits.
Evaluation retains `game_outcome=not_measured`, scientific and training admission
`not_claimed`.

An additional typed SDK GET confirmed the same live game instance was on
`combat_draw_pile`, menu revision 16, with only `return_native_information`.
The OS window tool continued returning an older combat image and returned
`noWindowsAvailable` when asked to activate the title bar. Therefore the final
rendered native effect was not independently confirmed visually. A stale capture
is possible, but not asserted as the proven cause. No game action was repeated
to manufacture visual success, and no fresh Human recording was claimed.

## Two observed application defects and owning corrections

1. An accepted pending model operation refreshed once but did not subsequently
   update the page. The UI fix performs bounded read-only status GETs, refreshes
   meaningful state changes, and displays changing budget counters. It never
   dispatches a command automatically; leaving the page/instance invalidates
   old replies. A manual read-only refresh remains available.
2. A concurrent GET could fail after Stop closed the Runtime port but before
   Workbench detached its client, leaving a false current observation error. A
   late successful GET could overwrite the authoritative stopped snapshot.
   The backend fix fences results by client, intent and pending Stop, and clears
   only the transient observation error after confirmed Stop. Runtime history,
   taint and uncertain command outcomes remain intact.

The Auto receipt contains that original observation error; it was not edited.
The error is not evidence of a proved environment identity drift. Separate
barrier regressions reproduced both backend races on old production code.
The UI regressions likewise failed on the old implementation. Candidate checks
and independent source review live in [PR #75](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/75).
These later fixes were not active during the above runs and require their own
exact-head CI and application verification. No Platform production bytes,
model weights, manifests or native legality changed in the source fix.
