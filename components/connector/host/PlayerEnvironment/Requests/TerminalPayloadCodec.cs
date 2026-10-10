using System;
using System.Buffers;
using System.Buffers.Text;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Closed terminal DTO encoder. Strings and borrowed JSON are emitted
/// incrementally through one charged scratch block and preallocated output
/// blocks. No serializer/MemoryStream/pool/UTF16 whole-document/copy fallback.</summary>
internal sealed class TerminalPayloadCodec
{
    private sealed record Field(PropertyInfo Property, string Name, JsonIgnoreCondition? Ignore);
    private static readonly IReadOnlyDictionary<Type, Field[]> fields = CreateShapes();
    private readonly IResultJsonElementRawView raw;
    private readonly bool legacyCarriageReturn;
    internal bool Supported => raw.Supported;
    internal string FrameworkIdentity => raw.FrameworkIdentity;
    internal TerminalPayloadCodec(IResultJsonElementRawView? raw = null, string? legacyNewLine = null)
    {
        this.raw = raw ?? JsonElementRawView.Instance;
        string newline = legacyNewLine ?? Environment.NewLine;
        if (newline is not ("\n" or "\r\n")) throw new ArgumentOutOfRangeException(nameof(legacyNewLine));
        legacyCarriageReturn = newline == "\r\n";
    }

    internal void Encode(object terminal, RequestResultArena.ResultReservation target, bool native)
    {
        if (!Supported) throw new ResultCodecUnsupportedException(FrameworkIdentity);
        if (terminal is not (NativeLogicalResult or PlayerEnvironmentActionReceipt
            or TextMenuActionResult or TextMenuV2ActionResult))
            throw new JsonException("The result codec accepts only existing terminal DTOs.");
        target.ResetEncoding();
        var writer = new Writer(target, raw, indented: !native, omitNull: !native, legacyCarriageReturn);
        writer.Value(terminal, 0);
    }

    private static IReadOnlyDictionary<Type, Field[]> CreateShapes()
    {
        var result = new Dictionary<Type, Field[]>();
        var pending = new Queue<Type>(new[] { typeof(NativeLogicalResult), typeof(PlayerEnvironmentActionReceipt),
            typeof(TextMenuActionResult), typeof(TextMenuV2ActionResult) });
        while (pending.TryDequeue(out Type? type))
        {
            type = Nullable.GetUnderlyingType(type) ?? type;
            if (typeof(JsonNode).IsAssignableFrom(type) || type == typeof(string)
                || type.IsPrimitive || type == typeof(decimal) || type == typeof(DateTimeOffset)
                || type == typeof(DateTime) || type == typeof(Guid)) continue;
            if (type.IsArray) { pending.Enqueue(type.GetElementType()!); continue; }
            if (type.IsGenericType && (type.GetGenericTypeDefinition() == typeof(IReadOnlyList<>)
                || type.GetGenericTypeDefinition() == typeof(IList<>) || type.GetGenericTypeDefinition() == typeof(IEnumerable<>)))
            { pending.Enqueue(type.GetGenericArguments()[0]); continue; }
            if (result.ContainsKey(type)) continue;
            if (type.Namespace != typeof(NativeLogicalResult).Namespace)
                throw new InvalidOperationException("A terminal DTO introduced an unreviewed serialization shape.");
            Field[] shape = type.GetProperties(BindingFlags.Public | BindingFlags.Instance)
                .Where(property => property.GetIndexParameters().Length == 0 && property.GetMethod is not null)
                .Select(property => new Field(property,
                    property.GetCustomAttribute<JsonPropertyNameAttribute>()?.Name
                        ?? JsonNamingPolicy.SnakeCaseLower.ConvertName(property.Name),
                    property.GetCustomAttribute<JsonIgnoreAttribute>()?.Condition))
                .Where(field => field.Ignore != JsonIgnoreCondition.Always).ToArray();
            if (shape.Length > 128) throw new InvalidOperationException("Terminal field metadata exceeds its closed shape bound.");
            result.Add(type, shape);
            foreach (Field field in shape) pending.Enqueue(field.Property.PropertyType);
        }
        return result;
    }

