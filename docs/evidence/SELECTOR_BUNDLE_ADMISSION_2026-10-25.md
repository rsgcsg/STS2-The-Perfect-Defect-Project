# Card-bundle selector admission repair

Owner: card D, Connector SDK. Base: `011830b9e6785a91929df5fe07db508fb232d8d5`
on develop. This is an implementation correction under the existing P1 r1.5
contract; it changes no game rules, InputSpec, weights or Runtime failure policy.

## First incorrect fact

The original checkpoint-876 / Defect A0 / fifth-seed run stopped after one
submission. Observation sequence 32 reported `required:selector:invalid_content`.
Its sealed public basis (SHA256
`b1e8d20a6e780a7fae1f126b2d9b2a7cdde7ab74ea6c908cc9c2c6d5e14572a3`)
already contains two complete card bundles with three cards each, including
names and descriptions. `CardBundleSelectionSurfaceReader` and
`VisibleInteractionProjection` emitted their existing two-stage bundle DTO.
The first error was `completeSelector` requiring single-card grid fields from
that different DTO. The old installed SDK reproduces the rejection on those
same bytes; the corrected SDK admits them with two whole-pack choices.

The incident is an implementation defect, not missing native card data or a
contract inability. The explicit operator `seal-unknown` receipt releases the
original slot while preserving its unknown result and original records. The
remaining 65 evaluation slots are not resumed by this repair.

## Correction and regression

Bundle admission checks the actual pack contents, unique identities and the
native choosing/selected-preview relationship. It does not invent
`cards/min_select/max_select` grid fields or flatten a pack into independent
card choices. The shared reducer maps native pack select to `selector.choose`
and preview confirm to `selector.finish`; preview return is excluded by the
existing append-only rule. Confirmation stays an explicit native action, with
no singleton-count automatic submission or unqualified continuation fold.

[Synthetic wire-shape and negative regressions](../../components/connector/sdk/typescript/test/p1GlobalBundleSelector.test.ts)
cover both stages, missing/empty/malformed contents, identity errors and invalid
preview state. Private raw recordings are used only for local incident checks.
The online Runtime and offline converter continue to consume the same SDK core.
Old projections and model/recording bytes retain their original identities.

## Evidence boundaries

The initial SDK gate passed 615 tests. Independent read-only review ran the
bundle, selector-forward and admission groups (85 passing tests) and found no
blocking issue. These are source/test results, not installed, loaded, Human or
strategy qualification. A new Runtime package carries the corrected bundled
SDK; package identity, actual install and one same-checkpoint/seed native-game
validation must be recorded separately before completion.

## Bounded historical impact

The current Store catalog contains 33 source artifacts, including 28 declared
human sources. Reading all 28 original archives through the existing Store and
verifying their transfer inventories/member hashes found 32,261 public bases:
1,003 selector bases, zero structurally incomplete selectors, and zero bundle
pages. Thus this bundle-admission defect has zero observed affected bases in
that historical human-source inventory. Source artifacts are not necessarily
independent games, and declared origin is not Human verification. There are
separately 72 `selector_decision_pre_or_lineage_unavailable` issue events; this
repair does not fix or qualify those missing-source events.

Of 66 named projections for those sources, 65 passed the current projection
reader; one remains `projection_identity_mismatch`, without relaxing its
admission. No inspected incomplete observation had an N supervision mask.
The training reader independently requires complete current information before
accepting N. These counts do not claim a census of unindexed historical files.

Defect-list entry: online chain Connector -> P1 shared reducer; first known
observed occurrence 2026-10-23 random GUI run, repeated 2026-10-25 evaluation
slot 35; blocks Agent progress at the starting bundle page; repair the bundle
DTO admission and existing forward selector mapping; regression is linked above.

## First native validation and preview conformance correction

The independent checkpoint-876/fifth-seed repair run on Runtime rc.44/SDK rc.13
passed the two-pack choosing page, selected and confirmed its preview, continued
to floor 9, and recorded public `game_over.context.result=loss`. All 88 reduced
observations were complete; all 92 native deliveries were delivered, with no
taint, pending request or Human handoff. Its typed original was sealed and fully
read back from R2. It remains excluded from the original evaluation ledger.

Those exact preview bytes exposed a fixture-conformance error: the real native
referent role is `selected_bundle`, while the synthetic preview fixture had used
`bundle`. The first candidate therefore used the existing `selector.confirm`
fallback. The corrected mapping binds that real role to the already-declared
`selector.finish`. Local replay of the sealed native preview proves one complete
finish choice, the same exact native confirmation action, and the same excluded
return. The fixture now mirrors the actual producer role. SDK rc.14/Runtime
rc.45 are a separate immutable delivery, preserving rc.13/rc.44 evidence rather
than relabeling it as a later candidate's live qualification.
