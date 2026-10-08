using System;
using System.Linq;
using System.Text.Json;

namespace STS2Connector.PlayerEnvironment.Protocol;

/// <summary>The final original-client fence has no optional/unbound identity fields.</summary>
public static class ClientRevocationWire
{
    public const int MaxRequestBytes = 1024;
    public static PlayerEnvironmentClientRevocationRequest Decode(byte[] utf8)
    {
        if (utf8.Length > MaxRequestBytes) throw new JsonException("Client revocation body exceeds its bound.");
        using JsonDocument document = JsonDocument.Parse(utf8);
        if (document.RootElement.ValueKind != JsonValueKind.Object)
            throw new JsonException("Client revocation requires an object.");
        JsonProperty[] fields = document.RootElement.EnumerateObject().ToArray();
        if (fields.Length != 2 || fields.Select(field => field.Name).Distinct(StringComparer.Ordinal).Count() != 2
            || fields.Any(field => field.Name is not ("runtime_instance_id" or "client_session_id")
                || field.Value.ValueKind != JsonValueKind.String))
            throw new JsonException("Only the two required original identity strings are accepted.");
        string runtime = document.RootElement.GetProperty("runtime_instance_id").GetString()!;
        string client = document.RootElement.GetProperty("client_session_id").GetString()!;
        if (!SafeIdentifier(runtime) || !SafeIdentifier(client))
            throw new JsonException("Original runtime and client identifiers must be bounded opaque ASCII strings.");
        return new(runtime, client);
    }
    private static bool SafeIdentifier(string value) => value.Length is > 0 and <= 128
        && value.All(character => char.IsAsciiLetterOrDigit(character) || character is '-' or '_' or '.');
}
