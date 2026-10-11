# Native Seam Matrix

This matrix records the exact STS2 `v0.111.0` ownership conclusions used by
Native Foundation. It is a migration artifact, not a coverage or qualification
claim. Native type and method names are conclusions from the exact local game
assembly; proprietary decompiled source is not part of this repository.

Evidence levels are `T0` source/assembly, `T1` deterministic test, `T2` exact
build/load automated runtime, and `T3` Human runtime. Evidence from the PR #3
predecessor artifact does not transfer to a new Native Foundation artifact.

## Combat

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | `CombatManager`, local `PlayerCombatState`, `ActionQueueSynchronizer` |
| State source | logical hand, energy/resources, potion slots, combat phase and current combat participants |
| Semantic action source | `NativeCombatDecisionProvider` enumerates logical hand cards, potions and End Turn |
| Native validator | `CardModel.CanPlayTargeting`; potion ownership, usage, custom usability and `IsValidTarget`; native play phase |
| Decision boundary | local player play phase with game action synchronization in `PlayPhase` |
| Presentation owner | `NCombatRoom`, `NPlayerHand`, potion holders and `NEndTurnButton` |
| Delivery seam | `CardModel.TryManualPlay`, `PotionModel.EnqueueManualUse`, exact `NEndTurnButton.CallReleaseLogic` completed logical gesture -> native captured-turn `EndPlayerTurnAction` request |
| Exact binding | process-local card, potion, target and room objects behind Connector referents; End Turn also binds current player/hand/button/combat state, enabled play readiness, not-ready Undo guard and read-only seen-FTUE gate |
| Lifecycle | exact `GameAction` accepted/start/pause/resume/cancel/finish observations |
| Next-decision seam | typed player-side `TurnStarted` after semantic `Play`/executor unpause/synchronizer `PlayPhase` and the exact `NEndTurnButton.OnTurnStarted` input-owner callback; Connector must independently capture a complete matching frame |
| Root/continuation | card, potion and End Turn are roots; nested choices remain descendants of the executing root |
| Current workaround | presentation readiness still uses hand/control state before intersecting the semantic catalog |
| Heuristic debt | End Turn deliverability depends on the native button; causal `S'` remains Annotator evidence, not Foundation action publication |
| Ritsu support | card/potion/combat lifecycle events exist, but no generic exact action timeline or causal next-decision replacement was found |
| Missing evidence | owner-ready repair artifact T2/T3, including Map Commit -> Combat ready -> no next Human input -> Close |
| Migration verdict | Direct Native Foundation implemented; presentation and exact delivery remain Connector-owned |

End Turn preserves native player-input queue ordering rather than invoking
`PlayerCmd.EndTurn` directly ahead of waiting inputs. Its gesture completion is
the same native factory used by the long-press bar; no physical signal or hold
duration is fabricated. Unseen/unknown playable-card FTUE fails the current input
owner explicitly unsupported/partial before authority. This does not authorize
FTUE/save/preference mutation, predict queued card legality or identify a
historical run's timeout cause. Exact source/ABI/queue tests, clean build and later
loaded runtime evidence remain separate; acceptance is not enqueue/Commit proof.
The standalone queue regression uses the pinned native queue and native queued
End Turn type with synthetic held action tasks; only native logger initialization
is isolated from absent Godot OS services. It verifies queue ordering/selection,
not executing End Turn effects. The cancellation branch is exact ABI/IL evidence:
native `GameAction.Cancel` requires Godot deferred completion, so cancellation
execution remains a separate Godot-backed gate. Neither method nor its effects
are patched or simulated to obtain a passing standalone check.

## PlayerChoice

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | the currently executing parent `GameAction` and its `PlayerChoiceContext`/branching continuation |
| State source | parent action, active choice task and domain-specific native choice collection |
| Semantic action source | domain-specific choice owner; no universal choice catalog is claimed |
| Native validator | the owning choice task and its exact selectable membership/confirmation rules |
| Decision boundary | parent action pauses for a player choice and later becomes ready/resumes |
| Presentation owner | generated-card, hand-selection or other typed choice screen |
| Delivery seam | existing typed native choice callbacks and controls |
| Exact binding | process-local parent action plus exact option/control object |
| Lifecycle | parent pause/ready/resume/cancel/finish; Foundation exposes parent lineage read-only |
| Next-decision seam | choice commit resumes the same root; only a later complete semantic boundary proves the next root decision |
| Root/continuation | continuation of the executing root, never an unrelated new root |
| Current workaround | each supported choice family still owns its presentation and membership adapter |
| Heuristic debt | generic choice action enumeration is not yet centralized; visible screen state cannot define parent lineage |
| Ritsu support | no exact parent/continuation lineage API found in `v0.5.18` or audited dev source |
| Missing evidence | final artifact Survivor/discard and generated-choice T3 |
| Migration verdict | shared lineage implemented; retain typed source-local choice adapters |

