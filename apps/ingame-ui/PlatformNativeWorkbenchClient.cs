using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace STS2PlatformLiveUi;

public sealed record PlatformNativeWorkbenchCommandResult(string RequestId, string Status,
    JsonElement? OwnerResponse, string? ErrorCode);

/// <summary>Fixed application transport, independent of gameplay and browser credentials.</summary>
internal sealed class PlatformNativeWorkbenchClient : IDisposable
{
    internal const int MaximumResponseBytes = 262144;
    internal const string ViewSchema = "spireagent/native-workbench-view-v1";
    internal const string CommandSchema = "spireagent/native-workbench-command-v1";
    internal const string ResultSchema = "spireagent/native-workbench-command-result-v1";
    internal static readonly HashSet<string> Actions = new(StringComparer.Ordinal) {
        "workspace.create", "curation.prepare", "recordings.refresh", "recordings.import",
        "recording.start", "recording.pause", "recording.resume", "recording.change_source", "recording.close",
        "datasets.preview", "datasets.human-preview", "datasets.source3-preview", "datasets.publish", "training.start",
        "training.pause", "training.cancel", "training.reconcile", "training.resume", "evaluation.start",
        "models.export", "models.register", "models.download", "models.load", "models.takeover",
        "models.auto", "models.shadow", "models.one_step", "models.tick",
        "models.human", "models.stop", "models.reconcile", "identity.login", "identity.poll", "identity.logout",
        "collection.consent", "collection.prepare", "collection.upload", "downloads.start"
    };
    private static readonly HashSet<string> Pages = new(StringComparer.Ordinal) { "play", "data", "training", "models", "settings" };
    private static readonly HashSet<string> SafeRejects = new(StringComparer.Ordinal) {
        "native_loopback_required", "native_pair_expired_or_missing", "native_pair_mismatch",
        "native_configuration_changed", "native_game_pair_changed", "native_pair_unavailable",
        "native_access_not_configured", "native_access_recovery_required", "native_launcher_mismatch",
        "native_selected_workbench_changed", "native_recovery_grace_expired", "native_model_intent_superseded",
        "invalid_native_request", "invalid_native_command", "invalid_native_payload", "native_action_not_supported"
    };
    private readonly HttpClient _http;
    private readonly bool _ownsHttp;

