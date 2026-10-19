using STS2Connector.Authority;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Witness;

namespace STS2Connector.PlayerEnvironment;

// Detached acquisition seam: projection, identity, bindings and submission remain production code.
internal sealed record SnapshotBuildInput(GameBuildIdentity Game, LiveObservation Observation,
    PersistentVisibleStateBuildResult Persistent, RewardPageSnapshotIdentity Identity)
{
    internal Func<string, PlayerReadBuildResult>? Read { get; init; }
    internal NativeLogicalGridCaptureResult? GridCapture { get; init; }
}

internal static partial class PlayerEnvironmentService
{
    internal static SnapshotBuildResult BuildSnapshot(
        bool suppressNativePageEvidence = true,
        IReadOnlyCollection<string>? requiredReadKinds = null,
        Func<string, IReadOnlyCollection<string>>? requiredReadKindsForInteraction = null,
        ProcessLocalCaptureProfiler? captureProfiler = null,
        string? inputProfile = null,
        bool textMenuCapture = false,
        int maximumBoundActions = MaxBoundActions,
        Func<LiveObservation, IReadOnlyCollection<string>>? requiredReadKindsForObservation = null,
        bool aggregateCurrentPublicReads = false,
        Action<SnapshotBuildResult>? sourcePublicProjection = null,
        SnapshotBuildInput? capturedInput = null)
    {
        bool publicBasisProfile = inputProfile == PublicBasisContract.Profile;
        bool ordinaryProjection = inputProfile == null || publicBasisProfile;
        aggregateCurrentPublicReads |= publicBasisProfile;
        P1CaptureGuard.ObserveSnapshotBuild();
        DateTimeOffset captureTime = DateTimeOffset.UtcNow;
        if (!IsSupportedInputProfile(inputProfile)
            || inputProfile is TextMenuContract.Profile or TextMenuV2Contract.Profile)
            throw new ArgumentException("Unsupported Player Environment input profile.", nameof(inputProfile));
        T Measure<T>(string phase, Func<T> operation) =>
            captureProfiler == null ? operation() : captureProfiler.Measure(phase, operation);

        GameBuildIdentity game = Measure(
            "game_identity",
            () => capturedInput?.Game ?? EnvironmentIdentityRuntime.ReadGame());
        NativeLogicalGridCaptureResult? gridCapture = publicBasisProfile || sourcePublicProjection != null
            ? capturedInput?.GridCapture ?? (capturedInput == null
                ? Measure("native_logical_grid", () => NativeLogicalGridCapture.CaptureCurrent(Entities, game, publicBasisOnly: publicBasisProfile)) : null)
            : null;
        LiveObservation nativeView = Measure("native_surface_state", () =>
            capturedInput?.Observation ?? LiveObservationReader.Build(Entities, game, () =>
            (publicBasisProfile ? NativeLogicalGridCapture.PublicBasisObservation(gridCapture, Entities, game) : null)
                ?? NativeGeneratedCardChoice.TryBuild(Entities, game)
                ?? NativeBossRelicSelection.TryBuild(Entities, game)
                ?? NativeSimpleCardSelection.TryBuild(Entities, game)
                ?? NativeDeckUpgradeSelection.TryBuild(Entities, game)
                ?? NativeDeckTransformSelection.TryBuild(Entities, game)
                ?? NativeCombatPileSelection.TryBuild(Entities, game)
                ?? NativeDeckCardSelection.TryBuild(Entities, game)
                ?? NativeRestSite.TryBuild(Entities, game),
                rewardPageProfile: !ordinaryProjection));
        if (capturedInput != null && publicBasisProfile && gridCapture?.Grid is { } frozenGrid)
            nativeView = LiveObservationReader.ApplyRequiredContextCompleteness(
                NativeLogicalGridCapture.PublicBasisObservation(frozenGrid, nativeView.Context, game));
        LiveObservation? navigationView = null;
        if (publicBasisProfile && capturedInput == null
            && nativeView.Surface is PotionPopupSurface { CanUse: false, CanDiscard: true } popup
            && nativeView.Context is not CombatLiveContext)
        {
            // Read the actual obscured owner now; never reuse H or a neighbouring frame.
            var underlying = LiveObservationReader.Build(Entities, game,
                () => NativeRestSite.TryBuild(Entities, game), includePotionPopup: false);
            if (NonCombatPotionBoundary.IsBase(underlying))
            {
                navigationView = nativeView;
                nativeView = underlying with { NonCombatNavigationPopup = popup };
            }
        }
        LiveObservation nativeDraft = nativeView;
        // Native lifecycle availability is observed once on the complete base
        // capture, before any profile branch. Rich leaves cannot renew its clock.
        PersistentVisibleStateBuildResult shared = Measure(
            "persistent_visible_state", () => capturedInput?.Persistent ?? (game.Compatibility.StateObservationAllowed
                ? PersistentVisibleStateReader.Build(Entities)
                : new PersistentVisibleStateBuildResult(false, null, null)));
        nativeView = CombatInputAvailability.Attach(nativeView, shared.RunActive && shared.State != null && shared.Failure == null,
            Environment.TickCount64);
        nativeDraft = navigationView ?? nativeView;
        if (suppressNativePageEvidence)
            nativeView = NativePageEvidence.SuppressMutation(nativeView);
        bool rewardPotionProfile = inputProfile == PlayerEnvironmentContract.RewardPotionPageProfile;
        if (rewardPotionProfile)
            nativeView = nativeView with { RewardPotionFacts = RewardPotionProfileReader.Capture(nativeView, Entities) };
        if (!ordinaryProjection && (rewardPotionProfile
                ? !IsRewardPotionPage(nativeView)
                : !IsOrdinaryRewardPage(nativeView)))
        {
            nativeView = nativeView with
            {
                Surface = new UnsupportedSurface(
                    nativeView.Surface.Kind,
                    rewardPotionProfile ? "reward_potion_page_profile" : "ordinary_reward_page_profile",
                    rewardPotionProfile
                        ? $"This current page is outside the exact reward/potion input profile: {nativeView.RewardPotionFacts?.Reason}."
                        : "This current page is outside the ordinary reward input profile."),
                Readiness = "degraded",
                Completeness = new StateCompleteness(
                    "degraded", "empty_fail_closed",
                    new[] { "ordinary reward current-page native owner" },
                    new[] { rewardPotionProfile ? "reward_potion_page_profile_unsupported"
                        : "ordinary_reward_page_profile_unsupported" })
            };
        }
        var identity = capturedInput?.Identity ?? RewardPageIdentity;
        long nativeSequence = identity.ObserveNative(
            CommonNativeSignature(game, nativeDraft, shared.State));
        bool shopCatalogAvailable = ShopSurfaceFacts.TryGetCurrent(out _, out _, out _);
        var capturedReads = new Dictionary<string, PlayerReadBuildResult>(StringComparer.Ordinal);
        PlayerReadBuildResult CaptureRead(string kind)
        {
            if (!capturedReads.TryGetValue(kind, out PlayerReadBuildResult? value))
            {
                value = capturedInput?.Read is { } read ? read(kind)
                    : PlayerVisibleReadBuilder.Build(kind, nativeView.Context, Entities);
                capturedReads.Add(kind, value);
            }
            return value;
        }
        if (sourcePublicProjection != null)
        {
            if (inputProfile != null || !textMenuCapture || !aggregateCurrentPublicReads)
                throw new InvalidOperationException("source_projection_profile_invalid");
            LiveObservation? gridView = NativeLogicalGridCapture.PublicBasisObservation(gridCapture, nativeView.Context, game);
            LiveObservation neutral = gridView == null ? nativeView
                : LiveObservationReader.ApplyRequiredContextCompleteness(gridView);
            if (gridView != null && suppressNativePageEvidence)
                neutral = NativePageEvidence.SuppressMutation(neutral);
            // The Source4 envelope remains its original untagged revision. Only
            // this public projection uses the already-frozen neutral grid facts.
            sourcePublicProjection(ProjectSnapshot(neutral, false, MaxBoundActions, false, gridView != null));
            return ProjectSnapshot(nativeView, true, maximumBoundActions, true, false);
        }
        SnapshotBuildResult? rawNavigation = null;
        if (navigationView != null)
        {
            var logical = nativeView;
            nativeView = navigationView;
            rawNavigation = ProjectSnapshot(navigationView, false, maximumBoundActions, true, false);
            capturedReads.Clear();
            nativeView = logical;
        }
        return ProjectSnapshot(nativeView, textMenuCapture, maximumBoundActions, false, publicBasisProfile)
            with { RawNativeNavigation = rawNavigation };

        SnapshotBuildResult ProjectSnapshot(LiveObservation draft, bool asTextMenu, int actionLimit,
            bool privatePreparation, bool publicBasisProjection)
        {
            draft = draft with
            {
                InputOwnership = draft.Surface is UnsupportedSurface
                    ? new InputOwnership(
                        "none_fail_closed",
                        null,
                        "An unsupported Surface cannot own Player Environment input.")
                    : new InputOwnership(
                        "current_ui_owned",
                        draft.Surface.Kind,
                        "The exact current native UI owns input.")
            };

            draft = LiveObservationReader.ApplyMissingPersistentStatePolicy(draft, shared);
            PlayerVisibilityProjection information = Measure("visibility_catalog", () =>
                PlayerVisibilityCatalog.Build(draft, shared.State != null, shopCatalogAvailable, aggregateCurrentPublicReads));
            IReadOnlyCollection<string>? selectedReadKinds = ordinaryProjection
                ? requiredReadKinds ?? requiredReadKindsForInteraction?.Invoke(draft.Surface.Kind)
                    ?? (aggregateCurrentPublicReads ? information.ReadCatalog.Select(read => read.Kind).Distinct(StringComparer.Ordinal).ToArray()
                        : requiredReadKindsForObservation?.Invoke(draft))
                : null;
            IReadOnlyDictionary<string, PlayerReadBuildResult> readBuilds = Measure(
                "required_read_builds",
                () => MaterializeRequiredReads(selectedReadKinds, CaptureRead));
            IReadOnlyList<PlayerReadCatalogEntry> readCatalog = ordinaryProjection
                ? information.ReadCatalog
                : Array.Empty<PlayerReadCatalogEntry>();
            IReadOnlyList<PlayerEnvironmentLinkedDetailCatalogEntry> linkedDetails =
                ordinaryProjection
                    ? BuildLinkedDetailCatalog(draft.Surface, draft.Context, readBuilds, includePublicCardDetails: aggregateCurrentPublicReads)
                    : Array.Empty<PlayerEnvironmentLinkedDetailCatalogEntry>();
            PlayerVisibilityState visibility = information.Visibility with
            {
                AvailableReads = readCatalog.Select(entry => entry.Kind).ToArray(),
                LinkedDetailKinds = linkedDetails.Select(entry => entry.Kind)
                    .Distinct(StringComparer.Ordinal)
                    .OrderBy(kind => kind, StringComparer.Ordinal)
                    .ToArray(),
                Missing = information.Visibility.Missing
                    .Where(value => value != "linked_entity_detail_catalog_not_implemented")
                    .ToArray()
            };
            JsonNode rawSurface = Measure(
                "surface_serialization",
                () => draft.Surface is NativePublicBasisGridSurface publicGrid ? publicGrid.PublicSurface.DeepClone()
                    : JsonSerializer.SerializeToNode(
                    draft.Surface,
                    draft.Surface.GetType(),
                    ConnectorMod._jsonOptions) ?? new JsonObject());
            PlayerEnvironmentInteractionContent surfaceContent = Measure(
                "visible_surface_projection",
                () => ProjectVisibleFacts(draft.Surface, draft.Context));
            if (!ordinaryProjection && draft.Surface is CardRewardSelectionSurface
                && draft.RewardPageFacts is { Qualified: true } profileFacts
                && surfaceContent.Surface is JsonObject page)
                ProjectRewardPageFacts(page, profileFacts);
            if (rewardPotionProfile && draft.RewardPotionFacts is { Qualified: true } potionFacts
                && surfaceContent.Surface is JsonObject potionPage)
                ProjectRewardPotionFacts(potionPage, draft.Surface, potionFacts);
            var belt = publicBasisProjection && NonCombatPotionBoundary.IsBase(draft)
                ? capturedInput == null ? NonCombatPotionBoundary.Capture(Entities) : Array.Empty<NonCombatPotionDiscardBinding>()
                : Array.Empty<NonCombatPotionDiscardBinding>();
            if (publicBasisProjection && NonCombatPotionBoundary.IsBase(draft)
                && surfaceContent.Surface is JsonObject basePage)
                basePage["non_combat_discardable_potions"] = new JsonArray(belt.Select(item =>
                    (JsonNode)new JsonObject { ["entity_id"] = item.PotionId, ["slot"] = item.Slot }).ToArray());
            var projectionContent = new PlayerEnvironmentInteractionContent(
                surfaceContent.Surface.DeepClone(), surfaceContent.Context.DeepClone());
            IReadOnlyList<NativeUiBoundAction> nativeBindings = Measure(
                "native_binding_catalog",
                () => CanPublishMutationAuthority(draft.Readiness)
                    ? asTextMenu && draft.Surface is CombatTurnSurface combat
                        ? BuildTextCombatBindings(draft, combat)
                        : rewardPotionProfile
                        ? NativeUiActionRuntime.BuildRewardPotionBindings(draft)
                        : BuildPlayerEnvironmentBindings(draft)
                    : Array.Empty<NativeUiBoundAction>());
            if (belt.Length > 0)
                nativeBindings = nativeBindings.Where(item => item.Candidate.Operation != "discard_potion_for_reward")
                    .Concat(belt.Select(item => NonCombatPotionBoundary.Bind(item, draft.NonCombatNavigationPopup))).ToArray();
            string interactionId = ReadFirstString(
                rawSurface,
                "screen_entity_id",
                "room_entity_id",
                "hand_entity_id",
                "map_screen_entity_id")
                ?? nativeBindings.SelectMany(item => item.Candidate.EntityBindings)
                    .FirstOrDefault(entity => IsOwnerRole(entity.Role))?.EntityId
                ?? "interaction_" + StableIdentityHash.Object(new { draft.Surface.Kind, draft.Signature })[..20];
            Dictionary<string, PlayerEnvironmentReferent> referents = Measure(
                "referent_projection",
                () => BuildFactReferents(projectionContent));
            // Aggregate public Reads also detail cards that the current page does not show
            // (e.g. the deck on a map page). Each targeted card is a current public entity,
            // so it is a referent; the snapshot contract requires every Read target to be one.
            if (aggregateCurrentPublicReads)
                foreach (PlayerEnvironmentLinkedDetailCatalogEntry detail in linkedDetails)
                    if (detail.Kind == "surface_card")
                        AddReferent(referents, detail.EntityId, "card", null, null, null);
            BoundActionProjectionResult projected = Measure(
                "bound_action_projection",
                () => ProjectBoundActions(nativeBindings, interactionId, referents, actionLimit));
            bool profileCatalogIncomplete = !ordinaryProjection
                && projected.Projection.Status != "complete";
            if (profileCatalogIncomplete)
                projected = CloseIncompleteRewardCatalog(projected);
            IReadOnlyList<PlayerEnvironmentInteractionCapability> capabilities =
                ProjectInteractionCapabilities(projected.Projection, referents);
            string stage = ReadFirstString(rawSurface, "stage") ?? draft.Readiness;
            string? prompt = ReadFirstString(rawSurface, "prompt", "body", "message");
            IReadOnlyList<PlayerEnvironmentReadOpportunity> reads = readCatalog
                .Select(entry => new PlayerEnvironmentReadOpportunity(
                    $"read:{entry.Kind}",
                    entry.Kind,
                    null,
                    PlayerEnvironmentContract.ReadContentSchema(entry.Kind),
                    entry.VisibilityBasis,
                    SnapshotBound: true,
                    entry.OrderingSemantics,
                    entry.HiddenByPolicy))
                .Concat(linkedDetails.Select(entry => new PlayerEnvironmentReadOpportunity(
                    $"read:{entry.Kind}:{entry.DetailKey ?? entry.EntityId}",
                    entry.Kind,
                    entry.EntityId,
                    PlayerEnvironmentContract.ReadContentSchema(entry.Kind),
                    entry.VisibilityBasis,
                    SnapshotBound: true,
                    "single_entity",
                    Array.Empty<string>())))
                .OrderBy(read => read.ReadId, StringComparer.Ordinal)
                .ToArray();
            var view = FinalSnapshotView(draft, projected.Projection, visibility.HiddenByPolicy,
                profileCatalogIncomplete, asTextMenu, stage);
            stage = view.Stage;
            string signature = Measure("stable_snapshot_signature", () => StableIdentityHash.Object(new
            {
                game.Version,
                game.Commit,
                shared.State,
                draft.Readiness,
                surface = projectionContent,
                interactionId,
                referents = referents.Values.OrderBy(item => item.ReferentId, StringComparer.Ordinal).ToArray(),
                authority = CanonicalAuthoritySignature(nativeBindings),
                reads,
                visibility.HiddenByPolicy
            }));
            signature = FinalSnapshotSignature(signature, draft.CombatWithdrawal, view);
            if (rewardPotionProfile && draft.RewardPotionFacts is { } exact)
                signature = RewardPotionViewSignature(signature, exact);
            (string snapshotId, long sequence) = privatePreparation
                ? identity.ObserveNativePreparation(nativeSequence, signature)
                : publicBasisProjection
                ? identity.ObservePublicBasis(nativeSequence, signature)
                : inputProfile == null
                ? identity.ObserveLegacy(nativeSequence, signature)
                : rewardPotionProfile
                    ? identity.ObserveRewardPotion(nativeSequence, signature)
                    : identity.ObserveReward(nativeSequence, signature);
            string status = view.Status;
            PlayerEnvironmentCompleteness completeness = view.Completeness;
            var snapshot = new PlayerEnvironmentSnapshot(
                PlayerEnvironmentContract.ProtocolVersion,
                ordinaryProjection
                    ? PlayerEnvironmentContract.SnapshotSchema
                    : rewardPotionProfile
                        ? PlayerEnvironmentContract.RewardPotionSnapshotSchema
                        : PlayerEnvironmentContract.OrdinaryRewardSnapshotSchema,
                snapshotId,
                sequence,
                captureTime,
                status,
                shared.State == null
                    ? null
                    : new PlayerEnvironmentContent(
                        "sts2.player-environment/persistent/run-player-1",
                        JsonSerializer.SerializeToNode(shared.State, ConnectorMod._jsonOptions) ?? new JsonObject()),
                new PlayerEnvironmentInteraction(
                    interactionId,
                    draft.Surface.Kind,
                    stage,
                    prompt,
                    ordinaryProjection
                        ? SurfaceContentSchema(draft.Surface.Kind)
                        : $"sts2.player-environment/surface/{draft.Surface.Kind}-{(rewardPotionProfile ? 3 : 2)}",
                    projectionContent,
                    capabilities),
                referents.Values.OrderBy(item => item.ReferentId, StringComparer.Ordinal).ToArray(),
                projected.Projection,
                reads,
                completeness,
                ToSessionReference(EnvironmentIdentityRuntime.HostIdentity(), game),
                ToInformationPolicy(EnvironmentIdentityRuntime.InformationPolicy()))
            { InputProfile = inputProfile };
            return new SnapshotBuildResult(
                snapshot,
                draft,
                projected.Bindings,
                readBuilds) { CapturedLogicalGrid = gridCapture ?? NativeLogicalGridCaptureResult.NotAttempted };
        }
    }

