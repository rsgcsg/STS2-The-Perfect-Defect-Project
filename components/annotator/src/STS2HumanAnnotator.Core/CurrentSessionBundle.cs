using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

public static class SessionBundlePacker
{
    public static CanonicalSessionBundleResult Pack(
        string recordingDirectory,
        string workerId,
        string campaignId,
        string outputDirectory,
        string packerSourceRevision,
        bool humanOriginAttested)
    {
        SessionBundleResult result = PackCore(recordingDirectory, workerId, campaignId, outputDirectory,
            packerSourceRevision, humanOriginAttested, compatibility: false);
        return new CanonicalSessionBundleResult(result.Status, result.BundleDirectory,
            result.BundleContentId, result.SessionId, result.RecordCount,
            result.ExportSha256, result.ChecksumsSha256);
    }

    /// <summary>Explicit compatibility export for existing Decision-record-2 consumers.</summary>
    public static SessionBundleResult PackCompatibility(
        string recordingDirectory, string workerId, string campaignId,
        string outputDirectory, string packerSourceRevision, bool humanOriginAttested) =>
        PackCore(recordingDirectory, workerId, campaignId, outputDirectory,
            packerSourceRevision, humanOriginAttested, compatibility: true);

    private static SessionBundleResult PackCore(
        string recordingDirectory, string workerId, string campaignId,
        string outputDirectory, string packerSourceRevision, bool humanOriginAttested,
        bool compatibility)
    {
        if (!humanOriginAttested)
            throw new InvalidDataException("Human origin must be explicitly attested.");
        ValidateIdentifier(workerId, nameof(workerId));
        ValidateIdentifier(campaignId, nameof(campaignId));
        if (packerSourceRevision.Length != 40 || !packerSourceRevision.All(Uri.IsHexDigit))
            throw new InvalidDataException("Packer source revision must be an exact Git SHA.");
        string source = Path.GetFullPath(recordingDirectory);
        CurrentRecordingManifest sourceManifest = Read<CurrentRecordingManifest>(
            Path.Combine(source, "recording-manifest.json"));
        if (sourceManifest.SourceSchemaVersion != null || sourceManifest.Schema == SourceSessionContract.ManifestSchema)
            throw new InvalidDataException("A source session requires the generic source bundle; Human packing is forbidden.");
        string destination = Path.GetFullPath(outputDirectory);
        if (destination == source || destination.StartsWith(source + Path.DirectorySeparatorChar, StringComparison.Ordinal))
            throw new InvalidDataException("A bundle must not modify or be nested inside its immutable source session.");
        RecordingAuditResult audit = RecordingSessionAuditor.Audit(source);
        if (audit.Status != "pass")
            throw new InvalidDataException("Current recording audit must pass before packing.");
        CurrentRecordingManifest manifest = Read<CurrentRecordingManifest>(
            Path.Combine(source, "recording-manifest.json"));
        HumanCaptureProfile profile = Read<HumanCaptureProfile>(
            Path.Combine(source, "capture-profile.json"));
        IReadOnlyList<CurrentDecisionRecord> records = RecordingSessionAuditor.ReadAdmitted(source);
        IReadOnlyList<CanonicalTransitionEvidence> canonical = compatibility
            ? Array.Empty<CanonicalTransitionEvidence>() : ReadCanonical(source);
        int count = compatibility ? records.Count : canonical.Count;
        if (compatibility && count == 0)
            throw new InvalidDataException("A compatibility bundle requires admitted Decision records.");
        if (!compatibility)
            RequireClosedSession(source);
        string schema = compatibility ? CurrentRecordingContract.SessionBundleSchema
            : CanonicalSessionBundleContract.Schema;
        string auditSchema = compatibility ? CurrentRecordingContract.SessionBundleAuditSchema
            : CanonicalSessionBundleContract.AuditSchema;
        string countKey = compatibility ? "record_count" : "canonical_count";

        string parent = Path.GetDirectoryName(destination)
            ?? throw new InvalidDataException("Bundle destination has no parent.");
        Directory.CreateDirectory(parent);
        string temporary = Path.Combine(parent, $".{Path.GetFileName(destination)}.tmp-{Guid.NewGuid():N}");
        Directory.CreateDirectory(temporary);
        try
        {
            string raw = Path.Combine(temporary, "raw");
            string auditDirectory = Path.Combine(temporary, "audit");
            string exportDirectory = Path.Combine(temporary, "export");
            string profileDirectory = Path.Combine(temporary, "profile");
            CopyDirectory(source, raw);
            Directory.CreateDirectory(auditDirectory);
            Directory.CreateDirectory(exportDirectory);
            Directory.CreateDirectory(profileDirectory);
            File.Copy(
                Path.Combine(source, "capture-profile.json"),
                Path.Combine(profileDirectory, "capture-profile.json"));

            var auditDocument = new JsonObject
            {
                ["schema"] = auditSchema,
                ["status"] = audit.Status,
                ["valid_records"] = audit.ValidRecords,
                ["invalid_records"] = audit.InvalidRecords,
                ["invalidations"] = audit.Invalidations,
                ["errors"] = JsonSerializer.SerializeToNode(audit.Errors, EvidenceJson.Options),
                ["non_claims"] = JsonSerializer.SerializeToNode(audit.NonClaims, EvidenceJson.Options)
            };
            if (!compatibility)
                auditDocument["canonical_count"] = count;
            Write(Path.Combine(auditDirectory, "audit-report.json"),
                EvidenceCanonicalJson.Serialize(auditDocument) + "\n");
            string exportPath = Path.Combine(exportDirectory,
                compatibility ? "decisions.jsonl" : "canonical-transitions.jsonl");
            if (compatibility)
                RecordingSessionAuditor.ExportAdmitted(source, exportPath);
            else
                File.Copy(Path.Combine(raw, "canonical-transitions.jsonl"), exportPath);
            string exportSha = EvidenceIdentity.Sha256File(exportPath);
            string[] runIds = (compatibility ? records.Select(record => record.RunId)
                    : ReadJournal(source).Where(row => row.RunId != null).Select(row => row.RunId!)
                        .Concat(canonical.Select(row => row.RunId)))
                .Distinct(StringComparer.Ordinal)
                .Order(StringComparer.Ordinal)
                .ToArray();
            var attestation = new JsonObject
            {
                ["attested"] = true,
                ["method"] = "explicit_owner_pack",
                ["worker_id"] = workerId,
                ["machine_verifiable"] = false
            };
            JsonObject rawSha = RecursiveChecksums(raw);
            var packer = new JsonObject {
                ["product"] = "STS2 Native UI Human Annotator Tool",
                ["version"] = CurrentRecordingContract.ProductVersion,
                ["source_revision"] = packerSourceRevision
            };
            var identity = new JsonObject
            {
                ["schema"] = schema,
                ["session_id"] = manifest.SessionId,
                ["timeline_id"] = manifest.TimelineId,
                ["capture_profile_id"] = profile.ProfileId,
                ["capture_profile_sha256"] = EvidenceIdentity.Sha256Json(profile),
                ["campaign_id"] = campaignId,
                ["worker_id"] = workerId,
                ["human_origin_attestation"] = attestation.DeepClone(),
                [countKey] = count,
                ["run_ids"] = new JsonArray(runIds
                    .Select(runId => (JsonNode?)JsonValue.Create(runId)).ToArray()),
                ["export_sha256"] = exportSha,
                ["raw_file_sha256"] = rawSha,
                ["audit"] = new JsonObject
                {
                    ["status"] = audit.Status,
                    ["valid_records"] = audit.ValidRecords,
                    ["invalid_records"] = audit.InvalidRecords,
                    ["invalidations"] = audit.Invalidations
                }
            };
            if (!compatibility)
            {
                identity["audit"]!["canonical_count"] = count;
                identity["packer"] = packer.DeepClone();
                identity["audit_sha256"] = EvidenceIdentity.Sha256File(
                    Path.Combine(auditDirectory, "audit-report.json"));
            }
            string contentId = EvidenceIdentity.Sha256Text(EvidenceCanonicalJson.Serialize(identity));
            var bundleManifest = new JsonObject
            {
                ["schema_version"] = compatibility ? 2 : CanonicalSessionBundleContract.SchemaVersion,
                ["schema"] = schema,
                ["bundle_content_id"] = contentId,
                ["session_id"] = manifest.SessionId,
                ["timeline_id"] = manifest.TimelineId,
                ["capture_profile_id"] = profile.ProfileId,
                ["capture_profile_sha256"] = EvidenceIdentity.Sha256Json(profile),
                ["campaign_id"] = campaignId,
                ["worker_id"] = workerId,
                ["human_origin_attestation"] = attestation,
                ["created_at"] = manifest.CreatedAt.ToUniversalTime().ToString("O"),
                ["packer"] = packer,
                [countKey] = count,
                ["run_ids"] = new JsonArray(runIds
                    .Select(runId => (JsonNode?)JsonValue.Create(runId)).ToArray()),
                ["export_sha256"] = exportSha,
                ["audit_status"] = audit.Status,
                ["content_identity"] = identity
            };
            Write(
                Path.Combine(temporary, "session-bundle-manifest.json"),
                EvidenceCanonicalJson.Serialize(bundleManifest) + "\n");
            WriteChecksums(temporary);
            if (Directory.Exists(destination))
            {
                if (!DirectoriesEqual(temporary, destination))
                    throw new IOException("An immutable current bundle already exists with different bytes.");
                Directory.Delete(temporary, true);
            }
            else
            {
                Directory.Move(temporary, destination);
            }
            return new SessionBundleResult(
                "pass",
                destination,
                contentId,
                manifest.SessionId,
                count,
                exportSha,
                EvidenceIdentity.Sha256File(Path.Combine(destination, "checksums.sha256")));
        }
        catch
        {
            if (Directory.Exists(temporary))
                Directory.Delete(temporary, true);
            throw;
        }
    }

