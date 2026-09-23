using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Potions;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Platform.NativeFoundation;

namespace STS2Connector.LiveHost;

// These private UI objects never enter the public Snapshot. The first v2 scope
// is the current ordinary reward overlay and its exact potion popup only.
internal sealed record RewardPotionOpenOption(
    string PotionEntityId, int Slot, string Name, string HolderEntityId,
    NPotionHolder Holder);

internal sealed record RewardPotionControlOption(
    string Kind, string ControlEntityId, bool Enabled, string Label);

internal sealed record RewardPotionProfileFacts(
    bool Qualified, string Reason, IReadOnlyList<RewardPotionOpenOption> Openers,
    string? RewardOwnerKind = null)
{
    // A delivery/staleness fact only. It is never projected to public S.
    public string? RewardOwnerEntityId { get; init; }
    public IReadOnlyList<RewardPotionControlOption> PopupControls { get; init; } =
        Array.Empty<RewardPotionControlOption>();
}

internal static class RewardPotionProfileReader
{
    private const BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly FieldInfo? HolderUsable =
        typeof(NPotionHolder).GetField("_isUsable", PrivateInstance);
    private static readonly FieldInfo? HolderDisabled =
        typeof(NPotionHolder).GetField("_disabledUntilPotionRemoved", PrivateInstance);
    private static readonly FieldInfo? TopBarTween =
        typeof(NTopBar).GetField("_hideTween", PrivateInstance);

    internal static bool ExactOpenControlReady(
        bool holderVisible, bool holderEnabled, bool holderUsable,
        bool disabledUntilRemoved, bool topBarVisible, bool focusEnabled,
        bool topBarSettled, bool gameOver) =>
        holderVisible && holderEnabled && holderUsable && !disabledUntilRemoved
        && topBarVisible && focusEnabled && topBarSettled && !gameOver;

    internal static bool IsTargeting() =>
        NRun.Instance?.GlobalUi.TargetManager?.IsInSelection == true;

    internal static RewardPotionProfileFacts Capture(
        LiveObservation draft, NativeEntityRegistry entities)
    {
        try { return CaptureCore(draft, entities); }
        catch (Exception) { return Unsupported("native_reward_potion_capture_unknown"); }
    }

    internal static bool ExactCurrentOwner(string? observedId, string? expectedId) =>
        expectedId != null && string.Equals(observedId, expectedId, StringComparison.Ordinal);

