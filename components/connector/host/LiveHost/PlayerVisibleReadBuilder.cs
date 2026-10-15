using STS2Connector.NativeUi;
using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.LiveHost;

internal sealed record PlayerReadDraft(
    string Kind,
    string VisibilityClass,
    string OrderingSemantics,
    IPlayerReadContent Content,
    PlayerReadCompleteness Completeness);

internal sealed record PlayerReadBuildResult(
    PlayerReadDraft? Draft,
    string? ErrorCode,
    string? Detail)
{
    public static PlayerReadBuildResult Success(PlayerReadDraft draft) =>
        new(draft, null, null);

    public static PlayerReadBuildResult Failure(string code, string detail) =>
        new(null, code, detail);
}

internal static class PlayerVisibleReadBuilder
{
    public const string RunDeckKind = "run_deck";
    public const string CombatPilesKind = "combat_piles";
    public const string ShopCatalogKind = "shop_catalog";

    internal static IReadOnlyList<string> HiddenByPolicyFor(string kind) =>
        string.Equals(kind, CombatPilesKind, StringComparison.Ordinal)
            ? new[] { "draw_pile_true_order" }
            : Array.Empty<string>();

    public static PlayerReadBuildResult Build(
        string kind,
        ILiveContext context,
        NativeEntityRegistry entities)
    {
        try
        {
            return kind switch
            {
                RunDeckKind => BuildRunDeck(entities),
                CombatPilesKind => BuildCombatPiles(context, entities),
                ShopCatalogKind => BuildShopCatalog(context, entities),
                _ => PlayerReadBuildResult.Failure(
                    "read_kind_not_implemented",
                    $"Player read kind '{kind}' is not implemented by this Host revision.")
            };
        }
        catch (Exception ex)
        {
            return PlayerReadBuildResult.Failure(
                "read_binding_failed",
                $"Player read binding failed closed with {ex.GetType().Name}.");
        }
    }

    private static PlayerReadBuildResult BuildRunDeck(NativeEntityRegistry entities)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        Player? player = runState == null ? null : LocalContext.GetMe(runState);
        if (player == null)
        {
            return PlayerReadBuildResult.Failure(
                "read_not_available",
                "Run deck reading requires a current local singleplayer run.");
        }

