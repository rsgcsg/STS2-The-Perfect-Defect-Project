using System.Linq;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Targeted v2 exposure projection. Legacy producers remain unchanged;
/// current visible card bodies and entered native tip bodies remain available.</summary>
internal static class TextMenuV2Visibility
{
    internal static PlayerEnvironmentSnapshot Sanitize(PlayerEnvironmentSnapshot source)
    {
        JsonNode context = source.Interaction.Content.Context.DeepClone();
        StripKnownCombatCollections(context);
        var referents = source.Referents.Select(referent =>
        {
            if (referent.Properties == null) return referent;
            JsonNode properties = referent.Properties.DeepClone();
            StripKnownCombatCollections(properties);
            if (properties is JsonObject own)
            {
                if (referent.Role == "orb" || referent.Role == "power") own.Remove("description");
                if (referent.Role == "intent") { own.Remove("title"); own.Remove("description"); }
            }
            return referent with { Properties = properties };
        }).ToArray();
        return source with
        {
            Interaction = source.Interaction with { Content = source.Interaction.Content with { Context = context } },
            Referents = referents
        };
    }

    private static void StripKnownCombatCollections(JsonNode? node)
    {
        if (node is JsonArray array)
        {
            foreach (JsonNode? child in array) StripKnownCombatCollections(child);
            return;
        }
        if (node is not JsonObject obj) return;
        if (obj["orbs"] is JsonArray orbs)
            foreach (JsonObject orb in orbs.OfType<JsonObject>()) orb.Remove("description");
        if (obj["intents"] is JsonArray intents)
            foreach (JsonObject intent in intents.OfType<JsonObject>())
            { intent.Remove("title"); intent.Remove("description"); }
        if (obj["statuses"] is JsonArray statuses)
            foreach (JsonObject status in statuses.OfType<JsonObject>()) status.Remove("description");
        // These are the declared combat owner/container paths. Never recursively
        // strip arbitrary description fields, hand cards or entered tip surfaces.
        foreach (string key in new[] { "player", "enemies", "companions" })
            StripKnownCombatCollections(obj[key]);
    }
}
