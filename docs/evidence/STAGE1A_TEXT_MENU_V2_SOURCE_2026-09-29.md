# Semantic card selection in text-menu-v2, 2026-09-29

This receipt records source, portable contract checks and local exact-game
compilation. It does not record installation, loading, game interaction, Human
capture, cross-Host equivalence or model/data qualification.

## Behavior and ownership

The explicit `text-menu-v2` profile adds a Host-owned text cursor for card and
target selection. The combat root contains one `select_card` choice per currently
playable card. Targeted cards enter a menu of the selected card's current native
targets; untargeted cards go directly to confirmation. Confirmation has one exact
native `play` leaf plus cancellation. Selecting or cancelling only changes the
text cursor and returns `effect_domain=text_menu`, `native_delivery=null`.
It does not manufacture a held card or target arrow in the native game UI.

The final leaf follows the existing native path:
`StartPlayerEnvironmentInput` → `NativeUiActionRuntime.StartNativeUiInput` →
`StartCombatCommand` → `StartDirectPlayCard` → `StartPlayCard` → `TryManualPlay`.
STS2 continues to own legality and execution. The Host captures complete current
card/target pairs through existing `BuildBindings` and `ProjectBoundActions`,
checks exact card membership and each card's target domain, and revalidates the
captured native binding at submission. An incomplete catalog is unavailable;
it is not a truncated menu advertised as complete.

Repeated observation of the same native source retains the text choice. Changed
source, private pair catalog or controller resets it. Stale requests cannot
submit the old choice. Native dispatch exceptions return unknown delivery,
`retry=never` and no successor; replay of the same request returns its original
result without redispatch. This does not add a new global service taint: a later
explicit request after observation follows the ordinary authorization and
execute-time checks. Policy consumers must preserve their existing unknown-run
quarantine. A delivered result's immediate observation is not causal settlement.

Staged labels use current public object names. Selecting a card does not borrow
one of its future target labels. Equal names are distinguished by current public
referent order while retaining opaque IDs only as exact bindings; the display
helper does not reorder candidates or send these IDs as descriptive names.

Default and explicit v1 routes retain their existing native input behavior.
V2 must be selected explicitly for capabilities, Snapshot, action/result and
atomic observation context. Profiles share controller authorization, submission
serialization and request-ID conflict detection. SDK decoders separately check
v2 referents, staged selection, current menu/result association and unknown
semantics; they do not invent native legality.

## Exact source and local build identities

- Connector source `d9d1d9ac573947b5abddc7f6ba8526dfa8e66fe2`, candidate
  `1.3.0-rc.7`, component tree `412e214b33679aad26a7831a9c501e68fd8760a6`.
- Whole-component source digest
  `291ace180e82a9f68d2ea06dcc92da41ff2c0f9c85c823861ea3a93c2a488450`;
  public contract digest
  `faa6eba31e4ca103e915f675fef80ea5c5593a4118dbe9389dc803e88148566c`.
  SDK source candidate is `1.3.0-rc.4`; no released SDK pin is repointed.
- Local game `v0.111.0 / 41cef1ea`, STS2 assembly SHA-256
  `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`,
  MVID `57785517-0b16-42b9-8b36-bad6fb28384b`.
- Clean standalone Connector build SHA-256
  `8b7b9e3fea14129c96e0758c360c7680640aec1a82408f1fa629eb1e94947c67`,
  MVID `9230dc11-5b64-485a-a833-a849a3b3e598`.
- Unified Mod build SHA-256
  `2daccde051606164cecb55e838e420d5bcd2c408e00339efc617e4fceb631aa2`,
  MVID `1fd85ae3-c5ce-4a7d-8f01-1b6a2accef9b`.
  This is a local build, not a newly published or production-installed Mod.

Identity scopes must not be conflated. The component/BOM identity includes all
tracked component files. The Mod's embedded composition identity intentionally
selects compiled source inputs. For example, standalone Annotator path revision
is `c1ae5b6ca1f62d0b50858e6340b524eb3d400655`, while compiled Core/Mod inputs
have revision `1b037dbfbc8c12d62901eaafaf8ca8bb5075f0ff`. Their different scope
explains these values; neither is silently replaced with the workspace HEAD.
Historical installation/load identities remain unchanged.

## Actual checks and review

On clean source `d9d1d9ac`, using the author's exclusive locked Node environment:

| Command | Actual result |
| --- | --- |
| `npm --prefix components/connector run check` | exit 0; SDK 36 passed; package/docs/contract/boundary/compatibility/CLI/release-tool/Python checks passed |
| `npm --prefix components/connector test` | exit 0; Host 315 passed, 0 skipped; SDK 36 passed; transport/docs checks passed |
| `npm --prefix components/connector run build` | exit 0; clean Release Host, 0 warnings/0 errors; exact build identity emitted |
| `npm run check:exact-game` with the existing explicit game directory | exit 0; Connector checks/build, Annotator Core 311 passed/0 skipped, unified Mod checks/build, 0 C# build warnings/errors |

An initial component check lacked TypeScript dependencies (exit 127). A subsequent
check used a temporary shared dependency symlink; this was corrected by removing
only that link and preparing the author's private locked dependency directory.
The final checks above used that private directory. Neither the lock nor the
linked source environment was altered. These earlier attempts are not substituted
for the final clean-source gate.

Independent review covered the SDK profile and the Live native binding chain,
with 40 earlier v1/v2 focused tests. The later display correction added a faithful
same-name target case; independent focused recheck passed 1/1. Final complete
Host count is 315, not a copied historical count. The final integration retains
these component bytes and maintains current BOM identity separately; its own
hosted gate remains a distinct current-head requirement.

## Remaining work

Existing v1 Human recordings, current observation-only M2 exports and the separate
Managed model smoke are not v2 evidence. They lack this staged selection and its
complete private pair witness, so changing a schema string cannot migrate them.
Managed v2, research projection/data admission and an installed Live canary need
their own owner work. Noncombat surfaces retain their existing scope; this card
staging change does not prove every game scene, strategy quality or Stage1a done.

## Candidate package lock correction

The first PR #106 full run `36565688487` (attempt 1; head
`c33d8dd8045c22947f82a330691a259361909243`) failed on both operating systems.
Policy Runtime's own workspace lock still named Connector SDK `1.3.0-rc.3`,
while its bundled workspace source was `1.3.0-rc.4`. `check:package` rejected
the mismatch at `tools/package.mjs:38`; this failure is retained and not a
Runtime behavior failure or a successful full gate.

Commit `25bb1116440f0cf158abff7f2ba00bbaee49161b` changes only that lock
entry to the exact workspace version. No dependency source, production code,
assertion, consumer pin or component version changes. The original lock
reproduced exit 1; the corrected component `npm --prefix components/policy-runtime
run check` returned exit 0 (142/142 tests, typecheck, build, deterministic
package and installed CPU smoke). A clean-commit `check:package` also returned
exit 0. The installed smoke used a temporary package and synthetic Connector;
it did not install a production Runtime or contact a game. Independent review
confirmed the one-field diff and the source SDK version.

Path identity now records Policy Runtime source
`25bb1116440f0cf158abff7f2ba00bbaee49161b`, tree
`a641eda77a7ac2da90bb84ef7b8c3c8700147cec`, digest
`361def1aefe2f83d6fe81d6a7dd79518660aa48122e494d6acbe1de46220f0d3`.
Connector identity and all historical installed/loaded evidence remain unchanged.
The corrected integrated head requires its own selected hosted gate.
