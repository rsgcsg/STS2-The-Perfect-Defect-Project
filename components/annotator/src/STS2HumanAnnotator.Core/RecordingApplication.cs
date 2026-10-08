namespace STS2HumanAnnotator.Core;

public static class RecordingApplicationContract
{
    public const string CommandSchema = "sts2.ai-platform/recording-command-1";
    public const string SourceCommandSchema = "sts2.ai-platform/recording-command-2";
    public const string CommandResultSchema = "sts2.ai-platform/recording-command-result-1";
    public const string StatusSchema = "sts2.ai-platform/recording-status-5";
    public const string EventBatchSchema = "sts2.ai-platform/recording-event-batch-2";
}

public enum RecordingLifecycleState
{
    Ready,
    Recording,
    Paused,
    Closing,
    Closed
}

public enum RecordingCommandKind
{
    StartNewSession,
    Pause,
    Resume,
    Close,
    ChangeSource
}

public static class RecordingClosePolicy
{
    public const string TerminalUnknownReason = "session_closed_before_successor_boundary";
    public const string TerminalUnknownDetail =
        "The session was closed before a complete semantic successor boundary was proved.";
}

public sealed record RecordingCommand(
    string CommandId,
    RecordingCommandKind Kind,
    string? CaptureProfileId = null,
    string Schema = RecordingApplicationContract.CommandSchema)
{
    public SourceDeclaration? SourceDeclaration { get; init; }
    public string? ExpectedSourceSegmentId { get; init; }
}

public sealed record RecordingLifecycleSnapshot(
    RecordingLifecycleState State,
    string? SessionId,
    DateTimeOffset ChangedAt,
    string Detail)
{
    public static RecordingLifecycleSnapshot Ready(DateTimeOffset changedAt) =>
        new(
            RecordingLifecycleState.Ready,
            null,
            changedAt,
            "Recorder runtime is ready; no recording session is open.");
}

public sealed record RecordingCommandResult(
    bool Accepted,
    bool Pending,
    string Code,
    string Detail,
    RecordingLifecycleSnapshot Lifecycle,
    string Schema = RecordingApplicationContract.CommandResultSchema);

public sealed record RecordingSessionStatus(
    string SessionId,
    string TimelineId,
    string RunId,
    string CaptureProfileId,
    string RecordingDirectory,
    DateTimeOffset StartedAt,
    DateTimeOffset? ClosedAt);

public sealed record RecordingCounters(
    long Records,
    long Invalidations,
    long ReadsMaterialized,
    long ReadsFailed,
    RecordingDecisionCounters? Decisions = null);

/// <summary>Counts only facts successfully appended to the authoritative streams.</summary>
public sealed record RecordingDecisionCounters(
    long AcceptedRoots, long AcceptedChildren, long Proved, long Unresolved,
    long CanonicalRoots, long CanonicalChildren,
    long Cancelled = 0, long Aborted = 0, long CaptureFailures = 0,
    long PersistenceFailures = 0, int DispositionVersion = 0, long FailedDecisions = 0, bool AccountingComplete = true)
{
    public long Accepted => AcceptedRoots + AcceptedChildren;
    public long Canonical => CanonicalRoots + CanonicalChildren;
    public long Pending => Math.Max(0, Accepted - Proved - Unresolved - Cancelled - Aborted);
    public long? RealFailures => DispositionVersion == 1 && AccountingComplete
        ? FailedDecisions : null;
}

public sealed record RecordingStoreSnapshot(
    RecordingCounters Counters,
    RecordingItemStatus? LastRecord,
    RecordingItemStatus? LastInvalidation,
    IReadOnlyDictionary<string, long> RecordedActionFamilies,
    IReadOnlyDictionary<string, long> InvalidatedNativeActions,
    IReadOnlyDictionary<string, long> InvalidationsByReason,
    string AppendHealth,
    string DiskHealth,
    string? LastError,
    bool Closed,
    IReadOnlyDictionary<string, long>? FailedActionFamilies = null);

public sealed record RecordingItemStatus(
    string Id,
    string Kind,
    DateTimeOffset ObservedAt,
    string? Detail);

/// <summary>
/// Read-only presentation facts copied from an action already admitted by the
/// owning Human/semantic path. This projection cannot authorize, settle,
/// record, or deliver an action.
/// </summary>
public sealed record RecordingActionProjection(
    string Verb,
    string? BoundActionId,
    string? SubjectReferentId,
    IReadOnlyDictionary<string, string> Arguments,
    string Label,
    string? EffectSummary = null,
    DecisionOccurrenceIdentity? Decision = null,
    string? PreSnapshotId = null,
    string? SuccessorSnapshotId = null,
    int? CandidateCount = null,
    string? PileType = null,
    HumanActionOccurrenceEvidence? FailedOccurrence = null,
    bool IsDiagnostic = false)
{
    public string? NativeActionKey { get; init; }
    public string? Disposition { get; init; }
}