    private static RewardPotionProfileFacts CaptureCore(
        LiveObservation draft, NativeEntityRegistry entities)
    {
        if (IsTargeting())
            return Unsupported("native_potion_target_selection_is_separate_page");
        ActiveSurfaceSnapshot active = ActiveInputResolver.Capture();
        bool outer = draft.Surface is RewardClaimSurface
            && active.TopOverlay is NRewardsScreen;
        bool inner = draft.Surface is CardRewardSelectionSurface
            && active.TopOverlay is NCardRewardSelectionScreen;
        bool popup = draft.Surface is PotionPopupSurface
            && active.TopOverlay is NRewardsScreen or NCardRewardSelectionScreen;
        if (!outer && !inner && !popup || active.OpenModal != null || active.MapIsOpen)
            return Unsupported("no_unique_current_reward_overlay");
        string? expectedScreenId = draft.Surface switch
        {
            RewardClaimSurface reward => reward.ScreenEntityId,
            CardRewardSelectionSurface card => card.ScreenEntityId,
            _ => null
        };
        if (active.TopOverlay == null || (popup
                ? !ReferenceEquals(active.TopOverlay, draft.PopupUnderlyingRewardOwner)
                : !ExactCurrentOwner(
                    entities.GetId(active.TopOverlay, "screen"), expectedScreenId)))
            return Unsupported("reward_overlay_owner_changed");
        NTopBar? topBar = NRun.Instance?.GlobalUi.TopBar;
        NPotionContainer? container = topBar?.PotionContainer;
        Player? player = RunManager.Instance.DebugOnlyGetState() is { } run
            ? LocalContext.GetMe(run)
            : null;
        if (topBar == null || container == null || player == null
            || !ConnectorMod.IsLiveNode(topBar) || !ConnectorMod.IsLiveNode(container)
            || HolderUsable == null || HolderDisabled == null || TopBarTween == null)
            return Unsupported("exact_topbar_holder_contract_unavailable");
        NPotionHolder[] holders = ConnectorMod.FindAll<NPotionHolder>(container).ToArray();
        if (holders.Length != player.PotionSlots.Count
            || holders.Distinct(ReferenceEqualityComparer.Instance).Count() != holders.Length)
            return Unsupported("potion_holder_slot_bijection_unavailable");
        NPotionPopup? currentPopup = PotionPopupSurfaceReader.Current();
        if (popup)
        {
            if (currentPopup == null || draft.Surface is not PotionPopupSurface surface
                || !ExactCurrentOwner(entities.GetId(currentPopup, "screen"),
                    surface.ScreenEntityId)
                || PotionPopupSurfaceReader.HolderOf(currentPopup) is not { } selected
                || !holders.Contains(selected, ReferenceEqualityComparer.Instance)
                || selected.Potion?.Model is not { } model
                || surface.Slot < 0 || surface.Slot >= player.PotionSlots.Count
                || !ReferenceEquals(player.GetPotionAtSlotIndex(surface.Slot), model)
                || entities.GetId(model, "potion") != surface.PotionEntityId)
                return Unsupported("popup_not_bound_to_current_reward_belt");
            NPotionPopupButton? use = currentPopup.GetNodeOrNull<NPotionPopupButton>("%UseButton");
            NPotionPopupButton? discard = currentPopup.GetNodeOrNull<NPotionPopupButton>("%DiscardButton");
            if (use == null || discard == null
                || !ConnectorMod.IsLiveNode(use) || !ConnectorMod.IsLiveNode(discard)
                || (surface.CanUse && (!use.IsEnabled || !ConnectorMod.IsNodeVisible(use)))
                || (surface.CanDiscard && (!discard.IsEnabled || !ConnectorMod.IsNodeVisible(discard))))
                return Unsupported("popup_control_binding_changed");
            var controls = new List<RewardPotionControlOption>();
            if (ConnectorMod.IsNodeVisible(use))
                controls.Add(new RewardPotionControlOption("use", entities.GetId(use, "control"),
                    surface.CanUse, "Use potion"));
            if (ConnectorMod.IsNodeVisible(discard))
                controls.Add(new RewardPotionControlOption("discard",
                    entities.GetId(discard, "control"), surface.CanDiscard, "Discard potion"));
            controls.Add(new RewardPotionControlOption("close", surface.ScreenEntityId + ":close",
                true, "Close potion popup"));
            return new RewardPotionProfileFacts(true, "current_native_potion_popup",
                Array.Empty<RewardPotionOpenOption>(),
                active.TopOverlay is NRewardsScreen ? "reward_claim" : "card_reward_selection")
            {
                RewardOwnerEntityId = entities.GetId(active.TopOverlay, "screen"),
                PopupControls = controls
            };
        }
        if (currentPopup != null)
            return Unsupported("competing_potion_popup");
        if (!ConnectorMod.IsNodeVisible(topBar)
            || topBar.FocusBehaviorRecursive != Control.FocusBehaviorRecursiveEnum.Enabled)
            return Unsupported("topbar_not_presented_for_input");
        try
        {
            if (TopBarTween.GetValue(topBar) is Tween tween && tween.IsValid())
                return Unsupported("topbar_presentation_not_settled");
        }
        catch (Exception)
        {
            return Unsupported("topbar_presentation_unknown");
        }
        var options = new List<RewardPotionOpenOption>();
        for (int slot = 0; slot < holders.Length; slot++)
        {
            PotionModel? expected = player.GetPotionAtSlotIndex(slot);
            NPotionHolder holder = holders[slot];
            if (!ConnectorMod.IsLiveNode(holder))
                return Unsupported("potion_holder_not_live");
            PotionModel? actual = holder.Potion?.Model;
            if (!ReferenceEquals(actual, expected))
                return Unsupported("potion_holder_slot_changed");
            if (expected == null)
                continue;
            try
            {
                if (HolderUsable.GetValue(holder) is not true
                    || HolderDisabled.GetValue(holder) is not false
                    || !ExactOpenControlReady(
                        ConnectorMod.IsNodeVisible(holder), holder.IsEnabled,
                        true, false, true, true, true,
                        expected.Owner.RunState.IsGameOver))
                    return Unsupported("potion_holder_open_not_proved");
            }
            catch (Exception)
            {
                return Unsupported("potion_holder_open_state_unknown");
            }
            options.Add(new RewardPotionOpenOption(
                entities.GetId(expected, "potion"), slot,
                ConnectorMod.SafeGetText(() => expected.Title.GetFormattedText()) ?? expected.Id.Entry,
                entities.GetId(holder, "potion_holder"), holder));
        }
        return new RewardPotionProfileFacts(true, "exact_current_reward_potion_controls",
            options, outer ? "reward_claim" : "card_reward_selection")
        {
            RewardOwnerEntityId = entities.GetId(active.TopOverlay, "screen")
        };
    }

    internal static NativeInputResult StartOpen(
        LiveObservation draft, NativeEntityRegistry entities,
        string expectedScreenId, string potionId)
    {
        if (draft.RewardPotionFacts is not { Qualified: true } facts
            || draft.Surface is not RewardClaimSurface and not CardRewardSelectionSurface
            || (draft.Surface is RewardClaimSurface outer
                ? outer.ScreenEntityId : ((CardRewardSelectionSurface)draft.Surface).ScreenEntityId)
                != expectedScreenId)
            return NativeInputResult.Rejected("reward_potion_owner_changed", "Current reward owner changed.");
        RewardPotionProfileFacts current = Capture(draft, entities);
        RewardPotionOpenOption? before = facts.Openers.SingleOrDefault(option =>
            option.PotionEntityId == potionId);
        RewardPotionOpenOption? now = current.Openers.SingleOrDefault(option =>
            option.PotionEntityId == potionId);
        if (!current.Qualified || before == null || now == null
            || !ReferenceEquals(before.Holder, now.Holder)
            || before.Slot != now.Slot || before.HolderEntityId != now.HolderEntityId)
            return NativeInputResult.Rejected("potion_holder_changed", "Exact potion holder changed before input.");
        // ForceClick follows NPotionHolder.OnRelease. Any uncertainty after this
        // call is raised to the existing Submit unknown-Receipt boundary.
        now.Holder.ForceClick();
        NPotionPopup? opened = PotionPopupSurfaceReader.Current();
        if (opened == null || !ReferenceEquals(PotionPopupSurfaceReader.HolderOf(opened), now.Holder))
            throw new InvalidOperationException("Potion holder click did not prove its own popup.");
        return NativeInputResult.Delivered("native_potion_holder_popup_opened");
    }

    private static RewardPotionProfileFacts Unsupported(string reason) =>
        new(false, reason, Array.Empty<RewardPotionOpenOption>());
}
