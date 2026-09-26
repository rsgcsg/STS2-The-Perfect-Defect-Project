using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

/// <summary>
/// The current recording contract. The <c>*-2</c> wire names are retained as
/// evidence identities; they describe one current format rather than a
/// parallel V2 product.
/// </summary>
public static class CurrentRecordingContract
{
    public const string ProductVersion = "0.3.0-rc.9";
    public const int SchemaVersion = 2;
    public const string RecordSchema = "sts2.human-annotator/decision-record-2";
    public const string ManifestSchema = "sts2.human-annotator/recording-manifest-2";
    public const string InvalidationSchema = "sts2.human-annotator/invalidation-2";
    public const string CoverageSchema = "sts2.human-annotator/coverage-2";
    public const string RuntimeStatusSchema = "sts2.human-annotator/runtime-status-2";
    public const string CaptureProfileSchema = "sts2.ai-platform/human-capture-profile-2";
    public const string ReadEvidenceSchema = "sts2.human-annotator/read-evidence-2";
    public const string RunJournalSchema = "sts2.human-annotator/run-journal-event-2";
    public const string SessionBundleSchema = "sts2.human-annotator/session-bundle-2";
    public const string SessionBundleAuditSchema = "sts2.human-annotator/session-bundle-audit-2";
}

public sealed record CaptureReadRequirement(
    string Phase,
    string Kind,
    bool Required,
    string? InteractionKind = null);

public sealed record HumanCaptureProfile(
    int SchemaVersion,
    string Schema,
    string ProfileId,
    string RecordSchema,
    IReadOnlyList<string> SupportedActionFamilies,
    IReadOnlyList<CaptureReadRequirement> Reads,
    IReadOnlyList<string> NonClaims);

public static class FullRunCoverageClassifications
{
    public const string InScopeImplemented = "IN_SCOPE_IMPLEMENTED";
    public const string NotAPlayerDecisionWithNativeJustification =
        "NOT_A_PLAYER_DECISION_WITH_NATIVE_JUSTIFICATION";
    public const string OutOfScopeWithJustification = "OUT_OF_SCOPE_WITH_JUSTIFICATION";
    public const string Blocked = "BLOCKED";
}

/// <summary>
/// Machine-checkable recording coverage map. This is deliberately a map of
/// witness/recording closure, not a legality table or an input authority.
/// </summary>
public sealed record FullRunCoverageEntry(
    string Family,
    string Classification,
    string UiInput,
    string NativeOwner,
    string SemanticProvider,
    string AcceptedSeam,
    string LifecycleCommit,
    string NextAuthoritativeBoundary,
    string? Justification = null);

public static class FullRunCoverageContract
{
    public const string Schema = "sts2.human-annotator/full-run-coverage-1";

