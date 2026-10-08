using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using Godot;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using STS2Connector.NativeUi;
using STS2Connector.Authority;
using STS2Connector.LiveHost;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Peek is still owned by the actual unvirtualized choice/bundle screen.
/// Hidden holder nodes do not erase that occurrence or invent a new selection.</summary>
internal static class NativeLogicalNonGridPeek
{
    internal static TextMenuFrame? TryCapture(SnapshotBuildResult native, PlayerEnvironmentSnapshot page, NativeEntityRegistry entities)
    {
        if (!EnvironmentIdentityRuntime.ExecutionAvailable(native.HostObservation.Game)) return null;
        Node? owner = NOverlayStack.Instance?.Peek() as Node;
        if (owner is not (NChooseACardSelectionScreen or NChooseABundleSelectionScreen)
            || owner.GetNodeOrNull<NPeekButton>("%PeekButton") is not { IsPeeking: true, IsEnabled: true } peek
            || !ConnectorMod.IsNodeVisible(peek) || NCapstoneContainer.Instance is { InUse: true }
            || owner is not IScreenContext context || !ActiveScreenContext.Instance.IsCurrent(context)
            || ActiveInputResolver.Capture().OpenModal != null) return null;
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
        object? completion = owner.GetType().GetField("_completionSource", flags)?.GetValue(owner);
        Task? task = completion switch
        {
            TaskCompletionSource<IEnumerable<CardModel>> choice => choice.Task,
            TaskCompletionSource<IEnumerable<IReadOnlyList<CardModel>>> bundle => bundle.Task,
            _ => null
        };
        if (task == null || task.IsCompleted) return null;
        string id = entities.GetId(owner, "screen");
        string kind = owner is NChooseACardSelectionScreen ? "native_generated_card_choice" : "card_bundle_selection";
        var referents = page.Referents.ToList();
        var surface = new JsonObject { ["kind"] = kind, ["stage"] = "peek", ["is_peeking"] = true };
        if (owner is NChooseABundleSelectionScreen)
        {
            NCardBundle? selected = owner.GetType().GetField("_selectedBundle", flags)?.GetValue(owner) as NCardBundle;
            string? selectedId = selected == null ? null : entities.GetId(selected, "bundle");
            surface["selected_bundle_referent_id"] = selectedId;
            if (selectedId != null && !referents.Any(referent => referent.ReferentId == selectedId))
                referents.Add(new(selectedId, "bundle", "entity", "Selected bundle",
                    new(true, false, true, false, "native_current_parent_selection"), null, null));
        }
        // Only the battlefield currently revealed by Peek and the public current
        // parent-selection fact are projected. Unopened bundle contents are not
        // aggregated into this view merely because private _bundles exists.
        PlayerEnvironmentSnapshot captured = page with
        {
            Status = "interactive", Referents = referents,
            Completeness = new("complete", "native_peek_current_battlefield_and_parent_selection",
                "exact_native_peek_return_control", Array.Empty<string>(), page.Completeness.HiddenByPolicy),
            Interaction = page.Interaction with
            {
                InteractionId = id, Kind = kind, Stage = "peek",
                ContentSchema = "sts2.player-environment/surface/native_logical_peek-1",
                Content = page.Interaction.Content with { Surface = surface },
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
        // NativeLogicalCapture.AppendPeek supplies the one current toggle with
        // owner/control/state revalidation, preserving this same native child.
        return new(captured, "native_peek:" + id + ":" + entities.GetId(task, "native_selector_request"), Array.Empty<TextMenuLeaf>());
    }
}
