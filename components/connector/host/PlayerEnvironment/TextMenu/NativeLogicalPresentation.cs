using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Entities.UI;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.addons.mega_text;
using STS2Connector.LiveHost;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Native logical profile only: actual rendered values are kept apart
/// from same-capture logical values. Reading these labels never refreshes them.</summary>
internal static class NativeLogicalPresentation
{
    internal static void AddCardDisplay(JsonObject destination, NCard? card)
    {
        if (card == null || card.Visibility != ModelVisibility.Visible) return;
        MegaLabel? star = card.GetNodeOrNull<MegaLabel>("%StarLabel");
        Control? starIcon = card.GetNodeOrNull<Control>("%StarIcon");
        MegaLabel? enchantment = card.GetNodeOrNull<MegaLabel>("%Enchantment/Label");
        Control? enchantmentTab = card.GetNodeOrNull<Control>("%Enchantment");
        destination["star_cost_visible"] = starIcon != null && ConnectorMod.IsNodeVisible(starIcon);
        destination["displayed_star_cost"] = star != null && starIcon != null
            && ConnectorMod.IsNodeVisible(starIcon) ? star.Text : null;
        destination["enchantment_visible"] = enchantmentTab != null && ConnectorMod.IsNodeVisible(enchantmentTab);
        destination["displayed_enchantment"] = enchantment != null && enchantmentTab != null
            && ConnectorMod.IsNodeVisible(enchantmentTab) ? enchantment.Text : null;
    }

    internal static JsonObject ResourceFacts(NStarCounter counter, JsonNode context)
    {
        MegaRichTextLabel? label = counter.GetNodeOrNull<MegaRichTextLabel>("%CountLabel");
        return ResourceFacts(label != null && ConnectorMod.IsNodeVisible(label) ? label.Text : null,
            context["player"]?["stars"]);
    }

    internal static JsonObject ResourceFacts(string? displayed, JsonNode? logical) => new()
    {
        ["presentation_basis"] = "native_current_star_counter",
        ["displayed_text"] = displayed,
        ["logical_stars"] = logical?.DeepClone()
    };

    internal static TextMenuFrame Attach(TextMenuFrame frame, NativeEntityRegistry entities)
    {
        if (frame.Page.Interaction.Content.Surface is not JsonObject source) return frame;
        JsonObject surface = (JsonObject)source.DeepClone();
        Node? root = NGame.Instance?.GetTree()?.Root;
        var displayed = new JsonArray();
        var referents = frame.Page.Referents.ToList();
        if (root != null)
            foreach (NCard card in ConnectorMod.FindAll<NCard>(root))
            {
                if (!ConnectorMod.IsNodeVisible(card) || card.Visibility != ModelVisibility.Visible
                    || card.Model == null) continue;
                var title = card.GetNodeOrNull<MegaLabel>("%TitleLabel");
                var cost = card.GetNodeOrNull<MegaLabel>("%EnergyLabel");
                var description = card.GetNodeOrNull<MegaRichTextLabel>("%DescriptionLabel");
                if (title == null || cost == null || description == null) continue;
                var facts = new JsonObject
                {
                    ["card_referent_id"] = entities.GetId(card.Model, "card"),
                    ["displayed_title"] = title.Text,
                    ["displayed_cost"] = cost.Text,
                    ["displayed_description"] = description.Text
                };
                AddCardDisplay(facts, card);
                displayed.Add(facts);
                string id = entities.GetId(card.Model, "card");
                if (!referents.Any(value => value.ReferentId == id))
                    referents.Add(new PlayerEnvironmentReferent(id, "card", "entity", title.Text,
                        new(true, true, false, false, "native_visible_fact"),
                        "sts2.player-environment/referent/native_displayed_card-1", facts.DeepClone()));
            }
        surface["actual_displayed_cards"] = displayed;
        if (NOverlayStack.Instance?.Peek() is NGameOverScreen gameOver)
        {
            var summary = new JsonArray();
            foreach (MegaLabel label in ConnectorMod.FindAll<MegaLabel>(gameOver))
                if (ConnectorMod.IsNodeVisible(label)) summary.Add(label.Text);
            foreach (MegaRichTextLabel label in ConnectorMod.FindAll<MegaRichTextLabel>(gameOver))
                if (ConnectorMod.IsNodeVisible(label)) summary.Add(label.Text);
            surface["actual_displayed_summary_text"] = summary;
        }
        return frame with { Page = frame.Page with
        {
            Referents = referents,
            Interaction = frame.Page.Interaction with
            { Content = frame.Page.Interaction.Content with { Surface = surface } }
        } };
    }
}
