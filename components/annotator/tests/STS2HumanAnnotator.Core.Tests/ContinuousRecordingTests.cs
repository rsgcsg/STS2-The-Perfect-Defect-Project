using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class ContinuousRecordingTests
{
    [Theory]
    [InlineData("declared_human")]
    [InlineData("agent_native_ui")]
    [InlineData("unknown")]
    public void NextSessionKeepsExplicitP1ProfileAndOriginAndStopsWhenDisarmed(string origin)
    {
        ContinuousRecording recording = new();
        recording.Arm(P1NativeRecordingContract.ProfileId, origin);
        recording.BeginSession(); recording.ObserveLaunch("run_started_native");
        recording.ObserveTerminal(false, false); recording.RequestSeal(); recording.MarkSealed();
        var command = recording.NextSessionCommand("next-native-run");
        Assert.NotNull(command);
        Assert.Equal(RecordingCommandKind.StartNewSession, command.Kind);
        Assert.Equal(P1NativeRecordingContract.ProfileId, command.CaptureProfileId);
        Assert.Equal(origin, command.P1NativeOrigin);
        recording.BeginSession(); // per-session witness reset must not reset chosen profile
        Assert.Equal(command.CaptureProfileId, recording.NextSessionCommand("next-again")!.CaptureProfileId);
        recording.Pause(); Assert.Null(recording.NextSessionCommand("must-not-start"));
        recording.Arm(P1NativeRecordingContract.ProfileId, origin);
        recording.Disarm(); Assert.Null(recording.NextSessionCommand("must-not-revive"));
    }

    [Fact]
    public void LegacyContinuousDefaultDoesNotBecomeP1OrClaimHumanOrigin()
    {
        ContinuousRecording recording = new(); recording.Arm();
        var command = recording.NextSessionCommand("legacy-next");
        Assert.NotNull(command); Assert.Null(command.CaptureProfileId); Assert.Null(command.P1NativeOrigin);
    }

    [Theory]
    [InlineData(true, false, "victory")]
    [InlineData(false, false, "defeat")]
    [InlineData(false, true, "abandoned")]
    public void TerminalSealKeepsArmedAndNextRunStartsWithoutTransferredWitnesses(
        bool victory, bool abandoned, string outcome)
    {
        ContinuousRecording recording = new();
        recording.Arm();
        recording.BeginSession();
        recording.ObserveLaunch("run_started_native");
        Assert.True(recording.ObserveTerminal(victory, abandoned));
        Assert.False(recording.ObserveTerminal(victory, abandoned));
        recording.RequestSeal();
        Assert.Equal("sealing", recording.Snapshot().SealState);
        recording.MarkSealed();
        recording.MarkSealed();
        var first = recording.Snapshot();
        Assert.True(first.Armed);
        Assert.True(first.BoundaryComplete);
        Assert.Equal(outcome, first.Outcome);
        Assert.Equal(1, first.FinishedRuns);
        recording.BeginSession();
        Assert.False(recording.Snapshot().NativeStartObserved);
        Assert.False(recording.Snapshot().TerminalObserved);
        Assert.Equal(1, recording.Snapshot().SealedSegments);
    }

    [Fact]
    public void SavedRunAndPauseNeverBecomeCompleteFreshRuns()
    {
        ContinuousRecording recording = new();
        recording.Arm(); recording.BeginSession();
        recording.ObserveLaunch("run_resumed_native");
        recording.ObserveTerminal(false, false);
        Assert.False(recording.Snapshot().BoundaryComplete);
        recording.BeginSession(); recording.ObserveLaunch("run_started_native");
        recording.Pause();
        Assert.False(recording.Armed);
        recording.Arm(); recording.ObserveTerminal(true, false);
        Assert.False(recording.Snapshot().BoundaryComplete);
    }

    [Fact]
    public void ExitCanSealPartialContentWithoutInventingATerminal()
    {
        ContinuousRecording recording = new();
        recording.Arm(); recording.BeginSession();
        recording.ObserveLaunch("run_started_native");
        recording.Disarm(); recording.RequestSeal(); recording.MarkSealed();
        var status = recording.Snapshot();
        Assert.False(status.Armed);
        Assert.False(status.TerminalObserved);
        Assert.False(status.BoundaryComplete);
        Assert.Equal("unknown", status.Outcome);
        Assert.Equal(0, status.FinishedRuns);
        Assert.Equal(1, status.SealedSegments);
    }
}