    internal sealed record SourceSnapshotBuild(SnapshotBuildResult PublicBasis, SnapshotBuildResult NativePreparation);
    internal static SourceSnapshotBuild BuildSourceSnapshot()
    {
        SnapshotBuildResult? ordinary = null;
        var native = BuildSnapshot(textMenuCapture: true, maximumBoundActions: NativeLogicalCapture.MaximumActions,
            aggregateCurrentPublicReads: true, sourcePublicProjection: value => ordinary = value);
        return new(ordinary ?? throw new InvalidOperationException("source_public_projection_missing"), native);
    }

    internal static CombatInputView FinalSnapshotView(LiveObservation draft, PlayerEnvironmentBoundActionProjection projection,
        IReadOnlyList<string> hidden, bool incompleteProfile, bool textCapture, string stage)
    {
        bool unsupported = draft.Surface is UnsupportedSurface || incompleteProfile;
        bool actions = projection.Status == "complete" && projection.MaterializedCount > 0;
        bool textEntry = CanPublishTextCombatEntry(textCapture, draft.Surface, draft.Readiness, draft.Completeness, projection);
        string status = actions || textEntry ? "interactive"
            : unsupported ? "visible_unsupported" : draft.Readiness == "settling" ? "settling" : "observed";
        var completeness = ToCompleteness(draft.Completeness, hidden, unsupported ? "visible_unmapped" : null);
        if (projection.Status != "complete") completeness = completeness with
        { Status = "partial", Missing = completeness.Missing.Append("finite_bound_action_projection_incomplete").Distinct(StringComparer.Ordinal).ToArray() };
        // The text/native adapter must first discover its complete richer leaves.
        return textCapture ? new(status, stage, completeness)
            : CombatWithdrawalProjection.Apply(status, stage, completeness, projection.Status == "complete",
                projection.MaterializedCount, draft.CombatWithdrawal);
    }
    internal static string FinalSnapshotSignature(string signature, CombatWithdrawalProof? proof, CombatInputView view) =>
        proof == null ? signature : StableIdentityHash.Object(new { signature, availability = proof.Identity,
            view.Status, view.Stage, view.Completeness });

