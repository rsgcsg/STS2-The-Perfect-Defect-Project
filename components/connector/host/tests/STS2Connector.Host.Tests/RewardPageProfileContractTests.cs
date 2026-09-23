using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Rewards;
using MegaCrit.Sts2.Core.Rewards;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Tests;

public sealed class RewardPageProfileContractTests
{
    private static readonly GameBuildIdentity Game = new("v", "commit", null, null,
        new CompatibilityAssessment("test", true, true, false, "test"));

    [Fact]
    public void ProfileAdmissionIsLimitedToExactCompleteOrdinaryRewardPages()
    {
        var outer = Observation(new RewardClaimSurface("reward_claim", "screen",
            new[] { new VisibleReward("reward-1", "card", "Cards", null, true) },
            false, Array.Empty<VisibleCombatPotion>(), true, false),
            "contract_complete_for_reward_claim");
        Assert.True(PlayerEnvironmentService.IsOrdinaryRewardPage(outer));
        Assert.False(PlayerEnvironmentService.IsOrdinaryRewardPage(outer with
        {
            Readiness = "settling"
        }));
        Assert.False(PlayerEnvironmentService.IsOrdinaryRewardPage(outer with
        {
            Completeness = outer.Completeness with { PlayerVisibleSemantics = "partial" }
        }));
        Assert.False(PlayerEnvironmentService.IsOrdinaryRewardPage(outer with
        {
            Surface = ((RewardClaimSurface)outer.Surface) with
            {
                DiscardablePotions = new[] { new VisibleCombatPotion(
                    "potion", "POTION", "Potion", null, 0, "none", false, false) }
            }
        }));
        Assert.False(PlayerEnvironmentService.IsOrdinaryRewardPage(outer with
        {
            Surface = new UnsupportedSurface("reward_claim", "NLinkedRewardSet", "linked")
        }));

        var inner = Observation(new CardRewardSelectionSurface(
            "card_reward_selection", "inner", Array.Empty<VisibleCard>(),
            Array.Empty<VisibleCardRewardAlternative>()),
            "contract_complete_for_card_reward_selection");
        Assert.False(PlayerEnvironmentService.IsOrdinaryRewardPage(inner));
        Assert.True(PlayerEnvironmentService.IsOrdinaryRewardPage(inner with
        {
            RewardPageFacts = new RewardPageProfileFacts(true,
                Array.Empty<RewardPageAlternativeEffect>())
        }));
    }

    [Fact]
    public void PrivateParentAndExactAlternativeMustQualifyTypedCurrentEffect()
    {
        var parent = (CardReward)RuntimeHelpers.GetUninitializedObject(typeof(CardReward));
        var alternative = (CardRewardAlternative)RuntimeHelpers.GetUninitializedObject(
            typeof(CardRewardAlternative));
        var visible = new[] { new VisibleCardRewardAlternative("alt-1", 0, "Skip", true) };
        NativeCardRewardParentFacts facts = new("captured", parent,
            new[] { new NativeCardRewardAlternativeFact(alternative, "skip",
                PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward) }, null);
        RewardPageProfileFacts qualified = CardRewardSurfaceReader.QualifyProfileFacts(
            true, facts, new[] { alternative }, visible);
        Assert.True(qualified.Qualified);
        Assert.Equal("return_to_rewards_without_claim",
            Assert.Single(qualified.AlternativeEffects).Effect);
        Assert.Empty(CardRewardSurfaceReader.QualifyProfileFacts(
            true, facts with { Status = "parent_not_bound" },
            new[] { alternative }, visible).AlternativeEffects);
        Assert.False(CardRewardSurfaceReader.QualifyProfileFacts(
            false, facts, new[] { alternative }, visible).Qualified);
        Assert.False(CardRewardSurfaceReader.QualifyProfileFacts(
            true, facts with { Alternatives = new[] {
                facts.Alternatives[0] with { AfterSelected = PostAlternateCardRewardAction.DoNothing }
            } }, new[] { alternative }, visible).Qualified);
        var wrong = (CardRewardAlternative)RuntimeHelpers.GetUninitializedObject(
            typeof(CardRewardAlternative));
        Assert.False(CardRewardSurfaceReader.QualifyProfileFacts(
            true, facts, new[] { wrong }, visible).Qualified);
    }

