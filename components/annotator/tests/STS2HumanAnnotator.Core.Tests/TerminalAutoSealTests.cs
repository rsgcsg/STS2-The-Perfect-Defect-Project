using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class TerminalAutoSealTests
{
    [Fact]
    public void ProductionReadySequenceObservesBoundaryBeforeClosingEvenWithNoPendingRoot()
    {
        var seal = new TerminalAutoSeal();
        var order = new List<string>();
        seal.ObserveNativeEnded("session-1", "run-1", abandoned: false);

        seal.CompleteDecisionOwnerReady("session-1", "run-1", "game_over",
            observeBoundary: () => order.Add("boundary_observed_no_root"),
            close: () => order.Add("close"));

        Assert.Equal(new[] { "boundary_observed_no_root", "close" }, order);
    }

    [Fact]
    public void FailedBoundaryStillRequestsFailClosedClosureAfterFailure()
    {
        var seal = new TerminalAutoSeal();
        var order = new List<string>();
        seal.ObserveNativeEnded("session-1", "run-1", abandoned: false);

        Assert.Throws<InvalidOperationException>(() =>
            seal.CompleteDecisionOwnerReady("session-1", "run-1", "game_over",
                observeBoundary: () =>
                {
                    order.Add("boundary_failed");
                    throw new InvalidOperationException("capture failed");
                },
                close: () => order.Add("close_as_unknown")));

        Assert.Equal(new[] { "boundary_failed", "close_as_unknown" }, order);
    }

    [Fact]
    public void StaleCallbackCannotCloseNewSessionEvenAfterItsBoundaryRuns()
    {
        var seal = new TerminalAutoSeal();
        var order = new List<string>();
        seal.ObserveNativeEnded("session-1", "run-1", abandoned: false);
        seal.Reset();
        seal.ObserveNativeEnded("session-2", "run-1", abandoned: false);

        seal.CompleteDecisionOwnerReady("session-1", "run-1", "game_over",
            observeBoundary: () => order.Add("old_callback"),
            close: () => order.Add("wrong_close"));

        Assert.Equal(new[] { "old_callback" }, order);
    }

    [Fact]
    public void FrameBetweenNativeEndAndExactGameOverReadyMustKeepSessionOpen()
    {
        var seal = new TerminalAutoSeal();
        seal.ObserveNativeEnded("session-1", "run-1", abandoned: false);

        Assert.False(seal.TakeOnProcessFrame("session-1", "run-1"));
        Assert.False(seal.TakeOnDecisionOwnerReady("session-1", "run-1", "combat_turn"));
        Assert.True(seal.TakeOnDecisionOwnerReady("session-1", "run-1", "game_over"));
        Assert.False(seal.TakeOnDecisionOwnerReady("session-1", "run-1", "game_over"));
    }

    [Fact]
    public void OldSessionAndRunCannotSealCurrentRecording()
    {
        var seal = new TerminalAutoSeal();
        seal.ObserveNativeEnded("session-1", "run-1", abandoned: false);
        Assert.False(seal.TakeOnDecisionOwnerReady("session-2", "run-1", "game_over"));
        Assert.False(seal.TakeOnDecisionOwnerReady("session-1", "run-2", "game_over"));
        seal.Reset();
        Assert.False(seal.TakeOnDecisionOwnerReady("session-1", "run-1", "game_over"));
    }

    [Fact]
    public void AbandonmentSealsOnFrameWithoutClaimingGameOverReady()
    {
        var seal = new TerminalAutoSeal();
        seal.ObserveNativeEnded("session-1", "run-1", abandoned: true);
        Assert.False(seal.TakeOnDecisionOwnerReady("session-1", "run-1", "game_over"));
        Assert.True(seal.TakeOnProcessFrame("session-1", "run-1"));
        Assert.False(seal.TakeOnProcessFrame("session-1", "run-1"));
    }

    [Fact]
    public void NextNativeLaunchEndsUnreadyTerminalWaitWithoutTransferringIt()
    {
        var seal = new TerminalAutoSeal();
        seal.ObserveNativeEnded("session-1", "run-1", abandoned: false);
        Assert.True(seal.TakeOnNextNativeLaunch("session-1", "run-1"));
        Assert.False(seal.TakeOnDecisionOwnerReady("session-1", "run-1", "game_over"));
    }
}