public sealed record RecordingPendingRootStatus(
    string RecordId,
    string RunId,
    DateTimeOffset Deadline);

public sealed record RecordingHealthStatus(
    string RequiredReads,
    string Append,
    string Disk,
    string? LastError,
    DateTimeOffset CheckedAt);

public sealed record RecordingCloseoutStatus(
    string State,
    DateTimeOffset? RequestedAt,
    DateTimeOffset? CompletedAt,
    string? Detail)
{
    public static RecordingCloseoutStatus Idle { get; } =
        new("idle", null, null, null);
}

public sealed record RecordingScopeStatus(
    IReadOnlyList<string> SupportedActionFamilies,
    IReadOnlyDictionary<string, long> RecordedByActionFamily,
    IReadOnlyDictionary<string, long> AcceptedFailedClosedByActionFamily,
    IReadOnlyDictionary<string, long> InvalidationsByReason,
    IReadOnlyList<string> SupportedNotObserved,
    IReadOnlyList<string> DeclaredOutOfScope,
    string Detail);

public sealed record RecordingApplicationStatus(
    string Schema,
    DateTimeOffset ObservedAt,
    int ProcessId,
    RecordingLifecycleSnapshot Lifecycle,
    RecordingSessionStatus? Session,
    RecordingCounters Counters,
    RecordingPendingRootStatus? PendingRoot,
    RecordingItemStatus? LastRecord,
    RecordingItemStatus? LastInvalidation,
    RecordingHealthStatus Health,
    RecordingScopeStatus Scope,
    RecordingCloseoutStatus Closeout,
    string RuntimeState,
    string Detail,
    RecorderEnvironmentIdentity? Environment,
    string? CurrentSnapshotId,
    IReadOnlyList<string> Blockers,
    long LatestEventSequence,
    ContinuousRecordingStatus? Continuous = null)
{
    public SourceSessionStatus? Source { get; init; }
}

public enum RecordingEventKind
{
    RuntimeReady,
    SessionStarted,
    SessionPaused,
    SessionResumed,
    SessionCloseRequested,
    SessionClosed,
    RunStarted,
    RunEnded,
    RootPending,
    DecisionRecorded,
    DecisionInvalidated,
    DecisionUnresolved,
    DecisionCancelled,
    DecisionAborted,
    DecisionProjectionOmitted,
    HealthChanged,
    CommandRejected,
    SourceChanged
}

public sealed record RecordingEvent(
    long Sequence,
    string EventId,
    RecordingEventKind Kind,
    DateTimeOffset ObservedAt,
    string? SessionId,
    string? RunId,
    string? RecordId,
    string? Detail,
    RecordingActionProjection? Action = null);

public sealed record RecordingEventBatch(
    long RequestedAfterSequence,
    long OldestAvailableSequence,
    long LatestSequence,
    bool Gap,
    IReadOnlyList<RecordingEvent> Events,
    string Schema = RecordingApplicationContract.EventBatchSchema);

public sealed class RecordingEventStream
{
    private readonly object _gate = new();
    private readonly int _capacity;
    private readonly Queue<RecordingEvent> _events = new();
    private long _sequence;

    public RecordingEventStream(int capacity = 512)
    {
        if (capacity < 1)
            throw new ArgumentOutOfRangeException(nameof(capacity));
        _capacity = capacity;
    }

    public long LatestSequence
    {
        get
        {
            lock (_gate)
                return _sequence;
        }
    }

    public RecordingEvent Publish(
        RecordingEventKind kind,
        DateTimeOffset observedAt,
        string? sessionId = null,
        string? runId = null,
        string? recordId = null,
        string? detail = null,
        RecordingActionProjection? action = null)
    {
        lock (_gate)
        {
            long sequence = ++_sequence;
            var value = new RecordingEvent(
                sequence,
                $"recording-event-{sequence:D8}",
                kind,
                observedAt,
                sessionId,
                runId,
                recordId,
                detail,
                action);
            _events.Enqueue(value);
            while (_events.Count > _capacity)
                _events.Dequeue();
            return value;
        }
    }

