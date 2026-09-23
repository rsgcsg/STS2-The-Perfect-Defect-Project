using STS2Connector.PlayerEnvironment;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.Tests;

public sealed class RewardPageSnapshotIdentityTests
{
    [Fact]
    public void UnchangedLegacyObserveDoesNotStaleRewardPage()
    {
        var identity = new RewardPageSnapshotIdentity();
        var before = identity.ObserveReward(identity.ObserveNative("native-a"), "reward-a");

        identity.ObserveLegacy(identity.ObserveNative("native-a"), "legacy-a");
        var submit = identity.ObserveReward(identity.ObserveNative("native-a"), "reward-a");

        Assert.Equal(before, submit);
    }

    [Fact]
    public void ProductionNativeFingerprintExcludesProfileOnlyFacts()
    {
        var game = new GameBuildIdentity("v", "commit", null, null,
            new CompatibilityAssessment("test", true, true, false, "test"));
        var surface = new CardRewardSelectionSurface(
            "card_reward_selection", "screen", Array.Empty<VisibleCard>(),
            Array.Empty<VisibleCardRewardAlternative>());
        var raw = new LiveObservation("native-page", "ready",
            new RewardFlowLiveContext("reward_flow", "card_reward"), surface,
            new StateCompleteness("complete", "exact", Array.Empty<string>(),
                Array.Empty<string>()), game, Array.Empty<string>());
        var profiled = raw with
        {
            RewardPageFacts = new RewardPageProfileFacts(true,
                Array.Empty<RewardPageAlternativeEffect>())
        };

        Assert.Equal(
            PlayerEnvironmentService.CommonNativeSignature(game, raw, null),
            PlayerEnvironmentService.CommonNativeSignature(game, profiled, null));
        Assert.NotEqual(
            PlayerEnvironmentService.CommonNativeSignature(game, raw, null),
            PlayerEnvironmentService.CommonNativeSignature(game,
                raw with { Signature = "different-native-page" }, null));
    }

    [Fact]
    public void UnchangedRewardObserveDoesNotStaleLegacyPage()
    {
        var identity = new RewardPageSnapshotIdentity();
        var before = identity.ObserveLegacy(identity.ObserveNative("native-a"), "legacy-a");

        identity.ObserveReward(identity.ObserveNative("native-a"), "reward-a");
        var submit = identity.ObserveLegacy(identity.ObserveNative("native-a"), "legacy-a");

        Assert.Equal(before, submit);
    }

    [Fact]
    public void LegacyOnlyNativeTransitionStalesEarlierRewardPage()
    {
        var identity = new RewardPageSnapshotIdentity();
        var before = identity.ObserveReward(identity.ObserveNative("native-a"), "reward-a");

        identity.ObserveNative("native-b");
        identity.ObserveNative("native-a");
        var submit = identity.ObserveReward(identity.ObserveNative("native-a"), "reward-a");

        Assert.NotEqual(before.StateId, submit.StateId);
    }

    [Fact]
    public void RewardOnlyNativeTransitionStalesEarlierLegacyPage()
    {
        var identity = new RewardPageSnapshotIdentity();
        var before = identity.ObserveLegacy(identity.ObserveNative("native-a"), "legacy-a");

        identity.ObserveReward(identity.ObserveNative("native-b"), "reward-b");
        identity.ObserveReward(identity.ObserveNative("native-a"), "reward-a");
        var submit = identity.ObserveLegacy(identity.ObserveNative("native-a"), "legacy-a");

        Assert.NotEqual(before.StateId, submit.StateId);
    }

    [Fact]
    public void SameNativeWithChangedProfileBindingStalesEarlierRewardPage()
    {
        var identity = new RewardPageSnapshotIdentity();
        long native = identity.ObserveNative("native-a");
        var before = identity.ObserveReward(native, "button-generation-1");
        var submit = identity.ObserveReward(identity.ObserveNative("native-a"), "button-generation-2");

        Assert.NotEqual(before.StateId, submit.StateId);
    }
}
