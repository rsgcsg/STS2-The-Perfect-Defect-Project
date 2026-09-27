using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

public static class RecordingSessionAuditor
{
    public static RecordingAuditResult Audit(string recordingDirectory)
    {
        string directory = Path.GetFullPath(recordingDirectory);
        var errors = new Dictionary<string, long>(StringComparer.Ordinal);
        long valid = 0;
        long invalid = 0;
        var recordIds = new HashSet<string>(StringComparer.Ordinal);
        long previousSequence = 0;
        CurrentRecordingManifest? manifest = ReadOrError<CurrentRecordingManifest>(
            Path.Combine(directory, "recording-manifest.json"), errors, "manifest_invalid");
        HumanCaptureProfile? profile = ReadOrError<HumanCaptureProfile>(
            Path.Combine(directory, "capture-profile.json"), errors, "capture_profile_invalid");
        if (manifest != null && profile != null
            && (manifest.Schema != CurrentRecordingContract.ManifestSchema
                || manifest.SchemaVersion != CurrentRecordingContract.SchemaVersion
                || manifest.CaptureProfileId != profile.ProfileId
                || manifest.CaptureProfileSha256 != EvidenceIdentity.Sha256Json(profile)))
            Add(errors, "manifest_capture_profile_mismatch");
        if (manifest?.DispositionSchemaVersion is not (null or 1))
            Add(errors, "invalidation_disposition_schema_mismatch");
        if (manifest?.ContinuousSchemaVersion is not (null or 1))
            Add(errors, "continuous_recording_schema_invalid");
        if (manifest?.TextInputSchemaVersion is not (null or HumanTextInputObservationContract.SchemaVersion))
            Add(errors, "human_text_input_schema_invalid");
        if (manifest?.CloseSchemaVersion is not (null or 1))
            Add(errors, "session_close_schema_invalid");
        if (profile != null)
        {
            RecordValidationResult result = HumanCaptureProfileValidator.Validate(profile);
            foreach (string error in result.Errors)
                Add(errors, error);
        }

        string[] decisionPaths = Directory.Exists(directory)
            ? DecisionPaths(directory)
            : Array.Empty<string>();
        if (decisionPaths.Length > 0)
        {
            foreach (string path in decisionPaths)
            {
                foreach ((string line, int lineNumber) in Lines(path))
                {
                    CurrentDecisionRecord? record;
                    try
                    {
                        record = JsonSerializer.Deserialize<CurrentDecisionRecord>(line, EvidenceJson.Options);
                    }
                    catch (JsonException)
                    {
                        invalid++;
                        Add(errors, $"json_invalid_at_{Path.GetFileName(path)}_line_{lineNumber}");
                        continue;
                    }
                    RecordValidationResult result = CurrentDecisionRecordValidator.Validate(record);
                    RecordValidationResult profileResult = profile == null || record == null
                        ? new RecordValidationResult(false, new[] { "capture_profile_missing" })
                        : HumanCaptureProfileValidator.ValidateRecord(profile, record);
                    if (!result.Valid || !profileResult.Valid)
                    {
                        invalid++;
                        foreach (string error in result.Errors.Concat(profileResult.Errors))
                            Add(errors, error);
                        continue;
                    }
                    if (!recordIds.Add(record!.RecordId))
                    {
                        invalid++;
                        Add(errors, "duplicate_record_id");
                        continue;
                    }
                    if (record.Sequence <= previousSequence)
                    {
                        invalid++;
                        Add(errors, "sequence_not_strictly_increasing");
                        continue;
                    }
                    previousSequence = record.Sequence;
                    foreach (ReadEvidence read in record.Pre.Reads.Concat(record.Successor.Reads))
                    {
                        if (read.Status != "materialized")
                            continue;
                        string blob = ResolveBelow(directory, read.PayloadRef!);
                        if (!File.Exists(blob) || EvidenceIdentity.Sha256File(blob) != read.PayloadSha256)
                            Add(errors, "read_blob_missing_or_changed");
                    }
                    valid++;
                }
            }
        }

        if (manifest != null)
        {
            try { InterruptedRecordingRecovery.Validate(directory, manifest); }
            catch (Exception exception) when (exception is IOException or InvalidDataException or JsonException or InvalidOperationException
                or ArgumentException or KeyNotFoundException or UnauthorizedAccessException)
            { Add(errors, "recording_recovery_invalid"); }
        }
        ValidateJournal(directory, manifest, errors);
        ValidateHumanTextInputs(directory, manifest, errors);
        IReadOnlyList<SemanticBoundaryTraceEvent> semanticEvents = ValidateSemanticBoundaryTrace(
            directory,
            manifest,
            errors);
        ValidateNativeSemanticDiscriminator(directory, manifest, errors);
        ValidateCanonicalTransitions(directory, manifest, semanticEvents, errors);
        // Legacy projection is optional only when every durable canonical row
        // has an explicit matching omission. A missing promised legacy file,
        // empty placeholder, or malformed canonical stream still fails closed.
        long invalidations = ValidateInvalidations(directory, manifest, semanticEvents, errors);
        if (errors.Count == 0 && (manifest?.DecisionSchemaVersion == 2 || manifest?.DispositionSchemaVersion == 1))
            ValidateProofProjectionCoverage(directory, profile!, semanticEvents, errors);
        if (decisionPaths.Length == 0
            && (errors.Count != 0 || !(HasOnlyOmittedLegacyProjections(directory)
                || HasOnlyNonCanonicalOccurrences(directory, manifest, semanticEvents)
                || IsEmptyContinuousSession(directory, manifest, semanticEvents))))
            Add(errors, "decision_file_missing");
        return new RecordingAuditResult(
            errors.Count == 0 && invalid == 0 ? "pass" : "fail",
            directory,
            valid,
            invalid,
            invalidations,
            errors,
            new[]
            {
                "audit_does_not_prove_human_origin",
                "audit_does_not_prove_non_interference",
                "audit_does_not_qualify_unseen_families",
                "read_capture_is_player_visible_evidence_not_hidden_state"
            });
    }

