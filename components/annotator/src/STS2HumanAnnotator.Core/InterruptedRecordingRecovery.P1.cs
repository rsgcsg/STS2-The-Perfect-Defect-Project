using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

public static partial class InterruptedRecordingRecovery
{
    private static readonly HashSet<string> P1Terminals = new(StringComparer.Ordinal)
        { "finished", "cancelled", "aborted", "unknown" };

    // Reuse the existing lease/inventory/staging owner. This is an offline
    // unknown closure, never a synthetic native completion or game continuation.
    private static int RecoverP1Copy(string directory, CurrentRecordingManifest manifest, DateTimeOffset now)
    {
        if (File.Exists(Path.Combine(directory, P1NativeRecordingContract.CloseFile)))
            throw new InvalidDataException("Ambiguous interrupted P1 close.");
        var rows = P1Rows(directory, manifest);
        var pending = P1Pending(rows);
        long sequence = rows.LongLength;
        foreach (var accepted in pending)
            Append(Path.Combine(directory, P1NativeRecordingContract.EventFile), new JsonObject {
                ["schema"] = P1NativeRecordingContract.EventSchema, ["sequence"] = ++sequence,
                ["session_id"] = manifest.SessionId, ["timeline_id"] = manifest.TimelineId,
                ["event_id"] = "recovery-p1-" + Guid.NewGuid().ToString("N"), ["recorded_at"] = now,
                ["kind"] = "lifecycle", ["run_id"] = accepted["run_id"]!.DeepClone(),
                ["decision_id"] = accepted["decision_id"]!.DeepClone(),
                ["action_witness_id"] = accepted["action_witness_id"]!.DeepClone(),
                ["phase"] = "unknown", ["reason"] = "process_interrupted",
                ["successor_basis"] = null, ["successor_missing_reason"] = "native_terminal_unobserved"
            });
        return pending.Length;
    }

    private static void WriteP1RecoveryClose(string directory, CurrentRecordingManifest manifest)
    {
        var rows = P1Rows(directory, manifest);
        DurableJson(Path.Combine(directory, P1NativeRecordingContract.CloseFile), new {
            schema = "sts2.human-annotator/p1-native-close-1", session_id = manifest.SessionId,
            timeline_id = manifest.TimelineId, status = "closed", event_count = rows.LongLength,
            accepted_count = rows.Count(e => e["kind"]!.GetValue<string>() == "decision_accepted"),
            terminal_count = rows.Count(e => e["kind"]!.GetValue<string>() == "lifecycle"
                && P1Terminals.Contains(e["phase"]!.GetValue<string>())),
            pending_count = P1Pending(rows).Length, issue_count = rows.Count(e => e["kind"]!.GetValue<string>() == "issue"),
            events_sha256 = EvidenceIdentity.Sha256File(Path.Combine(directory, P1NativeRecordingContract.EventFile)),
            run_journal_sha256 = EvidenceIdentity.Sha256File(Path.Combine(directory, "run-journal.jsonl")),
            declared_origin = manifest.P1NativeOrigin, non_claims = P1NativeRecordingContract.Profile.NonClaims
        });
    }

    private static JsonObject[] P1Rows(string directory, CurrentRecordingManifest manifest)
    {
        string path = Path.Combine(directory, P1NativeRecordingContract.EventFile);
        byte[] bytes = File.ReadAllBytes(path);
        if (bytes.Length > 0 && bytes[^1] != 10) throw new InvalidDataException("Interrupted partial P1 line.");
        string content = new UTF8Encoding(false, true).GetString(bytes);
        var rows = content.Length == 0 ? Array.Empty<JsonObject>() : content[..^1].Split('\n')
            .Select(line => JsonNode.Parse(line)?.AsObject() ?? throw new InvalidDataException("Invalid P1 row.")).ToArray();
        var ids = new HashSet<string>(StringComparer.Ordinal);
        for (int i = 0; i < rows.Length; i++)
        {
            var row = rows[i];
            if (row["schema"]?.GetValue<string>() != P1NativeRecordingContract.EventSchema
                || row["sequence"]?.GetValue<long>() != i + 1
                || row["session_id"]?.GetValue<string>() != manifest.SessionId
                || row["timeline_id"]?.GetValue<string>() != manifest.TimelineId
                || row["event_id"]?.GetValue<string>() is not { Length: > 0 } id || !ids.Add(id))
                throw new InvalidDataException("Interrupted P1 identity or order.");
            foreach (string role in new[] { "basis", "successor_basis" }) if (row[role] is JsonNode basis)
            {
                var reference = basis.Deserialize<P1NativeBasisReference>(EvidenceJson.Options)
                    ?? throw new InvalidDataException("Invalid P1 basis reference.");
                if (reference.Sha256 is not { Length: 64 } || !reference.Sha256.All(Uri.IsHexDigit))
                    throw new InvalidDataException("Invalid P1 basis reference.");
                string relative = $"public-bases/sha256/{reference.Sha256[..2]}/{reference.Sha256}.json";
                if (reference.PayloadRef != relative) throw new InvalidDataException("Invalid P1 basis reference.");
                string original = Path.Combine(directory, relative);
                if (new FileInfo(original).Length != reference.ByteCount || EvidenceIdentity.Sha256File(original) != reference.Sha256
                    || JsonNode.Parse(File.ReadAllText(original))!["snapshot"]!["snapshot_id"]?.GetValue<string>() != reference.SnapshotId)
                    throw new InvalidDataException("Interrupted P1 basis changed.");
            }
        }
        _ = P1Pending(rows); return rows;
    }