    public static IReadOnlyList<FullRunCoverageEntry> Entries { get; } =
        new FullRunCoverageEntry[]
        {
            new("ordinary_combat.play_card", FullRunCoverageClassifications.InScopeImplemented,
                "NCardPlay.TryPlayCard", "NCardPlay", "NativeCombatDecisionProvider",
                "GameAction.OnEnqueued (PlayCardAction)", "PlayCardAction lifecycle + native OnPlay commit",
                "next complete interactive combat boundary"),
            new("ordinary_combat.end_turn", FullRunCoverageClassifications.InScopeImplemented,
                "NEndTurnButton.OnRelease", "NEndTurnButton", "NativeCombatDecisionProvider",
                "EndPlayerTurnAction.OnEnqueued", "EndPlayerTurnAction native lifecycle",
                "next complete interactive combat boundary"),
            new("ordinary_combat.use_potion", FullRunCoverageClassifications.InScopeImplemented,
                "NPotionHolder.UsePotion/EnqueueManualUse", "NPotionHolder/PotionModel",
                "NativePotionUseDecisionProvider (combat or non-combat AnyTime)", "UsePotionAction enqueue", "UsePotionAction native lifecycle",
                "next exact native owner-ready boundary; legacy family name retained"),
            new("combat_hand_selector.select", FullRunCoverageClassifications.InScopeImplemented,
                "NPlayerHand.SelectCardIn*Mode", "NPlayerHand", "NativeCombatDecisionProvider",
                "NPlayerHand.SelectCardIn*Mode", "selector confirm/owner callback",
                "next complete interactive combat boundary"),
            new("combat_hand_selector.deselect", FullRunCoverageClassifications.InScopeImplemented,
                "NSelectedHandCardContainer.DeselectHolder", "NSelectedHandCardContainer",
                "NativeCombatDecisionProvider", "NSelectedHandCardContainer.DeselectHolder",
                "selector confirm/owner callback", "next complete interactive combat boundary"),
            new("combat_hand_selector.confirm", FullRunCoverageClassifications.InScopeImplemented,
                "NPlayerHand.OnSelectModeConfirmButtonPressed", "NPlayerHand",
                "NativeCombatDecisionProvider", "NPlayerHand.OnSelectModeConfirmButtonPressed",
                "selector owner completion", "next complete interactive boundary"),
            new("native_generated_card_choice.select", FullRunCoverageClassifications.InScopeImplemented,
                "NChooseACardSelectionScreen.SelectHolder", "NChooseACardSelectionScreen",
                "NativeGeneratedChoice provider", "NChooseACardSelectionScreen.SelectHolder",
                "selector completion + PlayerChoice continuation", "next exact semantic boundary"),
            new("native_generated_card_choice.skip", FullRunCoverageClassifications.InScopeImplemented,
                "NChooseACardSelectionScreen.OnSkipButtonReleased", "NChooseACardSelectionScreen",
                "NativeGeneratedChoice provider", "NChooseACardSelectionScreen.OnSkipButtonReleased",
                "selector completion + PlayerChoice continuation", "next exact semantic boundary"),
            new("boss_relic.select", FullRunCoverageClassifications.InScopeImplemented,
                "NRelicBasicHolder.Released -> NChooseARelicSelection.SelectHolder",
                "NChooseARelicSelection", "NativeBossRelicDecisionProvider",
                "NChooseARelicSelection.SelectHolder",
                "PlayerChoiceSynchronizer.SyncLocalChoice",
                "RunManager.ActEntered only when a later native act transition occurs; otherwise parent PlayerChoice continuation"),
            new("boss_relic.skip", FullRunCoverageClassifications.InScopeImplemented,
                "NChoiceSelectionSkipButton.Released -> NChooseARelicSelection.OnSkipButtonReleased",
                "NChooseARelicSelection", "NativeBossRelicDecisionProvider",
                "NChooseARelicSelection.OnSkipButtonReleased",
                "PlayerChoiceSynchronizer.SyncLocalChoice (empty result)",
                "parent PlayerChoice continuation; no inferred successor"),
            new("map_navigation.travel", FullRunCoverageClassifications.InScopeImplemented,
                "NMapScreen.OnMapPointSelectedLocally", "NMapScreen", "NativeMapDecisionProvider",
                "VoteForMapCoordAction enqueue", "VoteForMapCoordAction native lifecycle",
                "next exact map boundary"),
            new("reward_claim.claim", FullRunCoverageClassifications.InScopeImplemented,
                "NRewardButton.OnRelease", "NRewardButton", "NativeRewardDecisionProvider",
                "NRewardButton.OnRelease", "SelectLocalReward or card-selection owner",
                "next exact reward boundary"),
            new("reward_claim.proceed", FullRunCoverageClassifications.InScopeImplemented,
                "NRewardsScreen.OnProceedButtonPressed", "NRewardsScreen", "NativeRewardDecisionProvider",
                "NRewardsScreen.OnProceedButtonPressed", "RunManager.ProceedFromTerminalRewardsScreen or reward skip",
                "next exact reward/room boundary"),
            new("act_change.ready", FullRunCoverageClassifications.InScopeImplemented,
                "NRewardsScreen.OnProceedButtonPressed terminal boss/victory branch",
                "ActChangeSynchronizer/ActionQueueSynchronizer", "NativeActChangeDecisionProvider",
                "ActChangeSynchronizer.SetLocalPlayerReady -> RequestEnqueue(VoteToMoveToNextActAction)",
                "VoteToMoveToNextActAction.ExecuteAction -> OnPlayerReady",
                "RunManager.ActEntered only after all native readiness votes; no successor is guessed"),
            new("card_reward_selection.select", FullRunCoverageClassifications.InScopeImplemented,
                "NCardRewardSelectionScreen.SelectCard", "NCardRewardSelectionScreen",
                "NativeCardRewardDecisionProvider", "NCardRewardSelectionScreen.SelectCard",
                "card reward owner completion", "next exact reward boundary"),
            new("treasure_room.open", FullRunCoverageClassifications.InScopeImplemented,
                "NTreasureRoom.OnChestButtonReleased", "NTreasureRoom", "NativeTreasureDecisionProvider",
                "NTreasureRoom.OnChestButtonReleased", "OneOffSynchronizer.DoLocalTreasureRoomRewards",
                "next exact treasure/reward boundary"),
            new("treasure_room.select", FullRunCoverageClassifications.InScopeImplemented,
                "NTreasureRoomRelicCollection.PickRelic", "NTreasureRoomRelicCollection",
                "NativeTreasureDecisionProvider", "PickRelicAction enqueue", "PickRelicAction native lifecycle",
                "next exact treasure boundary"),
            new("treasure_room.skip", FullRunCoverageClassifications.InScopeImplemented,
                "NTreasureRoom.OnProceedButtonPressed skip", "NTreasureRoom", "NativeTreasureDecisionProvider",
                "NTreasureRoom.OnProceedButtonPressed", "PickRelicAction/treasure continuation",
                "next exact treasure boundary"),
            new("treasure_room.proceed", FullRunCoverageClassifications.InScopeImplemented,
                "NTreasureRoom.OnProceedButtonPressed proceed", "NTreasureRoom", "NativeTreasureDecisionProvider",
                "NTreasureRoom.OnProceedButtonPressed", "RunManager.ProceedFromTerminalRewardsScreen",
                "next exact terminal boundary"),
            new("event_option.choose", FullRunCoverageClassifications.InScopeImplemented,
                "NEventRoom.OptionButtonClicked", "NEventRoom/EventOption", "NativeRoomDecisionProvider",
                "NEventRoom.OptionButtonClicked -> EventOption.Chosen", "EventOption.Chosen task completion",
                "next exact event boundary"),
            new("event_option.proceed", FullRunCoverageClassifications.InScopeImplemented,
                "NEventRoom.OptionButtonClicked proceed", "NEventRoom/EventOption", "NativeRoomDecisionProvider",
                "NEventRoom.OptionButtonClicked -> EventOption.Chosen", "exact synchronous Chosen return or task completion",
                "exact NEventRoom.Proceed map-owner return or next Human execution boundary"),
            new("shop_room.open", FullRunCoverageClassifications.InScopeImplemented,
                "NMerchantRoom.OpenInventory", "NMerchantRoom", "NativeRoomDecisionProvider",
                "NMerchantRoom.OpenInventory", "inventory owner open", "next shop inventory boundary"),
            new("shop_room.proceed", FullRunCoverageClassifications.InScopeImplemented,
                "NMerchantRoom.HideScreen", "NMerchantRoom", "NativeRoomDecisionProvider",
                "NMerchantRoom.HideScreen", "shop room handoff", "next exact room boundary"),
            new("shop_inventory.purchase", FullRunCoverageClassifications.InScopeImplemented,
                "MerchantEntry.OnTryPurchaseWrapper", "MerchantEntry/NMerchantInventory",
                "NativeRoomDecisionProvider", "MerchantEntry.OnTryPurchaseWrapper", "purchase task completion",
                "next exact shop boundary"),
            new("shop_inventory.card_removal", FullRunCoverageClassifications.InScopeImplemented,
                "MerchantCardRemovalEntry.OnTryPurchaseWrapper", "MerchantCardRemovalEntry/NMerchantInventory",
                "NativeRoomDecisionProvider", "MerchantCardRemovalEntry.OnTryPurchaseWrapper(MerchantInventory?,bool,bool)",
                "outer removal Task retains the final parent disposition",
                "screen-keyed child continuation is durable on the same parent root"),
            new("shop_inventory.close", FullRunCoverageClassifications.InScopeImplemented,
                "NMerchantInventory.Close", "NMerchantInventory", "NativeRoomDecisionProvider",
                "NMerchantInventory.Close", "inventory close", "next exact shop boundary"),
            new("rest_site.choose", FullRunCoverageClassifications.InScopeImplemented,
                "NRestSiteButton.OnRelease -> RestSiteSynchronizer.ChooseLocalOption",
                "RestSiteSynchronizer/RestSiteOption", "NativeRoomDecisionProvider",
                "RestSiteSynchronizer.ChooseLocalOption", "ChooseLocalOption task completion",
                "next exact rest-site boundary"),
            new("rest_site.proceed", FullRunCoverageClassifications.InScopeImplemented,
                "NRestSiteRoom.OnProceedButtonReleased", "NRestSiteRoom", "NativeRoomDecisionProvider",
                "NRestSiteRoom.OnProceedButtonReleased", "room handoff", "next exact room boundary"),
            new("potion_belt.discard", FullRunCoverageClassifications.InScopeImplemented,
                "NPotionPopup.OnDiscardButtonPressed(NButton)", "NPotionPopup/DiscardPotionGameAction",
                "PotionPopupSurfaceReader native enabled controls", "exact popup potion and native action carrier",
                "DiscardPotionGameAction.ExecuteAction task with native cancellation check",
                "exact next decision boundary", "Independent discard in every native allowed room; no implied reward claim."),
            new("reward_potion_belt.discard_replace", FullRunCoverageClassifications.InScopeImplemented,
                "NPotionPopup.OnDiscardButtonPressed(NButton)",
                "NPotionPopup -> ActionQueueSynchronizer.RequestEnqueue(DiscardPotionGameAction)",
                "NativeRewardDecisionProvider (full belt discard actions)",
                "NPotionPopup.OnDiscardButtonPressed + exact current potion action binding",
                "DiscardPotionGameAction.ExecuteAction -> PotionCmd.Discard",
                "NRewardsScreen reward set after exact potion-slot post-state",
                "Full potion belt replacement is a real player decision; the popup cancel path has no mutation."),
            new("reward_potion_belt.cancel", FullRunCoverageClassifications.NotAPlayerDecisionWithNativeJustification,
                "NPotionPopup._Input(cancel/pauseAndBack/outside click)", "NPotionPopup", "none",
                "none: NPotionPopup.Remove", "no native reward/potion mutation",
                "outer NRewardsScreen remains authoritative",
                "Cancel closes the popup without enqueueing DiscardPotionGameAction; it is not an accepted player decision."),
            new("reward_nested.card_selection", FullRunCoverageClassifications.InScopeImplemented,
                "NCardRewardSelectionScreen.SelectCard", "NCardRewardSelectionScreen",
                "NativeCardRewardDecisionProvider", "NCardRewardSelectionScreen.SelectCard",
                "card reward owner completion", "next exact reward boundary",
                "The card-reward child has an exact native screen owner and parent reward binding."),
            new("nested_selector.decision", FullRunCoverageClassifications.InScopeImplemented,
                "exact selector input callback", "screen or hand episode", "Connector complete selector catalog",
                "exact native accepted selection/control callback", "native selector state mutation or completion",
                "same owner next state or exact next decision boundary", "Versioned decision occurrence; no second native root."),
            new("reward_nested.replacement_selection", FullRunCoverageClassifications.InScopeImplemented,
                "NCardRewardSelectionScreen.OnAlternateRewardSelected: Skip/REROLL/PaelsWing SACRIFICE",
                "NCardRewardSelectionScreen/CardReward/CardRewardAlternative",
                "NativeCardRewardDecisionProvider",
                "exact CardReward.OnSelect scope -> screen-keyed alternative index callback",
                "REROLL: CardReward.Reroll; terminating alternatives: Reward.SelectUnsynchronized Task",
                "next exact card-reward or reward-set boundary",
                "The shipped vanilla alternatives are real same-screen Human choices. REROLL cannot use the outer Task because that Task intentionally remains open for the refreshed selection."),
            new("generic_simple_card_selector", FullRunCoverageClassifications.InScopeImplemented,
                "NCardGrid.HolderPressed plus exact CompleteSelection; unowned teardown is not accepted",
                "NSimpleCardSelectScreen", "NativeSimpleCardSelection.TryBuild/DescribeCommands",
                "exact CardSelectCmd PlayerChoiceContext action or admitted parent/root scope -> NSimpleCardSelectScreen.Create",
                "CompleteSelection after completion-source settlement",
                "screen-keyed durable continuation on the parent root"),
            new("generic_deck_card_selector", FullRunCoverageClassifications.InScopeImplemented,
                "NCardGrid.HolderPressed plus exact CompleteSelection; unowned teardown is not accepted",
                "NDeckCardSelectScreen / typed upgrade-transform-enchant variants",
                "NativeDeckCardSelection.TryBuild/DescribeCommands",
                "exact parent/root async scope -> typed Create/ShowScreen factory",
                "Close/Confirm/Complete only after completion-source settlement",
                "screen-keyed durable continuation on the parent root"),
            new("generic_combat_pile_selector", FullRunCoverageClassifications.InScopeImplemented,
                "NCardGrid.HolderPressed plus exact CompleteSelection; unowned teardown is not accepted",
                "NCombatPileCardSelectScreen", "NativeCombatPileSelection.TryBuild/DescribeCommands",
                "exact CardSelectCmd PlayerChoiceContext action -> NCombatPileCardSelectScreen.Create",
                "CompleteSelection after completion-source settlement",
                "screen-keyed durable continuation on the parent root"),
            new("generic_card_bundle_selector", FullRunCoverageClassifications.InScopeImplemented,
                "NCardBundle.Hitbox plus exact ConfirmSelection; preview CancelSelection and unowned exit are not accepted",
                "NChooseABundleSelectionScreen", "CardBundleSelectionSurfaceReader.TryBuild/StartDirect*",
                "exact Event/Reward/Relic acquisition parent scope -> NChooseABundleSelectionScreen.ShowScreen",
                "ConfirmSelection after completion-source settlement",
                "screen-keyed durable continuation on the parent root"),
            new("target_picker.cancel", FullRunCoverageClassifications.NotAPlayerDecisionWithNativeJustification,
                "target-picker cancel/back closes targeting presentation", "NPlayerTargeting",
                "NativeCombatDecisionProvider", "none: no GameAction enqueue",
                "no STS2 mutation/Commit", "the original combat decision remains current",
                "Exact v0.111.0 cancel tears down targeting without enqueueing a GameAction; it is presentation cancellation, not an accepted Human decision."),
            new("run_setup.start", FullRunCoverageClassifications.NotAPlayerDecisionWithNativeJustification,
                "none: RunManager.Launch is native lifecycle setup", "RunManager", "RecorderRuntime",
                "RunManager.Launch", "RunManager.RunStarted event", "RunManager.RoomEntered/first interactive boundary",
                "No player decision is made at this seam."),
            new("run_terminal.end", FullRunCoverageClassifications.NotAPlayerDecisionWithNativeJustification,
                "none: RunManager.OnEnded is native terminal lifecycle", "RunManager", "RecorderRuntime",
                "RunManager.OnEnded(bool)", "RunManager.OnEnded", "no successor; terminal marker only",
                "No player decision is made and no successor is inferred."),
            new("shop_inventory.card_removal_nested_selector", FullRunCoverageClassifications.InScopeImplemented,
                "MerchantCardRemovalEntry.OnTryPurchaseWrapper -> OneOffSynchronizer.DoMerchantCardRemoval -> CardSelectCmd.FromDeckForRemoval",
                "NDeckCardSelectScreen", "NativeRoomDecisionProvider (parent only)",
                "exact three-argument removal Task scope -> NDeckCardSelectScreen.Create",
                "terminal selector callback after completion-source settlement",
                "durable child continuation on the same removal parent root"),
            new("event_option.nested_selector", FullRunCoverageClassifications.InScopeImplemented,
                "EventOption.Chosen callback may open a child selector", "NDeckCardSelectScreen or other child owner",
                "NativeRoomDecisionProvider (parent only)", "exact EventOption.Chosen logical async scope -> typed selector factory",
                "terminal child callback; EventOption.Chosen retains final parent disposition",
                "durable child continuation on the same EventOption root"),
            new("rest_site.nested_selector", FullRunCoverageClassifications.InScopeImplemented,
                "RestSiteOption.OnSelect may call CardSelectCmd.FromDeckForUpgrade", "NDeckCardSelectScreen",
                "NativeRoomDecisionProvider (parent only)", "exact ChooseLocalOption option/root logical async scope -> typed selector factory",
                "terminal child callback; ChooseLocalOption Task retains final parent disposition",
                "durable child continuation on the same RestSiteOption root"),
            new("reward_card_removal.nested_selector", FullRunCoverageClassifications.InScopeImplemented,
                "CardRemovalReward.OnSelect -> RewardSynchronizer.DoUnsyncedCardRemoval -> CardSelectCmd.FromDeckForRemoval",
                "CardRemovalReward/NDeckCardSelectScreen",
                "NativeRewardDecisionProvider (parent only)",
                "exact CardRemovalReward.OnSelect reward/root scope -> typed selector factory",
                "terminal child callback; RewardsSetSynchronizer.SelectLocalReward Task retains final parent disposition",
                "durable child continuation on the same reward-claim root"),
        };

