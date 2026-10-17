using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.HoverTips;
using MegaCrit.Sts2.Core.Models;

namespace STS2Platform.NativeFoundation;

// Read-only, typed native rendering for the two admitted selector kinds. No raw
// localization template, manual variable substitution or fallback can be complete.
public static class NativePublicCardDetails
{
    public sealed record Result(Dictionary<string, object?> Card, string[] Missing)
    {
        public bool Complete => Missing.Length == 0;
        public Dictionary<string, object?> PublicCard => NativePublicCardDetails.ToPublicCard(Card);
        public Dictionary<string, object?> PublicDetail => new()
        {
            ["keywords"] = Card.GetValueOrDefault("keywords"),
            ["card_previews"] = Card.GetValueOrDefault("card_previews") is List<Dictionary<string, object?>> previews
                ? previews.Select(ToPublicCard).ToArray() : null
        };
    }

    // Shape conversion only. Native owns every value and recursive hover-card
    // source; these display-only cards are not gameplay operands or action IDs.
    private static Dictionary<string, object?> ToPublicCard(Dictionary<string, object?> card)
    {
        var value = new Dictionary<string, object?>
        {
            ["entity_id"] = null, ["definition_id"] = card.GetValueOrDefault("id"),
            ["name"] = card.GetValueOrDefault("name"), ["type"] = card.GetValueOrDefault("type"),
            ["cost"] = card.GetValueOrDefault("cost"), ["star_cost"] = card.GetValueOrDefault("star_cost"),
            ["description"] = card.GetValueOrDefault("description"), ["rarity"] = card.GetValueOrDefault("rarity"),
            ["is_upgraded"] = card.GetValueOrDefault("is_upgraded"), ["is_selected"] = false,
            ["target_type"] = card.GetValueOrDefault("target_type"), ["can_play"] = null,
            ["keywords"] = card.GetValueOrDefault("keywords"),
            ["card_previews"] = card.GetValueOrDefault("card_previews") is List<Dictionary<string, object?>> previews
                ? previews.Select(ToPublicCard).ToArray() : null
        };
        if (card.GetValueOrDefault("enchantment") != null)
            value["existing_enchantment"] = new Dictionary<string, object?>
            {
                ["definition_id"] = card.GetValueOrDefault("enchantment_id"), ["name"] = card.GetValueOrDefault("enchantment"),
                ["description"] = card.GetValueOrDefault("enchantment_description"), ["amount"] = card.GetValueOrDefault("enchantment_amount"),
                ["visibility_basis"] = "card_hover_semantics"
            };
        return value;
    }

    public static Result Capture(CardModel card, PileType displayPile, CardPreviewMode mode = CardPreviewMode.Normal) =>
        Capture(card, displayPile, new HashSet<CardModel>(ReferenceEqualityComparer.Instance), mode);

