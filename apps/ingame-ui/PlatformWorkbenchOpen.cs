using System.Globalization;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace STS2PlatformLiveUi;

/// <summary>Typed, non-authorizing Workbench browser handoff metadata.</summary>
public sealed record PlatformWorkbenchOpenRegistration(
    string Url,
    string WorkbenchInstanceId,
    string RuntimeInstanceId,
    string? ExpectedWorkbenchInstanceId);

public enum PlatformWorkbenchOpenState
{
    Ready,
    NotRegistered,
    Unavailable,
    Stale
}

public sealed record PlatformWorkbenchOpenResult(
    PlatformWorkbenchOpenState State,
    string? Url = null)
{
    public bool CanOpen => State == PlatformWorkbenchOpenState.Ready && Url is not null;
}

/// <summary>
/// Validates the narrow registration contract and checks freshness before a user click opens it.
/// This client never opens a browser; the Godot UI calls OS.ShellOpen after this read-only check.
/// </summary>
public static class PlatformWorkbenchOpenClient
{
    public const string Schema = "sts2.platform/workbench-open-1";
    public const string CloseSchema = "sts2.platform/workbench-close-1";
    public const string StatusSchema = "sts2.platform/workbench-open-status-1";
    public const string GameStatusUrl = "http://127.0.0.1:15528/v1/workbench/status";
    private const int MaximumResponseBytes = 4096;
    private static readonly Regex LocalRootPattern = new(
        @"\Ahttp://127\.0\.0\.1:(?<port>[1-9][0-9]{0,4})/\z",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex InstancePattern = new(
        @"\A[a-f0-9]{32}\z",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);

    public static HttpClient CreateHttpClient() => new(
        new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false })
    {
        Timeout = TimeSpan.FromSeconds(2)
    };

    public static bool IsSafeWorkbenchRoot(string? value)
    {
        if (value is null)
            return false;
        Match match = LocalRootPattern.Match(value);
        return match.Success
            && int.TryParse(match.Groups["port"].Value, NumberStyles.None, CultureInfo.InvariantCulture, out int port)
            && port is >= 1 and <= 65535;
    }

    public static bool IsWorkbenchInstanceId(string? value) =>
        value is not null && InstancePattern.IsMatch(value);

    public static bool TryReadRegistration(
        JsonElement body,
        string expectedRuntimeInstanceId,
        out PlatformWorkbenchOpenRegistration? registration,
        out string error)
    {
        registration = null;
        error = "invalid_workbench_registration";
        if (body.ValueKind != JsonValueKind.Object || string.IsNullOrWhiteSpace(expectedRuntimeInstanceId))
            return false;

        string[] names = body.EnumerateObject().Select(item => item.Name).Order(StringComparer.Ordinal).ToArray();
        if (!names.SequenceEqual(
                new[] { "expected_workbench_instance_id", "runtime_instance_id", "schema", "workbench_instance_id", "workbench_url" },
                StringComparer.Ordinal)
            || !TryGetString(body, "schema", out string schema)
            || !TryGetString(body, "workbench_url", out string url)
            || !TryGetString(body, "workbench_instance_id", out string workbenchInstanceId)
            || !TryGetString(body, "runtime_instance_id", out string runtimeInstanceId)
            || !TryGetNullableString(body, "expected_workbench_instance_id", out string? expectedWorkbenchInstanceId)
            || schema != Schema
            || runtimeInstanceId != expectedRuntimeInstanceId
            || !IsSafeWorkbenchRoot(url)
            || !IsWorkbenchInstanceId(workbenchInstanceId))
            return false;

        if (expectedWorkbenchInstanceId is not null
            && (!IsWorkbenchInstanceId(expectedWorkbenchInstanceId) || expectedWorkbenchInstanceId != workbenchInstanceId))
            return false;

        registration = new PlatformWorkbenchOpenRegistration(
            url, workbenchInstanceId, runtimeInstanceId, expectedWorkbenchInstanceId);
        error = string.Empty;
        return true;
    }

