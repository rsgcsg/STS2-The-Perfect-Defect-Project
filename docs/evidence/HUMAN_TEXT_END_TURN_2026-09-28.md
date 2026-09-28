# Human end-turn input: source candidate

The text model already receives Connector's `end_turn` menu action. The gap is
training evidence: the native Human callback previously produced canonical
records but no corresponding pre-input text-menu label. This candidate adds a
separate input observation without rewriting old canonical records or changing
the legal menu.

Native source was inspected locally for STS2 v0.111.0 / 41cef1ea:
`NEndTurnButton.CallReleaseLogic` first checks native availability and can submit
`EndPlayerTurnAction` or `UndoEndPlayerTurnAction`. `ActionQueueSynchronizer`
may defer or relay a request. Consequently, one nested request plus normal
callback return proves an input request only, not queue admission, Commit or
successor. The separate tutorial callback is not covered.

Connector attaches an optional process-local witness to the existing leaf using
the exact current button. Missing witness does not remove that leaf. Annotator
freezes the complete current menu before the input and matches that button;
Evidence checks the new producer mechanism and exact chosen action. Subjectless
end_turn retains explicit JSON null and zero operands. This was checked using
actual TextMenuAction serialization with EvidenceJson.Options, not inferred
from a hand-written fixture.

The model-facing interface remains device-neutral. Godot/button/request objects
stay inside the live-game adapter and recorder. A simulator or fast Host may
implement the same public state/action/result contract using its own bindings;
its generated records do not thereby acquire native Human origin. This change
implements no simulator and adds no model strategy or legality engine.

Final source/component identities, checks, install/load and bounded Human
canary results must be appended from actual outputs. No new Human evidence is
claimed by this source candidate.

## Source identity and compatibility

Logic commit: `64dc96203bb29eff0bbc6517096b119cf7c30b28`; normal integration
retains PR #79's accepted source. Candidate version commit and all four current
path-scoped source revisions: `1b037dbfbc8c12d62901eaafaf8ca8bb5075f0ff`.

| Component | Version | Path tree | Source digest |
|---|---|---|---|
| Connector | 1.3.0-rc.5 | 370c8ee694b8d0a4effe4978c23713f051d56faf | 7b7418e6b8719e6cc4261b1d80b1ad90a801f719a657ab54d5c410481118df3d |
| Annotator | 0.3.0-rc.14 | a524cbbaa77459e8e24148b1ee1656df1e2ba7f9 | 25953dd81f713a560537c429e15d1a76dfb412fcab5009cfde5212c66dc47a8d |
| Evidence | 0.1.0-rc.19 | 0936dd501e0d4a1505b4100ca3ecd5f6cfe1607c | 0e289aba0d0c36ec79ddc9d172358c945aa7445c741a6a250c2e5de4f65653ad |
| Game Mod | 0.2.0-rc.18 | 850eed3795653caaf64faa39718a4b0ea9795ef4 | b99429bf58806638fca3b7537b3a02e91d7585630d8c99436f57fe68e3ff1f35 |

The Python combination, pyproject and lock pair the new producer with Evidence
rc.19 at that exact source. Old readers reject this new mechanism; existing
recordings and their original identities are not rewritten. No Runtime change,
consumer rc.6 pin change or model retraining is included.

## Local checks before final candidate publication

Logic-source checks passed: Connector Host 298 tests; Annotator Core 310 tests;
Evidence v3 file 33 tests; focused STPD text import 6 tests (11 deselected).
The STPD check used explicitly scoped candidate source imports in an isolated
Python environment, not a newly installed locked consumer. The native Annotator
Mod compiled against the exact local game with zero warnings/errors. Actual
C# serialization through EvidenceJson.Options retained explicit null subject;
both verifiers accepted it, and malformed subject/operand cases were rejected.
An earlier negative-case expectation failed because the existing catalog check
rejected malformed input earlier; the raw failure remains in the local logs.

Independent source review found no blocker. Final combination checks and hosted
CI are recorded on the eventual PR; these earlier checks are not relabelled as
final-head CI or installed Human acceptance.

## Next runtime boundary

After exact candidate build/install/load and matching reader activation, capture
one real Human end-turn click with a pre-input complete menu. Verify the sealed
new input row and importer, alongside canonical evidence without equating the
two. Tutorial/Undo, external-controller activity, missing witness and interrupted
capture must not manufacture accepted rows. Agent gameplay is never Human data.
Rewards and map input capture remain separately scoped follow-up work.
