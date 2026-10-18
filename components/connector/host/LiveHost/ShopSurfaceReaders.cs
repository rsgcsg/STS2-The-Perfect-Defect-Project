using STS2Connector.NativeUi;
using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Hooks;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.LiveHost;

/// <summary>
/// The open merchant inventory owns purchase and close controls. It is not the
/// same input surface as the surrounding merchant room, whose Proceed button
/// is disabled while the inventory is open.
/// </summary>
internal sealed class ShopInventorySurfaceReader : ILiveSurfaceReader
{
    private const string SurfaceKind = "shop_inventory";
    internal const string CardPurchaseDeliveryEvidence = "native_shop_card_purchase_started";
    internal const string PotionPurchaseDeliveryEvidence = "native_shop_potion_purchase_started";
    internal const string RelicPurchaseDeliveryEvidence = "native_shop_relic_purchase_started";
    internal const string CardRemovalDeliveryEvidence = "native_shop_removal_purchase_started";
    internal const string CloseInventoryDeliveryEvidence = "native_shop_back_button_clicked";

    public string Kind => SurfaceKind;

    public InputOwnerLayer Layer => InputOwnerLayer.Room;

    public LiveObservation? TryBuild(
        ActiveSurfaceSnapshot snapshot,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        if (!ShopSurfaceFacts.TryGetCurrent(out MerchantRoom? currentMerchantRoom, out NMerchantRoom? currentRoom, out MerchantInventory? currentInventory)
            || currentRoom == null
            || currentMerchantRoom == null
            || currentInventory == null
            || !currentRoom.Inventory.IsOpen)
        {
            return null;
        }

        return Build(currentMerchantRoom, currentRoom, currentInventory, entities, game);
    }

