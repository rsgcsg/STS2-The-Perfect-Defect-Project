using System;
using System.Text.Json.Serialization;

namespace STS2Connector.PlayerEnvironment.Protocol;

/// <summary>Byte retention of the public text-v2 current cursor. No action authority.</summary>
public static class SealedObservationContract
{
    public const string ReadProfile = "text-menu-v2-sealed-1";
    public const string Schema = "sts2.player-environment/sealed-observation-1";
    public const string ChunkSchema = "sts2.player-environment/sealed-observation-chunk-1";
    public const string ReleaseSchema = "sts2.player-environment/sealed-observation-release-1";
    public const string CapabilitiesSchema = "sts2.player-environment/sealed-observation-capabilities-1";
    public const string Route = "/api/player-environment/sealed-observation";
    public const int MaxCapsuleBytes = 8 * 1024 * 1024;
    public const int MaxRetainedBytes = 64 * 1024 * 1024;
    public const int MaxCapsules = 32;
    public const int TtlMs = 120_000;
    public const int DefaultChunkBytes = 64 * 1024;
    public const int MinChunkBytes = 1024;
    public const int MaxChunkBytes = 1024 * 1024;
}

public sealed record SealedObservationCapabilities(
    string Schema, string ReadProfile, string InputProfile, string CaptureSchema,
    string ChunkSchema, string ReleaseSchema, string CurrentRoute, string ReadRoute,
    string ReleaseRoute, int MaxCapsuleBytes, int MaxRetainedBytes, int MaxCapsules,
    int TtlMs, int DefaultChunkBytes, int MinChunkBytes, int MaxChunkBytes,
    bool CreatesMutationAuthority, string Scope);

public sealed record SealedObservationCapture(
    string Schema, string ReadProfile, string InputProfile, string CaptureId,
    string SourceSnapshotId, PlayerEnvironmentSessionReference Session,
    string GenerationId,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? GameContinuityId,
    DateTimeOffset CapturedAt, DateTimeOffset ExpiresAt, int TotalBytes,
    string Sha256, string FirstCursor, long CaptureOrdinal);

public sealed record SealedObservationChunk(
    string Schema, string CaptureId, string Sha256, int Offset, int TotalBytes,
    string DataBase64,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? NextCursor,
    bool End);

public sealed record SealedObservationReleaseRequest(string? CaptureId);
public sealed record SealedObservationRelease(string Schema, string CaptureId, bool Released);
