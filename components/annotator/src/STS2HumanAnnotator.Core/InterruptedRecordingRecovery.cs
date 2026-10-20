using System.Text;
using System.Security.Cryptography;
using System.Text.Json;

namespace STS2HumanAnnotator.Core;

/// <summary>
/// Recover only recorder-owned, unlocked current sessions into a separate immutable
/// copy. Original bytes never change; absent successors become explicit unknowns.
/// A corrupt/torn stream remains an incident, never trimmed into successful evidence.
/// </summary>
public static partial class InterruptedRecordingRecovery
{
    public sealed record OriginalFile(long Bytes, string Sha256);
    public const string Schema = "sts2.human-annotator/interrupted-recovery-1";
    private static readonly HashSet<string> TerminalKinds = new(StringComparer.Ordinal)
    {
        "transition_proved", "transition_unknown", "action_cancelled_before_start",
        "action_cancelled_after_start", "action_aborted_before_commit"
    };

    public static object RecoverOne(string recordingRoot, string recoveryRoot)
    {
        string root = Path.GetFullPath(recordingRoot), output = Path.GetFullPath(recoveryRoot);
        if (output == root || output.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.Ordinal)
            || root.StartsWith(output + Path.DirectorySeparatorChar, StringComparison.Ordinal))
            throw new InvalidDataException("Recovery must use a separate directory.");
        RejectLink(root);
        Directory.CreateDirectory(output);
        RejectLink(output);
        int busy = 0, incidents = 0;
        foreach (string source in Directory.GetDirectories(root).Order(StringComparer.Ordinal))
        {
            RejectLink(source);
            string manifestFile = Path.Combine(source, "recording-manifest.json");
            if (!File.Exists(manifestFile) || File.Exists(Path.Combine(source, "session-close-receipt.json")))
                continue;
            CurrentRecordingManifest manifest;
            try { manifest = Read<CurrentRecordingManifest>(manifestFile); }
            catch (Exception exception) when (exception is IOException or InvalidDataException or JsonException)
            { incidents++; continue; }
            if (manifest.RecoverySchemaVersion != 1 || manifest.CloseSchemaVersion != 1
                || manifest.Schema != CurrentRecordingContract.ManifestSchema)
                continue; // Historical sessions have no safe ownership lease.
            string lease = Path.Combine(source, "recording-owner.lock");
            if (!File.Exists(lease)) continue;
            RejectLink(lease);
            FileStream owner;
            try { owner = new FileStream(lease, FileMode.Open, FileAccess.ReadWrite, FileShare.None); }
            catch (IOException) { busy++; continue; }
            using (owner)
            {
                SortedDictionary<string, OriginalFile> inventory;
                try { inventory = Inventory(source); }
                catch (Exception exception) when (exception is IOException or InvalidDataException or UnauthorizedAccessException)
                { incidents++; continue; }
                string identity = EvidenceIdentity.Sha256Text(EvidenceCanonicalJson.Serialize(
                    JsonSerializer.SerializeToNode(inventory, EvidenceJson.Options)!));
                string destination = Path.Combine(output, "recovered-" + identity);
                if (Directory.Exists(destination)) continue;
                string incident = Path.Combine(output, "incident-" + identity + ".json");
                if (File.Exists(incident)) continue;
                string stage = Path.Combine(output, ".recovering-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(stage);
                try
                {
                    foreach (string relative in inventory.Keys)
                    {
                        string target = Path.Combine(stage, relative);
                        Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                        using var input = File.OpenRead(Path.Combine(source, relative));
                        using var copied = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                        input.CopyTo(copied);
                        copied.Flush(true);
                    }
                    RecoverCopy(stage, manifest, identity, inventory);
                    if (!inventory.SequenceEqual(Inventory(source)))
                        throw new InvalidDataException("Recovery source changed under lease.");
                    string auditStatus = manifest.P1NativeSchemaVersion == 1
                        ? P1NativeSessionBundlePacker.Audit(stage).Status : RecordingSessionAuditor.Audit(stage).Status;
                    if (auditStatus != "pass")
                        throw new InvalidDataException("Recovered copy did not pass the recording audit.");
                    Directory.Move(stage, destination);
                    return new { schema = Schema, status = "recovered", identity, busy };
                }
                catch (Exception exception) when (exception is IOException or InvalidDataException or JsonException or InvalidOperationException or ArgumentException or UnauthorizedAccessException)
                {
                    // Preserve the original and staged diagnostic copy; no repeated repair
                    // attempts for the same immutable inventory, no private exception text.
                    DurableJson(incident, new { schema = Schema, status = "incident", identity,
                        reason = "interrupted_recording_requires_review" });
                    return new { schema = Schema, status = "incident", identity, busy };
                }
            }
        }
        return new { schema = Schema, status = incidents == 0 ? "idle" : "incident", busy, incidents };
    }

