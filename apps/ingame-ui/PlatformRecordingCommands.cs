using System.Text.Json;
using STS2HumanAnnotator.Core;

namespace STS2PlatformLiveUi;

public sealed record PlatformRecordingRequest(string RuntimeInstanceId, string? RecordingSessionId,
    RecordingCommand Command);
public sealed record PlatformModelPreparationRequest(string RuntimeInstanceId, string? RecordingSessionId, string CommandId);
public sealed record PlatformRecordingHealth(string AppendHealth, string DiskHealth, string? Error);
public sealed record PlatformRecordingStatus(string Schema, string RuntimeInstanceId, string? RecordingSessionId,
    string RecordingLifecycle, string? CaptureProfileId, string CloseoutStatus,
    SourceSessionStatusV2? Source, PlatformRecordingHealth Health, IReadOnlyList<string> NonClaims);
public sealed record PlatformRecordingResult(string Schema, string CommandId, bool Accepted, bool Pending,
    string Code, PlatformRecordingStatus Status);
public sealed class PlatformRecordingNotDispatchedException : Exception { }
public sealed class PlatformRecordingPreconditionException(string code) : InvalidOperationException(code) { }

/// <summary>Strict application projection/composition only. Recorder owns lifecycle and Source admission.</summary>
public static class PlatformRecordingCommands
{
    public const string RequestSchema = "sts2.platform/recording-request-1";
    public const string StatusSchema = "sts2.platform/recording-status-1";
    public const string ResultSchema = "sts2.platform/recording-result-1";
    public const int MaximumRequestBytes = 4096;

    public static async Task<T> OnMainThread<T>(Func<T> work,
        Func<Func<T>, CancellationToken, Task<T>> dispatch)
    {
        using var queueDeadline = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        Task<T> task = dispatch(work, queueDeadline.Token);
        try { return await task.WaitAsync(TimeSpan.FromSeconds(10)); }
        catch (OperationCanceledException) when (task.IsCanceled)
        { throw new PlatformRecordingNotDispatchedException(); }
        catch (TimeoutException)
        {
            _ = task.ContinueWith(completed => { _ = completed.Exception; },
                CancellationToken.None, TaskContinuationOptions.OnlyOnFaulted, TaskScheduler.Default);
            throw;
        }
    }

    private static void Exact(JsonElement value, params string[] fields)
    {
        if (value.ValueKind != JsonValueKind.Object
            || !value.EnumerateObject().Select(p => p.Name).Order(StringComparer.Ordinal)
                .SequenceEqual(fields.Order(StringComparer.Ordinal)))
            throw new ArgumentException("invalid_recording_fields");
    }