    [Fact]
    public void CurrentOuterProjectionDoesNotBackfillUnopenedCardContents()
    {
        var context = new RewardFlowLiveContext("reward_flow", "room_rewards");
        var outer = new RewardClaimSurface("reward_claim", "screen",
            new[] { new VisibleReward("reward-1", "card", "Choose a card", null, true) },
            false, Array.Empty<VisibleCombatPotion>(), true, false);
        var outerFacts = PlayerEnvironmentService.ProjectVisibleFacts(outer, context);

        Assert.Contains("reward-1", outerFacts.Surface.ToJsonString());
        Assert.DoesNotContain("cards", outerFacts.Surface.ToJsonString(),
            StringComparison.OrdinalIgnoreCase);

        var inner = new CardRewardSelectionSurface("card_reward_selection", "inner",
            new[] { new VisibleCard("card-1", "CARD", "Card", "Skill", "1", null,
                "Draw two cards.", "Common", false, false, null) },
            Array.Empty<VisibleCardRewardAlternative>());
        var innerFacts = PlayerEnvironmentService.ProjectVisibleFacts(inner,
            new RewardFlowLiveContext("reward_flow", "card_reward"));
        Assert.Contains("Draw two cards.", innerFacts.Surface.ToJsonString());
    }

    [Fact]
    public void RewardProfileProjectsCapturedCurrentLabelsWithoutChangingRawNativeIdentity()
    {
        var legacyCard = new VisibleCard("card-1", "CARD", "Card", "Skill", "1", null,
            "legacy Hand description", "Common", false, false, null);
        Assert.True(CardRewardSurfaceReader.TryProjectRenderedCard(legacyCard,
            "[center]reward None description[/center]", "2", true,
            out VisibleCard captured));
        Assert.Equal("reward None description", captured.Description);
        Assert.Equal("2", captured.Cost);
        Assert.True(CardRewardSurfaceReader.TryProjectRenderedCard(legacyCard,
            "Visible card text", "-1", false, out VisibleCard hiddenCost));
        Assert.Equal(string.Empty, hiddenCost.Cost);
        Assert.False(CardRewardSurfaceReader.TryProjectRenderedCard(legacyCard,
            null, "2", true, out _));
        Assert.False(CardRewardSurfaceReader.TryProjectRenderedCard(legacyCard,
            "Visible card text", null, true, out _));
        var nativeDraft = Observation(new CardRewardSelectionSurface(
            "card_reward_selection", "inner", new[] { legacyCard },
            Array.Empty<VisibleCardRewardAlternative>()),
            "contract_complete_for_card_reward_selection");
        var profiledDraft = nativeDraft with
        {
            RewardPageFacts = new RewardPageProfileFacts(true,
                Array.Empty<RewardPageAlternativeEffect>())
            { CurrentCards = new[] { captured } }
        };

        Assert.Equal(PlayerEnvironmentService.CommonNativeSignature(Game, nativeDraft, null),
            PlayerEnvironmentService.CommonNativeSignature(Game, profiledDraft, null));
        var projected = PlayerEnvironmentService.ProjectVisibleFacts(
            profiledDraft.Surface, profiledDraft.Context);
        var page = Assert.IsType<JsonObject>(projected.Surface);
        PlayerEnvironmentService.ProjectRewardPageFacts(page, profiledDraft.RewardPageFacts!);
        Assert.Contains("reward None description", page.ToJsonString());
        Assert.DoesNotContain("legacy Hand description", page.ToJsonString());
        Assert.Contains("legacy Hand description", PlayerEnvironmentService.ProjectVisibleFacts(
            nativeDraft.Surface, nativeDraft.Context).Surface.ToJsonString());
    }

