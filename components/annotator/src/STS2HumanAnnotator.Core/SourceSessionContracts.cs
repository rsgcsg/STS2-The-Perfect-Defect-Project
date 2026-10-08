using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

/// <summary>Source accounting and public immutable bytes; never Human or causal proof.</summary>
public static class SourceSessionContract
{
    public const string ProfileId = "native-logical-source-v1";
    public const string ManifestSchema = "sts2.annotator/source-session-manifest-1";
    public const string ProfileSchema = "sts2.annotator/source-capture-profile-1";
    public const string SegmentSchema = "sts2.annotator/source-segment-1";
    public const string BoundarySchema = "sts2.annotator/source-boundary-1";
    public const string ObservationSchema = "sts2.annotator/public-observation-1";
    public const string InputSchema = "sts2.annotator/native-input-witness-1";
    public const string CloseSchema = "sts2.annotator/source-session-close-1";
    public const string BundleSchema = "sts2.annotator/source-session-bundle-1";
    public const string AuditSchema = "sts2.annotator/source-session-audit-1";
    public static readonly string[] StreamFiles =
    {
        "source-segments.jsonl", "source-boundaries.jsonl",
        "public-observations.jsonl", "native-input-witnesses.jsonl"
    };
    public static readonly string[] NonClaims =
    {
        "not_machine_proof_of_human_origin", "not_native_coverage_qualified",
        "not_causal_transition_proof", "not_research_admission", "not_g2_v1_approved"
    };

    public static void Validate(SourceDeclaration? source)
    {
        if (source == null || source.SourceKind is not ("declared_human" or "agent_native_ui" or "agent_protocol" or "unknown")
            || source.MachineVerifiable)
            throw new InvalidDataException("source_declaration_invalid");
        Identifier(source.ActorId); Identifier(source.DeclarationId);
    }

    public static void Identifier(string? value)
    {
        if (value == null || value.Length is < 1 or > 128 || value is "." or ".."
            || !value.All(c => c is >= 'a' and <= 'z' or >= 'A' and <= 'Z'
                or >= '0' and <= '9' or '-' or '_' or '.'))
            throw new InvalidDataException("source_identifier_invalid");
    }

    public static ulong Index(SourceClockReference clock)
    {
        Identifier(clock.StreamGeneration);
        if (!ulong.TryParse(clock.PublicationIndex, NumberStyles.None, CultureInfo.InvariantCulture, out ulong index)
            || clock.PublicationIndex != index.ToString(CultureInfo.InvariantCulture))
            throw new InvalidDataException("source_clock_invalid");
        return index;
    }

    public static bool IsSha256(string? value) => value != null && value.Length == 64
        && value.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f');
    public static string Sha256(ReadOnlySpan<byte> bytes) =>
        Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    public static string ProfileDigest(SourceCaptureProfile profile) => Sha256(SourceSessionJson.Bytes(profile));
    public static void ValidateEnvironment(RecorderEnvironmentIdentity environment)
    {
        Identifier(environment.RuntimeInstanceId); Identifier(environment.EnvironmentFingerprint);
        if (!IsSha256(environment.Game.MainAssemblySha256))
            throw new InvalidDataException("source_environment_game_identity_invalid");
        foreach (ExactArtifactIdentity artifact in new[] { environment.Connector, environment.Annotator })
            if (!IsSha256(artifact.Sha256) || !IsSha256(artifact.SourceDigestSha256)
                || artifact.SourceRevision.Length != 40 || !artifact.SourceRevision.All(Uri.IsHexDigit))
                throw new InvalidDataException("source_environment_artifact_invalid");
    }
}

public sealed record SourceDeclaration(
    string SourceKind, string ActorId, string DeclarationId, bool MachineVerifiable = false);
public sealed record SourceClockReference(string StreamGeneration, string PublicationIndex);
public sealed record SourceSeamCoverage(string Version, string Coverage);
public sealed record SourceSessionLimits(
    int MaxCaptureBytes = 64 * 1024 * 1024, long MaxPayloadBytes = 512L * 1024 * 1024,
    int MaxRowBytes = 1024 * 1024, int MaxRowsPerStream = 65536,
    long MaxBytesPerStream = 64L * 1024 * 1024, int MaxSegments = 256, int MaxPendingInputs = 128);
public sealed record SourceCaptureProfile(
    string Schema, string ProfileId, string InputProfile, string ScopeId,
    IReadOnlyList<string> EagerScope, IReadOnlyDictionary<string, SourceSeamCoverage> SeamCoverage,
    SourceSessionLimits Limits, IReadOnlyList<string> NonClaims);
