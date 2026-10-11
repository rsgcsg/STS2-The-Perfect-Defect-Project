# Native Foundation Architecture Example Suite

This suite records which architecture questions are answered at each evidence
level. It is not a gameplay-content qualification matrix. `predecessor T3`
means the PR #3 artifact proved the mechanism before this refactor; those bytes
do not prove the new candidate.

| Example | Semantic owner and action source | Presentation and exact delivery | Lifecycle / next decision | Current evidence and result |
|---|---|---|---|---|
| ordinary and targeted card | logical hand plus `CardModel.CanPlayTargeting` | visible hand intersect; exact card/target; `TryManualPlay` | `GameAction` lifecycle; later complete semantic boundary | T0/T1 pass; predecessor T3 pass; new T3 pending |
| potion no-target/self/enemy | potion slots plus native usage/custom/target checks | visible usable slot intersect; `EnqueueManualUse` | exact `UsePotionAction`; cancel before enqueue is not success | T0/T1 pass; predecessor representative T3 pass; new T3 pending |
| Survivor/discard | executing parent action and native choice task | exact hand-selection controls | parent pause/ready/resume; same-root continuation | T0/T1 lineage pass; new T3 pending |
| generated card choice | executing parent action and option owner | exact generated-choice options/control | parent continuation, not a new root | T0/T1 lineage pass; predecessor T3 pass; new T3 pending |
| rapid accepted card chain | semantic decision is logical state at execution, not staged UI | presentation can temporarily omit an accepted root | execution order controls causal sequence | predecessor schema-2 T3 pass; refactor regression T1 pass; new T3 pending |
| accepted then cancelled/aborted | accepted input is provenance only | no delivery rewrite | cancelled/abort disposition cannot create successful transition | T0/T1 pass; reproducible new T3 pending |
| End Turn cycle | local play phase owns End Turn | exact room/player/hand/button/combat state, enabled not-Undo and seen-FTUE guards; completed `CallReleaseLogic` gesture requests native captured-turn `EndPlayerTurnAction` after earlier queued inputs | input acceptance is not enqueue, execution or next turn; later play phase proves next decision | corrected queue/ABI/guard T0/T1 candidate; predecessor direct-route T3 does not qualify this correction; new build/runtime pending |
| Map route and annotation | `RunState` map graph plus `MapTravel`; annotation is presentation-only | native `MapPoint` intersected with exact map node or drawing-input stop | room transition proves next domain | typed provider T0/T1 and exact clean build pass; T2/T3 pending |
| Shop purchase/removal | merchant inventory entries; nested removal continuation | exact entry/slot/back/proceed controls | purchase task then refreshed inventory or nested choice | T0 audit; shared semantic provider and new runtime pending |
| Reward/CardReward/Map | exact `RewardsSet` -> exact card/alternative arrays -> `RunState` map | native subjects intersected with exact reward/holder/proceed/map delivery controls | owner changes are explicit; no immediate-observe causal claim | typed providers T0/T1 and exact clean build pass; T2/T3 pending |
| Treasure | exact `TreasureRoom` lifecycle plus `TreasureRoomRelicSynchronizer` options/vote | native room/relic subjects intersected with chest/holder/proceed controls | open -> collection -> claim/skip -> completed/Map | typed provider T0/T1 pass; final T2/T3 pending |
| Event/Rest | each current room model/choice owner | typed source-local controls | domain task/room transition | T0 route audit; migration and final runtime pending |
| run entry and GameOver | lobby/run manager and terminal history | exact menu/character/game-over controls | first room decision; terminal has no gameplay successor | existing contract tests; final T2/T3 pending |
| visible vs headless-like projection | one canonical native decision | presentation may expose different deliverability | lifecycle meaning unchanged | deterministic T1 pass; shipped normal-vs-headless T2 pending |
| Direct versus Ritsu | Platform-owned semantic normalization | public projection remains Ritsu-free | Ritsu lacks required exact generic action/lineage/next-decision seams | source/API T0 decisive for rejection; no runtime A/B claim |

The suite supports an unchanged Player Environment `1.0.0` public shape. The
internal derivation is now:

```text
native semantic decision
+ fair-player facts
+ current presentation deliverability
-> finite BoundActions
-> exact process-local revalidation and delivery
```

`Receipt.Successor` remains an immediate post-delivery observation. A causal
next decision is a separate semantic concept and is not added to the public
protocol until at least two domains prove a stable identity and a consumer
needs it.
