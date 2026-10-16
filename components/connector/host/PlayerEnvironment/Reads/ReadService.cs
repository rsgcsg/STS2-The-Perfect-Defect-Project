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

internal sealed record PlayerEnvironmentReadResolution(
    PlayerEnvironmentReadOpportunity? Opportunity,
    PlayerReadBuildResult? Build,
    string? ErrorCode,
    string? Detail);

internal static partial class PlayerEnvironmentService
{
    public static PlayerEnvironmentReadResult Read(
        string readId,
        string expectedSnapshotId)
    {
        SnapshotBuildResult snapshot =
            BuildSnapshot(requiredReadKinds: RequiredReadKindsFor(readId));
        PlayerEnvironmentSnapshot observation = snapshot.Snapshot;
        PlayerEnvironmentReadResolution resolution = ResolveReadMaterialization(
            observation,
            readId,
            expectedSnapshotId,
            snapshot.ReadBuilds);
        if (resolution.ErrorCode != null)
            return new PlayerEnvironmentReadResult(null, resolution.ErrorCode, resolution.Detail);

        return BuildResolvedRead(snapshot, resolution, expectedSnapshotId);
    }

    internal static PlayerEnvironmentReadResult BuildResolvedRead(
        SnapshotBuildResult snapshot,
        PlayerEnvironmentReadResolution resolution,
        string expectedSnapshotId, DateTimeOffset? capturedAt = null)
    {
        PlayerEnvironmentReadOpportunity opportunity = resolution.Opportunity!;
        if (opportunity.TargetReferentId != null)
            return ReadLinkedDetail(snapshot, opportunity, expectedSnapshotId, capturedAt);
        PlayerReadDraft draft = resolution.Build!.Draft!;
        return new PlayerEnvironmentReadResult(
            new PlayerEnvironmentReadResponse(
                PlayerEnvironmentContract.ProtocolVersion,
                PlayerEnvironmentContract.ReadSchema,
                opportunity.ReadId,
                expectedSnapshotId,
                snapshot.Snapshot.SnapshotId,
                capturedAt ?? DateTimeOffset.UtcNow,
                draft.Kind,
                null,
                opportunity.VisibilityBasis,
                draft.OrderingSemantics,
                PlayerEnvironmentContract.ReadContentSchema(draft.Kind),
                JsonSerializer.SerializeToNode(draft.Content, draft.Content.GetType(), ConnectorMod._jsonOptions) ?? new JsonObject(),
                ToCompleteness(draft.Completeness, opportunity.HiddenByPolicy),
                snapshot.Snapshot.Session,
                snapshot.Snapshot.InformationPolicy),
            null,
            null);
    }

    internal static PlayerEnvironmentReadResolution ResolveReadMaterialization(
        PlayerEnvironmentSnapshot observation,
        string readId,
        string expectedSnapshotId,
        IReadOnlyDictionary<string, PlayerReadBuildResult> readBuilds)
    {
        if (!string.Equals(observation.SnapshotId, expectedSnapshotId, StringComparison.Ordinal))
        {
            return new PlayerEnvironmentReadResolution(
                null,
                null,
                "stale_state",
                "The expected snapshot is no longer current; obtain a fresh Player Environment observation.");
        }

        PlayerEnvironmentReadOpportunity? opportunity = observation.Reads.SingleOrDefault(entry =>
            string.Equals(entry.ReadId, readId, StringComparison.Ordinal));
        if (opportunity == null)
        {
            return new PlayerEnvironmentReadResolution(
                null,
                null,
                "read_not_available",
                "This read is not in the current player-visible read catalog.");
        }

        if (opportunity.TargetReferentId != null)
            return new PlayerEnvironmentReadResolution(opportunity, null, null, null);

        if (!readBuilds.TryGetValue(opportunity.Kind, out PlayerReadBuildResult? build)
            || build == null)
        {
            return new PlayerEnvironmentReadResolution(
                opportunity,
                null,
                "read_materialization_missing",
                "The current authoritative snapshot did not materialize the advertised read.");
        }

        if (build.Draft == null)
        {
            return new PlayerEnvironmentReadResolution(
                opportunity,
                null,
                build.ErrorCode ?? "read_binding_failed",
                build.Detail ?? "The current authoritative snapshot could not materialize the advertised read.");
        }

        return new PlayerEnvironmentReadResolution(opportunity, build, null, null);
    }

