# Human text-input begin canary, 2026-09-26

Bounded recording/import evidence for PR #47, dependent on PR #46. This is not
full-game Human coverage, a trained policy, or acceptance of either PR's entire
source range. Raw observations, bundles and the engineering store remain private.

## Exact producer and installation

- Clean build/install workspace: `ff2bf0497c809c254bd08d367e794198c9a92d81`.
- Game Mod source: `91ad44e108d69139638d4106ecb555b5fc22755a`, version `0.2.0-rc.13`.
- Connector source: `2e3fdee5f7eec7f1a2b0ef70d9b64ccd5d895438`, version `1.3.0-rc.2`.
- Annotator source: `83936a7f4769de558d1a2d5a434459689b88f1fc`, version `0.3.0-rc.10`.
- Unified DLL SHA-256: `67beef71cfa6c9451b65be0130a6854cf21336799347e4c4f1ad24993f5bb359`.
- Unified DLL MVID: `9c348297-d848-4ccb-a479-379ff350d495`.
- Game: `v0.111.0` / `41cef1ea`, game DLL SHA-256
  `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`.

The user confirmed game exit. Owning Game Mod deploy, launch and verify-loaded
commands completed with exit 0; loaded verification reported pass/errors=[] at
10:06 UTC. Build, installed bytes and loaded SHA/MVID agreed. Deployment retained
its rollback archive and existing recording configuration. No model takeover,
model rebinding or production Node Runtime upgrade occurred.

## Owner-operated sample and actual results

The requested sample was: start recording in combat, pick up a card and cancel,
play a card normally, then close recording. The user replied that recording was
complete. This is explicit owner attestation, not machine proof of Human origin.
The recorder reported `recording_closed`; the close receipt was written at
10:34:23 UTC.

- `human-text-inputs.jsonl`: 7 rows, all `accepted_input`, `exact_unique`,
  `match_count=1`, `external_controller_active=false`.
- Native mechanism: `begin_card_play_exact_factory_return`; mapping basis:
  `text_menu_native_reference_equality`.
- Close receipt: count 7 and exact stream SHA-256
  `31efccc36d044f86cdbea457557dd8b21f558797bfc3494d6e05212dc241c6ce`.
- Core audit passed with no errors/invalidations and zero legacy Decision rows.
  The separate existing canonical stream contained 2 records; packing preserved
  them. These counts describe different contracts and are not interchangeable.
- Owner-attested `pack-session` passed. The actual locked Evidence V3 verifier
  passed, including the declared text-input stream.
- STPD archived, verified, published and reloaded the engineering source using
  clean producer `ff2bf04` and locked Evidence source `59d27531`. All 7 current-page
  projections bound the witnessed choice to exactly one matching menu entry.
  Their complete menus had 8–10 candidates.
- Creating a train/dev BC view correctly refused this single session with
  `independent_groups_required`. No split was weakened, synthetic session added,
  or training launched to make this check pass.
- The existing synthetic engineering B-S v2 export
  `13e9267079c66125aa4213f1aadd61b10142b2ec00b33eb646304d9564195ee5`
  also scored all 7 frozen snapshots offline. Every candidate received a finite
  score in the exact input-menu order. Joint inputs were 9,926–14,166 tokens,
  within that export's 16,384-token capacity, with no truncation. This check used
  source `5c791fb` with only this report/navigation documentation uncommitted.
  It did not train, select or deliver a game action, and says nothing about
  the small synthetic model's playing quality.

Seven accepted begins do not mean seven played cards. A successful pickup remains
an accepted begin when the user later cancels. This new stream does not yet label
target selection, cancellation, final confirmation, effects or causal successors.
Repeated same-page choices remain recorded rather than silently deduplicated.

## Source checks and remaining boundaries

Connector 293 tests, Annotator Core 287, Evidence 142 and nearby Python 67 passed
before this capture. Later package metadata checks included Runtime 121 tests and
temporary installed smoke, Game Mod 77 tests, identity 7 and BOM 14. These are
source/package checks, separate from the exact native canary above.

Hosted runs 36229415497 and 36229784848 exposed SDK-lock and Game Mod project-version
alignment omissions, subsequently fixed without weakening tests. Run36234778925
reached Python and failed the shipped Workbench Evidence-pin consistency test.
The one-field combination fix is separately tested; the final candidate's own
hosted run must be read from PR #47, not inferred from this canary.

Cancellation/target/confirmation labels, other Human scenes, full-run continuity,
general non-interference, policy quality and scientific dataset qualification
remain unproved. No public raw recording, weights, cloud upload or paid task was
created by this local canary/import verification.
