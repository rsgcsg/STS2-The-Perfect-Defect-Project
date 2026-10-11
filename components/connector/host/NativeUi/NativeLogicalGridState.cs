using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using Godot;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.ControllerInput;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using STS2Connector.LiveHost;

namespace STS2Connector.NativeUi;

/// <summary>One immutable private binding to a native selector occurrence and
/// its full game-owned logical roster. Capturing this record does not invoke
/// selection, preview, a native filter or a completion delegate.</summary>
internal sealed record NativeLogicalGridState(
    NCardGridSelectionScreen Owner, NCardGrid Grid, Task Completion,
    IReadOnlyList<CardModel> Cards, IReadOnlyList<CardModel> Selected,
    CardSelectorPrefs Preferences, string Stage, PileType DisplayPile,
    NPeekButton Peek, bool UpgradeView, IReadOnlyList<CardModel> InspectCards,
    IReadOnlyDictionary<string, NButton> Controls)
{
    private readonly HashSet<CardModel> members = Cards.ToHashSet<CardModel>(ReferenceEqualityComparer.Instance);
    private readonly HashSet<CardModel> selectedMembers = Selected.ToHashSet<CardModel>(ReferenceEqualityComparer.Instance);
    private readonly bool inspectRosterMatches = InspectCards.Count > 0 && UniqueReferences(InspectCards)
        && SameReferences(InspectCards, Cards, ordered: false);
    internal bool IsSelected(CardModel card) => selectedMembers.Contains(card);

    private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly FieldInfo? GridCards = typeof(NCardGrid).GetField("_cards", Fields);
    private static readonly FieldInfo? GridAnimating = typeof(NCardGrid).GetField("_cardsAnimatingOutForSetCards", Fields);
    private static readonly FieldInfo? GridPile = typeof(NCardGrid).GetField("_pileType", Fields);
    private static readonly FieldInfo? SelectorInspectCards = typeof(NCardGridSelectionScreen).GetField("_cards", Fields);
    private static readonly FieldInfo? CompletionSource = typeof(NCardGridSelectionScreen).GetField("_completionSource", Fields);
    private static readonly FieldInfo? HolderClickable = typeof(NCardHolder).GetField("_isClickable", Fields);

    internal static NativeLogicalGridState? Capture(NCardGridSelectionScreen owner)
    {
        if (!NativeLogicalGridCallbacks.Supported(owner.GetType()) || !ExactOwner(owner)
            || owner.GetNodeOrNull<NCardGrid>("%CardGrid") is not { } grid
            || grid.GetType() != typeof(NCardGrid)
            || !ConnectorMod.IsLiveNode(grid) || !grid.IsNodeReady()
            || GridCards?.GetValue(grid) is not IReadOnlyList<CardModel> cards
            || GridPile?.GetValue(grid) is not PileType pile
            || GridAnimating?.GetValue(grid) is not bool gridAnimating
            || CompletionSource?.GetValue(owner) is not TaskCompletionSource<IEnumerable<CardModel>> completion
            || owner.GetNodeOrNull<NPeekButton>("%PeekButton") is not { } peek
            || !BoundedCardSelectionFacts.TryRead(owner, out CardSelectorPrefs prefs,
                out IReadOnlyList<CardModel> selected, out _)) return null;
        CardModel[] roster = cards.ToArray();
        if (!UniqueReferences(roster) || !UniqueReferences(selected)
            || selected.Any(card => !roster.Contains(card, ReferenceEqualityComparer.Instance))) return null;
        var controls = new Dictionary<string, NButton>(StringComparer.Ordinal);
        string stage = peek.IsPeeking ? "peek" : "selecting";
        void Add(string verb, string path)
        {
            if (owner.GetNodeOrNull<NButton>(path) is { } control && VisibleEnabled(control)) controls.Add(verb, control);
        }
        bool Preview(string path) => owner.GetNodeOrNull<Control>(path) is { } node && ConnectorMod.IsNodeVisible(node);
        if (!peek.IsPeeking)
        {
            if (owner is NDeckUpgradeSelectScreen
                && Preview("%UpgradeSinglePreviewContainer") && Preview("%UpgradeMultiPreviewContainer")
                || owner is NDeckEnchantSelectScreen
                && Preview("%EnchantSinglePreviewContainer") && Preview("%EnchantMultiPreviewContainer")) return null;
            switch (owner)
            {
                case NDeckCardSelectScreen:
                    if (Preview("%PreviewContainer"))
                    { stage = "preview"; Add("confirm", "%PreviewConfirm"); Add("cancel_preview", "%PreviewCancel"); }
                    else { Add("open_preview", "%Confirm"); Add("cancel_selection", "%Close"); }
                    break;
                case NDeckUpgradeSelectScreen:
                    if (Preview("%UpgradeSinglePreviewContainer") || Preview("%UpgradeMultiPreviewContainer"))
                    {
                        stage = "preview";
                        string root = Preview("%UpgradeSinglePreviewContainer") ? "%UpgradeSinglePreviewContainer" : "%UpgradeMultiPreviewContainer";
                        Add("confirm", root + "/Confirm"); Add("cancel_preview", root + "/Cancel");
                    }
                    else { Add("cancel_selection", "%Close"); Add("toggle_upgrade_view", "%Upgrades"); }
                    break;
                case NDeckTransformSelectScreen:
                    if (Preview("%PreviewContainer"))
                    { stage = "preview"; Add("confirm", "%PreviewContainer/Confirm"); Add("cancel_preview", "%PreviewContainer/Cancel"); }
                    else { Add("confirm_selection", "Confirm"); Add("cancel_selection", "%Close"); Add("toggle_upgrade_view", "%Upgrades"); }
                    break;
                case NDeckEnchantSelectScreen:
                    if (Preview("%EnchantSinglePreviewContainer") || Preview("%EnchantMultiPreviewContainer"))
                    {
                        stage = "preview";
                        string root = Preview("%EnchantSinglePreviewContainer") ? "%EnchantSinglePreviewContainer" : "%EnchantMultiPreviewContainer";
                        Add("confirm", root + "/Confirm"); Add("cancel_preview", root + "/Cancel");
                    }
                    else { Add("open_preview", "Confirm"); Add("cancel_selection", "%Close"); }
                    break;
                case NSimpleCardSelectScreen:
                case NCombatPileCardSelectScreen:
                    Add("confirm", "%Confirm");
                    // Neither exact class declares a whole-page Close control.
                    break;
            }
        }
        if (gridAnimating) { stage = "settling"; controls.Clear(); }
        if (completion.Task.IsCompleted) stage = "completed";
        CardModel[] inspectCards = SelectorInspectCards?.GetValue(owner) is IReadOnlyList<CardModel> inspect
            ? inspect.ToArray() : Array.Empty<CardModel>();
        return new(owner, grid, completion.Task, Array.AsReadOnly(roster),
            Array.AsReadOnly(selected.ToArray()), prefs, stage, pile, peek, grid.IsShowingUpgrades, Array.AsReadOnly(inspectCards),
            new System.Collections.ObjectModel.ReadOnlyDictionary<string, NButton>(controls));
    }

    internal bool IsCurrent() => Capture(Owner) is { } current
        && ReferenceEquals(current.Grid, Grid) && ReferenceEquals(current.Completion, Completion)
        && SameReferences(Cards, current.Cards) && SameReferences(Selected, current.Selected, ordered: false)
        && current.Preferences.Equals(Preferences) && current.Stage == Stage
        && current.UpgradeView == UpgradeView && SameReferences(InspectCards, current.InspectCards)
        && current.Controls.Count == Controls.Count
        && Controls.All(pair => current.Controls.TryGetValue(pair.Key, out NButton? control)
            && ReferenceEquals(pair.Value, control));

    internal bool CanToggle(CardModel card)
    {
        if (Stage != "selecting" || Completion.IsCompleted || !ExactOwner(Owner)
            || !ConnectorMod.IsNodeVisible(Grid) || Grid.FocusBehaviorRecursive == Control.FocusBehaviorRecursiveEnum.Disabled
            || !members.Contains(card)) return false;
        // A currently allocated source may have a stricter actual control guard.
        // The exact six screens never disable an unallocated logical member.
        NGridCardHolder? holder = Grid.GetCardHolder(card);
        if (holder != null && (HolderClickable?.GetValue(holder) is not true
            || !holder.Hitbox.IsEnabled || holder.CardNode?.Visibility != MegaCrit.Sts2.Core.Entities.UI.ModelVisibility.Visible)) return false;
        bool selected = IsSelected(card);
        // These two actual callbacks guard Add with count < MaxSelect. Other
        // families enter native preview at their limit, rather than a new veto.
        return selected || Owner is not (NSimpleCardSelectScreen or NCombatPileCardSelectScreen)
            || Selected.Count < Preferences.MaxSelect;
    }

    internal bool CanInspect(CardModel card) => Stage == "selecting" && !Completion.IsCompleted
        && ExactOwner(Owner) && ConnectorMod.IsNodeVisible(Grid)
        && Grid.FocusBehaviorRecursive != Control.FocusBehaviorRecursiveEnum.Disabled
        && NControllerManager.Instance?.IsUsingDirectionalNavigation == false
        && inspectRosterMatches && members.Contains(card);

    internal NativeInputResult Inspect(CardModel card)
    {
        if (!IsCurrent() || !CanInspect(card))
            return NativeInputResult.Rejected("native_logical_inspect_changed", "The exact native inspector source or input mode changed.");
        int index = ReferenceIndex(InspectCards, card);
        // Exactly the native base ShowCardDetail policy: request-list order,
        // current upgrade mode and existing game-owned model. No caller index.
        if (NGame.Instance is not { } game)
            return NativeInputResult.Rejected("native_logical_game_changed", "The native game instance is unavailable before inspect input.");
        try { game.GetInspectCardScreen().Open(InspectCards.ToList(), index, UpgradeView); }
        catch (Exception)
        { return NativeInputResult.Unknown("native_logical_inspect_unknown", "The native inspector may have opened before throwing."); }
        return NativeInputResult.Delivered("native_logical_selector_inspect_open_delivered");
    }

    internal static int ReferenceIndex<T>(IReadOnlyList<T> values, T member) where T : class
    {
        for (int index = 0; index < values.Count; index++) if (ReferenceEquals(values[index], member)) return index;
        return -1;
    }
    internal static IReadOnlyList<CardModel>? ReadLogicalCards(NCardGrid grid) =>
        grid.GetType() == typeof(NCardGrid)
            ? GridCards?.GetValue(grid) as IReadOnlyList<CardModel> : null;

    internal NativeInputResult Toggle(CardModel card, bool expectedSelected) =>
        NativeLogicalCardInput.Dispatch(card,
            () => IsCurrent() && CanToggle(card) && IsSelected(card) == expectedSelected,
            exact => NativeLogicalGridCallbacks.Press(Owner, exact),
            "native_logical_card_model_callback_delivered");

    internal NativeInputResult Click(string verb, NButton control)
    {
        if (!IsCurrent() || Completion.IsCompleted || !Controls.TryGetValue(verb, out NButton? expected)
            || !ReferenceEquals(expected, control))
            return NativeInputResult.Rejected("native_logical_control_changed", "The exact native selector control/request stage changed.");
        return NativeSelectorControl.Click(control, () => ExactOwner(Owner), VisibleEnabled,
            button => button.ForceClick(), "native_logical_selector_control_clicked");
    }

    internal static bool ExactOwner(NCardGridSelectionScreen owner) =>
        ReferenceEquals(NOverlayStack.Instance?.Peek(), owner) && ActiveInputResolver.IsVisibleActiveOverlay(owner)
        && NCapstoneContainer.Instance is not { InUse: true }
        && ActiveInputResolver.Capture().OpenModal == null
        && ActiveScreenContext.Instance.IsCurrent(owner);
    internal static bool VisibleEnabled(NButton button) => ConnectorMod.IsLiveNode(button)
        && ConnectorMod.IsNodeVisible(button) && button.IsEnabled;
    internal static bool UniqueReferences<T>(IReadOnlyList<T> members) where T : class =>
        members.All(member => member != null) && members.Distinct(ReferenceEqualityComparer.Instance).Count() == members.Count;
    internal static bool SameReferences<T>(IReadOnlyList<T> left, IReadOnlyList<T> right, bool ordered = true) where T : class =>
        left.Count == right.Count && (ordered ? left.Zip(right).All(pair => ReferenceEquals(pair.First, pair.Second))
            : right.ToHashSet<T>(ReferenceEqualityComparer.Instance).IsSupersetOf(left));
}
