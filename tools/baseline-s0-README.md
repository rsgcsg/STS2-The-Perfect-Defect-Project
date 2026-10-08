# Bounded S0 collection and evaluation

This CLI reuses the shipped Host Driver, Connector sealed text-menu-v2 SDK,
Policy Runtime and trusted Python NDJSON2 adapter. It has no second executor.
The lead prepares the isolated `defect-a0-s0` profile template and exact installed
artifact first. The Driver explicitly selects Defect A0 and verifies its public
run identity. The CLI game directory is resolved through the existing Host
installation resolver before Driver start. Build the existing Connector SDK
and Runtime before running.

```sh
node tools/baseline-s0-runner.mjs collect \
  --installation /ABS/STS2 --local-root /ABS/host-local \
  --evidence-root /ABS/private-s0 --seed 1 --template-id defect-a0-s0
node tools/baseline-s0-runner.mjs evaluate \
  --installation /ABS/STS2 --local-root /ABS/host-local \
  --evidence-root /ABS/private-s0 --seed 2 --template-id defect-a0-s0 \
  --package /ABS/agent-package --python-command /ABS/python/.venv/bin/python
node --test tools/test/baseline-s0-runner.test.mjs
```

Budgets default to 60 policy calls, 60 submissions and 240000 ms. Flags
`--max-calls`, `--max-submissions`, `--deadline-ms` may only reduce those bounds.
S0 keeps the existing Runtime successor-observation compatibility policy
(41 attempts at 250 ms intervals), rather than introducing a shorter animation
budget without native evidence. The global deadline and unknown-no-retry rule
still apply. This sampled post-delivery observation is not causal settlement;
changing this timing does not qualify or repair an unrelated native focus bug.
The raw capture budget is 64 MiB / 4096 captures. SDK capture failure or ledger
write failure stops collection; native unknown is never retried. `--browse false`
disables the finite teacher information browse/return/revisit journey; this
parameter is recorded with the teacher source hash. This is an explicit public
heuristic teacher, not Human data or an optimal strategy.

## Actual text-menu-v2 scope

Teacher 1.2.0 and generated manifests share `S0_TEXT_V2_KINDS`, a finite registry
of current native text overrides and source-defined LiveHost/NativeUi passthrough
families. Unknown/unresolved kinds abstain or fail whole-decision admission;
there is no arbitrary-kind fallback. This source scope is not all-scene runtime
qualification. It intentionally excludes startup/menu/tutorial management.

| Public page/owner | Teacher choice | Source owner |
| --- | --- | --- |
| `native_map`, surface `map_navigation` | Current-C `activate` whose subject is a public `next_options` member; prefer public monster option. Otherwise current `return_native_map`. | `NativeTextMenuInformation.CaptureOwned`, `NativeTextMenuFrameBuilder`, `BoundActionProjection` |
| `combat_turn/ready`, virtual `root/card_targets/card_confirmation` | `select_card/select_target/play`, then `end_turn` when no advertised card choice. | `TextMenuV2Session.Observe` |
| `combat_card_operation/card_targeting` or `card_confirm` | Advertised `confirm_target/confirm_card` (or cancel); does not confuse native held-card ownership with virtual selection. | `NativeTextMenuFrameBuilder.CardOperationPage` |
| Native tips (`card_tips/relic_tips/power_tips/intent_tips/orb_tips/topbar_tips/native_tip`) | `return_native_tips` at root. Native ownership resets the virtual cursor. | `NativeTextMenuInformation.CaptureTip` |
| `run_deck/combat_draw_pile/combat_discard_pile/combat_exhaust_pile` | `return_native_information` | `NativeTextMenuInformation.CaptureOwned` |
| `relic_inspect/inspect_card` | `return_relic_inspect/return_card_inspect` | `NativeTextMenuInformation.CaptureRelic/CaptureCardInspect` |
| `potion_popup/potion_targeting` | Close/cancel advertised current operation; teacher does not initiate potion use. | `NativeTextMenuPotions`, `NativeTextMenuFrameBuilder` |
| Ordinary rewards | Current generic `activate` joined to enabled public reward subject; after claims, unique subjectless activate/explicit skip joined to public proceed flags | `RewardClaimSurfaceReader`, `NativeUiActionRuntime`, `BoundActionProjection.GenericAction` |
| Linked rewards | Semantic claim joined to current public entry/group/child, then exact screen-bound proceed/skip | `NativeTextMenuRewardPages` |
| Card reward | Current native `select` joined to public cards/selectable IDs; label-only alternatives abstain | `CardRewardSurfaceReader`, `NativeUiActionRuntime.DescribeCardRewardCommands`, `BoundActionProjection.GenericAction` |
| Exact source-defined selector families | Advertised confirm/select/skip/cancel | `LiveHost/*SurfaceReader`, `NativeUi/*Selection`, `NativeTextMenuFrameBuilder.OrderLegacyTextActions` |
| Event/dialogue/rest/treasure/shop/game-over | Declared forward-choice heuristic over current native C; unknown operations abstain | Exact LiveHost surface readers and bound-action projection |