    private static string? OptionalIdentifier(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Null) return null;
        if (value.ValueKind != JsonValueKind.String) throw new ArgumentException("invalid_recording_identifier");
        string? text = value.GetString();
        try { SourceSessionContract.Identifier(text); }
        catch (InvalidDataException) { throw new ArgumentException("invalid_recording_identifier"); }
        return text;
    }

    public static PlatformModelPreparationRequest ReadModelPreparation(JsonElement body)
    {
        Exact(body, "command_id", "recording_session_id", "runtime_instance_id");
        string runtime = OptionalIdentifier(body.GetProperty("runtime_instance_id"))
            ?? throw new ArgumentException("recording_runtime_required");
        string? session = OptionalIdentifier(body.GetProperty("recording_session_id"));
        JsonElement value = body.GetProperty("command_id");
        // The existing prepare-model wire accepts Guid.TryParse forms, unlike the new D-only entry.
        if (value.ValueKind != JsonValueKind.String || !Guid.TryParse(value.GetString(), out _))
            throw new ArgumentException("invalid_task_fields");
        return new(runtime, session, value.GetString()!);
    }

    public static PlatformRecordingRequest Read(JsonElement body)
    {
        Exact(body, "schema", "runtime_instance_id", "recording_session_id", "command");
        if (body.GetProperty("schema").ValueKind != JsonValueKind.String
            || body.GetProperty("schema").GetString() != RequestSchema)
            throw new ArgumentException("invalid_recording_schema");
        string runtime = OptionalIdentifier(body.GetProperty("runtime_instance_id"))
            ?? throw new ArgumentException("recording_runtime_required");
        string? session = OptionalIdentifier(body.GetProperty("recording_session_id"));
        JsonElement value = body.GetProperty("command");
        Exact(value, "schema", "command_id", "kind", "capture_profile_id", "source_declaration", "expected_source_segment_id");
        if (value.GetProperty("schema").ValueKind != JsonValueKind.String
            || value.GetProperty("schema").GetString() != SourceSessionContractV2.CommandSchema)
            throw new ArgumentException("invalid_source_command_schema");
        string id = OptionalIdentifier(value.GetProperty("command_id")) ?? "";
        if (!Guid.TryParseExact(id, "D", out _)) throw new ArgumentException("invalid_recording_command_id");
        if (value.GetProperty("kind").ValueKind != JsonValueKind.String)
            throw new ArgumentException("invalid_recording_kind");
        RecordingCommandKind kind = value.GetProperty("kind").GetString() switch
        {
            "start_new_session" => RecordingCommandKind.StartNewSession,
            "pause" => RecordingCommandKind.Pause,
            "resume" => RecordingCommandKind.Resume,
            "change_source" => RecordingCommandKind.ChangeSource,
            "close" => RecordingCommandKind.Close,
            _ => throw new ArgumentException("invalid_recording_kind")
        };
        string? profile = OptionalIdentifier(value.GetProperty("capture_profile_id"));
        string? segment = OptionalIdentifier(value.GetProperty("expected_source_segment_id"));
        SourceDeclaration? source = null;
        JsonElement declaration = value.GetProperty("source_declaration");
        if (declaration.ValueKind != JsonValueKind.Null)
        {
            Exact(declaration, "source_kind", "actor_id", "declaration_id", "machine_verifiable");
            if (declaration.GetProperty("machine_verifiable").ValueKind != JsonValueKind.False)
                throw new ArgumentException("source_declaration_not_attestation");
            source = new(OptionalIdentifier(declaration.GetProperty("source_kind")) ?? "",
                OptionalIdentifier(declaration.GetProperty("actor_id")) ?? "",
                OptionalIdentifier(declaration.GetProperty("declaration_id")) ?? "", false);
            try { SourceSessionContract.Validate(source); }
            catch (InvalidDataException) { throw new ArgumentException("invalid_source_declaration"); }
        }
        if (kind == RecordingCommandKind.StartNewSession)
        {
            if (profile != SourceSessionContractV3.ProfileId || source is null || segment is not null)
                throw new ArgumentException("source3_profile_and_declaration_required");
        }
        else if (kind == RecordingCommandKind.ChangeSource)
        {
            if (profile is not null || source is null || segment is null)
                throw new ArgumentException("source_declaration_and_segment_required");
        }
        else if (profile is not null || source is not null || segment is not null)
            throw new ArgumentException("source_command_fields_not_applicable");
        return new(runtime, session, new(id, kind, profile, SourceSessionContractV2.CommandSchema)
        { SourceDeclaration = source, ExpectedSourceSegmentId = segment });
    }

    public static string SchemaForProfile(string? profile) => profile switch
    {
        SourceSessionContract.ProfileId => RecordingApplicationContract.SourceCommandSchema,
        SourceSessionContractV2.ProfileId or SourceSessionContractV3.ProfileId => SourceSessionContractV2.CommandSchema,
        _ => RecordingApplicationContract.CommandSchema
    };

    public static RecordingCommand ForStatus(RecordingApplicationStatus status, RecordingCommandKind kind, string commandId,
        string? startProfile = null, SourceDeclaration? declaration = null) =>
        new(commandId, kind, kind == RecordingCommandKind.StartNewSession ? startProfile : null,
            SchemaForProfile(kind == RecordingCommandKind.StartNewSession ? startProfile : status.Session?.CaptureProfileId))
        {
            SourceDeclaration = declaration,
            ExpectedSourceSegmentId = kind == RecordingCommandKind.ChangeSource
                ? status.SourceV2?.SegmentId ?? status.Source?.SegmentId : null
        };

    // Never include arbitrary owner exception text or local paths in public operational views.
    public static string PublicCode(string? value, string fallback = "recording_unavailable") =>
        value is { Length: > 0 and <= 96 } && value.All(c => c is >= 'a' and <= 'z' or >= '0' and <= '9' or '_')
            ? value : fallback;

    public static PlatformRecordingStatus Project(string runtime, RecordingApplicationStatus status) =>
        new(StatusSchema, runtime, status.Lifecycle.SessionId, status.Lifecycle.State.ToString().ToLowerInvariant(),
            status.Session?.CaptureProfileId, status.Closeout.State,
            status.SourceV2 is { } source ? source with { Error = source.Error is null ? null : PublicCode(source.Error, "source_accounting_failed") } : null,
            new(status.Health.Append, status.Health.Disk,
                status.Health.LastError is null ? null : "recording_health_failed"), SourceSessionContract.NonClaims);

    /// <summary>Call on the native main thread. ExecuteForSession is the atomic lifecycle owner.</summary>
    public static PlatformRecordingResult Execute(PlatformRecordingRequest request, Func<string> runtime,
        Func<RecordingApplicationStatus> query, Func<RecordingCommand, string?, RecordingCommandResult> execute)
    {
        string current = runtime();
        if (request.RuntimeInstanceId != current) throw new PlatformRecordingPreconditionException("recording_game_instance_changed");
        RecordingCommandResult result = execute(request.Command, request.RecordingSessionId);
        return new(ResultSchema, request.Command.CommandId, result.Accepted, result.Pending,
            PublicCode(result.Code), Project(current, query()));
    }
}
