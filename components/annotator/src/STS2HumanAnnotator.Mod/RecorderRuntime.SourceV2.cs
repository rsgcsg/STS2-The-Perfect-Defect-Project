using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    private static SourceRecordingWorkerV2? _sourceWorkerV2;
    private static SourceSessionStatusV2? _lastSourceStatusV2;
    private static bool IsSourceRecordingV2 => _activeCaptureProfileId is SourceSessionContractV2.ProfileId or SourceSessionContractV3.ProfileId;

    private static RecordingCommandResult ExecuteSourceCommandV2(RecordingCommand command, RecordingSessionExpectation? expectedSession)
    {
        if (command.Schema != SourceSessionContractV2.CommandSchema)
            return RejectedCommand("source_v2_command_schema_required", "Use the explicit source recording command-3 schema.");
        try
        {
            SourceSessionContract.Identifier(command.CommandId);
            if (command.SourceDeclaration != null) SourceSessionContract.Validate(command.SourceDeclaration);
            if (command.ExpectedSourceSegmentId != null) SourceSessionContract.Identifier(command.ExpectedSourceSegmentId);
            if (command.Kind is not (RecordingCommandKind.StartNewSession or RecordingCommandKind.ChangeSource)
                && (command.SourceDeclaration != null || command.ExpectedSourceSegmentId != null || command.CaptureProfileId != null))
                throw new InvalidDataException("source_command_fields_not_applicable");
        }
        catch (InvalidDataException) { return RejectedCommand("invalid_source_command", "Source command fields are invalid for this operation."); }
        string fingerprint = EvidenceIdentity.Sha256Json(new { command, expected_session = expectedSession?.SessionId });
        RecordingCommandResult result;
        lock (Gate)
        {
            if (!_initialized) return RejectedCommand("not_initialized", "Recorder runtime is not initialized.");
            if (expectedSession != null && expectedSession.SessionId != _lifecycle.SessionId)
                return RejectedCommand("recording_session_changed", "The expected recording session changed.");
            if (SourceCommands.TryGetValue(command.CommandId, out var replay))
                return replay.Fingerprint == fingerprint ? replay.Result : RejectedCommand("recording_command_conflict", "The command ID belongs to another exact request.");
            if (CommandLedger.TryGet(command.CommandId, out _) || SourceCommands.Count >= 256)
                return RejectedCommand("recording_command_conflict_or_capacity", "The recording command ID is unavailable.");
            try
            {
                if (command.Kind == RecordingCommandKind.StartNewSession)
                {
                    if (command.CaptureProfileId is not (SourceSessionContractV2.ProfileId or SourceSessionContractV3.ProfileId) || command.SourceDeclaration == null)
                        throw new InvalidDataException("source_v2_profile_and_declaration_required");
                    result = RecordingLifecycleStateMachine.Apply(_lifecycle, command.Kind,
                        "session-" + Guid.NewGuid().ToString("N"), DateTimeOffset.UtcNow, false);
                    result = SourceLifecycleDescription(result, command.Kind);
                    if (result.Accepted) StartSourceSessionV2(result.Lifecycle, command.SourceDeclaration, command.CaptureProfileId!);
                }
                else if (!IsSourceRecordingV2 || _sourceWorkerV2 == null || _store?.IsSourceSessionV2 != true)
                    return RejectedCommand("source_v2_session_required", "There is no active source-v2 recording.");
                else if (IsStateNoOp(command.Kind, _lifecycle.State))
                    result = new(true, _lifecycle.State == RecordingLifecycleState.Closing, "already_" + _lifecycle.State.ToString().ToLowerInvariant(), _lifecycle.Detail, _lifecycle);
                else if (command.Kind == RecordingCommandKind.ChangeSource)
                {
                    if (command.SourceDeclaration == null || command.ExpectedSourceSegmentId == null || command.CaptureProfileId != null)
                        throw new InvalidDataException("source_declaration_and_expected_segment_required");
                    _sourceWorkerV2.ChangeSource(command.SourceDeclaration, command.ExpectedSourceSegmentId);
                    result = new(true, false, "source_changed", "Source declaration changed at the explicit paused boundary.", _lifecycle);
                }
                else
                {
                    result = RecordingLifecycleStateMachine.Apply(_lifecycle, command.Kind, null, DateTimeOffset.UtcNow, _sourceWorkerV2.Status.PendingInputs != 0);
                    result = SourceLifecycleDescription(result, command.Kind);
                    if (result.Accepted)
                    {
                        string kind = command.Kind switch
                        { RecordingCommandKind.Pause => "pause", RecordingCommandKind.Resume => "resume", RecordingCommandKind.Close => "close", _ => throw new InvalidDataException("source_command_invalid") };
                        _sourceWorkerV2.CommandBoundary(kind); _lifecycle = result.Lifecycle;
                        if (kind == "close") _closeout = new("closing", DateTimeOffset.UtcNow, null, "Source Close drains every original sealed epoch and admitted input.");
                    }
                }
            }
            catch (Exception exception)
            { result = RejectedCommand("source_recording_command_failed", exception.GetType().Name + ":" + exception.Message); _detail = result.Detail; }
            _lastSourceStatusV2 = _store?.GetSourceStatusV2() ?? _lastSourceStatusV2;
            SourceCommands.Add(command.CommandId, (fingerprint, result)); CommandLedger.Remember(command.CommandId, result);
            _runtimeState = RuntimeStatusForLifecycle(_lifecycle.State, _runtimeState);
        }
        PublishCommandEvent(command, result); return result;
    }
    private static void StartSourceSessionV2(RecordingLifecycleSnapshot lifecycle, SourceDeclaration source, string profileId)
    {
        if (_configuration == null || _sourceRevision == null) throw new InvalidOperationException("source_runtime_unavailable");
        int version = profileId == SourceSessionContractV3.ProfileId ? 3 : 2;
        var format = SourceSessionWireFormat.ForVersion(version);
        var attachment = NativeLogicalSourceRecording.Attach(version == 3);
        RecordingSessionStore? store = null;
        try
        {
            var environment = BuildEnvironment(attachment.Capabilities, attachment.ConnectorSourceDigest);
            var epoch = SourceRecordingWorkerV2.Packet(attachment.InitialEpoch, environment);
            var profile = new SourceCaptureProfileV2(format.Schema("source-capture-profile"), format.ProfileId,
                "native-logical-v1", epoch.Context.PublicationProfileId, epoch.Context.PublicationProfileDefinitionSha256,
                epoch.Context.EagerScope, new(), SourceSessionContractV2.NonClaims);
            var manifest = new CurrentRecordingManifest(version, format.Schema("source-session-manifest"), lifecycle.SessionId!,
                "timeline-" + Guid.NewGuid().ToString("N"), DateTimeOffset.UtcNow, RecorderMod.Version, _sourceRevision,
                System.Runtime.InteropServices.RuntimeInformation.RuntimeIdentifier, profile.ProfileId,
                SourceSessionContractV2.ProfileDigest(profile), Array.Empty<string>(), SourceSessionContractV2.NonClaims)
            { SourceSchemaVersion = version, SourceEnvironment = environment, RecoverySchemaVersion = 1 };
            store = version == 3 ? RecordingSessionStore.CreateSourceV3(_configuration.RecordingRoot, manifest, profile, source, epoch)
                : RecordingSessionStore.CreateSourceV2(_configuration.RecordingRoot, manifest, profile, source, epoch);
            var worker = new SourceRecordingWorkerV2(store, attachment, environment);
            _store = store; _sourceWorkerV2 = worker; _lastSourceStatusV2 = store.GetSourceStatusV2();
            _sourcePublicationBinding = null; _sourceAttachment = null; _sourceCloseBoundary = null;
            SessionId = manifest.SessionId; TimelineId = manifest.TimelineId; _recordingDirectory = store.DirectoryPath;
            _sessionStartedAt = manifest.CreatedAt; _sessionClosedAt = null; _activeCaptureProfileId = profileId;
            _sequence = 0; _journalSequence = 0; _semanticBoundaryEventSequence = 0; _humanTextInputSequence = 0;
            _humanTextInputHealthy = true; _humanTextInputPendingScopes = 0; _semanticBoundaryTraceHealthy = true;
            _closeDispositionPersistenceFailed = false; _closeProjectionPersistenceFailed = false; _closeout = RecordingCloseoutStatus.Idle;
            ResetNativeActionTrackingUnsafe(); RunLifecycle.Reset(); TerminalSeal.Reset(); Continuous.Disarm();
            _lastEnvironment = environment; _lastSnapshotId = null; _lastBlockers = SourceSessionContractV2.NonClaims;
            _requiredReadsHealth = "source_bridge"; _lifecycle = lifecycle; _runtimeState = "source_recording";
            _lastSourceStatus = null;
        }
        catch
        {
            if (store != null) store.AbortSourceV2("source_bridge_activation_failed"); attachment.Dispose(); throw;
        }
    }
    private static void FinalizeSourceCloseV2()
    {
        lock (Gate)
        {
            if (!IsSourceRecordingV2 || _sourceWorkerV2 is not { } worker || _store == null) return;
            _lastSourceStatusV2 = worker.Status; _lastSnapshotId = worker.SnapshotId;
            if (!worker.Completion.IsCompleted) return;
            if (!worker.Completion.IsCompletedSuccessfully)
            {
                _runtimeState = "source_accounting_failed"; _detail = worker.Status.Error ?? "source_worker_failed";
                _closeout = _closeout with { Detail = "Source worker failed; no clean final drain or bundle is asserted." };
                return;
            }
            if (_lifecycle.State != RecordingLifecycleState.Closing) return;
            _lastStoreSnapshot = _store.GetSnapshot(); _store = null; worker.Dispose(); _sourceWorkerV2 = null;
            _sessionClosedAt = DateTimeOffset.UtcNow; _lifecycle = RecordingLifecycleStateMachine.MarkClosed(_lifecycle, _sessionClosedAt.Value);
            _closeout = new("closed", _closeout.RequestedAt, _sessionClosedAt, "Source epochs and admitted inputs durably closed.");
            _runtimeState = "source_recording_closed"; PublishApplicationEvent(RecordingEventKind.SessionClosed, detail: _closeout.Detail);
        }
    }
}