    private static JsonObject[] P1Pending(JsonObject[] rows)
    {
        var accepted = new HashSet<string>(StringComparer.Ordinal);
        var decisions = new HashSet<string>(StringComparer.Ordinal);
        var pending = new Dictionary<string, JsonObject>(StringComparer.Ordinal);
        foreach (var row in rows)
        {
            string kind = row["kind"]?.GetValue<string>() ?? throw new InvalidDataException("P1 event kind missing.");
            if (kind == "decision_accepted")
            {
                string witness = row["action_witness_id"]?.GetValue<string>() ?? "", decision = row["decision_id"]?.GetValue<string>() ?? "";
                if (string.IsNullOrEmpty(witness) || string.IsNullOrEmpty(decision) || !accepted.Add(witness)
                    || !decisions.Add(decision) || row["run_id"]?.GetValue<string>() is not { Length: > 0 })
                    throw new InvalidDataException("Interrupted P1 duplicate acceptance.");
                pending.Add(witness, row);
            }
            else if (kind is "lifecycle" or "execution")
            {
                string witness = row["action_witness_id"]?.GetValue<string>() ?? "";
                if (!pending.TryGetValue(witness, out var original)
                    || row["decision_id"]?.GetValue<string>() != original["decision_id"]!.GetValue<string>()
                    || row["run_id"]?.GetValue<string>() != original["run_id"]!.GetValue<string>())
                    throw new InvalidDataException("Interrupted P1 lifecycle binding.");
                if (kind == "lifecycle" && P1Terminals.Contains(row["phase"]?.GetValue<string>() ?? "")) pending.Remove(witness);
            }
            else if (kind is not ("observation" or "issue")) throw new InvalidDataException("Interrupted P1 unknown event.");
        }
        return pending.Values.ToArray();
    }

    private static void ValidateP1Suffix(string directory, CurrentRecordingManifest manifest,
        SortedDictionary<string, OriginalFile> inventory, JsonElement receipt)
    {
        if (inventory.ContainsKey(P1NativeRecordingContract.CloseFile))
            throw new InvalidDataException("Original interrupted P1 already closed.");
        var rows = P1Rows(directory, manifest);
        var suffix = Suffix<JsonObject>(directory, P1NativeRecordingContract.EventFile, inventory);
        var pending = P1Pending(rows.Take(rows.Length - suffix.Length).ToArray());
        var fields = new[] { "schema", "sequence", "session_id", "timeline_id", "event_id", "recorded_at", "kind",
            "run_id", "decision_id", "action_witness_id", "phase", "reason", "successor_basis", "successor_missing_reason" };
        if (suffix.Length != pending.Length || receipt.GetProperty("p1_unknowns_added").GetInt32() != pending.Length
            || suffix.Any(row => !row.Select(p => p.Key).ToHashSet().SetEquals(fields)
                || row["kind"]?.GetValue<string>() != "lifecycle" || row["phase"]?.GetValue<string>() != "unknown"
                || row["reason"]?.GetValue<string>() != "process_interrupted" || row["successor_basis"] != null
                || row["successor_missing_reason"]?.GetValue<string>() != "native_terminal_unobserved"
                || !pending.Any(original => original["action_witness_id"]!.GetValue<string>() == row["action_witness_id"]!.GetValue<string>())))
            throw new InvalidDataException("P1 recovery suffix differs from unknown-only closure.");
    }
}
