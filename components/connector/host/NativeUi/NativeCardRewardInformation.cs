using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using Godot;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.UI;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using STS2Connector.LiveHost;
using STS2Platform.NativeFoundation;

namespace STS2Connector.NativeUi;

internal sealed record NativeRewardInformationCard(
    NGridCardHolder Holder, NCard Node, Node Hitbox, CardModel Model, bool FocusEnabled, bool Clickable)
{
    internal bool CanInspect => FocusEnabled && Clickable;
}
internal sealed record NativeRewardInformationAlternative(
    NCardRewardAlternativeButton Button, CardRewardAlternative Model, string Label, bool Enabled);

/// <summary>The ordinary reward's current native input occurrence. These private
/// references never create a second selection rule or an inspector card list.</summary>
internal sealed record NativeCardRewardInformation(
    NCardRewardSelectionScreen Owner, Control Row, Control AlternativeContainer, Task Completion,
    IReadOnlyList<NativeRewardInformationCard> Cards,
    IReadOnlyList<NativeRewardInformationAlternative> Alternatives)
{
    private static readonly FieldInfo? CompletionSource = typeof(NCardRewardSelectionScreen)
        .GetField("_completionSource", BindingFlags.Instance | BindingFlags.NonPublic);

    internal static NativeCardRewardInformation? Capture(NCardRewardSelectionScreen owner, NativeEntityRegistry entities)
    {
        if (!ExactOwner(owner)
            || owner.GetNodeOrNull<Control>("UI/CardRow") is not { } row || !NativeInformationInput.Mounted(row)
            || owner.GetNodeOrNull<Control>("UI/RewardAlternatives") is not { } alternatives || !NativeInformationInput.Mounted(alternatives)
            || CompletionSource?.GetValue(owner) is not TaskCompletionSource<int?> completion
            || completion.Task.IsCompleted) return null;
        NativeCardRewardDecision decision = NativeCardRewardDecisionProvider.Capture(owner, entities);
        if (decision.Status != "captured") return null;
        CardModel[] models = NativeSemanticActionCatalog.Subjects<CardModel>(decision.Actions, "select").ToArray();
        NGridCardHolder[] holders = row.GetChildren().OfType<NGridCardHolder>().ToArray();
        if (!NativeDecisionProjection.HasExactReferenceBijection(models, holders.Select(holder => holder.CardModel)))
            return null;
        var cards = new List<NativeRewardInformationCard>();
        foreach (CardModel model in models)
        {
            NGridCardHolder holder = holders.Single(value => ReferenceEquals(value.CardModel, model));
            if (!NativeInformationInput.Mounted(holder) || !ConnectorMod.IsNodeVisible(holder)
                || holder.CardNode is not { } node || !NativeInformationInput.Mounted(node)
                || !ConnectorMod.IsNodeVisible(node) || node.Visibility != ModelVisibility.Visible
                || !ReferenceEquals(node.Model, model) || !NativeInformationInput.Mounted(holder.Hitbox)) return null;
            cards.Add(new(holder, node, holder.Hitbox, model, holder.Hitbox.IsEnabled,
                CardRewardSurfaceReader.IsHolderClickable(holder)));
        }
        CardRewardAlternative[] choices = NativeSemanticActionCatalog
            .Subjects<CardRewardAlternative>(decision.Actions, "activate").ToArray();
        NCardRewardAlternativeButton[] buttons = alternatives.GetChildren().OfType<NCardRewardAlternativeButton>().ToArray();
        if (!CardRewardAlternativePresentationBindings.TryCapture(owner, choices, buttons, out var pairs))
            return null;
        var controls = new List<NativeRewardInformationAlternative>();
        foreach (var pair in pairs)
        {
            if (pair.Button is not NCardRewardAlternativeButton button
                || !NativeInformationInput.Mounted(button) || !ConnectorMod.IsNodeVisible(button)
                || CardRewardSurfaceReader.ReadAlternativeLabel(button) is not { } label
                || string.IsNullOrWhiteSpace(label)) return null;
            controls.Add(new(button, pair.Alternative, label, button.IsEnabled));
        }
        return new(owner, row, alternatives, completion.Task, cards, controls);
    }

    internal static bool ExactOwner(NCardRewardSelectionScreen owner) =>
        NativeInformationInput.Mounted(owner) && ConnectorMod.IsNodeVisible(owner)
        && ReferenceEquals(NOverlayStack.Instance?.Peek(), owner)
        && ActiveScreenContext.Instance.IsCurrent(owner)
        && NCapstoneContainer.Instance is not { InUse: true }
        && ActiveInputResolver.Capture().OpenModal == null;

    internal bool SameOccurrence(NativeCardRewardInformation? current) => current != null
        && !Completion.IsCompleted && !current.Completion.IsCompleted
        && ReferenceEquals(Owner, current.Owner) && ReferenceEquals(Row, current.Row)
        && ReferenceEquals(AlternativeContainer, current.AlternativeContainer)
        && ReferenceEquals(Completion, current.Completion)
        && Cards.Count == current.Cards.Count && Alternatives.Count == current.Alternatives.Count
        && Cards.Zip(current.Cards).All(pair =>
            ReferenceEquals(pair.First.Holder, pair.Second.Holder)
            && ReferenceEquals(pair.First.Node, pair.Second.Node)
            && ReferenceEquals(pair.First.Hitbox, pair.Second.Hitbox)
            && ReferenceEquals(pair.First.Model, pair.Second.Model))
        && Alternatives.Zip(current.Alternatives).All(pair =>
            ReferenceEquals(pair.First.Button, pair.Second.Button)
            && ReferenceEquals(pair.First.Model, pair.Second.Model)
            && pair.First.Label == pair.Second.Label);

    internal bool Current(NativeEntityRegistry entities) => SameOccurrence(Capture(Owner, entities));

    internal bool Allows(NativeCardRewardInformation? current, NativeRewardInformationCard card, bool inspect) =>
        SameOccurrence(current) && current!.Cards.Any(value =>
            ReferenceEquals(value.Holder, card.Holder) && (inspect ? value.CanInspect : value.FocusEnabled));

    internal bool CanFocus(NativeRewardInformationCard card, NativeEntityRegistry entities) =>
        Allows(Capture(Owner, entities), card, inspect: false);

    internal bool CanInspect(NativeRewardInformationCard card, NativeEntityRegistry entities) =>
        Allows(Capture(Owner, entities), card, inspect: true);

    internal NativeInputResult Inspect(NativeRewardInformationCard card, NativeEntityRegistry entities) =>
        NativeInformationInput.Dispatch(() => CanInspect(card, entities),
            () => card.Holder.EmitSignal(NCardHolder.SignalName.AltPressed, card.Holder),
            "native_card_reward_alt_pressed; native singleton inspector");
}