    internal static bool CanPublishTextCombatEntry(
        bool textMenuCapture,
        ILiveSurface surface,
        string readiness,
        StateCompleteness nativeCompleteness,
        PlayerEnvironmentBoundActionProjection projection) =>
        textMenuCapture
        && surface is CombatTurnSurface
        && readiness == "ready"
        && nativeCompleteness.PlayerVisibleSemantics.StartsWith(
            "contract_complete_for_immediate_combat_turn", StringComparison.Ordinal)
        && nativeCompleteness.Missing.Count == 0
        && projection.Status == "complete";

    internal static bool IsOrdinaryRewardPage(LiveObservation draft) =>
        draft.Readiness == "ready"
        && draft.Completeness.PlayerVisibleSemantics is
            "contract_complete_for_reward_claim" or
            "contract_complete_for_card_reward_selection"
        && (draft.Surface switch
        {
            RewardClaimSurface outer => outer.DiscardablePotions.Count == 0,
            CardRewardSelectionSurface => draft.RewardPageFacts is { Qualified: true },
            _ => false
        });

    internal static bool IsRewardPotionPage(LiveObservation draft) =>
        draft.RewardPotionFacts is { Qualified: true }
        && draft.Readiness == "ready"
        && (draft.Surface switch
        {
            RewardClaimSurface => draft.Completeness.PlayerVisibleSemantics
                == "contract_complete_for_reward_claim",
            CardRewardSelectionSurface => draft.Completeness.PlayerVisibleSemantics
                == "contract_complete_for_card_reward_selection"
                && draft.RewardPageFacts is { Qualified: true },
            PotionPopupSurface => draft.Completeness.PlayerVisibleSemantics
                == "contract_complete_for_native_potion_popup",
            _ => false
        });