        VisibleCard[] cards = BuildCards(player.Deck.Cards, PileType.Deck, entities);
        var content = new RunDeckReadContent(RunDeckKind, cards.Length, cards);
        return PlayerReadBuildResult.Success(new PlayerReadDraft(
            RunDeckKind,
            "normal_player_read",
            "unordered_multiset",
            content,
            new PlayerReadCompleteness(
                "complete_for_player_run_deck_contents_without_semantic_order",
                new[]
                {
                    "NDeckViewScreen.ShowScreen(Player)",
                    "PileType.Deck.GetPile(Player).Cards",
                    "NDeckViewScreen player-selectable sorting controls",
                    "CardModel player-visible card and enchantment semantics"
                },
                Array.Empty<string>())));
    }

    private static PlayerReadBuildResult BuildCombatPiles(
        ILiveContext context,
        NativeEntityRegistry entities)
    {
        if (context is not CombatLiveContext)
        {
            return PlayerReadBuildResult.Failure(
                "read_scope_mismatch",
                "Combat pile reading is available only for the current combat context.");
        }

        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        Player? player = runState == null ? null : LocalContext.GetMe(runState);
        PlayerCombatState? combat = player?.PlayerCombatState;
        if (combat == null)
        {
            return PlayerReadBuildResult.Failure(
                "read_not_available",
                "Combat pile reading requires a current local player combat state.");
        }

        CombatPileReadZone[] zones =
        {
            BuildZone("draw", combat.DrawPile, PileType.Draw, entities),
            BuildZone("discard", combat.DiscardPile, PileType.Discard, entities),
            BuildZone("exhaust", combat.ExhaustPile, PileType.Exhaust, entities)
        };
        var content = new CombatPilesReadContent(CombatPilesKind, zones);
        return PlayerReadBuildResult.Success(new PlayerReadDraft(
            CombatPilesKind,
            "normal_player_read",
            "unordered_multiset",
            content,
            new PlayerReadCompleteness(
                "complete_for_player_visible_combat_pile_contents_without_draw_order",
                new[]
                {
                    "NDrawPileButton/NDiscardPileButton/NExhaustPileButton",
                    "NCardPileScreen.Pile.Cards",
                    "NCardPileScreen player-visible card grid",
                    "CardModel.GetDescriptionForPile"
                },
                Array.Empty<string>())));
    }

    private static PlayerReadBuildResult BuildShopCatalog(
        ILiveContext context,
        NativeEntityRegistry entities)
    {
        if (context is not ShopLiveContext
            || !ShopSurfaceFacts.TryGetCurrent(
                out MerchantRoom? merchantRoom,
                out NMerchantRoom? room,
                out MerchantInventory? inventory)
            || merchantRoom == null
            || room == null
            || inventory == null)
        {
            return PlayerReadBuildResult.Failure(
                "read_scope_mismatch",
                "Shop catalog reading is available only for the current merchant context.");
        }

        MerchantEntry[] entries = inventory.AllEntries.ToArray();
        NMerchantSlot[] slots = room.Inventory.GetAllSlots().ToArray();
        var slotByEntry = new Dictionary<MerchantEntry, NMerchantSlot>();
        foreach (MerchantEntry entry in entries)
        {
            NMerchantSlot[] matches = slots.Where(slot => ReferenceEquals(slot.Entry, entry)).ToArray();
            if (matches.Length != 1)
            {
                return PlayerReadBuildResult.Failure(
                    "read_binding_failed",
                    $"Merchant entry {entry.GetType().Name} does not have exactly one UI slot.");
            }
            slotByEntry[entry] = matches[0];
        }

        bool inventoryOpen = room.Inventory.IsOpen;
        bool inputReady = inventoryOpen
                          && ShopSurfaceFacts.IsCurrentInventory(merchantRoom, room, inventory);
        Player player = inventory.Player;
        bool potionSlotsFull = ShopSurfaceFacts.OccupiedPotionSlots(player) >= player.PotionSlots.Count;
        VisibleShopCardOffer[] cards = inventory.CardEntries.Select(entry =>
            ShopInventorySurfaceReader.BuildCardOffer(
                entry,
                slotByEntry[entry],
                Array.IndexOf(entries, entry),
                inputReady,
                entities)).ToArray();
        VisibleShopRelicOffer[] relics = inventory.RelicEntries.Select(entry =>
            ShopInventorySurfaceReader.BuildRelicOffer(
                entry,
                slotByEntry[entry],
                Array.IndexOf(entries, entry),
                inputReady,
                entities)).ToArray();
        VisibleShopPotionOffer[] potions = inventory.PotionEntries.Select(entry =>
            ShopInventorySurfaceReader.BuildPotionOffer(
                entry,
                slotByEntry[entry],
                Array.IndexOf(entries, entry),
                player,
                potionSlotsFull,
                inputReady,
                entities)).ToArray();
        MerchantCardRemovalEntry? removalEntry = inventory.CardRemovalEntry;
        VisibleShopCardRemovalOffer? removal = removalEntry == null
            ? null
            : ShopInventorySurfaceReader.BuildRemovalOffer(
                removalEntry,
                slotByEntry[removalEntry],
                Array.IndexOf(entries, removalEntry),
                inputReady,
                entities);

        var content = new ShopCatalogReadContent(
            ShopCatalogKind,
            inventoryOpen ? "inventory_open" : "inventory_closed_open_to_inspect",
            cards,
            relics,
            potions,
            removal);
        return PlayerReadBuildResult.Success(new PlayerReadDraft(
            ShopCatalogKind,
            "normal_player_read",
            "fixed_ui_slots",
            content,
            new PlayerReadCompleteness(
                "complete_for_player_openable_standard_merchant_catalog_without_action_authority",
                new[]
                {
                    "MerchantRoom.GetLocalInventory",
                    "MerchantInventory typed entries",
                    "NMerchantInventory.GetAllSlots",
                    "MerchantEntry.Cost+EnoughGold+IsStocked",
                    "Player.PotionSlots+Hook.ShouldProcurePotion"
                },
                Array.Empty<string>())));
    }

    private static CombatPileReadZone BuildZone(
        string zone,
        CardPile pile,
        PileType pileType,
        NativeEntityRegistry entities)
    {
        VisibleCard[] cards = BuildCards(pile.Cards, pileType, entities);
        return new CombatPileReadZone(
            zone,
            cards.Length,
            "unordered_multiset",
            cards);
    }

    private static VisibleCard[] BuildCards(
        IEnumerable<CardModel> source,
        PileType displayPile,
        NativeEntityRegistry entities) => OrderPublicReadCards(source
            .Select(card => LiveContextReader.BuildCard(
                card,
                entities.GetId(card, "card"),
                displayPile: displayPile)));

    internal static VisibleCard[] OrderPublicReadCards(IEnumerable<VisibleCard> cards) => cards
            // Serialization order is deterministic only. The protocol declares
            // the collection unordered and never exposes the model's draw order.
            .OrderBy(card => card.DefinitionId, StringComparer.Ordinal)
            .ThenBy(card => card.EntityId, StringComparer.Ordinal)
            .ToArray();
}
