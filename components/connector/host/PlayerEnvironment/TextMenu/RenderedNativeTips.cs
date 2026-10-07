using System;
using System.Collections.Generic;
using System.Text.Json.Nodes;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Only already rendered children. A valid empty native set is distinct
/// from missing containers or an unresolved child; it has no invented body.</summary>
internal static class RenderedNativeTips
{
    internal static JsonNode? Capture<T>(IEnumerable<T>? textChildren, IEnumerable<T>? cardChildren,
        Func<T, JsonObject?> readText, Func<T, JsonObject?> readCard)
    {
        if (textChildren == null || cardChildren == null) return null;
        var texts = new JsonArray();
        foreach (T child in textChildren)
        {
            JsonObject? text = readText(child);
            if (text == null) return null;
            texts.Add(text.DeepClone());
        }
        var cards = new JsonArray();
        foreach (T child in cardChildren)
        {
            JsonObject? card = readCard(child);
            if (card == null) return null;
            cards.Add(card.DeepClone());
        }
        return new JsonObject { ["text_tips"] = texts, ["card_previews"] = cards };
    }
}
