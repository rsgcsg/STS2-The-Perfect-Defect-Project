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
        string expectedSnapshotId, DateTimeOffset? capturedAt = null, bool includePublicCardDetails = false,
        IReadOnlyDictionary<string, CapturedCardDetail>? capturedPublicCardDetails = null)
    {
        PlayerEnvironmentReadOpportunity opportunity = resolution.Opportunity!;
        if (opportunity.TargetReferentId != null)
            return ReadLinkedDetail(snapshot, opportunity, expectedSnapshotId, capturedAt, includePublicCardDetails, capturedPublicCardDetails);
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
        string expectedSnapshotId, DateTimeOffset? capturedAt = null, bool includePublicCardDetails = false,
        IReadOnlyDictionary<string, CapturedCardDetail>? capturedPublicCardDetails = null)
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
        CapturedCardDetail? captured = includePublicCardDetails
            ? capturedPublicCardDetails != null
                ? capturedPublicCardDetails.GetValueOrDefault(opportunity.ReadId)
                : CapturedCardDetails(snapshot.HostObservation.Surface, snapshot.HostObservation.Context, snapshot.ReadBuilds)
                .SingleOrDefault(value => string.Equals("read:surface_card:" + value.DetailKey, opportunity.ReadId, StringComparison.Ordinal)
                    && string.Equals(value.Card.EntityId, referentId, StringComparison.Ordinal))
            : null;
        if (captured?.Card.EntityId != referentId) captured = null;
        VisibleCard? card = includePublicCardDetails ? captured?.Card
            : VisibleSurfaceCards(snapshot.HostObservation.Surface).FirstOrDefault(value =>
                string.Equals(value.EntityId, referentId, StringComparison.Ordinal));
        if (card == null)
        {
            return new PlayerEnvironmentReadResult(
                null,
                "read_binding_failed",
                "The current card detail could not be rebuilt from the same UI Surface.");
        }

        JsonObject content = (JsonObject)(JsonSerializer.SerializeToNode(card, ConnectorMod._jsonOptions) ?? new JsonObject());
        string[] missing = Array.Empty<string>();
        if (includePublicCardDetails)
        {
            content["public_details_revision"] = "1";
            content["display_context"] = captured!.DisplayContext;
            content["parent_read_id"] = captured.ParentReadId;
            content["display_owner_entity_id"] = captured.OwnerEntityId;
            if (card.CurrentPublicDetails is { } details)
            {
                var detailMissing = new List<string>(card.CurrentPublicDetailsMissing);
                foreach (string field in new[] { "keywords", "card_previews" })
                    if (details[field] is JsonArray values) content[field] = values.DeepClone();
                    else detailMissing.Add("current_native_public_card_" + field + "_not_captured");
                if (details.ContainsKey("upgrade_preview"))
                {
                    content["public_details_revision"] = "2";
                    if (details["upgrade_preview"] is JsonObject preview) content["upgrade_preview"] = preview.DeepClone();
                    else detailMissing.Add("current_native_public_card_upgrade_preview_not_captured");
                }
                missing = detailMissing.Distinct(StringComparer.Ordinal).ToArray();
            }
            else missing = new[] { "current_native_public_card_details_not_captured" };
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
                content,
                new PlayerEnvironmentCompleteness(
                    missing.Length == 0 ? "complete" : "partial",
                    "complete_current_player_visible_card_detail",
                    "read_only",
                    missing,
                    Array.Empty<string>()),
                observation.Session,
                observation.InformationPolicy),
            null,
            null);
    }

    internal sealed record CapturedCardDetail(VisibleCard Card, string DisplayContext, string? ParentReadId, string? OwnerEntityId)
    {
        internal string DetailKey => "public-card-" + StableIdentityHash.Object(new { Card.EntityId, DisplayContext, ParentReadId, OwnerEntityId });
    }

    // Scoped to one already frozen Snapshot. Rebuilding and serializing the full
    // inventory for each individual Read made a full P1 freeze quadratic.
    internal static IReadOnlyDictionary<string, CapturedCardDetail> CapturePublicCardDetailIndex(SnapshotBuildResult snapshot) =>
        CapturedCardDetails(snapshot.HostObservation.Surface, snapshot.HostObservation.Context, snapshot.ReadBuilds)
            .ToDictionary(value => "read:surface_card:" + value.DetailKey, StringComparer.Ordinal);

    // This inventory consumes the already captured DTOs only. It cannot resolve
    // a native model, navigate, rerender, or obtain a second version of the run.
    private static IEnumerable<CapturedCardDetail> CapturedCardDetails(ILiveSurface surface, ILiveContext? context,
        IReadOnlyDictionary<string, PlayerReadBuildResult>? readBuilds)
    {
        var values = new List<CapturedCardDetail>();
        void Add(IEnumerable<VisibleCard> cards, string display, string? parent, string? owner)
        { values.AddRange(cards.Select(card => new CapturedCardDetail(card, display, parent, owner))); }
        switch (surface)
        {
            case NativePublicBasisGridSurface grid:
                foreach (NativeLogicalGridCardDisplay display in grid.CardDisplays)
                    Add(new[] { display.Card }, display.DisplayContext, null, grid.ScreenEntityId);
                break;
            case RewardClaimSurface rewards:
                foreach (VisibleReward reward in rewards.Rewards)
                    Add(reward.CardOptions ?? Array.Empty<VisibleCard>(), "reward_candidate", null, reward.GroupId ?? reward.EntityId);
                break;
            case CardRewardSelectionSurface { ParentStatus: "bound" } rewardCards:
                Add(rewardCards.Cards, "reward_candidate", null, rewardCards.ParentGroupId);
                break;
            case CardBundleSelectionSurface bundles:
                foreach (VisibleCardBundle bundle in bundles.Bundles) Add(bundle.Cards, "bundle_preview", null, bundle.EntityId);
                break;
            case NativeDeckUpgradeSelectionSurface upgrade:
                Add(upgrade.Cards, "selector_before_upgrade", null, upgrade.ScreenEntityId);
                Add(upgrade.PreviewCards, "selector_after_upgrade", null, upgrade.ScreenEntityId);
                break;
            case ShopInventorySurface shop:
                foreach (VisibleShopCardOffer offer in shop.Cards)
                    if (offer.Card != null) Add(new[] { offer.Card }, "shop_offer", null, offer.EntityId);
                break;
            default:
                Add(VisibleSurfaceCards(surface), "surface:" + surface.Kind, null, null);
                break;
        }
        if (context is CombatLiveContext combat) Add(combat.Player.Hand, "hand", null, combat.Player.PlayerEntityId);
        if (readBuilds != null)
            foreach (var entry in readBuilds.OrderBy(entry => entry.Key, StringComparer.Ordinal))
                switch (entry.Value.Draft?.Content)
                {
                    case RunDeckReadContent deck:
                        Add(deck.Cards, "deck", "read:" + entry.Key, null); break;
                    case CombatPilesReadContent piles:
                        foreach (CombatPileReadZone zone in piles.Zones) Add(zone.Cards, zone.Zone, "read:" + entry.Key, null);
                        break;
                    case ShopCatalogReadContent shop:
                        foreach (VisibleShopCardOffer offer in shop.Cards)
                            if (offer.Card != null) Add(new[] { offer.Card }, "shop_offer", "read:" + entry.Key, offer.EntityId);
                        break;
                }
        return values.GroupBy(value => value.DetailKey, StringComparer.Ordinal).Select(group =>
            {
                CapturedCardDetail first = group.First();
                string Signature(CapturedCardDetail value) => JsonSerializer.Serialize(new { value.Card,
                    Details = value.Card.CurrentPublicDetails, Missing = value.Card.CurrentPublicDetailsMissing }, ConnectorMod._jsonOptions);
                string expected = Signature(first);
                if (group.Skip(1).Any(value => Signature(value) != expected))
                    throw new SealedObservationException("capture_failed", "The same public card display identity has conflicting captured facts.");
                return first;
            }).OrderBy(value => value.DetailKey, StringComparer.Ordinal).ToArray();
    }

    internal static IReadOnlyList<PlayerEnvironmentLinkedDetailCatalogEntry> BuildLinkedDetailCatalog(
        ILiveSurface surface, ILiveContext? context = null,
        IReadOnlyDictionary<string, PlayerReadBuildResult>? readBuilds = null, bool includePublicCardDetails = false) =>
        includePublicCardDetails
            ? CapturedCardDetails(surface, context, readBuilds).Select(value => new PlayerEnvironmentLinkedDetailCatalogEntry(
                "surface_card", value.Card.EntityId, "normal_player_visible_surface_card", value.DetailKey, value.DisplayContext, value.ParentReadId)).ToArray()
            : VisibleSurfaceCards(surface).GroupBy(card => card.EntityId, StringComparer.Ordinal)
                .Select(group => new PlayerEnvironmentLinkedDetailCatalogEntry("surface_card", group.Key, "normal_player_visible_surface_card"))
                .OrderBy(entry => entry.EntityId, StringComparer.Ordinal).ToArray();

    private static IEnumerable<VisibleCard> VisibleSurfaceCards(ILiveSurface surface) =>
        surface switch
        {
            NativePublicBasisGridSurface value => value.Cards,
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