    private static void RecoverCopy(string directory, CurrentRecordingManifest manifest,
        string identity, SortedDictionary<string, OriginalFile> inventory)
    {
        string journalFile = Path.Combine(directory, "run-journal.jsonl");
        var journal = Rows<RunJournalEvent>(journalFile);
        if (journal.Length == 0 || journal.Any(e => e.Kind == "session_closed"))
            throw new InvalidDataException("Ambiguous interrupted close.");
        string traceFile = Path.Combine(directory, "semantic-boundary-trace.jsonl");
        var trace = Rows<SemanticEvidenceEvent>(traceFile);
        if (trace.Any(e => e.Schema != SemanticEvidenceContract.EventSchema
            || e.SessionId != manifest.SessionId || e.TimelineId != manifest.TimelineId))
            throw new InvalidDataException("Unsupported interrupted trace.");
        if (manifest.P1NativeSchemaVersion == 1 && trace.Length != 0)
            throw new InvalidDataException("P1 recovery cannot repair mixed semantic streams.");
        var accepted = trace.Where(e => e.Kind == "action_accepted")
            .ToDictionary(e => e.Action.ActionWitnessId, StringComparer.Ordinal);
        var terminals = trace.Where(e => TerminalKinds.Contains(e.Kind))
            .Select(e => e.Action.ActionWitnessId).ToHashSet(StringComparer.Ordinal);
        long sequence = trace.Select(e => e.Sequence).DefaultIfEmpty().Max();
        DateTimeOffset now = DateTimeOffset.UtcNow;
        foreach (var entry in accepted.Values.Where(e => !terminals.Contains(e.Action.ActionWitnessId)))
            Append(traceFile, entry with {
                EventId = "recovery-" + Guid.NewGuid().ToString("N"), Sequence = ++sequence,
                ObservedAt = now, Kind = "transition_unknown", ProofStatus = "process_interrupted",
                Boundary = null, SuccessorRef = null, NativeCompletion = null,
                NativeContinuation = null, NativeHumanContinuation = null,
                Detail = "Original process ended before a durable terminal disposition.",
                NonClaims = new[] { "no_semantic_successor", "offline_recovery_not_native_event" }
            });
        int p1Unknowns = manifest.P1NativeSchemaVersion == 1 ? RecoverP1Copy(directory, manifest, now) : 0;
        long journalSequence = journal.Max(e => e.Sequence);
        var last = journal[^1];
        Append(journalFile, last with { EventId = "recovery-" + identity, Sequence = ++journalSequence,
            RecordedAt = now, Kind = "recording_interrupted", RecordId = null, SnapshotId = null,
            Detail = "Offline recovery of immutable source inventory " + identity });
        Append(journalFile, last with { EventId = "recovery-close-" + identity, Sequence = ++journalSequence,
            RecordedAt = now, Kind = "session_closed", RecordId = null, SnapshotId = null,
            Detail = "Recovered copy sealed; original process did not close normally." });
        DurableJson(Path.Combine(directory, "recording-recovery.json"), new {
            schema = Schema, original_inventory_sha256 = identity, original_files = inventory,
            disposition = "interrupted_partial", unknowns_added = accepted.Keys.Count(k => !terminals.Contains(k)),
            p1_unknowns_added = p1Unknowns
        });
        if (manifest.P1NativeSchemaVersion == 1) WriteP1RecoveryClose(directory, manifest);
        DurableJson(Path.Combine(directory, "session-close-receipt.json"), new {
            schema = "sts2.human-annotator/session-close-1", session_id = manifest.SessionId,
            timeline_id = manifest.TimelineId, closed_at = now, status = "closed", recovery = Schema
        });
    }

