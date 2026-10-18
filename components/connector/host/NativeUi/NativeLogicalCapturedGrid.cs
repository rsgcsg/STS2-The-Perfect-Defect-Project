using System;
using System.Collections.Generic;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.NativeUi;

/// <summary>One capture-local acquisition result. Attempted with Grid=null is
/// a frozen result, never permission to acquire a later state as backfill.</summary>
internal sealed record NativeLogicalGridCaptureResult(
    bool Attempted, NativeLogicalCapturedGrid? Grid, IReadOnlyList<string> Missing)
{
    internal static NativeLogicalGridCaptureResult NotAttempted { get; } = new(false, null, Array.Empty<string>());
}

internal enum NativeLogicalGridInputKind { Toggle, Inspect, Control }

/// <summary>One existing native input, shared by PE bindings and the native
/// frame. Exact original operands stay process-local; this is not a registry.</summary>
internal sealed record NativeLogicalGridInputSpec(
    string Key, string NativeLeafKey, string RequestEntityId,
    string NativeVerb, string PublicVerb, string Stage, string Label, string SubjectEntityId,
    NativeLogicalGridInputKind Kind,
    [property: JsonIgnore] NativeLogicalGridState State,
    [property: JsonIgnore] CardModel? Card = null,
    [property: JsonIgnore] bool? ExpectedSelected = null,
    [property: JsonIgnore] NButton? Control = null)
{
    internal string NativeCallbackVerb => Kind != NativeLogicalGridInputKind.Control ? NativeVerb : NativeVerb switch
    {
        "open_preview" or "confirm" or "confirm_selection" => "confirm",
        "cancel_preview" or "cancel_selection" => "cancel",
        "toggle_upgrade_view" => "open",
        _ => throw new InvalidOperationException("Unregistered native selector control.")
    };
    internal NativeInputResult Dispatch() => Kind switch
    {
        NativeLogicalGridInputKind.Toggle when Card != null && ExpectedSelected is bool selected => State.Toggle(Card, selected),
        NativeLogicalGridInputKind.Inspect when Card != null => State.Inspect(Card),
        NativeLogicalGridInputKind.Control when Control != null => State.Click(NativeVerb, Control),
        _ => NativeInputResult.Rejected("native_logical_grid_input_spec_invalid", "The exact captured native grid input is incomplete.")
    };
}

internal sealed record NativeLogicalGridCardDisplay(VisibleCard Card, string DisplayContext);

/// <summary>Only the explicit public-basis input profile uses this full logical
/// surface. Its facts, details and input specs were frozen by the early capture.
/// Old holder-window surfaces retain their existing types and JSON.</summary>
internal sealed record NativePublicBasisGridSurface(
    string Kind, string Stage, string ScreenEntityId, string RequestEntityId,
    IReadOnlyList<VisibleCard> Cards) : ILiveSurface
{
    [JsonIgnore] internal JsonObject PublicSurface { get; init; } = new();
    [JsonIgnore] internal IReadOnlyList<NativeLogicalGridCardDisplay> CardDisplays { get; init; } = Array.Empty<NativeLogicalGridCardDisplay>();
    [JsonIgnore] internal IReadOnlyList<PlayerEnvironmentReferent> Referents { get; init; } = Array.Empty<PlayerEnvironmentReferent>();
    [JsonIgnore] internal IReadOnlyList<NativeLogicalGridInputSpec> Inputs { get; init; } = Array.Empty<NativeLogicalGridInputSpec>();
    [JsonIgnore] internal IReadOnlyList<string> Missing { get; init; } = Array.Empty<string>();
}

/// <summary>No native getters are needed to compose either projection from
/// this value. State is proof/revalidation only; display copies receive no IDs.</summary>
internal sealed record NativeLogicalCapturedGrid(
    [property: JsonIgnore] NativeLogicalGridState State,
    string OwnerEntityId, string RequestEntityId,
    JsonObject NativeSurface,
    IReadOnlyList<PlayerEnvironmentReferent> NativeReferents,
    IReadOnlyList<NativeLogicalGridInputSpec> Inputs,
    IReadOnlyList<string> NativeMissing,
    string? Prompt,
    NativePublicBasisGridSurface NeutralSurface)
{
    internal IReadOnlyList<VisibleCard> FullCards => NeutralSurface.Cards;
    internal IReadOnlyList<string> NeutralMissing => NeutralSurface.Missing;
}