    public RecordingEventBatch ReadAfter(long afterSequence)
    {
        if (afterSequence < 0)
            throw new ArgumentOutOfRangeException(nameof(afterSequence));

        lock (_gate)
        {
            long oldest = _events.Count == 0 ? _sequence + 1 : _events.Peek().Sequence;
            bool gap = afterSequence < oldest - 1;
            return new RecordingEventBatch(
                afterSequence,
                oldest,
                _sequence,
                gap,
                gap
                    ? Array.Empty<RecordingEvent>()
                    : _events.Where(value => value.Sequence > afterSequence).ToArray());
        }
    }
}

public sealed class RecordingCommandLedger
{
    private readonly object _gate = new();
    private readonly int _capacity;
    private readonly Dictionary<string, RecordingCommandResult> _results = new(StringComparer.Ordinal);
    private readonly Queue<string> _order = new();

    public RecordingCommandLedger(int capacity = 256)
    {
        if (capacity < 1)
            throw new ArgumentOutOfRangeException(nameof(capacity));
        _capacity = capacity;
    }

    public bool TryGet(string commandId, out RecordingCommandResult? result)
    {
        lock (_gate)
            return _results.TryGetValue(commandId, out result);
    }

    public void Remember(string commandId, RecordingCommandResult result)
    {
        if (string.IsNullOrWhiteSpace(commandId))
            throw new ArgumentException("A command id is required.", nameof(commandId));
        lock (_gate)
        {
            if (_results.ContainsKey(commandId))
                return;
            _results.Add(commandId, result);
            _order.Enqueue(commandId);
            while (_order.Count > _capacity)
                _results.Remove(_order.Dequeue());
        }
    }
}

public static class RecordingLifecycleStateMachine
{
    public static RecordingCommandResult Apply(
        RecordingLifecycleSnapshot current,
        RecordingCommandKind command,
        string? newSessionId,
        DateTimeOffset changedAt,
        bool pendingRoot)
    {
        return command switch
        {
            RecordingCommandKind.StartNewSession
                when current.State is RecordingLifecycleState.Ready or RecordingLifecycleState.Closed
                    && !string.IsNullOrWhiteSpace(newSessionId) =>
                Accepted(
                    RecordingLifecycleState.Recording,
                    newSessionId,
                    changedAt,
                    "session_started",
                    "Recording session started and is accepting eligible native-human decisions."),
            RecordingCommandKind.Pause when current.State == RecordingLifecycleState.Recording =>
                Accepted(
                    RecordingLifecycleState.Paused,
                    current.SessionId,
                    changedAt,
                    "recording_paused",
                    pendingRoot
                        ? "Recording is paused for new witnesses; the admitted pending root will still settle."
                        : "Recording is paused; no new decision will be admitted."),
            RecordingCommandKind.Resume when current.State == RecordingLifecycleState.Paused =>
                Accepted(
                    RecordingLifecycleState.Recording,
                    current.SessionId,
                    changedAt,
                    "recording_resumed",
                    "Recording resumed and is accepting eligible native-human decisions."),
            RecordingCommandKind.Close
                when current.State is RecordingLifecycleState.Recording or RecordingLifecycleState.Paused =>
                Accepted(
                    RecordingLifecycleState.Closing,
                    current.SessionId,
                    changedAt,
                    "recording_close_requested",
                    pendingRoot
                        ? $"Close terminates the session before the admitted pending root's successor boundary; it is retained as terminal unknown ({RecordingClosePolicy.TerminalUnknownReason})."
                        : "Close was accepted and the session is ready to flush."),
            _ => new RecordingCommandResult(
                false,
                false,
                "invalid_transition",
                $"Cannot {command.ToString().ToLowerInvariant()} while recording is {current.State.ToString().ToLowerInvariant()}.",
                current)
        };
    }

    public static RecordingLifecycleSnapshot MarkClosed(
        RecordingLifecycleSnapshot current,
        DateTimeOffset changedAt)
    {
        if (current.State != RecordingLifecycleState.Closing)
            throw new InvalidOperationException("Only a closing recording session can be marked closed.");
        return new RecordingLifecycleSnapshot(
            RecordingLifecycleState.Closed,
            current.SessionId,
            changedAt,
            "Recording session is closed; a new isolated session may now be started.");
    }

    private static RecordingCommandResult Accepted(
        RecordingLifecycleState state,
        string? sessionId,
        DateTimeOffset changedAt,
        string code,
        string detail,
        bool pending = false)
    {
        var lifecycle = new RecordingLifecycleSnapshot(
            state,
            sessionId,
            changedAt,
            detail);
        return new RecordingCommandResult(true, pending, code, detail, lifecycle);
    }
}