    public static long ExportCanonical(string recordingDirectory, string output)
    {
        string source = Path.GetFullPath(recordingDirectory);
        RecordingAuditResult audit = RecordingSessionAuditor.Audit(source);
        if (audit.Status != "pass")
            throw new InvalidDataException("Current recording audit must pass before export.");
        RequireClosedSession(source);
        IReadOnlyList<CanonicalTransitionEvidence> rows = ReadCanonical(source);
        string destination = Path.GetFullPath(output);
        if (destination.StartsWith(source + Path.DirectorySeparatorChar, StringComparison.Ordinal))
            throw new InvalidDataException("An export must not modify its immutable source session.");
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        File.Copy(Path.Combine(source, "canonical-transitions.jsonl"), destination, overwrite: false);
        return rows.Count;
    }

    private static IReadOnlyList<CanonicalTransitionEvidence> ReadCanonical(string source) =>
        File.ReadLines(Path.Combine(source, "canonical-transitions.jsonl"))
            .Where(line => !string.IsNullOrWhiteSpace(line))
            .Select(line => JsonSerializer.Deserialize<CanonicalTransitionEvidence>(line, EvidenceJson.Options)
                ?? throw new InvalidDataException("Canonical transition is null."))
            .ToArray();

    private static RunJournalEvent[] ReadJournal(string source) =>
        File.ReadLines(Path.Combine(source, "run-journal.jsonl"))
            .Where(line => !string.IsNullOrWhiteSpace(line))
            .Select(line => JsonSerializer.Deserialize<RunJournalEvent>(line, EvidenceJson.Options)!).ToArray();

