using System.Text.Json.Nodes;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.Tests;

public sealed class RewardPotionProfileContractTests
{
    private static readonly GameBuildIdentity Game = new("v", "commit", null, null,
        new CompatibilityAssessment("test", true, true, false, "test"));

    [Fact]
    public void V2OuterKeepsRewardAndProceedButReplacesLegacyDirectDiscardWithOpen()
    {
        var surface = new RewardClaimSurface("reward_claim", "reward-screen",
            new[] { new VisibleReward("reward-1", "card", "Choose a card", null, true) },
            true, new[] { new VisibleCombatPotion("potion", "POTION", "Test potion",
                null, 0, "none", false, false) }, true, false);
        var draft = Observation(surface) with {
            Completeness = new StateCompleteness("contract_complete_for_reward_claim", "exact",
                Array.Empty<string>(), Array.Empty<string>()),
            RewardPotionFacts = new RewardPotionProfileFacts(true, "exact_holder",
                new[] { new RewardPotionOpenOption("potion", 0, "Test potion",
                    "holder", null!) })
        };
        Assert.False(PlayerEnvironmentService.IsOrdinaryRewardPage(draft));
        Assert.True(PlayerEnvironmentService.IsRewardPotionPage(draft));
        IReadOnlyList<NativeUiBoundAction> bindings =
            NativeUiActionRuntime.BuildRewardPotionBindings(draft);
        Assert.Contains(bindings, value => value.Candidate.Operation == "open_potion_popup");
        Assert.Contains(bindings, value => value.Candidate.Operation == "claim_reward");
        Assert.DoesNotContain(bindings, value => value.Candidate.Operation == "discard_potion_for_reward");

        JsonObject page = Assert.IsType<JsonObject>(PlayerEnvironmentService
            .ProjectVisibleFacts(surface, draft.Context).Surface);
        PlayerEnvironmentService.ProjectRewardPotionFacts(page, surface, draft.RewardPotionFacts!);
        Assert.False(page.ContainsKey("discardable_potions"));
        Assert.Equal("potion", page["openable_potions"]?[0]?["potion_entity_id"]?.GetValue<string>());
        var refs = PlayerEnvironmentService.ProjectFactReferents(page);
        BoundActionProjectionResult projected = PlayerEnvironmentService.ProjectBoundActions(
            bindings, surface.ScreenEntityId, refs);
        Assert.Equal("complete", projected.Projection.Status);
        var open = Assert.Single(projected.Projection.Actions,
            value => value.SubjectReferentId == "potion");
        Assert.Equal("open", open.Verb);
        Assert.Empty(open.Arguments);
    }

