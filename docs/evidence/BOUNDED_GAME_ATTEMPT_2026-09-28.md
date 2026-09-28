# Bounded game-attempt controls and fixed small-B diagnostic

Date: 2026-09-28 (Australia/Brisbane). Source/test, installed application,
recorded native delivery and learned quality are separate claims.

## Source package

Base: `bc47ad294b35f7bd68b0d8dc9b7dedc3364b1159` (PR #75 integrated).
This application change exposes existing Runtime CLI budget capabilities before
loading a text-menu model. The service owns two fixed choices:

| Choice | Submission attempts | Policy calls | Deadline per explicit autonomous authorization |
| --- | ---: | ---: | ---: |
| short (default) | 16 | 32 | 60 seconds |
| extended | 2000 | 4000 | 30 minutes |

Loading still starts Human/released. The UI does not start Auto, renew budgets,
retry unknown actions or change a loaded profile. Runtime remains the authority
for admission, cancellation, budget expiration and delivery. No Runtime/Connector
source, native package, policy manifest contract or legacy rc.6 pin is changed.
Legacy selections offer only their existing default; unavailable budget metadata
is unknown, not invented. The text-menu startup and live limits must match the
requested profile. The larger choice is a bounded opportunity to attempt a run,
not proof a model can finish it; a later explicit Auto request is a new authority
window, not an automatic extension.

After existing Evidence verification, reports distinguish recorded action verbs,
internal menu navigation, native submission attempts and delivery classifications.
They expose budget exhaustion and an optional observed terminal page. The latter
requires a complete text-menu snapshot with both context/surface `game_over` and
public `win`/`loss`; conflicting results are ambiguous. An observed terminal page
is not a from-start model game: there is no attested native-game-run identity here.
`game_outcome` remains `not_measured`; failed verification cannot provide success
counts. Old evidence and old reports are not rewritten.

## Fixed learning diagnostic (actually executed)

Producer: `bc47ad294b35f7bd68b0d8dc9b7dedc3364b1159`.
Locked private Python 3.11 development environment, CPU, two Torch threads.
No Qwen/model download, GPU/cloud charge or private-data upload.

- Task: `learning-check-2833938f03204e9f85606175415a8cb6`.
- Dataset: `26e75e6ea837fcc6f616f8617e55677a97d74651c997f95e37795a5529ab70f1`.
- Training input: `6d7475e7ebd710b348e70e01f3bc3f8efbe20b91e5edab40a163d847f001feae`.
- Input view: `9e453e8059e7d93361918b1a50bf4e5284be769ceb5245f37c70979ddc9b07b1`.
- New run: `d174add04f357028f8514485520049efcd5c8eb06e819144d7f9977daae35d5d`.
- Model: `005af0725595c3c0f1a59aede0916c77034e49e881da650e1742830c6e1489a6`.
- Evaluation: `555d313e841a40cc2c7c5729a2c216ad9911d03b8089417edf9e2a8ff22ec2bb`.
- Final checkpoint: `a0c8e42015c7ef22760ffdd2f489a66c27bdd8675f29de0b33d75ece0e81e839`.

One predeclared scratch run changes only steps 3 -> 260 from the existing tiny
B.s.v2 configuration (width48, one layer, two heads, FF96, dropout0, seed1701,
LR0.0003, weight decay0.01, gradient clip1, max tokens16384). It does not resume a
checkpoint under a changed configuration. Existing worker/artifact/report owners
executed the run under the local training lock, after exact source/use-ledger
checks. New immutable run/use records were added; old data/models were retained.
The existing browser three-step training operation was not relabelled as this run.
The user-facing training configuration is unchanged by this diagnostic.

Completed, exit0, 31.24 seconds on this Mac with other background processes;
this is not a portable performance estimate. The unchanged Human input-label
split has 26 train/5 dev: 23 combat-turn and 8 card-operation inputs in total,
not a full-game corpus. These are not extra canonical decisions. Native-run
independence remains unknown. No partition/hyperparameter change after dev results.

| Fixed dev (n=5) | 3-step baseline | 260-step diagnostic |
| --- | ---: | ---: |
| Top-1 | 0/5 | 1/5 |
| MRR | 0.18038 | 0.34000 |
| NLL (lower is better) | 2.80292 | 3.24002 |

NLL worsened. This tiny mixed result does not establish learned-quality improvement.
A separate read-only reviewer checked actual manifests, parents, config, metrics,
and preservation of the baseline. No raw samples or weights accompany this report.

## Actual installed short attempt (separate from the new source package)

Using the already active clean Workbench
`ec9b6d1c07baa97c6538aa935f3d95dbaf9c7f07`, root operated Safari's real local model
page: export/verify the new model -> register -> prepare/load (Human) -> one explicit
Auto -> automatic budget handoff -> explicit Stop/seal. No new application or
native package was installed for this attempt.

- Selection: `local-text-b-96d79b7e299349589230d69d597c01b6`.
- Runtime run: `run-f17993e9-d759-4cf5-abe6-014fa6682c38`.
- Exported policy SHA: `b6130eb1d3d710d3824f9dbb1fa39133d4ab5cf6115f305f43f1a78026037347`.
- Manifest SHA: `8839ce96d84e3dcf158734b07bba7750c7f72104c27e770f7db5bd7908fa282a`.
- Runtime code SHA: `74334ca24c90194b4c97b9b9ffaacb2fdc93f3ef53d879998f2f9c11b6c05600`
  (previously installed rc.10; Game Mod rc.17 unchanged).
- Evidence content: `7267597e3bb3c0fb2484420a8bf577c8701936aa97c59f03bfccb0b22fcadd6e`.
- Evaluation: `2a56385f15b9a3ebab2c0c6eacd6f8c6c4572ec2662d4edb924d6f920e0812ae`.

Owner verifier and root's independent invocation returned pass/findings=[] for
71 sealed events. Sixteen scoring calls/dispatch attempts reached the existing
16-attempt budget in 3233ms. One native `return_native_information` was delivered;
15 other operations were internal text-menu navigation (`open_information`8,
`back`7). One input was `combat_draw_pile`; the remaining 15 were `combat_turn`.
This proves neither 16 native actions nor useful combat. The model still loops.
The final state is stopped/unloaded/Human/released, untainted, errors=[];
`error_code` is null and `observation_error` is absent. No automatic restart.

## Native continuation boundary and next evidence

The existing Native Foundation records the ActChange semantic chain
`SetLocalPlayerReady -> RequestEnqueue`, then `VoteToMoveToNextActAction.ExecuteAction
-> OnPlayerReady`, then conditional `all_ready -> EnterNextAct -> ActEntered`.
Enqueue is not Commit/successor. The available local partial native-source tree
does not include the synchronizer/vote implementation or a verifiable build
identity; single-player automatic readiness versus an explicit post-Boss control
is therefore unresolved. No speculative Connector action was added.

Remaining work: representative non-combat data/strategy, independent native-run
identity, whole-run continuity and exact native cross-act continuation. Increasing
budget alone cannot solve the observed policy loop. Memory/control comparisons
remain separately defined experiments, not a presumed cure or action filtering.
