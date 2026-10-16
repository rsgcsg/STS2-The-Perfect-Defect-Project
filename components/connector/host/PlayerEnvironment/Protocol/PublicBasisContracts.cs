using System.Collections.Generic;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace STS2Connector.PlayerEnvironment.Protocol;

/// <summary>Current public source facts, independent of an Agent protocol or policy.
/// Complete capture of this inventory is not qualification of a consumer's information requirements.</summary>
public static class PublicBasisContract
{
    public const string Profile = "public-basis-1";
    public const string Revision = "1";
    public const string Schema = "sts2.player-environment/public-basis-1";
    public const string ReadProfile = "public-basis-sealed-1";
    public const string CaptureSchema = "sts2.player-environment/public-basis-handle-1";
    public const string ChunkSchema = "sts2.player-environment/public-basis-chunk-1";
    public const string ReleaseSchema = "sts2.player-environment/public-basis-release-1";
    public const string CapabilitiesSchema = "sts2.player-environment/public-basis-capabilities-1";
    public const string Route = "/api/player-environment/public-basis";
    public const string Scope = "current_public_snapshot_and_advertised_read_instances";
    public const int MaxReadInstances = 10_000;
}

public sealed record PublicBasisRead(
    string ReadId, string Kind,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] JsonObject? Response,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? Error);

public sealed record PublicBasisObservation(
    string Schema, string Revision, string CaptureScope, string CaptureStatus,
    JsonObject Snapshot, IReadOnlyList<PublicBasisRead> Reads, IReadOnlyList<string> Missing);
