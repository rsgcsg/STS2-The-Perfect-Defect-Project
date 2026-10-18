using STS2Connector.NativeUi;
using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.HoverTips;
using MegaCrit.Sts2.Core.Models.Events;
using MegaCrit.Sts2.Core.Nodes.Events;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.LiveHost;

internal sealed class EventOptionSurfaceReader : ILiveSurfaceReader
{
    internal const string ChooseDeliveryEvidence = "native_event_option_button_clicked";
    internal const string ProceedDeliveryEvidence = "native_event_proceed_button_clicked";

    public string Kind => "event_option";

    public InputOwnerLayer Layer => InputOwnerLayer.Room;

    public LiveObservation? TryBuild(
        ActiveSurfaceSnapshot snapshot,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        NEventRoom? room = NEventRoom.Instance;
        if (runState?.CurrentRoom is not EventRoom eventRoom
            || runState.Players.Count != 1
            || room == null
            || !ConnectorMod.IsLiveNode(room)
            || CombatManager.Instance.IsInProgress)
        {
            return null;
        }

        if (!NativeEventRoomBinding.TryCapture(eventRoom, room, out var owner))
            return null;

        EventLiveContext context = LiveContextReader.BuildEvent(owner);
        if (context.InDialogue)
            return null;

        NEventOptionButton[] allButtons = room.Layout?.OptionButtons.ToArray()
            ?? Array.Empty<NEventOptionButton>();
        var visibleButtons = allButtons
            .Select((button, index) => (Button: button, Index: index))
            .Where(entry => ConnectorMod.IsNodeVisible(entry.Button))
            .ToArray();
        if (visibleButtons.Any(entry => !ReferenceEquals(entry.Button.Event, owner.Event)))
            return null;
        var options = new List<VisibleEventOption>(visibleButtons.Length);
        int actionableOptionCount = 0;
        foreach ((NEventOptionButton button, int position) in visibleButtons)
        {
            EventOption option = button.Option;
            string entityId = entities.GetId(option, "event_option");
            options.Add(new VisibleEventOption(
                entityId,
                position,
                ConnectorMod.SafeGetText(() => option.Title),
                ConnectorMod.SafeGetText(() => option.Description),
                button.IsEnabled,
                option.IsLocked,
                option.IsProceed,
                option.WasChosen,
                ReadWillKillPlayer(button, option),
                option.Relic == null ? null : ConnectorMod.SafeGetText(() => option.Relic.Title),
                option.Relic == null ? null : ConnectorMod.SafeGetText(() => option.Relic.DynamicDescription),
                BuildTooltips(option.HoverTips, entityId)));

            if (!option.IsLocked && button.IsEnabled)
                actionableOptionCount++;
        }

        var surface = new EventOptionSurface(
            Kind,
            entities.GetId(room, "screen"),
            options);
        var missing = new List<string>();
        if (context.Name == null)
            missing.Add("context.name");
        if (options.Count == 0)
            missing.Add("surface.options");
        string readiness = ClassifyReadiness(
            context.Name != null,
            options.Count,
            actionableOptionCount);
        var completeness = new StateCompleteness(
            missing.Count == 0 ? "contract_complete_for_supported_surface" : "partial",
            actionableOptionCount > 0
                ? "derived_from_same_validator_as_execution"
                : "temporarily_empty_while_ui_settles",
            new[]
            {
                "NEventRoom.current_event",
                "NEventLayout.rendered_text",
                "NEventLayout.OptionButtons",
                "NEventOptionButton.Option",
                "EventOption.HoverTips",
                "EventOption.WillKillPlayer"
            },
            missing);
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            context,
            surface
        });

        return new LiveObservation(
            signature,
            readiness,
            context,
            surface,
            completeness,
            game,
            Array.Empty<string>());
    }

    internal static string ClassifyReadiness(
        bool contextNameAvailable,
        int visibleOptionCount,
        int actionCount)
    {
        // A normal event can render before its option buttons are attached.
        // That is a known protocol in a transient state, not a malformed or
        // unsupported surface. Missing context semantics remain degraded.
        if (!contextNameAvailable)
            return "degraded";
        if (visibleOptionCount == 0)
            return "settling";
        return actionCount > 0 ? "ready" : "settling";
    }

    private static NativeInputResult StartOption(
        NEventRoom expectedRoom,
        NEventOptionButton expectedButton,
        EventOption expectedOption,
        int expectedIndex)
    {
        NEventRoom? currentRoom = NEventRoom.Instance;
        if (RunManager.Instance.DebugOnlyGetState()?.CurrentRoom is not EventRoom eventRoom
            || !NativeEventRoomBinding.TryCapture(eventRoom, expectedRoom, out var owner)
            || !ReferenceEquals(currentRoom, expectedRoom)
            || !ReferenceEquals(expectedButton.Event, owner.Event))
            return NativeInputResult.Rejected("event_room_changed", "The event room is no longer current.");
        NEventOptionButton[] currentButtons = expectedRoom.Layout?.OptionButtons.ToArray()
            ?? Array.Empty<NEventOptionButton>();
        if (expectedIndex < 0
            || expectedIndex >= currentButtons.Length
            || !ReferenceEquals(currentButtons[expectedIndex], expectedButton)
            || !ReferenceEquals(expectedButton.Option, expectedOption)
            || expectedOption.IsLocked
            || !expectedButton.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedButton))
        {
            return NativeInputResult.Rejected("event_option_changed", "The event option is no longer enabled at the advertised position.");
        }

        expectedButton.ForceClick();
        return NativeInputResult.Delivered(
            expectedOption.IsProceed ? ProceedDeliveryEvidence : ChooseDeliveryEvidence);
    }

    internal static NativeInputResult StartOption(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedOptionId,
        bool expectedProceed)
    {
        NEventRoom? room = NEventRoom.Instance;
        if (room == null
            || !ConnectorMod.IsLiveNode(room)
            || !string.Equals(
                entities.GetId(room, "screen"),
                expectedScreenId,
                StringComparison.Ordinal)
            || !entities.TryResolve(expectedOptionId, out EventOption? option)
            || option == null)
        {
            return NativeInputResult.Rejected(
                "event_option_changed",
                "The exact event room or option entity is no longer current.");
        }

        NEventOptionButton[] buttons = room.Layout?.OptionButtons.ToArray()
            ?? Array.Empty<NEventOptionButton>();
        (NEventOptionButton Button, int Index)[] matches = buttons
            .Select((button, index) => (Button: button, Index: index))
            .Where(entry =>
                ReferenceEquals(entry.Button.Option, option)
                && ConnectorMod.IsNodeVisible(entry.Button))
            .ToArray();
        if (matches.Length != 1
            || option.IsProceed != expectedProceed)
        {
            return NativeInputResult.Rejected(
                "event_option_changed",
                "The exact event option no longer has one matching visible native control.");
        }

        return StartOption(
            room,
            matches[0].Button,
            option,
            matches[0].Index);
    }

    private static string BuildLabel(EventOption option)
    {
        string? title = ConnectorMod.SafeGetText(() => option.Title);
        string? description = ConnectorMod.SafeGetText(() => option.Description);
        return title ?? description ?? (option.IsProceed ? "Proceed" : "Choose event option");
    }

    private static bool ReadWillKillPlayer(NEventOptionButton button, EventOption option)
    {
        if (option.WillKillPlayer == null)
            return false;
        var owner = button.Event.Owner
                    ?? throw new InvalidOperationException("A lethal event option has no current player owner.");
        return option.WillKillPlayer(owner);
    }

    private static IReadOnlyList<VisibleEventOptionTooltip> BuildTooltips(
        IEnumerable<IHoverTip> tips,
        string ownerEntityId)
    {
        var result = new List<VisibleEventOptionTooltip>();
        int cardOrdinal = 0;
        foreach (IHoverTip tip in IHoverTip.RemoveDupes(tips))
        {
            switch (tip)
            {
                case HoverTip text:
                    result.Add(new VisibleEventOptionTooltip(
                        "text",
                        text.Title == null ? null : ConnectorMod.StripRichTextTags(text.Title),
                        ConnectorMod.StripRichTextTags(text.Description),
                        null));
                    break;
                case CardHoverTip card:
                    result.Add(new VisibleEventOptionTooltip(
                        "card",
                        null,
                        null,
                        LiveContextReader.BuildCard(
                            card.Card,
                            VisibleEntityFacts.BuildTooltipCardEntityId(
                                ownerEntityId,
                                card.Card.Id.Entry,
                                cardOrdinal++),
                            displayPile: PileType.Deck)));
                    break;
                default:
                    throw new NotSupportedException($"Unsupported event-option hover-tip type: {tip.GetType().Name}");
            }
        }
        return result;
    }
}
