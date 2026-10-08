using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

/// <summary>The reviewed target definition. Actual registration coverage is a separate Hub declaration.</summary>
internal static class NativeLogicalPublicationProfile
{
    internal const string ProfileId = "native-logical-publication-profile-v1";
    internal const string DefinitionSha256 = "c060cfd354c6702e10711e6329848836ec133f2117841750e317b6ab27b244cf";
    private const string ResourceName = "STS2Connector.NativeLogicalPublicationProfileV1";
    private sealed record WireDefinition(string Schema, string ProfileId, string InputProfile,
        string DeliveryMode, IReadOnlyList<string> EagerScope, IReadOnlyList<NativeLogicalSeamCoverage> RequiredSeams);
    private static readonly Lazy<WireDefinition> Definition = new(ReadDefinition);
    internal static IReadOnlyList<NativeLogicalSeamCoverage> RequiredCoverage => Definition.Value.RequiredSeams;

    private static WireDefinition ReadDefinition()
    {
        using Stream resource = typeof(NativeLogicalPublicationProfile).Assembly.GetManifestResourceStream(ResourceName)
            ?? throw new InvalidOperationException("The canonical publication profile resource is missing.");
        using var bytes = new MemoryStream();
        resource.CopyTo(bytes);
        if (bytes.Length > 65536 || NativeLogicalWire.Hash(bytes.GetBuffer().AsSpan(0, checked((int)bytes.Length))) != DefinitionSha256)
            throw new InvalidOperationException("The canonical publication profile resource changed.");
        WireDefinition value = JsonSerializer.Deserialize<WireDefinition>(bytes.GetBuffer().AsSpan(0, checked((int)bytes.Length)), NativeLogicalWire.Options)
            ?? throw new InvalidOperationException("The canonical publication profile is invalid.");
        if (value.Schema != "sts2.player-environment/native-logical-publication-profile-1"
            || value.ProfileId != ProfileId || value.InputProfile != NativeLogicalContract.Profile
            || value.DeliveryMode != "full_reference" || !value.EagerScope.SequenceEqual(NativeLogicalProjector.ScopeFields)
            || value.RequiredSeams.Count != 13
            || value.RequiredSeams.Select(s => s.SourceSeam).Distinct(StringComparer.Ordinal).Count() != value.RequiredSeams.Count
            || value.RequiredSeams.Any(s => s.Version != "1" || s.Coverage != "complete_at_seam"))
            throw new InvalidOperationException("The canonical publication profile target is invalid.");
        return value with
        {
            EagerScope = Array.AsReadOnly(value.EagerScope.ToArray()),
            RequiredSeams = Array.AsReadOnly(value.RequiredSeams.ToArray())
        };
    }

    internal static IReadOnlyList<NativeLogicalSeamCoverage> ValidateConfirmation(string profileId,
        string definitionSha256, IReadOnlyList<NativeLogicalSeamCoverage> confirmedCoverage)
    {
        if (profileId != ProfileId || definitionSha256 != DefinitionSha256)
            throw new NativeLogicalException("publication_profile_mismatch", "Composition must bind the exact reviewed publication definition.");
        var target = RequiredCoverage;
        if (confirmedCoverage is null || confirmedCoverage.Count != target.Count
            || confirmedCoverage.Any(s => s is null)
            || confirmedCoverage.Select(s => s.SourceSeam).Distinct(StringComparer.Ordinal).Count() != target.Count)
            throw new NativeLogicalException("publication_registration_incomplete", "Each required source seam must be confirmed once.");
        var confirmed = confirmedCoverage.ToDictionary(s => s.SourceSeam, StringComparer.Ordinal);
        if (target.Any(s => !confirmed.TryGetValue(s.SourceSeam, out var actual) || actual != s))
            throw new NativeLogicalException("publication_registration_incomplete", "Actual registration does not meet the exact required source seam target.");
        // The target file owns order; registration completion order is not a second profile definition.
        return target;
    }
}

internal sealed record NativeLogicalPublicationDeclaration(string StreamGeneration,
    string? PublicationProfileId, string? PublicationProfileDefinitionSha256,
    IReadOnlyList<NativeLogicalSeamCoverage> Coverage);
