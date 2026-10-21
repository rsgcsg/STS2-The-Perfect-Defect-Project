using System.Text.Json;

namespace STS2HumanAnnotator.Core;

public sealed record P1NativePrefixWatermark(long EventCount, long EventsByteCount, string EventsSha256,
    long JournalByteCount, string JournalSha256, long AcceptedCount, long TerminalCount, long PendingCount, long IssueCount);
public sealed record P1NativePrefixResult(string PrefixId, string PrefixDirectory, string ManifestPath,
    string ManifestSha256, P1NativePrefixWatermark Watermark);

public sealed partial class RecordingSessionStore
{
    public const string P1NativePrefixSchema = "sts2.human-annotator/p1-native-prefix-1";
    public const string P1NativePrefixManifestFile = "p1-native-prefix-manifest.json";

    /// <summary>Freeze original bytes at the committed Store watermark without closing or settling pending decisions.</summary>
    public P1NativePrefixResult ExportP1NativePrefix()
    {
        lock (_gate)
        {
            EnsureOpen();
            if (!IsP1NativeSession || _p1NativeEvents == null) throw new InvalidOperationException("p1_native_profile_required");
            if (_p1NativeWriteFailed) throw new InvalidDataException("p1_native_persistence_failed");
            try { SealP1NativeBases(); }
            catch (Exception error) { MarkWriteFailureUnsafe(error); throw; }
            _p1NativeEvents.Flush(flushToDisk: true);
            _journal.Flush(flushToDisk: true);
            string id = $"p1-prefix-{Guid.NewGuid():N}";
            string parent = Path.Combine(Path.GetDirectoryName(DirectoryPath)!, "p1-native-prefixes");
            Directory.CreateDirectory(parent);
            string temporary = Path.Combine(parent, "." + id), destination = Path.Combine(parent, id);
            string raw = Path.Combine(temporary, "raw");
            Directory.CreateDirectory(raw);
            try
            {
                var files = new[] { "recording-manifest.json", "capture-profile.json", P1NativeRecordingContract.EventFile, "run-journal.jsonl" }
                    .Select(name => Path.Combine(DirectoryPath, name));
                string bases = Path.Combine(DirectoryPath, "public-bases");
                if (Directory.Exists(bases)) files = files.Concat(Directory.EnumerateFiles(bases, "*", SearchOption.AllDirectories));
                foreach (string file in files)
                {
                    string output = Path.Combine(raw, Path.GetRelativePath(DirectoryPath, file));
                    Directory.CreateDirectory(Path.GetDirectoryName(output)!);
                    using var input = File.OpenRead(file);
                    using var copied = new FileStream(output, FileMode.CreateNew, FileAccess.Write, FileShare.Read);
                    input.CopyTo(copied); copied.Flush(flushToDisk: true);
                }
                string events = Path.Combine(raw, P1NativeRecordingContract.EventFile), journal = Path.Combine(raw, "run-journal.jsonl");
                var watermark = new P1NativePrefixWatermark(_p1NativeSequence, new FileInfo(events).Length,
                    EvidenceIdentity.Sha256File(events), new FileInfo(journal).Length, EvidenceIdentity.Sha256File(journal),
                    _p1NativeAccepted, _p1NativeTerminal, _p1NativePending.Count, _p1NativeIssues);
                WriteCreateNew(Path.Combine(temporary, P1NativePrefixManifestFile), JsonSerializer.Serialize(new {
                    schema = P1NativePrefixSchema, prefix_id = id, session_id = Manifest.SessionId, timeline_id = Manifest.TimelineId,
                    capture_profile_id = Manifest.CaptureProfileId, declared_origin = Manifest.P1NativeOrigin,
                    status = "open", scope = "durable_prefix", created_at = DateTimeOffset.UtcNow, watermark,
                    raw_file_sha256 = SessionBundlePacker.RecursiveChecksums(raw)
                }, EvidenceJson.IndentedOptions));
                Directory.Move(temporary, destination);
                string manifest = Path.Combine(destination, P1NativePrefixManifestFile);
                return new(id, destination, manifest, EvidenceIdentity.Sha256File(manifest), watermark);
            }
            finally { if (Directory.Exists(temporary)) Directory.Delete(temporary, recursive: true); }
        }
    }
}
