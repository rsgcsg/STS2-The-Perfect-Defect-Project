using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

public static class HumanCaptureProfileValidator
{
    private static readonly HashSet<string> Phases = new(StringComparer.Ordinal)
    {
        "pre",
        "successor"
    };

    public static RecordValidationResult Validate(HumanCaptureProfile? profile)
    {
        var errors = new List<string>();
        if (profile == null)
            return new RecordValidationResult(false, new[] { "capture_profile_missing" });
        if (profile.SchemaVersion != CurrentRecordingContract.SchemaVersion
            || profile.Schema != CurrentRecordingContract.CaptureProfileSchema)
            errors.Add("capture_profile_schema_mismatch");
        if (string.IsNullOrWhiteSpace(profile.ProfileId)
            || profile.RecordSchema != (profile.ProfileId == P1NativeRecordingContract.ProfileId
                ? P1NativeRecordingContract.EventSchema : CurrentRecordingContract.RecordSchema))
            errors.Add("capture_profile_identity_incomplete");
        if (profile.SupportedActionFamilies.Count == 0
            || profile.SupportedActionFamilies.Any(string.IsNullOrWhiteSpace)
            || profile.SupportedActionFamilies.Count
            != profile.SupportedActionFamilies.Distinct(StringComparer.Ordinal).Count())
            errors.Add("capture_profile_action_families_invalid");
        if (profile.Reads.Count == 0
            || profile.Reads.Any(read => !Phases.Contains(read.Phase)
                || string.IsNullOrWhiteSpace(read.Kind))
            || profile.Reads.Count != profile.Reads
                .Select(read => $"{read.Phase}\0{read.Kind}\0{read.InteractionKind}")
                .Distinct(StringComparer.Ordinal)
                .Count())
            errors.Add("capture_profile_reads_invalid");
        return new RecordValidationResult(errors.Count == 0, errors);
    }

    public static RecordValidationResult ValidateRecord(
        HumanCaptureProfile profile,
        CurrentDecisionRecord record)
    {
        var errors = new List<string>();
        if (!string.Equals(record.CaptureProfileId, profile.ProfileId, StringComparison.Ordinal))
            errors.Add("record_capture_profile_mismatch");
        string family = ResolveActionFamily(record.DecisionFamily, record.Action.Verb);
        if (!profile.SupportedActionFamilies.Contains(family, StringComparer.Ordinal))
            errors.Add("record_action_family_outside_profile");
        ValidateRequiredReads(profile, record.Pre.Reads, "pre", record.Pre.InteractionKind, errors);
        ValidateRequiredReads(
            profile,
            record.Successor.Reads,
            "successor",
            record.Successor.InteractionKind,
            errors);
        return new RecordValidationResult(errors.Count == 0, errors);
    }

    public static string ResolveActionFamily(string decisionFamily, string verb) =>
        decisionFamily == "ordinary_combat" && verb == "play"
            ? "ordinary_combat.play_card"
            : decisionFamily == "ordinary_combat" && verb == "use"
                ? "ordinary_combat.use_potion"
            : $"{decisionFamily}.{verb}";

    private static void ValidateRequiredReads(
        HumanCaptureProfile profile,
        IReadOnlyList<ReadEvidence> reads,
        string phase,
        string interactionKind,
        ICollection<string> errors)
    {
        HashSet<string> materialized = reads
            .Where(read => string.Equals(read.Status, "materialized", StringComparison.Ordinal))
            .Select(read => read.Kind)
            .ToHashSet(StringComparer.Ordinal);
        foreach (CaptureReadRequirement requirement in profile.Reads.Where(read =>
                     read.Required
                     && string.Equals(read.Phase, phase, StringComparison.Ordinal)
                     && (read.InteractionKind == null
                         || string.Equals(read.InteractionKind, interactionKind, StringComparison.Ordinal))))
        {
            if (!materialized.Contains(requirement.Kind))
                errors.Add($"{phase}_required_read_missing_{requirement.Kind}");
        }
    }
}