    private static Result Capture(CardModel card, PileType displayPile, HashSet<CardModel> path, CardPreviewMode mode = CardPreviewMode.Normal)
    {
        var visible = new Dictionary<string, object?>();
        var missing = new List<string>();
        bool addedToPath = false;
        CardModel subject = card;
        string stage = "canonical_copy";
        try
        {
            if (path.Count >= 16 || !(addedToPath = path.Add(card)))
                throw new InvalidOperationException("Unqualified recursive native hover-card display");
            // A native CardHoverTip may refer to a readonly canonical card.
            // Initialize/render only a qualified detached presentation copy;
            // retain the original reference in cycle detection throughout.
            if (typeof(AbstractModel).GetField("<IsMutable>k__BackingField", BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(card) is false)
            {
                if (!NativeDetachedCardPreviewFactory.TryCanonicalHoverCopy(card, out CardModel? display, out string? gap)
                    || display == null) throw new InvalidOperationException(gap ?? "native_canonical_hover_copy_unavailable");
                card = display;
            }
            stage = "preflight";
            NativeCardPreviewReadQualification.InvokePreflight(card, mode, displayPile);
            stage = "card_identity";
            visible["id"] = card.Id.Entry; visible["type"] = card.Type.ToString();
            visible["rarity"] = card.Rarity.ToString(); visible["is_upgraded"] = card.IsUpgraded;
            visible["name"] = null; visible["description"] = null;
            stage = "preview_update";
            NativeCardRenderTransaction.Run(card, displayPile, () =>
            {
                stage = "title";
                visible["name"] = card.Title;
                stage = "description";
                visible["description"] = mode == CardPreviewMode.Upgrade ? card.GetDescriptionForUpgradePreview()
                    : card.GetDescriptionForPile(displayPile, card.CurrentTarget);
                stage = "energy_cost";
                visible["cost"] = card.EnergyCost.CostsX ? "X" : card.EnergyCost.GetWithModifiers(CostModifiers.All).ToString();
                stage = "star_cost";
                visible["star_cost"] = card.HasStarCostX ? "X"
                    : card.CurrentStarCost >= 0 ? card.GetStarCostWithModifiers().ToString() : null;
                stage = "target_type";
                visible["target_type"] = card.TargetType.ToString();
                stage = "enchantment";
                if (card.Enchantment != null)
                {
                    visible["enchantment_id"] = card.Enchantment.Id.Entry;
                    visible["enchantment"] = card.Enchantment.Title.GetFormattedText();
                    visible["enchantment_description"] = card.Enchantment.DynamicDescription.GetFormattedText();
                    visible["enchantment_amount"] = card.Enchantment.Amount;
                }
                stage = "hover_tips";
                var keywords = new List<Dictionary<string, object?>>();
                var previews = new List<Dictionary<string, object?>>();
                foreach (IHoverTip tip in IHoverTip.RemoveDupes(card.HoverTips))
                {
                    if (tip is HoverTip text)
                        keywords.Add(new() { ["name"] = text.Title, ["description"] = text.Description });
                    else if (tip is CardHoverTip preview)
                    {
                        Result child = Capture(preview.Card, PileType.None, path);
                        previews.Add(child.Card);
                        if (!child.Complete) missing.Add("native_selector_hover_card_incomplete");
                    }
                    else missing.Add("unqualified_native_card_hover_preview");
                }
                visible["keywords"] = keywords;
                visible["card_previews"] = previews;
                return true;
            }, mode);
            stage = "visible_text_projection";
            // Same public text as the Reference surface readers: rich-text tags removed,
            // inline icons as [file.png], description newlines as spaces.
            foreach (string key in new[] { "name", "description", "enchantment", "enchantment_description" })
                if (visible.GetValueOrDefault(key) is string raw)
                    visible[key] = key.EndsWith("description", StringComparison.Ordinal)
                        ? NativeVisibleText.Strip(raw).Replace("\n", " ") : NativeVisibleText.Strip(raw);
            if (visible.GetValueOrDefault("keywords") is List<Dictionary<string, object?>> plain)
                foreach (var tip in plain)
                    foreach (string field in new[] { "name", "description" })
                        if (tip.GetValueOrDefault(field) is string raw) tip[field] = NativeVisibleText.Strip(raw);
            foreach (string key in new[] { "name", "description", "cost", "target_type" })
                if (visible.GetValueOrDefault(key) is not string value || string.IsNullOrWhiteSpace(value))
                    missing.Add("native_selector_card_" + key);
            // The text profile has no qualified inline-icon projection yet. Do not
            // erase an icon and retain a misleading allegedly complete description.
            foreach (string? value in visible.Values.OfType<string>())
                if (value.Contains('{') || value.Contains('}'))
                    missing.Add("unqualified_native_selector_rich_text");
            if (visible.GetValueOrDefault("keywords") is List<Dictionary<string, object?>> tips)
                foreach (var tip in tips)
                    if (tip.Values.Any(value => value is not string text || string.IsNullOrWhiteSpace(text))
                        || tip.Values.OfType<string>().Any(value => value.Contains('{') || value.Contains('}')))
                        missing.Add("native_selector_hover_text");
            if (card.Enchantment != null
                && (visible.GetValueOrDefault("enchantment_description") is not string text || string.IsNullOrWhiteSpace(text)))
                missing.Add("native_selector_enchantment_description");
        }
        catch (Exception error) when (error is not NativeRenderStateViolationException)
        {
            // Private, opt-in diagnosis of an actual render failure. Type metadata
            // avoids invoking a model getter after preflight has rejected it.
            if (Environment.GetEnvironmentVariable("STS2_NATIVE_CARD_RENDER_DIAGNOSTICS") == "1")
                Console.Error.WriteLine($"[native-card-render] {subject.GetType().FullName} stage={stage}: {error}");
            missing.Add(error is InvalidOperationException && error.Message.StartsWith("native_", StringComparison.Ordinal)
                ? error.Message : "native_selector_card_render_failed");
            visible["description"] = null;
        }
        finally { if (addedToPath) path.Remove(subject); }
        return new(visible, missing.Distinct(StringComparer.Ordinal).ToArray());
    }
}
