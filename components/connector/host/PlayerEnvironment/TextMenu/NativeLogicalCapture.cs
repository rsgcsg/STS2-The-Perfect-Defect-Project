using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using STS2Connector.LiveHost;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Host-private capture adapter for native-logical-v1. Every returned
/// leaf is an existing exact native control; no information or card cursor is
/// introduced. Publication, public handles, immutable bytes and dispatch admission
/// belong to the separate native-logical service.</summary>
internal static class NativeLogicalCapture
{
    internal const int MaximumActions = 65_536;

    internal static TextMenuFrame Prepare(TextMenuFrame frame, NativeEntityRegistry entities)
    {
        frame = frame with { Page = TextMenuV2Visibility.Sanitize(frame.Page) };
        frame = AppendPeek(frame, entities);
        frame = NativeLogicalPresentation.Attach(frame, entities);
        frame = NativeLogicalGridCompleteness.Check(frame, entities);
        return Validate(frame);
    }

    internal static TextMenuFrame Validate(TextMenuFrame frame)
    {
        // Both native projection and complete flattened leaves must fit the
        // announced bound. Never advertise a partial prefix as a complete relation.
        bool capacity = frame.Leaves.Count > MaximumActions
            || frame.Page.BoundActions.Status == "truncated"
                && frame.Page.BoundActions.TotalCount > MaximumActions;
        if (capacity)
            return frame with
            {
                Page = frame.Page with
                {
                    Status = NativeLogicalCapturePolicy.FailureStatus(frame.Page.Status),
                    Completeness = frame.Page.Completeness with
                    {
                        Status = "partial",
                        Missing = frame.Page.Completeness.Missing.Append("native_logical_action_capacity_exceeded")
                            .Distinct(StringComparer.Ordinal).ToArray()
                    }
                },
                Leaves = Array.Empty<TextMenuLeaf>()
            };
        return NativeLogicalCapturePolicy.CloseIncompleteRequiredScope(frame);
    }

    private static TextMenuFrame AppendPeek(TextMenuFrame frame, NativeEntityRegistry entities)
    {
        if (frame.Page.Interaction.Stage == "native_information_page"
            || frame.LogicalGridProof?.Completion.IsCompleted == true
            || frame.LogicalGridProof?.Stage == "settling"
            || NCapstoneContainer.Instance is { InUse: true }) return frame;
        Node? owner = NOverlayStack.Instance?.Peek() as Node;
        NPeekButton? button = owner?.GetNodeOrNull<NPeekButton>("%PeekButton");
        if (button == null && frame.Page.Interaction.Kind == "combat_hand_card_selection")
        {
            owner = NPlayerHand.Instance;
            button = NPlayerHand.Instance?.PeekButton;
        }
        if (owner == null || button == null || !button.IsEnabled
            || !ConnectorMod.IsNodeVisible(button)) return frame;
        Node exactOwner = owner;
        NPeekButton exact = button;
        bool wasPeeking = button.IsPeeking;
        string id = entities.GetId(button, "peek_control");
        string verb = wasPeeking ? "close_peek" : "open_peek";
        var referents = frame.Page.Referents.ToList();
        if (!referents.Any(value => value.ReferentId == id))
            referents.Add(new PlayerEnvironmentReferent(id, "peek", "control", "Peek",
                new(true, true, false, false, "native_visible_fact"), null,
                new JsonObject { ["is_peeking"] = wasPeeking }));
        // Replace any legacy close edge with the same source control, keeping
        // one structural member for this exact native toggle.
        var leaves = frame.Leaves.Where(leaf => leaf.Verb is not ("close_peek" or "open_peek")).ToList();
        leaves.Add(new TextMenuLeaf("native_peek:" + id + ":" + verb, "root", verb,
            wasPeeking ? "Return from Peek" : "Peek at battlefield", id,
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => TogglePeek(exactOwner, exact, wasPeeking)));
        return frame with { Page = frame.Page with { Referents = referents }, Leaves = leaves };
    }

    private static NativeInputResult TogglePeek(Node owner, NPeekButton button, bool expectedPeeking)
    {
        bool owns = owner is NPlayerHand hand
            ? ReferenceEquals(NPlayerHand.Instance, hand) && ReferenceEquals(hand.PeekButton, button)
                && hand.CurrentMode is NPlayerHand.Mode.SimpleSelect or NPlayerHand.Mode.UpgradeSelect
            : ReferenceEquals(NOverlayStack.Instance?.Peek(), owner)
                && ReferenceEquals(owner.GetNodeOrNull<NPeekButton>("%PeekButton"), button);
        if (!owns || !ConnectorMod.IsLiveNode(owner) || !ConnectorMod.IsLiveNode(button)
            || !ConnectorMod.IsNodeVisible(button) || !button.IsEnabled
            || button.IsPeeking != expectedPeeking)
            return NativeInputResult.Rejected("native_peek_control_changed",
                "The exact enabled native Peek control or state changed.");
        button.ForceClick();
        return NativeInputResult.Delivered("native_peek_button_clicked");
    }
}