public static class CurrentDecisionRecordValidator
{
    public static RecordValidationResult Validate(CurrentDecisionRecord? record)
    {
        var errors = new List<string>();
        if (record == null)
            return new RecordValidationResult(false, new[] { "record_missing" });
        if (record.SchemaVersion != CurrentRecordingContract.SchemaVersion
            || record.Schema != CurrentRecordingContract.RecordSchema)
            errors.Add("schema_mismatch");
        if (string.IsNullOrWhiteSpace(record.RecordId)
            || string.IsNullOrWhiteSpace(record.SessionId)
            || string.IsNullOrWhiteSpace(record.RunId)
            || string.IsNullOrWhiteSpace(record.TimelineId)
            || string.IsNullOrWhiteSpace(record.CaptureProfileId)
            || record.Sequence <= 0)
            errors.Add("record_identity_incomplete");

        if (!IsSha256(record.Environment.Game.MainAssemblySha256)
            || !Guid.TryParse(record.Environment.Game.MainAssemblyModuleVersionId, out _))
            errors.Add("game_identity_incomplete");
        if (!IsExactArtifact(record.Environment.Connector)
            || !IsExactArtifact(record.Environment.Annotator))
            errors.Add("artifact_identity_incomplete");
        if (string.IsNullOrWhiteSpace(record.Environment.RuntimeInstanceId)
            || string.IsNullOrWhiteSpace(record.Environment.EnvironmentFingerprint)
            || string.IsNullOrWhiteSpace(record.Environment.ModsetFingerprint))
            errors.Add("runtime_identity_incomplete");
        if (!RecordingEnvironmentAdmission.IsExactModset(record.Environment.ModsetStatus))
            errors.Add("modset_not_exact_recording_envelope");
        if (record.Pre.Snapshot == null
            || string.IsNullOrWhiteSpace(record.Pre.SnapshotId)
            || record.Pre.CatalogCount <= 0
            || !IsSha256(record.Pre.CatalogDigest))
            errors.Add("pre_frame_not_complete");
        else
            ValidatePreFrame(record, errors);
        if (!string.Equals(record.Mapping.Status, "exact_unique", StringComparison.Ordinal)
            || record.Mapping.MatchCount != 1
            || !string.Equals(record.Mapping.Basis, "reference_equality_to_frozen_host_binding", StringComparison.Ordinal))
            errors.Add("mapping_not_exact_unique");
        if (string.IsNullOrWhiteSpace(record.Action.BoundActionId)
            || string.IsNullOrWhiteSpace(record.Action.Verb))
            errors.Add("action_incomplete");
        if (record.Successor.Snapshot == null
            || string.IsNullOrWhiteSpace(record.Successor.SnapshotId)
            || string.Equals(record.Pre.SnapshotId, record.Successor.SnapshotId, StringComparison.Ordinal)
            || !string.Equals(record.Successor.Status, "interactive", StringComparison.Ordinal))
            errors.Add("stable_successor_missing");
        else
            ValidateSuccessor(record, errors);
        if (string.IsNullOrWhiteSpace(record.NativeWitness.Origin)
            || string.IsNullOrWhiteSpace(record.NativeWitness.NativeActionType))
            errors.Add("native_witness_incomplete");
        if (!string.Equals(record.Eligibility.Status, "admitted", StringComparison.Ordinal))
            errors.Add("record_not_admitted");
        ValidateReads(record.Pre.Reads, "pre", record.Pre.SnapshotId, record.Environment, errors);
        ValidateReads(
            record.Successor.Reads,
            "successor",
            record.Successor.SnapshotId,
            record.Environment,
            errors);
        return new RecordValidationResult(errors.Count == 0, errors);
    }

    private static bool IsExactArtifact(ExactArtifactIdentity artifact) =>
        !string.IsNullOrWhiteSpace(artifact.Product)
        && !string.IsNullOrWhiteSpace(artifact.Version)
        && IsSha256(artifact.SourceDigestSha256)
        && IsSha256(artifact.Sha256)
        && Guid.TryParse(artifact.ModuleVersionId, out _)
        && artifact.SourceRevision.Length == 40
        && artifact.SourceRevision.All(Uri.IsHexDigit);

    private static void ValidatePreFrame(
        CurrentDecisionRecord record,
        ICollection<string> errors)
    {
        if (record.Pre.Snapshot is not JsonObject snapshot
            || ReadString(snapshot, "snapshot_id") != record.Pre.SnapshotId
            || ReadString(snapshot, "status") != "interactive"
            || snapshot["interaction"] is not JsonObject interaction
            || ReadString(interaction, "interaction_id") != record.Pre.InteractionId
            || ReadString(interaction, "kind") != record.Pre.InteractionKind
            || ReadString(interaction, "content_schema") != record.Pre.SurfaceSchema
            || snapshot["completeness"] is not JsonObject completeness
            || ReadString(completeness, "status") != "complete"
            || snapshot["bound_actions"] is not JsonObject catalog
            || ReadString(catalog, "status") != "complete"
            || catalog["actions"] is not JsonArray actions
            || actions.Count != record.Pre.CatalogCount
            || !string.Equals(
                EvidenceIdentity.Sha256Json(catalog),
                record.Pre.CatalogDigest,
                StringComparison.Ordinal))
        {
            errors.Add("pre_frame_evidence_mismatch");
            return;
        }

        JsonObject[] matches = actions
            .OfType<JsonObject>()
            .Where(action => ReadString(action, "bound_action_id") == record.Action.BoundActionId)
            .ToArray();
        if (matches.Length != 1 || !SameAction(matches[0], record.Action))
            errors.Add("chosen_action_not_exactly_once_in_catalog");
        if (snapshot["session"] is not JsonObject session
            || ReadString(session, "runtime_instance_id") != record.Environment.RuntimeInstanceId
            || ReadString(session, "environment_fingerprint") != record.Environment.EnvironmentFingerprint)
            errors.Add("pre_runtime_identity_mismatch");
    }