The teacher's optional browse journey requests an information category and tip,
counts a visit only when the native tip page is actually obtained, returns through
the native owner and repeats once. A stale chosen leaf does not fabricate a
visit. Browse offers are capped at 16, then ordinary play continues; native return
actions remain preferred. No raw runtime IDs, coordinates, private rule state or
consumer-created operands determine executable authority. Public map references
are only used to rank existing C members, never reconstruct legality.

Ordinary rewards use the legacy generic projection because the specialized
linked adapter returns no frame when no linked group exists. Consequently
`claim_reward -> choose -> activate` and `proceed_rewards -> activate_control ->
activate` do not arrive as semantic reward verbs. The teacher discriminates by
the current public surface schema, typed visible subject and enabled descriptor;
it never reads private native-operation fields or chooses from labels. Claimable
rewards precede proceeding; unresolved claim bindings or ambiguous subjectless
controls abstain rather than silently skipping. The entire C and score count
remain intact.

The current unprofiled card-reward alternative descriptor exposes only ID,
index, label and enabled state. It does not expose whether the native alternative
rerolls or returns without completing the parent reward. A typed selectable card
is preferred; alternative-only input abstains with
`card_reward_alternative_effect_not_public` in optional raw `policy_result`
diagnostic metadata. Diagnostics are not adapter output or model input. Resolving
that protocol ambiguity requires the owning public contract, not label guessing,
forced runner input or a history reset to conceal a reward loop.

`records.jsonl` uses `sts2.baseline-s0/raw-record-1` envelopes with monotonic
`record_index`, `run_id`, `recorded_at`, `type`, `payload`. Each `capture` stores
the complete metadata and a relative `snapshot_path` to exact original UTF-8
bytes, SHA256 and length. Raw capture alone is never a training row.

Only actual adapter invocation (teacher) or child port write offer boundary
(learned adapter) emits `policy_offer`. It joins the latest exact full snapshot
to its capsule and stores `offer_id`, `capture_id`, capture ordinal, bytes path
and digest, snapshot identity, continuity token, complete candidate digest/count,
`input_spec=s0-admitted-policy-offers-v1`, `source_kind=agent`, actor identity and
`I=false,F=false`. Successful `policy_result` joins by `offer_id`, echoes validated
completion, complete scores, selected index and chosen action ID. Failed/aborted
offers have no fabricated label. A projector must validate the actual capsule,
offer/result and current catalog; it must not infer offered inputs from polls,
receipt successors or captures, and must derive advance/score-only via the
versioned structured projection. Receipts/ticks/control are separate diagnostic
records and never model features. Public choice is not Commit or causal outcome.

`run_start`, `episode_identity`, `capabilities`, `runtime_identity` preserve
teacher parameters/source hashes, run seed/template, exact game/install/runtime
identity, original Driver provenance, compiled Runtime digest and generated
manifest. The existing AgentRunEvidence writer additionally records admitted
Runtime decisions, submit results and lifecycle. No raw files enter Git.

Every exit closes the owning episode. Runtime Stop precedes a fresh control GET
that must show the exact runtime, a client list and null/omitted controller
(the native serializer omits null fields); then the child adapter
and Driver close. `summary.json` preserves the explicit termination reason,
release confirmation and errors. Unknown termination may have confirmed control
release but remains unknown; it is not a successful native action claim.

New runs also store the exact `episode.close()` result in the additive
`native_exit` field of `run_end` and `summary.json`. The versioned wrapper binds
the owning runtime and actual close-return time; its receipt preserves the
Host's exit code/signal, `forced` flag and Host shutdown response without
normalizing a forced or nonzero exit into a graceful one. A missing return is
`not_reported`, a thrown close is `close_failed`, and an episode never returned
by startup is `episode_not_returned`. Missing new native exit evidence fails the
CLI; control release alone never proves process exit. These are closure metadata,
not additional policy input or training steps.

The dataset verifier accepts old runs with both fields absent and makes no exit
claim for them; it does not backfill historical receipts. When the new receipt
is present, summary/ledger, runtime, process tuple and shutdown identity must
agree. Forced exit remains explicitly forced in derived provenance. Validating
recorded exit structure/identity does not itself authenticate a producer or
qualify a clean native shutdown; the owner inspects the exact native evidence.

Tests exercise public choice/browse, exact capsule joins, corrupt/over-budget
capsules, capability binding, unknown no-retry, Stop release and failure cleanup.
The Stop check uses the SDK's `controlSnapshot()` strict decoder and canonical
GET `/api/player-environment/controller` route. Build the SDK before these tests;
the production helper is exercised through the actual SDK with a fetch fixture
that asserts this route, not a duplicated mock control implementation.
They are portable source tests; real installed/load/game/training qualification
belongs to the lead's exact native execution packet.