    private static LiveObservation Build(
        MerchantRoom merchantRoom,
        NMerchantRoom room,
        MerchantInventory inventory,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        NMerchantInventory inventoryUi = room.Inventory;
        MerchantEntry[] entries = inventory.AllEntries.ToArray();
        NMerchantSlot[] slots = inventoryUi.GetAllSlots().ToArray();
        var slotByEntry = new Dictionary<MerchantEntry, NMerchantSlot>();
        foreach (MerchantEntry entry in entries)
        {
            NMerchantSlot[] matches = slots.Where(slot => ReferenceEquals(slot.Entry, entry)).ToArray();
            if (matches.Length != 1)
                return BindingUnavailable(game, $"Merchant entry {entry.GetType().Name} does not have exactly one UI slot.");
            slotByEntry[entry] = matches[0];
        }

        NBackButton[] backButtons = ConnectorMod.FindAll<NBackButton>(inventoryUi)
            .Where(ConnectorMod.IsLiveNode)
            .ToArray();
        if (backButtons.Length != 1)
            return BindingUnavailable(game, "The open merchant inventory does not have exactly one live back button.");
        NBackButton backButton = backButtons[0];
        bool inputReady = ActiveScreenContext.Instance.IsCurrent(inventoryUi)
                          && backButton.IsEnabled
                          && ConnectorMod.IsNodeVisible(backButton);

        Player player = inventory.Player;
        int occupiedPotionSlots = ShopSurfaceFacts.OccupiedPotionSlots(player);
        bool potionSlotsFull = occupiedPotionSlots >= player.PotionSlots.Count;
        VisibleShopCardOffer[] cards = inventory.CardEntries.Select(entry =>
            BuildCardOffer(entry, slotByEntry[entry], Array.IndexOf(entries, entry), inputReady, entities)).ToArray();
        VisibleShopRelicOffer[] relics = inventory.RelicEntries.Select(entry =>
            BuildRelicOffer(entry, slotByEntry[entry], Array.IndexOf(entries, entry), inputReady, entities)).ToArray();
        VisibleShopPotionOffer[] potions = inventory.PotionEntries.Select(entry =>
            BuildPotionOffer(entry, slotByEntry[entry], Array.IndexOf(entries, entry), player, potionSlotsFull, inputReady, entities)).ToArray();
        MerchantCardRemovalEntry? removalEntry = inventory.CardRemovalEntry;
        VisibleShopCardRemovalOffer? removal = removalEntry != null
            ? BuildRemovalOffer(
                removalEntry,
                slotByEntry[removalEntry],
                Array.IndexOf(entries, removalEntry),
                inputReady,
                entities)
            : null;

        bool canClose = inputReady;
        var surface = new ShopInventorySurface(
            SurfaceKind,
            entities.GetId(inventoryUi, "screen"),
            cards,
            relics,
            potions,
            removal,
            canClose) { ShopSessionEntityId = entities.GetId(merchantRoom, "shop_session") };
        bool hasActionableControl = canClose
            || cards.Any(offer => offer.CanPurchase)
            || relics.Any(offer => offer.CanPurchase)
            || potions.Any(offer => offer.CanPurchase)
            || removal?.CanPurchase == true;
        string readiness = hasActionableControl ? "ready" : "settling";
        var completeness = new StateCompleteness(
            "contract_complete_for_visible_normal_merchant_inventory",
            hasActionableControl
                ? "derived_from_exact_inventory_entries_ui_slots_capacity_and_back_control"
                : "temporarily_empty_while_merchant_inventory_settles",
            new[]
            {
                "MerchantRoom.GetLocalInventory",
                "MerchantInventory typed entries",
                "NMerchantInventory.GetAllSlots",
                "MerchantEntry.Cost+EnoughGold+IsStocked",
                "Player.PotionSlots+Hook.ShouldProcurePotion",
                "NMerchantInventory.BackButton"
            },
            Array.Empty<string>());
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            context = LiveContextReader.BuildShop(merchantRoom, entities),
            surface
        });
        return new LiveObservation(
            signature,
            readiness,
            LiveContextReader.BuildShop(merchantRoom, entities),
            surface,
            completeness,
            game,
            Array.Empty<string>());
    }

    internal static VisibleShopCardOffer BuildCardOffer(
        MerchantCardEntry entry,
        NMerchantSlot slot,
        int inventoryIndex,
        bool inputReady,
        NativeEntityRegistry entities)
    {
        bool stocked = entry.IsStocked && entry.CreationResult?.Card != null;
        bool visible = ConnectorMod.IsNodeVisible(slot);
        bool canPurchase = stocked && visible && inputReady && entry.EnoughGold && slot.Hitbox.IsEnabled;
        return new VisibleShopCardOffer(
            entities.GetId(entry, "shop_entry"),
            entities.GetId(slot, "shop_slot"),
            inventoryIndex,
            entry.Cost,
            stocked,
            visible,
            entry.EnoughGold,
            canPurchase,
            ShopSurfaceFacts.BlockedReason(stocked, visible, entry.EnoughGold, canPurchase),
            entry.IsOnSale,
            stocked
                ? LiveContextReader.BuildCard(
                    entry.CreationResult!.Card,
                    entities.GetId(entry.CreationResult.Card, "card"),
                    displayPile: PileType.None)
                : null);
    }

    internal static VisibleShopRelicOffer BuildRelicOffer(
        MerchantRelicEntry entry,
        NMerchantSlot slot,
        int inventoryIndex,
        bool inputReady,
        NativeEntityRegistry entities)
    {
        bool stocked = entry.IsStocked && entry.Model != null;
        bool visible = ConnectorMod.IsNodeVisible(slot);
        bool canPurchase = stocked && visible && inputReady && entry.EnoughGold && slot.Hitbox.IsEnabled;
        VisibleRelic? relic = entry.Model is { } model
            ? VisibleEntityFacts.BuildRelic(model, entities)
            : null;
        return new VisibleShopRelicOffer(
            entities.GetId(entry, "shop_entry"),
            entities.GetId(slot, "shop_slot"),
            inventoryIndex,
            entry.Cost,
            stocked,
            visible,
            entry.EnoughGold,
            canPurchase,
            ShopSurfaceFacts.BlockedReason(stocked, visible, entry.EnoughGold, canPurchase),
            relic);
    }

    internal static VisibleShopPotionOffer BuildPotionOffer(
        MerchantPotionEntry entry,
        NMerchantSlot slot,
        int inventoryIndex,
        Player player,
        bool potionSlotsFull,
        bool inputReady,
        NativeEntityRegistry entities)
    {
        bool stocked = entry.IsStocked && entry.Model != null;
        bool visible = ConnectorMod.IsNodeVisible(slot);
        bool procurementAllowed = stocked && ShopSurfaceFacts.CanProcurePotion(player, entry.Model);
        bool canPurchase = stocked
                           && visible
                           && inputReady
                           && entry.EnoughGold
                           && slot.Hitbox.IsEnabled
                           && !potionSlotsFull
                           && procurementAllowed;
        string? blockedReason = !stocked ? "sold_out"
            : !visible ? "not_visible"
            : !entry.EnoughGold ? "insufficient_gold"
            : potionSlotsFull ? "potion_slots_full"
            : !procurementAllowed ? "potion_procurement_forbidden"
            : !canPurchase ? "ui_control_disabled"
            : null;
        PotionModel? potion = entry.Model;
        return new VisibleShopPotionOffer(
            entities.GetId(entry, "shop_entry"),
            entities.GetId(slot, "shop_slot"),
            inventoryIndex,
            entry.Cost,
            stocked,
            visible,
            entry.EnoughGold,
            canPurchase,
            blockedReason,
            potion?.Id.Entry,
            potion == null ? null : ConnectorMod.SafeGetText(() => potion.Title),
            potion == null ? null : ConnectorMod.SafeGetText(() => potion.DynamicDescription),
            potion?.Rarity.ToString());
    }

    internal static VisibleShopCardRemovalOffer BuildRemovalOffer(
        MerchantCardRemovalEntry entry,
        NMerchantSlot slot,
        int inventoryIndex,
        bool inputReady,
        NativeEntityRegistry entities)
    {
        bool stocked = entry.IsStocked;
        bool visible = ConnectorMod.IsNodeVisible(slot);
        bool canPurchase = stocked && visible && inputReady && entry.EnoughGold && slot.Hitbox.IsEnabled;
        return new VisibleShopCardRemovalOffer(
            entities.GetId(entry, "shop_entry"),
            entities.GetId(slot, "shop_slot"),
            inventoryIndex,
            entry.Cost,
            MerchantCardRemovalEntry.PriceIncrease,
            stocked,
            visible,
            entry.EnoughGold,
            canPurchase,
            !stocked ? "already_used"
                : ShopSurfaceFacts.BlockedReason(stocked, visible, entry.EnoughGold, canPurchase));
    }

    private static NativeInputResult StartPurchase(
        MerchantRoom expectedMerchantRoom,
        NMerchantRoom expectedRoom,
        MerchantInventory expectedInventory,
        MerchantEntry expectedEntry,
        NMerchantSlot expectedSlot,
        int expectedPrice,
        Func<bool>? extraValidator,
        string deliveryEvidence)
    {
        if (!ShopSurfaceFacts.IsCurrentInventory(expectedMerchantRoom, expectedRoom, expectedInventory)
            || !ReferenceEquals(expectedSlot.Entry, expectedEntry)
            || !expectedEntry.IsStocked
            || !expectedEntry.EnoughGold
            || expectedEntry.Cost != expectedPrice
            || !ConnectorMod.IsNodeVisible(expectedSlot)
            || !expectedSlot.Hitbox.IsEnabled
            || extraValidator?.Invoke() == false)
        {
            return NativeInputResult.Rejected(
                "shop_offer_changed",
                "The advertised shop offer is no longer current and purchasable.");
        }

        try
        {
            TaskHelper.RunSafely(expectedEntry.OnTryPurchaseWrapper(expectedInventory));
        }
        catch (Exception)
        {
            return NativeInputResult.Rejected(
                "shop_purchase_start_failed",
                "The exact merchant purchase command could not be started.");
        }

        return NativeInputResult.Delivered(deliveryEvidence);
    }

    private static NativeInputResult StartCardRemoval(
        MerchantRoom expectedMerchantRoom,
        NMerchantRoom expectedRoom,
        MerchantInventory expectedInventory,
        MerchantCardRemovalEntry expectedEntry,
        NMerchantSlot expectedSlot,
        int expectedPrice)
    {
        if (!ShopSurfaceFacts.IsCurrentInventory(expectedMerchantRoom, expectedRoom, expectedInventory)
            || !ReferenceEquals(expectedSlot.Entry, expectedEntry)
            || !expectedEntry.IsStocked
            || !expectedEntry.EnoughGold
            || expectedEntry.Cost != expectedPrice
            || !ConnectorMod.IsNodeVisible(expectedSlot)
            || !expectedSlot.Hitbox.IsEnabled)
        {
            return NativeInputResult.Rejected(
                "shop_card_removal_changed",
                "The advertised card-removal service is no longer current and purchasable.");
        }

        TaskHelper.RunSafely(expectedEntry.OnTryPurchaseWrapper(expectedInventory));
        return NativeInputResult.Delivered(CardRemovalDeliveryEvidence);
    }

    internal static NativeInputResult StartCardPurchase(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedOfferId,
        int expectedPrice)
    {
        NativeOfferBinding<MerchantCardEntry>? binding =
            ResolveNativeOffer<MerchantCardEntry>(
                entities,
                expectedScreenId,
                expectedOfferId);
        if (binding?.Entry.CreationResult?.Card is not { } card)
        {
            return NativeOfferRejected();
        }
        return StartPurchase(
            binding.MerchantRoom,
            binding.Room,
            binding.Inventory,
            binding.Entry,
            binding.Slot,
            expectedPrice,
            null,
            CardPurchaseDeliveryEvidence);
    }

    internal static NativeInputResult StartRelicPurchase(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedOfferId,
        int expectedPrice)
    {
        NativeOfferBinding<MerchantRelicEntry>? binding =
            ResolveNativeOffer<MerchantRelicEntry>(
                entities,
                expectedScreenId,
                expectedOfferId);
        if (binding?.Entry.Model is not { } relic)
        {
            return NativeOfferRejected();
        }
        return StartPurchase(
            binding.MerchantRoom,
            binding.Room,
            binding.Inventory,
            binding.Entry,
            binding.Slot,
            expectedPrice,
            null,
            RelicPurchaseDeliveryEvidence);
    }

    internal static NativeInputResult StartPotionPurchase(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedOfferId,
        int expectedPrice)
    {
        NativeOfferBinding<MerchantPotionEntry>? binding =
            ResolveNativeOffer<MerchantPotionEntry>(
                entities,
                expectedScreenId,
                expectedOfferId);
        if (binding?.Entry.Model is not { } potion)
        {
            return NativeOfferRejected();
        }
        return StartPurchase(
            binding.MerchantRoom,
            binding.Room,
            binding.Inventory,
            binding.Entry,
            binding.Slot,
            expectedPrice,
            () => ShopSurfaceFacts.CanProcurePotion(binding.Inventory.Player, potion),
            PotionPurchaseDeliveryEvidence);
    }

    internal static NativeInputResult StartCardRemoval(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedOfferId,
        int expectedPrice)
    {
        NativeOfferBinding<MerchantCardRemovalEntry>? binding =
            ResolveNativeOffer<MerchantCardRemovalEntry>(
                entities,
                expectedScreenId,
                expectedOfferId);
        if (binding == null)
        {
            return NativeOfferRejected();
        }
        return StartCardRemoval(
            binding.MerchantRoom,
            binding.Room,
            binding.Inventory,
            binding.Entry,
            binding.Slot,
            expectedPrice);
    }

    internal static NativeInputResult StartCloseInventory(
        NativeEntityRegistry entities,
        string expectedScreenId)
    {
        if (!ShopSurfaceFacts.TryGetCurrent(
                out MerchantRoom? merchantRoom,
                out NMerchantRoom? room,
                out MerchantInventory? inventory)
            || merchantRoom == null
            || room == null
            || inventory == null
            || !entities.TryResolve(
                expectedScreenId,
                out NMerchantInventory? inventoryUi)
            || inventoryUi == null
            || !ReferenceEquals(inventoryUi, room.Inventory))
        {
            return NativeInputResult.Rejected(
                "shop_inventory_binding_changed",
                "The exact merchant inventory is no longer current.");
        }
        NBackButton[] backButtons = ConnectorMod.FindAll<NBackButton>(inventoryUi)
            .Where(ConnectorMod.IsLiveNode)
            .ToArray();
        return backButtons.Length == 1
            ? StartCloseInventory(
                merchantRoom,
                room,
                inventory,
                backButtons[0])
            : NativeInputResult.Rejected(
                "shop_inventory_binding_changed",
                "The exact merchant inventory close control is unavailable.");
    }

    private sealed record NativeOfferBinding<TEntry>(
        MerchantRoom MerchantRoom,
        NMerchantRoom Room,
        MerchantInventory Inventory,
        TEntry Entry,
        NMerchantSlot Slot)
        where TEntry : MerchantEntry;

    private static NativeOfferBinding<TEntry>? ResolveNativeOffer<TEntry>(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedOfferId)
        where TEntry : MerchantEntry
    {
        if (!ShopSurfaceFacts.TryGetCurrent(
                out MerchantRoom? merchantRoom,
                out NMerchantRoom? room,
                out MerchantInventory? inventory)
            || merchantRoom == null
            || room == null
            || inventory == null
            || !ShopSurfaceFacts.IsCurrentInventory(merchantRoom, room, inventory)
            || !entities.TryResolve(
                expectedScreenId,
                out NMerchantInventory? inventoryUi)
            || inventoryUi == null
            || !ReferenceEquals(inventoryUi, room.Inventory)
            || !entities.TryResolve(expectedOfferId, out TEntry? entry)
            || entry == null
            || !inventory.AllEntries.Any(value => ReferenceEquals(value, entry)))
        {
            return null;
        }

        NMerchantSlot[] matches = inventoryUi.GetAllSlots()
            .Where(value => ReferenceEquals(value.Entry, entry))
            .ToArray();
        if (matches.Length != 1)
            return null;
        return new NativeOfferBinding<TEntry>(
            merchantRoom,
            room,
            inventory,
            entry,
            matches[0]);
    }

    private static NativeInputResult NativeOfferRejected() =>
        NativeInputResult.Rejected(
            "shop_offer_changed",
            "The exact merchant offer is no longer current and purchasable.");

    private static NativeInputResult StartCloseInventory(
        MerchantRoom expectedMerchantRoom,
        NMerchantRoom expectedRoom,
        MerchantInventory expectedInventory,
        NBackButton expectedBackButton)
    {
        if (!ShopSurfaceFacts.IsCurrentInventory(expectedMerchantRoom, expectedRoom, expectedInventory)
            || !expectedBackButton.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedBackButton))
        {
            return NativeInputResult.Rejected(
                "shop_inventory_close_changed",
                "The advertised shop inventory close control is no longer current and enabled.");
        }

        expectedBackButton.ForceClick();
        return NativeInputResult.Delivered(CloseInventoryDeliveryEvidence);
    }

    private static LiveObservation BindingUnavailable(GameBuildIdentity game, string reason)
    {
        var context = new UnknownLiveContext("unknown", nameof(MerchantRoom), reason);
        var surface = new UnsupportedSurface("unsupported", SurfaceKind, reason);
        var completeness = new StateCompleteness(
            "partial",
            "empty_fail_closed",
            new[] { "MerchantRoom+NMerchantInventory exact-version binding" },
            new[] { "shop_inventory", "legal_actions" });
        string signature = StableIdentityHash.Object(new { game.Version, reason });
        return new LiveObservation(
            signature,
            "degraded",
            context,
            surface,
            completeness,
            game,
            new[] { "shop_inventory_binding_unavailable" })
        {
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.surface.shop_inventory.binding_unavailable",
                    "error",
                    "surface",
                    "actions_suppressed",
                    "update_host_adapter",
                    reason)
            }
        };
    }
}

