using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

public sealed class NativeLogicalException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}

public static class NativeLogicalWire
{
    public static JsonSerializerOptions Options => new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow
    };
    internal static readonly UTF8Encoding Utf8 = new(false, true);
    public static byte[] Encode<T>(T value) => JsonSerializer.SerializeToUtf8Bytes(value, Options);
    public static byte[] EncodeBounded<T>(T value, int maxBytes)
    {
        using var stream = new BoundedWireStream(maxBytes);
        JsonSerializer.Serialize(stream, value, Options);
        return stream.ToArray();
    }
    public static T Clone<T>(T value) => JsonSerializer.Deserialize<T>(Encode(value), Options)!;
    public static string Hash(ReadOnlySpan<byte> bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    internal static string Id(string prefix) => prefix + "-" + Convert.ToHexString(RandomNumberGenerator.GetBytes(16)).ToLowerInvariant();
    internal static void Text(string value, int maxBytes = 65536)
    {
        if (value is null) throw new NativeLogicalException("invalid_expression", "A required string is null.");
        try { if (Utf8.GetByteCount(value) > maxBytes) throw new NativeLogicalException("capacity_exceeded", "A field exceeds the announced byte limit."); }
        catch (EncoderFallbackException) { throw new NativeLogicalException("invalid_expression", "Strings must contain Unicode scalar values."); }
    }
    internal static string Number(ulong value) => value.ToString(CultureInfo.InvariantCulture);
}

internal sealed class BoundedWireStream(int limit) : System.IO.MemoryStream
{
    public override void Write(byte[] buffer, int offset, int count) { Check(count); base.Write(buffer, offset, count); }
    public override void Write(ReadOnlySpan<byte> buffer) { Check(buffer.Length); base.Write(buffer); }
    private void Check(int count)
    { if (count > limit - Length) throw new NativeLogicalException("capacity_exceeded", "Encoding exceeds the announced complete-object byte limit."); }
}

// Signed opaque tokens have a fixed bounded body; no per-cursor retained state.
internal sealed class NativeLogicalCursor
{
    private readonly byte[] key = RandomNumberGenerator.GetBytes(32);
    internal string Create(string binding, ulong position, long deadline)
    {
        string body = NativeLogicalWire.Hash(NativeLogicalWire.Utf8.GetBytes(binding)) + ":" + position.ToString(CultureInfo.InvariantCulture) + ":" + deadline.ToString(CultureInfo.InvariantCulture);
        return Convert.ToBase64String(Encoding.ASCII.GetBytes(body + ":" + NativeLogicalWire.Hash(HMACSHA256.HashData(key, Encoding.ASCII.GetBytes(body))))).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }
    internal ulong Parse(string token, string binding, long now, bool checkExpiry = true)
    {
        try
        {
            if (token.Length is 0 or > 1024) throw new FormatException();
            string padded = token.Replace('-', '+').Replace('_', '/');
            padded = padded.PadRight((padded.Length + 3) / 4 * 4, '=');
            string[] p = Encoding.ASCII.GetString(Convert.FromBase64String(padded)).Split(':');
            if (p.Length != 4 || !ulong.TryParse(p[1], NumberStyles.None, CultureInfo.InvariantCulture, out ulong pos) || !long.TryParse(p[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out long deadline)) throw new FormatException();
            string expected = Create(binding, pos, deadline);
            if (!CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(token), Encoding.ASCII.GetBytes(expected))) throw new FormatException();
            if (checkExpiry && now >= deadline) throw new NativeLogicalException("expired", "The cursor expired.");
            return pos;
        }
        catch (FormatException) { throw new NativeLogicalException("cursor_mismatch", "Cursor is not bound to this immutable object and access path."); }
    }
}

