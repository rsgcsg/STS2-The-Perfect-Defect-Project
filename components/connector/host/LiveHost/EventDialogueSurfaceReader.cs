using STS2Connector.NativeUi;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Nodes.Events;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.addons.mega_text;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.LiveHost;

/// <summary>
/// Player-visible ancient-event dialogue. The game constructs nodes for every
/// future line up front, so this provider deliberately exposes only indices at
/// or before the current line. Reading the backing dialogue list would leak
/// unrevealed text and is therefore forbidden by this contract.
/// </summary>
internal sealed class EventDialogueSurfaceReader : ILiveSurfaceReader
{
    private const string SurfaceKind = "event_dialogue";
    internal const string AdvanceDeliveryEvidence = "native_dialogue_hitbox_clicked";

    private static readonly FieldInfo? CurrentLineField =
        typeof(NAncientEventLayout).GetField(
            "_currentDialogueLine",
            BindingFlags.Instance | BindingFlags.NonPublic);

    public string Kind => SurfaceKind;

    public InputOwnerLayer Layer => InputOwnerLayer.Room;

    public LiveObservation? TryBuild(
        ActiveSurfaceSnapshot snapshot,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        NEventRoom? room = NEventRoom.Instance;
        if (runState?.CurrentRoom is not EventRoom eventRoom
            || room == null
            || !ConnectorMod.IsLiveNode(room)
            || CombatManager.Instance.IsInProgress)
        {
            return null;
        }

        if (!NativeEventRoomBinding.TryCapture(eventRoom, room, out var owner))
            return null;

        EventLiveContext context = LiveContextReader.BuildEvent(owner);
        if (!context.InDialogue)
            return null;

        NAncientEventLayout? layout = owner.Layout as NAncientEventLayout;
        NAncientDialogueHitbox? hitbox = layout?.GetNodeOrNull<NAncientDialogueHitbox>("%DialogueHitbox");
        VBoxContainer? container = layout?.GetNodeOrNull<VBoxContainer>("%DialogueContainer");
        Control? fakeNextContainer = layout?.GetNodeOrNull<Control>("%FakeNextButtonContainer");
        MegaLabel? fakeNextLabel = fakeNextContainer?.GetNodeOrNull<MegaLabel>("FakeNextButton/Label");
        if (layout == null
            || hitbox == null
            || container == null
            || CurrentLineField?.GetValue(layout) is not int currentLine)
        {
            return BindingUnavailable(game, context, "Ancient dialogue controls or the exact current-line binding are unavailable.");
        }

        NAncientDialogueLine[] allLineNodes = container.GetChildren()
            .OfType<NAncientDialogueLine>()
            .ToArray();
        if (currentLine < 0 || currentLine >= allLineNodes.Length)
            return BindingUnavailable(game, context, "The current dialogue line does not match the rendered dialogue nodes.");

        var revealed = new List<VisibleDialogueLine>(currentLine + 1);
        for (int index = 0; index <= currentLine; index++)
        {
            NAncientDialogueLine lineNode = allLineNodes[index];
            string? text = ReadLineText(lineNode);
            if (text == null)
                return BindingUnavailable(game, context, $"Revealed dialogue line {index} has no readable player-visible text.");
            revealed.Add(new VisibleDialogueLine(
                entities.GetId(lineNode, "dialogue_line"),
                index,
                text,
                ReadSpeaker(lineNode),
                index == currentLine));
        }

        string advanceLabel = ReadLabel(fakeNextLabel) ?? "Continue";
        bool canAdvance = ConnectorMod.IsNodeVisible(hitbox) && hitbox.IsEnabled;
        var surface = new EventDialogueSurface(
            SurfaceKind,
            entities.GetId(layout, "screen"),
            currentLine,
            revealed,
            advanceLabel)
        {
            CanAdvance = canAdvance
        };
        string readiness = canAdvance ? "ready" : "settling";
        var completeness = new StateCompleteness(
            "contract_complete_for_revealed_ancient_dialogue",
            canAdvance
                ? "derived_from_current_dialogue_hitbox"
                : "temporarily_empty_while_dialogue_transitions_to_options",
            new[]
            {
                "NAncientEventLayout._currentDialogueLine",
                "NAncientEventLayout.%DialogueContainer revealed prefix only",
                "NAncientDialogueLine.%Text+speaker-tail",
                "NAncientEventLayout.%DialogueHitbox+%FakeNextButtonContainer"
            },
            Array.Empty<string>());
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

    internal static NativeInputResult StartDirectAdvance(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedLineId,
        int expectedLine)
    {
        NEventRoom? room = NEventRoom.Instance;
        if (room == null
            || !ConnectorMod.IsLiveNode(room)
            || !entities.TryResolve(expectedScreenId, out NAncientEventLayout? layout)
            || layout == null
            || CurrentLineField?.GetValue(layout) is not int currentLine
            || currentLine != expectedLine)
        {
            return NativeInputResult.Rejected(
                "event_dialogue_changed",
                "The exact ancient dialogue owner or line is no longer current.");
        }
        NAncientDialogueHitbox? hitbox = layout.GetNodeOrNull<NAncientDialogueHitbox>("%DialogueHitbox");
        VBoxContainer? container = layout.GetNodeOrNull<VBoxContainer>("%DialogueContainer");
        NAncientDialogueLine? line = container?.GetChildren()
            .OfType<NAncientDialogueLine>()
            .ElementAtOrDefault(expectedLine);
        if (hitbox == null
            || line == null
            || !string.Equals(
                entities.GetId(line, "dialogue_line"),
                expectedLineId,
                StringComparison.Ordinal))
        {
            return NativeInputResult.Rejected(
                "event_dialogue_changed",
                "The exact revealed dialogue line or native advance control no longer resolves.");
        }
        return StartAdvance(room, layout, hitbox, expectedLine);
    }

    private static NativeInputResult StartAdvance(
        NEventRoom expectedRoom,
        NAncientEventLayout expectedLayout,
        NAncientDialogueHitbox expectedHitbox,
        int expectedLine)
    {
        if (RunManager.Instance.DebugOnlyGetState()?.CurrentRoom is not EventRoom eventRoom
            || !NativeEventRoomBinding.TryCapture(eventRoom, expectedRoom, out var owner)
            || !ReferenceEquals(owner.Layout, expectedLayout)
            || !ConnectorMod.IsLiveNode(expectedLayout)
            || !ConnectorMod.IsNodeVisible(expectedHitbox)
            || !expectedHitbox.IsEnabled
            || CurrentLineField?.GetValue(expectedLayout) is not int currentLine
            || currentLine != expectedLine)
        {
            return NativeInputResult.Rejected(
                "event_dialogue_changed",
                "The advertised ancient dialogue line is no longer current or advanceable.");
        }

        expectedHitbox.ForceClick();
        return NativeInputResult.Delivered(AdvanceDeliveryEvidence);
    }

    private static string? ReadLineText(NAncientDialogueLine line)
    {
        MegaRichTextLabel? label = line.GetNodeOrNull<MegaRichTextLabel>("%Text");
        if (label == null)
            return null;
        try
        {
            return NormalizeText(label.Text?.ToString());
        }
        catch
        {
            return null;
        }
    }

    private static string ReadSpeaker(NAncientDialogueLine line)
    {
        Control? ancientTail = line.GetNodeOrNull<Control>("%DialogueTailLeft");
        Control? characterTail = line.GetNodeOrNull<Control>("%DialogueTailRight");
        if (ancientTail?.Visible == true && characterTail?.Visible != true)
            return "ancient";
        if (characterTail?.Visible == true && ancientTail?.Visible != true)
            return "character";
        return "unknown";
    }

    private static string? ReadLabel(MegaLabel? label)
    {
        if (label == null || !ConnectorMod.IsNodeVisible(label))
            return null;
        try
        {
            return NormalizeText(label.Text?.ToString());
        }
        catch
        {
            return null;
        }
    }

    private static string? NormalizeText(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return null;
        string stripped = ConnectorMod.StripRichTextTags(value);
        string normalized = string.Join(" ", stripped
            .Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        return normalized.Length == 0 ? null : normalized;
    }

    private static LiveObservation BindingUnavailable(
        GameBuildIdentity game,
        EventLiveContext context,
        string reason)
    {
        var surface = new UnsupportedSurface("unsupported", SurfaceKind, reason);
        var completeness = new StateCompleteness(
            "partial",
            "empty_fail_closed",
            new[] { "NAncientEventLayout exact-version binding" },
            new[] { "current_revealed_line", "advance_action" });
        string signature = StableIdentityHash.Object(new { game.Version, context, reason });
        return new LiveObservation(
            signature,
            "degraded",
            context,
            surface,
            completeness,
            game,
            new[] { "event_dialogue_binding_unavailable" })
        {
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.surface.event_dialogue.binding_unavailable",
                    "error",
                    "surface",
                    "actions_suppressed",
                    "update_host_adapter",
                    reason)
            }
        };
    }
}
