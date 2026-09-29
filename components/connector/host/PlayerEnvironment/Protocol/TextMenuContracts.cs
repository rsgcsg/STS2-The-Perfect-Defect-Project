using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace STS2Connector.PlayerEnvironment.Protocol;

/// <summary>A current native page plus a bounded text presentation cursor.
/// Navigation does not claim a native input delivery or a game transition.</summary>
public static class TextMenuContract
{
    public const string Profile = "text-menu-v1";
    public const string SnapshotSchema = "sts2.player-environment/text-menu-snapshot-1";
    public const string ResultSchema = "sts2.player-environment/text-menu-action-result-1";
    public const string ObservationContextSchema =
        "sts2.player-environment/text-menu-observation-context-1";
}

/// <summary>Scheduling identity captured with an unchanged public text-menu page.
/// The opaque run token is not a model input, run-start witness or action authority.</summary>
public sealed record TextMenuObservationContext(
    string Schema, TextMenuSnapshot Snapshot,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? GameContinuityId);

public sealed record TextMenuCursor(string Cursor, long Revision, string NativeSnapshotId);

public sealed record TextMenuAction(
    string ActionId, string Kind, string Verb, string Label,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? SubjectReferentId,
    IReadOnlyList<PlayerEnvironmentBoundActionArgument> Arguments, string EffectDomain);

public sealed record TextMenuActionCatalog(
    string Status, int MaterializedCount, long TotalCount,
    string OrderingSemantics, IReadOnlyList<TextMenuAction> Actions);

public sealed record TextMenuSnapshot(
    string ProtocolVersion, string Schema, string InputProfile,
    string SnapshotId, long Sequence, DateTimeOffset ObservedAt, string Status,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] PlayerEnvironmentContent? Persistent,
    PlayerEnvironmentInteraction Interaction,
    IReadOnlyList<PlayerEnvironmentReferent> Referents,
    PlayerEnvironmentCompleteness Completeness,
    PlayerEnvironmentSessionReference Session,
    PlayerEnvironmentInformationPolicy InformationPolicy,
    TextMenuCursor Menu, TextMenuActionCatalog MenuActions);

public sealed record TextMenuActionResult(
    string ProtocolVersion, string Schema, string InputProfile,
    string RequestId, string Status,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? EffectDomain,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? NativeDelivery,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] TextMenuAction? Action,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? ReasonCode,
    string Detail, string Retry,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] TextMenuSnapshot? Successor,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] PlayerEnvironmentAttribution? Attribution);

/// <summary>Opt-in text-only card staging over exact native semantic play leaves.
/// Selection never means that STS2 has a held card or target focus.</summary>
public static class TextMenuV2Contract
{
    public const string Profile = "text-menu-v2";
    public const string SnapshotSchema = "sts2.player-environment/text-menu-snapshot-2";
    public const string ResultSchema = "sts2.player-environment/text-menu-action-result-2";
    public const string ObservationContextSchema =
        "sts2.player-environment/text-menu-observation-context-2";
}

public sealed record TextMenuV2Selection(string Role, string ReferentId);
public sealed record TextMenuV2Cursor(
    string Cursor, long Revision, string NativeSnapshotId,
    IReadOnlyList<TextMenuV2Selection> Selection);

public sealed record TextMenuV2Snapshot(
    string ProtocolVersion, string Schema, string InputProfile,
    string SnapshotId, long Sequence, DateTimeOffset ObservedAt, string Status,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] PlayerEnvironmentContent? Persistent,
    PlayerEnvironmentInteraction Interaction,
    IReadOnlyList<PlayerEnvironmentReferent> Referents,
    PlayerEnvironmentCompleteness Completeness,
    PlayerEnvironmentSessionReference Session,
    PlayerEnvironmentInformationPolicy InformationPolicy,
    TextMenuV2Cursor Menu, TextMenuActionCatalog MenuActions);

public sealed record TextMenuV2ObservationContext(
    string Schema, TextMenuV2Snapshot Snapshot,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? GameContinuityId);

public sealed record TextMenuV2ActionResult(
    string ProtocolVersion, string Schema, string InputProfile,
    string RequestId, string Status,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? EffectDomain,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? NativeDelivery,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] TextMenuAction? Action,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? ReasonCode,
    string Detail, string Retry,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] TextMenuV2Snapshot? Successor,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] PlayerEnvironmentAttribution? Attribution);
