# Native terminal successor and sealing canary

Date: 2026-09-30, Australia/Brisbane. A bounded, user-operated defeat/terminal
boundary verification. Raw observations remain private. This receipt does not
qualify a fresh-start Full Run, every scene, a new model, or policy quality.

## Identity and collection

The installed Mod/Workbench still uses accepted source
`de23ce167fddc5bfe149cd29f2773299b261845d`, tree
`5ff4b641ac5c6f072203c9b782c8877c2adae86d`. The newer PR119 source was not
installed for this capture.

- Game: `v0.111.0/41cef1ea`, macOS arm64.
- Mod DLL SHA-256: `188d080e15712ddadf461c5aa883534464b3b393eb37dfb6f3ea1e7dbcbdd9df`.
- MVID: `23880303-49fd-4f11-be2d-1ad7c5ff471f`.
- Connector / Annotator source: `330fc960c1a42f70988e976465c4baaa0674c4c7`.
- Fixed CollectionTool release: `a4ae13461633f51cfa4cebc85ddec12b630db61c6dae4f459a05140ce4d89af9`.
- Capture profile: `human-full-run-read-rich-v4`, digest
  `1ed42e6709aaa46b9d38f2f72ce778e731d17aee3f06bf0153ea369b936ff459`.

The user was asked to record natural play through victory/defeat, leave the
result page open, and not manually close the recorder or press Continue. The
user then reported that the terminal page had appeared. The recording began
at `2026-09-30T02:11:37.879215Z` and closed at `02:16:56.083028Z`.
Its journal begins with `run_observed_in_progress`, not a native new-run start.
Human origin is operator attestation, not a file-verifier conclusion.

## What the records prove

The final accepted action is #168, `EndPlayerTurnAction`. Semantic trace entries
1006–1012 retain its acceptance, execution boundary, start/finish, exact native
Commit, then `proved_native_commit_then_owner_boundary`. The boundary is
`native_decision_owner_ready`, domain `game_over`, native owner `NGameOverScreen`,
mechanism `NGameOverScreen.AnimateIn->NGameOverContinueButton.OnEnable.postfix`.
State and Reads are complete and the proof has no blockers.

The action witness, execution-state reference and successor reference match
canonical transition #168 (`end_turn`). Its successor frame has
`interaction_kind=game_over`; the referenced bytes hash to
`b8e6a1195d96796291bfde36fda35befe1625a996360e4b7540bc898798fbc80`.
The final row-2 input carrier also matches that action.

| Journal sequence | Observed event |
|---|---|
| 476 | `run_ended_native`, `RunManager.OnEnded(isVictory=false)` |
| 477 | Final canonical `end_turn` persisted |
| 478 | Recording Close requested |
| 479 | Session flushed and closed |

There is **no terminal unknown** in this sequence. The direct evidence is that
the real owner-ready successor was persisted before Close. Close metadata does
not carry a separate automatic/caller-reason field: attribution to automatic
sealing also uses the exact source path
`CompleteDecisionOwnerReady -> ExecuteTerminalAutoSeal(automatic:true)` and the
operator steps. It is not independently proved by a Close timestamp or flag.
Earlier manual-close unknowns remain valid historical evidence.

## Distinct populations and executed verification

| Population | Result |
|---|---|
| Native accepted actions / canonical-transition-evidence-3 | 168 / 168 |
| Compatible decision-record-2 projections in run stream | 57 valid, 0 invalid |
| Explicit current-decision projection omissions | 111; retained, not relabelled as canonical loss |
| Invalidations | 11 `diagnostic` / `human_action_native_type_mismatch`; no `decision_failure` |
| Row-2 input witnesses | 143: 134 accepted, 1 rejected/cancelled, 8 not mapped |
| Recorded Reads | 557 successful, 0 failed |

The fixed producer `audit` exited 0 with `pass`, 57 valid records, 0 invalid,
11 invalidations and no errors. The independent reviewer verified the terminal
references, frame hash, exact running identity, Close digest and typed input
stream. These checks do not turn the eight unmapped inputs into labels.

After verifying the pinned CollectionTool inventory, `pack-session` produced
a new private bundle without modifying the original session. Accepted-source
`verify_human_session_bundle` returned `pass`, zero findings, export count 168
at `2026-09-30T02:19:40.880357Z`. Bundle content ID:
`81791a45dea839ee326b5094fc5c09175c9f70c104e07c5b1f8183bc527bd3ca`.
The Close receipt's 143-input stream SHA-256 is
`8a4ce34442ebca037bb4f2732671ee7427a1df2d4396a411d17e51ec7f0400a0`.

No game action was automated during this audit. No private data was uploaded,
no training/dev purpose was assigned, and no model training or installation ran.
The observed mapping omissions still require their own bounded assessment;
neither this terminal result nor PR119 CI implies complete Stage1a acceptance.

## Separate source integration

PR119 exact head `dd5a44d929091b7de96c349774d38fb8012cc56b` passed
[full run 36655382514/1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36655382514):
plan, Linux, Windows and portable succeeded; docs was unselected. Independent
source/assembly review had no remaining blocker. The older Windows training
exception's exact cause remains unknown; a passing new run is not an explanation.

Normal merge `f6e7811e70decf34abe4dac88140fef703ed43f1` has parents
`a887e4f1c75eb48066d00c49b279abee51abd30c` and the accepted PR head. Its tree
`c59c49a0164f9bced6f07df8eaa06532458d7853` is unchanged from that head.
The integration check is
[36659363360](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36659363360);
attempt 1 completed successfully. Its planner selected `reuse` with reason
`verified_execution_same_tree`, referencing full run 36655382514. Fresh
guards/docs and portable succeeded; Linux/Windows were not re-executed.
Integration did not update
the running installation or turn this old-installed-version capture into new
Runtime/model qualification. Main and Workshop were not changed.
