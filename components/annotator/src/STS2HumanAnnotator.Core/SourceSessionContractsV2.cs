using System.Text;
using System.Text.Json.Serialization;

namespace STS2HumanAnnotator.Core;

/// <summary>One recorder lifecycle with immutable native attachment epochs. No Human or causal authority.</summary>
public static class SourceSessionContractV2
{
    public const string ProfileId = "native-logical-source-v2";
    public const string PublicationProfileId = "native-logical-publication-profile-v1";
    public const string PublicationProfileDefinitionSha256 = "c060cfd354c6702e10711e6329848836ec133f2117841750e317b6ab27b244cf";
    public const string ManifestSchema = "sts2.annotator/source-session-manifest-2";
    public const string ProfileSchema = "sts2.annotator/source-capture-profile-2";
    public const string EpochSchema = "sts2.annotator/source-attachment-epoch-2";
    public const string SegmentSchema = "sts2.annotator/source-segment-2";
    public const string BoundarySchema = "sts2.annotator/source-boundary-2";
    public const string ObservationSchema = "sts2.annotator/public-observation-2";
    public const string InputSchema = "sts2.annotator/native-input-witness-2";
    public const string CloseSchema = "sts2.annotator/source-session-close-2";
    public const string BundleSchema = "sts2.annotator/source-session-bundle-2";
    public const string AuditSchema = "sts2.annotator/source-session-audit-2";
    public const string CommandSchema = "sts2.ai-platform/recording-command-3";
    public static readonly string[] StreamFiles =
    {
        "source-attachment-epochs.jsonl", "source-segments.jsonl", "source-boundaries.jsonl",
        "public-observations.jsonl", "native-input-witnesses.jsonl"
    };
    public static readonly string[] NonClaims = SourceSessionContract.NonClaims.Take(4)
        .Append("not_non_interference_qualified").Append("not_g2_v1_approved").ToArray();
    public static ulong Index(SourceNativePositionV2 position)
    {
        SourceSessionContract.Identifier(position.EpochId);
        return SourceSessionContract.Index(new(position.StreamGeneration, position.PublicationIndex));
    }
    public static void Text(string? value, int maximumBytes = 128)
    {
        if (value == null) throw new InvalidDataException("source_text_required");
        try
        {
            if (new UTF8Encoding(false, true).GetByteCount(value) > maximumBytes)
                throw new InvalidDataException("source_text_capacity");
        }
        catch (EncoderFallbackException) { throw new InvalidDataException("source_text_unicode_invalid"); }
    }
    public static void Validate(SourceNativeTransitionV2 transition)
    {
        SourceSessionContract.Identifier(transition.WitnessId); Text(transition.Mechanism);
        foreach (string? continuity in new[] { transition.PreviousGameContinuityId, transition.GameContinuityId })
            if (continuity != null) SourceSessionContract.Identifier(continuity);
        if (transition.Kind is not ("setup_handoff" or "launch" or "terminal" or "cleanup"))
            throw new InvalidDataException("source_native_transition_kind_invalid");
        if (transition.Kind is "setup_handoff" or "launch")
        {
            if (transition.StartProvenance is not ("new" or "saved" or "unknown")
                || transition.GameContinuityId == null || transition.Graceful != null || transition.Victory != null)
                throw new InvalidDataException("source_native_setup_or_launch_invalid");
            if (transition.Kind == "setup_handoff" && transition.PreviousGameContinuityId == transition.GameContinuityId
                || transition.Kind == "launch" && transition.PreviousGameContinuityId != transition.GameContinuityId)
                throw new InvalidDataException("source_native_continuity_transition_invalid");
        }
        else if (transition.StartProvenance != null
            || transition.Kind == "terminal" && (transition.Victory == null || transition.Graceful != null
                || transition.GameContinuityId == null || transition.PreviousGameContinuityId != transition.GameContinuityId)
            || transition.Kind == "cleanup" && (transition.Graceful == null || transition.Victory != null))
            throw new InvalidDataException("source_native_terminal_or_cleanup_invalid");
    }
    public static string ProfileDigest(SourceCaptureProfileV2 profile) => SourceSessionContract.Sha256(SourceSessionJson.Bytes(profile));
}