## Map

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | `RunState` map graph, current `MapPoint`, visited coordinates and native `MapTravel` rule |
| State source | current `ActMap`, `CurrentMapPoint`, `VisitedMapCoords`, starting/boss points and graph children |
| Semantic action source | `NativeMapDecisionProvider` calls `MapTravel.GetTravelablePointsFrom` or selects the game-owned starting/boss transition point |
| Native validator | game-owned destination membership; Connector separately rechecks current `NMapPoint.State` and input readiness before delivery |
| Decision boundary | map is open, travel is enabled, no travel is active and no annotation mode owns input |
| Presentation owner | `NMapScreen`, `NMapPoint`, `NMapDrawingInput` and current controller mode |
| Delivery seam | `NMapScreen.OnMapPointSelectedLocally`; annotation stop on the exact drawing input |
| Exact binding | public referent binds the process-local `MapPoint`; Connector resolves one current `NMapPoint` only for delivery |
| Lifecycle | exact `VoteForMapCoordAction` lifecycle proves Commit; room transition remains separate |
| Next-decision seam | a proved typed owner-ready publisher (currently Combat player-turn only), another explicit map decision, or next-root execution pre; room entry and `Finished` alone are not `S'` |
| Root/continuation | route selection is a root; annotation is a non-gameplay UI continuation |
| Current workaround | private `_isInputDisabled`/`_drawingInput`, FTUE and controller on-screen filtering remain delivery readiness only |
| Heuristic debt | non-Combat room owner-ready publishers remain unproved; map annotation is presentation-only |
| Ritsu support | map-generation and room lifecycle helpers, not exact route-choice authority |
| Missing evidence | continuation artifact install/load and representative map T2/T3 |
| Migration verdict | typed native decision adapter implemented; old UI reachability authority removed |

## Merchant / Shop

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | current `MerchantInventory` and its card/relic/potion/removal entries |
| State source | stocked entries, prices, local player gold/capacity and removal state |
| Semantic action source | `NativeRoomDecisionProvider` projects the current room controls and inventory entries; delivery still rechecks visible enabled controls |
| Native validator | entry stock/affordability and `OnTryPurchaseWrapper` domain validation |
| Decision boundary | current merchant room/inventory and no purchase/selection transition in progress |
| Presentation owner | merchant room button, inventory screen slots/back button and room Proceed |
| Delivery seam | entry `OnTryPurchaseWrapper`; exact room/open/back/proceed controls |
| Exact binding | inventory plus exact entry/slot/removal object |
| Lifecycle | purchase task and any nested removal choice; room open/proceed and inventory close are exact native callbacks |
| Next-decision seam | refreshed inventory, nested removal decision, or room/map transition |
| Root/continuation | purchase is a root; card removal selection is a continuation |
| Current workaround | `CanPurchase` mixes stock/gold with hitbox visibility/enabled state |
| Heuristic debt | UI slot actionability remains a delivery-time guard; semantic membership comes from the STS2-owned room/inventory catalog |
| Ritsu support | high-level merchant purchase lifecycle, but not exact entry binding or nested removal lineage |
| Missing evidence | representative final-candidate shop open/purchase/removal/close/proceed T3 and nested selector outcomes |
| Migration verdict | typed read-only room/inventory provider and exact entry/task witnesses implemented; final Human qualification remains pending |

## Reward

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | current room reward set and each native `Reward` object |
| State source | unclaimed rewards, potion capacity, linked reward sets and reward-screen progression |
| Semantic action source | `NativeRewardDecisionProvider` projects unselected native rewards, full-belt potion discard operands and native proceed policy from the exact `RewardsSet` |
| Native validator | `Reward.SuccessfullySelected`, potion slots/`CanUseOrRemovePotions`, `RewardsSet.DisallowSkipping`/`AllRewardsSuccessfullySelected`, and `Hook.ShouldProceedToNextMapPoint`; linked sets remain explicit unsupported |
| Decision boundary | rewards overlay owns input and has at least one exact claim/discard/proceed operation |
| Presentation owner | `NRewardsScreen`, `NRewardButton`, potion popup and `NProceedButton` |
| Delivery seam | exact reward button callback, `DiscardPotionGameAction`, or Proceed control |
| Exact binding | public referent binds the process-local `Reward` or `PotionModel`; Connector resolves one current button/slot only for delivery |
| Lifecycle | non-nested claim/proceed uses exact bound Task completion; a CardReward claim commits when exact `ShowScreen` owner creation opens the child decision, while the parent Task remains a later business completion |
| Next-decision seam | CardReward owner, remaining reward set, Map owner, or next-root execution pre; Task completion alone is not `S'` |
| Root/continuation | claim/proceed are roots; nested reward choices are continuations |
| Current workaround | visible/enabled buttons and popup slots remain current delivery readiness; linked sets fail closed |
| Heuristic debt | reward-specific `OnSelect` may still return false at native Commit; Receipt remains delivery-only and no business Outcome is inferred |
| Ritsu support | reward-taken and rewards-screen continuation events, not complete action enumeration/binding |
| Missing evidence | continuation artifact install/load and final `lethal -> Reward` T2/T3 |
| Migration verdict | typed native decision adapter implemented; visible reward buttons no longer create semantic membership |

