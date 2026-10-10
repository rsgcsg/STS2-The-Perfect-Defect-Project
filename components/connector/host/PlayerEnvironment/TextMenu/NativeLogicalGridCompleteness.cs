using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Entities.UI;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using STS2Connector.NativeUi;

namespace STS2Connector.PlayerEnvironment;

/// <summary>New-profile completeness guard. A virtualized grid window cannot
/// stand in for its complete entered logical list. No card or ID is created by
/// this read-only guard, and missing bindings never become native capabilities.</summary>
internal static class NativeLogicalGridCompleteness
{
    private static readonly FieldInfo? GridCards = typeof(NCardGrid)
        .GetField("_cards", BindingFlags.Instance | BindingFlags.NonPublic);

    internal static TextMenuFrame Check(TextMenuFrame frame, NativeEntityRegistry entities)
    {
        if (frame.Page.Interaction.Kind is "inspect_card" or "relic_inspect") return frame;
        HashSet<string> publishedCards = PublishedCardIds(frame.Page.Interaction.Content.Surface);
        if (frame.LogicalDeckProof is { } deck && deck.IsCurrent())
        {
            bool content = deck.Cards.All(card => entities.TryGetExistingId(card, out string? id)
                && id != null && publishedCards.Contains(id));
            var inspectLeaves = frame.Leaves.Where(leaf => leaf.Verb == "inspect_deck_card")
                .GroupBy(leaf => leaf.SubjectReferentId ?? "", StringComparer.Ordinal)
                .ToDictionary(group => group.Key, group => group.ToArray(), StringComparer.Ordinal);
            bool actions = deck.Cards.Where(deck.CanInspect).All(card => entities.TryGetExistingId(card, out string? id)
                && id != null && inspectLeaves.TryGetValue(id, out TextMenuLeaf[]? leaves) && leaves.Length == 1
                && leaves[0].NativeWitness is { } binding && ReferenceEquals(binding.Owner, deck.Owner)
                && ReferenceEquals(binding.Subject, card));
            var gaps = Missing(content, actions);
            if (gaps.Count == 0) return frame;
            return frame with { Page = frame.Page with { Completeness = frame.Page.Completeness with
            { Status = "partial", Missing = frame.Page.Completeness.Missing.Concat(gaps).Distinct(StringComparer.Ordinal).ToArray() } } };
        }
        if (frame.LogicalGridProof is { } proof && proof.IsCurrent())
        {
            bool completeContent = proof.Cards.All(card => entities.TryGetExistingId(card, out string? id)
                && id != null && publishedCards.Contains(id));
            var bindings = frame.Leaves.Where(leaf => leaf.Verb is "select" or "deselect" or "inspect_card")
                .GroupBy(leaf => leaf.SubjectReferentId ?? "", StringComparer.Ordinal)
                .ToDictionary(group => group.Key, group => group.ToArray(), StringComparer.Ordinal);
            bool Exact(CardModel card, string verb) => entities.TryGetExistingId(card, out string? id) && id != null
                && bindings.TryGetValue(id, out TextMenuLeaf[]? matches)
                && matches.Where(leaf => leaf.Verb == verb).ToArray() is { Length: 1 } exact
                && exact[0].NativeWitness is { } witness && ReferenceEquals(witness.Owner, proof.Owner)
                && ReferenceEquals(witness.Subject, card);
            bool completeBindings = proof.Cards.Where(proof.CanToggle).All(card =>
                Exact(card, proof.IsSelected(card) ? "deselect" : "select"))
                && proof.Cards.Where(proof.CanInspect).All(card => Exact(card, "inspect_card"));
            var gaps = Missing(completeContent, completeBindings);
            if (gaps.Count == 0) return frame;
            return frame with { Page = frame.Page with { Completeness = frame.Page.Completeness with
            { Status = "partial", Missing = frame.Page.Completeness.Missing.Concat(gaps).Distinct(StringComparer.Ordinal).ToArray() } } };
        }
        Node? owner = NCapstoneContainer.Instance?.CurrentCapstoneScreen as Node
            ?? NOverlayStack.Instance?.Peek() as Node;
        if (owner == null) return frame;
        var missing = new HashSet<string>(StringComparer.Ordinal);
        foreach (NCardGrid grid in ConnectorMod.FindAll<NCardGrid>(owner))
        {
            if (!ConnectorMod.IsNodeVisible(grid)) continue;
            if (grid.GetType() != typeof(NCardGrid)
                || GridCards?.GetValue(grid) is not IReadOnlyList<CardModel> logical)
            {
                missing.Add("native_logical_grid_source_binding_missing");
                continue;
            }
            CardModel[] displayed = grid.CurrentlyDisplayedCardHolders
                .Where(holder => ConnectorMod.IsNodeVisible(holder)
                    && holder.CardNode?.Visibility == ModelVisibility.Visible && holder.CardModel != null)
                .Select(holder => holder.CardModel).ToArray();
            bool contentComplete = logical.All(card => entities.TryGetExistingId(card, out string? id)
                && id != null && publishedCards.Contains(id));
            bool bindingsComplete = NativeTextMenuRewardPages.ExactReferences(logical, displayed);
            foreach (string reason in Missing(contentComplete, bindingsComplete)) missing.Add(reason);
        }
        if (missing.Count == 0) return frame;
        return frame with { Page = frame.Page with
        {
            Completeness = frame.Page.Completeness with
            {
                Status = "partial",
                Missing = frame.Page.Completeness.Missing.Concat(missing)
                    .Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray()
            }
        } };
    }

    internal static IReadOnlyList<string> Missing(bool contentComplete, bool bindingsComplete)
    {
        var missing = new List<string>();
        if (!contentComplete) missing.Add("native_logical_public_list_content_incomplete");
        if (!bindingsComplete) missing.Add("native_logical_public_list_action_bindings_incomplete");
        return missing;
    }

    // Limit the membership check to entered logical list facts. Actual rendered
    // card observations or unrelated contextual hand cards cannot repair it.
    private static HashSet<string> PublishedCardIds(JsonNode surface)
    {
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (JsonArray? cards in new[] { surface["cards"] as JsonArray, surface["details"]?["cards"] as JsonArray })
            if (cards != null)
                foreach (JsonObject card in cards.OfType<JsonObject>())
                    if (card["entity_id"] is JsonValue value && value.TryGetValue<string>(out string? id) && id != null)
                        ids.Add(id);
        return ids;
    }
}
