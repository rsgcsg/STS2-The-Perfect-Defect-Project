using STS2Connector.PlayerEnvironment;
using STS2Connector.NativeUi;
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

    [Theory]
    [InlineData("card_tips")]
    [InlineData("relic_tips")]
    [InlineData("potion_tips")]
    [InlineData("creature_tips")]
    [InlineData("resource_tips")]
    public void MerchantTipCaptureRetainsItsExactSourceUntilNativeExit(string kind)
    {
        var source = new LatchedTipSource();
        source.Enter();
        object? retainedSource = source;
        object? retainedSet = source.Tip;
        NativeTipEntry? retainedEntry = NativeTipEntry.Focus;
        // Capture's kind gate must not discard an otherwise current native owner.
        if (!NativeTextMenuInformation.IsTipKind(kind))
        {
            retainedSource = retainedSet = null;
            retainedEntry = null;
        }
        Assert.Same(source, retainedSource);
        Assert.Same(source.Tip, retainedSet);
        // The same still-rendered registry set is reusable only with retained proof.
        Assert.True(NativeTipReturn.CanReuse(retainedSource, source, retainedEntry,
            NativeTipEntry.Focus, retainedSet, source.Tip!, true, true));
        int exits = 0;
        var closed = NativeTipReturn.Close(true,
            () => NativeTextMenuInformation.IsTipKind(kind)
                && ReferenceEquals(retainedSet, source.Tip),
            () => ReferenceEquals(retainedSource, source) && retainedEntry == NativeTipEntry.Focus,
            () => { exits++; source.Exit(); }, source.Remove,
            () => { retainedSource = retainedSet = null; retainedEntry = null; });
        Assert.True(closed.Accepted);
        Assert.Equal(1, exits);
        Assert.False(source.Focused);
        Assert.Equal(0, source.HandHoverNotifications);
        Assert.Null(retainedSource);
        source.Enter();
        Assert.NotNull(source.Tip);
        Assert.Equal(2, source.Created);
    }

    [Fact]
    public void WithdrawnEntryCapabilityDoesNotPreventClosingTheExactRetainedSource()
    {
        var source = new LatchedTipSource();
        source.Enter();
        object retainedSet = source.Tip!;
        bool entryAvailable = false, sourceCurrent = true;
        int probes = 0, entries = 0, exits = 0;
        var blocked = NativeTextMenuInformation.EnterAvailableTip(
            () => { probes++; return entryAvailable; },
            () => { entries++; source.Enter(); return NativeInputResult.Delivered("native entry"); });
        Assert.Equal("native_tip_entry_unavailable", blocked.ErrorCode);
        Assert.Equal(LegacyNativeInputDisposition.NotDelivered, blocked.LegacyDisposition);
        Assert.Equal(1, probes);
        Assert.Equal(0, entries);
        // The return owner is the same native set/source, independent of whether
        // a new entry is allowed by phase/target/hand/tip-block capability.
        var closed = NativeTipReturn.Close(true,
            () => ReferenceEquals(source.Tip, retainedSet), () => sourceCurrent,
            () => { exits++; source.Exit(); }, source.Remove, () => sourceCurrent = false);
        Assert.True(closed.Accepted);
        Assert.Equal(1, exits);
        Assert.Null(source.Tip);
        Assert.False(source.Focused);
        Assert.Equal(0, source.HandHoverNotifications);
    }

    [Fact]
    public void UnreadableEntryCapabilityRejectsBeforeExitOrEntryWithoutFallback()
    {
        int inputs = 0;
        var result = NativeTextMenuInformation.EnterAvailableTip(
            () => throw new InvalidOperationException("native source unreadable"),
            () => { inputs++; return NativeInputResult.Delivered("unexpected input"); });
        Assert.Equal("native_tip_entry_unreadable", result.ErrorCode);
        Assert.Equal(LegacyNativeInputDisposition.NotDelivered, result.LegacyDisposition);
        Assert.Equal(0, inputs);
    }

    [Theory]
    [InlineData("unknown_tips")]
    [InlineData("potion")]
    [InlineData("native_map")]
    public void UndeclaredTipKindsCannotRetainOrCloseNativeInput(string kind)
    {
        int exits = 0, removes = 0;
        Assert.False(NativeTextMenuInformation.IsTipKind(kind));
        var result = NativeTipReturn.Close(true,
            () => NativeTextMenuInformation.IsTipKind(kind), () => true,
            () => exits++, () => removes++, () => throw new InvalidOperationException("no cleanup"));
        Assert.False(result.Accepted);
        Assert.Equal("native_information_owner_changed", result.ErrorCode);
        Assert.Equal(0, exits);
        Assert.Equal(0, removes);
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
    [Theory]
    [InlineData("deck")]
    [InlineData("relic_inspect")]
    [InlineData("map_back")]
    [InlineData("map_topbar_close")]
    [InlineData("map_travel")]
    public void DepartingMapBalancesTheRetainedSourceBeforeTheNativePageTransition(string destination)
    {
        var source = new LatchedTipSource();
        source.Enter();
        object? first = source.Tip;
        bool retained = true;
        var order = new List<string>();
        var result = NativeTipReturn.BeforeTransition(true, () => retained,
            () => { order.Add("exit"); source.Exit(); },
            () => { order.Add("forget"); retained = false; },
            () =>
            {
                order.Add(destination);
                Assert.False(source.Focused);
                Assert.Null(source.Tip);
                source.Remove(); // Native capstones may clear rendered tips again.
                return NativeInputResult.Delivered(destination);
            });
        Assert.Equal(LegacyNativeInputDisposition.Delivered, result.LegacyDisposition);
        Assert.Equal(new[] { "exit", "forget", destination }, order);
        // After the native destination returns, the same source can create a fresh set.
        source.Enter();
        Assert.NotNull(source.Tip);
        Assert.NotSame(first, source.Tip);
        Assert.Equal(2, source.Created);
    }

    [Fact]
    public void StaleDepartureSourceRejectsBeforeExitOrTransition()
    {
        var calls = new List<string>();
        var result = NativeTipReturn.BeforeTransition(true, () => false,
            () => calls.Add("exit"), () => calls.Add("forget"),
            () => { calls.Add("transition"); return NativeInputResult.Delivered("unexpected"); });
        Assert.Equal(LegacyNativeInputDisposition.NotDelivered, result.LegacyDisposition);
        Assert.Empty(calls);
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void ExitOrTransitionExceptionsAreUnknownWithNoRetry(bool exitThrows)
    {
        int exits = 0, forgets = 0, transitions = 0;
        var result = NativeTipReturn.BeforeTransition(true, () => true,
            () => { exits++; if (exitThrows) throw new InvalidOperationException("exit received"); },
            () => forgets++, () => { transitions++; throw new InvalidOperationException("transition received"); });
        Assert.Equal(LegacyNativeInputDisposition.Unknown, result.LegacyDisposition);
        Assert.Equal(1, exits);
        Assert.Equal(exitThrows ? 0 : 1, forgets);
        Assert.Equal(exitThrows ? 0 : 1, transitions);
    }

    [Fact]
    public void RejectedTargetAfterDeliveredExitCannotBeRetriedAsNoInput()
    {
        int transitions = 0;
        var result = NativeTipReturn.BeforeTransition(true, () => true, () => { }, () => { },
            () => { transitions++; return NativeInputResult.Rejected("target_changed", "after native exit"); });
        Assert.Equal(LegacyNativeInputDisposition.Unknown, result.LegacyDisposition);
        Assert.Contains("target_changed", result.Detail);
        Assert.Equal(1, transitions);
    }

    [Fact]
    public void NoRetainedSignalKeepsTheOriginalTransitionResultAndDoesNoCleanup()
    {
        var expected = NativeInputResult.Rejected("stale", "no input");
        int transitions = 0;
        var actual = NativeTipReturn.BeforeTransition(false,
            () => throw new InvalidOperationException("no source"),
            () => throw new InvalidOperationException("no exit"),
            () => throw new InvalidOperationException("no cleanup"),
            () => { transitions++; return expected; });
        Assert.Same(expected, actual);
        Assert.Equal(1, transitions);
    }

}
