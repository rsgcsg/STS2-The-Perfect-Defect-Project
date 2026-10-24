using STS2Connector.LiveHost;
using Xunit;

namespace STS2Connector.Host.Tests;

public sealed class RewardStateCompletenessTests
{
    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void ExactEmptyRewardStateRemainsCompleteWhenProceedIsDisabled(bool hasCommand)
    {
        var state = RewardClaimSurfaceReader.DescribeCompleteness(true, hasCommand);
        Assert.Equal("contract_complete_for_reward_claim", state.PlayerVisibleSemantics);
        Assert.Empty(state.Missing);
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void MissingExactPresentationBindingNeverBecomesComplete(bool hasCommand)
    {
        var state = RewardClaimSurfaceReader.DescribeCompleteness(false, hasCommand);
        Assert.Equal("partial", state.PlayerVisibleSemantics);
        Assert.Contains("native_reward_presentation_bijection", state.Missing);
    }
    [Theory]
    [InlineData(true, true, true)]
    [InlineData(true, false, false)]
    [InlineData(false, true, false)]
    [InlineData(false, false, false)]
    public void NativeTerminalProceedLatchOwnsTransitionReadiness(
        bool terminal, bool disabledForever, bool pending)
    {
        Assert.Equal(pending,
            STS2Platform.NativeFoundation.NativeRewardDecisionProvider.TerminalProceedPending(
                terminal, disabledForever));
    }

    [Fact]
    public void ExactNativeProceedLatchAccessorReadsWithoutChangingTheNativeField()
    {
        var screen = (MegaCrit.Sts2.Core.Nodes.Screens.NRewardsScreen)
            System.Runtime.CompilerServices.RuntimeHelpers.GetUninitializedObject(
                typeof(MegaCrit.Sts2.Core.Nodes.Screens.NRewardsScreen));
        var field = screen.GetType().GetField("_disableProceedForever",
            System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!;
        field.SetValue(screen, true);
        var read = typeof(STS2Platform.NativeFoundation.NativeRewardDecisionProvider).GetMethod(
            "DisableProceedForever", System.Reflection.BindingFlags.Static
                | System.Reflection.BindingFlags.NonPublic)!;
        Assert.True((bool)read.Invoke(null, new object[] { screen })!);
        Assert.True((bool)field.GetValue(screen)!);
        field.SetValue(screen, false);
        Assert.False((bool)read.Invoke(null, new object[] { screen })!);
    }

}
