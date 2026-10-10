using STS2HumanAnnotator.Core;

namespace STS2PlatformLiveUi;

/// <summary>Application handoff only. The recorder remains the lifecycle owner.</summary>
public static class PlatformCollectionHandoff
{
    private static bool HealthySource3(RecordingApplicationStatus status, string runtime)
    {
        if (status.Session?.CaptureProfileId != SourceSessionContractV3.ProfileId
            || status.SourceV2 is not { AccountingComplete: true, Error: null, Declaration.MachineVerifiable: false }
            || status.Environment?.RuntimeInstanceId != runtime
            || status.Health is not { Append: "healthy", Disk: "healthy", LastError: null }) return false;
        try
        {
            SourceSessionContract.Validate(status.SourceV2.Declaration);
            SourceSessionContract.Identifier(status.SourceV2.SegmentId);
            SourceSessionContract.Identifier(status.SourceV2.EpochId);
            return status.Lifecycle.SessionId is not null && status.Session.SessionId == status.Lifecycle.SessionId;
        }
        catch (InvalidDataException) { return false; }
    }

    public static bool CanPreserveForNativeAgent(RecordingApplicationStatus status, string runtime) =>
        HealthySource3(status, runtime) && status.SourceV2!.Declaration.SourceKind == "agent_protocol"
        && status.Lifecycle.State == RecordingLifecycleState.Recording && status.Continuous?.Armed != true;

    public static PlatformTaskStatusV2 ProjectV2(string runtime, RecordingApplicationStatus status) =>
        new(PlatformRecordingCommands.TaskStatusSchemaV2, runtime, status.Lifecycle.SessionId,
            status.Lifecycle.State.ToString().ToLowerInvariant(), status.Closeout.State, status.Session?.CaptureProfileId,
            status.SourceV2?.SegmentId ?? status.Source?.SegmentId, status.SourceV2?.Declaration ?? status.Source?.Declaration,
            CanPreserveForNativeAgent(status, runtime), SourceSessionContract.NonClaims);

    /// <summary>Application compatibility metadata only; never Runtime authentication or origin proof.</summary>
    public static PlatformModelPreparationResultV2 PrepareForModel(PlatformModelPreparationRequestV2 request,
        Func<string> runtime, Func<RecordingApplicationStatus> query,
        Func<RecordingCommand, string?, RecordingCommandResult> execute)
    {
        request.ModelContext.Validate();
        string current = runtime();
        if (request.RuntimeInstanceId != current) throw new PlatformRecordingPreconditionException("recording_game_instance_changed");
        RecordingApplicationStatus before = query();
        if (before.Lifecycle.SessionId != request.RecordingSessionId)
            throw new PlatformRecordingPreconditionException("recording_session_changed");
        if ((before.SourceV2?.SegmentId ?? before.Source?.SegmentId) != request.SourceSegmentId)
            throw new PlatformRecordingPreconditionException("recording_source_segment_changed");
        PlatformModelPreparationResultV2 Result(bool ready, string disposition, RecordingApplicationStatus status) =>
            new(PlatformRecordingCommands.ModelResultSchemaV2, request.CommandId, current, request.ModelContext,
                ready, disposition, ProjectV2(current, status));
        if (Ready(before)) return Result(true, "no_active_recording", before);
        if (before.Lifecycle.State == RecordingLifecycleState.Closing) return Result(false, "close_pending", before);
        if (before.Session?.CaptureProfileId == SourceSessionContractV3.ProfileId)
        {
            if (!HealthySource3(before, current)) return Result(false, "blocked", before);
            if (before.SourceV2!.Declaration.SourceKind == "agent_protocol"
                && before.Lifecycle.State == RecordingLifecycleState.Paused) return Result(false, "paused", before);
            if (before.SourceV2!.Declaration.SourceKind == "agent_protocol" && request.ModelContext.NativeAgent)
            {
                if (CanPreserveForNativeAgent(before, current)) return Result(true, "retained_agent_protocol", before);
                return Result(false, "blocked", before);
            }
        }
        RecordingApplicationStatus after = Prepare(request.RecordingSessionId, request.CommandId, query, execute);
        return Result(Ready(after), Ready(after) ? "closed_for_model" : "close_pending", after);
    }

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