    /// <summary>
    /// Mandatory census families. A family must be declared even while its
    /// exact native parent carrier is BLOCKED; omission is not a valid way to
    /// make qualification appear complete.
    /// </summary>
    public static IReadOnlyList<string> MandatoryFamilies { get; } =
        new[]
        {
            "potion_belt.discard",
            "reward_potion_belt.discard_replace",
            "reward_nested.card_selection",
            "reward_nested.replacement_selection",
            "generic_simple_card_selector",
            "generic_deck_card_selector",
            "generic_combat_pile_selector",
            "generic_card_bundle_selector",
            "target_picker.cancel",
            "native_generated_card_choice.skip",
            "boss_relic.select",
            "boss_relic.skip",
            "shop_inventory.card_removal_nested_selector",
            "event_option.nested_selector",
            "rest_site.nested_selector",
            "reward_card_removal.nested_selector"
        };

    public static IReadOnlyList<string> Validate() => Validate(Entries);

    public static IReadOnlyList<string> Validate(
        IReadOnlyList<FullRunCoverageEntry> entries)
    {
        var errors = new List<string>();
        foreach (FullRunCoverageEntry entry in entries)
        {
            if (string.IsNullOrWhiteSpace(entry.Family)
                || string.IsNullOrWhiteSpace(entry.Classification)
                || string.IsNullOrWhiteSpace(entry.UiInput)
                || string.IsNullOrWhiteSpace(entry.NativeOwner)
                || string.IsNullOrWhiteSpace(entry.SemanticProvider)
                || string.IsNullOrWhiteSpace(entry.AcceptedSeam)
                || string.IsNullOrWhiteSpace(entry.LifecycleCommit)
                || string.IsNullOrWhiteSpace(entry.NextAuthoritativeBoundary))
                errors.Add($"coverage_entry_incomplete:{entry.Family}");
            if (entry.Classification == FullRunCoverageClassifications.Blocked
                && !entry.UiInput.Contains("BLOCKED", StringComparison.Ordinal)
                && !entry.AcceptedSeam.Contains("BLOCKED", StringComparison.Ordinal))
                errors.Add($"blocked_entry_missing_reason:{entry.Family}");
            if (entry.Classification is not FullRunCoverageClassifications.InScopeImplemented
                and not FullRunCoverageClassifications.NotAPlayerDecisionWithNativeJustification
                and not FullRunCoverageClassifications.OutOfScopeWithJustification
                and not FullRunCoverageClassifications.Blocked)
                errors.Add($"coverage_classification_unknown:{entry.Family}");
        }
        errors.AddRange(entries.GroupBy(entry => entry.Family, StringComparer.Ordinal)
            .Where(group => group.Count() != 1)
            .Select(group => $"coverage_family_duplicate:{group.Key}"));
        var declared = entries
            .Select(entry => entry.Family)
            .ToHashSet(StringComparer.Ordinal);
        errors.AddRange(MandatoryFamilies
            .Where(family => !declared.Contains(family))
            .Select(family => $"mandatory_family_missing:{family}"));
        return errors;
    }

