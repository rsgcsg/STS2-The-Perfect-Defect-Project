using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Platform.NativeFoundation;

namespace STS2Connector.NativeUi;

internal sealed record NonCombatPotionDiscardBinding(string PotionId, int Slot, PotionModel Potion);

// Public-basis noncombat belt scope. Native slots, holder availability and the
// existing typed decision provider remain the authority for independent discard.
internal static class NonCombatPotionBoundary
{
    internal static bool IsBase(LiveObservation view) => view.Readiness == "ready"
        && view.Context is not CombatLiveContext && view.Surface.Kind is
            "event_option" or "event_dialogue" or "map_navigation" or "rest_site"
            or "shop_room" or "shop_inventory" or "treasure_room"
            or "reward_claim" or "card_reward_selection";

    internal static NonCombatPotionDiscardBinding[] Capture(NativeEntityRegistry entities)
    {
        if (CombatManager.Instance.IsInProgress) return Array.Empty<NonCombatPotionDiscardBinding>();
        var run = RunManager.Instance.DebugOnlyGetState();
        var player = run == null ? null : LocalContext.GetMe(run);
        if (player == null) return Array.Empty<NonCombatPotionDiscardBinding>();
        var actions = NativePotionDiscardDecisionProvider.Capture(entities).Actions;
        var result = new List<NonCombatPotionDiscardBinding>();
        for (int slot = 0; slot < player.PotionSlots.Count; slot++)
        {
            var potion = player.GetPotionAtSlotIndex(slot);
            if (!CombatTurnSurfaceReader.CanDiscardPotion(player, potion, slot)) continue;
            bool available = PotionPopupSurfaceReader.TryCurrentDiscardHolder(player, potion!, slot, out _, out bool known);
            if (!known) throw new InvalidOperationException("Noncombat potion holder binding unavailable.");
            if (!available) continue;
            if (!NativeSemanticActionCatalog.ContainsExactlyOnce(actions, "discard", potion!))
                throw new InvalidOperationException("Noncombat native discard operand not exact.");
            result.Add(new(entities.GetId(potion!, "potion"), slot, potion!));
        }
        return result.ToArray();
    }

    internal static NativeUiBoundAction Bind(NonCombatPotionDiscardBinding item, PotionPopupSurface? popup)
    {
        var operands = new Dictionary<string, string> { ["potion_id"] = item.PotionId,
            ["potion_slot"] = item.Slot.ToString(CultureInfo.InvariantCulture) };
        var entities = new List<ActionEntityBinding> { new("potion", item.PotionId) };
        if (popup != null)
        {
            operands["screen_id"] = popup.ScreenEntityId;
            entities.Add(new("screen", popup.ScreenEntityId));
        }
        return new(new NativeUiActionCandidate(
            "noncombat-discard:" + item.PotionId + ":" + item.Slot, "activate_control", "discard_potion",
            "Discard " + item.Potion.Title.GetFormattedText(), operands,
            new Dictionary<string, NativeUiOperandDomain>(), entities,
            "NativePotionDiscardDecisionProvider+current_enabled_holder")) { NonCombatDiscard = item };
    }

    internal static NativeInputResult Start(NativeEntityRegistry entities, NonCombatPotionDiscardBinding expected)
    {
        var current = Capture(entities).SingleOrDefault(item => item.PotionId == expected.PotionId
            && item.Slot == expected.Slot && ReferenceEquals(item.Potion, expected.Potion));
        if (current == null) return NativeInputResult.Rejected("noncombat_potion_changed", "Exact current belt discard changed.");
        var potion = current.Potion;
        var popup = PotionPopupSurfaceReader.Current();
        if (popup != null && ReferenceEquals(PotionPopupSurfaceReader.Potion(popup), potion)
            && PotionPopupSurfaceReader.Capture(entities, STS2Connector.Authority.EnvironmentIdentityRuntime.ReadGame())?.Surface is PotionPopupSurface currentPopup)
            return PotionPopupSurfaceReader.Start(entities, currentPopup, "discard_potion");
        // An unrelated popup contributes only a checked navigation stage.
        if (popup != null) popup.Remove();
        if (!PotionPopupSurfaceReader.TryCurrentDiscardHolder(potion.Owner, potion, current.Slot, out var holder, out _))
            return popup == null
                ? NativeInputResult.Rejected("noncombat_potion_holder_changed", "Exact current holder changed.")
                : NativeInputResult.PartiallyDelivered("noncombat_potion_holder_changed", "Popup closed; exact holder changed.",
                    new NativeInputStage(NativeInputStageKind.PotionPopupInput, NativeInputDelivery.Delivered, "native_potion_popup_closed"));
        holder!.DisableUntilPotionRemoved();
        RunManager.Instance.ActionQueueSynchronizer.RequestEnqueue(new DiscardPotionGameAction(potion.Owner, (uint)current.Slot, false));
        return popup == null ? NativeInputResult.Delivered("native_noncombat_discard_potion_action_enqueued")
            : NativeInputResult.DeliveredStages("native_noncombat_discard_potion_action_enqueued",
                new(NativeInputStageKind.PotionPopupInput, NativeInputDelivery.Delivered, "native_potion_popup_closed"),
                new(NativeInputStageKind.BoundActionInput, NativeInputDelivery.Delivered, "native_noncombat_discard_potion_action_enqueued"));
    }
}
