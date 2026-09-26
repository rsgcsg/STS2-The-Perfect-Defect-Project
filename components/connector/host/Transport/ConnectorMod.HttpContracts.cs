using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Text.Json;
using Godot;

namespace STS2Connector;

public static partial class ConnectorMod
{
    private static T? ReadBoundedJsonBody<T>(
        HttpListenerRequest request,
        HttpListenerResponse response,
        int maxBodyBytes,
        string contractLabel)
    {
        if (request.ContentLength64 > maxBodyBytes)
        {
            SendApiError(
                response,
                413,
                "request_too_large",
                $"{contractLabel} request exceeds {maxBodyBytes / 1024} KiB.");
            return default;
        }

        byte[]? bytes = ReadBoundedBodyBytes(request.InputStream, maxBodyBytes);
        if (bytes == null)
        {
            SendApiError(
                response,
                413,
                "request_too_large",
                $"{contractLabel} request exceeds {maxBodyBytes / 1024} KiB.");
            return default;
        }

        try
        {
            string body = request.ContentEncoding.GetString(bytes);
            return JsonSerializer.Deserialize<T>(body, _jsonOptions);
        }
        catch (JsonException)
        {
            SendApiError(
                response,
                400,
                "invalid_json",
                "Request body must be valid JSON.");
            return default;
        }
    }

    internal static byte[]? ReadBoundedBodyBytes(Stream inputStream, int maxBodyBytes)
    {
        byte[] bytes = new byte[maxBodyBytes + 1];
        int count = 0;
        while (count < bytes.Length)
        {
            int read = inputStream.Read(bytes, count, bytes.Length - count);
            if (read == 0)
                break;
            count += read;
        }
        return count > maxBodyBytes ? null : bytes[..count];
    }

    internal static bool IsSafeProtocolIdentifier(string? value, int maxLength)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > maxLength)
            return false;
        return value.All(character =>
            char.IsAsciiLetterOrDigit(character) || character is '-' or '_' or '.');
    }

    private static bool IsSafeProtocolLabel(string? value, int maxLength) =>
        !string.IsNullOrWhiteSpace(value)
        && value.Length <= maxLength
        && value.All(character => !char.IsControl(character));

    private static void SendApiInternalError(
        HttpListenerResponse response,
        string code,
        Exception exception)
    {
        GD.PrintErr($"[STS2 Player Environment Host] {code}: {exception}");
        SendApiError(
            response,
            500,
            code,
            $"Host operation failed with {exception.GetType().Name}. See local game log for details.");
    }

    private static void SendApiError(
        HttpListenerResponse response,
        int statusCode,
        string code,
        string detail)
    {
        response.StatusCode = statusCode;
        SendJson(response, new { error = new { code, detail } });
    }
}