/// <summary>
/// The merchant room owns opening the inventory and proceeding to the map only
/// after the inventory is closed. These actions never coexist with purchases.
/// </summary>
internal sealed class ShopRoomSurfaceReader : ILiveSurfaceReader
{
    private const string SurfaceKind = "shop_room";
    internal const string OpenInventoryDeliveryEvidence = "native_merchant_button_clicked";
    internal const string ProceedDeliveryEvidence = "native_shop_proceed_button_clicked";

    public string Kind => SurfaceKind;

    public InputOwnerLayer Layer => InputOwnerLayer.Room;

    public LiveObservation? TryBuild(
        ActiveSurfaceSnapshot snapshot,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        if (!ShopSurfaceFacts.TryGetCurrent(out MerchantRoom? merchantRoom, out NMerchantRoom? room, out MerchantInventory? inventory)
            || room == null
            || merchantRoom == null
            || inventory == null
            || room.Inventory.IsOpen)
        {
            return null;
        }

        string roomId = entities.GetId(room, "room");
        bool canOpen = room.MerchantButton.IsEnabled && ConnectorMod.IsNodeVisible(room.MerchantButton);
        bool canProceed = room.ProceedButton.IsEnabled && ConnectorMod.IsNodeVisible(room.ProceedButton);
        var surface = new ShopRoomSurface(SurfaceKind, roomId, canOpen, canProceed)
            { ShopSessionEntityId = entities.GetId(merchantRoom, "shop_session") };
        bool hasActionableControl = canOpen || canProceed;
        string readiness = hasActionableControl ? "ready" : "settling";
        var completeness = new StateCompleteness(
            "contract_complete_for_visible_normal_merchant_room_controls",
            hasActionableControl
                ? "derived_from_exact_merchant_and_proceed_controls"
                : "temporarily_empty_while_merchant_room_settles",
            new[]
            {
                "NMerchantRoom.MerchantButton",
                "NMerchantRoom.ProceedButton",
                "NMerchantInventory.IsOpen"
            },
            Array.Empty<string>());
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            context = LiveContextReader.BuildShop(merchantRoom, entities),
            surface
        });
        return new LiveObservation(
            signature,
            readiness,
            LiveContextReader.BuildShop(merchantRoom, entities),
            surface,
            completeness,
            game,
            Array.Empty<string>());
    }

    private static NativeInputResult StartOpenInventory(
        MerchantRoom expectedMerchantRoom,
        NMerchantRoom expectedRoom,
        MerchantInventory expectedInventory)
    {
        if (!ShopSurfaceFacts.IsCurrentRoom(expectedMerchantRoom, expectedRoom, expectedInventory)
            || expectedRoom.Inventory.IsOpen
            || !expectedRoom.MerchantButton.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedRoom.MerchantButton))
        {
            return NativeInputResult.Rejected(
                "shop_open_changed",
                "The advertised merchant control is no longer current and enabled.");
        }

        expectedRoom.MerchantButton.ForceClick();
        return NativeInputResult.Delivered(OpenInventoryDeliveryEvidence);
    }

    internal static NativeInputResult StartOpenInventory(
        NativeEntityRegistry entities,
        string expectedRoomId)
    {
        if (!ShopSurfaceFacts.TryGetCurrent(
                out MerchantRoom? merchantRoom,
                out NMerchantRoom? room,
                out MerchantInventory? inventory)
            || merchantRoom == null
            || room == null
            || inventory == null
            || !string.Equals(
                entities.GetId(room, "room"),
                expectedRoomId,
                StringComparison.Ordinal))
        {
            return NativeInputResult.Rejected(
                "shop_room_binding_changed",
                "The exact merchant room is no longer current.");
        }
        return StartOpenInventory(merchantRoom, room, inventory);
    }

    private static NativeInputResult StartProceed(
        MerchantRoom expectedMerchantRoom,
        NMerchantRoom expectedRoom)
    {
        if (!ReferenceEquals(RunManager.Instance.DebugOnlyGetState()?.CurrentRoom, expectedMerchantRoom)
            || !ReferenceEquals(NMerchantRoom.Instance, expectedRoom)
            || expectedRoom.Inventory.IsOpen
            || !expectedRoom.ProceedButton.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedRoom.ProceedButton))
        {
            return NativeInputResult.Rejected(
                "shop_proceed_changed",
                "The advertised shop proceed control is no longer current and enabled.");
        }

        expectedRoom.ProceedButton.ForceClick();
        return NativeInputResult.Delivered(ProceedDeliveryEvidence);
    }

    internal static NativeInputResult StartProceed(
        NativeEntityRegistry entities,
        string expectedRoomId)
    {
        if (!ShopSurfaceFacts.TryGetCurrent(
                out MerchantRoom? merchantRoom,
                out NMerchantRoom? room,
                out MerchantInventory? inventory)
            || merchantRoom == null
            || room == null
            || inventory == null
            || !string.Equals(
                entities.GetId(room, "room"),
                expectedRoomId,
                StringComparison.Ordinal))
        {
            return NativeInputResult.Rejected(
                "shop_room_binding_changed",
                "The exact merchant room is no longer current.");
        }
        return StartProceed(merchantRoom, room);
    }
}