    internal PlatformNativeWorkbenchClient(HttpClient? client = null)
    {
        _http = client ?? new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false, UseCookies = false });
        _ownsHttp = client is null;
        if (_ownsHttp) _http.Timeout = Timeout.InfiniteTimeSpan;
    }

    public void Dispose() { if (_ownsHttp) _http.Dispose(); }

    private static HttpRequestMessage Request(PlatformNativeWorkbenchConnection connection, HttpMethod method, string route, bool originalRecovery = false)
    {
        connection.Binding.Validate();
        long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        if (connection.Binding.ExpiresAt <= now && (!originalRecovery || now > connection.Binding.ExpiresAt + 600))
            throw new InvalidOperationException("native_pair_expired_or_missing");
        var request = new HttpRequestMessage(method, connection.Binding.WorkbenchUrl.TrimEnd('/') + route);
        request.Headers.Add("Authorization", "Bearer " + connection.Token);
        request.Headers.Add("X-STS2-Game-Instance-ID", connection.Binding.RuntimeInstanceId);
        request.Headers.Add("X-SpireAgent-Workbench-Instance-ID", connection.Binding.WorkbenchInstanceId);
        request.Headers.Add("X-SpireAgent-Configuration-ID", connection.Binding.ConfigurationId);
        request.Headers.Add("X-SpireAgent-Pair-ID", connection.Binding.PairId);
        return request;
    }

    private async Task<JsonDocument> ReadAsync(HttpResponseMessage response, CancellationToken token)
    {
        if (response.Content.Headers.ContentLength is > MaximumResponseBytes)
            throw new InvalidOperationException("native_response_limit");
        using Stream stream = await response.Content.ReadAsStreamAsync(token).ConfigureAwait(false);
        using var output = new MemoryStream();
        byte[] buffer = new byte[8192];
        while (true)
        {
            int count = await stream.ReadAsync(buffer, token).ConfigureAwait(false);
            if (count == 0) break;
            if (output.Length + count > MaximumResponseBytes) throw new InvalidOperationException("native_response_limit");
            output.Write(buffer, 0, count);
        }
        return JsonDocument.Parse(output.ToArray());
    }

    internal async Task<JsonElement> ViewAsync(PlatformNativeWorkbenchConnection connection, string page,
        int offset, string? contextId, CancellationToken cancellationToken = default)
    {
        if (!Pages.Contains(page) || offset is < 0 or > 10000)
            throw new InvalidOperationException("invalid_native_query");
        string route = $"/api/native-workbench/v1/view?page={page}&limit=25&offset={offset}";
        if (!string.IsNullOrEmpty(contextId)) route += "&id=" + Uri.EscapeDataString(contextId);
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        deadline.CancelAfter(TimeSpan.FromSeconds(2));
        using HttpRequestMessage request = Request(connection, HttpMethod.Get, route);
        using HttpResponseMessage response = await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token).ConfigureAwait(false);
        if (response.StatusCode != HttpStatusCode.OK) throw new InvalidOperationException("native_view_unavailable");
        using JsonDocument document = await ReadAsync(response, deadline.Token).ConfigureAwait(false);
        JsonElement body = document.RootElement;
        if (!PlatformNativeWorkbenchPair.Names(body, "schema", "binding", "page", "observed_at", "availability", "reason",
            "capabilities", "cards", "items", "pagination", "context")
            || body.GetProperty("schema").GetString() != ViewSchema || body.GetProperty("page").GetString() != page
            || PlatformNativeWorkbenchPair.Read(body.GetProperty("binding")) != connection.Binding)
            throw new InvalidOperationException("native_view_binding_mismatch");
        return body.Clone();
    }

    internal async Task<PlatformNativeWorkbenchCommandResult> CommandAsync(
        PlatformNativeWorkbenchConnection connection, string action, object payload, string requestId,
        CancellationToken cancellationToken = default)
    {
        if (!Actions.Contains(action) || !PlatformNativeWorkbenchPair.Hex(requestId, 32))
            return new(requestId, "rejected", null, "native_action_not_supported");
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(new { schema = CommandSchema, request_id = requestId, payload });
        if (bytes.Length > 32768) return new(requestId, "rejected", null, "invalid_native_request");
        HttpRequestMessage request;
        bool originalRecovery = false;
        if (action is "models.human" or "models.stop")
        {
            using JsonDocument payloadDocument = JsonDocument.Parse(bytes);
            JsonElement body = payloadDocument.RootElement.GetProperty("payload");
            originalRecovery = PlatformNativeWorkbenchPair.Names(body, "native_request_id")
                && PlatformNativeWorkbenchPair.Hex(body.GetProperty("native_request_id").GetString(), 32);
        }
        try { request = Request(connection, HttpMethod.Post, "/api/native-workbench/v1/actions/" + action, originalRecovery); }
        catch (InvalidOperationException error) { return new(requestId, "rejected", null, error.Message); }
        using (request)
        using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken))
        {
            request.Content = new ByteArrayContent(bytes);
            request.Content.Headers.ContentType = new("application/json");
            deadline.CancelAfter(TimeSpan.FromSeconds(45));
            try
            {
                using HttpResponseMessage response = await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token).ConfigureAwait(false);
                using JsonDocument document = await ReadAsync(response, deadline.Token).ConfigureAwait(false);
                JsonElement body = document.RootElement;
                if (response.StatusCode != HttpStatusCode.OK)
                {
                    if (PlatformNativeWorkbenchPair.Names(body, "error") && body.GetProperty("error").GetString() is string error
                        && SafeRejects.Contains(error)) return new(requestId, "rejected", null, error);
                    return new(requestId, "unconfirmed", null, "native_command_unconfirmed");
                }
                if (!PlatformNativeWorkbenchPair.Names(body, "schema", "binding", "request_id", "status", "owner_response", "error")
                    || body.GetProperty("schema").GetString() != ResultSchema
                    || body.GetProperty("request_id").GetString() != requestId
                    || PlatformNativeWorkbenchPair.Read(body.GetProperty("binding")) != connection.Binding)
                    return new(requestId, "unconfirmed", null, "native_command_unconfirmed");
                string? status = body.GetProperty("status").GetString();
                if (status is not ("accepted" or "rejected" or "unconfirmed"))
                    return new(requestId, "unconfirmed", null, "native_command_unconfirmed");
                JsonElement owner = body.GetProperty("owner_response");
                JsonElement errorValue = body.GetProperty("error");
                string? errorCode = errorValue.ValueKind == JsonValueKind.Object && errorValue.TryGetProperty("code", out JsonElement code) ? code.GetString() : null;
                return new(requestId, status, owner.ValueKind == JsonValueKind.Null ? null : owner.Clone(), errorCode);
            }
            catch (Exception error) when (error is HttpRequestException or OperationCanceledException or JsonException or InvalidOperationException or IOException)
            { return new(requestId, "unconfirmed", null, "native_command_unconfirmed"); }
        }
    }

    internal static string ExternalUrl(PlatformNativeWorkbenchConnection connection, JsonElement context)
    {
        JsonElement external = context.GetProperty("external");
        string? view = external.GetProperty("view").GetString();
        if (view is not ("local-models" or "local-workspace" or "devices")) throw new InvalidOperationException("native_external_target_invalid");
        string result = connection.Binding.WorkbenchUrl + "?view=" + view;
        if (external.TryGetProperty("id", out JsonElement identity))
        {
            string? id = identity.GetString();
            if (view != "local-workspace" || !PlatformNativeWorkbenchPair.Hex(id, 64)) throw new InvalidOperationException("native_external_target_invalid");
            result += "&id=" + id;
        }
        return result;
    }

    internal static bool TrustedLoginUrl(string hub, string value) => Uri.TryCreate(hub, UriKind.Absolute, out Uri? origin)
        && (origin.Scheme is "https" or "http") && Uri.TryCreate(value, UriKind.Absolute, out Uri? target)
        && target.Scheme == origin.Scheme && target.Authority == origin.Authority
        && target.AbsolutePath == origin.AbsolutePath.TrimEnd('/') + "/app/"
        && System.Text.RegularExpressions.Regex.IsMatch(target.Query, @"\A\?view=connect&flow=[a-f0-9]{32}\z");
}