    internal static SortedDictionary<string, OriginalFile> Inventory(string directory)
    {
        var result = new SortedDictionary<string, OriginalFile>(StringComparer.Ordinal);
        var pending = new Stack<(string Path, int Depth)>();
        pending.Push((directory, 0));
        long bytes = 0;
        int entries = 0;
        while (pending.TryPop(out var item))
        {
            if (item.Depth > 16) throw new InvalidDataException("Recovery depth limit.");
            foreach (string path in Directory.EnumerateFileSystemEntries(item.Path))
            {
                if (++entries > 50000) throw new InvalidDataException("Recovery inventory limit.");
                RejectLink(path);
                if (Directory.Exists(path)) pending.Push((path, item.Depth + 1));
                else if (path != Path.Combine(directory, "recording-owner.lock"))
                {
                    long length = new FileInfo(path).Length;
                    bytes += length;
                    if (bytes > 1024L * 1024 * 1024) throw new InvalidDataException("Recovery size limit.");
                    result.Add(Path.GetRelativePath(directory, path).Replace('\\', '/'),
                        new OriginalFile(length, EvidenceIdentity.Sha256File(path)));
                }
            }
        }
        return result;
    }

    // Called by the normal auditor as well as by recovery: a recovery marker
    // cannot turn modified original bytes or fabricated successes into evidence.
    internal static void Validate(string directory, CurrentRecordingManifest manifest)
    {
        if (manifest.RecoverySchemaVersion is not (null or 1))
            throw new InvalidDataException("Unknown recovery schema.");
        string path = Path.Combine(directory, "recording-recovery.json");
        var journal = Rows<RunJournalEvent>(Path.Combine(directory, "run-journal.jsonl"));
        var trace = Rows<SemanticEvidenceEvent>(Path.Combine(directory, "semantic-boundary-trace.jsonl"));
        bool marker = journal.Any(e => e.Kind == "recording_interrupted")
            || trace.Any(e => e.ProofStatus == "process_interrupted");
        if (!File.Exists(path))
        {
            if (marker) throw new InvalidDataException("Missing recovery receipt.");
            return;
        }
        if (manifest.RecoverySchemaVersion != 1 || manifest.CloseSchemaVersion != 1)
            throw new InvalidDataException("Recovery not declared.");
        var receipt = Read<JsonElement>(path);
        var inventory = receipt.GetProperty("original_files").Deserialize<SortedDictionary<string, OriginalFile>>(EvidenceJson.Options)!;
        string identity = EvidenceIdentity.Sha256Text(EvidenceCanonicalJson.Serialize(
            JsonSerializer.SerializeToNode(inventory, EvidenceJson.Options)!));
        if (receipt.GetProperty("schema").GetString() != Schema
            || receipt.GetProperty("disposition").GetString() != "interrupted_partial"
            || receipt.GetProperty("original_inventory_sha256").GetString() != identity
            || inventory.ContainsKey("session-close-receipt.json") || inventory.ContainsKey("recording-recovery.json"))
            throw new InvalidDataException("Invalid recovery receipt.");
        var observed = Inventory(directory);
        if (!observed.Keys.ToHashSet(StringComparer.Ordinal).SetEquals(
            inventory.Keys.Concat(new[] { "recording-recovery.json", "session-close-receipt.json" })
                .Concat(manifest.P1NativeSchemaVersion == 1 ? new[] { P1NativeRecordingContract.CloseFile } : Array.Empty<string>())))
            throw new InvalidDataException("Recovery inventory membership differs.");
        foreach (var (relative, original) in inventory)
        {
            if (!observed.TryGetValue(relative, out var current) || original.Bytes < 0 || current.Bytes < original.Bytes)
                throw new InvalidDataException("Recovery original missing.");
            using var input = File.OpenRead(Path.Combine(directory, relative));
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            byte[] buffer = new byte[65536];
            long remaining = original.Bytes;
            while (remaining > 0)
            {
                int read = input.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                if (read == 0) throw new InvalidDataException("Recovery original truncated.");
                hash.AppendData(buffer, 0, read); remaining -= read;
            }
            if (Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant() != original.Sha256
                || (relative is not ("run-journal.jsonl" or "semantic-boundary-trace.jsonl")
                    && !(manifest.P1NativeSchemaVersion == 1 && relative == P1NativeRecordingContract.EventFile)
                    && current.Bytes != original.Bytes))
                throw new InvalidDataException("Recovery original changed.");
        }
        var addedTrace = Suffix<SemanticEvidenceEvent>(directory, "semantic-boundary-trace.jsonl", inventory);
        var addedJournal = Suffix<RunJournalEvent>(directory, "run-journal.jsonl", inventory);
        var originalTrace = trace.Take(trace.Length - addedTrace.Length).ToArray();
        var pending = originalTrace.Where(e => e.Kind == "action_accepted" && !originalTrace.Any(t =>
            TerminalKinds.Contains(t.Kind) && t.Action.ActionWitnessId == e.Action.ActionWitnessId)).ToArray();
        if (addedTrace.Length != pending.Length || receipt.GetProperty("unknowns_added").GetInt32() != pending.Length
            || addedTrace.Select(e => e.Action.ActionWitnessId).Distinct().Count() != pending.Length
            || addedTrace.Any(e => e.Kind != "transition_unknown" || e.ProofStatus != "process_interrupted"
                || e.Boundary != null || e.SuccessorRef != null || e.NativeCompletion != null
                || e.NativeContinuation != null || e.NativeHumanContinuation != null
                || !pending.Any(p => EvidenceIdentity.Sha256Json(p.Action) == EvidenceIdentity.Sha256Json(e.Action)
                    && p.HumanObservationRef == e.HumanObservationRef
                    && p.ExecutionPreRef == e.ExecutionPreRef && p.ExecutionSemanticActionSpaceRef == e.ExecutionSemanticActionSpaceRef
                    && p.RelatedActionWitnessId == e.RelatedActionWitnessId))
            || addedJournal.Length != 2 || addedJournal[0].Kind != "recording_interrupted"
            || addedJournal[1].Kind != "session_closed" || journal.Count(e => e.Kind == "session_closed") != 1
            || addedJournal[0].Detail != "Offline recovery of immutable source inventory " + identity)
            throw new InvalidDataException("Recovery suffix differs from unknown-only closure.");
        if (manifest.P1NativeSchemaVersion == 1) ValidateP1Suffix(directory, manifest, inventory, receipt);
        var close = Read<JsonElement>(Path.Combine(directory, "session-close-receipt.json"));
        if (close.GetProperty("recovery").GetString() != Schema)
            throw new InvalidDataException("Recovery close marker missing.");
    }

