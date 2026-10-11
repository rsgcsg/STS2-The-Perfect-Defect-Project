using System.Text.Json;

namespace STS2HumanAnnotator.Core;

/// <summary>Integrity of a captured public projection, never a native legality predicate.</summary>
internal static class SourceCaptureCodec
{
    private static readonly string[] Domains = { "persistent", "interaction", "referents", "catalog" };

    internal static bool Validate(JsonElement body, PublicCaptureReference capture, bool requireFull = false,
        IReadOnlyList<SourcePublicAction>? actions = null) => Validate(body, capture.SnapshotId,
            capture.ScopeId, capture.StreamGeneration, requireFull, actions);

    internal static bool Validate(JsonElement body, string snapshot, string scope, string generation,
        bool requireFull = false, IReadOnlyList<SourcePublicAction>? actions = null)
    {
        Require(body.ValueKind == JsonValueKind.Object, "source_capture_shape_invalid");
        JsonElement completeness = Object(body, "completeness");
        string[] included = Strings(completeness, "included"), missing = Strings(completeness, "missing");
        Require(included.SequenceEqual(Domains.Where(included.Contains))
            && missing.SequenceEqual(Domains.Where(value => !included.Contains(value))),
            "source_capture_scope_completeness_invalid");
        bool full = Boolean(completeness, "full_reference_complete");
        Require(Text(completeness, "status") == (missing.Length == 0 ? "complete" : "partial")
            && full == (missing.Length == 0), "source_capture_scope_completeness_invalid");

        // Explicit null persistent facts are legal in the Connector's nullable domain.
        // A missing property or malformed non-null domain is not an explicit null fact.
        JsonElement persistent = Field(body, "persistent");
        if (!included.Contains("persistent")) Require(persistent.ValueKind == JsonValueKind.Null, "source_capture_shape_invalid");
        else if (persistent.ValueKind != JsonValueKind.Null)
        {
            Require(persistent.ValueKind == JsonValueKind.Object, "source_capture_shape_invalid");
            _ = Text(persistent, "content_schema");
            Require(Field(persistent, "content").ValueKind != JsonValueKind.Null, "source_capture_shape_invalid");
        }
        JsonElement interaction = Field(body, "interaction");
        if (!included.Contains("interaction")) Require(interaction.ValueKind == JsonValueKind.Null, "source_capture_shape_invalid");
        else
        {
            Require(interaction.ValueKind == JsonValueKind.Object, "source_capture_shape_invalid");
            foreach (string name in new[] { "interaction_id", "kind", "stage", "content_schema" }) _ = Text(interaction, name);
            NullableText(interaction, "prompt");
            JsonElement content = Object(interaction, "content");
            _ = Text(Object(content, "surface"), "kind"); _ = Text(Object(content, "context"), "kind");
            JsonElement capabilities = Array(interaction, "capabilities");
            foreach (JsonElement capability in capabilities.EnumerateArray())
            {
                _ = Text(capability, "verb"); _ = Text(capability, "availability_basis"); NullableText(capability, "subject_role");
                foreach (JsonElement argument in Array(capability, "arguments").EnumerateArray())
                { _ = Text(argument, "role"); _ = Boolean(argument, "required"); }
            }
        }
        var referents = new HashSet<string>(StringComparer.Ordinal);
        JsonElement referentArray = Array(body, "referents");
        if (!included.Contains("referents")) Require(referentArray.GetArrayLength() == 0, "source_capture_shape_invalid");
        foreach (JsonElement referent in referentArray.EnumerateArray())
        {
            Require(referents.Add(Text(referent, "referent_id")), "source_capture_referent_identity_invalid");
            _ = Text(referent, "role"); _ = Text(referent, "kind"); NullableText(referent, "label"); NullableText(referent, "properties_schema");
            JsonElement state = Object(referent, "state");
            _ = Boolean(state, "visible"); _ = Text(state, "observation_basis");
            foreach (string name in new[] { "enabled", "selected", "focused" }) NullableBoolean(state, name);
        }
        JsonElement descriptor = Object(body, "catalog");
        JsonElement owner = Object(body, "owner_occurrence");
        foreach (string name in new[] { "owner_id", "occurrence_id", "binding_revision" }) _ = Text(owner, name);
        NullableText(owner, "focus_referent_id"); NullableText(owner, "focus_occurrence");
        Require(Text(descriptor, "snapshot_id") == snapshot && Text(descriptor, "scope_id") == scope
            && Text(descriptor, "stream_generation") == generation, "source_capture_scope_mismatch");
        _ = Text(descriptor, "catalog_ref");
        _ = Strings(descriptor, "access_methods"); _ = Text(descriptor, "ordering_semantics");
        if (included.Contains("catalog"))
            Require(Text(descriptor, "status") == "complete" && Field(descriptor, "total_count").ValueKind == JsonValueKind.Number
                && Field(descriptor, "total_count").TryGetInt64(out long count)
                && count is >= 0 and <= 65536 && SourceSessionContract.IsSha256(Text(descriptor, "digest")),
                "source_capture_catalog_shape_invalid");
        else
            Require(Text(descriptor, "status") == "not_captured" && Field(descriptor, "total_count").ValueKind == JsonValueKind.Null
                && Field(descriptor, "digest").ValueKind == JsonValueKind.Null && Strings(descriptor, "access_methods").Length == 0,
                "source_capture_catalog_shape_invalid");
        if (requireFull) Require(full, "source_capture_full_reference_incomplete");
        if (full && actions != null)
            foreach (SourcePublicAction action in actions)
                Require((action.SubjectReferentId == null || referents.Contains(action.SubjectReferentId))
                    && action.Arguments.All(argument => referents.Contains(argument.ReferentId)),
                    "source_catalog_operand_not_in_capture");
        return full;
    }

    private static JsonElement Field(JsonElement value, string name)
    {
        Require(value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out _), "source_capture_shape_invalid");
        return value.GetProperty(name);
    }
    private static JsonElement Object(JsonElement value, string name)
    { JsonElement field = Field(value, name); Require(field.ValueKind == JsonValueKind.Object, "source_capture_shape_invalid"); return field; }
    private static JsonElement Array(JsonElement value, string name)
    { JsonElement field = Field(value, name); Require(field.ValueKind == JsonValueKind.Array, "source_capture_shape_invalid"); return field; }
    private static string Text(JsonElement value, string name)
    { JsonElement field = Field(value, name); Require(field.ValueKind == JsonValueKind.String, "source_capture_shape_invalid"); return field.GetString()!; }
    private static string[] Strings(JsonElement value, string name)
    {
        return Array(value, name).EnumerateArray().Select(field =>
        { Require(field.ValueKind == JsonValueKind.String, "source_capture_shape_invalid"); return field.GetString()!; }).ToArray();
    }
    private static bool Boolean(JsonElement value, string name)
    { JsonElement field = Field(value, name); Require(field.ValueKind is JsonValueKind.True or JsonValueKind.False, "source_capture_shape_invalid"); return field.GetBoolean(); }
    private static void NullableText(JsonElement value, string name)
    { if (value.TryGetProperty(name, out JsonElement field)) Require(field.ValueKind is JsonValueKind.Null or JsonValueKind.String, "source_capture_shape_invalid"); }
    private static void NullableBoolean(JsonElement value, string name)
    { if (value.TryGetProperty(name, out JsonElement field)) Require(field.ValueKind is JsonValueKind.Null or JsonValueKind.True or JsonValueKind.False, "source_capture_shape_invalid"); }
    private static void Require(bool condition, string code)
    { if (!condition) throw new InvalidDataException(code); }
}