public sealed record SourceBridgeContext(
    SourceCaptureProfile Profile, RecorderEnvironmentIdentity Environment, SourceClockReference StartingClock);

/// <summary>A passive provider bridge. It cannot execute input or furnish native operands.</summary>
public interface ISourceRecordingBridge
{
    // Context and initial reservation only. No observer runs before Activate.
    ISourceRecordingAttachment Attach();
}
public interface ISourceRecordingAttachment : IDisposable
{
    SourceBridgeContext Context { get; }
    // Activates once and replays from Context.StartingClock, including the initial reserved observation.
    void Activate(Action<SourceObservationPacket> observer);
    SourceClockReference ReadBoundaryClock();
    // True only after every reserved occurrence through this boundary was delivered or explicitly gapped.
    bool IsDrainedThrough(SourceClockReference boundary);
}

public sealed record FrozenPublicCapture(
    string CaptureId, string SnapshotId, string ScopeId, string StreamGeneration,
    DateTimeOffset CapturedAt, byte[] Bytes, string Sha256);
public sealed record FrozenPublicCatalog(
    string CatalogRef, string SnapshotId, string ScopeId, string StreamGeneration,
    int TotalCount, string StructuralDigest, byte[] Bytes, string PayloadSha256);
public sealed record PublicCaptureReference(
    string CaptureId, string SnapshotId, string ScopeId, string StreamGeneration,
    DateTimeOffset CapturedAt, long ByteCount, string Sha256, string PayloadRef);
public sealed record PublicCatalogReference(
    string CatalogRef, string SnapshotId, string ScopeId, string StreamGeneration,
    int TotalCount, string StructuralDigest, long ByteCount, string PayloadSha256, string PayloadRef);
public sealed record SourceActionArgument(string Role, string ReferentId);
public sealed record SourcePublicAction(
    string ActionId, string Kind, string Verb, string Label, string? SubjectReferentId,
    IReadOnlyList<SourceActionArgument> Arguments, string EffectDomain);
public sealed record SourceObservationPacket(
    SourceClockReference Clock, string ScopeId, string SourceSeam, string SourceIndex, string Phase,
    string? SnapshotId, string? OwnerOccurrence, string Completeness,
    FrozenPublicCapture? Capture, FrozenPublicCatalog? Catalog,
    string? MissingReason = null, string? GapAfterIndex = null);
public sealed record SourceSegment(
    string Schema, long Sequence, string SessionId, string TimelineId,
    string SegmentId, string? PreviousSegmentId, SourceDeclaration Declaration,
    SourceClockReference BoundaryClock, DateTimeOffset RecordedAt);
public sealed record SourceBoundary(
    string Schema, long Sequence, string SessionId, string TimelineId, string Kind,
    string SegmentId, SourceClockReference Clock, SourceClockReference? GapAfter,
    string? GapReason, DateTimeOffset RecordedAt);
public sealed record SourcePublicObservation(
    string Schema, long Sequence, string SessionId, string TimelineId, string SegmentId,
    SourceClockReference Clock, string ScopeId, string SourceSeam, string SourceIndex, string Phase,
    string? SnapshotId, string? OwnerOccurrence, string Completeness,
    PublicCaptureReference? Capture, PublicCatalogReference? Catalog,
    string? MissingReason, string? GapAfterIndex);
public sealed record SourceInputOutcome(
    string MappingStatus, int MatchCount, SourcePublicAction? SelectedAction,
    string NativeMechanism, string Delivery, string? ReasonCode = null);
public sealed record SourceNativeInputWitness(
    string Schema, long Sequence, string SessionId, string TimelineId, string InputId,
    string SegmentId, SourceClockReference PreClock, PublicCaptureReference? PreCapture,
    PublicCatalogReference? Catalog, SourceInputOutcome Outcome, DateTimeOffset RecordedAt);

/// <summary>The store-issued token keeps a declaration fixed across delayed completion.</summary>
public sealed class SourceInputScope
{
    internal SourceInputScope(object owner, string inputId, string segmentId,
        SourceClockReference preClock, PublicCaptureReference? pre, PublicCatalogReference? catalog)
    {
        Owner = owner; InputId = inputId; SegmentId = segmentId;
        PreClock = preClock; PreCapture = pre; Catalog = catalog;
    }
    internal object Owner { get; }
    public string InputId { get; }
    public string SegmentId { get; }
    public SourceClockReference PreClock { get; }
    public PublicCaptureReference? PreCapture { get; }
    public PublicCatalogReference? Catalog { get; }
    internal bool BasisBound { get; init; }
}

public sealed record SourceSessionStatus(
    string SegmentId, SourceDeclaration Declaration, long Observations, long Inputs,
    int PendingInputs, long Gaps, bool AccountingComplete, string? Error);
