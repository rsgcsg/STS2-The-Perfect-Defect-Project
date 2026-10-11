using System.Text.Json;

namespace STS2HumanAnnotator.Core;

/// <summary>Narrow version adapter; both formats use one admission, persistence and audit engine.</summary>
internal sealed record SourceSessionWireFormat(int Version)
{
    internal bool Ordered => Version == 3;
    internal string Schema(string family) => "sts2.annotator/" + family + "-" + Version;
    internal string ProfileId => "native-logical-source-v" + Version;
    internal string? Fence(ulong ordinal) => Ordered ? ordinal.ToString(System.Globalization.CultureInfo.InvariantCulture) : null;
    internal static SourceSessionWireFormat ForVersion(int version) => version is 2 or 3 ? new(version)
        : throw new InvalidDataException("source_format_unsupported");
    internal static SourceSessionWireFormat ForProfile(SourceCaptureProfileV2 profile) =>
        profile.ProfileId == SourceSessionContractV2.ProfileId ? ForVersion(2)
        : profile.ProfileId == SourceSessionContractV3.ProfileId ? ForVersion(3)
        : throw new InvalidDataException("source_profile_invalid");
    internal static bool IsOrderMember(Type type, string name) =>
        name == "after_input_ordinal" && (type == typeof(SourceAttachmentEpochV2)
            || type == typeof(SourceSegmentV2) || type == typeof(SourceBoundaryV2)
            || type == typeof(SourceNativeSealV2) || type == typeof(SourcePausedIntervalV2)
            || type == typeof(SourceFinalDrainV2))
        || name == "through_input_ordinal" && type == typeof(SourcePausedIntervalV2)
        || name is "input_prefix_ordinal" or "basis_order" && type == typeof(SourceNativeInputWitnessV2);
    internal HashSet<string> Fields(Type type) => type.GetProperties(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public)
        .Select(x => JsonNamingPolicy.SnakeCaseLower.ConvertName(x.Name))
        .Where(name => Ordered || !IsOrderMember(type, name)).ToHashSet(StringComparer.Ordinal);
}