public sealed record SourceSessionLimitsV2(
    int MaxCaptureBytes = 64 * 1024 * 1024, long MaxPayloadBytes = 512L * 1024 * 1024,
    int MaxRowBytes = 1024 * 1024, int MaxRowsPerStream = 65536, long MaxBytesPerStream = 64L * 1024 * 1024,
    int MaxSegments = 256, int MaxEpochs = 256, int MaxRetiringEpochs = 2, int MaxMetadataPackets = 32,
    long MaxCopiedPayloadBytes = 128L * 1024 * 1024, int MaxPendingInputs = 128,
    int EncodingDeadlineMs = 2000, int CloseBarrierGraceMs = 3000);
public sealed record SourceCaptureProfileV2(string Schema, string ProfileId, string InputProfile,
    string PublicationProfileId, string PublicationProfileDefinitionSha256,
    IReadOnlyList<string> EagerScope, SourceSessionLimitsV2 Limits, IReadOnlyList<string> NonClaims);
public sealed record SourceNativePositionV2(string EpochId, string StreamGeneration, string PublicationIndex);
public sealed record SourceNativeSealV2(string EpochId, string StreamGeneration, string ReservedThrough, string CompletedThrough)
{ [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? AfterInputOrdinal { get; init; } }
public sealed record SourceNativeTransitionV2(string WitnessId, string Kind, string Mechanism,
    string? PreviousGameContinuityId, string? GameContinuityId, string? StartProvenance, bool? Graceful, bool? Victory);
public sealed record SourceEpochContextV2(string PublicationProfileId, string PublicationProfileDefinitionSha256,
    string ScopeId, string StreamGeneration, IReadOnlyList<string> EagerScope,
    IReadOnlyDictionary<string, SourceSeamCoverage> SeamCoverage, RecorderEnvironmentIdentity Environment,
    string? GameContinuityId);
public sealed record SourceEpochPacketV2(string EpochId, string? PreviousEpochId, SourceEpochContextV2 Context,
    SourceNativePositionV2 StartingPosition, SourceNativePositionV2 InitialPosition,
    SourceNativeSealV2? PredecessorSeal, SourceNativeTransitionV2? Transition);
public sealed record SourceAttachmentEpochV2(string Schema, long Sequence, string SessionId, string TimelineId,
    string EpochId, string? PreviousEpochId, SourceEpochContextV2 Context,
    SourceNativePositionV2 StartingPosition, SourceNativePositionV2 InitialPosition,
    SourceNativeSealV2? PredecessorSeal, SourceNativeTransitionV2? Transition, DateTimeOffset RecordedAt)
{ [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? AfterInputOrdinal { get; init; } }
public sealed record SourceSegmentV2(string Schema, long Sequence, string SessionId, string TimelineId,
    string SegmentId, string? PreviousSegmentId, SourceDeclaration Declaration,
    SourceNativePositionV2 BoundaryPosition, DateTimeOffset RecordedAt)
{ [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? AfterInputOrdinal { get; init; } }
public sealed record SourcePausedIntervalV2(string EpochId, string StreamGeneration, string AfterIndex,
    string ThroughIndex, string Reason = "recording_paused")
{
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? AfterInputOrdinal { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? ThroughInputOrdinal { get; init; }
}
public sealed record SourceBoundaryV2(string Schema, long Sequence, string SessionId, string TimelineId,
    string Kind, string SegmentId, SourceNativePositionV2 Position, IReadOnlyList<SourceNativeSealV2> SealedEpochs,
    IReadOnlyList<SourcePausedIntervalV2> PausedIntervals, SourceNativeTransitionV2? Transition, DateTimeOffset RecordedAt)
{ [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? AfterInputOrdinal { get; init; } }
public sealed record FrozenPublicCaptureV2(string EpochId, FrozenPublicCapture Capture);
public sealed record FrozenPublicCatalogV2(string EpochId, FrozenPublicCatalog Catalog);
public sealed record PublicCaptureReferenceV2(string EpochId, string CaptureId, string SnapshotId, string ScopeId,
    string StreamGeneration, DateTimeOffset CapturedAt, long ByteCount, string Sha256, string PayloadRef);
public sealed record PublicCatalogReferenceV2(string EpochId, string CatalogRef, string SnapshotId, string ScopeId,
    string StreamGeneration, int TotalCount, string StructuralDigest, long ByteCount, string PayloadSha256, string PayloadRef);
public sealed record SourceObservationPacketV2(SourceNativePositionV2 Position, string SourceSeam,
    string SourceIndex, string Phase, string? SnapshotId, string? OwnerOccurrence, string? GameContinuityId,
    string Completeness, FrozenPublicCaptureV2? Capture, FrozenPublicCatalogV2? Catalog,
    string? MissingReason = null, string? GapAfterIndex = null);
public sealed record SourcePublicObservationV2(string Schema, long Sequence, string SessionId, string TimelineId,
    string EpochId, string SegmentId, SourceNativePositionV2 Position, string SourceSeam, string SourceIndex,
    string Phase, string? SnapshotId, string? OwnerOccurrence, string? GameContinuityId, string Completeness,
    PublicCaptureReferenceV2? Capture, PublicCatalogReferenceV2? Catalog, string? MissingReason, string? GapAfterIndex);
public sealed record SourceInputStageV2(string Stage, string Delivery, string Evidence);
public sealed record SourceInputOutcomeV2(string MappingStatus, int MatchCount, SourcePublicAction? SelectedAction,
    string NativeMechanism, string Delivery, string? ReasonCode, IReadOnlyList<SourceInputStageV2> Stages);
public sealed record SourceNativeInputWitnessV2(string Schema, long Sequence, string SessionId, string TimelineId,
    string InputId, string EpochId, string SegmentId, SourceNativePositionV2 PrePosition,
    PublicCaptureReferenceV2? PreCapture, PublicCatalogReferenceV2? Catalog,
    SourceInputOutcomeV2 Outcome, DateTimeOffset RecordedAt)
{
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? InputPrefixOrdinal { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public SourceBasisOrderV3? BasisOrder { get; init; }
}
public sealed record SourceFinalDrainV2(string EpochId, string StreamGeneration, string SealedReservedThrough,
    string CompletedThrough, string DurableThrough, bool AdmittedInputsTerminal)
{ [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? AfterInputOrdinal { get; init; } }
public sealed record SourceSessionStatusV2(string EpochId, string SegmentId, SourceDeclaration Declaration,
    long Observations, long Inputs, int PendingInputs, int Epochs, long Gaps, bool AccountingComplete, string? Error);

/// <summary>Store-issued immutable token. Prefix admission performs no filesystem I/O and freezes the original declaration.</summary>
public sealed class SourceInputTokenV2
{
    internal SourceInputTokenV2(object owner, string inputId, string sessionId, string timelineId,
        string segmentId, SourceNativePositionV2 prePosition, string? inputPrefixOrdinal = null)
    {
        Owner = owner; InputId = inputId; SessionId = sessionId; TimelineId = timelineId;
        SegmentId = segmentId; PrePosition = prePosition; InputPrefixOrdinal = inputPrefixOrdinal;
    }
    internal object Owner { get; }
    public string InputId { get; }
    public string SessionId { get; }
    public string TimelineId { get; }
    public string SegmentId { get; }
    public SourceNativePositionV2 PrePosition { get; }
    public string? InputPrefixOrdinal { get; }
}