internal static class ShopSurfaceFacts
{
    public static bool TryGetCurrent(
        out MerchantRoom? merchantRoom,
        out NMerchantRoom? room,
        out MerchantInventory? inventory)
    {
        merchantRoom = RunManager.Instance.DebugOnlyGetState()?.CurrentRoom as MerchantRoom;
        room = NMerchantRoom.Instance;
        inventory = merchantRoom?.GetLocalInventory();
        return merchantRoom != null
               && room != null
               && inventory != null
               && ConnectorMod.IsLiveNode(room)
               && ReferenceEquals(room.Room, merchantRoom)
               && ReferenceEquals(room.Inventory.Inventory, inventory);
    }

    public static bool IsCurrentRoom(
        MerchantRoom merchantRoom,
        NMerchantRoom room,
        MerchantInventory inventory) =>
        IsCurrentMerchant(merchantRoom, room, inventory)
        && !ActiveInputResolver.IsVisibleActiveOverlay(NOverlayStack.Instance?.Peek())
        && ActiveScreenContext.Instance.IsCurrent(room)
        && NMapScreen.Instance?.IsOpen != true;

    public static bool IsCurrentMerchant(
        MerchantRoom merchantRoom,
        NMerchantRoom room,
        MerchantInventory inventory) =>
        TryGetCurrent(out MerchantRoom? currentRoom, out NMerchantRoom? currentUi, out MerchantInventory? currentInventory)
        && ReferenceEquals(currentRoom, merchantRoom)
        && ReferenceEquals(currentUi, room)
        && ReferenceEquals(currentInventory, inventory);

