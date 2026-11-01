using System;
using System.Collections.Generic;
using System.Linq;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.NativeUi;

internal static partial class NativeUiActionRuntime
{
    internal static IReadOnlyList<NativeUiActionDescriptor> DescribeAbandonCommands(AbandonRunConfirmationSurface surface) =>
        surface.Controls.Where(control => control.Enabled && (control.SemanticId == "yes" && control.Operation == "confirm_abandon"
            || control.SemanticId == "no" && control.Operation == "cancel_abandon"))
        .Select(control => NativeDescriptor($"trial_abandon:{surface.ScreenEntityId}:{control.EntityId}", control.Operation,
            "confirmation", control.Label, "NAbandonRunConfirmPopup exact current visible Yes/No",
            new[] { new ActionEntityBinding("screen", surface.ScreenEntityId), new ActionEntityBinding("control", control.EntityId) })).ToArray();

    private static IReadOnlyList<NativeUiBoundAction> BuildAbandonBindings(LiveObservation draft, AbandonRunConfirmationSurface surface) =>
        DescribeAbandonCommands(surface).Select(action => BindActionToCurrentObservation(draft, action))
            .Where(binding => binding != null).Cast<NativeUiBoundAction>().ToArray();

    private static NativeInputResult StartAbandonCommand(LiveObservation draft, NativeUiInput request, NativeUiBoundAction binding)
    {
        if (draft.Surface is not AbandonRunConfirmationSurface surface
            || !HasExactOperand(request, "screen_id", surface.ScreenEntityId))
            return NativeInputResult.Rejected("trial_abandon_owner_changed", "The exact current modal binding changed.");
        var controlBindings = binding.Candidate.EntityBindings.Where(value => value.Role == "control").ToArray();
        if (controlBindings.Length != 1) return NativeInputResult.Rejected("trial_abandon_control_changed", "The exact native control binding is unavailable.");
        string id = controlBindings[0].EntityId;
        var controls = surface.Controls.Where(control => control.EntityId == id && control.Enabled
            && control.Operation == binding.Candidate.Operation).ToArray();
        if (controls.Length != 1 || !(controls[0].SemanticId == "yes" && controls[0].Operation == "confirm_abandon"
            || controls[0].SemanticId == "no" && controls[0].Operation == "cancel_abandon"))
            return NativeInputResult.Rejected("trial_abandon_control_changed", "The exact native confirmation control changed.");
        return AbandonRunConfirmationSurfaceReader.Start(Entities, surface, id, controls[0].SemanticId == "yes");
    }
}