    public static FullRunCoverageValidation ValidateForQualification() =>
        ValidateForQualification(Entries);

    public static FullRunCoverageValidation ValidateForQualification(
        IReadOnlyList<FullRunCoverageEntry> entries)
    {
        IReadOnlyList<string> structuralErrors = Validate(entries);
        IReadOnlyList<string> missing = MandatoryFamilies
            .Where(family => entries.All(entry => !string.Equals(
                entry.Family,
                family,
                StringComparison.Ordinal)))
            .ToArray();
        // Qualification is a claim over the complete census, not only the
        // currently mandatory subset. Any explicitly BLOCKED entry is an
        // unresolved player-decision surface and therefore blocks the claim.
        IReadOnlyList<string> blocked = entries
            .Where(entry => entry.Classification == FullRunCoverageClassifications.Blocked)
            .Select(entry => entry.Family)
            .Distinct(StringComparer.Ordinal)
            .OrderBy(family => family, StringComparer.Ordinal)
            .ToArray();
        var errors = structuralErrors
            .Concat(blocked.Select(family => $"in_scope_blocked:{family}"))
            .ToArray();
        return new FullRunCoverageValidation(
            errors.Length == 0,
            errors.Length == 0 && missing.Count == 0 && blocked.Count == 0,
            errors,
            missing,
            blocked);
    }
}