    private static void RequireClosedSession(string source)
    {
        RunJournalEvent[] journal = ReadJournal(source);
        if (journal.Length == 0 || journal[^1].Kind != "session_closed"
            || journal.Count(row => row.Kind == "session_closed") != 1)
            throw new InvalidDataException("Current bundle/export requires a sealed, closed session.");
    }

    internal static JsonObject RecursiveChecksums(string directory)
    {
        var result = new JsonObject();
        foreach (string file in Directory.GetFiles(directory, "*", SearchOption.AllDirectories)
                     .Order(StringComparer.Ordinal))
        {
            string relative = Path.GetRelativePath(directory, file).Replace('\\', '/');
            result[relative] = EvidenceIdentity.Sha256File(file);
        }
        return result;
    }

    internal static void CopyDirectory(string source, string destination)
    {
        foreach (string directory in Directory.GetDirectories(source, "*", SearchOption.AllDirectories)
                     .Prepend(source))
        {
            string relative = Path.GetRelativePath(source, directory);
            Directory.CreateDirectory(relative == "."
                ? destination
                : Path.Combine(destination, relative));
        }
        foreach (string file in Directory.GetFiles(source, "*", SearchOption.AllDirectories))
        {
            // The retired native-action ledger is an archival reader input,
            // never part of a current bundle or its identity.
            if (string.Equals(
                    Path.GetFileName(file),
                    "native-action-ledger.jsonl",
                    StringComparison.Ordinal))
                continue;
            File.Copy(file, Path.Combine(destination, Path.GetRelativePath(source, file)));
        }
    }

