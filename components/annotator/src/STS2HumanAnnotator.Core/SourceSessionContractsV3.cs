namespace STS2HumanAnnotator.Core;

/// <summary>Versioned native-prefix order supplement over the shared Source recording engine.</summary>
public static class SourceSessionContractV3
{
    public const string ProfileId = "native-logical-source-v3";
    public const string ManifestSchema = "sts2.annotator/source-session-manifest-3";
    public const string ProfileSchema = "sts2.annotator/source-capture-profile-3";
    public const string EpochSchema = "sts2.annotator/source-attachment-epoch-3";
    public const string SegmentSchema = "sts2.annotator/source-segment-3";
    public const string BoundarySchema = "sts2.annotator/source-boundary-3";
    public const string ObservationSchema = "sts2.annotator/public-observation-3";
    public const string InputSchema = "sts2.annotator/native-input-witness-3";
    public const string CloseSchema = "sts2.annotator/source-session-close-3";
    public const string BundleSchema = "sts2.annotator/source-session-bundle-3";
    public const string AuditSchema = "sts2.annotator/source-session-audit-3";
    public const string TypeId = "source-session-bundle-v3";
    public const string FrozenBasis = "native_prefix_frozen";
    public const string UnprovenBasis = "unproven";
    public const string OrderUnprovenReason = "source_prefix_capture_order_unproven";

    // The public native publication/capture profile is unchanged. These are
    // Source-only metadata facts, never model features or action authority.
    public const string PublicationProfileId = SourceSessionContractV2.PublicationProfileId;
    public const string PublicationProfileDefinitionSha256 = SourceSessionContractV2.PublicationProfileDefinitionSha256;

    public static ulong Ordinal(string value) => SourceSessionContract.Index(new("source-input-prefix", value));
    public static void Validate(SourceInputOrderV3 order)
    {
        if (Ordinal(order.InputPrefixOrdinal) == 0
            || order.BasisOrder.Status is not (FrozenBasis or UnprovenBasis)
            || order.BasisOrder.ReasonCode != (order.BasisOrder.Status == UnprovenBasis ? OrderUnprovenReason : null))
            throw new InvalidDataException("source_input_order_invalid");
    }
}

/// <summary>Original native input admission ordinal and native acquisition seal; stream sequence remains durable order.</summary>
public sealed record SourceInputOrderV3(string InputPrefixOrdinal, SourceBasisOrderV3 BasisOrder);
public sealed record SourceBasisOrderV3(string Status, string? ReasonCode);

/// <summary>Original last-issued input cut, acquired by the existing filesystem-free Source metadata owner.</summary>
public sealed record SourceInputFenceV3(string AfterInputOrdinal);
