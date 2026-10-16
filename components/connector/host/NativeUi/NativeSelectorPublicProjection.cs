using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Models;

namespace STS2Connector.NativeUi;

/// <summary>Shape-only projection of the existing selector occurrence. Every
/// model value comes from the one qualified native public-card formatter.
/// Detached display objects never receive entity IDs or input bindings.</summary>
internal static class NativeSelectorPublicProjection
{
    internal sealed record Presentation(Dictionary<string, object?> Card, IReadOnlyList<string> Missing);
    internal sealed record Result(JsonObject Request, JsonArray Options, JsonObject? EnchantmentSummary,
        IReadOnlyList<string> Missing);

    internal static Result Project(NativeSelectorPreviewFacts facts, string ownerId, string requestId,
        Func<CardModel, string> entityId, PileType originalPile,
        Func<CardModel, PileType, CardPreviewMode, Presentation> render)
    {
        var missing = new HashSet<string>(facts.Missing.Concat(facts.Request.Missing), StringComparer.Ordinal);
        var options = new JsonArray();
        foreach (NativeSelectorCandidatePreview candidate in facts.Candidates)
        {
            string subject = entityId(candidate.Original);
            var candidateMissing = new HashSet<string>(candidate.Missing, StringComparer.Ordinal);
            Presentation before = render(candidate.Original, originalPile, CardPreviewMode.Normal);
            candidateMissing.UnionWith(before.Missing);
            JsonObject beforeCard = Card(before.Card, subject, facts.Request.Selected.Contains(candidate.Original, ReferenceEqualityComparer.Instance));
            JsonObject? afterCard = null;
            string availability = facts.RequiresAggregateAfterPresentation ? "unavailable" : "not_applicable";
            if (candidate.Detached is { } detached)
            {
                Presentation after = render(detached.Display, detached.DisplayPile, detached.PreviewMode);
                candidateMissing.UnionWith(after.Missing);
                afterCard = Card(after.Card, subject, false);
                availability = after.Missing.Count == 0 ? "available" : "unavailable";
            }
            if (facts.RequiresAggregateAfterPresentation && (!candidate.ModelReady || candidate.Detached == null))
                candidateMissing.Add("native_selector_required_after_presentation_unavailable");
            if (candidateMissing.Count > 0) missing.Add("native_selector_candidate_presentation_incomplete");
            missing.UnionWith(candidateMissing);
            options.Add(new JsonObject
            {
                ["subject_entity_id"] = subject, ["before"] = beforeCard, ["after"] = afterCard,
                ["display_context"] = candidate.DisplayContext, ["after_availability"] = availability,
                ["missing"] = Strings(candidateMissing.Order(StringComparer.Ordinal))
            });
        }
        NativeSelectorOccurrenceFacts request = facts.Request;
        var publicRequest = new JsonObject
        {
            ["entity_id"] = requestId, ["owner_entity_id"] = ownerId,
            ["family"] = request.Family, ["stage"] = request.Stage,
            ["selection_model"] = request.SelectionModel, ["add_transition"] = request.AddTransition,
            ["min_select"] = request.MinSelect, ["max_select"] = request.MaxSelect,
            ["require_manual_confirmation"] = request.RequireManualConfirmation,
            ["displayed_holder_count"] = request.DisplayedHolderCount,
            ["selected_entity_ids"] = Strings(request.Selected.Select(entityId)),
            ["can_finish_now"] = request.CanFinishNow, ["can_decline_now"] = request.CanDeclineNow,
            ["mechanics_qualified"] = request.MechanicsQualified,
            ["preview_information_complete"] = missing.Count == 0,
            ["capability_revision"] = NativeSelectorPublicFacts.CapabilityRevision,
            ["missing"] = Strings(missing.Order(StringComparer.Ordinal))
        };
        JsonObject? summary = facts.EnchantmentSummary is { } enchantment
            ? new() { ["title"] = enchantment.Title, ["description"] = enchantment.Description, ["amount"] = enchantment.Amount }
            : null;
        return new(publicRequest, options, summary, missing.Order(StringComparer.Ordinal).ToArray());
    }

    private static JsonObject Card(Dictionary<string, object?> card, string subject, bool selected)
    {
        JsonObject result = JsonSerializer.SerializeToNode(card) as JsonObject
            ?? throw new InvalidOperationException("Native public card shape was not an object.");
        result["entity_id"] = subject;
        result["is_selected"] = selected;
        return result;
    }

    private static JsonArray Strings(IEnumerable<string> values) => new(values.Select(value => (JsonNode?)JsonValue.Create(value)).ToArray());
}
