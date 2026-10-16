using System;
using System.Collections.Generic;
using System.Linq;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.LiveHost;

internal sealed record PlayerVisibilityProjection(
    PlayerVisibilityState Visibility,
    IReadOnlyList<PlayerReadCatalogEntry> ReadCatalog);

/// <summary>
/// Read-only visibility inventory. It describes what the current player may
/// inspect; it never grants action authority by itself.
/// </summary>
internal static class PlayerVisibilityCatalog
{
    public static PlayerVisibilityProjection Build(
        LiveObservation draft,
        bool activeRunSharedStateAvailable,
        bool shopCatalogSourceAvailable,
        bool aggregateCurrentPublicReads = false)
    {
        var entries = new List<PlayerReadCatalogEntry>();
        foreach (string kind in CurrentReadKinds(
                     draft,
                     activeRunSharedStateAvailable,
                     shopCatalogSourceAvailable, aggregateCurrentPublicReads))
        {
            if (kind == PlayerVisibleReadBuilder.MapKind)
                entries.Add(new PlayerReadCatalogEntry(kind, "active_run", "player_visible",
                    "player_openable_current_generated_map", true, false, "native_map_topology", "low",
                    new[] { "route_planning" }, Array.Empty<string>()));
            else if (kind == PlayerVisibleReadBuilder.RunDeckKind && activeRunSharedStateAvailable)
            {
                entries.Add(new PlayerReadCatalogEntry(
                    kind,
                    "active_run",
                    "player_visible",
                    "player_openable_run_deck_view",
                    StateBound: true,
                    CreatesActionAuthority: false,
                    "unordered_multiset",
                    "medium",
                    new[] { "card_acquisition", "deck_upgrade", "deck_removal", "deck_transform", "shop" },
                    Array.Empty<string>()));
            }
            else if (kind == PlayerVisibleReadBuilder.CombatPilesKind && draft.Context.Kind == "combat")
            {
                entries.Add(new PlayerReadCatalogEntry(
                    kind,
                    "current_combat",
                    "player_visible",
                    "player_openable_draw_discard_exhaust_pile_views",
                    StateBound: true,
                    CreatesActionAuthority: false,
                    "per_zone",
                    "medium",
                    new[] { "combat_planning", "discard_sensitive_effect", "exhaust_sensitive_effect" },
                    PlayerVisibleReadBuilder.HiddenByPolicyFor(kind)));
            }
            else if (kind == PlayerVisibleReadBuilder.ShopCatalogKind
                     && draft.Context.Kind == "shop"
                     && shopCatalogSourceAvailable)
            {
                entries.Add(new PlayerReadCatalogEntry(
                    kind,
                    "current_shop",
                    "player_visible",
                    "player_openable_current_merchant_inventory",
                    StateBound: true,
                    CreatesActionAuthority: false,
                    "fixed_ui_slots",
                    "low",
                    new[] { "shop_planning", "purchase_comparison", "leave_shop_decision" },
                    Array.Empty<string>()));
            }
        }

        string[] hidden = draft.Context.Kind == "combat"
            ? new[] { "hidden_rng", "draw_pile_true_order", "future_enemy_moves", "future_rewards", "future_events" }
            : new[] { "hidden_rng", "future_rewards", "future_events" };
        string[] missing = draft.Completeness.Missing
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        var visibility = new PlayerVisibilityState(
            $"{draft.Context.Kind}.{draft.Surface.Kind}.v1",
            draft.Completeness.Missing.Count == 0 ? "complete" : "partial",
            "partial_catalog",
            entries.Select(entry => entry.Kind).ToArray(),
            Array.Empty<string>(),
            hidden,
            missing,
            "fail_closed");
        return new PlayerVisibilityProjection(visibility, entries);
    }

    private static IReadOnlyList<string> CurrentReadKinds(
        LiveObservation draft,
        bool activeRunSharedStateAvailable,
        bool shopCatalogSourceAvailable,
        bool aggregateCurrentPublicReads = false)
    {
        var kinds = new List<string>();
        if (activeRunSharedStateAvailable)
        {
            kinds.Add(PlayerVisibleReadBuilder.RunDeckKind);
            // The game-over screen has no openable map (native entry is unsupported there).
            if (aggregateCurrentPublicReads && draft.Context.Kind is not ("terminal" or "game_over"))
                kinds.Add(PlayerVisibleReadBuilder.MapKind);
        }
        if (draft.Context.Kind == "combat")
            kinds.Add(PlayerVisibleReadBuilder.CombatPilesKind);
        if (draft.Context.Kind == "shop" && shopCatalogSourceAvailable)
            kinds.Add(PlayerVisibleReadBuilder.ShopCatalogKind);
        return kinds;
    }
}
