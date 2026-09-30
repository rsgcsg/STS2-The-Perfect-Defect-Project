using System.Globalization;
using System.Diagnostics;
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
    Stale,
    UnsupportedPlatform,
    LauncherMissing,
    LaunchFailed,
    LaunchTimedOut
}

public sealed record PlatformWorkbenchOpenResult(
    PlatformWorkbenchOpenState State,
    string? Url = null)
{
    public bool CanOpen => State == PlatformWorkbenchOpenState.Ready && Url is not null;
    internal string? RuntimeInstanceId { get; init; }
}

internal interface IWorkbenchOpenProcess : IDisposable
{
    Task<int> WaitForExitAsync(CancellationToken cancellationToken);
}

internal interface IWorkbenchOpenLauncher
{
    IWorkbenchOpenProcess? Start(out PlatformWorkbenchOpenState failure);
}

internal sealed class FixedWorkbenchOpenLauncher : IWorkbenchOpenLauncher
{
    private readonly Func<bool> _macOS;
    private readonly Func<string?> _home;
    private readonly Func<string, bool> _exists;
    private readonly Func<ProcessStartInfo, Process?> _start;

    internal FixedWorkbenchOpenLauncher() : this(
        OperatingSystem.IsMacOS,
        () => Environment.GetEnvironmentVariable("HOME"),
        File.Exists,
        Process.Start) { }

    internal FixedWorkbenchOpenLauncher(Func<bool> macOS, Func<string?> home,
        Func<string, bool> exists, Func<ProcessStartInfo, Process?> start)
    {
        _macOS = macOS;
        _home = home;
        _exists = exists;
        _start = start;
    }

    private sealed class StartedProcess(Process process) : IWorkbenchOpenProcess
    {
        public async Task<int> WaitForExitAsync(CancellationToken cancellationToken)
        {
            await process.WaitForExitAsync(cancellationToken);
            return process.ExitCode;
        }

        public void Dispose() => process.Dispose();
    }

    internal static string? InstalledPath(string? home, bool macOS) =>
        macOS && !string.IsNullOrWhiteSpace(home) && home.StartsWith('/') && !home.Contains('\0')
            ? home.TrimEnd('/') + "/Library/Application Support/spireagent/workbench/open"
            : null;

    public IWorkbenchOpenProcess? Start(out PlatformWorkbenchOpenState failure)
    {
        failure = PlatformWorkbenchOpenState.LaunchFailed;
        if (!_macOS())
        {
            failure = PlatformWorkbenchOpenState.UnsupportedPlatform;
            return null;
        }
        string? path = InstalledPath(_home(), true);
        if (path is null || !_exists(path))
        {
            failure = PlatformWorkbenchOpenState.LauncherMissing;
            return null;
        }
        try
        {
            Process? process = _start(new ProcessStartInfo(path)
            {
                UseShellExecute = false,
                CreateNoWindow = true
            });
            return process is null ? null : new StartedProcess(process);
        }
        catch (Exception exception) when (exception is System.ComponentModel.Win32Exception
            or InvalidOperationException or IOException or UnauthorizedAccessException)
        {
            return null;
        }
    }
}