    [Fact]
    public void V2PopupDescribesOneNativeUseButtonEvenWhenLegacyExpandsTargets()
    {
        var popup = new PotionPopupSurface("potion_popup", "popup", "potion", "POTION",
            "Test potion", 0, true, true)
        {
            DirectCombatUse = true,
            UseTargetEntityIds = new[] { "enemy-a", "enemy-b" }
        };
        var controls = new[] {
            new RewardPotionControlOption("use", "use-control", true, "Use potion"),
            new RewardPotionControlOption("discard", "discard-control", true, "Discard potion"),
            new RewardPotionControlOption("close", "popup:close", true, "Close popup")
        };
        var draft = Observation(popup) with {
            RewardPotionFacts = new RewardPotionProfileFacts(true, "current_popup",
                Array.Empty<RewardPotionOpenOption>()) { PopupControls = controls }
        };
        Assert.Equal(2, NativeUiActionRuntime.DescribePotionPopupCommands(popup)
            .Count(value => value.Kind == "use_potion"));

        IReadOnlyList<NativeUiBoundAction> bindings =
            NativeUiActionRuntime.BuildRewardPotionBindings(draft);
        Assert.Equal(new[] { "choose_potion_use", "discard_potion", "cancel_potion_popup" },
            bindings.Select(value => value.Candidate.Operation));
        Assert.DoesNotContain(bindings, value => value.Candidate.Operation == "use_potion"
            || value.Candidate.EntityBindings.Any(entity => entity.Role == "target"));

        JsonObject page = Assert.IsType<JsonObject>(PlayerEnvironmentService
            .ProjectVisibleFacts(popup, draft.Context).Surface);
        PlayerEnvironmentService.ProjectRewardPotionFacts(page, popup, draft.RewardPotionFacts!);
        Assert.False(page.ContainsKey("direct_combat_use"));
        Assert.False(page.ContainsKey("use_target_entity_ids"));
        var refs = PlayerEnvironmentService.ProjectFactReferents(page);
        BoundActionProjectionResult projected = PlayerEnvironmentService.ProjectBoundActions(
            bindings, popup.ScreenEntityId, refs);
        Assert.Equal("complete", projected.Projection.Status);
        Assert.Equal(3, projected.Projection.TotalCount);
        var use = Assert.Single(projected.Projection.Actions,
            value => value.SubjectReferentId == "use-control");
        var discard = Assert.Single(projected.Projection.Actions,
            value => value.SubjectReferentId == "discard-control");
        var close = Assert.Single(projected.Projection.Actions,
            value => value.SubjectReferentId == "popup:close");
        Assert.Equal("activate", use.Verb);
        Assert.Equal("activate", discard.Verb);
        Assert.Equal("cancel", close.Verb);
        Assert.Equal("potion", Assert.Single(use.Arguments).ReferentId);
        Assert.Equal("potion", Assert.Single(discard.Arguments).ReferentId);
        Assert.Empty(close.Arguments);
    }

    [Fact]
    public void V2QualificationAndCommonGenerationFailClosedAcrossThreeProfiles()
    {
        var popup = Observation(new PotionPopupSurface("potion_popup", "popup", "potion",
            "POTION", "Test potion", 0, false, true)) with {
            RewardPotionFacts = new RewardPotionProfileFacts(true, "current_popup",
                Array.Empty<RewardPotionOpenOption>())
        };
        Assert.True(PlayerEnvironmentService.IsRewardPotionPage(popup));
        Assert.False(PlayerEnvironmentService.IsRewardPotionPage(popup with {
            RewardPotionFacts = popup.RewardPotionFacts! with { Qualified = false }
        }));
        Assert.False(PlayerEnvironmentService.IsRewardPotionPage(popup with {
            Readiness = "settling"
        }));
        Assert.False(RewardPotionProfileReader.ExactCurrentOwner("popup-a", "popup-b"));
        Assert.True(RewardPotionProfileReader.ExactCurrentOwner("popup-a", "popup-a"));
        Assert.False(RewardPotionProfileReader.ExactOpenControlReady(
            true, true, true, true, true, true, true, false));
        Assert.False(RewardPotionProfileReader.ExactOpenControlReady(
            true, true, true, false, true, true, false, false));
        Assert.True(RewardPotionProfileReader.ExactOpenControlReady(
            true, true, true, false, true, true, true, false));

        // Same popup/buttons and same kind of underlying reward, but a different
        // exact reward screen must invalidate the old v2 delivery token.
        var samePopupA = popup.RewardPotionFacts! with {
            RewardOwnerKind = "reward_claim", RewardOwnerEntityId = "reward-screen-a",
            PopupControls = new[] { new RewardPotionControlOption(
                "close", "popup:close", true, "Close") }
        };
        var samePopupB = samePopupA with { RewardOwnerEntityId = "reward-screen-b" };
        string viewA = PlayerEnvironmentService.RewardPotionViewSignature("same-public-popup", samePopupA);
        string viewB = PlayerEnvironmentService.RewardPotionViewSignature("same-public-popup", samePopupB);
        Assert.NotEqual(viewA, viewB);
        var ownerIdentity = new RewardPageSnapshotIdentity();
        long ownerNative = ownerIdentity.ObserveNative("same-native-popup");
        var ownerBefore = ownerIdentity.ObserveRewardPotion(ownerNative, viewA);
        var ownerAfter = ownerIdentity.ObserveRewardPotion(
            ownerIdentity.ObserveNative("same-native-popup"), viewB);
        Assert.NotEqual(ownerBefore.StateId, ownerAfter.StateId);

        var raw = popup with { RewardPotionFacts = null };
        Assert.Equal(PlayerEnvironmentService.CommonNativeSignature(Game, raw, null),
            PlayerEnvironmentService.CommonNativeSignature(Game, popup, null));
        var identity = new RewardPageSnapshotIdentity();
        var v2Before = identity.ObserveRewardPotion(identity.ObserveNative("native-a"), "v2-a");
        var v1Before = identity.ObserveReward(identity.ObserveNative("native-a"), "v1-a");
        var legacyBefore = identity.ObserveLegacy(identity.ObserveNative("native-a"), "legacy-a");
        Assert.Equal(v2Before,
            identity.ObserveRewardPotion(identity.ObserveNative("native-a"), "v2-a"));
        Assert.Equal(v1Before,
            identity.ObserveReward(identity.ObserveNative("native-a"), "v1-a"));
        identity.ObserveRewardPotion(identity.ObserveNative("native-b"), "v2-b");
        identity.ObserveRewardPotion(identity.ObserveNative("native-a"), "v2-a");
        Assert.NotEqual(legacyBefore.StateId,
            identity.ObserveLegacy(identity.ObserveNative("native-a"), "legacy-a").StateId);
        Assert.NotEqual(v1Before.StateId,
            identity.ObserveReward(identity.ObserveNative("native-a"), "v1-a").StateId);
        Assert.NotEqual(v2Before.StateId,
            identity.ObserveRewardPotion(identity.ObserveNative("native-a"), "v2-a").StateId);
        Assert.NotEqual(v2Before.StateId,
            identity.ObserveRewardPotion(identity.ObserveNative("native-a"), "changed-holder").StateId);
    }