    internal static void ProjectRewardPotionFacts(
        JsonObject page, ILiveSurface surface, RewardPotionProfileFacts facts)
    {
        if (surface is PotionPopupSurface)
        {
            // Future targeting is a separate native page, not an operand of
            // today's popup Use button.
            page.Remove("direct_combat_use");
            page.Remove("use_target_entity_ids");
            page["controls"] = new JsonArray(facts.PopupControls.Select(option =>
                (JsonNode)new JsonObject
                {
                    ["entity_id"] = option.ControlEntityId,
                    ["kind"] = option.Kind,
                    ["enabled"] = option.Enabled,
                    ["label"] = option.Label
                }).ToArray());
            return;
        }
        if (surface is RewardClaimSurface)
            page.Remove("discardable_potions"); // Legacy direct discard is not a v2 page action.
        page["openable_potions"] = new JsonArray(facts.Openers.Select(option =>
            (JsonNode)new JsonObject
            {
                ["potion_entity_id"] = option.PotionEntityId,
                ["slot"] = option.Slot,
                ["name"] = option.Name
            }).ToArray());
    }

    internal static string RewardPotionViewSignature(
        string baseSignature, RewardPotionProfileFacts exact) =>
        StableIdentityHash.Object(new
        {
            baseSignature,
            holderBinding = exact.Openers.Select(option => new
            {
                option.HolderEntityId, option.PotionEntityId, option.Slot
            }).ToArray(),
            controls = exact.PopupControls.Select(option => new
            {
                option.Kind, option.ControlEntityId, option.Enabled
            }).ToArray(),
            exact.RewardOwnerKind,
            exact.RewardOwnerEntityId
        });

