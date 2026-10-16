using System;
using System.Collections.Generic;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace STS2Connector.PlayerEnvironment.Protocol;

/// <summary>A read-only capture foundation, not the full P1 Agent environment.
/// NativeBasis preserves the legacy schema and native action semantics.</summary>
public static class P1CaptureContract
{
    public const string Profile = "p1-atomic-capture-c1-1";
    public const string Revision = "c1-1";
    public const string Scope = "ordinary_ready_combat_base_and_read_instances";
    public const string ReadProfile = "p1-atomic-capture-c1-sealed-1";
    public const string ObservationSchema = "sts2.player-environment/p1-atomic-capture-c1-1";
    public const string CaptureSchema = "sts2.player-environment/p1-atomic-capture-handle-c1-1";
    public const string ChunkSchema = "sts2.player-environment/p1-atomic-capture-chunk-c1-1";
    public const string ReleaseSchema = "sts2.player-environment/p1-atomic-capture-release-c1-1";
    public const string CapabilitiesSchema = "sts2.player-environment/p1-atomic-capture-capabilities-c1-1";
    public const string RecordSchema = "sts2.player-environment/p1-atomic-capture-record-c1-1";
    public const string Route = "/api/player-environment/p1-capture";
    public const int MaxReadInstances = 10_000;
    public const string NativeCatalogSemantics = "legacy_native_basis_not_p1_semantic_catalog";
}

public sealed record P1CaptureCapabilities(
    string Schema, string InputProfile, string Revision, string SupportedCaptureScope,
    bool AgentAdmissible, bool CreatesMutationAuthority, string NativeCatalogSemantics,
    int MaxReadInstances, IReadOnlyList<string> RequiredFullP1Missing, SealedObservationCapabilities Retention);

public sealed record P1CaptureCoverage(
    string RequirementId, bool Required, string Availability,
    IReadOnlyList<string> ReadInstanceIds, IReadOnlyList<string> Missing);

public sealed record P1CapturedRead(
    string InstanceId, string Kind,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? SourceReadId,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? TargetEntityId,
    string DisplayContext,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? ParentReadInstanceId,
    string Availability, string ContentSchema,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] JsonNode? Content,
    IReadOnlyList<string> Missing);

/// <summary>Detached public values only. P1 status is diagnostic/unsupported until
/// the complete information, semantic catalog and commitment contract closes.</summary>
public sealed record P1CapturedObservation(
    string Schema, string InputProfile, string Revision, string CaptureScope,
    string ScopeStatus, string P1Status, bool AgentAdmissible, bool CreatesMutationAuthority,
    string NativeCatalogSemantics, string OccurrenceSemantics,
    string SourceSnapshotId, DateTimeOffset CapturedAt,
    PlayerEnvironmentSessionReference Session, JsonObject NativeBasis,
    IReadOnlyList<P1CapturedRead> Reads, IReadOnlyList<P1CaptureCoverage> Coverage);
