using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

public static class P1NativeRecordingContract
{
    public const string ProfileId = "human-p1-native-decisions-1";
    public const string EventSchema = "sts2.human-annotator/p1-native-event-1";
    public const string EventFile = "p1-native-events.jsonl";
    public const string CloseFile = "p1-native-close.json";
    public static HumanCaptureProfile Profile { get; } = new(2, CurrentRecordingContract.CaptureProfileSchema,
        ProfileId, EventSchema, HumanCaptureProfiles.FullRunReadRich.SupportedActionFamilies,
        new[] { new CaptureReadRequirement("pre", "all_advertised_read_instances", true) },
        new[] { "p1_adapter_family_coverage_separate", "not_whole_run_qualified", "origin_is_declared_not_machine_proven" });
    public static bool CompleteBasis(ReadOnlyMemory<byte> bytes)
    {
        var value = JsonNode.Parse(bytes.Span)!.AsObject();
        return value["schema"]?.GetValue<string>() == "sts2.player-environment/public-basis-1"
            && value["capture_status"]?.GetValue<string>() == "complete"
            && value["missing"] is JsonArray missing && missing.Count == 0;
    }
}

public sealed record P1NativeBasisReference(string SnapshotId, string Sha256, long ByteCount, string PayloadRef);

public sealed partial class RecordingSessionStore
{
    private FileStream? _p1NativeEvents;
    private long _p1NativeSequence, _p1NativeAccepted, _p1NativeTerminal, _p1NativeIssues;
    private bool _p1NativeWriteFailed;
    private readonly HashSet<string> _p1NativeAcceptedIds = new(StringComparer.Ordinal);
    private readonly Dictionary<string, (string Decision, string Run)> _p1NativePending = new(StringComparer.Ordinal);
    public bool IsP1NativeSession => Manifest.P1NativeSchemaVersion == 1;

    public P1NativeBasisReference PersistP1NativeBasis(string snapshotId, ReadOnlyMemory<byte> bytes)
    {
        EnsureOpen();
        P1NativeBasisReference? result = null;
        P1NativeWrite(() => {
            using var parsed = JsonDocument.Parse(bytes);
            if (parsed.RootElement.GetProperty("schema").GetString() != "sts2.player-environment/public-basis-1"
                || parsed.RootElement.GetProperty("snapshot").GetProperty("snapshot_id").GetString() != snapshotId)
                throw new InvalidDataException("p1_native_original_basis_identity_mismatch");
            byte[] payload = bytes.ToArray();
            string digest = EvidenceIdentity.Sha256Bytes(payload);
            string relative = $"public-bases/sha256/{digest[..2]}/{digest}.json";
            string destination = ResolveRelative(relative);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            if (File.Exists(destination))
            {
                if (EvidenceIdentity.Sha256File(destination) != digest) throw new IOException("p1_native_basis_collision");
            }
            else { using var file = new FileStream(destination, FileMode.CreateNew, FileAccess.Write); file.Write(payload); file.Flush(flushToDisk: true); }
            result = new(snapshotId, digest, payload.LongLength, relative);
        });
        return result!;
    }

    // Same append lock, health and durable Close owner as every existing recording stream.
    public void AppendP1NativeEvent(JsonObject value)
    {
        EnsureOpen();
        P1NativeWrite(() => {
            var row = (JsonObject)value.DeepClone();
            string kind = row["kind"]?.GetValue<string>() ?? throw new InvalidDataException("p1_native_kind_missing");
            string? witness = row["action_witness_id"]?.GetValue<string>();
            if (kind == "decision_accepted" && (witness == null || _p1NativeAcceptedIds.Contains(witness)))
                throw new InvalidDataException("p1_native_duplicate_or_missing_accepted_identity");
            bool terminal = kind == "lifecycle" && row["phase"]?.GetValue<string>() is "finished" or "cancelled" or "aborted" or "unknown";
            if (terminal && (witness == null || !_p1NativePending.ContainsKey(witness)))
                throw new InvalidDataException("p1_native_terminal_without_accepted");
            row["schema"] = P1NativeRecordingContract.EventSchema;
            row["sequence"] = _p1NativeSequence + 1;
            row["session_id"] = Manifest.SessionId; row["timeline_id"] = Manifest.TimelineId;
            row["event_id"] = $"p1-native-{Guid.NewGuid():N}";
            row["recorded_at"] = DateTimeOffset.UtcNow;
            AppendBufferedLine(_p1NativeEvents!, row);
            _p1NativeSequence++;
            if (kind == "decision_accepted")
            {
                _p1NativeAccepted++; _admittedCount++; _p1NativeAcceptedIds.Add(witness!);
                _p1NativePending.Add(witness!, (row["decision_id"]!.GetValue<string>(), row["run_id"]!.GetValue<string>()));
            }
            if (kind == "issue") _p1NativeIssues++;
            if (terminal)
            {
                _p1NativePending.Remove(witness!);
                _p1NativeTerminal++;
            }
        });
    }

    private void P1NativeWrite(Action operation)
    {
        if (!IsP1NativeSession || _p1NativeEvents == null) throw new InvalidOperationException("p1_native_profile_required");
        try { ExecuteWrite(operation); }
        catch { _p1NativeWriteFailed = true; throw; }
    }

    private void PrepareP1NativeClose()
    {
        if (!IsP1NativeSession) return;
        if (_p1NativeWriteFailed) throw new InvalidDataException("p1_native_persistence_failed");
        foreach (var item in _p1NativePending.ToArray())
            AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["run_id"] = item.Value.Run,
                ["decision_id"] = item.Value.Decision, ["action_witness_id"] = item.Key,
                ["phase"] = "unknown", ["reason"] = "session_closed_before_native_terminal",
                ["successor_basis"] = null, ["successor_missing_reason"] = "native_terminal_unobserved" });
    }
    private void WriteP1NativeClose()
    {
        if (!IsP1NativeSession) return;
        WriteAtomic(Path.Combine(DirectoryPath, P1NativeRecordingContract.CloseFile), JsonSerializer.Serialize(new {
            schema = "sts2.human-annotator/p1-native-close-1", session_id = Manifest.SessionId, timeline_id = Manifest.TimelineId,
            status = "closed", event_count = _p1NativeSequence, accepted_count = _p1NativeAccepted,
            terminal_count = _p1NativeTerminal, pending_count = _p1NativePending.Count, issue_count = _p1NativeIssues,
            events_sha256 = EvidenceIdentity.Sha256File(Path.Combine(DirectoryPath, P1NativeRecordingContract.EventFile)),
            run_journal_sha256 = EvidenceIdentity.Sha256File(Path.Combine(DirectoryPath, "run-journal.jsonl")),
            declared_origin = Manifest.P1NativeOrigin, non_claims = P1NativeRecordingContract.Profile.NonClaims
        }, EvidenceJson.IndentedOptions));
    }
}
