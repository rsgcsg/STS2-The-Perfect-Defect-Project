using System;
using System.Collections.Generic;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace STS2Connector.PlayerEnvironment.Protocol;

/// <summary>
/// The fair-player environment boundary shared by Live UI and future hosts.
/// Host lifecycle, rewards, strategy and privileged simulation are outside it.
/// </summary>
public static class PlayerEnvironmentContract
{
    public const string ProtocolVersion = "1.0.0";
    public const string EnvironmentId = "sts2_player_environment";
    public const string EnvironmentName = "STS2 Player Environment";
    public const string SnapshotSchema = "sts2.player-environment/snapshot-1";
    public const string ActionSchema = "sts2.player-environment/action-1";
    public const string ReceiptSchema = "sts2.player-environment/receipt-1";
    public const string ReadSchema = "sts2.player-environment/read-1";
    public const string ControlSchema = "sts2.player-environment/control-1";
    public const string NativePageEvidenceSchema =
        "sts2.player-environment/native-page-evidence-1";
    public const string NativePageEvidenceProfile = "native_pages.v1";
    public const string OrdinaryRewardPageProfile = "ordinary-reward-page-v1";
    public const string OrdinaryRewardSnapshotSchema =
        "sts2.player-environment/ordinary-reward-page-snapshot-1";
    public const string RewardPotionPageProfile = "ordinary-reward-potion-page-v2";
    public const string RewardPotionSnapshotSchema =
        "sts2.player-environment/ordinary-reward-potion-page-snapshot-2";

    public static string ReadContentSchema(string kind) =>
        $"sts2.player-environment/read/{kind}-1";
}

public sealed record PlayerEnvironmentHostIdentity(
    string Id,
    string Name,
    string Version,
    string RuntimeInstanceId,
    string HostKind,
    PlayerEnvironmentImplementationIdentity Implementation);

public sealed record PlayerEnvironmentImplementationIdentity(
    string? SourceRevision,
    string? ModuleVersionId,
    string? ArtifactSha256);

public sealed record PlayerEnvironmentCompatibility(
    string Status,
    bool ObservationAllowed,
    string Detail);

public sealed record PlayerEnvironmentModset(
    string Status,
    string Fingerprint,
    string Scope,
    IReadOnlyList<string> LoadedModIds,
    string Detail);

public sealed record PlayerEnvironmentGameIdentity(
    string? Version,
    string? Commit,
    string? Branch,
    int? MainAssemblyHash,
    PlayerEnvironmentCompatibility Compatibility,
    PlayerEnvironmentModset Modset);

public sealed record PlayerEnvironmentSessionReference(
    string RuntimeInstanceId,
    string EnvironmentFingerprint);

public sealed record PlayerEnvironmentInformationPolicy(
    string Id,
    string Scope,
    bool IncludesHiddenInformation,
    string UnknownFieldBehavior);

public sealed record PlayerEnvironmentControlPolicy(
    int RecommendedRenewalMs);

public sealed record PlayerEnvironmentCapabilitiesResponse(
    string ProtocolVersion,
    string SnapshotSchema,
    string ActionSchema,
    string ReceiptSchema,
    string ControlSchema,
    string Status,
    PlayerEnvironmentHostIdentity Host,
    PlayerEnvironmentGameIdentity Game,
    string EnvironmentFingerprint,
    IReadOnlyList<string> Verbs,
    bool SnapshotBound,
    bool SingleController,
    bool ExecutionAvailable,
    PlayerEnvironmentControlPolicy Control,
    IReadOnlyList<PlayerEnvironmentEvidenceProfile> EvidenceProfiles,
    IReadOnlyList<string> NonClaims)
{
    public string? InputProfile { get; init; }
}

public sealed record PlayerEnvironmentEvidenceProfile(
    string Id,
    bool Enabled,
    IReadOnlyList<string> SupportedKinds,
    bool SnapshotBound,
    bool RuntimeBound,
    bool DefaultInConsumerFlow,
    bool CreatesMutationAuthority,
    bool EntersActionLedger);

public sealed record PlayerEnvironmentNativePageOpenRequest(
    string? Profile,
    string? Kind,
    string? ExpectedSnapshotId,
    string? ExpectedRuntimeInstanceId);

public sealed record PlayerEnvironmentNativePageReturnRequest(
    string? Profile,
    string? ExpectedRuntimeInstanceId);

public sealed record PlayerEnvironmentNativePageOwner(
    string ContextKind,
    string InteractionKind,
    string NativeOwner);

