# Full-run recording and non-creature potion execution, 2026-09-29

This is a bounded source/recording receipt, not whole-1a, model quality or
zero-failure full-run qualification. Raw recording/game files stay private.

## Closed recording

The user's confirmed mouse recording has session
`session-20260928T162157Z-2b0c0cbd78874df382d89a1b630f2f5a`, timeline
`timeline-b2c1d10ccdaf48309ecb4ab5a5b8a6cd`, and one assigned `run-0001`.
Native launch was observed at 2026-09-28 16:21:59.467230 UTC; native
`RunManager.OnEnded(isVictory=false)` at 16:40:51.451385 UTC. There was no
native resume, recorder pause or abandonment marker. Journal sequences 1–2031
are continuous. These are observed boundaries, not proof of every game family.

Producer: game v0.111.0 / 41cef1ea, Connector 1.3.0-rc.5, Annotator
0.3.0-rc.14, source `1b037dbfbc8c12d62901eaafaf8ca8bb5075f0ff`, runtime
`ff0bed0e757b4cccb4f2991d87820fb3`, capture profile
`human-full-run-read-rich-v4`. Loaded DLL SHA256:
`9ac7025dfdde2585808a71eaf9e99ebe8808be5bf3ec6d21dcd8f115b2e78280`.

| Evidence view | Actual result |
|---|---|
| Collection-tool audit | exit 0; 256 valid compatibility records, 0 invalid |
| Semantic trace | 733 accepted, 725 proved, 722 durable canonical, 7 cancellations |
| Human text inputs | 584 total: 537 accepted, 6 rejected/cancelled, 41 not mapped |
| Invalidations | 47 diagnostic, 3 explicitly failed-closed |
| Owner's deduplicated real failures | 4: the 3 failed projections plus 1 unresolved successor |
| Native semantic diagnostic | exit 1; 500 accepted, 493 successful, 7 cancelled, 490 exact membership, 3 unknown |
| Player-choice pause/resume | 12 each |
| Independent Evidence V3 bundle verification | exit 0, findings empty; failures remain inside the verified artifact |

The source tool's release ID was verified before audit and private packing:
`12be629c35a8d5b28a3edb1ed5b23024905203aafab6c06c5ad7bc9e1773cd6a`.
The derived audit bundle ID is
`af7a283b8056e19be2ef9721e50c8eeff8f713f04aa9ae9e37cc2e621e150c3f`.
All 2449 original files (75,290,736 bytes) retained their before/after hashes.
The Human-origin attestation comes from the user; structural verification does
not itself prove Human origin. No dataset or training was started by the audit.
These views cannot be added together as training sample counts.

Canonical records include combat, map, reward/card reward, shop, rest,
treasure, event, deck upgrade/enchant/removal, potion popup, combat-pile and
generated-card selectors. Unseen variants remain unverified. The final
EndPlayerTurnAction retains `session_closed_before_successor_boundary` despite
native completion; natural run termination is not permission to fabricate S'.

## First incorrect owning fact

Three successful native UsePotionAction occurrences had no member in
`potion_belt_non_combat_use` at acceptance and before execution. Their exact
execution frames still contained the selected FoulPotion in its slot; this was
not an enqueue-time removal. Annotator correctly refused canonical projection
with `A game-action transition requires a typed native execution action space.`
The diagnostic native type mismatches are not 47 additional gameplay failures.

Read-only inspection of the exact v0.111.0 native implementation established:

- FoulPotion.TargetType is TargetedNoCreature outside combat; its native custom
  usability checks the current merchant/fake-merchant target and inventory state.
- NPotionHolder.TargetNode maps an accepted NMerchantButton to a null Creature.
- PotionModel.EnqueueManualUse creates and queues the action before removal;
  OnUseWrapper removes it later.
- UsePotionAction.ExecuteAction preserves null for TargetedNoCreature and
  calls the native IsValidTarget(null) before use.

The omitted branch was in Native Foundation's CaptureNonCombat, which only
produced Self/AnyPlayer operands. The correction uses native type and
validators; it does not copy merchant rules into another legality engine.
Current slot, usage, permission, living owner and custom usability guards stay
in place; queued membership is distinct from permission to submit again.

The public popup consumes these semantic facts too. Its direct-use path must
exclude TargetedNoCreature both when describing the menu and when revalidating
delivery. It retains the existing native target-selection path and exact
merchant-node binding, rather than silently skipping a player interaction.
No new wire schema, native operand, retry, or retrospective evidence repair is
introduced. The three sealed failures remain failures.

## Regression and remaining runtime boundary

On the parent implementation, the new null-target membership regression failed
at Assert.Single because the catalog was empty (4 passed, 1 failed). The native
FoulPotion type/IsValidTarget checks use the actual game assembly; the provider
fixture supplies scene-dependent custom availability rather than pretending
to construct a live Godot merchant. Independent blockers and queued/removed
slots, wrong subject/target, popup staging and existing FruitJuice direct use
are covered. Annotator's projection test also covers targetless UsePotionAction
and rejection without typed execution evidence.

Exact commands and final check results belong to the candidate PR receipt.
Pure .NET tests cannot establish live merchant button/target manager behavior,
real cancellation, public delivery or newly recorded Human evidence. A new
candidate needs its own exact build/load and focused runtime check. This source
change does not upgrade the identities or qualification of the recording above.
