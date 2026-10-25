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

Defect-list entry: online chain Connector -> P1 shared reducer; first known
observed occurrence 2026-10-23 random GUI run, repeated 2026-10-25 evaluation
slot 35; blocks Agent progress at the starting bundle page; repair the bundle
DTO admission and existing forward selector mapping; regression is linked above.
