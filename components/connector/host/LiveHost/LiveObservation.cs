using System;
using System.Collections.Generic;
using STS2Connector.LiveHost.Contracts;
using System.Text.Json.Serialization;

namespace STS2Connector.LiveHost;

internal sealed record LiveObservation(
    string Signature,
    string Readiness,
    ILiveContext Context,
    ILiveSurface Surface,
    StateCompleteness Completeness,
    GameBuildIdentity Game,
    IReadOnlyList<string> Warnings)
{
    // Private capture evidence. Only the opted-in Player Environment projection
    // may turn a checked current-page effect into a public fact.
    [JsonIgnore]
    public RewardPageProfileFacts? RewardPageFacts { get; init; }
    [JsonIgnore]
    public RewardPotionProfileFacts? RewardPotionFacts { get; init; }
    [JsonIgnore]
    public object? PopupUnderlyingRewardOwner { get; init; }
    public InputOwnership InputOwnership { get; init; } = new(
        "current_ui_owned",
        Surface.Kind,
        "The exact current native UI owns this interaction.");

    public IReadOnlyList<HostDiagnostic> Diagnostics { get; init; } =
        Array.Empty<HostDiagnostic>();
}

internal sealed record RewardPageAlternativeEffect(string EntityId, string Effect);

internal sealed record RewardPageProfileFacts(
    bool Qualified,
    IReadOnlyList<RewardPageAlternativeEffect> AlternativeEffects)
{
    // Captured from the same current holders with the native reward display pile.
    // The legacy surface and its signature remain mode-neutral.
    public IReadOnlyList<VisibleCard> CurrentCards { get; init; } = Array.Empty<VisibleCard>();
}
