namespace STS2HumanAnnotator.Core;

/// <summary>Same original source engine, with an independently retained neutral public basis.</summary>
public static class SourceSessionContractV4
{
    public const string ProfileId = "native-logical-source-v4";
    public const string TypeId = "source-session-bundle-v4";
    public const string PublicBasisSchema = "sts2.player-environment/public-basis-1";
    public const string PublicBasisProfile = "public-basis-1";
    public const string PublicBasisRevision = "1";
    public const string PublicBasisScope = "current_public_snapshot_and_advertised_read_instances";
    public static SourcePublicBasisProfileV4 PublicBasis { get; } = new(
        PublicBasisSchema, PublicBasisProfile, PublicBasisRevision, PublicBasisScope);
}

public sealed record SourcePublicBasisProfileV4(string Schema, string Profile, string Revision, string CaptureScope);

/// <summary>Original bytes detached before native input; no game objects or dispatch closures.</summary>
public sealed record FrozenPublicBasisV4(string EpochId, string SnapshotId, string ScopeId,
    string StreamGeneration, DateTimeOffset CapturedAt, byte[] Bytes, string Sha256);

public sealed record SourcePublicBasisReferenceV4(string EpochId, string SnapshotId, string ScopeId,
    string StreamGeneration, DateTimeOffset CapturedAt, long ByteCount, string Sha256, string PayloadRef);

/// <summary>Native observation/catalog completeness is retained in its original fields, independently.</summary>
public sealed record SourceCapturedPartsV4(SourcePublicBasisReferenceV4? PublicBasis, string? PublicBasisMissingReason,
    SourceNativeStageV4? NativeStage = null);
public sealed record SourceNativeStageV4(string Kind, string OwnerRef, string SubjectRef,
    IReadOnlyDictionary<string, string> Arguments, string Evidence, bool? NoSubmission);

public sealed record SourceNativeCarrierV4(string Kind, string CarrierRef);
public sealed record SourceNativeFactsV4(string Origin, string? OwnerRef, string? SubjectRef,
    IReadOnlyDictionary<string, string> Arguments, IReadOnlyList<SourceNativeCarrierV4> Carriers, string? MissingReason);

public static class SourceSessionAuditV4
{
    public static SourceSessionAuditResult Audit(string recordingDirectory) => SourceSessionAuditV2.AuditVersion(recordingDirectory, 4);
}

public static class SourceSessionBundlePackerV4
{
    public static SourceSessionBundleResult Pack(string recordingDirectory, string workerId,
        string campaignId, string outputDirectory, string packerSourceRevision) =>
        SourceSessionBundlePackerV2.PackVersion(recordingDirectory, workerId, campaignId, outputDirectory, packerSourceRevision, 4);
    public static SourceSessionAuditResult Export(string recordingDirectory, string outputDirectory) =>
        SourceSessionBundlePackerV2.ExportVersion(recordingDirectory, outputDirectory, 4);
}