## CardReward

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | native card-reward option collection and alternative reward choices |
| State source | offered cards, alternatives and skip/proceed policy of the active reward |
| Semantic action source | `NativeCardRewardDecisionProvider` projects the exact `CardCreationResult` and `CardRewardAlternative` arrays supplied to `ShowScreen`/`RefreshOptions` |
| Native validator | exact current native option membership; current holder/button remains a delivery check only |
| Decision boundary | `NCardRewardSelectionScreen` is current and one exact option is actionable |
| Presentation owner | card-reward screen, `NCardHolder` and alternative buttons |
| Delivery seam | holder `Pressed` signal or exact alternative button callback |
| Exact binding | public referent binds the exact `CardModel` or `CardRewardAlternative`; Connector maps it to one current holder/button for delivery |
| Lifecycle | the exact selection completion source proves Commit and resolves the reward continuation |
| Next-decision seam | remaining Reward owner, Map owner, or next-root execution pre; the selection callback does not itself prove `S'` |
| Root/continuation | continuation of the reward claim that opened it |
| Current workaround | private `_isClickable` and enabled alternative buttons remain temporary delivery readiness |
| Heuristic debt | parent reward root identity is not yet a generic Foundation lineage contract; the typed continuation owner is exact |
| Ritsu support | no exact card-reward option catalog/parent lineage replacement found |
| Missing evidence | continuation artifact install/load and representative CardReward T2/T3 |
| Migration verdict | typed continuation adapter implemented; holders/buttons no longer create semantic membership |

## Event

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | current event model/state and its current option/proceed phase |
| State source | event state, current option set and visible event text |
| Semantic action source | native event options; current Platform enumerates visible `NEventOptionButton` controls |
| Native validator | option state/availability and event-owned transition callback |
| Decision boundary | event room owns input and an option, proceed or dialogue-advance control is current |
| Presentation owner | event option buttons, dialogue hitbox and room Proceed |
| Delivery seam | exact option/proceed button or dialogue hitbox callback |
| Exact binding | room/event owner plus exact option/control object |
| Lifecycle | event task/state transition, possible nested selector, room completion |
| Next-decision seam | next event phase, nested choice, Map, or terminal owner |
| Root/continuation | event option is a root; dialogue and nested choices are continuations |
| Current workaround | visible/enabled option controls define action publication |
| Heuristic debt | event task/state successor coverage still requires a final-candidate canary |
| Ritsu support | room/event lifecycle coverage is useful, but no exact generic option authority was found |
| Missing evidence | final-candidate option/proceed/nested-selector T3 |
| Migration verdict | typed read-only event catalog and exact option-task witness implemented; final Human qualification remains pending |

## Rest

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | current rest-site option collection and local player/run state |
| State source | available `RestSiteOption` objects, used/disabled state and room progression |
| Semantic action source | native rest-site options; current Platform discovers bound option buttons |
| Native validator | option availability and native option execution callback |
| Decision boundary | current rest-site room has an enabled option or exact Proceed control |
| Presentation owner | rest-site option buttons and room Proceed |
| Delivery seam | exact option button or Proceed callback |
| Exact binding | room plus exact option model/control identity |
| Lifecycle | option task/effect and room progression |
| Next-decision seam | nested selection, remaining rest phase, or Map |
| Root/continuation | rest option is a root; upgrade/other selectors are continuations |
| Current workaround | button enablement and visibility publish options |
| Heuristic debt | model availability is projected from native options; nested successor coverage still needs a final-candidate canary |
| Ritsu support | rest heal/smith hooks, not complete option authority or nested lineage |
| Missing evidence | final-candidate option/proceed/nested-selector T3 |
| Migration verdict | typed read-only rest catalog and exact synchronizer/proceed witnesses implemented; final Human qualification remains pending |