public sealed record FullRunCoverageValidation(
    bool IsValid,
    bool QualificationReady,
    IReadOnlyList<string> Errors,
    IReadOnlyList<string> MissingMandatoryFamilies,
    IReadOnlyList<string> BlockedInScopeFamilies);

public sealed record ReadEvidence(
    int SchemaVersion,
    string Schema,
    string ReadEvidenceId,
    string ReadId,
    string Kind,
    string SnapshotId,
    string RuntimeInstanceId,
    string EnvironmentFingerprint,
    string Status,
    string? ContentSchema,
    JsonNode? Completeness,
    string? PayloadRef,
    string? PayloadSha256,
    DateTimeOffset CapturedAt,
    string? ErrorCode,
    string? Detail);

public sealed record CapturedReadPayload(
    string ReadId,
    string Kind,
    string SnapshotId,
    string RuntimeInstanceId,
    string EnvironmentFingerprint,
    string Status,
    string? ContentSchema,
    JsonNode? Content,
    JsonNode? Completeness,
    DateTimeOffset CapturedAt,
    string? ErrorCode,
    string? Detail);

public sealed record CurrentDecisionFrame(
    string SnapshotId,
    string InteractionId,
    string InteractionKind,
    string SurfaceSchema,
    string CatalogDigest,
    int CatalogCount,
    JsonNode Snapshot,
    IReadOnlyList<ReadEvidence> Reads);

