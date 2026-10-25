using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Mod;

// Compile the production restart/save partial unchanged. Only game-owned runtime
// fields are supplied; these cases do not start a game or qualify native recording.
internal static partial class RecorderRuntime
{
    private static AnnotatorConfiguration? _configuration;
    private static readonly ContinuousRecording Continuous = new();

    internal static (bool Armed, string? Error) TestRestoreStandby(string root)
    {
        _configuration = new(root, Path.Combine(root, "status.json"));
        Continuous.Disarm(); _standbyError = null;
        RestoreRecordingStandby();
        return (Continuous.Armed, _standbyError);
    }

    internal static RecordingCommandResult TestSaveStandby(string root, RecordingCommand command,
        RecordingCommandResult result)
    {
        _configuration = new(root, Path.Combine(root, "status.json"));
        Continuous.Arm(P1NativeRecordingContract.ProfileId, "agent_native_ui");
        return SaveRecordingStandby(command, result);
    }
}

[Collection("P1 recorder runtime")]
public sealed class RecordingStandbyRuntimeTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "standby-runtime-" + Guid.NewGuid().ToString("N"));

    [Theory]
    [InlineData("{\"schema\":\"sts2.human-annotator/p1-recording-standby-1\",\"enabled\":true,\"origin\":\"unsupported\"}")]
    [InlineData("{")]
    public void InvalidPreferenceDoesNotBreakRecorderStartupOrGuessHuman(string original)
    {
        Directory.CreateDirectory(_root);
        string path = Path.Combine(_root, P1RecordingStandby.FileName);
        File.WriteAllText(path, original);
        var result = RecorderRuntime.TestRestoreStandby(_root);
        Assert.False(result.Armed);
        Assert.Equal("recording_standby_restore_failed", result.Error);
        Assert.Equal(original, File.ReadAllText(path));
        new P1RecordingStandby(P1RecordingStandby.SettingsSchema, true, "agent_native_ui").Save(_root);
        var selected = RecorderRuntime.TestRestoreStandby(_root);
        Assert.True(selected.Armed);
        Assert.Null(selected.Error);
    }

    [Fact]
    public void MissingPreferenceStartsIdleWithoutCreatingRecordingFiles()
    {
        var status = RecorderRuntime.TestRestoreStandby(_root);
        Assert.False(status.Armed); Assert.Null(status.Error);
        Assert.False(Directory.Exists(_root));
    }

    [Fact]
    public void PreferenceWriteFailureKeepsTheAlreadyAppliedCommandAccepted()
    {
        Directory.CreateDirectory(_root);
        string blockedRoot = Path.Combine(_root, "file-is-not-directory");
        File.WriteAllText(blockedRoot, "original bytes");
        var applied = new RecordingCommandResult(true, false, "session_started", "Already applied.",
            new(RecordingLifecycleState.Recording, "session-one", DateTimeOffset.UtcNow, "Started."));
        var result = RecorderRuntime.TestSaveStandby(blockedRoot,
            new("original-start", RecordingCommandKind.StartNewSession), applied);
        Assert.True(result.Accepted);
        Assert.Equal(applied.Lifecycle, result.Lifecycle);
        Assert.Equal("recording_standby_save_failed", result.Code);
        Assert.Equal("original bytes", File.ReadAllText(blockedRoot));
    }

    public void Dispose() { if (Directory.Exists(_root)) Directory.Delete(_root, true); }
}
