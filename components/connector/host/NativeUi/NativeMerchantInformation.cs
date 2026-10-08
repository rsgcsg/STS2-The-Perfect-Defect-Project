using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.ControllerInput;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.UI;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Potions;
using MegaCrit.Sts2.Core.Nodes.Relics;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Rooms;
using STS2Connector.LiveHost;

namespace STS2Connector.NativeUi;

internal enum NativeMerchantInformationKind { Card, Relic, Potion }
internal sealed record NativeMerchantInformationEntry(
    MerchantEntry Entry, NMerchantSlot Slot, Node Hitbox, Node Display, object Model,
    NativeMerchantInformationKind Kind, bool Enabled)
{
    internal bool CanInspect => Enabled && Kind is NativeMerchantInformationKind.Card or NativeMerchantInformationKind.Relic;
}

/// <summary>Exact currently stocked merchant information controls. Gold and
/// purchase eligibility are intentionally absent: native previews do not buy.</summary>
internal sealed record NativeMerchantInformation(
    MerchantRoom Room, NMerchantRoom RoomNode, MerchantInventory Inventory, NMerchantInventory Owner,
    IReadOnlyList<NativeMerchantInformationEntry> Entries)
{
    private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
    private static readonly FieldInfo? CardDisplay = typeof(NMerchantCard).GetField("_cardNode", Fields);
    private static readonly FieldInfo? RelicDisplay = typeof(NMerchantRelic).GetField("_relicNode", Fields);
    private static readonly FieldInfo? PotionDisplay = typeof(NMerchantPotion).GetField("_potionNode", Fields);

    internal static NativeMerchantInformation? Capture()
    {
        if (!ShopSurfaceFacts.TryGetCurrent(out var room, out var node, out var inventory)
            || room == null || node == null || inventory == null
            || !ShopSurfaceFacts.IsCurrentInventory(room, node, inventory)
            || !NativeInformationInput.Mounted(node.Inventory)) return null;
        NMerchantSlot[] slots = node.Inventory.GetAllSlots().ToArray();
        var entries = new List<NativeMerchantInformationEntry>();
        foreach (MerchantEntry entry in inventory.AllEntries)
        {
            if (entry is not (MerchantCardEntry or MerchantRelicEntry or MerchantPotionEntry)) continue;
            NMerchantSlot[] matches = slots.Where(slot => ReferenceEquals(slot.Entry, entry)).ToArray();
            if (matches.Length != 1) return null;
            if (!entry.IsStocked) continue;
            NMerchantSlot slot = matches[0];
            if (!NativeInformationInput.Mounted(slot) || !ConnectorMod.IsNodeVisible(slot)
                || !NativeInformationInput.Mounted(slot.Hitbox)) return null;
            Node? display;
            object? model;
            NativeMerchantInformationKind kind;
            switch (entry)
            {
                case MerchantCardEntry card when slot is NMerchantCard:
                    display = CardDisplay?.GetValue(slot) as NCard;
                    model = card.CreationResult?.Card;
                    kind = NativeMerchantInformationKind.Card;
                    if (display is not NCard shownCard || shownCard.Visibility != ModelVisibility.Visible
                        || !ReferenceEquals(shownCard.Model, model)) return null;
                    break;
                case MerchantRelicEntry relic when slot is NMerchantRelic:
                    display = RelicDisplay?.GetValue(slot) as NRelic;
                    model = relic.Model;
                    kind = NativeMerchantInformationKind.Relic;
                    if (display is not NRelic shownRelic || !ReferenceEquals(shownRelic.Model, model)) return null;
                    break;
                case MerchantPotionEntry potion when slot is NMerchantPotion:
                    display = PotionDisplay?.GetValue(slot) as NPotion;
                    model = potion.Model;
                    kind = NativeMerchantInformationKind.Potion;
                    if (display is not NPotion shownPotion || !ReferenceEquals(shownPotion.Model, model)) return null;
                    break;
                default: return null;
            }
            if (model == null || display == null || !NativeInformationInput.Mounted(display)
                || !ConnectorMod.IsNodeVisible((CanvasItem)display) || !slot.IsAncestorOf(display)) return null;
            entries.Add(new(entry, slot, slot.Hitbox, display, model, kind, slot.Hitbox.IsEnabled));
        }
        return new(room, node, inventory, node.Inventory, entries);
    }

    internal bool SameOccurrence(NativeMerchantInformation? current) => current != null
        && ReferenceEquals(Room, current.Room) && ReferenceEquals(RoomNode, current.RoomNode)
        && ReferenceEquals(Inventory, current.Inventory) && ReferenceEquals(Owner, current.Owner)
        && Entries.Count == current.Entries.Count
        && Entries.Zip(current.Entries).All(pair =>
            ReferenceEquals(pair.First.Entry, pair.Second.Entry)
            && ReferenceEquals(pair.First.Slot, pair.Second.Slot)
            && ReferenceEquals(pair.First.Hitbox, pair.Second.Hitbox)
            && ReferenceEquals(pair.First.Display, pair.Second.Display)
            && ReferenceEquals(pair.First.Model, pair.Second.Model)
            && pair.First.Kind == pair.Second.Kind);

    internal bool Allows(NativeMerchantInformation? current, NativeMerchantInformationEntry entry, bool inspect) =>
        SameOccurrence(current) && current!.Entries.Any(value =>
            ReferenceEquals(value.Slot, entry.Slot) && value.Enabled && (!inspect || value.CanInspect));

    internal bool Current(NativeMerchantInformationEntry entry, bool inspect = false) =>
        Allows(Capture(), entry, inspect);

    internal NativeInputResult Inspect(NativeMerchantInformationEntry entry) =>
        NativeInformationInput.Dispatch(() => Current(entry, inspect: true), () =>
        {
            using var input = new InputEventAction { Action = MegaInput.confirm, Pressed = true };
            entry.Slot._GuiInput(input);
        }, "native_merchant_confirm_input; native singleton inspector");
}