    private static void ValidateHumanTextInputs(
        string directory, CurrentRecordingManifest? manifest, IDictionary<string, long> errors)
    {
        if (File.Exists(Path.Combine(directory, "human-text-input-failure.json")))
            Add(errors, "human_text_input_append_failure");
        string path = Path.Combine(directory, HumanTextInputObservationContract.FileName);
        if (manifest?.TextInputSchemaVersion != HumanTextInputObservationContract.SchemaVersion)
        {
            if (File.Exists(path)) Add(errors, "undeclared_human_text_input_stream");
            return;
        }
        if (!File.Exists(path))
        {
            Add(errors, "human_text_input_stream_missing");
            return;
        }
        using (FileStream stream = File.OpenRead(path))
        {
            if (stream.Length > 0)
            {
                stream.Seek(-1, SeekOrigin.End);
                if (stream.ReadByte() != (byte)'\n')
                    Add(errors, "human_text_input_torn_tail");
            }
        }
        long sequence = 0;
        var ids = new HashSet<string>(StringComparer.Ordinal);
        var journalRuns = new HashSet<string>(StringComparer.Ordinal);
        string journalPath = Path.Combine(directory, "run-journal.jsonl");
        if (File.Exists(journalPath))
        {
            foreach ((string journalLine, _) in Lines(journalPath))
            {
                try
                {
                    RunJournalEvent? entry = JsonSerializer.Deserialize<RunJournalEvent>(journalLine, EvidenceJson.Options);
                    if (entry?.Kind is "run_started" or "run_started_native"
                        or "run_resumed_native" or "run_observed_in_progress")
                        journalRuns.Add(entry.RunId);
                }
                catch (JsonException) { /* Journal validation reports malformed rows. */ }
            }
        }
        foreach (string line in File.ReadLines(path))
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                Add(errors, "human_text_input_blank_line");
                continue;
            }
            HumanTextInputObservation? value;
            try { value = JsonSerializer.Deserialize<HumanTextInputObservation>(line, EvidenceJson.Options); }
            catch (JsonException)
            {
                Add(errors, "human_text_input_json_invalid");
                continue;
            }
            foreach (string error in HumanTextInputObservationValidator.Validate(value))
                Add(errors, error);
            if (value == null) continue;
            if (value.SessionId != manifest.SessionId || value.TimelineId != manifest.TimelineId)
                Add(errors, "human_text_input_manifest_mismatch");
            if (!journalRuns.Contains(value.RunId))
                Add(errors, "human_text_input_run_not_observed");
            if (value.Sequence != sequence + 1)
                Add(errors, "human_text_input_sequence_invalid");
            sequence = value.Sequence;
            if (!ids.Add(value.RecordId)) Add(errors, "human_text_input_duplicate_record_id");
        }
        if (manifest.CloseSchemaVersion == 1)
        {
            string receiptPath = Path.Combine(directory, "session-close-receipt.json");
            if (File.Exists(receiptPath))
            {
                try
                {
                    JsonNode? receipt = JsonNode.Parse(File.ReadAllText(receiptPath));
                    // Offline recovery seals its original inventory instead of
                    // claiming a normal producer-side stream count.
                    if (receipt?["recovery"]?.GetValue<string>()
                            != InterruptedRecordingRecovery.Schema
                        && (receipt?["human_text_input_count"]?.GetValue<long>() != sequence
                            || receipt?["human_text_inputs_sha256"]?.GetValue<string>()
                                != EvidenceIdentity.Sha256File(path)))
                        Add(errors, "human_text_input_close_seal_mismatch");
                }
                catch (Exception exception) when (exception is JsonException or InvalidOperationException
                    or FormatException)
                { Add(errors, "human_text_input_close_seal_invalid"); }
            }
        }
    }

    private static void ValidateProofProjectionCoverage(
        string directory, HumanCaptureProfile profile,
        IReadOnlyList<SemanticBoundaryTraceEvent> events, IDictionary<string, long> errors)
    {
        string canonicalPath = Path.Combine(directory, "canonical-transitions.jsonl");
        var canonical = File.Exists(canonicalPath) ? Lines(canonicalPath)
            .Select(line => JsonSerializer.Deserialize<CanonicalTransitionEvidence>(line.Line, EvidenceJson.Options)!)
            .GroupBy(row => row.ActionWitnessId, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal)
            : new Dictionary<string, int>(StringComparer.Ordinal);
        string invalidationPath = Path.Combine(directory, "invalidations.jsonl");
        InvalidationRecord[] failures = File.Exists(invalidationPath) ? Lines(invalidationPath)
            .Select(line => JsonSerializer.Deserialize<InvalidationRecord>(line.Line, EvidenceJson.Options)!)
            .Where(row => row.DecisionFailure?.Kind == "persistence").ToArray() : Array.Empty<InvalidationRecord>();
        RunJournalEvent[] omissions = Lines(Path.Combine(directory, "run-journal.jsonl"))
            .Select(line => JsonSerializer.Deserialize<RunJournalEvent>(line.Line, EvidenceJson.Options)!)
            .Where(row => row.Kind == "canonical_projection_unsupported").ToArray();
        SemanticBoundaryTraceEvent[] proofs = events.Where(row => row.Kind == SemanticBoundaryTraceKinds.TransitionProved).ToArray();
        foreach (SemanticBoundaryTraceEvent proof in proofs)
        {
            string witness = proof.Action.ActionWitnessId;
            string? family = SemanticTransitionProjection.CaptureFamily(proof.Action);
            int successes = canonical.GetValueOrDefault(witness);
            InvalidationRecord[] failed = failures.Where(row => row.DecisionFailure!.DecisionWitnessId == witness).ToArray();
            RunJournalEvent[] unsupported = omissions.Where(row => row.RecordId == proof.Action.RecordId).ToArray();
            bool failureValid = failed.Length == 1
                && failed[0].RunId == proof.Action.RunId
                && profile.SupportedActionFamilies.Contains(failed[0].DecisionFailure!.ActionFamily, StringComparer.Ordinal)
                && (family == null || failed[0].DecisionFailure!.ActionFamily == family);
            bool unsupportedValid = unsupported.Length == 1 && family != null
                && !profile.SupportedActionFamilies.Contains(family, StringComparer.Ordinal)
                && unsupported[0].RunId == proof.Action.RunId && unsupported[0].Detail == family;
            if (successes + (failureValid ? 1 : 0) + (unsupportedValid ? 1 : 0) != 1
                || (failed.Length > 0 && !failureValid) || (unsupported.Length > 0 && !unsupportedValid))
                Add(errors, "proved_action_projection_disposition_missing_or_ambiguous");
        }
        foreach (RunJournalEvent omission in omissions)
            if (!proofs.Any(proof => proof.Action.RecordId == omission.RecordId))
                Add(errors, "unsupported_projection_proof_missing");
    }

    private static bool HasOnlyNonCanonicalOccurrences(
        string directory, CurrentRecordingManifest? manifest,
        IReadOnlyList<SemanticBoundaryTraceEvent> events)
    {
        // A failed/unknown-only current session is valuable immutable evidence.
        // It must not need a fabricated compatibility or canonical success row.
        if ((manifest?.DecisionSchemaVersion != DecisionOccurrenceIdentity.CurrentSchemaVersion
                && manifest?.DispositionSchemaVersion != 1)
            || !File.Exists(Path.Combine(directory, "canonical-transitions.jsonl"))
            || Lines(Path.Combine(directory, "canonical-transitions.jsonl")).Any())
            return false;
        if (events.Any(row => row.Kind == SemanticBoundaryTraceKinds.ActionAccepted))
            return true;
        string path = Path.Combine(directory, "invalidations.jsonl");
        return File.Exists(path) && Lines(path).Any(line =>
            JsonSerializer.Deserialize<InvalidationRecord>(line.Line, EvidenceJson.Options)
                ?.HumanOccurrence?.Disposition == "failed_closed");
    }

    private static bool IsEmptyContinuousSession(string directory, CurrentRecordingManifest? manifest,
        IReadOnlyList<SemanticBoundaryTraceEvent> events)
    {
        // Continuous recording can be armed and stopped entirely in the menu.
        // Explicit empty streams are required; missing promised rows still fail.
        return manifest?.ContinuousSchemaVersion == 1 && manifest.DispositionSchemaVersion == 1
            && events.Count == 0
            && new[] { "canonical-transitions.jsonl", "semantic-boundary-trace.jsonl", "invalidations.jsonl" }
                .All(name => File.Exists(Path.Combine(directory, name)) && !Lines(Path.Combine(directory, name)).Any())
            && !Lines(Path.Combine(directory, "run-journal.jsonl")).Any(line =>
                JsonSerializer.Deserialize<RunJournalEvent>(line.Line, EvidenceJson.Options)!.Kind is
                    "canonical_transition_recorded" or "current_decision_projection_omitted" or "decision_recorded");
    }

    private static bool HasOnlyOmittedLegacyProjections(string directory)
    {
        string canonicalPath = Path.Combine(directory, "canonical-transitions.jsonl");
        string journalPath = Path.Combine(directory, "run-journal.jsonl");
        if (!File.Exists(canonicalPath) || !File.Exists(journalPath))
            return false;
        var canonical = Lines(canonicalPath)
            .Select(line => JsonSerializer.Deserialize<CanonicalTransitionEvidence>(
                line.Line, EvidenceJson.Options)!.TransitionId)
            .ToHashSet(StringComparer.Ordinal);
        var journal = Lines(journalPath)
            .Select(line => JsonSerializer.Deserialize<RunJournalEvent>(
                line.Line, EvidenceJson.Options)!).ToArray();
        var recorded = journal.Where(row => row.Kind == "canonical_transition_recorded")
            .Select(row => row.RecordId ?? string.Empty).ToArray();
        var omitted = journal.Where(row => row.Kind == "current_decision_projection_omitted")
            .Select(row => row.RecordId ?? string.Empty).ToArray();
        return canonical.Count > 0
            && recorded.Length == canonical.Count
            && omitted.Length == canonical.Count
            && canonical.SetEquals(recorded)
            && canonical.SetEquals(omitted);
    }

    private static long ValidateInvalidations(
        string directory,
        CurrentRecordingManifest? manifest,
        IReadOnlyList<SemanticBoundaryTraceEvent> semanticEvents,
        IDictionary<string, long> errors)
    {
        string path = Path.Combine(directory, "invalidations.jsonl");
        if (!File.Exists(path))
            return 0;

        long count = 0;
        foreach ((string line, int lineNumber) in Lines(path))
        {
            count++;
            InvalidationRecord? value;
            try
            {
                value = JsonSerializer.Deserialize<InvalidationRecord>(line, EvidenceJson.Options);
            }
            catch (JsonException)
            {
                Add(errors, $"invalidation_json_invalid_at_line_{lineNumber}");
                continue;
            }
            if (value == null
                || value.SchemaVersion != CurrentRecordingContract.SchemaVersion
                || value.Schema != CurrentRecordingContract.InvalidationSchema
                || manifest == null
                || value.SessionId != manifest.SessionId)
            {
                Add(errors, "invalidation_identity_invalid");
                continue;
            }
            if (manifest.DispositionSchemaVersion == 1 && value.Disposition == null)
                Add(errors, "invalidation_disposition_schema_mismatch");
            foreach (string error in RecordingDisposition.Validate(value))
                Add(errors, error);
            if (value.DecisionFailure is { Kind: "persistence" } failure)
            {
                if (!semanticEvents.Any(row => row.Kind == SemanticBoundaryTraceKinds.ActionAccepted
                        && row.Action.ActionWitnessId == failure.DecisionWitnessId))
                    Add(errors, "invalidation_persistence_action_missing");
                string canonicalPath = Path.Combine(directory, "canonical-transitions.jsonl");
                if (File.Exists(canonicalPath) && Lines(canonicalPath).Any(line =>
                        JsonSerializer.Deserialize<CanonicalTransitionEvidence>(line.Line, EvidenceJson.Options)
                            ?.ActionWitnessId == failure.DecisionWitnessId))
                    Add(errors, "invalidation_persistence_contradicts_canonical");
            }
            foreach (string error in HumanActionOccurrenceEvidenceValidator.Validate(value.HumanOccurrence))
                Add(errors, error);
            if (value.NativeActionType is "NChooseACardSelectionScreen.SelectHolder"
                or "NChooseACardSelectionScreen.OnSkipButtonReleased")
            {
                ValidateGeneratedChoiceOccurrence(value.HumanOccurrence, value.NativeActionType, errors);
            }
        }
        return count;
    }

    private static void ValidateGeneratedChoiceOccurrence(
        HumanActionOccurrenceEvidence? occurrence,
        string nativeActionType,
        IDictionary<string, long> errors)
    {
        if (occurrence == null)
        {
            Add(errors, "generated_choice_human_occurrence_missing");
            return;
        }
        if (!string.Equals(occurrence.NativeActionType, nativeActionType, StringComparison.Ordinal)
            || !string.Equals(occurrence.NativeMechanism, nativeActionType, StringComparison.Ordinal))
        {
            Add(errors, "generated_choice_human_occurrence_identity_mismatch");
        }
        if (string.IsNullOrWhiteSpace(occurrence.NativeOwnerWitnessId))
            Add(errors, "generated_choice_owner_missing");
        if (nativeActionType == "NChooseACardSelectionScreen.SelectHolder")
        {
            if (string.IsNullOrWhiteSpace(occurrence.NativeSubjectWitnessId))
                Add(errors, "generated_choice_subject_missing");
            if (!occurrence.NativeOperands.TryGetValue("selected_card_holder", out string? holder)
                || string.IsNullOrWhiteSpace(holder))
            {
                Add(errors, "generated_choice_selected_holder_missing");
            }
        }
        if (occurrence.PausedParentActionWitnessId != null
            && (string.IsNullOrWhiteSpace(occurrence.PausedParentActionType)
                || string.IsNullOrWhiteSpace(occurrence.PausedParentState)))
        {
            Add(errors, "generated_choice_parent_lineage_incomplete");
        }
    }

    private static void ValidateCanonicalTransitions(
        string directory,
        CurrentRecordingManifest? manifest,
        IReadOnlyList<SemanticBoundaryTraceEvent> semanticEvents,
        IDictionary<string, long> errors)
    {
        string path = Path.Combine(directory, "canonical-transitions.jsonl");
        // A current session may omit canonical rows until semantic proof is
        // persisted; archival streams are not promoted by this audit.
        if (!File.Exists(path))
            return;
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach ((string line, _) in Lines(path))
        {
            CanonicalTransitionEvidence? value;
            try
            {
                value = JsonSerializer.Deserialize<CanonicalTransitionEvidence>(
                    line,
                    EvidenceJson.Options);
            }
            catch (JsonException)
            {
                Add(errors, "canonical_transition_json_invalid");
                continue;
            }
            if (value == null)
            {
                Add(errors, "canonical_transition_null");
                continue;
            }
            if (!CanonicalTransitionEvidenceContract.IsCurrent(
                    value.SchemaVersion,
                    value.Schema))
            {
                Add(errors, "canonical_transition_current_schema_required");
                continue;
            }
            foreach (string error in CanonicalTransitionEvidenceValidator.Validate(value))
                Add(errors, error);
            if (manifest == null
                || value.SessionId != manifest.SessionId
                || value.TimelineId != manifest.TimelineId)
                Add(errors, "canonical_transition_manifest_mismatch");
            if (!seen.Add(value.TransitionId))
                Add(errors, "canonical_transition_duplicate");
            string recordId = value.TransitionId.StartsWith("canonical-", StringComparison.Ordinal)
                ? value.TransitionId["canonical-".Length..]
                : string.Empty;
            CurrentDecisionFrame? pre = ReadSemanticFrameReference(
                directory,
                value.PreStateRef,
                errors);
            CurrentDecisionFrame? successor = ReadSemanticFrameReference(
                directory,
                value.SuccessorRef,
                errors);
            SemanticBoundaryTraceEvent[] proofs = semanticEvents.Where(candidate =>
                    candidate.Kind == SemanticBoundaryTraceKinds.TransitionProved
                    && candidate.Action.ActionWitnessId == value.ActionWitnessId)
                .ToArray();
            if (proofs.Length != 1)
            {
                Add(errors, "canonical_transition_semantic_proof_missing_or_duplicate");
                continue;
            }
            SemanticBoundaryTraceEvent proof = proofs[0];
            if (value.Decision != proof.Action.Decision)
                Add(errors, "canonical_transition_decision_mismatch");
            if (recordId != proof.Action.RecordId
                || value.RunId != proof.Action.RunId
                || value.ActionSequence != proof.Action.ActionSequence
                || EvidenceIdentity.Sha256Json(value.Action)
                    != EvidenceIdentity.Sha256Json(proof.Action.BoundAction)
                || EvidenceIdentity.Sha256Json(value.NativeInput)
                    != EvidenceIdentity.Sha256Json(proof.Action.NativeInput))
                Add(errors, "canonical_transition_semantic_action_mismatch");
            if (pre == null || proof.SemanticPre == null
                || SemanticFrameDigest(pre) != SemanticFrameDigest(proof.SemanticPre))
                Add(errors, "canonical_transition_semantic_pre_mismatch");
            if (successor == null || proof.SemanticSuccessor == null
                || SemanticFrameDigest(successor)
                    != SemanticFrameDigest(proof.SemanticSuccessor))
                Add(errors, "canonical_transition_semantic_successor_mismatch");

            if (value.ActionSpaceAuthority == "native_semantic_execution")
            {
                ExecutionSemanticActionSpaceEvidence? actionSpace =
                    ReadExecutionSemanticActionSpaceReference(
                        directory,
                        value.ExecutionSemanticActionSpaceRef,
                        errors);
                if (actionSpace == null
                    || proof.ExecutionSemanticActionSpace == null
                    || EvidenceIdentity.Sha256Json(actionSpace)
                        != EvidenceIdentity.Sha256Json(proof.ExecutionSemanticActionSpace))
                    Add(errors, "canonical_transition_execution_semantic_evidence_mismatch");
                else
                {
                    foreach (string error in ExecutionSemanticActionSpaceValidator.Validate(
                                 actionSpace,
                                 proof.Action))
                        Add(errors, error);
                }
            }
            else if (value.ActionSpaceAuthority == "public_bound_actions"
                     && (pre == null || value.Action == null || !PublicCatalogContainsExactlyOnce(pre, value.Action)))
            {
                Add(errors, "canonical_transition_public_action_space_invalid");
            }
        }
    }

    private static ExecutionSemanticActionSpaceEvidence?
        ReadExecutionSemanticActionSpaceReference(
            string directory,
            ExecutionSemanticActionSpaceReference? reference,
            IDictionary<string, long> errors,
            bool required = true)
    {
        if (reference == null)
        {
            if (required)
                Add(errors, "execution_semantic_action_space_ref_missing");
            return null;
        }
        string path;
        try
        {
            path = ResolveBelow(directory, reference.ObjectRef);
        }
        catch (InvalidDataException)
        {
            Add(errors, "execution_semantic_action_space_ref_invalid");
            return null;
        }
        if (!File.Exists(path) || EvidenceIdentity.Sha256File(path) != reference.ContentSha256)
        {
            Add(errors, "execution_semantic_action_space_missing_or_changed");
            return null;
        }
        try
        {
            ExecutionSemanticActionSpaceEvidence? value =
                JsonSerializer.Deserialize<ExecutionSemanticActionSpaceEvidence>(
                    File.ReadAllText(path),
                    EvidenceJson.Options);
            if (value == null)
            {
                Add(errors, "execution_semantic_action_space_identity_mismatch");
                return null;
            }
            if (!ExecutionSemanticActionSpaceContract.IsCurrent(
                    value.SchemaVersion,
                    value.Schema))
            {
                Add(errors, "execution_semantic_action_space_current_schema_required");
                return null;
            }
            if (value.ActionWitnessId != reference.ActionWitnessId
                || value.SemanticStateDigest != reference.SemanticStateDigest
                || value.SemanticCatalogDigest != reference.SemanticCatalogDigest)
            {
                Add(errors, "execution_semantic_action_space_identity_mismatch");
                return null;
            }
            return value;
        }
        catch (JsonException)
        {
            Add(errors, "execution_semantic_action_space_json_invalid");
            return null;
        }
    }

    private static bool PublicCatalogContainsExactlyOnce(
        CurrentDecisionFrame frame,
        RecordedBoundAction selected)
    {
        if (frame.Snapshot["completeness"]?["status"]?.GetValue<string>() != "complete"
            || frame.Snapshot["bound_actions"]?["status"]?.GetValue<string>() != "complete"
            || frame.Snapshot["bound_actions"]?["actions"] is not JsonArray actions
            || frame.CatalogCount != actions.Count)
            return false;
        return actions.Count(candidate =>
            candidate?["bound_action_id"]?.GetValue<string>() == selected.BoundActionId
            && candidate?["verb"]?.GetValue<string>() == selected.Verb
            && candidate?["subject_referent_id"]?.GetValue<string>()
                == selected.SubjectReferentId
            && PublicArgumentsMatch(candidate?["arguments"], selected.Arguments)) == 1;
    }

    private static bool PublicArgumentsMatch(
        JsonNode? node,
        IReadOnlyDictionary<string, string> expected)
    {
        if (node is not JsonArray values)
            return expected.Count == 0;
        var actual = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (JsonNode? value in values)
        {
            string? role = value?["role"]?.GetValue<string>();
            string? referent = value?["referent_id"]?.GetValue<string>();
            if (string.IsNullOrWhiteSpace(role)
                || string.IsNullOrWhiteSpace(referent)
                || !actual.TryAdd(role, referent))
                return false;
        }
        return actual.Count == expected.Count
            && actual.All(pair => expected.TryGetValue(pair.Key, out string? referent)
                                  && referent == pair.Value);
    }

    private static CurrentDecisionFrame? ReadSemanticFrameReference(
        string directory,
        SemanticFrameReference reference,
        IDictionary<string, long> errors)
    {
        string path;
        try
        {
            path = ResolveBelow(directory, reference.ObjectRef);
        }
        catch (InvalidDataException)
        {
            Add(errors, "canonical_transition_frame_ref_invalid");
            return null;
        }
        if (!File.Exists(path) || EvidenceIdentity.Sha256File(path) != reference.ContentSha256)
        {
            Add(errors, "canonical_transition_frame_missing_or_changed");
            return null;
        }
        try
        {
            CurrentDecisionFrame? frame = JsonSerializer.Deserialize<CurrentDecisionFrame>(
                File.ReadAllText(path),
                EvidenceJson.Options);
            if (frame == null || frame.SnapshotId != reference.SnapshotId)
            {
                Add(errors, "canonical_transition_frame_identity_mismatch");
                return null;
            }
            return frame;
        }
        catch (JsonException)
        {
            Add(errors, "canonical_transition_frame_json_invalid");
            return null;
        }
    }

    private static string SemanticFrameDigest(CurrentDecisionFrame frame)
    {
        JsonNode node = JsonSerializer.SerializeToNode(frame, EvidenceJson.Options)
            ?? throw new InvalidDataException("Semantic frame serialization returned null.");
        return EvidenceIdentity.Sha256Text(EvidenceCanonicalJson.Serialize(node));
    }

    public static IReadOnlyList<CurrentDecisionRecord> ReadAdmitted(string recordingDirectory)
    {
        RecordingAuditResult audit = Audit(recordingDirectory);
        if (audit.Status != "pass")
            throw new InvalidDataException("Current recording audit must pass before records are read.");
        return DecisionPaths(Path.GetFullPath(recordingDirectory))
            .SelectMany(path => Lines(path).Select(item =>
                JsonSerializer.Deserialize<CurrentDecisionRecord>(item.Line, EvidenceJson.Options)
                ?? throw new InvalidDataException("Current decision record is null.")))
            .ToArray();
    }

    public static long ExportAdmitted(string recordingDirectory, string outputPath)
    {
        IReadOnlyList<CurrentDecisionRecord> records = ReadAdmitted(recordingDirectory);
        string destination = Path.GetFullPath(outputPath);
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        string temporary = destination + $".tmp-{Guid.NewGuid():N}";
        using (var writer = new StreamWriter(temporary, false, new UTF8Encoding(false)))
        {
            writer.NewLine = "\n";
            foreach (CurrentDecisionRecord record in records)
                writer.WriteLine(JsonSerializer.Serialize(record, EvidenceJson.Options));
        }
        File.Move(temporary, destination, true);
        return records.Count;
    }

    private static void ValidateJournal(
        string directory,
        CurrentRecordingManifest? manifest,
        IDictionary<string, long> errors)
    {
        string path = Path.Combine(directory, "run-journal.jsonl");
        if (!File.Exists(path))
        {
            Add(errors, "run_journal_missing");
            return;
        }
        long previous = 0;
        bool closed = false;
        foreach ((string line, _) in Lines(path))
        {
            RunJournalEvent? value;
            try
            {
                value = JsonSerializer.Deserialize<RunJournalEvent>(line, EvidenceJson.Options);
            }
            catch (JsonException)
            {
                Add(errors, "run_journal_json_invalid");
                continue;
            }
            if (value == null
                || value.Schema != CurrentRecordingContract.RunJournalSchema
                || value.SchemaVersion != CurrentRecordingContract.SchemaVersion
                || value.Sequence <= previous
                || manifest == null
                || value.SessionId != manifest.SessionId
                || value.TimelineId != manifest.TimelineId)
            {
                Add(errors, "run_journal_invalid");
                continue;
            }
            previous = value.Sequence;
            closed |= value.Kind == "session_closed";
        }
        if (previous == 0)
            Add(errors, "run_journal_empty");
        if (closed && manifest?.CloseSchemaVersion == 1)
        {
            string receiptPath = Path.Combine(directory, "session-close-receipt.json");
            try
            {
                JsonNode? receipt = File.Exists(receiptPath) ? JsonNode.Parse(File.ReadAllText(receiptPath)) : null;
                if (receipt?["schema"]?.GetValue<string>() != "sts2.human-annotator/session-close-1"
                    || receipt?["session_id"]?.GetValue<string>() != manifest.SessionId
                    || receipt?["timeline_id"]?.GetValue<string>() != manifest.TimelineId
                    || receipt?["status"]?.GetValue<string>() != "closed"
                    || !DateTimeOffset.TryParse(receipt?["closed_at"]?.GetValue<string>(), out _))
                    Add(errors, "session_close_receipt_invalid_or_missing");
            }
            catch (Exception exception) when (exception is IOException or InvalidDataException or JsonException or InvalidOperationException)
            {
                Add(errors, "session_close_receipt_invalid_or_missing");
            }
        }
    }

    private static IReadOnlyList<SemanticBoundaryTraceEvent> ValidateSemanticBoundaryTrace(
        string directory,
        CurrentRecordingManifest? manifest,
        IDictionary<string, long> errors)
    {
        string path = Path.Combine(directory, "semantic-boundary-trace.jsonl");
        // The semantic trace is current evidence. A pre-trace recording is
        // archival input and is not promoted by this current audit.
        if (!File.Exists(path))
            return Array.Empty<SemanticBoundaryTraceEvent>();

        var events = new List<SemanticBoundaryTraceEvent>();
        var semanticFrames = new Dictionary<string, CurrentDecisionFrame>(StringComparer.Ordinal);
        foreach ((string line, _) in Lines(path))
        {
            string? schema;
            try
            {
                using JsonDocument document = JsonDocument.Parse(line);
                schema = document.RootElement.TryGetProperty("schema", out JsonElement property)
                    ? property.GetString()
                    : null;
            }
            catch (JsonException)
            {
                Add(errors, "semantic_boundary_trace_json_invalid");
                continue;
            }
            if (schema == SemanticEvidenceContract.EventSchema)
            {
                SemanticBoundaryTraceEvent? materialized = MaterializeSemanticEvidenceEvent(
                    directory,
                    manifest,
                    line,
                    semanticFrames,
                    errors);
                if (materialized != null)
                    events.Add(materialized);
                continue;
            }
            if (schema != SemanticBoundaryTraceContract.EventSchema)
            {
                Add(errors, "semantic_boundary_trace_current_schema_required");
                continue;
            }
            SemanticBoundaryTraceEvent? value;
            try
            {
                value = JsonSerializer.Deserialize<SemanticBoundaryTraceEvent>(
                    line,
                    EvidenceJson.Options);
            }
            catch (JsonException)
            {
                Add(errors, "semantic_boundary_trace_json_invalid");
                continue;
            }
            if (value == null
                || manifest == null
                || value.SessionId != manifest.SessionId
                || value.TimelineId != manifest.TimelineId)
            {
                Add(errors, "semantic_boundary_trace_session_mismatch");
                continue;
            }
            events.Add(value);
        }
        foreach (string error in SemanticBoundaryTraceValidator.Validate(events))
            Add(errors, error);
        if (manifest?.DecisionSchemaVersion is { } decisionSchema)
        {
            if (decisionSchema is not (1 or DecisionOccurrenceIdentity.CurrentSchemaVersion))
                Add(errors, "decision_manifest_schema_invalid");
            foreach (var value in events)
            {
                if (value.Action.Decision == null)
                    Add(errors, "decision_identity_required_by_manifest");
                else if (value.Action.Decision.SchemaVersion != decisionSchema)
                    Add(errors, "decision_schema_manifest_mismatch");
            }
        }
        return events;
    }

    private static SemanticBoundaryTraceEvent? MaterializeSemanticEvidenceEvent(
        string directory,
        CurrentRecordingManifest? manifest,
        string line,
        IDictionary<string, CurrentDecisionFrame> semanticFrames,
        IDictionary<string, long> errors)
    {
        SemanticEvidenceEvent? value;
        try
        {
            value = JsonSerializer.Deserialize<SemanticEvidenceEvent>(line, EvidenceJson.Options);
        }
        catch (JsonException)
        {
            Add(errors, "semantic_evidence_event_json_invalid");
            return null;
        }
        if (value == null
            || !SemanticEvidenceContract.IsCurrent(value.SchemaVersion, value.Schema)
            || manifest == null
            || value.SessionId != manifest.SessionId
            || value.TimelineId != manifest.TimelineId)
        {
            Add(errors, "semantic_evidence_event_session_mismatch");
            return null;
        }

        CurrentDecisionFrame? humanObservation = ResolveSemanticFrame(
            directory,
            value.HumanObservationRef,
            semanticFrames,
            errors);
        CurrentDecisionFrame? executionPre = ResolveSemanticFrame(
            directory,
            value.ExecutionPreRef,
            semanticFrames,
            errors);
        CurrentDecisionFrame? successor = ResolveSemanticFrame(
            directory,
            value.SuccessorRef,
            semanticFrames,
            errors);
        SemanticBoundaryObservation? boundary = null;
        if (value.Boundary != null)
        {
            CurrentDecisionFrame? state = ResolveSemanticFrame(
                directory,
                value.Boundary.StateRef,
                semanticFrames,
                errors);
            boundary = SemanticBoundaryObservationCodec.Materialize(value.Boundary, state);
        }
        ExecutionSemanticActionSpaceEvidence? executionSemanticActionSpace =
            ReadExecutionSemanticActionSpaceReference(
                directory,
                value.ExecutionSemanticActionSpaceRef,
                errors,
                required: false);

        return new SemanticBoundaryTraceEvent(
            SemanticBoundaryTraceContract.SchemaVersion,
            SemanticBoundaryTraceContract.EventSchema,
            value.EventId,
            value.SessionId,
            value.TimelineId,
            value.RunId,
            value.Sequence,
            value.ObservedAt,
            value.Kind,
            value.Action,
            value.ProofStatus,
            value.RelatedActionWitnessId,
            boundary,
            executionPre,
            successor,
            value.Detail,
            value.NonClaims)
        {
            HumanObservation = humanObservation,
            NativeCompletion = value.NativeCompletion,
            NativeContinuation = value.NativeContinuation,
            NativeHumanContinuation = value.NativeHumanContinuation,
            ExecutionSemanticActionSpace = executionSemanticActionSpace
        };
    }

    private static CurrentDecisionFrame? ResolveSemanticFrame(
        string directory,
        SemanticFrameReference? reference,
        IDictionary<string, CurrentDecisionFrame> semanticFrames,
        IDictionary<string, long> errors)
    {
        if (reference == null)
            return null;
        string cacheKey = $"{reference.ContentSha256}\n{reference.ObjectRef}";
        if (semanticFrames.TryGetValue(cacheKey, out CurrentDecisionFrame? cached))
        {
            if (cached.SnapshotId != reference.SnapshotId)
            {
                Add(errors, "semantic_frame_identity_mismatch");
                return null;
            }
            return cached;
        }
        try
        {
            string path = ResolveBelow(directory, reference.ObjectRef);
            if (!File.Exists(path)
                || EvidenceIdentity.Sha256File(path) != reference.ContentSha256)
            {
                Add(errors, "semantic_frame_missing_or_changed");
                return null;
            }
            CurrentDecisionFrame? frame = JsonSerializer.Deserialize<CurrentDecisionFrame>(
                File.ReadAllText(path),
                EvidenceJson.Options);
            if (frame == null || frame.SnapshotId != reference.SnapshotId)
            {
                Add(errors, "semantic_frame_identity_mismatch");
                return null;
            }
            semanticFrames.Add(cacheKey, frame);
            return frame;
        }
        catch (Exception exception) when (
            exception is IOException or InvalidDataException or JsonException or InvalidDataException)
        {
            Add(errors, "semantic_frame_invalid");
            return null;
        }
    }

    private static void
        ValidateNativeSemanticDiscriminator(
            string directory,
            CurrentRecordingManifest? manifest,
            IDictionary<string, long> errors)
    {
        string path = Path.Combine(directory, "native-semantic-discriminator.jsonl");
        // The stream is diagnostic-only. It can report integrity failures, but
        // per-action coverage/membership never becomes current causal authority.
        if (!File.Exists(path))
            return;

        var events = new List<NativeSemanticDiscriminatorEvent>();
        foreach ((string line, _) in Lines(path))
        {
            NativeSemanticDiscriminatorEvent? value;
            try
            {
                value = JsonSerializer.Deserialize<NativeSemanticDiscriminatorEvent>(
                    line,
                    EvidenceJson.Options);
            }
            catch (JsonException)
            {
                Add(errors, "native_semantic_discriminator_json_invalid");
                continue;
            }
            if (value == null)
            {
                Add(errors, "native_semantic_discriminator_null");
                continue;
            }
            if (manifest == null
                || value.SessionId != manifest.SessionId
                || value.TimelineId != manifest.TimelineId)
                Add(errors, "native_semantic_discriminator_manifest_mismatch");
            events.Add(value);
        }
        if (events.Count == 0)
            return;

        NativeSemanticDiscriminatorReport report =
            NativeSemanticDiscriminatorAnalyzer.Analyze(events);
        foreach (string _ in report.Errors.Where(error =>
                     !NativeSemanticDiscriminatorAnalyzer.IsDiagnosticOnlyError(error)))
            Add(errors, "native_semantic_discriminator_analysis_failed");
    }

    private static T? ReadOrError<T>(string path, IDictionary<string, long> errors, string error)
    {
        try
        {
            return JsonSerializer.Deserialize<T>(File.ReadAllText(path), EvidenceJson.Options);
        }
        catch (Exception exception) when (exception is IOException or InvalidDataException or JsonException)
        {
            Add(errors, error);
            return default;
        }
    }

    private static string[] DecisionPaths(string directory) =>
        Directory.GetFiles(directory, "run-*.jsonl")
            .Where(path => !string.Equals(
                Path.GetFileName(path),
                "run-journal.jsonl",
                StringComparison.Ordinal))
            .Order(StringComparer.Ordinal)
            .ToArray();

    private static string ResolveBelow(string rootDirectory, string relative)
    {
        string root = Path.GetFullPath(rootDirectory) + Path.DirectorySeparatorChar;
        string path = Path.GetFullPath(Path.Combine(rootDirectory, relative));
        if (!path.StartsWith(root, StringComparison.Ordinal))
            throw new InvalidDataException("Read payload path escaped the recording directory.");
        return path;
    }

    private static IEnumerable<(string Line, int Number)> Lines(string path)
    {
        int number = 0;
        foreach (string line in File.ReadLines(path))
        {
            number++;
            if (!string.IsNullOrWhiteSpace(line))
                yield return (line, number);
        }
    }

    private static void Add(IDictionary<string, long> errors, string key)
    {
        errors.TryGetValue(key, out long count);
        errors[key] = count + 1;
    }
}
