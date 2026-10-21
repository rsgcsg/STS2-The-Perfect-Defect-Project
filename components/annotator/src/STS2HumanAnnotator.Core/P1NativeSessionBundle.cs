using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

public sealed record P1NativeAuditResult(string Status, IReadOnlyList<string> Errors, long AcceptedCount, long IssueCount);

public static class P1NativeSessionBundlePacker
{
    public const string Schema = "sts2.human-annotator/p1-native-session-bundle-1";
    public static P1NativeAuditResult Audit(string directory)
    {
        var errors = new List<string>();
        var manifest = JsonSerializer.Deserialize<CurrentRecordingManifest>(File.ReadAllText(Path.Combine(directory, "recording-manifest.json")), EvidenceJson.Options)!;
        if (manifest.P1NativeSchemaVersion != 1 || manifest.CaptureProfileId != P1NativeRecordingContract.ProfileId)
            errors.Add("p1_native_manifest_mismatch");
        try { InterruptedRecordingRecovery.Validate(directory, manifest); }
        catch (Exception exception) when (exception is IOException or InvalidDataException or JsonException or InvalidOperationException
            or ArgumentException or KeyNotFoundException or UnauthorizedAccessException)
        { errors.Add("recording_recovery_invalid"); }
        var close = JsonNode.Parse(File.ReadAllText(Path.Combine(directory, P1NativeRecordingContract.CloseFile)))!;
        if (close["status"]?.GetValue<string>() != "closed" || close["pending_count"]?.GetValue<long>() != 0
            || close["session_id"]?.GetValue<string>() != manifest.SessionId || close["timeline_id"]?.GetValue<string>() != manifest.TimelineId)
            errors.Add("p1_native_close_mismatch");
        foreach (var pair in new[] { (P1NativeRecordingContract.EventFile, "events_sha256"), ("run-journal.jsonl", "run_journal_sha256") })
            if (EvidenceIdentity.Sha256File(Path.Combine(directory, pair.Item1)) != close[pair.Item2]?.GetValue<string>())
                errors.Add("p1_native_stream_hash_mismatch");
        if (!File.Exists(Path.Combine(directory, "session-close-receipt.json"))) errors.Add("recording_close_missing");
        long sequence = 0, accepted = 0, issues = 0;
        foreach (string line in File.ReadLines(Path.Combine(directory, P1NativeRecordingContract.EventFile)))
        {
            var row = JsonNode.Parse(line)!;
            if (row["schema"]?.GetValue<string>() != P1NativeRecordingContract.EventSchema
                || row["sequence"]?.GetValue<long>() != ++sequence || row["session_id"]?.GetValue<string>() != manifest.SessionId
                || row["timeline_id"]?.GetValue<string>() != manifest.TimelineId) errors.Add("p1_native_event_identity_or_order");
            if (row["kind"]?.GetValue<string>() == "decision_accepted") accepted++;
            if (row["kind"]?.GetValue<string>() == "issue") issues++;
        }
        if (sequence != close["event_count"]?.GetValue<long>() || accepted != close["accepted_count"]?.GetValue<long>()
            || issues != close["issue_count"]?.GetValue<long>()) errors.Add("p1_native_close_count_mismatch");
        return new(errors.Count == 0 ? "pass" : "fail", errors, accepted, issues);
    }

    public static CanonicalSessionBundleResult Pack(string recordingDirectory, string workerId, string campaignId,
        string outputDirectory, string packerSourceRevision, bool humanOriginAttested = false)
    {
        string source = Path.GetFullPath(recordingDirectory), destination = Path.GetFullPath(outputDirectory);
        SessionBundlePacker.ValidateIdentifier(workerId, nameof(workerId)); SessionBundlePacker.ValidateIdentifier(campaignId, nameof(campaignId));
        if (packerSourceRevision.Length != 40 || !packerSourceRevision.All(Uri.IsHexDigit)) throw new InvalidDataException("exact_packer_source_required");
        if (destination == source || destination.StartsWith(source + Path.DirectorySeparatorChar, StringComparison.Ordinal))
            throw new InvalidDataException("p1_native_bundle_must_not_modify_source");
        var audit = Audit(source);
        if (audit.Status != "pass") throw new InvalidDataException(string.Join(",", audit.Errors));
        var manifest = JsonSerializer.Deserialize<CurrentRecordingManifest>(File.ReadAllText(Path.Combine(source, "recording-manifest.json")), EvidenceJson.Options)!;
        if (humanOriginAttested && manifest.P1NativeOrigin != "declared_human") throw new InvalidDataException("p1_native_engineering_record_is_not_human");
        Directory.CreateDirectory(destination);
        string temporary = Path.Combine(destination, $".p1-native-pack-{Guid.NewGuid():N}");
        Directory.CreateDirectory(temporary);
        try
        {
            string raw = Path.Combine(temporary, "raw"); SessionBundlePacker.CopyDirectory(source, raw);
            var identity = new JsonObject { ["schema"] = Schema, ["session_id"] = manifest.SessionId, ["timeline_id"] = manifest.TimelineId,
                ["capture_profile_id"] = manifest.CaptureProfileId, ["declared_origin"] = manifest.P1NativeOrigin,
                ["worker_id"] = workerId, ["campaign_id"] = campaignId, ["packer_source_revision"] = packerSourceRevision,
                ["human_origin_attestation"] = new JsonObject { ["attested"] = humanOriginAttested,
                    ["method"] = "explicit_owner_pack", ["machine_verifiable"] = false },
                ["raw_file_sha256"] = SessionBundlePacker.RecursiveChecksums(raw) };
            byte[] bytes = Encoding.UTF8.GetBytes(EvidenceCanonicalJson.Serialize(identity));
            string content = EvidenceIdentity.Sha256Bytes(bytes);
            File.WriteAllBytes(Path.Combine(temporary, "content-identity.json"), bytes);
            File.WriteAllText(Path.Combine(temporary, "session-bundle-manifest.json"), JsonSerializer.Serialize(new {
                schema = Schema, bundle_content_id = content, content_identity = identity }, EvidenceJson.IndentedOptions));
            SessionBundlePacker.WriteChecksums(temporary);
            string final = Path.Combine(destination, content); Directory.Move(temporary, final);
            return new("packed", final, content, manifest.SessionId, audit.AcceptedCount,
                EvidenceIdentity.Sha256File(Path.Combine(final, "raw", P1NativeRecordingContract.EventFile)),
                EvidenceIdentity.Sha256File(Path.Combine(final, "checksums.sha256")));
        }
        finally { if (Directory.Exists(temporary)) Directory.Delete(temporary, true); }
    }
}