    private static void ValidateSuccessor(
        CurrentDecisionRecord record,
        ICollection<string> errors)
    {
        if (record.Successor.Snapshot is not JsonObject snapshot
            || ReadString(snapshot, "snapshot_id") != record.Successor.SnapshotId
            || ReadString(snapshot, "status") != record.Successor.Status
            || snapshot["interaction"] is not JsonObject interaction
            || ReadString(interaction, "interaction_id") != record.Successor.InteractionId
            || ReadString(interaction, "kind") != record.Successor.InteractionKind
            || snapshot["bound_actions"] is not JsonObject catalog
            || ReadString(catalog, "status") != "complete")
        {
            errors.Add("successor_evidence_mismatch");
            return;
        }

        if (snapshot["session"] is not JsonObject session
            || ReadString(session, "runtime_instance_id") != record.Environment.RuntimeInstanceId
            || ReadString(session, "environment_fingerprint") != record.Environment.EnvironmentFingerprint)
            errors.Add("successor_runtime_identity_mismatch");
    }

    private static bool SameAction(JsonObject value, RecordedBoundAction action)
    {
        if (ReadString(value, "verb") != action.Verb
            || ReadNullableString(value, "subject_referent_id") != action.SubjectReferentId
            || value["arguments"] is not JsonArray arguments)
            return false;
        var actual = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (JsonObject argument in arguments.OfType<JsonObject>())
        {
            string? role = ReadString(argument, "role");
            string? referentId = ReadString(argument, "referent_id");
            if (role == null || referentId == null || !actual.TryAdd(role, referentId))
                return false;
        }
        return actual.Count == arguments.Count
               && actual.Count == action.Arguments.Count
               && actual.All(pair => action.Arguments.TryGetValue(pair.Key, out string? value)
                                     && value == pair.Value);
    }

    private static string? ReadString(JsonObject value, string key)
    {
        try
        {
            return value[key]?.GetValue<string>();
        }
        catch (InvalidOperationException)
        {
            return null;
        }
    }

    private static string? ReadNullableString(JsonObject value, string key) =>
        value[key] == null ? null : ReadString(value, key);

    private static void ValidateReads(
        IReadOnlyList<ReadEvidence> reads,
        string phase,
        string snapshotId,
        RecorderEnvironmentIdentity environment,
        ICollection<string> errors)
    {
        if (reads.Count != reads.Select(read => read.Kind).Distinct(StringComparer.Ordinal).Count())
            errors.Add($"{phase}_read_kind_duplicate");
        if (reads.Count
            != reads.Select(read => read.ReadEvidenceId).Distinct(StringComparer.Ordinal).Count())
            errors.Add($"{phase}_read_evidence_id_duplicate");
        foreach (ReadEvidence read in reads)
        {
            if (read.SchemaVersion != CurrentRecordingContract.SchemaVersion
                || read.Schema != CurrentRecordingContract.ReadEvidenceSchema
                || string.IsNullOrWhiteSpace(read.ReadEvidenceId)
                || string.IsNullOrWhiteSpace(read.ReadId)
                || string.IsNullOrWhiteSpace(read.Kind))
                errors.Add($"{phase}_read_identity_invalid");
            if (read.SnapshotId != snapshotId
                || read.RuntimeInstanceId != environment.RuntimeInstanceId
                || read.EnvironmentFingerprint != environment.EnvironmentFingerprint)
                errors.Add($"{phase}_read_binding_mismatch");
            if (read.Status == "materialized")
            {
                if (string.IsNullOrWhiteSpace(read.ContentSchema)
                    || read.Completeness is not JsonObject
                    || !IsSha256(read.PayloadSha256)
                    || !IsSafePayloadRef(read.PayloadRef)
                    || read.ErrorCode != null)
                    errors.Add($"{phase}_read_materialization_invalid");
            }
            else if (read.Status is "not_available" or "failed" or "stale")
            {
                if (read.PayloadRef != null || read.PayloadSha256 != null
                    || string.IsNullOrWhiteSpace(read.ErrorCode))
                    errors.Add($"{phase}_read_failure_invalid");
            }
            else
            {
                errors.Add($"{phase}_read_status_invalid");
            }
        }
    }

    private static bool IsSafePayloadRef(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return false;
        string normalized = value.Replace('\\', '/');
        return normalized.StartsWith("blobs/sha256/", StringComparison.Ordinal)
               && !normalized.StartsWith("/", StringComparison.Ordinal)
               && !normalized.Split('/').Contains("..", StringComparer.Ordinal);
    }

    private static bool IsSha256(string? value) =>
        value is { Length: 64 } && value.All(Uri.IsHexDigit);
}
