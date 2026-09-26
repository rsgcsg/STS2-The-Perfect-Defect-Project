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

internal static partial class PlayerEnvironmentService
{
    internal static SnapshotBuildResult BuildSnapshot(
        bool suppressNativePageEvidence = true,
        IReadOnlyCollection<string>? requiredReadKinds = null,
        Func<string, IReadOnlyCollection<string>>? requiredReadKindsForInteraction = null,
        ProcessLocalCaptureProfiler? captureProfiler = null,
        string? inputProfile = null,
        bool textMenuCapture = false)
    {
        if (!IsSupportedInputProfile(inputProfile))
            throw new ArgumentException("Unsupported Player Environment input profile.", nameof(inputProfile));
        T Measure<T>(string phase, Func<T> operation) =>
            captureProfiler == null ? operation() : captureProfiler.Measure(phase, operation);

        GameBuildIdentity game = Measure(
            "game_identity",
            EnvironmentIdentityRuntime.ReadGame);
        LiveObservation draft = Measure("native_surface_state", () =>
            LiveObservationReader.Build(Entities, game, () =>
            NativeGeneratedCardChoice.TryBuild(Entities, game)
                ?? NativeBossRelicSelection.TryBuild(Entities, game)
                ?? NativeSimpleCardSelection.TryBuild(Entities, game)
                ?? NativeDeckUpgradeSelection.TryBuild(Entities, game)
                ?? NativeDeckTransformSelection.TryBuild(Entities, game)
                ?? NativeCombatPileSelection.TryBuild(Entities, game)
                ?? NativeDeckCardSelection.TryBuild(Entities, game)
                ?? NativeRestSite.TryBuild(Entities, game),
                rewardPageProfile: inputProfile != null));
        LiveObservation nativeDraft = draft;
        if (suppressNativePageEvidence)
            draft = NativePageEvidence.SuppressMutation(draft);
        bool rewardPotionProfile = inputProfile == PlayerEnvironmentContract.RewardPotionPageProfile;
        if (rewardPotionProfile)
            draft = draft with { RewardPotionFacts = RewardPotionProfileReader.Capture(draft, Entities) };
        if (inputProfile != null && (rewardPotionProfile
                ? !IsRewardPotionPage(draft)
                : !IsOrdinaryRewardPage(draft)))
        {
            draft = draft with
            {
                Surface = new UnsupportedSurface(
                    draft.Surface.Kind,
                    rewardPotionProfile ? "reward_potion_page_profile" : "ordinary_reward_page_profile",
                    rewardPotionProfile
                        ? $"This current page is outside the exact reward/potion input profile: {draft.RewardPotionFacts?.Reason}."
                        : "This current page is outside the ordinary reward input profile."),
                Readiness = "degraded",
                Completeness = new StateCompleteness(
                    "degraded", "empty_fail_closed",
                    new[] { "ordinary reward current-page native owner" },
                    new[] { rewardPotionProfile ? "reward_potion_page_profile_unsupported"
                        : "ordinary_reward_page_profile_unsupported" })
            };
        }
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

        PersistentVisibleStateBuildResult shared = Measure(
            "persistent_visible_state",
            () => game.Compatibility.StateObservationAllowed
                ? PersistentVisibleStateReader.Build(Entities)
                : new PersistentVisibleStateBuildResult(false, null, null));
        long nativeSequence = RewardPageIdentity.ObserveNative(
            CommonNativeSignature(game, nativeDraft, shared.State));
        draft = LiveObservationReader.ApplyMissingPersistentStatePolicy(draft, shared);
        IReadOnlyCollection<string>? selectedReadKinds = inputProfile == null
            ? requiredReadKinds ?? requiredReadKindsForInteraction?.Invoke(draft.Surface.Kind)
            : null;
        IReadOnlyDictionary<string, PlayerReadBuildResult> readBuilds = Measure(
            "required_read_builds",
            () => BuildRequiredReads(selectedReadKinds, draft.Context));
        PlayerVisibilityProjection information = Measure("visibility_catalog", () =>
        {
            bool shopCatalogAvailable = ShopSurfaceFacts.TryGetCurrent(out _, out _, out _);
            return PlayerVisibilityCatalog.Build(draft, shared.State != null, shopCatalogAvailable);
        });
        IReadOnlyList<PlayerReadCatalogEntry> readCatalog = inputProfile == null
            ? information.ReadCatalog
            : Array.Empty<PlayerReadCatalogEntry>();
        IReadOnlyList<PlayerEnvironmentLinkedDetailCatalogEntry> linkedDetails =
            inputProfile == null
                ? BuildLinkedDetailCatalog(draft.Surface)
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
            () => JsonSerializer.SerializeToNode(
                draft.Surface,
                draft.Surface.GetType(),
                ConnectorMod._jsonOptions) ?? new JsonObject());
        PlayerEnvironmentInteractionContent surfaceContent = Measure(
            "visible_surface_projection",
            () => ProjectVisibleFacts(draft.Surface, draft.Context));
        if (inputProfile != null && draft.Surface is CardRewardSelectionSurface
            && draft.RewardPageFacts is { Qualified: true } profileFacts
            && surfaceContent.Surface is JsonObject page)
            ProjectRewardPageFacts(page, profileFacts);
        if (rewardPotionProfile && draft.RewardPotionFacts is { Qualified: true } potionFacts
            && surfaceContent.Surface is JsonObject potionPage)
            ProjectRewardPotionFacts(potionPage, draft.Surface, potionFacts);
        IReadOnlyList<NativeUiBoundAction> nativeBindings = Measure(
            "native_binding_catalog",
            () => CanPublishMutationAuthority(draft.Readiness)
                ? textMenuCapture && draft.Surface is CombatTurnSurface combat
                    ? BuildTextCombatBindings(draft, combat)
                    : rewardPotionProfile
                    ? NativeUiActionRuntime.BuildRewardPotionBindings(draft)
                    : BuildPlayerEnvironmentBindings(draft)
                : Array.Empty<NativeUiBoundAction>());
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
            () => BuildFactReferents(surfaceContent));
        BoundActionProjectionResult projected = Measure(
            "bound_action_projection",
            () => ProjectBoundActions(nativeBindings, interactionId, referents));
        bool profileCatalogIncomplete = inputProfile != null
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
                $"read:{entry.Kind}:{entry.EntityId}",
                entry.Kind,
                entry.EntityId,
                PlayerEnvironmentContract.ReadContentSchema(entry.Kind),
                entry.VisibilityBasis,
                SnapshotBound: true,
                "single_entity",
                Array.Empty<string>())))
            .OrderBy(read => read.ReadId, StringComparer.Ordinal)
            .ToArray();
        string signature = Measure("stable_snapshot_signature", () => StableIdentityHash.Object(new
        {
            game.Version,
            game.Commit,
            shared.State,
            draft.Readiness,
            surface = surfaceContent,
            interactionId,
            referents = referents.Values.OrderBy(item => item.ReferentId, StringComparer.Ordinal).ToArray(),
            authority = CanonicalAuthoritySignature(nativeBindings),
            reads,
            visibility.HiddenByPolicy
        }));
        if (rewardPotionProfile && draft.RewardPotionFacts is { } exact)
            signature = RewardPotionViewSignature(signature, exact);
        (string snapshotId, long sequence) = inputProfile == null
            ? RewardPageIdentity.ObserveLegacy(nativeSequence, signature)
            : rewardPotionProfile
                ? RewardPageIdentity.ObserveRewardPotion(nativeSequence, signature)
                : RewardPageIdentity.ObserveReward(nativeSequence, signature);
        bool visibleUnsupported = draft.Surface is UnsupportedSurface
            || profileCatalogIncomplete;
        bool actionsPublished = projected.Projection.Status == "complete"
            && projected.Projection.MaterializedCount > 0;
        bool textCombatEntryReady = CanPublishTextCombatEntry(
            textMenuCapture, draft.Surface, draft.Readiness,
            draft.Completeness, projected.Projection);
        string status = actionsPublished || textCombatEntryReady
            ? "interactive"
            : visibleUnsupported ? "visible_unsupported" : draft.Readiness == "settling" ? "settling" : "observed";
        PlayerEnvironmentCompleteness completeness = ToCompleteness(
            draft.Completeness,
            visibility.HiddenByPolicy,
            visibleUnsupported ? "visible_unmapped" : null);
        if (projected.Projection.Status != "complete")
        {
            completeness = completeness with
            {
                Status = "partial",
                Missing = completeness.Missing
                    .Append("finite_bound_action_projection_incomplete")
                    .Distinct(StringComparer.Ordinal)
                    .ToArray()
            };
        }
        var snapshot = new PlayerEnvironmentSnapshot(
            PlayerEnvironmentContract.ProtocolVersion,
            inputProfile == null
                ? PlayerEnvironmentContract.SnapshotSchema
                : rewardPotionProfile
                    ? PlayerEnvironmentContract.RewardPotionSnapshotSchema
                    : PlayerEnvironmentContract.OrdinaryRewardSnapshotSchema,
            snapshotId,
            sequence,
            DateTimeOffset.UtcNow,
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
                inputProfile == null
                    ? SurfaceContentSchema(draft.Surface.Kind)
                    : $"sts2.player-environment/surface/{draft.Surface.Kind}-{(rewardPotionProfile ? 3 : 2)}",
                surfaceContent,
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
            readBuilds);
    }

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
        PersistentVisibleState? sharedState) =>
        StableIdentityHash.Object(new
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