    public static bool IsCurrentInventory(
        MerchantRoom merchantRoom,
        NMerchantRoom room,
        MerchantInventory inventory) =>
        IsCurrentMerchant(merchantRoom, room, inventory)
        && !ActiveInputResolver.IsVisibleActiveOverlay(NOverlayStack.Instance?.Peek())
        && room.Inventory.IsOpen
        && ActiveScreenContext.Instance.IsCurrent(room.Inventory)
        && ConnectorMod.IsNodeVisible(room.Inventory);

    public static int OccupiedPotionSlots(Player player)
    {
        int occupied = 0;
        for (int slot = 0; slot < player.PotionSlots.Count; slot++)
        {
            if (player.GetPotionAtSlotIndex(slot) != null)
                occupied++;
        }
        return occupied;
    }

    public static bool CanProcurePotion(Player player, PotionModel? potion) =>
        potion != null
        && OccupiedPotionSlots(player) < player.PotionSlots.Count
        && Hook.ShouldProcurePotion(player.RunState, player.Creature.CombatState, potion, player);

    public static bool ContainsPotionInstance(Player player, PotionModel expectedPotion)
    {
        for (int slot = 0; slot < player.PotionSlots.Count; slot++)
        {
            if (ReferenceEquals(player.GetPotionAtSlotIndex(slot), expectedPotion))
                return true;
        }
        return false;
    }

    public static string? BlockedReason(bool stocked, bool visible, bool affordable, bool canPurchase) =>
        !stocked ? "sold_out"
            : !visible ? "not_visible"
            : !affordable ? "insufficient_gold"
            : !canPurchase ? "ui_control_disabled"
            : null;
}