    [Fact]
    public void V2ProfileIdentityDoesNotReplayAcrossOldViews()
    {
        Assert.True(PlayerEnvironmentService.IsSupportedInputProfile(
            PlayerEnvironmentContract.RewardPotionPageProfile));
        var request = new PlayerEnvironmentActionRequest("r", "s", "a", "c", "l", 1)
        { InputProfile = PlayerEnvironmentContract.RewardPotionPageProfile };
        Assert.NotEqual(PlayerEnvironmentService.ActionRequestFingerprint(request),
            PlayerEnvironmentService.ActionRequestFingerprint(request with {
                InputProfile = PlayerEnvironmentContract.OrdinaryRewardPageProfile
            }));
        Assert.NotEqual(PlayerEnvironmentService.ActionRequestFingerprint(request),
            PlayerEnvironmentService.ActionRequestFingerprint(request with { InputProfile = null }));
        var observed = new PlayerEnvironmentSnapshot("1.0.0",
            PlayerEnvironmentContract.RewardPotionSnapshotSchema,
            "s", 1, DateTimeOffset.UtcNow, "interactive", null, null!,
            Array.Empty<PlayerEnvironmentReferent>(), null!,
            Array.Empty<PlayerEnvironmentReadOpportunity>(), null!, null!, null!)
        { InputProfile = PlayerEnvironmentContract.RewardPotionPageProfile };
        Assert.True(PlayerEnvironmentService.IsCurrentRequestSnapshot(observed, request));
        Assert.False(PlayerEnvironmentService.IsCurrentRequestSnapshot(observed,
            request with { InputProfile = PlayerEnvironmentContract.OrdinaryRewardPageProfile }));
    }

    private static LiveObservation Observation(ILiveSurface surface) =>
        new("native", "ready", new RewardFlowLiveContext("reward_flow", "room_rewards"),
            surface, new StateCompleteness("contract_complete_for_native_potion_popup", "exact",
                Array.Empty<string>(), Array.Empty<string>()), Game, Array.Empty<string>());
}