public sealed record PlayerEnvironmentNativePageRead(
    string NativePageType,
    string ReadKind,
    int VisibleReferentCount,
    IReadOnlyList<string> VisibleReferentIds,
    string ContentSchema,
    JsonNode Content,
    PlayerEnvironmentCompleteness Completeness,
    IReadOnlyList<string> Evidence);

public sealed record PlayerEnvironmentNativePageResponse(
    string ProtocolVersion,
    string Schema,
    string SessionId,
    string Profile,
    string Kind,
    string Phase,
    string ExpectedSnapshotId,
    string PreSnapshotId,
    string? OpenedSnapshotId,
    string? PostSnapshotId,
    string ExpectedRuntimeInstanceId,
    string ObservedRuntimeInstanceId,
    PlayerEnvironmentNativePageOwner PreOwner,
    PlayerEnvironmentNativePageOwner CurrentOwner,
    PlayerEnvironmentNativePageOwner? PostOwner,
    PlayerEnvironmentNativePageRead? Page,
    PlayerEnvironmentHostIdentity Host,
    PlayerEnvironmentGameIdentity Game,
    bool CreatesMutationAuthority,
    bool EntersActionLedger,
    string? ErrorCode,
    string? Detail);

public sealed record PlayerEnvironmentClientRegistrationRequest(
    string? ClientInstanceId,
    string? ProductId,
    string? ProductName,
    string? ProductVersion);

public sealed record PlayerEnvironmentControllerLeaseRequest(
    string? ClientSessionId,
    string? ControllerLeaseId,
    long? ControllerGeneration);

public sealed record PlayerEnvironmentClient(
    string ClientSessionId,
    string ClientInstanceId,
    string ProductId,
    string ProductName,
    string ProductVersion,
    DateTimeOffset RegisteredAt,
    DateTimeOffset LastSeenAt);

public sealed record PlayerEnvironmentControllerLease(
    string Status,
    string ControllerLeaseId,
    long ControllerGeneration,
    string ClientSessionId,
    DateTimeOffset AcquiredAt,
    DateTimeOffset ExpiresAt);

public sealed record PlayerEnvironmentClientRegistrationResponse(
    string ProtocolVersion,
    string Schema,
    string RuntimeInstanceId,
    PlayerEnvironmentClient Client,
    PlayerEnvironmentControllerLease? Controller);

public sealed record PlayerEnvironmentControlSnapshot(
    string ProtocolVersion,
    string Schema,
    string RuntimeInstanceId,
    IReadOnlyList<PlayerEnvironmentClient> Clients,
    PlayerEnvironmentControllerLease? Controller);

public sealed record PlayerEnvironmentControllerLeaseResponse(
    string ProtocolVersion,
    string Schema,
    string RuntimeInstanceId,
    string Status,
    string Detail,
    PlayerEnvironmentClient? Client,
    PlayerEnvironmentControllerLease? Controller);

/// <summary>
/// The one current player interaction scope. A Live Host normally derives this
/// from the active UI owner; a Headless host may derive it from a decision
/// point without fabricating a UI node or control.
/// </summary>
public sealed record PlayerEnvironmentInteraction(
    string InteractionId,
    string Kind,
    string Stage,
    string? Prompt,
    string ContentSchema,
    PlayerEnvironmentInteractionContent Content,
    IReadOnlyList<PlayerEnvironmentInteractionCapability> Capabilities);

/// <summary>
/// The tagged, player-visible interaction payload. Surface and context may
/// evolve independently, but both must identify their current semantic kind.
/// </summary>
public sealed record PlayerEnvironmentInteractionContent(
    JsonNode Surface,
    JsonNode Context);

/// <summary>
/// Strategy-free grammar for an input currently accepted by this interaction.
/// It describes roles, not executable operand combinations. Exact bindings stay
/// private to the host and are exposed separately through a finite projection.
/// </summary>
public sealed record PlayerEnvironmentInteractionCapability(
    string Verb,
    string? SubjectRole,
    IReadOnlyList<PlayerEnvironmentCapabilityArgument> Arguments,
    string AvailabilityBasis);

public sealed record PlayerEnvironmentCapabilityArgument(
    string Role,
    bool Required);

public sealed record PlayerEnvironmentContent(
    string ContentSchema,
    JsonNode Content);

public sealed record PlayerEnvironmentReferentState(
    bool Visible,
    bool? Enabled,
    bool? Selected,
    bool? Focused,
    string ObservationBasis);