/// <summary>Presentation fences persist across auth renewal. They never retry an operation.</summary>
internal sealed class PlatformNativeWorkbenchCommands
{
    internal sealed record NativeModelIntent(PlatformNativeWorkbenchConnection Connection, string RequestId);
    private NativeModelIntent? _nativeModel;
    internal NativeModelIntent? SubmittedModelIntent { get { lock (_gate) return _nativeModel; } }
    private readonly object _gate = new();
    private readonly HashSet<string> _pending = new(StringComparer.Ordinal);
    private readonly Dictionary<string, int> _pendingOwners = new(StringComparer.Ordinal);
    private readonly Dictionary<string, string> _unknown = new(StringComparer.Ordinal);
    internal static bool Recovery(string action) => action is "models.human" or "models.stop" or "models.reconcile"
        or "training.pause" or "training.cancel" or "training.reconcile" or "training.resume" or "identity.poll"
        or "recording.close" or "recording.start";
    private static string Owner(string action) => action switch { "models.export" => "export", "models.register" => "registration",
        "evaluation.start" => "evaluation", "recordings.import" => "import", "recordings.refresh" => "recording_catalog",
        "downloads.start" => "member_download", _ => action.Split('.')[0] };
    private static string Scope(string config, string action, string? recordingContext = null) =>
        config + ":" + Owner(action) + (action.StartsWith("recording.", StringComparison.Ordinal) ? ":" + recordingContext : "");
    internal static string RecordingContext(string? runtime, string? session) => runtime + ":" + (session ?? "ready");
    private static string? PayloadContext(string action, object payload)
    {
        if (!action.StartsWith("recording.", StringComparison.Ordinal)) return null;
        JsonElement body = JsonSerializer.SerializeToElement(payload);
        return RecordingContext(body.GetProperty("runtime_instance_id").GetString(),
            body.GetProperty("recording_session_id").GetString());
    }
    internal bool CanSubmit(string config, string action, string? recordingContext = null)
    {
        string owner = Scope(config, action, recordingContext);
        lock (_gate) return !_pending.Contains(config + ":" + action)
            && (Recovery(action) || (!_pendingOwners.ContainsKey(owner) && !_unknown.ContainsKey(owner)));
    }
    internal string[] Unconfirmed { get { lock (_gate) return _unknown.Values.ToArray(); } }

    internal async Task<PlatformNativeWorkbenchCommandResult> RunAsync(PlatformNativeWorkbenchClient client,
        PlatformNativeWorkbenchConnection connection, string action, object payload, CancellationToken token)
    {
        string scope = Scope(connection.Binding.ConfigurationId, action, PayloadContext(action, payload));
        string pendingKey = connection.Binding.ConfigurationId + ":" + action;
        lock (_gate)
        {
            // Recovery is an independent lane and may interrupt an unresolved owner POST.
            if (_pending.Contains(pendingKey) || (_pendingOwners.ContainsKey(scope) && !Recovery(action))) return new("", "rejected", null, "owner_command_pending");
            if (_unknown.ContainsKey(scope) && !Recovery(action)) return new("", "rejected", null, "owner_command_unconfirmed");
            _pending.Add(pendingKey);
            _pendingOwners[scope] = _pendingOwners.GetValueOrDefault(scope) + 1;
        }
        string requestId = Guid.NewGuid().ToString("N");
        NativeModelIntent? previousModel;
        lock (_gate)
        {
            previousModel = _nativeModel;
            if (action is "models.load" or "models.takeover" or "models.auto" or "models.shadow" or "models.one_step" or "models.tick") _nativeModel = new(connection, requestId);
        }
        try
        {
            PlatformNativeWorkbenchCommandResult result = await client.CommandAsync(connection, action, payload, requestId, token).ConfigureAwait(false);
            lock (_gate)
            {
                if (result.Status == "unconfirmed") _unknown[scope] = action + " · " + requestId;
                if (result.Status == "rejected" && _nativeModel?.RequestId == requestId) _nativeModel = previousModel;
                // An accepted recovery ACK can still be pending; no status read or new
                // auth pair erases the original uncertainty. Owner terminal proof is separate.
            }
            return result;
        }
        finally { lock (_gate) { _pending.Remove(pendingKey); if (--_pendingOwners[scope] == 0) _pendingOwners.Remove(scope); } }
    }
}