/// <summary>Strict fixture/transport decoder. Required explicit-null fields stay distinct from omissions.</summary>
public static class NativeLogicalDecoder
{
    private static readonly Dictionary<Type, string> schemas = new Dictionary<Type, string>
        {
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalObservation)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.ObservationSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCapture)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.CaptureSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalReadChunk)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.ReadSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCatalogPage)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.CatalogPageSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalResolve)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.ResolveSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalAttachReply)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.AttachSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalEvent)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.EventSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalEventBatch)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.EventBatchSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalAwaitReply)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.AwaitSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalResult)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.ResultSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalObservationContext)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.ContextSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCurrentReply)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.CurrentSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalRenewReply)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.RenewSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCancelWaitReply)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.CancelWaitSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalDetachReply)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.DetachSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalRetainReply)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.RetainSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalReleaseReply)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.ReleaseSchema,
            [typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCapabilities)] = STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.CapabilitiesSchema
        };

    private sealed record Field(string Name, System.Reflection.NullabilityInfo Nullability);
    private static readonly System.Collections.Concurrent.ConcurrentDictionary<Type, Field[]> fields = new();
    public static T Decode<T>(ReadOnlySpan<byte> utf8)
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(utf8.ToArray());
            ValidateJson(document.RootElement);
            ValidateShape(document.RootElement, typeof(T));
            T value = JsonSerializer.Deserialize<T>(document.RootElement, NativeLogicalWire.Options)!;
            if (value is null) throw new JsonException("Missing object.");
            return value;
        }
        catch (Exception e) when (e is JsonException or InvalidOperationException or EncoderFallbackException or KeyNotFoundException || e is NativeLogicalException native && native.Code is "invalid_expression" or "capacity_exceeded")
        { throw new NativeLogicalException("invalid_wire", "Wire fields, scalar strings and required shape must match the declared contract."); }
    }
    private static void ValidateJson(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.String) NativeLogicalWire.Text(value.GetString()!, int.MaxValue);
        else if (value.ValueKind == JsonValueKind.Array) foreach (var child in value.EnumerateArray()) ValidateJson(child);
        else if (value.ValueKind == JsonValueKind.Object)
        {
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var field in value.EnumerateObject())
            { if (!seen.Add(field.Name)) throw new JsonException("Duplicate field."); NativeLogicalWire.Text(field.Name, int.MaxValue); ValidateJson(field.Value); }
        }
    }
    private static void Delivery(JsonElement value)
    {
        if (value.GetString() is not ("not_started" or "rejected_before_input" or "delivered" or "partially_delivered" or "unknown"))
            throw new JsonException("Unknown input delivery disposition.");
    }
    private static void U64(JsonElement value, string name)
    {
        string text = value.GetProperty(name).GetString()!;
        if (text.Length == 0 || text.Length > 1 && text[0] == '0' || !ulong.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out _)) throw new JsonException("Source indexes are canonical decimal U64 strings.");
    }
    private static void ValidateShape(JsonElement value, Type type, System.Reflection.NullabilityInfo? nullability = null)
    {
        Type? nullable = Nullable.GetUnderlyingType(type);
        if (value.ValueKind == JsonValueKind.Null)
        {
            if (nullable is null && nullability?.ReadState != System.Reflection.NullabilityState.Nullable)
                throw new JsonException("A required record, collection member or scalar is null.");
            return;
        }
        if (nullable is not null) { ValidateShape(value, nullable, nullability); return; }
        if (type == typeof(JsonElement) || typeof(System.Text.Json.Nodes.JsonNode).IsAssignableFrom(type)) return;
        if (type == typeof(string) || type.IsValueType) return; // The serializer checks scalar numeric/string ranges.
        if (type.IsGenericType && (type.GetGenericTypeDefinition() == typeof(Dictionary<,>)
            || type.GetGenericTypeDefinition() == typeof(IDictionary<,>)
            || type.GetGenericTypeDefinition() == typeof(IReadOnlyDictionary<,>)))
        {
            if (value.ValueKind != JsonValueKind.Object) throw new JsonException("Expected dictionary object.");
            Type[] arguments = type.GetGenericArguments();
            if (arguments[0] != typeof(string)) throw new JsonException("Wire dictionary keys must be strings.");
            var elementNullability = nullability?.GenericTypeArguments.Length == 2 ? nullability.GenericTypeArguments[1] : null;
            foreach (var field in value.EnumerateObject()) ValidateShape(field.Value, arguments[1], elementNullability);
            return;
        }
        if (type.IsArray || type.IsGenericType && typeof(System.Collections.IEnumerable).IsAssignableFrom(type))
        {
            if (value.ValueKind != JsonValueKind.Array) throw new JsonException("Expected array.");
            Type element = type.IsArray ? type.GetElementType()! : type.GetGenericArguments()[0];
            var elementNullability = type.IsArray ? nullability?.ElementType
                : nullability?.GenericTypeArguments.Length == 1 ? nullability.GenericTypeArguments[0] : null;
            foreach (var child in value.EnumerateArray()) ValidateShape(child, element, elementNullability);
            return;
        }
        if (value.ValueKind != JsonValueKind.Object) throw new JsonException("Expected record object.");
        if (schemas.TryGetValue(type, out string? schema) && (!value.TryGetProperty("schema", out var encodedSchema) || encodedSchema.GetString() != schema)) throw new JsonException("Unknown profile schema.");
        if (value.TryGetProperty("input_profile", out var profile) && profile.GetString() != STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.Profile) throw new JsonException("Unknown input profile.");
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalAction) && value.GetProperty("kind").GetString() != "native_input") throw new JsonException("Unknown action kind.");
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalResult))
        {
            Delivery(value.GetProperty("delivery"));
            if (value.GetProperty("retry").GetString() != "never_automatic") throw new JsonException("Native result never authorizes automatic retry.");
            if (value.GetProperty("stages").GetArrayLength() > STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.MaxInputStages) throw new JsonException("Too many known input stages.");
        }
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalInputStage))
        {
            NativeLogicalWire.Text(value.GetProperty("stage").GetString()!, STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.MaxStageFieldBytes);
            NativeLogicalWire.Text(value.GetProperty("evidence").GetString()!, STS2Connector.PlayerEnvironment.Protocol.NativeLogicalContract.MaxStageFieldBytes);
            Delivery(value.GetProperty("delivery"));
        }
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalEvent)) { U64(value, "publication_index"); U64(value, "source_index"); }
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCapture)) U64(value, "capture_ordinal");
        Field[] shape = fields.GetOrAdd(type, t =>
        {
            var context = new System.Reflection.NullabilityInfoContext();
            var result = new List<Field>();
            foreach (var property in t.GetProperties(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance))
            {
                var ignore = (JsonIgnoreAttribute?)Attribute.GetCustomAttribute(property, typeof(JsonIgnoreAttribute));
                if (ignore?.Condition == JsonIgnoreCondition.Always) continue;
                result.Add(new(JsonNamingPolicy.SnakeCaseLower.ConvertName(property.Name), context.Create(property)));
            }
            return result.ToArray();
        });
        foreach (var field in shape)
        {
            if (!value.TryGetProperty(field.Name, out JsonElement child)) throw new JsonException("Required explicit field is missing.");
            ValidateShape(child, field.Nullability.Type, field.Nullability);
        }
        ValidateEnvelope(value, type);
    }
    private static void Same(JsonElement left, string leftName, JsonElement right, string rightName)
    { if (left.GetProperty(leftName).GetString() != right.GetProperty(rightName).GetString()) throw new JsonException("Frozen reference identities differ."); }
    private static void CaptureJoin(JsonElement reference, JsonElement capture)
    {
        Same(reference, "observation_ref", capture, "snapshot_id"); Same(reference, "capture_ref", capture, "capture_id");
        Same(reference, "stream_generation", capture, "stream_generation"); Same(reference, "input_profile", capture, "input_profile");
    }
    private static void RetentionJoin(JsonElement retention, JsonElement capture)
    {
        JsonElement original = retention.GetProperty("capture");
        foreach (string name in new[] { "capture_id", "snapshot_id", "stream_generation", "scope_id", "input_profile", "sha256", "read_cursor", "capture_ordinal", "captured_at", "expires_at" }) Same(original, name, capture, name);
        Same(original.GetProperty("session"), "runtime_instance_id", capture.GetProperty("session"), "runtime_instance_id");
        Same(original.GetProperty("session"), "environment_fingerprint", capture.GetProperty("session"), "environment_fingerprint");
        if (original.GetProperty("byte_count").GetInt32() != capture.GetProperty("byte_count").GetInt32()) throw new JsonException("Frozen byte counts differ.");
    }
    private static void ValidateEnvelope(JsonElement value, Type type)
    {
        string? Status() => value.GetProperty("status").GetString();
        bool Null(string name) => value.GetProperty(name).ValueKind == JsonValueKind.Null;
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCurrentReply))
        {
            if (Status() is not ("captured" or "partial" or "stale" or "capacity_exceeded" or "source_capture_incomplete" or "failed")) throw new JsonException("Unknown Current disposition.");
            if (Status() == "captured" && (Null("context") || Null("capture") || !Null("reason"))) throw new JsonException("Captured Current requires its coherent references.");
            if (Status() == "partial" && (Null("context") || Null("capture") || value.GetProperty("reason").GetString() != "scope_omission")) throw new JsonException("Partial Current is a real requested-scope view with an explicit omission reason.");
            if (Status() is not ("captured" or "partial") && (!Null("context") || !Null("capture") || !Null("retention") || Null("reason"))) throw new JsonException("Failed Current has no captured references and requires its explicit reason.");
            if (!Null("context") && !Null("capture")) CaptureJoin(value.GetProperty("context"), value.GetProperty("capture"));
            if (!Null("retention"))
            { if (Null("capture")) throw new JsonException("Retention requires its actual capture."); RetentionJoin(value.GetProperty("retention"), value.GetProperty("capture")); }
        }
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalRetainReply))
        {
            if (Status() is not ("retained" or "payload_expired" or "capacity_exceeded")) throw new JsonException("Unknown retention disposition.");
            if (Status() == "retained" && (Null("retention") || !Null("reason"))) throw new JsonException("Retained reply needs a fresh reader reference.");
            if (Status() != "retained" && (!Null("retention") || Null("reason"))) throw new JsonException("Failed retention cannot fabricate a reference.");
        }
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalReleaseReply))
        {
            if (Status() != "released" || !value.GetProperty("released").GetBoolean() || !Null("reason")) throw new JsonException("Release is an idempotent own-handle disposition.");
        }
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCancelWaitReply))
        {
            if (Status() is not ("cancelled" or "not_pending") || value.GetProperty("cancelled").GetBoolean() != (Status() == "cancelled")) throw new JsonException("Cancellation reply must preserve the actual pending-wait disposition.");
        }
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalDetachReply) && Status() != "detached") throw new JsonException("Unknown detach disposition.");
        if (type == typeof(STS2Connector.PlayerEnvironment.Protocol.NativeLogicalRenewReply))
        {
            if (Status() is not ("renewed" or "subscription_expired")) throw new JsonException("Unknown renewal disposition.");
            if (Status() == "renewed")
            { if (Null("subscription") || Null("next_cursor") || Null("high_watermark") || Null("retained_start_cursor") || !Null("reason")) throw new JsonException("Renewal needs its live resource and position."); }
            else if (!Null("subscription") || !Null("next_cursor") || !Null("high_watermark") || !Null("retained_start_cursor") || !Null("gap") || Null("reason")) throw new JsonException("Expired subscriptions cannot be revived.");
        }
    }

}
