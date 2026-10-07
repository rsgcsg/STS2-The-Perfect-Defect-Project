using STS2Connector.PlayerEnvironment;
using Xunit;

namespace STS2Connector;

public sealed class NativeTipReturnTests
{
    // Behavioral fixture for the inspected 9cb4f1ad game build's NCardHolder:
    // repeated focus entry creates no tip until its focus-exit lifecycle runs.
    // This is not a proprietary source copy or a runtime qualification substitute.
    private sealed class LatchedTipSource
    {
        internal bool Focused;
        internal object? Tip;
        internal int Created;
        internal int HandHoverNotifications;
        internal void Enter()
        {
            HandHoverNotifications++;
            if (Focused) return;
            Focused = true;
            Tip = new object();
            Created++;
        }
        internal void Exit()
        {
            HandHoverNotifications--;
            Focused = false;
            Tip = null;
        }
        internal void Remove() => Tip = null;
    }

    [Fact]
    public void ReturnBalancesFocusSoTheSameNativeHolderCanReopenTips()
    {
        var original = new LatchedTipSource();
        original.Enter();
        original.Remove(); // The old adapter removed only the rendered tip.
        original.Enter();
        Assert.Null(original.Tip);
        Assert.Equal(1, original.Created);

        var source = new LatchedTipSource();
        source.Enter();
        object? opened = source.Tip;
        bool ownerCleared = false;
        var calls = new List<string>();
        var result = NativeTipReturn.Close(true,
            () => ReferenceEquals(source.Tip, opened), () => true,
            () => { calls.Add("exit"); source.Exit(); },
            () => { calls.Add("remove"); source.Remove(); },
            () => { calls.Add("clear"); ownerCleared = true; });
        Assert.True(result.Accepted);
        Assert.Equal(new[] { "exit", "remove", "clear" }, calls);
        Assert.True(ownerCleared);
        Assert.False(source.Focused);
        Assert.Equal(0, source.HandHoverNotifications);
        source.Enter();
        Assert.NotNull(source.Tip);
        Assert.NotSame(opened, source.Tip);
        Assert.Equal(2, source.Created);
    }

    [Theory]
    [InlineData(false, true, "native_information_owner_changed")]
    [InlineData(true, false, "native_tip_source_changed")]
    public void InvalidOwnerOrEntrySourceFailsBeforeAnyNativeExitOrRemoval(
        bool ownerCurrent, bool sourceCurrent, string reason)
    {
        var calls = new List<string>();
        var result = NativeTipReturn.Close(true, () => ownerCurrent, () => sourceCurrent,
            () => calls.Add("exit"), () => calls.Add("remove"), () => calls.Add("clear"));
        Assert.False(result.Accepted);
        Assert.Equal(reason, result.ErrorCode);
        Assert.Empty(calls);
    }

    [Fact]
    public void DirectRelicTipsHaveNoSignalEntryToBalance()
    {
        var calls = new List<string>();
        var result = NativeTipReturn.Close(false, () => true,
            () => throw new InvalidOperationException("Direct relic has no signal source"),
            () => throw new InvalidOperationException("Direct relic must not emit an exit"),
            () => calls.Add("remove"), () => calls.Add("clear"));
        Assert.True(result.Accepted);
        Assert.Equal(new[] { "remove", "clear" }, calls);
    }

    [Fact]
    public void ExitExceptionRetainsFailureAndNeverRunsCleanupOrRetries()
    {
        int exits = 0, removals = 0, clears = 0;
        Assert.Throws<InvalidOperationException>(() => NativeTipReturn.Close(true,
            () => true, () => true,
            () => { exits++; throw new InvalidOperationException("native callback failed after entry"); },
            () => removals++, () => clears++));
        Assert.Equal(1, exits);
        Assert.Equal(0, removals);
        Assert.Equal(0, clears);
    }
}
