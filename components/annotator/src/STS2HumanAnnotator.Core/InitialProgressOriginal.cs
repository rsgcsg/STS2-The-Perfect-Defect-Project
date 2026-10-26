using System.Text.Json;

namespace STS2HumanAnnotator.Core;

// Private recording provenance only. This is never part of a player observation,
// a native action binding or a research eligibility decision.
public sealed record InitialProgressOriginal(
    string Schema, string Status, string SessionId, string TimelineId, string RunId,
    DateTimeOffset CapturedAt, int? NativeProfileId, string Source, string? PayloadRef,
    long? Bytes, string? Sha256, string? Reason)
{
    public const string CurrentSchema = "sts2.human-annotator/initial-progress-original-1";
    public const int MaximumBytes = 16 * 1024 * 1024;

    public static IReadOnlyList<string> Audit(string directory, CurrentRecordingManifest manifest)
    {
        string root = Path.Combine(directory, "initial-progress");
        if (!Directory.Exists(root)) return Array.Empty<string>(); // Historical absence stays unknown.
        if (new DirectoryInfo(root).LinkTarget != null) return new[] { "initial_progress_original_invalid" };
        var errors = new List<string>();
        foreach (string path in Directory.GetFiles(root, "*.json"))
        {
            try
            {
                var value = JsonSerializer.Deserialize<InitialProgressOriginal>(File.ReadAllBytes(path), EvidenceJson.Options)!;
                if (value == null || new FileInfo(path).LinkTarget != null) throw new InvalidDataException();
                SessionBundlePacker.ValidateIdentifier(value.RunId, "runId");
                if (value.Schema != CurrentSchema || value.SessionId != manifest.SessionId
                    || value.TimelineId != manifest.TimelineId || Path.GetFileName(path) != value.RunId + ".json"
                    || value.Source != "native_progress_file" || value.CapturedAt == default)
                    throw new InvalidDataException();
                if (value.Status == "captured")
                {
                    if (value.Sha256 == null || value.Sha256.Length != 64 || !value.Sha256.All(Uri.IsHexDigit)
                        || value.PayloadRef != "initial-progress/" + value.Sha256 + ".bin"
                        || value.Bytes is not > 0 or > MaximumBytes || value.Reason != null)
                        throw new InvalidDataException();
                    string payload = Path.Combine(directory, "initial-progress", value.Sha256 + ".bin");
                    var info = new FileInfo(payload);
                    if (!info.Exists || info.LinkTarget != null || info.Length != value.Bytes
                        || EvidenceIdentity.Sha256File(payload) != value.Sha256) throw new InvalidDataException();
                }
                else if (value.Status != "unknown" || value.PayloadRef != null || value.Bytes != null
                    || value.Sha256 != null || string.IsNullOrWhiteSpace(value.Reason)) throw new InvalidDataException();
            }
            catch (Exception error) when (error is IOException or InvalidDataException or JsonException or ArgumentException or UnauthorizedAccessException)
            { errors.Add("initial_progress_original_invalid"); }
        }
        return errors;
    }
}

public sealed partial class RecordingSessionStore
{
    public bool TryPersistInitialProgressOriginal(string runId, DateTimeOffset capturedAt,
        int? nativeProfileId, byte[]? raw, string? reason = null, Action<Exception>? reportFailure = null)
    {
        try { PersistInitialProgressOriginal(runId, capturedAt, nativeProfileId, raw, reason); return true; }
        catch (Exception error)
        {
            // Optional provenance and even its diagnostic sink cannot alter
            // the native launch/recording path when storage is unavailable.
            try { reportFailure?.Invoke(error); } catch { }
            return false;
        }
    }

    private void PersistInitialProgressOriginal(string runId, DateTimeOffset capturedAt,
        int? nativeProfileId, byte[]? raw, string? reason = null)
    {
        SessionBundlePacker.ValidateIdentifier(runId, nameof(runId));
        lock (_gate)
        {
            if (_closed || _disposed) throw new InvalidOperationException("recording_closed");
            string root = Path.Combine(DirectoryPath, "initial-progress"), metadata = Path.Combine(root, runId + ".json");
            if (File.Exists(metadata)) return; // One launch witness, never a later backfill.
            Directory.CreateDirectory(root);
            string? sha = null, payload = null;
            if (raw is { Length: > 0 and <= InitialProgressOriginal.MaximumBytes })
            {
                sha = EvidenceIdentity.Sha256Bytes(raw); payload = "initial-progress/" + sha + ".bin";
                string file = Path.Combine(root, sha + ".bin");
                if (!File.Exists(file))
                {
                    using var stream = new FileStream(file, FileMode.CreateNew, FileAccess.Write, FileShare.Read);
                    stream.Write(raw); stream.Flush(true);
                }
                if (EvidenceIdentity.Sha256File(file) != sha) throw new InvalidDataException("initial_progress_original_changed");
            }
            var value = new InitialProgressOriginal(InitialProgressOriginal.CurrentSchema,
                sha == null ? "unknown" : "captured", Manifest.SessionId, Manifest.TimelineId, runId,
                capturedAt, nativeProfileId, "native_progress_file", payload, sha == null ? null : raw!.LongLength,
                sha, sha == null ? reason ?? "native_progress_file_unavailable" : null);
            WriteCreateNew(metadata, JsonSerializer.Serialize(value, EvidenceJson.IndentedOptions));
        }
    }
}