/// <summary>
/// Validates the narrow registration contract and checks freshness before a user click opens it.
/// CheckAsync is read-only. OpenAsync may run only the fixed installed launcher after NotRegistered;
/// the Godot UI opens a browser only for a subsequent Ready result.
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
        CancellationToken cancellationToken = default) =>
        await CheckWithTimeProviderAsync(client, TimeProvider.System, cancellationToken);

    public static Task<PlatformWorkbenchOpenResult> OpenAsync(
        HttpClient client,
        CancellationToken cancellationToken = default) =>
        OpenWithLauncherAsync(client, new FixedWorkbenchOpenLauncher(), TimeProvider.System,
            TimeSpan.FromSeconds(30), TimeSpan.FromMilliseconds(200), cancellationToken);

    internal static async Task<PlatformWorkbenchOpenResult> OpenWithLauncherAsync(
        HttpClient client,
        IWorkbenchOpenLauncher launcher,
        TimeProvider timeProvider,
        TimeSpan launchDeadline,
        TimeSpan pollInterval,
        CancellationToken cancellationToken = default)
    {
        if (cancellationToken.IsCancellationRequested)
            return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Unavailable);
        PlatformWorkbenchOpenResult initial = await CheckWithTimeProviderAsync(
            client, timeProvider, cancellationToken);
        if (initial.State != PlatformWorkbenchOpenState.NotRegistered || cancellationToken.IsCancellationRequested)
            return initial;
        if (initial.RuntimeInstanceId is null)
            return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Unavailable);

        using var deadline = new CancellationTokenSource(launchDeadline, timeProvider);
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(
            cancellationToken, deadline.Token);
        PlatformWorkbenchOpenResult beforeLaunch = await CheckWithTimeProviderAsync(
            client, timeProvider, linked.Token);
        if (linked.IsCancellationRequested)
            return new PlatformWorkbenchOpenResult(cancellationToken.IsCancellationRequested
                ? PlatformWorkbenchOpenState.Unavailable : PlatformWorkbenchOpenState.LaunchTimedOut);
        if (beforeLaunch.RuntimeInstanceId is not null
            && beforeLaunch.RuntimeInstanceId != initial.RuntimeInstanceId)
            return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Stale);
        if (beforeLaunch.State != PlatformWorkbenchOpenState.NotRegistered)
            return beforeLaunch;
        if (beforeLaunch.RuntimeInstanceId is null)
            return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Unavailable);

        using IWorkbenchOpenProcess? process = launcher.Start(out PlatformWorkbenchOpenState failure);
        if (process is null)
            return new PlatformWorkbenchOpenResult(failure);

        Task<int> exited = process.WaitForExitAsync(linked.Token);
        try
        {
            bool exitObserved = false;
            while (!linked.IsCancellationRequested)
            {
                if (!exitObserved && exited.IsCompleted)
                {
                    try
                    {
                        if (await exited != 0)
                            return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.LaunchFailed);
                        exitObserved = true;
                    }
                    catch (OperationCanceledException)
                    {
                        break;
                    }
                    catch (Exception exception) when (exception is InvalidOperationException
                        or System.ComponentModel.Win32Exception)
                    {
                        return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.LaunchFailed);
                    }
                }
                PlatformWorkbenchOpenResult current = await CheckWithTimeProviderAsync(
                    client, timeProvider, linked.Token);
                if (linked.IsCancellationRequested)
                    break;
                if (current.RuntimeInstanceId is not null
                    && current.RuntimeInstanceId != initial.RuntimeInstanceId)
                    return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Stale);
                if (current.State != PlatformWorkbenchOpenState.NotRegistered)
                {
                    if (exited.IsCompletedSuccessfully && exited.Result != 0)
                        return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.LaunchFailed);
                    return current;
                }
                Task delay = Task.Delay(pollInterval, timeProvider, linked.Token);
                if (exitObserved)
                    await Task.WhenAny(delay).ConfigureAwait(false);
                else
                    await Task.WhenAny(exited, delay).ConfigureAwait(false);
            }
            return new PlatformWorkbenchOpenResult(cancellationToken.IsCancellationRequested
                ? PlatformWorkbenchOpenState.Unavailable : PlatformWorkbenchOpenState.LaunchTimedOut);
        }
        finally
        {
            linked.Cancel();
            try
            {
                // Cancel only our wait; the installed broker and Workbench are never killed.
                await exited.WaitAsync(TimeSpan.FromSeconds(1)).ConfigureAwait(false);
            }
            catch (Exception exception) when (exception is OperationCanceledException
                or TimeoutException or InvalidOperationException
                or System.ComponentModel.Win32Exception) { }
        }
    }

    internal static async Task<PlatformWorkbenchOpenResult> CheckWithTimeProviderAsync(
        HttpClient client,
        TimeProvider timeProvider,
        CancellationToken cancellationToken = default)
    {
        bool checkingWorkbenchHealth = false;
        try
        {
            using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(2), timeProvider);
            using var linkedTimeout = CancellationTokenSource.CreateLinkedTokenSource(
                cancellationToken, deadline.Token);
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
                return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.NotRegistered)
                {
                    RuntimeInstanceId = runtimeInstanceId
                };
            if (state != "registered")
                return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Unavailable);
            if (!TryGetString(status, "workbench_url", out string url)
                || !TryGetString(status, "workbench_instance_id", out string instanceId)
                || !IsSafeWorkbenchRoot(url)
                || !IsWorkbenchInstanceId(instanceId))
                return new PlatformWorkbenchOpenResult(PlatformWorkbenchOpenState.Unavailable);

            checkingWorkbenchHealth = true;
            using var healthDeadline = new CancellationTokenSource(TimeSpan.FromSeconds(2), timeProvider);
            using var healthTimeout = CancellationTokenSource.CreateLinkedTokenSource(
                cancellationToken, healthDeadline.Token);
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
                    { RuntimeInstanceId = runtimeInstanceId }
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
