using System;
using System.Linq;
using System.Reflection;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Extensions;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Potions;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Platform.NativeFoundation;
using MegaCrit.Sts2.Core.Entities.Creatures;

namespace STS2Connector.LiveHost;

// A top-bar popup owns its controls independently of the room beneath it.
// Enabled native buttons are the authority; no potion-use rules are recreated.
internal static class PotionPopupSurfaceReader
{
    private static readonly FieldInfo? Holder = typeof(NPotionPopup).GetField("_holder", BindingFlags.Instance | BindingFlags.NonPublic);
    internal static NPotionPopup? Current()
    {
        var root = NGame.Instance?.GetTree()?.Root;
        if (root == null) return null;
        NPotionPopup[] popups = ConnectorMod.FindAll<NPotionPopup>(root)
            .Where(popup => ConnectorMod.IsLiveNode(popup) && ConnectorMod.IsNodeVisible(popup)
                && !popup.IsMarkedForRemoval).ToArray();
        if (popups.Length > 1) throw new InvalidOperationException("Ambiguous visible potion popup ownership.");
        return popups.SingleOrDefault();
    }
    internal static NPotionHolder? HolderOf(NPotionPopup popup) =>
        Holder?.GetValue(popup) as NPotionHolder;
    internal static PotionModel? Potion(NPotionPopup popup) => HolderOf(popup)?.Potion?.Model;
    internal static LiveObservation? Capture(
        NativeEntityRegistry entities, GameBuildIdentity game,
        ActiveSurfaceSnapshot? active = null)
    {
        NPotionPopup? popup = Current();
        if (popup == null) return null;
        PotionModel? potion = Potion(popup);
        var use = popup.GetNodeOrNull<NPotionPopupButton>("%UseButton");
        var discard = popup.GetNodeOrNull<NPotionPopupButton>("%DiscardButton");
        if (potion == null || use == null || discard == null)
            throw new InvalidOperationException("Exact potion popup controls are unavailable.");
        int slot = potion.Owner.PotionSlots.IndexOf(potion);
        if (slot < 0) throw new InvalidOperationException("Popup potion is no longer in the native belt.");
        var surface = new PotionPopupSurface("potion_popup", entities.GetId(popup, "screen"),
            entities.GetId(potion, "potion"), potion.Id.Entry, potion.Title.GetFormattedText(), slot,
            use.IsEnabled && ConnectorMod.IsNodeVisible(use), discard.IsEnabled && ConnectorMod.IsNodeVisible(discard));
        NativeSemanticAction[] nativeUses = NativePotionUseDecisionProvider.Capture(entities).Actions
            .Where(action => action.Verb == "use" && ReferenceEquals(action.NativeSubject, potion)).ToArray();
        surface = surface with { DirectCombatUse = nativeUses.Length > 0,
            UseTargetEntityIds = nativeUses.SelectMany(action => action.Operands).Where(operand => operand.Role == "target")
                .Select(operand => operand.ReferentId).Distinct().ToArray() };
        var context = LiveContextReader.Build(entities);
        return new LiveObservation(StableIdentityHash.Object(new { surface, context }), "ready", context, surface,
            new StateCompleteness("contract_complete_for_native_potion_popup", "native_enabled_controls",
                new[] { "NPotionPopup.UseButton", "NPotionPopup.DiscardButton", "NPotionPopup.Remove", "Player.PotionSlots" }, Array.Empty<string>()),
            game, Array.Empty<string>())
        {
            PopupUnderlyingRewardOwner = active?.TopOverlay is
                MegaCrit.Sts2.Core.Nodes.Screens.NRewardsScreen or
                MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NCardRewardSelectionScreen
                ? active.TopOverlay : null
        };
    }
    internal static NativeInputResult Start(
        NativeEntityRegistry entities, PotionPopupSurface expected, string operation,
        string? targetId = null, string? expectedControlId = null)
    {
        NPotionPopup? popup = Current();
        if (popup == null || entities.GetId(popup, "screen") != expected.ScreenEntityId
            || Potion(popup) is not { } potion || entities.GetId(potion, "potion") != expected.PotionEntityId
            || potion.Owner.PotionSlots.IndexOf(potion) != expected.Slot)
            return NativeInputResult.Rejected("potion_popup_changed", "Exact popup/potion binding changed.");
        if (operation == "cancel_potion_popup")
        {
            popup.Remove();
            return NativeInputResult.Delivered("native_potion_popup_closed");
        }
        if (operation == "use_potion" && expected.DirectCombatUse)
        {
            var useButton = popup.GetNodeOrNull<NPotionPopupButton>("%UseButton");
            if (useButton == null || !useButton.IsEnabled)
                return NativeInputResult.Rejected("potion_popup_control_unavailable", "Native use control is disabled.");
            Creature? target = null;
            if (targetId != null && (!entities.TryResolve(targetId, out target) || target == null))
                return NativeInputResult.Rejected("potion_target_changed", "Exact native target changed.");
            if (potion.IsQueued || !NativePotionUseDecisionProvider.Capture(entities).Actions.Any(action =>
                    action.Verb == "use" && ReferenceEquals(action.NativeSubject, potion)
                    && (target == null ? action.Operands.Count == 0
                        : action.Operands.Count == 1 && ReferenceEquals(action.Operands[0].NativeValue, target))))
                return NativeInputResult.Rejected("potion_no_longer_usable", "Exact native potion decision changed.");
            if (MegaCrit.Sts2.Core.Combat.CombatManager.Instance.IsInProgress)
                return CombatTurnSurfaceReader.StartUsePotion(potion.Owner, potion, expected.Slot, target);
            // Same STS2 entry point as NPotionHolder; current popup control,
            // belt membership and native target were revalidated above.
            potion.EnqueueManualUse(target);
            popup.Remove();
            return NativeInputResult.Delivered("native_potion_use_enqueued");
        }
        string? path = operation switch { "discard_potion" => "%DiscardButton", "choose_potion_use" => "%UseButton", _ => null };
        var button = path == null ? null : popup.GetNodeOrNull<NPotionPopupButton>(path);
        if (button == null || !button.IsEnabled || !ConnectorMod.IsNodeVisible(button)
            || expectedControlId != null
                && entities.GetId(button, "control") != expectedControlId)
            return NativeInputResult.Rejected("potion_popup_control_unavailable", "Native control is no longer enabled.");
        button.ForceClick();
        return NativeInputResult.Delivered("native_potion_popup_input_delivered");
    }
}
