using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Host-private current bindings. Capture is read-only; Dispatch is
/// called only after fresh capture, exact snapshot and controller admission.</summary>
internal sealed record TextMenuNativeWitnessBinding(
    [property: JsonIgnore] object Owner,
    [property: JsonIgnore] object? Subject,
    [property: JsonIgnore] IReadOnlyDictionary<string, object> Arguments);

internal sealed record TextMenuLeaf(
    string Key, string Group, string Verb, string Label,
    string? SubjectReferentId,
    IReadOnlyList<PlayerEnvironmentBoundActionArgument> Arguments,
    [property: JsonIgnore] Func<NativeInputResult> Dispatch,
    [property: JsonIgnore] TextMenuNativeWitnessBinding? NativeWitness = null);

internal sealed record TextMenuFrame(
    PlayerEnvironmentSnapshot Page, string OwnerKey, IReadOnlyList<TextMenuLeaf> Leaves)
{
    [JsonIgnore]
    internal string? GameContinuityId { get; init; }

    // Only the opt-in v2 capture populates these host-private native semantic
    // play pairs. The v1 native holder/target path ignores them entirely.
    [JsonIgnore]
    internal IReadOnlyList<TextMenuLeaf> CardPlays { get; init; } = Array.Empty<TextMenuLeaf>();

    [JsonIgnore]
    internal bool CardPlayCatalogComplete { get; init; }
}

internal sealed record TextMenuChoice(TextMenuAction Action, TextMenuLeaf? Leaf, string? TargetCursor);
internal sealed record TextMenuProjection(
    TextMenuSnapshot Snapshot, IReadOnlyDictionary<string, TextMenuChoice> Choices);