    internal static string CommonNativeSignature(
        GameBuildIdentity game,
        LiveObservation nativeDraft,
        PersistentVisibleState? sharedState)
    {
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            game.Commit,
            sharedState,
            nativeDraft.Signature,
            nativeDraft.Readiness,
            nativeDraft.Surface,
            nativeDraft.Context,
            nativeDraft.Completeness
        });
        if (nativeDraft.EndTurnBindingIdentity != null)
            signature = StableIdentityHash.Object(new { signature, nativeDraft.EndTurnBindingIdentity });
        return nativeDraft.CombatWithdrawal == null ? signature
            : StableIdentityHash.Object(new { signature, availability = nativeDraft.CombatWithdrawal.Identity });
    }

    internal static BoundActionProjectionResult CloseIncompleteRewardCatalog(
        BoundActionProjectionResult projected) =>
        projected.Projection.Status == "complete"
            ? projected
            : new BoundActionProjectionResult(
                projected.Projection with
                {
                    Status = "unavailable",
                    MaterializedCount = 0,
                    Actions = Array.Empty<PlayerEnvironmentBoundAction>()
                },
                new Dictionary<string, PlayerEnvironmentNativeBinding>(StringComparer.Ordinal));

    internal static void ProjectRewardPageFacts(JsonObject page, RewardPageProfileFacts facts)
    {
        page["cards"] = JsonSerializer.SerializeToNode(
            facts.CurrentCards, ConnectorMod._jsonOptions);
        page["alternative_effects"] = JsonSerializer.SerializeToNode(
            facts.AlternativeEffects, ConnectorMod._jsonOptions);
    }

    private static IReadOnlyDictionary<string, PlayerReadBuildResult> BuildRequiredReads(
        IReadOnlyCollection<string>? requiredReadKinds,
        ILiveContext context) => MaterializeRequiredReads(
            requiredReadKinds,
            kind => PlayerVisibleReadBuilder.Build(kind, context, Entities));

    internal static IReadOnlyDictionary<string, PlayerReadBuildResult> MaterializeRequiredReads(
        IReadOnlyCollection<string>? requiredReadKinds,
        Func<string, PlayerReadBuildResult> build) =>
        requiredReadKinds == null
            ? new Dictionary<string, PlayerReadBuildResult>(StringComparer.Ordinal)
            : requiredReadKinds
                .Where(kind => !string.IsNullOrWhiteSpace(kind))
                .Distinct(StringComparer.Ordinal)
                .ToDictionary(
                    kind => kind,
                    build,
                    StringComparer.Ordinal);

    internal static bool CanPublishMutationAuthority(string readiness) =>
        string.Equals(readiness, "ready", StringComparison.Ordinal);

    internal static PlayerEnvironmentHostIdentity ToHostIdentity(LiveHostIdentity identity) => new(
        PlayerEnvironmentContract.EnvironmentId,
        PlayerEnvironmentContract.EnvironmentName,
        identity.Version,
        identity.RuntimeInstanceId,
        EnvironmentIdentityRuntime.HostKind(),
        new PlayerEnvironmentImplementationIdentity(
            identity.SourceRevision,
            identity.ModuleVersionId,
            identity.ArtifactSha256));

    internal static PlayerEnvironmentGameIdentity ToGameIdentity(GameBuildIdentity game)
    {
        ModsetIdentity? modset = game.Modset;
        return new PlayerEnvironmentGameIdentity(
            game.Version,
            game.Commit,
            game.Branch,
            game.MainAssemblyHash,
            new PlayerEnvironmentCompatibility(
                game.Compatibility.Status,
                game.Compatibility.StateObservationAllowed,
                game.Compatibility.Detail),
            new PlayerEnvironmentModset(
                modset?.Status ?? "unavailable",
                modset?.Fingerprint ?? "unavailable",
                modset?.FingerprintScope ?? "unavailable",
                modset?.Mods
                    .Where(item => string.Equals(
                        item.LoadState,
                        "Loaded",
                        StringComparison.Ordinal))
                    .Select(item => item.Id)
                    .OrderBy(id => id, StringComparer.Ordinal)
                    .ToArray() ?? Array.Empty<string>(),
                modset?.Detail ?? "No loaded Modset identity was available."));
    }

    private static PlayerEnvironmentSessionReference ToSessionReference(
        LiveHostIdentity identity,
        GameBuildIdentity game) => new(
            identity.RuntimeInstanceId,
            StableIdentityHash.Object(new
            {
                identity.ArtifactSha256,
                identity.ModuleVersionId,
                game.Version,
                game.Commit,
                game.MainAssemblyHash,
                game.MainAssemblySha256,
                game.MainAssemblyMvid,
                Modset = game.Modset?.Fingerprint
            }));

    private static PlayerEnvironmentInformationPolicy ToInformationPolicy(
        InformationPolicyInfo policy) => new(
            policy.Id,
            policy.Scope,
            policy.IncludesHiddenInformation,
            policy.UnknownFieldBehavior);

    private static PlayerEnvironmentAttribution ToAttribution(
        MutationAttribution value) => new(
            value.RuntimeInstanceId,
            value.ClientSessionId,
            value.ClientInstanceId,
            value.ProductId,
            value.ProductName,
            value.ProductVersion,
            value.ControllerLeaseId,
            value.ControllerGeneration);

}