    private static IReadOnlyCollection<string> RequiredReadKindsFor(string readId)
    {
        const string prefix = "read:";
        if (!readId.StartsWith(prefix, StringComparison.Ordinal))
            return Array.Empty<string>();

        string kind = readId[prefix.Length..];
        return kind.Length > 0 && !kind.Contains(':', StringComparison.Ordinal)
            ? new[] { kind }
            : Array.Empty<string>();
    }

    private static PlayerEnvironmentReadResult ReadLinkedDetail(
        SnapshotBuildResult snapshot,
        PlayerEnvironmentReadOpportunity opportunity,
        string expectedSnapshotId, DateTimeOffset? capturedAt = null)
    {
        PlayerEnvironmentSnapshot observation = snapshot.Snapshot;
        if (!string.Equals(opportunity.Kind, "surface_card", StringComparison.Ordinal)
            || opportunity.TargetReferentId == null)
        {
            return new PlayerEnvironmentReadResult(
                null,
                "read_kind_not_implemented",
                "This current read kind has no bounded Live host implementation.");
        }
        string referentId = opportunity.TargetReferentId;
        VisibleCard? card = VisibleSurfaceCards(snapshot.HostObservation.Surface)
            .FirstOrDefault(value => string.Equals(
                value.EntityId,
                referentId,
                StringComparison.Ordinal));
        if (card == null)
        {
            return new PlayerEnvironmentReadResult(
                null,
                "read_binding_failed",
                "The current card detail could not be rebuilt from the same UI Surface.");
        }

        return new PlayerEnvironmentReadResult(
            new PlayerEnvironmentReadResponse(
                PlayerEnvironmentContract.ProtocolVersion,
                PlayerEnvironmentContract.ReadSchema,
                opportunity.ReadId,
                expectedSnapshotId,
                observation.SnapshotId,
                capturedAt ?? DateTimeOffset.UtcNow,
                opportunity.Kind,
                referentId,
                opportunity.VisibilityBasis,
                opportunity.OrderingSemantics,
                opportunity.ContentSchema,
                JsonSerializer.SerializeToNode(card, ConnectorMod._jsonOptions) ?? new JsonObject(),
                new PlayerEnvironmentCompleteness(
                    "complete",
                    "complete_current_player_visible_card_detail",
                    "read_only",
                    Array.Empty<string>(),
                    Array.Empty<string>()),
                observation.Session,
                observation.InformationPolicy),
            null,
            null);
    }

    internal static IReadOnlyList<PlayerEnvironmentLinkedDetailCatalogEntry>
        BuildLinkedDetailCatalog(ILiveSurface surface) =>
        VisibleSurfaceCards(surface)
            .GroupBy(card => card.EntityId, StringComparer.Ordinal)
            .Select(group => new PlayerEnvironmentLinkedDetailCatalogEntry(
                "surface_card",
                group.Key,
                "normal_player_visible_surface_card"))
            .OrderBy(entry => entry.EntityId, StringComparer.Ordinal)
            .ToArray();

    private static IEnumerable<VisibleCard> VisibleSurfaceCards(ILiveSurface surface) =>
        surface switch
        {
            NativeDeckUpgradeSelectionSurface value => value.Cards.Concat(value.PreviewCards),
            NativeDeckCardSelectionSurface value => value.Cards,
            NativeCombatPileSelectionSurface value => value.Cards,
            NativeGeneratedCardChoiceSurface value => value.Cards,
            NativeSimpleCardSelectionSurface value => value.Cards,
            DeckEnchantSelectionSurface value => value.Cards,
            DeckTransformSelectionSurface value => value.Cards,
            CombatHandCardSelectionSurface value => value.Cards,
            CardRewardSelectionSurface value => value.Cards,
            CardBundleSelectionSurface value => value.Bundles.SelectMany(bundle => bundle.Cards),
            _ => Array.Empty<VisibleCard>()
        };

    private static PlayerEnvironmentCompleteness ToCompleteness(
        StateCompleteness completeness,
        IReadOnlyList<string> hiddenByPolicy,
        string? forcedStatus = null) => new(
            forcedStatus ?? (completeness.Missing.Count == 0 ? "complete" : "partial"),
            completeness.PlayerVisibleSemantics,
            completeness.InteractionDiscovery,
            completeness.Missing,
            hiddenByPolicy);

    internal static PlayerEnvironmentCompleteness ToCompleteness(
        PlayerReadCompleteness completeness,
        IReadOnlyList<string> hiddenByPolicy) => new(
            completeness.Missing.Count == 0 ? "complete" : "partial",
            completeness.PlayerVisibleSemantics,
            "read_only",
            completeness.Missing,
            hiddenByPolicy);

}