/// <summary>
/// A stable player-visible object or control in the current snapshot. This is
/// an information identity, not an authorization object. Exact native operands
/// remain private to the host implementation.
/// </summary>
public sealed record PlayerEnvironmentReferent(
    string ReferentId,
    string Role,
    string Kind,
    string? Label,
    PlayerEnvironmentReferentState State,
    string? PropertiesSchema,
    JsonNode? Properties);

public sealed record PlayerEnvironmentBoundActionArgument(
    string Role,
    string ReferentId);

public sealed record PlayerEnvironmentBoundAction(
    string BoundActionId,
    string Verb,
    string InteractionId,
    string? SubjectReferentId,
    IReadOnlyList<PlayerEnvironmentBoundActionArgument> Arguments,
    string Label);

/// <summary>
/// A deterministic finite consumer projection over the current C-local
/// execution bindings. Truncated projections never grant action authority.
/// </summary>
public sealed record PlayerEnvironmentBoundActionProjection(
    string Schema,
    string Status,
    int MaterializedCount,
    long TotalCount,
    int Limit,
    string OrderingSemantics,
    IReadOnlyList<PlayerEnvironmentBoundAction> Actions);

public sealed record PlayerEnvironmentReadOpportunity(
    string ReadId,
    string Kind,
    string? TargetReferentId,
    string ContentSchema,
    string VisibilityBasis,
    bool SnapshotBound,
    string OrderingSemantics,
    IReadOnlyList<string> HiddenByPolicy);

public sealed record PlayerEnvironmentCompleteness(
    string Status,
    string VisibleInformation,
    string InteractionDiscovery,
    IReadOnlyList<string> Missing,
    IReadOnlyList<string> HiddenByPolicy);

public sealed record PlayerEnvironmentSnapshot(
    string ProtocolVersion,
    string Schema,
    string SnapshotId,
    long Sequence,
    DateTimeOffset ObservedAt,
    string Status,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] PlayerEnvironmentContent? Persistent,
    PlayerEnvironmentInteraction Interaction,
    IReadOnlyList<PlayerEnvironmentReferent> Referents,
    PlayerEnvironmentBoundActionProjection BoundActions,
    IReadOnlyList<PlayerEnvironmentReadOpportunity> Reads,
    PlayerEnvironmentCompleteness Completeness,
    PlayerEnvironmentSessionReference Session,
    PlayerEnvironmentInformationPolicy InformationPolicy)
{
    public string? InputProfile { get; init; }
}

public sealed record PlayerEnvironmentReadResponse(
    string ProtocolVersion,
    string Schema,
    string ReadId,
    string ExpectedSnapshotId,
    string ObservedSnapshotId,
    DateTimeOffset ObservedAt,
    string Kind,
    string? TargetReferentId,
    string VisibilityBasis,
    string OrderingSemantics,
    string ContentSchema,
    JsonNode Content,
    PlayerEnvironmentCompleteness Completeness,
    PlayerEnvironmentSessionReference Session,
    PlayerEnvironmentInformationPolicy InformationPolicy);

public sealed record PlayerEnvironmentActionRequest(
    string? RequestId,
    string? ExpectedSnapshotId,
    string? BoundActionId,
    string? ClientSessionId,
    string? ControllerLeaseId,
    long? ControllerGeneration,
    string? InputProfile = null);

public sealed record PlayerEnvironmentActionSummary(
    string BoundActionId,
    string Verb,
    string? SubjectReferentId,
    IReadOnlyList<PlayerEnvironmentBoundActionArgument> Arguments);

public sealed record PlayerEnvironmentRetryPolicy(
    bool Allowed,
    string Reason);

public sealed record PlayerEnvironmentAttribution(
    string RuntimeInstanceId,
    string ClientSessionId,
    string ClientInstanceId,
    string ProductId,
    string ProductName,
    string ProductVersion,
    string ControllerLeaseId,
    long ControllerGeneration);

/// <summary>
/// Reports native input delivery. Successor is an immediate post-delivery
/// observation when available; it is not business completion or a canonical
/// causal next-decision state.
/// </summary>
public sealed record PlayerEnvironmentActionReceipt(
    string ProtocolVersion,
    string Schema,
    string RequestId,
    string Delivery,
    PlayerEnvironmentActionSummary Action,
    string? ReasonCode,
    string? Detail,
    PlayerEnvironmentRetryPolicy Retry,
    PlayerEnvironmentSnapshot? Successor)
{
    public PlayerEnvironmentAttribution? Attribution { get; init; }
    public string? InputProfile { get; init; }
}
