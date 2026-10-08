using System.Text.Json;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    private static ISourceRecordingBridge? _sourceBridge;
    private static ISourceRecordingAttachment? _sourceAttachment;
    private static SourceClockReference? _sourceCloseBoundary;
    private static SourceSessionStatus? _lastSourceStatus;
    private static string _activeCaptureProfileId = HumanCaptureProfiles.FullRunReadRich.ProfileId;
    private static readonly Dictionary<string, (string Fingerprint, RecordingCommandResult Result)> SourceCommands = new(StringComparer.Ordinal);
    private static bool IsSourceRecording => _activeCaptureProfileId == SourceSessionContract.ProfileId;

    internal static void ConfigureSourceBridge(ISourceRecordingBridge bridge)
    {
        ArgumentNullException.ThrowIfNull(bridge);
        lock (Gate)
        {
            if (_lifecycle.State is not (RecordingLifecycleState.Ready or RecordingLifecycleState.Closed))
                throw new InvalidOperationException("Cannot replace the passive bridge in an active recording.");
            _sourceBridge = bridge;
        }
    }

    private static bool IsSourceCommand(RecordingCommand command) =>
        command.Schema == RecordingApplicationContract.SourceCommandSchema
        || command.CaptureProfileId == SourceSessionContract.ProfileId
        || command.Kind == RecordingCommandKind.ChangeSource
        || IsSourceRecording && command.Kind != RecordingCommandKind.StartNewSession;

    private static RecordingCommandResult ExecuteSourceCommand(
        RecordingCommand command, RecordingSessionExpectation? expectedSession)
    {
        if (command.Schema != RecordingApplicationContract.SourceCommandSchema)
            return RejectedCommand("source_command_schema_required", "Use the explicit source recording command schema.");
        if (string.IsNullOrWhiteSpace(command.CommandId))
            return RejectedCommand("invalid_command_id", "Recording command_id is required.");
        try
        {
            SourceSessionContract.Identifier(command.CommandId);
            if (command.SourceDeclaration != null) SourceSessionContract.Validate(command.SourceDeclaration);
            if (command.ExpectedSourceSegmentId != null) SourceSessionContract.Identifier(command.ExpectedSourceSegmentId);
            if (command.CaptureProfileId != null) SourceSessionContract.Identifier(command.CaptureProfileId);
            if (command.Kind is not (RecordingCommandKind.StartNewSession or RecordingCommandKind.ChangeSource)
                && (command.SourceDeclaration != null || command.ExpectedSourceSegmentId != null))
                throw new InvalidDataException("source_command_fields_not_applicable");
        }
        catch (InvalidDataException)
        { return RejectedCommand("invalid_source_command", "Source command fields are invalid for this operation."); }
        string fingerprint = EvidenceIdentity.Sha256Json(new { command, expected_session = expectedSession?.SessionId });
        RecordingCommandResult result;
        lock (Gate)
        {
            if (expectedSession != null && expectedSession.SessionId != _lifecycle.SessionId)
                return RejectedCommand("recording_session_changed", "The expected recording session changed.");
            if (SourceCommands.TryGetValue(command.CommandId, out var replay))
                return replay.Fingerprint == fingerprint ? replay.Result
                    : RejectedCommand("recording_command_conflict", "This command ID belongs to a different exact request.");
            if (CommandLedger.TryGet(command.CommandId, out _))
                return RejectedCommand("recording_command_conflict", "This command ID already belongs to another recording request.");
            if (SourceCommands.Count >= 256)
                return RejectedCommand("source_command_capacity", "This runtime retained its announced 256 source command outcomes.");
            if (!_initialized)
                return RejectedCommand("not_initialized", "Recorder runtime is not initialized.");
            try
            {
                if (command.Kind == RecordingCommandKind.StartNewSession)
                {
                    if (command.CaptureProfileId != SourceSessionContract.ProfileId || command.SourceDeclaration == null)
                        throw new InvalidDataException("source_profile_and_declaration_required");
                    result = RecordingLifecycleStateMachine.Apply(_lifecycle, command.Kind,
                        "session-" + Guid.NewGuid().ToString("N"), DateTimeOffset.UtcNow, false);
                    if (result.Accepted) StartSourceSession(result.Lifecycle, command.SourceDeclaration);
                }
                else if (IsSourceRecording && IsStateNoOp(command.Kind, _lifecycle.State))
                    result = new(true, _lifecycle.State == RecordingLifecycleState.Closing,
                        "already_" + _lifecycle.State.ToString().ToLowerInvariant(), _lifecycle.Detail, _lifecycle);
                else if (!IsSourceRecording || _store?.IsSourceSession != true || _sourceAttachment == null)
                    return RejectedCommand("source_session_required", "There is no active source recording.");
                else if (command.Kind == RecordingCommandKind.ChangeSource)
                {
                    if (command.SourceDeclaration == null || command.ExpectedSourceSegmentId == null)
                        throw new InvalidDataException("source_declaration_and_expected_segment_required");
                    _store.ChangeSource(command.SourceDeclaration, command.ExpectedSourceSegmentId,
                        _sourceAttachment.ReadBoundaryClock(), _lifecycle.State);
                    result = new(true, false, "source_changed", "Source declaration changed at an explicit paused boundary.", _lifecycle);
                }
                else
                {
                    result = RecordingLifecycleStateMachine.Apply(_lifecycle, command.Kind, null,
                        DateTimeOffset.UtcNow, _store.GetSourceStatus()!.PendingInputs != 0);
                    if (result.Accepted)
                    {
                        SourceClockReference clock = _sourceAttachment.ReadBoundaryClock();
                        string kind = command.Kind switch
                        {
                            RecordingCommandKind.Pause => "pause", RecordingCommandKind.Resume => "resume",
                            RecordingCommandKind.Close => "close", _ => throw new InvalidOperationException("source_command_invalid")
                        };
                        _store.RecordSourceBoundary(kind, clock, result.Lifecycle.State);
                        _lifecycle = result.Lifecycle;
                        if (kind == "close")
                        {
                            _sourceCloseBoundary = clock;
                            _closeout = new("closing", DateTimeOffset.UtcNow, null,
                                "Source Close awaits all reserved publications through its exact boundary.");
                        }
                    }
                }
            }
            catch (Exception exception)
            {
                result = RejectedCommand("source_recording_command_failed", exception.GetType().Name + ":" + exception.Message);
                _detail = result.Detail;
            }
            if (_store?.IsSourceSession == true) _lastSourceStatus = _store.GetSourceStatus();
            SourceCommands.Add(command.CommandId, (fingerprint, result));
            CommandLedger.Remember(command.CommandId, result);
            _runtimeState = RuntimeStatusForLifecycle(_lifecycle.State, _runtimeState);
        }
        PublishCommandEvent(command, result);
        if (result.Accepted && command.Kind == RecordingCommandKind.Close) FinalizeSourceClose();
        return result;
    }

    private static void StartSourceSession(RecordingLifecycleSnapshot lifecycle, SourceDeclaration source)
    {
        SourceSessionContract.Validate(source);
        if (_configuration == null || _sourceRevision == null || _sourceBridge == null)
            throw new InvalidOperationException("source_bridge_unavailable");
        ISourceRecordingAttachment attachment = _sourceBridge.Attach(); // no callback until Activate
        try
        {
            SourceBridgeContext context = attachment.Context;
            var manifest = new CurrentRecordingManifest(1, SourceSessionContract.ManifestSchema,
                lifecycle.SessionId!, "timeline-" + Guid.NewGuid().ToString("N"), DateTimeOffset.UtcNow,
                RecorderMod.Version, _sourceRevision, System.Runtime.InteropServices.RuntimeInformation.RuntimeIdentifier,
                SourceSessionContract.ProfileId, SourceSessionContract.ProfileDigest(context.Profile),
                Array.Empty<string>(), SourceSessionContract.NonClaims)
            { SourceSchemaVersion = 1, SourceEnvironment = context.Environment, RecoverySchemaVersion = 1 };
            RecordingSessionStore store = RecordingSessionStore.CreateSource(_configuration.RecordingRoot,
                manifest, context.Profile, source, context.StartingClock);
            _store = store; _sourceAttachment = attachment; _sourceCloseBoundary = null;
            SessionId = manifest.SessionId; TimelineId = manifest.TimelineId;
            _recordingDirectory = store.DirectoryPath; _sessionStartedAt = manifest.CreatedAt; _sessionClosedAt = null;
            _activeCaptureProfileId = SourceSessionContract.ProfileId; _lastSourceStatus = store.GetSourceStatus();
            _sequence = 0; _journalSequence = 0; _semanticBoundaryEventSequence = 0; _humanTextInputSequence = 0;
            _humanTextInputHealthy = true; _humanTextInputPendingScopes = 0;
            _semanticBoundaryTraceHealthy = true; _closeDispositionPersistenceFailed = false;
            _closeProjectionPersistenceFailed = false; _closeout = RecordingCloseoutStatus.Idle;
            ResetNativeActionTrackingUnsafe(); RunLifecycle.Reset(); TerminalSeal.Reset(); Continuous.Disarm();
            _lastEnvironment = context.Environment; _lastSnapshotId = null; _lastBlockers = SourceSessionContract.NonClaims;
            _requiredReadsHealth = "source_bridge"; _lifecycle = lifecycle; _runtimeState = "source_recording";
            AppendJournal("session_started", null, null, "Explicit native-logical source recording; no Human attestation.");
            attachment.Activate(ObserveSourcePublication); // replay includes initial reservation after store is ready
        }
        catch
        {
            attachment.Dispose();
            _store?.MarkSourceAccountingFailed("source_bridge_activation_failed");
            throw;
        }
    }

    private static void ObserveSourcePublication(SourceObservationPacket packet)
    {
        lock (Gate)
        {
            if (_store?.IsSourceSession != true || _lifecycle.State is RecordingLifecycleState.Ready or RecordingLifecycleState.Closed)
                return;
            try
            {
                _store.AppendPublicObservation(packet);
                _lastSourceStatus = _store.GetSourceStatus(); _lastSnapshotId = packet.SnapshotId;
            }
            catch (Exception exception)
            {
                _store.MarkSourceAccountingFailed("source_publication_append_failed");
                _lastSourceStatus = _store.GetSourceStatus(); _runtimeState = "source_accounting_failed";
                _detail = exception.GetType().Name;
            }
        }
    }

    internal static SourceInputScope BeginSourceInput(string inputId, SourceClockReference clock,
        FrozenPublicCapture? capture, FrozenPublicCatalog? catalog)
    {
        lock (Gate)
        {
            if (_store?.IsSourceSession != true) throw new InvalidOperationException("source_profile_required");
            // Freeze source context before copying/encoding work. The producer may pass null and bind later.
            SourceInputScope scope = _store.BeginSourceInput(inputId, clock, null, null, _lifecycle.State);
            if (capture == null && catalog == null) return scope;
            PublicCaptureReference? pre = capture == null ? null : _store.PersistPublicCapture(capture);
            PublicCatalogReference? relation = catalog == null ? null : _store.PersistPublicCatalog(catalog);
            return _store.BindSourceInputBasis(scope, pre, relation);
        }
    }

    internal static SourceInputScope BindSourceInputBasis(SourceInputScope scope,
        FrozenPublicCapture? capture, FrozenPublicCatalog? catalog)
    {
        lock (Gate)
        {
            if (_store?.IsSourceSession != true) throw new InvalidOperationException("source_profile_required");
            PublicCaptureReference? pre = capture == null ? null : _store.PersistPublicCapture(capture);
            PublicCatalogReference? relation = catalog == null ? null : _store.PersistPublicCatalog(catalog);
            return _store.BindSourceInputBasis(scope, pre, relation);
        }
    }

    internal static void CompleteSourceInput(SourceInputScope scope, SourceInputOutcome outcome)
    {
        lock (Gate)
        {
            if (_store?.IsSourceSession != true) throw new InvalidOperationException("source_profile_required");
            _store.CompleteSourceInput(scope, outcome);
            _lastSourceStatus = _store.GetSourceStatus();
        }
    }

    private static void FinalizeSourceClose()
    {
        lock (Gate)
        {
            if (_lifecycle.State != RecordingLifecycleState.Closing || _store?.IsSourceSession != true
                || _sourceAttachment == null || _sourceCloseBoundary == null)
                return;
            try
            {
                if (!_sourceAttachment.IsDrainedThrough(_sourceCloseBoundary)) return;
                AppendJournal("session_closed", null, _lastSnapshotId, "Source streams flushed at explicit Close.");
                _store.Dispose(); _lastSourceStatus = _store.GetSourceStatus();
                _lastStoreSnapshot = _store.GetSnapshot(); _store = null;
                _sourceAttachment.Dispose(); _sourceAttachment = null;
                _sessionClosedAt = DateTimeOffset.UtcNow;
                _lifecycle = RecordingLifecycleStateMachine.MarkClosed(_lifecycle, _sessionClosedAt.Value);
                _closeout = new("closed", _closeout.RequestedAt, _sessionClosedAt, "Source recording durably closed.");
                _runtimeState = "source_recording_closed";
                PublishApplicationEvent(RecordingEventKind.SessionClosed, detail: _closeout.Detail);
            }
            catch (Exception exception)
            {
                _store?.MarkSourceAccountingFailed("source_close_persistence_failed");
                _lastSourceStatus = _store?.GetSourceStatus(); _runtimeState = "source_close_failed";
                _detail = exception.GetType().Name;
                _closeout = _closeout with { State = "closing", Detail = "Source accounting or durable Close failed." };
            }
        }
    }
}
