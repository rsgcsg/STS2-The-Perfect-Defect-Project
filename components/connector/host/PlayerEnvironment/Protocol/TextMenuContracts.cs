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
}

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
