using System.Text.Json;

namespace STS2HumanAnnotator.Core;

/// <summary>Narrow version adapter; both formats use one admission, persistence and audit engine.</summary>
internal sealed record SourceSessionWireFormat(int Version)
{
    internal bool Ordered => Version >= 3;
    internal bool PublicBasis => Version >= 4;
    internal string Schema(string family) => "sts2.annotator/" + family + "-" + Version;
    internal string ProfileId => "native-logical-source-v" + Version;
    internal string? Fence(ulong ordinal) => Ordered ? ordinal.ToString(System.Globalization.CultureInfo.InvariantCulture) : null;
    internal static SourceSessionWireFormat ForVersion(int version) => version is 2 or 3 or 4 ? new(version)
        : throw new InvalidDataException("source_format_unsupported");
    internal static SourceSessionWireFormat ForProfile(SourceCaptureProfileV2 profile) =>
        profile.ProfileId == SourceSessionContractV2.ProfileId ? ForVersion(2)
        : profile.ProfileId == SourceSessionContractV3.ProfileId ? ForVersion(3)
        : profile.ProfileId == SourceSessionContractV4.ProfileId ? ForVersion(4)
        : throw new InvalidDataException("source_profile_invalid");
    internal static bool IsOrderMember(Type type, string name) =>
        name == "after_input_ordinal" && (type == typeof(SourceAttachmentEpochV2)
            || type == typeof(SourceSegmentV2) || type == typeof(SourceBoundaryV2)
            || type == typeof(SourceNativeSealV2) || type == typeof(SourcePausedIntervalV2)
            || type == typeof(SourceFinalDrainV2))
        || name == "through_input_ordinal" && type == typeof(SourcePausedIntervalV2)
        || name is "input_prefix_ordinal" or "basis_order" && type == typeof(SourceNativeInputWitnessV2);
    internal static bool IsPublicBasisMember(Type type, string name) =>
        name == "public_basis" && type == typeof(SourceCaptureProfileV2)
        || name == "captured_parts" && (type == typeof(SourcePublicObservationV2) || type == typeof(SourceNativeInputWitnessV2))
        || name == "native_facts" && type == typeof(SourceInputOutcomeV2);
    internal bool Includes(Type type, string name) =>
        // Mixed-run identity belongs only to the P1-native manifest extension.
        // It must neither become required nor be admitted in historical Source2/3/4.
        !(type == typeof(CurrentRecordingManifest) && name == "mixed_run_id") &&
        (Ordered || !IsOrderMember(type, name)) && (PublicBasis || !IsPublicBasisMember(type, name));
    internal HashSet<string> Fields(Type type) => type.GetProperties(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public)
        .Select(x => JsonNamingPolicy.SnakeCaseLower.ConvertName(x.Name))
        .Where(name => Includes(type, name)).ToHashSet(StringComparer.Ordinal);
}
