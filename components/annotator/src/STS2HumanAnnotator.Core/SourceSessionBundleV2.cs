using System.Text.Json;
using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

/// <summary>Generic immutable source evidence export. It has no Human attestation switch.</summary>
public static class SourceSessionBundlePackerV2
{
    public static SourceSessionBundleResult Pack(string recordingDirectory, string workerId,
        string campaignId, string outputDirectory, string packerSourceRevision)
    {
        SessionBundlePacker.ValidateIdentifier(workerId, nameof(workerId));
        SessionBundlePacker.ValidateIdentifier(campaignId, nameof(campaignId));
        if (packerSourceRevision.Length != 40 || !packerSourceRevision.All(Uri.IsHexDigit))
            throw new InvalidDataException("source_packer_revision_invalid");
        string source = Path.GetFullPath(recordingDirectory), output = Path.GetFullPath(outputDirectory);
        RejectNested(source, output);
        SourceSessionAuditResult audit = SourceSessionAuditV2.Audit(source);
        if (audit.Status != "pass") throw new InvalidDataException("source_audit_required:" + string.Join(",", audit.Errors));
        string parent = Path.GetDirectoryName(output) ?? throw new InvalidDataException("source_bundle_parent_missing");
        Directory.CreateDirectory(parent);
        string temporary = Path.Combine(parent, "." + Path.GetFileName(output) + ".tmp-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(temporary);
        try
        {
            string raw = Path.Combine(temporary, "raw");
            SessionBundlePacker.CopyDirectory(source, raw);
            audit = SourceSessionAuditV2.Audit(raw);
            if (audit.Status != "pass") throw new InvalidDataException("source_copied_audit_required:" + string.Join(",", audit.Errors));
            CurrentRecordingManifest manifest = SourceSessionJson.Read<CurrentRecordingManifest>(Path.Combine(raw, "recording-manifest.json"));
            string export = Path.Combine(temporary, "export");
            CopyExport(raw, export);
            string auditDirectory = Path.Combine(temporary, "audit"); Directory.CreateDirectory(auditDirectory);
            File.WriteAllBytes(Path.Combine(auditDirectory, "source-audit.json"), SourceSessionJson.Bytes(audit));
            JsonObject rawHashes = SessionBundlePacker.RecursiveChecksums(raw);
            JsonObject exportHashes = SessionBundlePacker.RecursiveChecksums(export);
            var identity = new
            {
                schema = SourceSessionContractV2.BundleSchema, session_id = manifest.SessionId,
                timeline_id = manifest.TimelineId, capture_profile_id = manifest.CaptureProfileId,
                worker_id = workerId, campaign_id = campaignId, packer_source_revision = packerSourceRevision,
                raw_file_sha256 = rawHashes, export_file_sha256 = exportHashes,
                audit_sha256 = SourceSessionContract.Sha256(File.ReadAllBytes(Path.Combine(auditDirectory, "source-audit.json"))),
                source_kinds = audit.SourceKinds, human_origin_attested = false,
                non_claims = SourceSessionContractV2.NonClaims
            };
            byte[] identityBytes = SourceSessionJson.Bytes(identity);
            File.WriteAllBytes(Path.Combine(temporary, "content-identity.json"), identityBytes);
            string contentId = SourceSessionContract.Sha256(identityBytes);
            var bundle = new
            {
                schema_version = 2, schema = SourceSessionContractV2.BundleSchema, bundle_content_id = contentId,
                session_id = manifest.SessionId, timeline_id = manifest.TimelineId,
                capture_profile_id = manifest.CaptureProfileId, capture_profile_sha256 = manifest.CaptureProfileSha256,
                worker_id = workerId, campaign_id = campaignId, source_kinds = audit.SourceKinds,
                human_origin_attested = false, audit_status = "pass",
                observation_count = audit.ObservationCount, input_count = audit.InputCount, gap_count = audit.GapCount,
                content_identity = JsonSerializer.Deserialize<JsonObject>(identityBytes, SourceSessionJson.Options),
                non_claims = SourceSessionContractV2.NonClaims
            };
            File.WriteAllBytes(Path.Combine(temporary, "source-session-bundle-manifest.json"), SourceSessionJson.Bytes(bundle));
            SessionBundlePacker.WriteChecksums(temporary);
            if (Directory.Exists(output))
            {
                if (!SessionBundlePacker.DirectoriesEqual(temporary, output))
                    throw new IOException("immutable_source_bundle_changed_retry");
                Directory.Delete(temporary, true);
            }
            else Directory.Move(temporary, output);
            return new("pass", output, contentId, manifest.SessionId, audit.ObservationCount, audit.InputCount,
                SourceSessionContract.Sha256(File.ReadAllBytes(Path.Combine(output, "checksums.sha256"))));
        }
        catch { if (Directory.Exists(temporary)) Directory.Delete(temporary, true); throw; }
    }

    public static SourceSessionAuditResult Export(string recordingDirectory, string outputDirectory)
    {
        string source = Path.GetFullPath(recordingDirectory), output = Path.GetFullPath(outputDirectory);
        RejectNested(source, output);
        SourceSessionAuditResult audit = SourceSessionAuditV2.Audit(source);
        if (audit.Status != "pass") throw new InvalidDataException("source_audit_required");
        if (Directory.Exists(output)) throw new IOException("source_export_already_exists");
        string parent = Path.GetDirectoryName(output) ?? throw new InvalidDataException("source_export_parent_missing");
        Directory.CreateDirectory(parent);
        string temporary = Path.Combine(parent, "." + Path.GetFileName(output) + ".tmp-" + Guid.NewGuid().ToString("N"));
        string snapshot = temporary + ".raw";
        try
        {
            SessionBundlePacker.CopyDirectory(source, snapshot);
            audit = SourceSessionAuditV2.Audit(snapshot);
            if (audit.Status != "pass") throw new InvalidDataException("source_copied_audit_required");
            CopyExport(snapshot, temporary); Directory.Move(temporary, output);
        }
        catch { if (Directory.Exists(temporary)) Directory.Delete(temporary, true); throw; }
        finally { if (Directory.Exists(snapshot)) Directory.Delete(snapshot, true); }
        return audit;
    }

    private static void CopyExport(string source, string output)
    {
        Directory.CreateDirectory(output);
        foreach (string file in SourceSessionContractV2.StreamFiles)
            File.Copy(Path.Combine(source, file), Path.Combine(output, file));
        foreach (string family in new[] { "public-captures", "public-catalogs" })
            if (Directory.Exists(Path.Combine(source, family)))
                SessionBundlePacker.CopyDirectory(Path.Combine(source, family), Path.Combine(output, family));
    }
    private static void RejectNested(string source, string output)
    {
        if (source == output || output.StartsWith(source + Path.DirectorySeparatorChar, StringComparison.Ordinal)
            || source.StartsWith(output + Path.DirectorySeparatorChar, StringComparison.Ordinal))
            throw new InvalidDataException("source_bundle_must_be_separate");
    }
}