public sealed record SourceSessionAuditResult(
    string Schema, string Status, string SessionId, long ObservationCount, long InputCount,
    long GapCount, IReadOnlyList<string> SourceKinds, IReadOnlyList<string> Errors,
    IReadOnlyList<string> NonClaims);
public sealed record SourceSessionBundleResult(
    string Status, string BundleDirectory, string BundleContentId, string SessionId,
    long ObservationCount, long InputCount, string ChecksumsSha256);

/// <summary>Independent integrity checks over the public catalogue, not native legality.</summary>
public static class SourceCatalogCodec
{
    private static readonly HashSet<string> Fields = new(StringComparer.Ordinal)
    { "action_id", "kind", "verb", "label", "subject_referent_id", "arguments", "effect_domain" };

    public static IReadOnlyList<SourcePublicAction> Decode(byte[] bytes)
    {
        using JsonDocument document = JsonDocument.Parse(bytes);
        if (document.RootElement.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException("source_catalog_array_required");
        var actions = new List<SourcePublicAction>();
        foreach (JsonElement item in document.RootElement.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object
                || item.EnumerateObject().Count() != Fields.Count
                || item.EnumerateObject().Select(x => x.Name).ToHashSet(StringComparer.Ordinal).Count != Fields.Count
                || item.EnumerateObject().Any(x => !Fields.Contains(x.Name)))
                throw new InvalidDataException("source_catalog_fields_invalid");
            string Read(string field) => item.GetProperty(field).GetString()
                ?? throw new InvalidDataException("source_catalog_string_required");
            var args = new List<SourceActionArgument>();
            JsonElement arguments = item.GetProperty("arguments");
            if (arguments.ValueKind != JsonValueKind.Array)
                throw new InvalidDataException("source_catalog_arguments_invalid");
            foreach (JsonElement arg in arguments.EnumerateArray())
            {
                if (arg.ValueKind != JsonValueKind.Object
                    || arg.EnumerateObject().Count() != 2
                    || !arg.TryGetProperty("role", out JsonElement role)
                    || !arg.TryGetProperty("referent_id", out JsonElement referent))
                    throw new InvalidDataException("source_catalog_argument_invalid");
                args.Add(new(role.GetString() ?? throw new InvalidDataException("role_required"),
                    referent.GetString() ?? throw new InvalidDataException("referent_required")));
            }
            JsonElement subject = item.GetProperty("subject_referent_id");
            actions.Add(new(Read("action_id"), Read("kind"), Read("verb"), Read("label"),
                subject.ValueKind == JsonValueKind.Null ? null : subject.GetString(),
                args, Read("effect_domain")));
        }
        _ = Digest(actions);
        return actions;
    }

    public static string Digest(IReadOnlyList<SourcePublicAction> actions)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        hash.AppendData(Encoding.UTF8.GetBytes("sts2.native-logical.catalog.v1\0"));
        void Count(uint value)
        {
            Span<byte> bytes = stackalloc byte[4];
            System.Buffers.Binary.BinaryPrimitives.WriteUInt32BigEndian(bytes, value);
            hash.AppendData(bytes);
        }
        void Text(string value)
        {
            if (value == null) throw new InvalidDataException("source_catalog_string_required");
            for (int index = 0; index < value.Length; index++)
                if (char.IsSurrogate(value[index]))
                {
                    if (!char.IsHighSurrogate(value[index]) || index + 1 >= value.Length
                        || !char.IsLowSurrogate(value[++index]))
                        throw new InvalidDataException("source_catalog_unicode_invalid");
                }
            byte[] bytes = Encoding.UTF8.GetBytes(value);
            Count(checked((uint)bytes.Length)); hash.AppendData(bytes);
        }
        Count(checked((uint)actions.Count));
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (SourcePublicAction action in actions)
        {
            if (!ids.Add(action.ActionId) || action.Kind != "native_input" || action.EffectDomain != "native_input"
                || action.Arguments.Select(x => x.Role).Distinct(StringComparer.Ordinal).Count() != action.Arguments.Count)
                throw new InvalidDataException("source_catalog_identity_invalid");
            Text(action.ActionId); Text(action.Kind); Text(action.Verb); Text(action.Label);
            hash.AppendData(new[] { action.SubjectReferentId == null ? (byte)0 : (byte)1 });
            if (action.SubjectReferentId != null) Text(action.SubjectReferentId);
            Count(checked((uint)action.Arguments.Count));
            foreach (SourceActionArgument argument in action.Arguments)
            { Text(argument.Role); Text(argument.ReferentId); }
            Text(action.EffectDomain);
        }
        return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }
}