    private static T[] Suffix<T>(string directory, string name, SortedDictionary<string, OriginalFile> inventory)
    {
        using var input = File.OpenRead(Path.Combine(directory, name));
        long offset = inventory[name].Bytes;
        if (offset > 0)
        {
            input.Position = offset - 1;
            if (input.ReadByte() != 10) throw new InvalidDataException("Interrupted partial line.");
        }
        input.Position = offset;
        using var reader = new StreamReader(input, Encoding.UTF8, false);
        var result = new List<T>();
        while (reader.ReadLine() is { } line)
            result.Add(JsonSerializer.Deserialize<T>(line, EvidenceJson.Options) ?? throw new InvalidDataException("Invalid recovery suffix."));
        return result.ToArray();
    }

    private static void RejectLink(string path)
    {
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("Recovery does not follow links.");
    }
    private static T Read<T>(string path) => JsonSerializer.Deserialize<T>(File.ReadAllText(path), EvidenceJson.Options)
        ?? throw new InvalidDataException("Invalid recovery input.");
    private static T[] Rows<T>(string path) => File.ReadLines(path).Where(s => !string.IsNullOrWhiteSpace(s))
        .Select(s => JsonSerializer.Deserialize<T>(s, EvidenceJson.Options) ?? throw new InvalidDataException("Invalid stream.")).ToArray();
    private static void Append<T>(string path, T value)
    {
        using var file = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.None);
        byte[] bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value, EvidenceJson.Options) + "\n");
        file.Write(bytes); file.Flush(true);
    }
    private static void DurableJson<T>(string path, T value)
    {
        using var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        byte[] bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value, EvidenceJson.IndentedOptions));
        file.Write(bytes); file.Flush(true);
    }
}
