# Two local Human recording fragments, 2026-09-28

The owner attested two completed recordings. The supervisor inspected the exact
closed evidence through the installed Evidence V3 verifier; an independent
read-only audit was cross-checked against actual typed rows. This report contains
aggregate facts, not raw gameplay observations or a public copy of the recordings.

## Identity and performed operations

The installed and loaded Game Mod remains `0.2.0-rc.17`, artifact SHA-256
`c1c2dc7d5a673b523943c50a00d35884ef3f64af8bcf4a1bcf7c0c362cb3aa5a`, MVID
`204ec969-386e-42d9-ac23-a47f3aa2e10c`. The recorded game assembly SHA-256 is
`9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`, MVID
`57785517-0b16-42b9-8b36-bad6fb28384b` (v0.111.0, revision 41cef1ea).
Connector path-source is `ed40f0ebbfd59587eca0d5688f9c271062d85753`;
Annotator path-source is `393d4be605a0b3902cd29e83eb12a15fd8b9f138`.
The read-only owner observation at 2026-09-27T22:35:08Z matched the earlier loaded
artifact/process and reported bound status with no errors. No Mod was reinstalled.

The first fragment was already closed. The second was still recording after the
owner's completion message. A single existing native task request, bound to the
observed runtime/session IDs, asked the Recorder owner for Close. The owner
returned lifecycle `closed` and closeout `closed`. This application operation
neither ran a model nor delivered gameplay. A UI click attempt before that request
failed with `noWindowsAvailable`; it was not counted as a successful Close.

Both fragments were imported through the existing local Workbench API, fixed
collection tool and typed bundle verifier into the same artifact store. Original
recordings remain in place. No team upload, dataset publication or real-data
training was performed. Human origin is owner-attested, not machine-proved.

## Verified aggregate results

| Fragment | Accepted input labels | Rejected/cancelled input rows | Canonical decisions | Unresolved decisions | Diagnostic invalidations |
|---|---:|---:|---:|---:|---:|
| A | 5 | 0 | 4 | 1 | 0 |
| B | 26 | 2 | 19 | 1 | 1 |

Accepted input labels are A: 4 begin-card and 1 confirm-target; B: 19 begin-card,
6 confirm-target and 1 cancel-card. B's other two cancel-card attempts have
`same_input_native_continuation_unproved`; they are not accepted cancellations.
Input labels and canonical transitions are different views and must not be added
as if they were independent complete decisions.

Canonical action bindings are A: 2 plays without target arguments, 1 targeted
play and 1 end-turn; B: 11 plays without target arguments, 5 targeted plays,
2 end-turns and 1 selection. The exact first-fragment Defense+ native play has
unique frozen binding, native Commit and a proved canonical successor. This is a
bounded example of untargeted native card execution, not universal card coverage.

Each final trace is `native_commit_observed` followed by `transition_unknown`
for the same action witness (A sequences 29/30; B sequences 122/123). The reason
states that the session closed before a complete semantic successor boundary was
proved. These two final actions remain excluded from canonical transitions;
current/later observations were not used to fabricate successors. Each summary
counts one real failure and one unresolved decision for this same endpoint.

B's diagnostic is `human_action_native_type_mismatch`, associated with
`ReadyToBeginEnemyTurnAction`. It is retained as a diagnostic; it is not reclassified
as an extra failed Human input. The two rejected input rows remain independently
visible. These results are not a zero-failure session claim.

## Input and runtime boundaries

There is no Human `confirm_card` label in either fragment. The current contract
explicitly keeps untargeted mouse release/play-zone continuation asynchronous and
unproved as a same-input label. A begin-card label does not prove confirmation.
This recording limitation is distinct from model execution: Connector's public
begin operation uses the native directional-navigation/holder-Pressed path, and
untargeted model confirmation uses the exact `NControllerCardPlay._Input` path.
Neither a source path nor these mouse recordings qualify model-controlled play.

The journals contain no native run start/end qualification and include unassigned
run context. Two recording fragments do not establish two independent full games.
Research admission and train/dev independence remain unevaluated.

The active Workbench application was `9bb9e22f7849cc74df2f53f1b38f038bc6a36d09`.
Its actual Safari detail for fragment B displayed 26 input labels / 19 complete
decisions, matching the read-only preview; no create-dataset or train button was
clicked. Local import success is separate from policy registration, Runtime
loading, full-scene delivery and learned strategy quality.
