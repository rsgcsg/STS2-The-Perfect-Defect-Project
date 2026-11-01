using System;
using System.Linq;
using System.Collections.Generic;
using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Events;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.addons.mega_text;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Platform.NativeFoundation;

namespace STS2Connector.LiveHost;

// Only the actual current in-run Trial confirmation; the main-menu abandon
// popup and arbitrary modal types never gain this capability.
internal sealed class AbandonRunConfirmationSurfaceReader : ILiveSurfaceReader
{
    internal const string SurfaceKind = "abandon_run_confirmation";
    private const string OptionKey = "TRIAL.pages.REJECT.options.DOUBLE_DOWN";
    private static readonly FieldInfo? MainMenuOwner = typeof(NAbandonRunConfirmPopup)
        .GetField("_mainMenuNode", BindingFlags.Instance | BindingFlags.NonPublic);
    public string Kind => SurfaceKind;
    public InputOwnerLayer Layer => InputOwnerLayer.Modal;

    internal static TrialAbandonIntention? ParentCapability(EventRoom room, EventModel model,
        EventOption option, NativeEntityRegistry entities)
    {
        if (model is not Trial || model.Owner?.RunState is not RunState run
            || !ReferenceEquals(run.CurrentRoom, room) || !ReferenceEquals(room.LocalMutableEvent, model)
            || !ReferenceEquals(RunManager.Instance.EventSynchronizer.GetLocalEvent(), model)
            || run.Players.Count != 1 || !ReferenceEquals(run.Players[0], model.Owner)
            || option.TextKey != OptionKey || !model.CurrentOptions.Contains(option, ReferenceEqualityComparer.Instance)) return null;
        return new(SurfaceKind, entities.GetId(model, "event"), entities.GetId(room, "room"), entities.GetId(option, "event_option"),
            PublicText(new LocString("main_menu_ui", "ABANDON_RUN_CONFIRMATION.header").GetFormattedText()),
            PublicText(new LocString("main_menu_ui", "ABANDON_RUN_CONFIRMATION.body").GetFormattedText()));
    }

    private sealed record Current(NAbandonRunConfirmPopup Modal, TrialAbandonIntention Intention,
        NPopupYesNoButton Yes, NPopupYesNoButton No, string Title, string Body);

    private static Current? CaptureCurrent(ActiveSurfaceSnapshot snapshot, NativeEntityRegistry entities)
    {
        if (snapshot.OpenModal is not NAbandonRunConfirmPopup modal || MainMenuOwner == null
            || MainMenuOwner.GetValue(modal) != null || !ReferenceEquals(NModalContainer.Instance?.OpenModal, modal)
            || !ConnectorMod.IsLiveNode(modal) || !ConnectorMod.IsNodeVisible(modal)
            || RunManager.Instance.DebugOnlyGetState() is not RunState run || !RunManager.Instance.IsInProgress
            || run.CurrentRoom is not EventRoom room || room.LocalMutableEvent is not Trial model) return null;
        var options = model.CurrentOptions.Where(option => option.TextKey == OptionKey && option.WasChosen).ToArray();
        if (options.Length != 1 || ParentCapability(room, model, options[0], entities) is not { } intention) return null;
        NVerticalPopup popup = modal.GetNode<NVerticalPopup>("VerticalPopup");
        return new(modal, intention, popup.GetNode<NPopupYesNoButton>("YesButton"), popup.GetNode<NPopupYesNoButton>("NoButton"),
            PublicText(popup.GetNode<MegaLabel>("Header").Text), PublicText(popup.GetNode<MegaRichTextLabel>("Description").Text));
    }

    public LiveObservation? TryBuild(ActiveSurfaceSnapshot snapshot, NativeEntityRegistry entities, GameBuildIdentity game)
    {
        Current? current = CaptureCurrent(snapshot, entities);
        if (current == null) return null;
        var controls = new[] { Control(current.Yes, "yes", "confirm_abandon", entities),
            Control(current.No, "no", "cancel_abandon", entities) };
        var surface = new AbandonRunConfirmationSurface(SurfaceKind, "awaiting_confirmation", entities.GetId(current.Modal, "screen"),
            entities.GetId(current.Modal, "modal"), current.Intention.EventEntityId, current.Intention.RoomEntityId,
            current.Intention.ParentOptionEntityId, current.Title, current.Body, controls);
        var context = new EventLiveContext("event", "TRIAL", null, false, false, null);
        return new LiveObservation(StableIdentityHash.Object(new { game.Version, context, surface }),
            controls.Any(control => control.Enabled) ? "ready" : "settling", context, surface,
            new StateCompleteness("contract_complete_for_supported_surface", "derived_from_exact_current_native_modal_controls",
                new[] { "NModalContainer.OpenModal", "NAbandonRunConfirmPopup+NVerticalPopup", "Trial.CurrentOptions" }, Array.Empty<string>()),
            game, Array.Empty<string>());
    }

    internal static NativeInputResult Start(NativeEntityRegistry entities, AbandonRunConfirmationSurface expected,
        string expectedControlId, bool yes)
    {
        Current? current = CaptureCurrent(ActiveInputResolver.Capture(), entities);
        if (current == null || entities.GetId(current.Modal, "screen") != expected.ScreenEntityId
            || current.Intention.EventEntityId != expected.EventEntityId || current.Intention.RoomEntityId != expected.RoomEntityId
            || current.Intention.ParentOptionEntityId != expected.ParentOptionEntityId)
            return NativeInputResult.Rejected("trial_abandon_owner_changed", "The exact Trial confirmation is no longer current.");
        NPopupYesNoButton button = yes ? current.Yes : current.No;
        if (entities.GetId(button, "control") != expectedControlId || !Usable(button))
            return NativeInputResult.Rejected("trial_abandon_control_changed", "The exact visible enabled native control changed.");
        button.ForceClick();
        return NativeInputResult.Delivered(yes ? "native_trial_abandon_yes_button_clicked" : "native_trial_abandon_no_button_clicked");
    }

    private static VisibleAbandonControl Control(NPopupYesNoButton button, string semantic, string operation, NativeEntityRegistry entities) =>
        new(entities.GetId(button, "control"), semantic, operation, PublicText(button.GetNode<MegaLabel>("%Label").Text), Usable(button));
    private static bool Usable(NButton button) => ConnectorMod.IsLiveNode(button) && ConnectorMod.IsNodeVisible(button) && button.IsEnabled;
    private static string PublicText(string text)
    {
        string value = NativeVisibleText.Strip(text);
        return !string.IsNullOrWhiteSpace(value) ? value : throw new InvalidOperationException("Native abandon confirmation text is unavailable.");
    }
}