    private ref struct Writer
    {
        private readonly RequestResultArena.ResultReservation target;
        private readonly IResultJsonElementRawView raw;
        private readonly bool indented, omitNull;
        private readonly bool carriageReturn;
        private readonly Span<byte> scratch;
        internal Writer(RequestResultArena.ResultReservation target, IResultJsonElementRawView raw,
            bool indented, bool omitNull, bool carriageReturn)
        { this.target = target; this.raw = raw; this.indented = indented; this.omitNull = omitNull;
            this.carriageReturn = carriageReturn; scratch = target.Scratch; }

        private void Separator(int count, int depth)
        {
            if (count != 0) target.Append((byte)',');
            if (indented) LineIndent(depth);
        }
        private void EndContainer(int count, int depth, byte end)
        {
            if (indented && count != 0) LineIndent(depth);
            target.Append(end);
        }
        private void LineIndent(int depth)
        {
            // Match the existing .NET9 legacy serializer's framing, including
            // Windows CRLF. String contents/raw numeric tokens stay untouched.
            if (carriageReturn) target.Append((byte)'\r');
            target.Append((byte)'\n');
            for (int index = 0; index < depth * 2; index++) target.Append((byte)' ');
        }
        private void PropertyName(string name) { String(name); target.Append((byte)':'); if (indented) target.Append((byte)' '); }
        private void Utf8PropertyName(ReadOnlySpan<byte> name, bool escaped)
        { Utf8String(name, escaped); target.Append((byte)':'); if (indented) target.Append((byte)' '); }

        internal void Value(object? value, int depth)
        {
            if (depth > 64) throw new JsonException("The existing terminal JSON depth limit was exceeded.");
            if (value is null) { target.Append("null"u8); return; }
            switch (value)
            {
                case string text: String(text); return;
                case bool boolean: target.Append(boolean ? "true"u8 : "false"u8); return;
                case byte number: Number(number); return;
                case sbyte number: Number(number); return;
                case short number: Number(number); return;
                case ushort number: Number(number); return;
                case int number: Number(number); return;
                case uint number: Number(number); return;
                case long number: Number(number); return;
                case ulong number: Number(number); return;
                case decimal number: Number(number); return;
                case float number when float.IsFinite(number): Number(number); return;
                case double number when double.IsFinite(number): Number(number); return;
                case float or double: throw new JsonException("JSON cannot encode nonfinite numeric facts.");
                case DateTimeOffset date: Date(date); return;
                case DateTime date: Date(date); return;
                case Guid guid: NumberAsString(guid, "D"); return;
                case JsonElement element: Element(element, depth); return;
                case JsonObject obj:
                    target.Append((byte)'{'); int objectCount = 0;
                    foreach (var property in obj)
                    { Separator(objectCount++, depth + 1); PropertyName(property.Key); Value(property.Value, depth + 1); }
                    EndContainer(objectCount, depth, (byte)'}'); return;
                case JsonArray array:
                    target.Append((byte)'['); int arrayCount = 0;
                    foreach (JsonNode? item in array)
                    { Separator(arrayCount++, depth + 1); Value(item, depth + 1); }
                    EndContainer(arrayCount, depth, (byte)']'); return;
                case JsonValue scalar: NodeScalar(scalar, depth); return;
                case IEnumerable array:
                    target.Append((byte)'['); int count = 0;
                    foreach (object? item in array)
                    { Separator(count++, depth + 1); Value(item, depth + 1); }
                    EndContainer(count, depth, (byte)']'); return;
            }
            if (!fields.TryGetValue(value.GetType(), out Field[]? shape))
                throw new JsonException("The terminal contains an unreviewed object shape.");
            target.Append((byte)'{'); int fieldCount = 0;
            foreach (Field field in shape)
            {
                object? child = field.Property.GetValue(value);
                if (child is null && (field.Ignore == JsonIgnoreCondition.WhenWritingNull
                    || omitNull && field.Ignore != JsonIgnoreCondition.Never)) continue;
                if (field.Ignore == JsonIgnoreCondition.WhenWritingDefault)
                    throw new JsonException("Default-value omission needs an explicit reviewed terminal codec.");
                Separator(fieldCount++, depth + 1); PropertyName(field.Name); Value(child, depth + 1);
            }
            EndContainer(fieldCount, depth, (byte)'}');
        }

        private void NodeScalar(JsonValue value, int depth)
        {
            // Serialized/parsed DOM values borrow their original encoded bytes;
            // do not GetString/GetRawText a potentially huge lazy JsonElement.
            if (value.TryGetValue<JsonElement>(out var element)) { Element(element, depth); return; }
            if (value.TryGetValue<string>(out var text)) { String(text); return; }
            if (value.TryGetValue<bool>(out var boolean)) { Value(boolean, depth); return; }
            if (value.TryGetValue<byte>(out var b)) { Number(b); return; }
            if (value.TryGetValue<sbyte>(out var sb)) { Number(sb); return; }
            if (value.TryGetValue<short>(out var s)) { Number(s); return; }
            if (value.TryGetValue<ushort>(out var us)) { Number(us); return; }
            if (value.TryGetValue<int>(out var i)) { Number(i); return; }
            if (value.TryGetValue<uint>(out var ui)) { Number(ui); return; }
            if (value.TryGetValue<long>(out var l)) { Number(l); return; }
            if (value.TryGetValue<ulong>(out var ul)) { Number(ul); return; }
            if (value.TryGetValue<decimal>(out var dec)) { Number(dec); return; }
            if (value.TryGetValue<float>(out var f)) { Value(f, depth); return; }
            if (value.TryGetValue<double>(out var d)) { Value(d, depth); return; }
            if (value.TryGetValue<DateTimeOffset>(out var dto)) { Date(dto); return; }
            if (value.TryGetValue<DateTime>(out var dt)) { Date(dt); return; }
            if (value.TryGetValue<Guid>(out var guid)) { NumberAsString(guid, "D"); return; }
            throw new JsonException("A custom JSON scalar needs a reviewed bounded converter.");
        }

        private void Element(JsonElement element, int depth)
        {
            // Raw bytes are used only in this synchronous call. The DTO/root
            // holds the JsonElement/document; no borrowed view enters a payload.
            ReadOnlyMemory<byte> borrowed = raw.Borrow(element);
            var reader = new Utf8JsonReader(borrowed.Span, new JsonReaderOptions
                { CommentHandling = JsonCommentHandling.Skip, MaxDepth = 64 });
            if (!reader.Read()) throw new JsonException("The original DOM value is empty.");
            Token(ref reader, depth);
            if (reader.Read()) throw new JsonException("The original DOM value contains another root.");
        }
        private void Token(ref Utf8JsonReader reader, int depth)
        {
            if (depth > 64) throw new JsonException("The terminal JSON depth limit was exceeded.");
            switch (reader.TokenType)
            {
                case JsonTokenType.StartObject:
                    target.Append((byte)'{'); int objectCount = 0;
                    while (reader.Read() && reader.TokenType != JsonTokenType.EndObject)
                    {
                        if (reader.TokenType != JsonTokenType.PropertyName) throw new JsonException();
                        Separator(objectCount++, depth + 1);
                        Utf8PropertyName(reader.ValueSpan, reader.ValueIsEscaped);
                        if (!reader.Read()) throw new JsonException();
                        Token(ref reader, depth + 1);
                    }
                    if (reader.TokenType != JsonTokenType.EndObject) throw new JsonException();
                    EndContainer(objectCount, depth, (byte)'}'); return;
                case JsonTokenType.StartArray:
                    target.Append((byte)'['); int arrayCount = 0;
                    while (reader.Read() && reader.TokenType != JsonTokenType.EndArray)
                    { Separator(arrayCount++, depth + 1); Token(ref reader, depth + 1); }
                    if (reader.TokenType != JsonTokenType.EndArray) throw new JsonException();
                    EndContainer(arrayCount, depth, (byte)']'); return;
                case JsonTokenType.String: Utf8String(reader.ValueSpan, reader.ValueIsEscaped); return;
                case JsonTokenType.Number: target.Append(reader.ValueSpan); return;
                case JsonTokenType.True: target.Append("true"u8); return;
                case JsonTokenType.False: target.Append("false"u8); return;
                case JsonTokenType.Null: target.Append("null"u8); return;
                default: throw new JsonException("The original DOM token cannot be encoded.");
            }
        }

        private void String(string value)
        {
            target.Append((byte)'"');
            ReadOnlySpan<char> remaining = value.AsSpan();
            while (!remaining.IsEmpty)
            {
                if (Rune.DecodeFromUtf16(remaining, out Rune rune, out int consumed) != OperationStatus.Done)
                    throw new JsonException("A terminal string contains an invalid Unicode scalar.");
                Scalar(rune); remaining = remaining[consumed..];
            }
            target.Append((byte)'"');
        }
        private void Utf8String(ReadOnlySpan<byte> value, bool escaped)
        {
            target.Append((byte)'"');
            while (!value.IsEmpty)
            {
                if (escaped && value[0] == (byte)'\\')
                {
                    if (value.Length < 2) throw new JsonException();
                    byte escape = value[1]; value = value[2..];
                    Rune rune;
                    if (escape == (byte)'u')
                    {
                        int first = HexScalar(value); value = value[4..];
                        if (first is >= 0xD800 and <= 0xDBFF)
                        {
                            if (value.Length < 6 || value[0] != (byte)'\\' || value[1] != (byte)'u') throw new JsonException();
                            int second = HexScalar(value[2..]);
                            if (second is < 0xDC00 or > 0xDFFF) throw new JsonException();
                            rune = new Rune(0x10000 + (first - 0xD800) * 1024 + second - 0xDC00); value = value[6..];
                        }
                        else if (!Rune.TryCreate(first, out rune)) throw new JsonException();
                    }
                    else rune = new Rune(escape switch
                    { (byte)'"' => '"', (byte)'\\' => '\\', (byte)'/' => '/', (byte)'b' => '\b',
                        (byte)'f' => '\f', (byte)'n' => '\n', (byte)'r' => '\r', (byte)'t' => '\t', _ => throw new JsonException() });
                    Scalar(rune);
                }
                else
                {
                    if (Rune.DecodeFromUtf8(value, out Rune rune, out int consumed) != OperationStatus.Done) throw new JsonException();
                    Scalar(rune); value = value[consumed..];
                }
            }
            target.Append((byte)'"');
        }
        private static int HexScalar(ReadOnlySpan<byte> value)
        {
            if (value.Length < 4) throw new JsonException();
            int result = 0;
            for (int index = 0; index < 4; index++)
            {
                int digit = value[index] switch { >= (byte)'0' and <= (byte)'9' => value[index] - '0',
                    >= (byte)'a' and <= (byte)'f' => value[index] - 'a' + 10,
                    >= (byte)'A' and <= (byte)'F' => value[index] - 'A' + 10, _ => throw new JsonException() };
                result = result * 16 + digit;
            }
            return result;
        }
        private void Scalar(Rune rune)
        {
            int bytes = rune.EncodeToUtf8(scratch[..4]);
            if (JavaScriptEncoder.UnsafeRelaxedJsonEscaping.EncodeUtf8(scratch[..bytes], scratch[16..64],
                    out int consumed, out int written, isFinalBlock: true) != OperationStatus.Done || consumed != bytes)
                throw new JsonException("The bounded scalar encoder did not complete.");
            target.Append(scratch.Slice(16, written));
        }
        private void Number<T>(T value) where T : IUtf8SpanFormattable
        {
            if (!value.TryFormat(scratch[..64], out int written, default, CultureInfo.InvariantCulture))
                throw new JsonException("A scalar numeric format exceeds fixed scratch.");
            target.Append(scratch[..written]);
        }
        private void NumberAsString<T>(T value, ReadOnlySpan<char> format) where T : IUtf8SpanFormattable
        {
            if (!value.TryFormat(scratch[..64], out int written, format, CultureInfo.InvariantCulture)) throw new JsonException();
            target.Append((byte)'"'); target.Append(scratch[..written]); target.Append((byte)'"');
        }
        private void Date(DateTimeOffset value)
        {
            if (!Utf8Formatter.TryFormat(value, scratch[..64], out int written, new StandardFormat('O'))) throw new JsonException();
            TrimDate(written);
        }
        private void Date(DateTime value)
        {
            if (!Utf8Formatter.TryFormat(value, scratch[..64], out int written, new StandardFormat('O'))) throw new JsonException();
            TrimDate(written);
        }
        private void TrimDate(int written)
        {
            int fractionStart = scratch[..written].IndexOf((byte)'.');
            target.Append((byte)'"');
            if (fractionStart < 0) target.Append(scratch[..written]);
            else
            {
                int end = fractionStart + 8; // round-trip has seven fractional digits
                int last = end;
                while (last > fractionStart + 1 && scratch[last - 1] == (byte)'0') last--;
                if (last == fractionStart + 1) last = fractionStart;
                target.Append(scratch[..last]); target.Append(scratch.Slice(end, written - end));
            }
            target.Append((byte)'"');
        }
    }
}
