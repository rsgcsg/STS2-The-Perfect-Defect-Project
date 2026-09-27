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
    PlayerEnvironmentSnapshot Page, string OwnerKey, IReadOnlyList<TextMenuLeaf> Leaves);

internal sealed record TextMenuChoice(TextMenuAction Action, TextMenuLeaf? Leaf, string? TargetCursor);
internal sealed record TextMenuProjection(
    TextMenuSnapshot Snapshot, IReadOnlyDictionary<string, TextMenuChoice> Choices);