    public static async Task<PlatformWorkbenchOpenResult> CheckAsync(
        HttpClient client,
        CancellationToken cancellationToken = default)
    {
        bool checkingWorkbenchHealth = false;
        try
        {
            using var linkedTimeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            linkedTimeout.CancelAfter(TimeSpan.FromSeconds(2));
            using HttpResponseMessage statusResponse = await client.GetAsync(
                GameStatusUrl,
                HttpCompletionOption.ResponseHeadersRead,
                linkedTimeout.Token);
            if (statusResponse.StatusCode != HttpStatusCode.OK)
                return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Unavailable);

            using JsonDocument statusDocument = await ReadJsonAsync(statusResponse.Content, linkedTimeout.Token);
            JsonElement status = statusDocument.RootElement;
            if (!HasExactNames(status, "schema", "status", "runtime_instance_id", "workbench_url", "workbench_instance_id")
                || !TryGetString(status, "schema", out string schema)
                || schema != StatusSchema
                || !TryGetString(status, "status", out string state)
                || !TryGetString(status, "runtime_instance_id", out string runtimeInstanceId)
                || string.IsNullOrWhiteSpace(runtimeInstanceId))
                return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Unavailable);
            if (state == "unregistered")
                return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.NotRegistered);
            if (state != "registered")
                return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Unavailable);
            if (!TryGetString(status, "workbench_url", out string url)
                || !TryGetString(status, "workbench_instance_id", out string instanceId)
                || !IsSafeWorkbenchRoot(url)
                || !IsWorkbenchInstanceId(instanceId))
                return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Unavailable);

            checkingWorkbenchHealth = true;
            using var healthTimeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            healthTimeout.CancelAfter(TimeSpan.FromSeconds(2));
            using HttpResponseMessage healthResponse = await client.GetAsync(
                new Uri(new Uri(url, UriKind.Absolute), "health"),
                HttpCompletionOption.ResponseHeadersRead,
                healthTimeout.Token);
            if (healthResponse.StatusCode != HttpStatusCode.OK)
                return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Stale);
            using JsonDocument healthDocument = await ReadJsonAsync(healthResponse.Content, healthTimeout.Token);
            JsonElement health = healthDocument.RootElement;
            if (!HasExactNames(health, "instance_id")
                || !TryGetString(health, "instance_id", out string observedInstanceId))
                return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Stale);
            return observedInstanceId == instanceId
                ? new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Ready, url)
                : new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Stale);
        }
        catch (OperationCanceledException)
        {
            return new PlatformWorkbenchOpenResult(checkingWorkbenchHealth
                ? PlatformWorkbenchOpenState.Stale : PlatformWorkbenchOpenState.Unavailable);
        }
        catch (HttpRequestException)
        {
            return new PlatformWorkbenchOpenResult(checkingWorkbenchHealth
                ? PlatformWorkbenchOpenState.Stale : PlatformWorkbenchOpenState.Unavailable);
        }
        catch (JsonException)
        {
            return new PlatformWorkbenchOpenResult(checkingWorkbenchHealth
                ? PlatformWorkbenchOpenState.Stale : PlatformWorkbenchOpenState.Unavailable);
        }
        catch (IOException)
        {
            return new PlatformWorkbenchOpenResult(checkingWorkbenchHealth
                ? PlatformWorkbenchOpenState.Stale : PlatformWorkbenchOpenState.Unavailable);
        }
    }

    private static async Task<JsonDocument> ReadJsonAsync(HttpContent content, CancellationToken cancellationToken)
    {
        if (content.Headers.ContentLength is > MaximumResponseBytes)
            throw new JsonException("response_too_large");
        await using Stream stream = await content.ReadAsStreamAsync(cancellationToken);
        using var limited = new MemoryStream();
        byte[] buffer = new byte[1024];
        while (true)
        {
            int read = await stream.ReadAsync(buffer, cancellationToken);
            if (read == 0)
                break;
            if (limited.Length + read > MaximumResponseBytes)
                throw new JsonException("response_too_large");
            limited.Write(buffer, 0, read);
        }
        return JsonDocument.Parse(limited.ToArray());
    }

    private static bool HasExactNames(JsonElement value, params string[] expected)
    {
        if (value.ValueKind != JsonValueKind.Object)
            return false;
        string[] names = value.EnumerateObject().Select(item => item.Name).Order(StringComparer.Ordinal).ToArray();
        return names.SequenceEqual(expected.Order(StringComparer.Ordinal), StringComparer.Ordinal);
    }

    private static bool TryGetString(JsonElement value, string name, out string result)
    {
        if (value.TryGetProperty(name, out JsonElement property)
            && property.ValueKind == JsonValueKind.String)
        {
            result = property.GetString() ?? string.Empty;
            return true;
        }
        result = string.Empty;
        return false;
    }

    private static bool TryGetNullableString(JsonElement value, string name, out string? result)
    {
        if (value.TryGetProperty(name, out JsonElement property) && property.ValueKind == JsonValueKind.Null)
        {
            result = null;
            return true;
        }
        if (TryGetString(value, name, out string text))
        {
            result = text;
            return true;
        }
        result = null;
        return false;
    }
}