    [Fact]
    public void ProfileGuardAndReplayFingerprintDistinguishModes()
    {
        Assert.True(PlayerEnvironmentService.IsSupportedInputProfile(null));
        Assert.True(PlayerEnvironmentService.IsSupportedInputProfile(
            PlayerEnvironmentContract.OrdinaryRewardPageProfile));
        Assert.False(PlayerEnvironmentService.IsSupportedInputProfile(""));
        Assert.False(PlayerEnvironmentService.IsSupportedInputProfile("ordinary-reward-page-v2"));

        var legacy = new PlayerEnvironmentActionRequest("request", "snapshot", "action",
            "client", "lease", 1);
        var reward = legacy with { InputProfile = PlayerEnvironmentContract.OrdinaryRewardPageProfile };
        Assert.NotEqual(PlayerEnvironmentService.ActionRequestFingerprint(legacy),
            PlayerEnvironmentService.ActionRequestFingerprint(reward));
        Assert.Equal(PlayerEnvironmentService.ActionRequestFingerprint(reward),
            PlayerEnvironmentService.ActionRequestFingerprint(reward with { }));
        var observed = new PlayerEnvironmentSnapshot("1.0.0",
            PlayerEnvironmentContract.OrdinaryRewardSnapshotSchema,
            "snapshot", 1, DateTimeOffset.UtcNow, "interactive", null, null!,
            Array.Empty<PlayerEnvironmentReferent>(), null!,
            Array.Empty<PlayerEnvironmentReadOpportunity>(), null!, null!, null!)
        { InputProfile = PlayerEnvironmentContract.OrdinaryRewardPageProfile };
        Assert.True(PlayerEnvironmentService.IsCurrentRequestSnapshot(observed, reward));
        Assert.False(PlayerEnvironmentService.IsCurrentRequestSnapshot(observed, legacy));
        Assert.False(PlayerEnvironmentService.IsCurrentRequestSnapshot(observed,
            reward with { ExpectedSnapshotId = "stale" }));

        var receipt = new PlayerEnvironmentActionReceipt("1.0.0",
            PlayerEnvironmentContract.ReceiptSchema, "request", "delivered",
            new PlayerEnvironmentActionSummary("action", "activate", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>()), null, null,
            new PlayerEnvironmentRetryPolicy(false, "fresh_snapshot_required"), observed)
        { InputProfile = PlayerEnvironmentContract.OrdinaryRewardPageProfile };
        Assert.True(PlayerEnvironmentService.ReceiptMatchesInputProfile(receipt,
            PlayerEnvironmentContract.OrdinaryRewardPageProfile));
        Assert.False(PlayerEnvironmentService.ReceiptMatchesInputProfile(receipt, null));
        Assert.Equal(PlayerEnvironmentContract.OrdinaryRewardPageProfile,
            JsonNode.Parse(JsonSerializer.Serialize(receipt, ConnectorMod._jsonOptions))?
                ["input_profile"]?.GetValue<string>());
        Assert.DoesNotContain("input_profile", JsonSerializer.Serialize(
            legacy, ConnectorMod._jsonOptions));
    }

    [Fact]
    public void UnsupportedSubmitProfileReturnsBeforeAnyNativeSnapshotOrInput()
    {
        var request = new PlayerEnvironmentActionRequest(
            "request-unknown-profile", "snapshot", "action", "client", "lease", 1,
            "ordinary-reward-page-v2");

        PlayerEnvironmentActionReceipt receipt = PlayerEnvironmentService.Submit(request);

        Assert.Equal("not_delivered", receipt.Delivery);
        Assert.Equal("unsupported_input_profile", receipt.ReasonCode);
        Assert.Null(receipt.Successor);
    }

    private static LiveObservation Observation(ILiveSurface surface, string completeness) =>
        new("native", "ready", new RewardFlowLiveContext("reward_flow", "room_rewards"),
            surface, new StateCompleteness(completeness, "exact",
                Array.Empty<string>(), Array.Empty<string>()), Game,
            Array.Empty<string>());
}
