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
                : inputProfile == PlayerEnvironmentContract.RewardPotionPageProfile
                    ? PlayerEnvironmentContract.RewardPotionSnapshotSchema
                    : PlayerEnvironmentContract.OrdinaryRewardSnapshotSchema,
            PlayerEnvironmentContract.ActionSchema,
            PlayerEnvironmentContract.ReceiptSchema,
            PlayerEnvironmentContract.ControlSchema,
            "implemented",
            ToHostIdentity(host),
            ToGameIdentity(game),
            ToSessionReference(host, game).EnvironmentFingerprint,
            new[]
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
            }.Concat(inputProfile == PlayerEnvironmentContract.RewardPotionPageProfile
                ? new[] {
                    "This profile covers ordinary reward controls and exact potion navigation only; other top-bar controls and later targeting pages are outside its action scope."
                }
                : Array.Empty<string>()).ToArray()) { InputProfile = inputProfile };
    }

    internal static bool IsSupportedInputProfile(string? inputProfile) =>
        inputProfile == null
        || string.Equals(inputProfile, PlayerEnvironmentContract.OrdinaryRewardPageProfile,
            StringComparison.Ordinal)
        || string.Equals(inputProfile, PlayerEnvironmentContract.RewardPotionPageProfile,
            StringComparison.Ordinal);

    public static PlayerEnvironmentSnapshot Observe(string? inputProfile = null) =>
        BuildSnapshot(inputProfile: inputProfile).Snapshot;

}
