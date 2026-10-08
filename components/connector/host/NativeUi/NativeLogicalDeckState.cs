using System;
using System.Collections.Generic;
using System.Linq;
using Godot;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using STS2Connector.LiveHost;

namespace STS2Connector.NativeUi;

/// <summary>The entered deck view's complete native sorted roster and exact
/// return/control binding. This is UI presentation input, not card selection.</summary>
internal sealed record NativeLogicalDeckState(NDeckViewScreen Owner, NCardGrid Grid,
    IReadOnlyList<CardModel> Cards, NButton Back, bool UpgradeView)
{
    private readonly HashSet<CardModel> members = Cards.ToHashSet<CardModel>(ReferenceEqualityComparer.Instance);
    internal static NativeLogicalDeckState? Capture(NDeckViewScreen owner)
    {
        if (!ExactOwner(owner) || owner.GetNodeOrNull<NCardGrid>("CardGrid") is not { } grid
            || !grid.IsNodeReady() || NativeLogicalGridState.ReadLogicalCards(grid) is not { } cards
            || owner.GetNodeOrNull<NButton>("BackButton") is not { } back) return null;
        CardModel[] models = cards.ToArray();
        return NativeLogicalGridState.UniqueReferences(models)
            ? new(owner, grid, Array.AsReadOnly(models), back, grid.IsShowingUpgrades) : null;
    }
    internal bool IsCurrent() => Capture(Owner) is { } current && ReferenceEquals(current.Grid, Grid)
        && ReferenceEquals(current.Back, Back) && current.UpgradeView == UpgradeView
        && NativeLogicalGridState.SameReferences(Cards, current.Cards);
    internal bool CanInspect(CardModel card) => ExactOwner(Owner) && NativeLogicalGridState.VisibleEnabled(Back)
        && members.Contains(card);

    internal NativeInputResult Inspect(CardModel card)
    {
        if (!IsCurrent() || !CanInspect(card) || NGame.Instance is not { } game)
            return NativeInputResult.Rejected("native_logical_deck_inspect_changed", "The exact native deck/source/return control changed.");
        int index = NativeLogicalGridState.ReferenceIndex(Cards, card);
        NInspectCardScreen inspect = game.GetInspectCardScreen();
        // Preserve NCardsViewScreen's public-control return policy. There is no
        // protected invocation, field write, holder or caller-selected index.
        void ReturnNativeControl()
        {
            if (!ConnectorMod.IsLiveNode(inspect)) return;
            NativeLogicalInspectorReturn.Restore(inspect.Visible,
                () => OwnsCapstone(Owner) && ReferenceEquals(Owner.GetNodeOrNull<NButton>("BackButton"), Back)
                    && ConnectorMod.IsLiveNode(Back), () => Back.Enable());
        }
        var stages = new List<NativeInputStage>();
        try
        {
            Back.Disable();
            stages.Add(new(NativeInputStageKind.InspectionReturnControl, NativeInputDelivery.Delivered,
                "native_deck_return_control_disabled"));
            inspect.Open(Cards.ToList(), index, UpgradeView);
            stages.Add(new(NativeInputStageKind.CardInspectionOpen, NativeInputDelivery.Delivered,
                "native_deck_inspection_open_input_delivered"));
            inspect.Connect(CanvasItem.SignalName.VisibilityChanged, Callable.From(ReturnNativeControl), 4u);
        }
        catch (Exception)
        {
            if (!stages.Any(stage => stage.Stage == NativeInputStageKind.CardInspectionOpen))
                stages.Add(new(NativeInputStageKind.CardInspectionOpen, NativeInputDelivery.Unknown,
                    "native_deck_inspection_open_threw"));
            return NativeInputResult.Unknown("native_logical_deck_inspect_unknown",
                "Native deck/inspect presentation input may have been delivered before throwing.", stages.ToArray());
        }
        return NativeInputResult.DeliveredStages("native_logical_deck_full_list_inspect_open_delivered", stages.ToArray());
    }

    internal NativeInputResult Click(string path, NButton button)
    {
        if (!IsCurrent() || !ReferenceEquals(Owner.GetNodeOrNull<NButton>(path), button))
            return NativeInputResult.Rejected("native_logical_deck_control_changed", "The exact native deck control changed.");
        return NativeSelectorControl.Click(button, () => ExactOwner(Owner), NativeLogicalGridState.VisibleEnabled,
            control => control.ForceClick(), "native_logical_deck_control_clicked");
    }
    private static bool OwnsCapstone(NDeckViewScreen owner) => ConnectorMod.IsLiveNode(owner)
        && ConnectorMod.IsNodeVisible(owner) && ReferenceEquals(NCapstoneContainer.Instance?.CurrentCapstoneScreen, owner);
    internal static bool ExactOwner(NDeckViewScreen owner) => OwnsCapstone(owner)
        && ActiveScreenContext.Instance.IsCurrent(owner) && ActiveInputResolver.Capture().OpenModal == null;
}
