using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Text.Json;
using STS2HumanAnnotator.Core;

return args switch
{
    ["audit", string directory] => Audit(directory),
    ["audit-source-v2", string directory] => AuditSourceV2(directory),
    ["audit-source-v3", string directory] => AuditSourceV2(directory, 3),
    ["export-source-v2", string directory, string output] => ExportSourceV2(directory, output),
    ["export-source-v3", string directory, string output] => ExportSourceV2(directory, output, 3),
    ["pack-source-v2", string directory, string worker, string campaign, string output, string revision] =>
        PackSourceV2(directory, worker, campaign, output, revision),
    ["pack-source-v3", string directory, string worker, string campaign, string output, string revision] =>
        PackSourceV2(directory, worker, campaign, output, revision, 3),
    ["audit-native-semantic", string directory] => AuditNativeSemantic(directory),
    ["export", string directory, string output] => Export(directory, output),
    ["export-compatibility", string directory, string output] => Export(directory, output, compatibility: true),
    ["pack-session", string directory, string worker, string campaign,
        string output, string sourceRevision, "human_origin_attested"] =>
        PackSession(directory, worker, campaign, output, sourceRevision),
    ["pack-session-compatibility", string directory, string worker, string campaign,
        string output, string sourceRevision, "human_origin_attested"] =>
        PackSession(directory, worker, campaign, output, sourceRevision, compatibility: true),
    ["identity", string assembly] => Identity(assembly),
    ["recover-interrupted", string recordings, string recovered] => RecoverInterrupted(recordings, recovered),
    _ => Usage()
};

static int AuditSourceV2(string directory, int version = 2)
{
    var audit = version == 3 ? SourceSessionAuditV3.Audit(directory) : SourceSessionAuditV2.Audit(directory);
    Console.WriteLine(JsonSerializer.Serialize(audit, EvidenceJson.IndentedOptions));
    return audit.Status == "pass" ? 0 : 1;
}
static int ExportSourceV2(string directory, string output, int version = 2)
{
    var result = version == 3 ? SourceSessionBundlePackerV3.Export(directory, output) : SourceSessionBundlePackerV2.Export(directory, output);
    Console.WriteLine(JsonSerializer.Serialize(result, EvidenceJson.IndentedOptions)); return 0;
}
static int PackSourceV2(string directory, string worker, string campaign, string output, string revision, int version = 2)
{
    var result = version == 3 ? SourceSessionBundlePackerV3.Pack(directory, worker, campaign, output, revision)
        : SourceSessionBundlePackerV2.Pack(directory, worker, campaign, output, revision);
    Console.WriteLine(JsonSerializer.Serialize(result, EvidenceJson.IndentedOptions)); return 0;
}

static int RecoverInterrupted(string recordings, string recovered)
{
    Console.WriteLine(JsonSerializer.Serialize(
        InterruptedRecordingRecovery.RecoverOne(recordings, recovered), EvidenceJson.IndentedOptions));
    return 0;
}

static int PackSession(
    string directory,
    string worker,
    string campaign,
    string output,
    string sourceRevision,
    bool compatibility = false)
{
    object result = compatibility
        ? SessionBundlePacker.PackCompatibility(directory, worker, campaign, output, sourceRevision, true)
        : SessionBundlePacker.Pack(directory, worker, campaign, output, sourceRevision, true);
    Console.WriteLine(JsonSerializer.Serialize(result, EvidenceJson.IndentedOptions));
    return 0;
}

static int Audit(string directory)
{
    RecordingAuditResult audit = RecordingSessionAuditor.Audit(directory);
    Console.WriteLine(JsonSerializer.Serialize(audit, EvidenceJson.IndentedOptions));
    return audit.Status == "pass" ? 0 : 1;
}

static int AuditNativeSemantic(string directory)
{
    string path = Path.Combine(
        Path.GetFullPath(directory),
        "native-semantic-discriminator.jsonl");
    if (!File.Exists(path))
    {
        Console.Error.WriteLine($"Native semantic discriminator stream is absent: {path}");
        return 1;
    }
    NativeSemanticDiscriminatorEvent[] events = File.ReadLines(path)
        .Where(line => !string.IsNullOrWhiteSpace(line))
        .Select(line => JsonSerializer.Deserialize<NativeSemanticDiscriminatorEvent>(
            line,
            EvidenceJson.Options)
            ?? throw new InvalidDataException("A discriminator event could not be decoded."))
        .ToArray();
    NativeSemanticDiscriminatorReport report =
        NativeSemanticDiscriminatorAnalyzer.Analyze(events);
    Console.WriteLine(JsonSerializer.Serialize(report, EvidenceJson.IndentedOptions));
    return report.Status == "pass" ? 0 : 1;
}

static int Export(string directory, string output, bool compatibility = false)
{
    long count = compatibility ? RecordingSessionAuditor.ExportAdmitted(directory, output)
        : SessionBundlePacker.ExportCanonical(directory, output);
    Console.WriteLine(JsonSerializer.Serialize(
        new { status = "pass", exported_rows = count, format = compatibility ? "decision-record-2" : "canonical-transitions", output = Path.GetFullPath(output) },
        EvidenceJson.IndentedOptions));
    return 0;
}

static int Identity(string assembly)
{
    string path = Path.GetFullPath(assembly);
    using var stream = File.OpenRead(path);
    using var pe = new PEReader(stream);
    MetadataReader metadata = pe.GetMetadataReader();
    Guid mvid = metadata.GetGuid(metadata.GetModuleDefinition().Mvid);
    Console.WriteLine(JsonSerializer.Serialize(
        new
        {
            path,
            sha256 = EvidenceIdentity.Sha256File(path),
            module_version_id = mvid.ToString("D")
        },
        EvidenceJson.IndentedOptions));
    return 0;
}

static int Usage()
{
    Console.Error.WriteLine("usage: sts2-human-annotator audit <recording-dir> | audit-native-semantic <recording-dir> | audit-source[-v2] <recording-dir> | export-source[-v2] <recording-dir> <output-dir> | pack-source[-v2] <recording-dir> <worker-id> <campaign-id> <output-dir> <source-revision> | export[-compatibility] <recording-dir> <output.jsonl> | pack-session[-compatibility] <recording-dir> <worker-id> <campaign-id> <output-dir> <source-revision> human_origin_attested | identity <assembly> | recover-interrupted <recordings-root> <recovered-root>");
    return 2;
}