    internal static void WriteChecksums(string directory)
    {
        string content = string.Join("\n", Directory.GetFiles(directory, "*", SearchOption.AllDirectories)
            .Where(path => Path.GetFileName(path) != "checksums.sha256")
            .Order(StringComparer.Ordinal)
            .Select(path => $"{EvidenceIdentity.Sha256File(path)}  {Path.GetRelativePath(directory, path).Replace('\\', '/') }")) + "\n";
        Write(Path.Combine(directory, "checksums.sha256"), content);
    }

    internal static bool DirectoriesEqual(string first, string second)
    {
        string[] firstFiles = Directory.GetFiles(first, "*", SearchOption.AllDirectories)
            .Select(path => Path.GetRelativePath(first, path).Replace('\\', '/'))
            .Order(StringComparer.Ordinal).ToArray();
        string[] secondFiles = Directory.GetFiles(second, "*", SearchOption.AllDirectories)
            .Select(path => Path.GetRelativePath(second, path).Replace('\\', '/'))
            .Order(StringComparer.Ordinal).ToArray();
        return firstFiles.SequenceEqual(secondFiles, StringComparer.Ordinal)
               && firstFiles.All(relative => EvidenceIdentity.Sha256File(Path.Combine(first, relative))
                   == EvidenceIdentity.Sha256File(Path.Combine(second, relative)));
    }

    private static T Read<T>(string path) =>
        JsonSerializer.Deserialize<T>(File.ReadAllText(path), EvidenceJson.Options)
        ?? throw new InvalidDataException($"JSON file is empty: {path}");

    internal static void ValidateIdentifier(string value, string name)
    {
        if (value.Length is < 3 or > 64
            || !value.All(character => char.IsLower(character) || char.IsDigit(character)
                || character is '-' or '_'))
            throw new InvalidDataException($"{name} must be a lowercase pseudonymous identifier.");
    }

    private static void Write(string path, string content)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, content, new UTF8Encoding(false));
    }
}

public static class CanonicalSessionBundleContract
{
    public const int SchemaVersion = 3;
    public const string Schema = "sts2.human-annotator/session-bundle-3";
    public const string AuditSchema = "sts2.human-annotator/session-bundle-audit-3";
}

public sealed record CanonicalSessionBundleResult(
    string Status, string BundleDirectory, string BundleContentId, string SessionId,
    long CanonicalCount, string ExportSha256, string ChecksumsSha256);