## Treasure

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | exact `TreasureRoom`/`IRunState` pair registered by `NTreasureRoom.Create`, plus the current `TreasureRoomRelicSynchronizer` collection |
| State source | room-owned chest-open/collection lifecycle, pre-generated `CurrentRelics`, exact `OnPicked` local-player Commit observation and room progression |
| Semantic action source | `NativeTreasureDecisionProvider` projects `open`, exact relic `select`, `skip` and `proceed` from those game-owned operands |
| Native validator | current room identity/stage, exact relic reference membership and committed local vote; Connector separately rechecks the current delivery control |
| Decision boundary | provider stage is `closed`, `opening`, `relic_choice`, `resolving` or `completed`; only stage-valid operations are semantic candidates |
| Presentation owner | chest control, relic holders and `NProceedButton` |
| Delivery seam | exact chest/holder/proceed callback |
| Exact binding | public room/relic referents bind the process-local `TreasureRoom`/`RelicModel`; UI nodes stay Host-local delivery operands |
| Lifecycle | bound chest/proceed Task or exact `PickRelicAction` completion proves Commit |
| Next-decision seam | relic-choice/completed Treasure owner, Map owner, or next-root execution pre; completion alone is not `S'` |
| Root/continuation | open is a root; relic choice is its continuation; proceed starts room transition |
| Current workaround | removed as semantic authority; visible controls only intersect the provider catalog for delivery |
| Heuristic debt | exact private lifecycle fields remain version-bound read-only inputs; `CurrentRelics` is pre-generated at room entry and cannot prove chest opening; predicted `GetPlayerVote` state cannot prove relic Commit |
| Ritsu support | treasure generation hooks are not an exact claim/continuation contract |
| Missing evidence | final continuation artifact T2 cold-load and representative T3 open/select/skip/proceed exercise |
| Migration verdict | typed Native Foundation provider implemented at T0/T1; Connector presentation adapter retained only for delivery |

## Run Entry / Room And Act Transition

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | `StartRunLobby`, `RunManager`, `RunState` and native room/act lifecycle |
| State source | selected character/run configuration, active run identity, room and act transition state |
| Semantic action source | lobby choices before start; current room's domain adapter after start |
| Native validator | lobby/run setup constraints and game-owned room/act transition rules |
| Decision boundary | exact menu/lobby owner before run, then the current domain owner inside the run |
| Presentation owner | main/single-player/character-select/tutorial screens, then room/overlay controls |
| Delivery seam | exact menu/select/embark callbacks; game-owned room transition thereafter |
| Exact binding | menu/lobby/character object or current run/room identity |
| Lifecycle | run create/resume, room enter/exit and act transition |
| Next-decision seam | first room decision or the next domain owner after transition |
| Root/continuation | run entry is lifecycle control expressed through player UI; room decisions remain domain roots |
| Current workaround | several menu controls and private lobby binding define publication |
| Heuristic debt | run lifecycle and gameplay decision ownership are not yet represented by one neutral adapter |
| Ritsu support | broad room/act lifecycle and identity helpers, but no replacement for exact Platform action authority |
| Missing evidence | final artifact run-entry T2 and room/act transition T3 |
| Migration verdict | retain typed menu adapters; add lifecycle observations without moving process ownership from Host Runtime |

## Terminal / GameOver

| Field | Exact-version conclusion |
|---|---|
| Semantic owner | `RunManager.History`, terminal run state and `NGameOverScreen` stage |
| State source | win/loss, score/summary and terminal navigation state |
| Semantic action source | terminal summary advance/return controls; there is no gameplay successor after terminal |
| Native validator | current game-over stage and exact enabled control |
| Decision boundary | exact game-over overlay owns input and is at intro or summary stage |
| Presentation owner | `NGameOverScreen`, Continue and return controls |
| Delivery seam | exact Continue or return button callback |
| Exact binding | game-over screen plus stage-specific control |
| Lifecycle | summary animation/advance and return-to-menu/timeline |
| Next-decision seam | none for gameplay terminal; later menu state is a new lifecycle context |
| Root/continuation | terminal navigation, not a gameplay continuation |
| Current workaround | private score/animation fields and enabled controls classify stage |
| Heuristic debt | terminal semantic state and presentation stage are still co-located in one reader |
| Ritsu support | no exact terminal Player Environment replacement identified |
| Missing evidence | final artifact T2/T3 terminal journey |
| Migration verdict | typed terminal adapter retained; neutral terminal observation pending |

## Cross-Cutting Verdict

`Surface` is presentation classification. `NativeDecision` is semantic truth.
`current_ui_owned` means that the current UI owns input, never that it owns all
game legality. A decision-time capture may publish actions; execution-time
binding must resolve the same process-local operands and re-run native checks.
Only Connector BoundActions authorize mutation. Annotator consumes lifecycle
and decision facts read-only. Host Runtime owns process lifecycle only.
