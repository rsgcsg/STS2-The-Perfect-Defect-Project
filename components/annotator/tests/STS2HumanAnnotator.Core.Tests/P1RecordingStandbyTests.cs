using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class P1RecordingStandbyTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "p1-standby-" + Guid.NewGuid().ToString("N"));

    [Theory]
    [InlineData("declared_human")]
    [InlineData("agent_native_ui")]
    [InlineData("unknown")]
    public void RestartRestoresOnlyTheChosenProfileAndOriginWithFreshRunAccounting(string origin)
    {
        ContinuousRecording previous = new();
        previous.Arm(P1NativeRecordingContract.ProfileId, origin);
        previous.BeginSession(); previous.ObserveLaunch("run_started_native");
        previous.ObserveTerminal(false, false); previous.MarkSealed();
        P1RecordingStandby.From(previous.NextSessionCommand("previous" )).Save(_root);

        var next = P1RecordingStandby.Load(_root).NextSession("new-process-native-launch");
        Assert.NotNull(next);
        Assert.Equal("new-process-native-launch", next.CommandId);
        Assert.Equal(P1NativeRecordingContract.ProfileId, next.CaptureProfileId);
        Assert.Equal(origin, next.P1NativeOrigin);
        ContinuousRecording restarted = new(); restarted.Arm(next.CaptureProfileId, next.P1NativeOrigin);
        restarted.BeginSession(); restarted.ObserveLaunch("run_resumed_native");
        Assert.False(restarted.Snapshot().NativeStartObserved);
        Assert.True(restarted.Snapshot().Resumed);
        Assert.False(restarted.Snapshot().BoundaryComplete);
        Assert.Equal(0, restarted.Snapshot().SealedSegments);
        Assert.Equal(0, restarted.Snapshot().FinishedRuns);
        Assert.DoesNotContain("session", File.ReadAllText(Path.Combine(_root, P1RecordingStandby.FileName)));
    }

    [Fact]
    public void ManualStopOrCompatibilityRecordingDoesNotReviveP1AfterRestart()
    {
        Assert.Null(P1RecordingStandby.Load(_root).NextSession("absent"));
        new P1RecordingStandby(P1RecordingStandby.SettingsSchema, true, "declared_human").Save(_root);
        P1RecordingStandby.From(null).Save(_root);
        Assert.Null(P1RecordingStandby.Load(_root).NextSession("stopped"));
        ContinuousRecording legacy = new(); legacy.Arm();
        P1RecordingStandby.From(legacy.NextSessionCommand("legacy")).Save(_root);
        Assert.Null(P1RecordingStandby.Load(_root).NextSession("no-p1-default"));
    }

    [Fact]
    public void InvalidOrTornPreferenceIsVisibleAndCannotInventHumanOrigin()
    {
        Directory.CreateDirectory(_root);
        string path = Path.Combine(_root, P1RecordingStandby.FileName);
        File.WriteAllText(path, "{\"schema\":\"sts2.human-annotator/p1-recording-standby-1\",\"enabled\":true}");
        Assert.Throws<InvalidDataException>(() => P1RecordingStandby.Load(_root));
        File.WriteAllText(path, "{");
        Assert.Throws<System.Text.Json.JsonException>(() => P1RecordingStandby.Load(_root));
        // A new explicit selection replaces a broken preference, never a recording.
        new P1RecordingStandby(P1RecordingStandby.SettingsSchema, true, "unknown").Save(_root);
        Assert.Equal("unknown", P1RecordingStandby.Load(_root).NextSession("explicit")!.P1NativeOrigin);
        Assert.Single(Directory.GetFiles(_root));
    }

    public void Dispose() { if (Directory.Exists(_root)) Directory.Delete(_root, recursive: true); }
}
