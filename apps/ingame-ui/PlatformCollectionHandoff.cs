using STS2HumanAnnotator.Core;

namespace STS2PlatformLiveUi;

/// <summary>Application handoff only. The recorder remains the lifecycle owner.</summary>
public static class PlatformCollectionHandoff
{
    public static bool Ready(RecordingApplicationStatus status) =>
        status.Continuous?.Armed != true && (status.Lifecycle.State == RecordingLifecycleState.Ready
        || (status.Lifecycle.State == RecordingLifecycleState.Closed
            && status.Closeout.State == "closed"));

    public static RecordingApplicationStatus Prepare(
        string? expectedSessionId,
        string commandId,
        Func<RecordingApplicationStatus> query,
        Func<RecordingCommand, string?, RecordingCommandResult> execute)
    {
        RecordingApplicationStatus before = query();
        if (before.Lifecycle.SessionId != expectedSessionId)
            throw new InvalidOperationException("recording_session_changed");
        if (Ready(before))
            return before;
        if (before.Lifecycle.State is RecordingLifecycleState.Recording or RecordingLifecycleState.Paused
            || before.Continuous?.Armed == true)
        {
            RecordingCommandResult result = execute(
                PlatformRecordingCommands.ForStatus(before, RecordingCommandKind.Close, commandId), expectedSessionId);
            if (!result.Accepted)
                throw new InvalidOperationException("recording_close_rejected:" + result.Code);
        }
        // Closing is a real intermediate state. Never poll it into a proof or
        // release a model command before the owner confirms durable Close.
        RecordingApplicationStatus after = query();
        if (after.Lifecycle.SessionId != expectedSessionId)
            throw new InvalidOperationException("recording_session_changed");
        return after;
    }
}
