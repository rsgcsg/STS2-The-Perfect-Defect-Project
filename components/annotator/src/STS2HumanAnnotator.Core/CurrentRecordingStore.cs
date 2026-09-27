using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Runtime.CompilerServices;

namespace STS2HumanAnnotator.Core;

public sealed class RecordingSessionStore : IDisposable
{
    private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);
    private readonly object _gate = new();
    private readonly Dictionary<string, FileStream> _decisionFiles = new(StringComparer.Ordinal);
    // Keep the fast same-object lookup without retaining complete frame graphs
    // for the lifetime of a recording session.
    private readonly ConditionalWeakTable<CurrentDecisionFrame, SemanticFrameReference>
        _semanticFrames = new();
    private readonly Dictionary<string, SemanticFrameReference> _semanticFramesByDigest =
        new(StringComparer.Ordinal);
    private readonly Dictionary<string, ExecutionSemanticActionSpaceReference>
        _executionSemanticActionSpacesByDigest = new(StringComparer.Ordinal);
    private readonly RecordingPerformanceProfiler _performance = new();
    private readonly FileStream _invalidations;
    private readonly FileStream? _ownerLease;
    private readonly FileStream _journal;
    private readonly FileStream _semanticBoundaryTrace;
    private readonly FileStream _canonicalTransitions;
    private readonly FileStream _nativeSemanticDiscriminator;
    private readonly FileStream? _humanTextInputs;
    private long _humanTextInputSequence;
    private readonly HashSet<string> _humanTextInputIds = new(StringComparer.Ordinal);
    private bool _humanTextInputAppendFailed;
    private readonly Dictionary<string, long> _families = new(StringComparer.Ordinal);
    private readonly Dictionary<string, long> _readsByKind = new(StringComparer.Ordinal);
    private readonly Dictionary<string, long> _invalidationsByReason = new(StringComparer.Ordinal);
    private readonly Dictionary<string, long> _recordedActionFamilies = new(StringComparer.Ordinal);
    private readonly Dictionary<string, long> _invalidatedNativeActions = new(StringComparer.Ordinal);
    private RecordingDecisionCounters _decisions = new(0, 0, 0, 0, 0, 0, DispositionVersion: 1);
    // Non-authorizing counters keyed only by already persisted exact identities.
    private readonly HashSet<string> _countedFailureIds = new(StringComparer.Ordinal);
    private readonly Dictionary<string, long> _failedActionFamilies = new(StringComparer.Ordinal);
    private long _admittedCount;
    private long _invalidationCount;
    private long _readMaterialized;
    private long _readFailed;
    private RecordingItemStatus? _lastRecord;
    private RecordingItemStatus? _lastInvalidation;
    private string _appendHealth = "healthy";
    private string _diskHealth = "healthy";
    private string? _lastError;
    private bool _closed;

    private RecordingSessionStore(
        string directory,
        CurrentRecordingManifest manifest,
        HumanCaptureProfile captureProfile)
    {
        DirectoryPath = directory;
        Manifest = manifest;
        CaptureProfile = captureProfile;
        Directory.CreateDirectory(directory);
        if (manifest.RecoverySchemaVersion == 1)
            _ownerLease = new FileStream(Path.Combine(directory, "recording-owner.lock"),
                FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
        try
        {
            WriteCreateNew(
                Path.Combine(directory, "recording-manifest.json"),
                JsonSerializer.Serialize(manifest, EvidenceJson.IndentedOptions));
            WriteCreateNew(
                Path.Combine(directory, "capture-profile.json"),
                JsonSerializer.Serialize(captureProfile, EvidenceJson.IndentedOptions));
            _invalidations = OpenBufferedAppend(Path.Combine(directory, "invalidations.jsonl"));
            _journal = OpenBufferedAppend(Path.Combine(directory, "run-journal.jsonl"));
            _semanticBoundaryTrace = OpenRecoverableAppend(
                Path.Combine(directory, "semantic-boundary-trace.jsonl"));
            _canonicalTransitions = OpenBufferedAppend(
                Path.Combine(directory, "canonical-transitions.jsonl"));
            _nativeSemanticDiscriminator = OpenBufferedAppend(
                Path.Combine(directory, "native-semantic-discriminator.jsonl"));
            if (manifest.TextInputSchemaVersion == HumanTextInputObservationContract.SchemaVersion)
                _humanTextInputs = OpenBufferedAppend(Path.Combine(directory,
                    HumanTextInputObservationContract.FileName));
            WriteCoverage();
        }
        catch
        {
            _invalidations?.Dispose(); _journal?.Dispose(); _semanticBoundaryTrace?.Dispose();
            _canonicalTransitions?.Dispose(); _nativeSemanticDiscriminator?.Dispose(); _ownerLease?.Dispose();
            _humanTextInputs?.Dispose();
            throw;
        }
    }

    public string DirectoryPath { get; }
    public CurrentRecordingManifest Manifest { get; }
    public HumanCaptureProfile CaptureProfile { get; }

    public T Measure<T>(string phase, Func<T> operation) =>
        _performance.Measure(phase, operation);

    public void Measure(string phase, Action operation) =>
        _performance.Measure(phase, operation);

    public void ObservePerformance(string phase, long elapsedMicroseconds) =>
        _performance.ObserveMicroseconds(phase, elapsedMicroseconds);

    public void MarkDecisionAccountingUnavailable()
    {
        lock (_gate) _decisions = _decisions with { AccountingComplete = false };
    }

    public RecordingStoreSnapshot GetSnapshot()
    {
        lock (_gate)
        {
            return new RecordingStoreSnapshot(
                new RecordingCounters(
                    _admittedCount,
                    _invalidationCount,
                    _readMaterialized,
                    _readFailed, _decisions),
                _lastRecord,
                _lastInvalidation,
                new Dictionary<string, long>(_recordedActionFamilies, StringComparer.Ordinal),
                new Dictionary<string, long>(_invalidatedNativeActions, StringComparer.Ordinal),
                new Dictionary<string, long>(_invalidationsByReason, StringComparer.Ordinal),
                _appendHealth,
                _diskHealth,
                _lastError,
                _closed, new Dictionary<string, long>(_failedActionFamilies, StringComparer.Ordinal));
        }
    }

    public static RecordingSessionStore Create(
        string root,
        CurrentRecordingManifest manifest,
        HumanCaptureProfile captureProfile)
    {
        RecordValidationResult profileValidation = HumanCaptureProfileValidator.Validate(captureProfile);
        if (!profileValidation.Valid)
            throw new InvalidDataException(
                $"Capture profile failed validation: {string.Join(',', profileValidation.Errors)}");
        if (manifest.SchemaVersion != CurrentRecordingContract.SchemaVersion
            || manifest.Schema != CurrentRecordingContract.ManifestSchema
            || manifest.CaptureProfileId != captureProfile.ProfileId
            || manifest.CaptureProfileSha256 != EvidenceIdentity.Sha256Json(captureProfile)
            || manifest.TextInputSchemaVersion is not (null or HumanTextInputObservationContract.SchemaVersion))
            throw new InvalidDataException("Current recording manifest does not bind the capture profile.");
        return new RecordingSessionStore(
            Path.Combine(Path.GetFullPath(root), SafeId(manifest.SessionId, nameof(manifest.SessionId))),
            manifest,
            captureProfile);
    }

    public ReadEvidence PersistRead(CapturedReadPayload capture)
    {
        EnsureOpen();
        try
        {
            ReadEvidence? result = null;
            ExecuteWrite(() =>
            {
                result = PersistReadCore(capture);
                RecordReadUnsafe(capture.Kind, capture.Status == "materialized");
            });
            return result!;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            MarkWriteFailure(exception);
            throw;
        }
    }

    private ReadEvidence PersistReadCore(CapturedReadPayload capture)
    {
        string evidenceId = $"read-{Guid.NewGuid():N}";
        if (capture.Status != "materialized")
        {
            return new ReadEvidence(
                CurrentRecordingContract.SchemaVersion,
                CurrentRecordingContract.ReadEvidenceSchema,
                evidenceId,
                capture.ReadId,
                capture.Kind,
                capture.SnapshotId,
                capture.RuntimeInstanceId,
                capture.EnvironmentFingerprint,
                capture.Status,
                capture.ContentSchema,
                capture.Completeness?.DeepClone(),
                null,
                null,
                capture.CapturedAt,
                capture.ErrorCode ?? "read_not_materialized",
                capture.Detail);
        }
        if (capture.Content == null || capture.Completeness == null
            || string.IsNullOrWhiteSpace(capture.ContentSchema))
            throw new InvalidDataException("A materialized Read requires content, schema and completeness.");
        (byte[] payload, string digest) = _performance.Measure(
            "read_serialize_hash",
            () =>
            {
                byte[] encoded = Encoding.UTF8.GetBytes(
                    EvidenceCanonicalJson.Serialize(capture.Content) + "\n");
                return (encoded, EvidenceIdentity.Sha256Bytes(encoded));
            });
        string relative = $"blobs/sha256/{digest[..2]}/{digest}.json";
        string destination = ResolveRelative(relative);
        _performance.Measure("read_blob_verify_or_write", () =>
        {
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            if (File.Exists(destination))
            {
                if (EvidenceIdentity.Sha256File(destination) != digest)
                    throw new IOException("Content-addressed Read blob collision.");
            }
            else
            {
                string temporary = destination + $".tmp-{Guid.NewGuid():N}";
                File.WriteAllBytes(temporary, payload);
                File.Move(temporary, destination);
            }
        });
        return new ReadEvidence(
            CurrentRecordingContract.SchemaVersion,
            CurrentRecordingContract.ReadEvidenceSchema,
            evidenceId,
            capture.ReadId,
            capture.Kind,
            capture.SnapshotId,
            capture.RuntimeInstanceId,
            capture.EnvironmentFingerprint,
            "materialized",
            capture.ContentSchema,
            capture.Completeness.DeepClone(),
            relative,
            digest,
            capture.CapturedAt,
            null,
            capture.Detail);
    }

    public IReadOnlyList<ReadEvidence> PersistReads(IReadOnlyList<CapturedReadPayload> captures)
    {
        if (captures.Count == 0)
            return Array.Empty<ReadEvidence>();
        EnsureOpen();
        try
        {
            var result = new List<ReadEvidence>(captures.Count);
            ExecuteWrite(() =>
            {
                foreach (CapturedReadPayload capture in captures)
                {
                    result.Add(PersistReadCore(capture));
                    RecordReadUnsafe(capture.Kind, capture.Status == "materialized");
                }
            });
            return result;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            MarkWriteFailure(exception);
            throw;
        }
    }

    public SemanticFrameReference PersistSemanticFrame(CurrentDecisionFrame frame)
    {
        EnsureOpen();
        SemanticFrameReference? result = null;
        ExecuteWrite(() =>
        {
            if (_semanticFrames.TryGetValue(frame, out result))
                return;
            byte[] payload = _performance.Measure(
                "semantic_frame_serialize",
                () =>
                {
                    JsonNode node = JsonSerializer.SerializeToNode(frame, EvidenceJson.Options)
                        ?? throw new InvalidDataException(
                            "Semantic frame serialization returned null.");
                    return Encoding.UTF8.GetBytes(EvidenceCanonicalJson.Serialize(node));
                });
            string digest = _performance.Measure(
                "semantic_frame_hash",
                () => EvidenceIdentity.Sha256Bytes(payload));
            if (_semanticFramesByDigest.TryGetValue(digest, out result))
            {
                _semanticFrames.Add(frame, result);
                return;
            }
            string relative = $"semantic-frames/sha256/{digest[..2]}/{digest}.json";
            string destination = ResolveRelative(relative);
            _performance.Measure("semantic_frame_verify_or_write", () =>
            {
                Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
                if (File.Exists(destination))
                {
                    if (EvidenceIdentity.Sha256File(destination) != digest)
                        throw new IOException("Content-addressed semantic frame collision.");
                }
                else
                {
                    string temporary = destination + $".tmp-{Guid.NewGuid():N}";
                    File.WriteAllBytes(temporary, payload);
                    File.Move(temporary, destination);
                }
            });
            result = new SemanticFrameReference(frame.SnapshotId, digest, relative);
            _semanticFrames.Add(frame, result);
            _semanticFramesByDigest.Add(digest, result);
        });
        return result!;
    }

    public ExecutionSemanticActionSpaceReference PersistExecutionSemanticActionSpace(
        ExecutionSemanticActionSpaceEvidence value)
    {
        IReadOnlyList<string> errors = ExecutionSemanticActionSpaceValidator.Validate(value);
        if (errors.Count > 0
            || (value.SchemaVersion != ExecutionSemanticActionSpaceContract.SchemaVersion || value.Schema != ExecutionSemanticActionSpaceContract.Schema))
            throw new InvalidDataException(
                $"Execution semantic action space failed validation: {string.Join(',', errors)}");
        EnsureOpen();
        ExecutionSemanticActionSpaceReference? result = null;
        ExecuteWrite(() =>
        {
            byte[] payload = _performance.Measure(
                "execution_semantic_action_space_serialize",
                () =>
                {
                    JsonNode node = JsonSerializer.SerializeToNode(value, EvidenceJson.Options)
                        ?? throw new InvalidDataException(
                            "Execution semantic action-space serialization returned null.");
                    return Encoding.UTF8.GetBytes(EvidenceCanonicalJson.Serialize(node));
                });
            string digest = _performance.Measure(
                "execution_semantic_action_space_hash",
                () => EvidenceIdentity.Sha256Bytes(payload));
            if (_executionSemanticActionSpacesByDigest.TryGetValue(digest, out result))
                return;
            string relative =
                $"semantic-action-spaces/sha256/{digest[..2]}/{digest}.json";
            string destination = ResolveRelative(relative);
            _performance.Measure("execution_semantic_action_space_verify_or_write", () =>
            {
                Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
                if (File.Exists(destination))
                {
                    if (EvidenceIdentity.Sha256File(destination) != digest)
                        throw new IOException(
                            "Content-addressed execution semantic action-space collision.");
                }
                else
                {
                    string temporary = destination + $".tmp-{Guid.NewGuid():N}";
                    File.WriteAllBytes(temporary, payload);
                    File.Move(temporary, destination);
                }
            });
            result = new ExecutionSemanticActionSpaceReference(
                value.ActionWitnessId,
                value.SemanticStateDigest,
                value.SemanticCatalogDigest,
                digest,
                relative);
            _executionSemanticActionSpacesByDigest.Add(digest, result);
        });
        return result!;
    }

    public void AppendDecision(CurrentDecisionRecord record)
    {
        EnsureOpen();
        RecordValidationResult validation = CurrentDecisionRecordValidator.Validate(record);
        RecordValidationResult profileValidation =
            HumanCaptureProfileValidator.ValidateRecord(CaptureProfile, record);
        if (!validation.Valid || !profileValidation.Valid)
            throw new InvalidDataException(
                $"Current decision record failed validation: {string.Join(',', validation.Errors.Concat(profileValidation.Errors))}");
        _performance.Measure(
            "decision_read_blob_verify",
            () => VerifyReadBlobs(record.Pre.Reads.Concat(record.Successor.Reads)));
        ExecuteWrite(() =>
        {
            _performance.Measure(
                "decision_append_buffered",
                () => RecoverableAppendBatch.Write(DecisionFile(record.RunId),
                    new[] { JsonSerializer.SerializeToUtf8Bytes(record, EvidenceJson.Options) }));
            _admittedCount++;
            _families[record.DecisionFamily] = _families.GetValueOrDefault(record.DecisionFamily) + 1;
        });
    }

    public void AppendRunEvent(RunJournalEvent value)
    {
        if (value.SchemaVersion != CurrentRecordingContract.SchemaVersion
            || value.Schema != CurrentRecordingContract.RunJournalSchema
            || value.SessionId != Manifest.SessionId
            || value.TimelineId != Manifest.TimelineId
            || value.Sequence <= 0
            || string.IsNullOrWhiteSpace(value.Kind))
            throw new InvalidDataException("Run journal event is invalid for this recording.");
        EnsureOpen();
        ExecuteWrite(() =>
            _performance.Measure("journal_append_buffered", () => AppendBufferedLine(_journal, value)));
    }

    public void AppendSemanticEvidenceEvents(IReadOnlyList<SemanticEvidenceEvent> values)
    {
        foreach (SemanticEvidenceEvent value in values)
            ValidateSemanticEvidenceEvent(value);
        if (values.Count == 0)
            return;
        EnsureOpen();
        ExecuteWrite(() =>
        {
            _performance.Measure("semantic_event_append_buffered", () =>
                RecoverableAppendBatch.Write(
                    _semanticBoundaryTrace,
                    values.Select(value =>
                        JsonSerializer.SerializeToUtf8Bytes(value, EvidenceJson.Options)).ToArray()));
            foreach (var value in values)
                CountDecisionEvent(value.Kind, value.Action.Decision, value.Action.ActionWitnessId);
        });
    }

    private void CountDecisionEvent(string kind, DecisionOccurrenceIdentity? decision, string actionId)
    {
        if (kind == SemanticBoundaryTraceKinds.ActionAccepted)
            _decisions = decision?.DecisionKind == "nested_selector"
                ? _decisions with { AcceptedChildren = _decisions.AcceptedChildren + 1 }
                : _decisions with { AcceptedRoots = _decisions.AcceptedRoots + 1 };
        else if (kind == SemanticBoundaryTraceKinds.TransitionProved)
            _decisions = _decisions with { Proved = _decisions.Proved + 1 };
        else if (kind == SemanticBoundaryTraceKinds.TransitionUnknown)
        {
            _decisions = _decisions with { Unresolved = _decisions.Unresolved + 1 };
            bool firstFailure = !_countedFailureIds.Contains(actionId);
            CountFailure(actionId);
            string family = decision?.DecisionKind is "nested_selector" or "native_selector"
                ? "nested_selector.decision" : decision?.Family ?? "unclassified";
            if (firstFailure) _failedActionFamilies[family] = _failedActionFamilies.GetValueOrDefault(family) + 1;
        }
        else if (kind is SemanticBoundaryTraceKinds.ActionCancelledBeforeStart or SemanticBoundaryTraceKinds.ActionCancelledAfterStart)
            _decisions = _decisions with { Cancelled = _decisions.Cancelled + 1 };
        else if (kind == SemanticBoundaryTraceKinds.ActionAbortedBeforeCommit)
            _decisions = _decisions with { Aborted = _decisions.Aborted + 1 };
    }

    private void CountFailure(string id)
    {
        if (_countedFailureIds.Add(id))
            _decisions = _decisions with { FailedDecisions = _decisions.FailedDecisions + 1 };
    }

    public void AppendCanonicalTransition(CanonicalTransitionEvidence value)
    {
        IReadOnlyList<string> errors = CanonicalTransitionEvidenceValidator.Validate(value);
        if (errors.Count > 0
            || (value.SchemaVersion != CanonicalTransitionEvidenceContract.SchemaVersion || value.Schema != CanonicalTransitionEvidenceContract.Schema)
            || value.SessionId != Manifest.SessionId
            || value.TimelineId != Manifest.TimelineId)
        {
            throw new InvalidDataException(
                $"Canonical transition evidence is invalid: {string.Join(',', errors)}");
        }
        EnsureOpen();
        ExecuteWrite(() =>
        {
            _performance.Measure("canonical_transition_append_buffered",
                () => AppendBufferedLine(_canonicalTransitions, value));
            string family = value.Decision?.DecisionKind is "nested_selector" or "native_selector"
                ? "nested_selector.decision" : value.Decision?.Family ?? "legacy_unclassified";
            _recordedActionFamilies[family] = _recordedActionFamilies.GetValueOrDefault(family) + 1;
            _lastRecord = new RecordingItemStatus(value.TransitionId, value.Action?.Verb ?? value.NativeInput!.Verb, value.RecordedAt, family);
            _decisions = value.Decision?.DecisionKind == "nested_selector"
                ? _decisions with { CanonicalChildren = _decisions.CanonicalChildren + 1 }
                : _decisions with { CanonicalRoots = _decisions.CanonicalRoots + 1 };
        });
    }

    public void AppendNativeSemanticDiscriminatorEvent(
        NativeSemanticDiscriminatorEvent value)
    {
        if (value.SchemaVersion != NativeSemanticDiscriminatorContract.SchemaVersion
            || value.Schema != NativeSemanticDiscriminatorContract.EventSchema
            || value.SessionId != Manifest.SessionId
            || value.TimelineId != Manifest.TimelineId
            || value.Sequence <= 0
            || string.IsNullOrWhiteSpace(value.EventId)
            || string.IsNullOrWhiteSpace(value.Phase)
            || string.IsNullOrWhiteSpace(value.ActionWitnessId)
            || string.IsNullOrWhiteSpace(value.NativeActionType))
            throw new InvalidDataException("Native semantic discriminator event is invalid.");
        EnsureOpen();
        ExecuteWrite(() =>
            _performance.Measure(
                "native_semantic_discriminator_append_buffered",
                () => AppendBufferedLine(_nativeSemanticDiscriminator, value)));
    }

    public void AppendHumanTextInputObservation(HumanTextInputObservation value)
    {
        lock (_gate)
        {
            if (_closed) throw new ObjectDisposedException(nameof(RecordingSessionStore));
            if (_humanTextInputAppendFailed)
                throw new InvalidDataException("Human text input stream already failed this session.");
            try
            {
                if (Manifest.TextInputSchemaVersion != HumanTextInputObservationContract.SchemaVersion
                    || _humanTextInputs is null)
                    throw new InvalidDataException("Human text input stream was not declared by this manifest.");
                IReadOnlyList<string> errors = HumanTextInputObservationValidator.Validate(value);
                if (errors.Count > 0 || value.SessionId != Manifest.SessionId
                    || value.TimelineId != Manifest.TimelineId)
                    throw new InvalidDataException($"Human text input observation is invalid: {string.Join(',', errors)}");
                if (value.Sequence != _humanTextInputSequence + 1)
                    throw new InvalidDataException("Human text input sequence is not contiguous.");
                if (_humanTextInputIds.Contains(value.RecordId))
                    throw new InvalidDataException("Human text input record ID is duplicated.");
                AppendBufferedLine(_humanTextInputs, value);
                _humanTextInputSequence = value.Sequence;
                _humanTextInputIds.Add(value.RecordId);
            }
            catch (Exception exception)
            {
                _humanTextInputAppendFailed = true;
                _appendHealth = "failed";
                _lastError = exception.Message;
                if (exception is IOException or UnauthorizedAccessException)
                    _diskHealth = "failed";
                try
                {
                    WriteCreateNew(Path.Combine(DirectoryPath, "human-text-input-failure.json"),
                        JsonSerializer.Serialize(new {
                            schema = "sts2.human-annotator/human-text-input-failure-1",
                            session_id = Manifest.SessionId, timeline_id = Manifest.TimelineId,
                            failed_at = DateTimeOffset.UtcNow,
                            reason = exception is IOException or UnauthorizedAccessException
                                ? "write_failed" : "append_validation_failed"
                        }, EvidenceJson.IndentedOptions));
                }
                catch (Exception markerError) when (markerError is IOException or UnauthorizedAccessException)
                { /* The permanent latch still prevents a clean close. */ }
                throw;
            }
        }
    }

    public void AppendInvalidation(InvalidationRecord invalidation)
    {
        if (invalidation.SchemaVersion != CurrentRecordingContract.SchemaVersion
            || invalidation.Schema != CurrentRecordingContract.InvalidationSchema
            || invalidation.SessionId != Manifest.SessionId
            || string.IsNullOrWhiteSpace(invalidation.InvalidationId)
            || string.IsNullOrWhiteSpace(invalidation.ReasonCode)
            || HumanActionOccurrenceEvidenceValidator.Validate(invalidation.HumanOccurrence).Count > 0
            || RecordingDisposition.Validate(invalidation).Count > 0
            || (Manifest.DispositionSchemaVersion == 1 && invalidation.Disposition == null))
            throw new InvalidDataException("Invalidation is invalid for this current recording.");
        EnsureOpen();
        ExecuteWrite(() =>
        {
            _performance.Measure(
                "invalidation_append_buffered",
                () => AppendBufferedLine(_invalidations, invalidation));
            _invalidationCount++;
            if (invalidation.DecisionFailure is { } failure && !_countedFailureIds.Contains(failure.DecisionWitnessId))
            {
                CountFailure(failure.DecisionWitnessId);
                _decisions = failure.Kind == "capture"
                    ? _decisions with { CaptureFailures = _decisions.CaptureFailures + 1 }
                    : _decisions with { PersistenceFailures = _decisions.PersistenceFailures + 1 };
                _failedActionFamilies[failure.ActionFamily] = _failedActionFamilies.GetValueOrDefault(failure.ActionFamily) + 1;
            }
            _invalidationsByReason[invalidation.ReasonCode] =
                _invalidationsByReason.GetValueOrDefault(invalidation.ReasonCode) + 1;
            if (!string.IsNullOrWhiteSpace(invalidation.NativeActionType))
            {
                _invalidatedNativeActions[invalidation.NativeActionType] =
                    _invalidatedNativeActions.GetValueOrDefault(invalidation.NativeActionType) + 1;
            }
            _lastInvalidation = new RecordingItemStatus(
                invalidation.InvalidationId,
                invalidation.ReasonCode,
                invalidation.RecordedAt,
                invalidation.Detail);
        });
    }

    public static void WriteRuntimeStatus(string path, RecorderRuntimeStatus status) =>
        WriteAtomic(path, JsonSerializer.Serialize(status, EvidenceJson.IndentedOptions));

    public void Dispose()
    {
        lock (_gate)
        {
            if (_closed)
                return;
            try
            {
                WriteCoverage();
                _performance.Measure("close_evidence_durable_flush", () =>
                {
                    foreach (FileStream stream in _decisionFiles.Values)
                        stream.Flush(flushToDisk: true);
                    _invalidations.Flush(flushToDisk: true);
                    _journal.Flush(flushToDisk: true);
                    _semanticBoundaryTrace.Flush(flushToDisk: true);
                    _canonicalTransitions.Flush(flushToDisk: true);
                    _nativeSemanticDiscriminator.Flush(flushToDisk: true);
                    _humanTextInputs?.Flush(flushToDisk: true);
                });
                WriteAtomic(
                    Path.Combine(DirectoryPath, "performance-profile.json"),
                    JsonSerializer.Serialize(
                        _performance.Snapshot(Manifest.SessionId),
                        EvidenceJson.IndentedOptions));
                foreach (FileStream stream in _decisionFiles.Values)
                    stream.Dispose();
                _invalidations.Dispose();
                _journal.Dispose();
                _semanticBoundaryTrace.Dispose();
                _canonicalTransitions.Dispose();
                _nativeSemanticDiscriminator.Dispose();
                _humanTextInputs?.Dispose();
                if (_humanTextInputAppendFailed)
                {
                    _ownerLease?.Dispose();
                    throw new InvalidDataException("Human text input stream failed; session cannot close cleanly.");
                }
                if (Manifest.CloseSchemaVersion == 1)
                {
                    string receipt = Path.Combine(DirectoryPath, "session-close-receipt.json");
                    string temporary = receipt + $".tmp-{Guid.NewGuid():N}";
                    WriteCreateNew(temporary, JsonSerializer.Serialize(new {
                        schema = "sts2.human-annotator/session-close-1",
                        session_id = Manifest.SessionId, timeline_id = Manifest.TimelineId,
                        closed_at = DateTimeOffset.UtcNow, status = "closed",
                        human_text_input_count = Manifest.TextInputSchemaVersion == 1
                            ? _humanTextInputSequence : (long?)null,
                        human_text_inputs_sha256 = Manifest.TextInputSchemaVersion == 1
                            ? EvidenceIdentity.Sha256File(Path.Combine(DirectoryPath,
                                HumanTextInputObservationContract.FileName)) : null
                    }, EvidenceJson.IndentedOptions));
                    File.Move(temporary, receipt); // only after evidence and receipt bytes flush successfully
                }
                _closed = true;
                _appendHealth = "closed";
                _ownerLease?.Dispose();
            }
            catch (Exception exception)
            {
                MarkWriteFailureUnsafe(exception);
                _decisions = _decisions with { AccountingComplete = false };
                throw;
            }
        }
    }

    private void RecordReadUnsafe(string kind, bool materialized)
    {
        if (materialized)
            _readMaterialized++;
        else
            _readFailed++;
        _readsByKind[kind] = _readsByKind.GetValueOrDefault(kind) + 1;
    }

    private void EnsureOpen()
    {
        lock (_gate)
        {
            if (_closed)
                throw new ObjectDisposedException(nameof(RecordingSessionStore));
        }
    }

    private void ExecuteWrite(Action operation)
    {
        lock (_gate)
        {
            if (_closed)
                throw new ObjectDisposedException(nameof(RecordingSessionStore));
            try
            {
                operation();
                if (!_humanTextInputAppendFailed)
                {
                    _appendHealth = "healthy";
                    _diskHealth = "healthy";
                    _lastError = null;
                }
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                MarkWriteFailureUnsafe(exception);
                throw;
            }
        }
    }

    private void MarkWriteFailure(Exception exception)
    {
        lock (_gate)
            MarkWriteFailureUnsafe(exception);
    }

    private void MarkWriteFailureUnsafe(Exception exception)
    {
        _appendHealth = "failed";
        _diskHealth = "failed";
        _lastError = exception.Message;
    }

    private void VerifyReadBlobs(IEnumerable<ReadEvidence> reads)
    {
        foreach (ReadEvidence read in reads.Where(read => read.Status == "materialized"))
        {
            string path = ResolveRelative(read.PayloadRef!);
            if (!File.Exists(path) || EvidenceIdentity.Sha256File(path) != read.PayloadSha256)
                throw new InvalidDataException($"Read blob is absent or changed: {read.ReadEvidenceId}");
        }
    }

    private string ResolveRelative(string relative)
    {
        string root = Path.GetFullPath(DirectoryPath) + Path.DirectorySeparatorChar;
        string path = Path.GetFullPath(Path.Combine(DirectoryPath, relative));
        if (!path.StartsWith(root, StringComparison.Ordinal))
            throw new InvalidDataException("Evidence path escaped the recording directory.");
        return path;
    }

    private void WriteCoverage()
    {
        var coverage = new CurrentCoverageSummary(
            CurrentRecordingContract.SchemaVersion,
            CurrentRecordingContract.CoverageSchema,
            Manifest.SessionId,
            _admittedCount,
            _invalidationCount,
            _readMaterialized,
            _readFailed,
            new Dictionary<string, long>(_families, StringComparer.Ordinal),
            new Dictionary<string, long>(_readsByKind, StringComparer.Ordinal),
            new Dictionary<string, long>(_invalidationsByReason, StringComparer.Ordinal),
            DateTimeOffset.UtcNow);
        _performance.Measure("coverage_write", () =>
            WriteAtomic(
                Path.Combine(DirectoryPath, "coverage.json"),
                JsonSerializer.Serialize(coverage, EvidenceJson.IndentedOptions)));
    }

    private FileStream DecisionFile(string runId)
    {
        string safe = SafeId(runId, nameof(runId));
        if (!_decisionFiles.TryGetValue(safe, out FileStream? stream))
        {
            stream = OpenRecoverableAppend(Path.Combine(DirectoryPath, $"{safe}.jsonl"));
            _decisionFiles.Add(safe, stream);
        }
        return stream;
    }

    private static string SafeId(string value, string name) =>
        value.All(character => char.IsLetterOrDigit(character) || character is '-' or '_')
            ? value
            : throw new InvalidDataException($"{name} contains unsafe path characters.");

    private static FileStream OpenBufferedAppend(string path) => new(
        path,
        FileMode.Append,
        FileAccess.Write,
        FileShare.Read,
        64 * 1024,
        FileOptions.SequentialScan);

    private static FileStream OpenRecoverableAppend(string path)
    {
        var stream = new FileStream(
            path,
            FileMode.OpenOrCreate,
            FileAccess.Write,
            FileShare.Read,
            64 * 1024,
            FileOptions.SequentialScan);
        stream.Position = stream.Length;
        return stream;
    }

    private static void AppendLine<T>(
        FileStream stream,
        T value,
        bool flushToDisk = true)
    {
        byte[] json = JsonSerializer.SerializeToUtf8Bytes(value, EvidenceJson.Options);
        stream.Write(json);
        stream.WriteByte((byte)'\n');
        if (flushToDisk)
            stream.Flush(flushToDisk: true);
    }

    private static void AppendBufferedLine<T>(FileStream stream, T value)
    {
        AppendLine(stream, value, flushToDisk: false);
        stream.Flush();
    }

    private void ValidateSemanticEvidenceEvent(SemanticEvidenceEvent value)
    {
        if (!SemanticEvidenceContract.IsCurrent(value.SchemaVersion, value.Schema)
            || value.SessionId != Manifest.SessionId
            || value.TimelineId != Manifest.TimelineId
            || value.Sequence <= 0
            || string.IsNullOrWhiteSpace(value.EventId)
            || string.IsNullOrWhiteSpace(value.Kind)
            || string.IsNullOrWhiteSpace(value.Action.ActionWitnessId))
            throw new InvalidDataException("Semantic evidence event is invalid for this recording.");
    }

    private static void WriteCreateNew(string path, string content)
    {
        using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read);
        byte[] bytes = Utf8NoBom.GetBytes(content + "\n");
        stream.Write(bytes);
        stream.Flush(true);
    }

    private static void WriteAtomic(string path, string content)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        string temporary = path + $".tmp-{Guid.NewGuid():N}";
        File.WriteAllText(temporary, content + "\n", Utf8NoBom);
        File.Move(temporary, path, true);
    }
}

internal static class EvidenceCanonicalJson
{
    internal static string Serialize(JsonNode node) => node switch
    {
        JsonObject value => "{" + string.Join(",", value
            .OrderBy(pair => pair.Key, StringComparer.Ordinal)
            .Select(pair => JsonSerializer.Serialize(pair.Key) + ":" + Serialize(pair.Value!))) + "}",
        JsonArray value => "[" + string.Join(",", value.Select(item => Serialize(item!))) + "]",
        _ => node.ToJsonString(EvidenceJson.Options)
    };
}
