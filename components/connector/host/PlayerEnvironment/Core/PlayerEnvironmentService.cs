using STS2Connector.Authority;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.NativeUi;

namespace STS2Connector.PlayerEnvironment;

internal sealed record SnapshotBuildResult(
    PlayerEnvironmentSnapshot Snapshot,
    LiveObservation HostObservation,
    IReadOnlyDictionary<string, PlayerEnvironmentNativeBinding> Bindings,
    IReadOnlyDictionary<string, PlayerReadBuildResult> ReadBuilds);

internal sealed record PlayerEnvironmentNativeBinding(
    NativeUiBoundAction NativeAction,
    IReadOnlyDictionary<string, string> ExactOperands);

internal sealed record PlayerEnvironmentReadResult(
    PlayerEnvironmentReadResponse? Read,
    string? ErrorCode,
    string? Detail);

internal sealed record PlayerEnvironmentLinkedDetailCatalogEntry(
    string Kind,
    string EntityId,
    string VisibilityBasis);

internal sealed record BoundActionProjectionResult(
    PlayerEnvironmentBoundActionProjection Projection,
    IReadOnlyDictionary<string, PlayerEnvironmentNativeBinding> Bindings);

/// <summary>
/// Thin composition facade for Player Environment observation, reads,
/// projection, input delivery and control.
/// </summary>
internal static partial class PlayerEnvironmentService
{
    private const int MaxBoundActions = 512;
    private static NativeEntityRegistry Entities => NativeUiRuntime.Entities;
    private static readonly RewardPageSnapshotIdentity RewardPageIdentity = new();
    private static readonly ConcurrentDictionary<string, string> RequestFingerprints =
        new(StringComparer.Ordinal);
    private static readonly ConcurrentDictionary<string, PlayerEnvironmentActionReceipt> Receipts =
        new(StringComparer.Ordinal);
    private static readonly object SubmissionGate = new();
    private static readonly Lazy<PlayerEnvironmentNativePageSession> NativePageEvidenceLazy =
        new(() => new PlayerEnvironmentNativePageSession(
            new LiveNativePageEvidenceHost(
                requiredReadKinds => BuildSnapshot(
                    suppressNativePageEvidence: false,
                    requiredReadKinds: requiredReadKinds),
                Entities)));
    private static PlayerEnvironmentNativePageSession NativePageEvidence =>
        NativePageEvidenceLazy.Value;

    public static PlayerEnvironmentCapabilitiesResponse GetCapabilities() =>
        GetCapabilities(null);

    public static PlayerEnvironmentCapabilitiesResponse GetCapabilities(string? inputProfile)
    {
        if (!IsSupportedInputProfile(inputProfile))
            throw new ArgumentException("Unsupported Player Environment input profile.", nameof(inputProfile));
        GameBuildIdentity game = EnvironmentIdentityRuntime.ReadGame();
        LiveHostIdentity host = EnvironmentIdentityRuntime.HostIdentity();
        return new PlayerEnvironmentCapabilitiesResponse(
            PlayerEnvironmentContract.ProtocolVersion,
            inputProfile == null
                ? PlayerEnvironmentContract.SnapshotSchema
                : inputProfile == TextMenuContract.Profile
                    ? TextMenuContract.SnapshotSchema
                : inputProfile == PlayerEnvironmentContract.RewardPotionPageProfile
                    ? PlayerEnvironmentContract.RewardPotionSnapshotSchema
                    : PlayerEnvironmentContract.OrdinaryRewardSnapshotSchema,
            PlayerEnvironmentContract.ActionSchema,
            inputProfile == TextMenuContract.Profile
                ? TextMenuContract.ResultSchema : PlayerEnvironmentContract.ReceiptSchema,
            PlayerEnvironmentContract.ControlSchema,
            "implemented",
            ToHostIdentity(host),
            ToGameIdentity(game),
            ToSessionReference(host, game).EnvironmentFingerprint,
            inputProfile == TextMenuContract.Profile ? new[]
            {
                "activate", "select", "deselect", "confirm", "cancel", "play", "target",
                "use", "end_turn", "skip", "open", "close", "purchase", "navigate",
                "begin_card_play", "cancel_card_play", "focus_target", "confirm_target", "confirm_card",
                "open_potion_popup", "choose_potion_use", "discard_potion", "close_potion_popup",
                "select_potion_target", "cancel_potion_target",
                "claim_reward", "claim_linked_reward", "proceed_rewards", "skip_rewards",
                "open_information", "open_relic_inspect", "open_relic_tips", "open_card_tips",
                "open_power_tips", "open_intent_tips", "open_orb_tips", "open_topbar_tips", "back",
                "show_relic_tips", "show_card_tips", "show_power_tips", "show_intent_tips",
                "show_orb_tips", "show_topbar_tips", "open_run_deck", "open_native_map", "inspect_relic",
                "open_combat_draw_pile", "open_combat_discard_pile", "open_combat_exhaust_pile",
                "return_native_information", "return_native_map", "return_relic_inspect", "return_native_tips",
                "inspect_deck_card", "inspect_bundle_card", "return_card_inspect", "previous_inspect_card", "next_inspect_card",
                "toggle_card_upgrade_preview", "previous_relic", "next_relic"
            } : new[]
            {
                "activate", "select", "deselect", "confirm", "cancel", "play",
                "target", "use", "end_turn", "skip", "open", "close"
            },
            SnapshotBound: true,
            SingleController: true,
            ExecutionAvailable: EnvironmentIdentityRuntime.ExecutionAvailable(game),
            new PlayerEnvironmentControlPolicy(
                MutationControlRuntime.Capability().RecommendedRenewalMs),
            new[] { NativePageEvidence.Capability() },
            new[]
            {
                "Delivered means native UI input was delivered, not that a business transaction settled.",
                "D annotations are outside the C observation and never authorize bound actions.",
                "Build or install does not prove this artifact is loaded or Live-exercised."
            }.Concat(inputProfile == TextMenuContract.Profile
                ? new[] {
                    "Text navigation changes only the presentation cursor; it never reports native delivery.",
                    "The current menu is complete at its cursor; deeper information leaves remain reachable through explicit navigation.",
                    "Only in-run pages are in scope. No start, load, character or process actions are granted."
                }
                : inputProfile == PlayerEnvironmentContract.RewardPotionPageProfile
                ? new[] {
                    "This profile covers ordinary reward controls and exact potion navigation only; other top-bar controls and later targeting pages are outside its action scope."
                }
                : Array.Empty<string>()).ToArray()) { InputProfile = inputProfile };
    }

    internal static bool IsSupportedInputProfile(string? inputProfile) =>
        inputProfile == null
        || string.Equals(inputProfile, TextMenuContract.Profile, StringComparison.Ordinal)
        || string.Equals(inputProfile, PlayerEnvironmentContract.OrdinaryRewardPageProfile,
            StringComparison.Ordinal)
        || string.Equals(inputProfile, PlayerEnvironmentContract.RewardPotionPageProfile,
            StringComparison.Ordinal);

    public static PlayerEnvironmentSnapshot Observe(string? inputProfile = null) =>
        BuildSnapshot(inputProfile: inputProfile).Snapshot;

}