public sealed record CurrentSuccessor(
    string SnapshotId,
    string Status,
    string InteractionId,
    string InteractionKind,
    DateTimeOffset ObservedAt,
    JsonNode Snapshot,
    IReadOnlyList<ReadEvidence> Reads);

public sealed record CurrentDecisionRecord(
    int SchemaVersion,
    string Schema,
    string RecordId,
    string SessionId,
    string RunId,
    string TimelineId,
    long Sequence,
    DateTimeOffset RecordedAt,
    RecorderEnvironmentIdentity Environment,
    string CaptureProfileId,
    CurrentDecisionFrame Pre,
    NativeWitnessEvidence NativeWitness,
    ExactMappingEvidence Mapping,
    RecordedBoundAction Action,
    CurrentSuccessor Successor,
    string DecisionFamily,
    string Surface,
    RecordEligibility Eligibility);

public sealed record RunJournalEvent(
    int SchemaVersion,
    string Schema,
    string EventId,
    string SessionId,
    string RunId,
    string TimelineId,
    long Sequence,
    DateTimeOffset RecordedAt,
    string Kind,
    string? RecordId,
    string? SnapshotId,
    string? Detail);

public sealed record CurrentRecordingManifest(
    int SchemaVersion,
    string Schema,
    string SessionId,
    string TimelineId,
    DateTimeOffset CreatedAt,
    string RecorderVersion,
    string RecorderSourceRevision,
    string Platform,
    string CaptureProfileId,
    string CaptureProfileSha256,
    IReadOnlyList<string> SupportedFamilies,
    IReadOnlyList<string> NonClaims)
{
    public int? DecisionSchemaVersion { get; init; }
    public int? DispositionSchemaVersion { get; init; }
    public int? CloseSchemaVersion { get; init; }
    public int? RecoverySchemaVersion { get; init; }
    public int? ContinuousSchemaVersion { get; init; }
}

public sealed record CurrentCoverageSummary(
    int SchemaVersion,
    string Schema,
    string SessionId,
    long AdmittedRecords,
    long Invalidations,
    long ReadMaterialized,
    long ReadFailed,
    IReadOnlyDictionary<string, long> Families,
    IReadOnlyDictionary<string, long> ReadsByKind,
    IReadOnlyDictionary<string, long> InvalidationsByReason,
    DateTimeOffset UpdatedAt);
