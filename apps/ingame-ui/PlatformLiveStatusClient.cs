using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2HumanAnnotator.Core;
using STS2HumanAnnotator.Mod;

namespace STS2PlatformLiveUi;

public sealed class PlatformLiveStatusClient : IDisposable
{
    private const string PolicyRuntimeHttpSchema = "sts2.policy-runtime/http-2";
    private const string PolicyRuntimeTickSchema = "sts2.policy-runtime/http-2/tick-1";

    private readonly HttpClient _connectorHttp;
    private readonly HttpClient _policyRuntimeHttp;
    private readonly HttpClient _policyRuntimeCommandHttp;
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    public PlatformLiveStatusClient()
    {
        _connectorHttp = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false })
        {
            BaseAddress = new Uri($"http://127.0.0.1:{ResolveConnectorPort()}/"),
            Timeout = TimeSpan.FromMilliseconds(900)
        };
        _policyRuntimeHttp = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false })
        {
            BaseAddress = ResolvePolicyRuntimeAddress(),
            Timeout = TimeSpan.FromMilliseconds(900)
        };
        _policyRuntimeCommandHttp = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false })
        {
            BaseAddress = _policyRuntimeHttp.BaseAddress,
            Timeout = TimeSpan.FromSeconds(45)
        };
    }

    public async Task<PlatformLiveStatus> ReadAsync(CancellationToken cancellationToken = default)
    {
        PlayerEnvironmentCapabilitiesResponse? capabilities = null;
        PlayerEnvironmentSnapshot? snapshot = null;
        PlayerEnvironmentControlSnapshot? controller = null;
        var errors = new List<string>();
        string connectorTransportStatus = "unavailable";
        string? connectorTransportDetail = null;

        try
        {
            PlayerEnvironmentCapabilitiesResponse fetchedCapabilities = await GetAsync<PlayerEnvironmentCapabilitiesResponse>(
                _connectorHttp,
                "api/player-environment/capabilities",
                cancellationToken);
            PlayerEnvironmentSnapshot fetchedSnapshot = await GetAsync<PlayerEnvironmentSnapshot>(
                _connectorHttp,
                "api/player-environment/snapshot",
                cancellationToken);
            PlayerEnvironmentControlSnapshot fetchedController = await GetAsync<PlayerEnvironmentControlSnapshot>(
                _connectorHttp,
                "api/player-environment/controller",
                cancellationToken);
            PlatformLiveStatusProjection.EnsureConnectorCoherence(
                fetchedCapabilities,
                fetchedSnapshot,
                fetchedController);
            capabilities = fetchedCapabilities;
            snapshot = fetchedSnapshot;
            controller = fetchedController;
            connectorTransportStatus = "connected";
        }
        catch (Exception exception) when (IsLoopbackFailure(exception))
        {
            capabilities = null;
            snapshot = null;
            controller = null;
            connectorTransportDetail = exception is TaskCanceledException
                ? "Connector loopback request timed out."
                : exception.Message;
            errors.Add($"Connector: {connectorTransportDetail}");
        }

        IPlatformRuntimeStatus? policyRuntime = null;
        string policyRuntimeTransportStatus = "unavailable";
        string? policyRuntimeTransportDetail = null;
        try
        {
            PolicyRuntimeHttpStatusResponse response = await GetAsync<PolicyRuntimeHttpStatusResponse>(
                _policyRuntimeHttp,
                "status",
                cancellationToken);
            EnsurePolicyRuntimeStatus(response.Schema, response.Status);
            policyRuntime = response.Status;
            policyRuntimeTransportStatus = "connected";
        }
        catch (Exception exception) when (IsLoopbackFailure(exception))
        {
            policyRuntimeTransportDetail = exception is TaskCanceledException
                ? "Policy Runtime loopback request timed out."
                : exception.Message;
            errors.Add($"Policy Runtime: {policyRuntimeTransportDetail}");
        }

        RecordingApplicationStatus recording = RecordingApplicationService.Instance.QueryStatus();
        return PlatformLiveStatusProjection.Build(
            policyRuntime,
            capabilities,
            snapshot,
            controller,
            recording,
            connectorTransportStatus,
            connectorTransportDetail,
            policyRuntimeTransportStatus,
            policyRuntimeTransportDetail,
            errors);
    }

    public async Task<PlatformPolicyBinding> ObserveBindingAsync(
        string expectedRunId, string expectedGameInstanceId, CancellationToken cancellationToken = default)
    {
        PlatformPolicyBinding observed = await GetAsync<PlatformPolicyBinding>(
            _policyRuntimeHttp, "v2/environment", cancellationToken);
        observed.Validate(expectedRunId, expectedGameInstanceId);
        return observed;
    }

    public async Task<IPlatformRuntimeStatus> SetModeAsync(
        string mode,
        string expectedRunId,
        PlatformPolicyBinding? binding = null,
        CancellationToken cancellationToken = default)
    {
        ValidateMode(mode);
        if (mode != "human" && binding is null) throw new InvalidOperationException("A fresh game binding is required.");
        PolicyRuntimeHttpStatusResponse response = await PostAsync<PolicyRuntimeHttpStatusResponse>(
            "mode",
            new { mode },
            expectedRunId,
            cancellationToken, binding, value => {
                EnsureCommandStatus(value.Schema, value.Status, expectedRunId);
                if (value.Status.Mode != mode || (mode == "human" && value.Status.Controller != "released"))
                    throw new JsonException("Policy Runtime did not confirm the requested mode.");
            });
        return response.Status;
    }

    public async Task<IPlatformRuntimeStatus> StopAsync(string expectedRunId, CancellationToken cancellationToken = default)
    {
        var response = await PostAsync<PolicyRuntimeHttpStatusResponse>("stop", new { }, expectedRunId, cancellationToken,
            validate: value => {
                EnsureCommandStatus(value.Schema, value.Status, expectedRunId);
                if (value.Status.Lifecycle != "stopped" || value.Status.Mode != "human" || value.Status.Controller != "released")
                    throw new JsonException("Policy Runtime did not confirm stop.");
            });
        return response.Status;
    }

    public async Task<IPlatformRuntimeStatus> TickAsync(string expectedRunId, PlatformPolicyBinding binding, CancellationToken cancellationToken = default)
    {
        PolicyRuntimeTickResponse response = await PostAsync<PolicyRuntimeTickResponse>(
            "tick",
            new { max_ticks = 1 },
            expectedRunId,
            cancellationToken, binding, value => {
                EnsureCommandStatus(value.Schema, value.Status, expectedRunId, allowTickSchema: true);
                if (value.Schema != PolicyRuntimeTickSchema || value.Results == null || value.Results.Count != 1
                    || value.Results[0].Type is not ("human" or "shadow" or "delivered" or "not_delivered" or "unknown" or "not_admitted" or "not_executed" or "observation" or "awaited" or "closed"))
                    throw new JsonException("Policy Runtime tick result is incomplete.");
            });
        return response.Status;
    }

    public void Dispose()
    {
        _connectorHttp.Dispose();
        _policyRuntimeHttp.Dispose();
        _policyRuntimeCommandHttp.Dispose();
    }

    private async Task<T> GetAsync<T>(
        HttpClient client,
        string relativePath,
        CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await client.GetAsync(relativePath, cancellationToken);
        return await ReadResponseAsync<T>(response, relativePath, cancellationToken);
    }

    private async Task<T> PostAsync<T>(
        string relativePath,
        object body,
        string expectedRunId,
        CancellationToken cancellationToken,
        PlatformPolicyBinding? binding = null,
        Action<T>? validate = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(expectedRunId);
        using var request = new HttpRequestMessage(HttpMethod.Post, "v2/" + relativePath)
        {
            Content = JsonContent.Create(body, options: JsonOptions)
        };
        request.Headers.Add("X-STS2-Policy-Run-ID", expectedRunId);
        if (binding is not null)
        {
            binding.Validate(expectedRunId, binding.RuntimeInstanceId);
            request.Headers.Add("X-STS2-Game-Instance-ID", binding.RuntimeInstanceId);
            request.Headers.Add("X-STS2-Recovery-Epoch", binding.RecoveryEpoch!.Value.ToString(System.Globalization.CultureInfo.InvariantCulture));
        }
        return await PlatformPolicyTransport.SendAsync(_policyRuntimeCommandHttp, request, async response => {
            T value = await ReadResponseAsync<T>(response, relativePath, cancellationToken);
            validate?.Invoke(value);
            return value;
        }, cancellationToken);
    }

    private static async Task<T> ReadResponseAsync<T>(
        HttpResponseMessage response,
        string relativePath,
        CancellationToken cancellationToken)
    {
        if (response.StatusCode == HttpStatusCode.NotFound)
            throw new HttpRequestException($"Loopback endpoint not found: {relativePath}");
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<T>(JsonOptions, cancellationToken)
            ?? throw new JsonException($"Loopback endpoint returned an empty {typeof(T).Name}.");
    }

    private static void EnsureCommandStatus(string envelopeSchema, IPlatformRuntimeStatus status,
        string expectedRunId, bool allowTickSchema = false)
    {
        EnsurePolicyRuntimeStatus(envelopeSchema, status, allowTickSchema);
        if (status.RunId != expectedRunId)
            throw new JsonException("Policy Runtime command response changed run identity.");
    }

    private static void EnsurePolicyRuntimeStatus(
        string envelopeSchema,
        IPlatformRuntimeStatus status,
        bool allowTickSchema = false)
    {
        if (envelopeSchema != PolicyRuntimeHttpSchema
            && !(allowTickSchema && envelopeSchema == PolicyRuntimeTickSchema))
        {
            throw new JsonException($"Policy Runtime HTTP schema is unsupported: {envelopeSchema}");
        }
        if (status.Schema != PolicyRuntimeStatus.CurrentSchema && status.Schema != NativeAgentRuntimeStatus.CurrentSchema)
            throw new JsonException($"Policy Runtime status schema is unsupported: {status.Schema}");
        if (status.Runtime == null || string.IsNullOrWhiteSpace(status.Runtime.Version))
            throw new JsonException("Policy Runtime software identity is absent.");
        if (status is PolicyRuntimeStatus policy && (policy.Policy == null || string.IsNullOrWhiteSpace(policy.Policy.ManifestId)))
            throw new JsonException("Policy Runtime policy identity is absent.");
        if (status is NativeAgentRuntimeStatus native)
        {
            if (native.Agent is null || string.IsNullOrWhiteSpace(native.Agent.ManifestId)
                || string.IsNullOrWhiteSpace(native.Agent.AgentId) || string.IsNullOrWhiteSpace(native.Agent.AgentVersion)
                || string.IsNullOrWhiteSpace(native.Agent.Provider) || string.IsNullOrWhiteSpace(native.Agent.Architecture)
                || string.IsNullOrWhiteSpace(native.Agent.ArtifactId) || native.Agent.Adapter is null
                || string.IsNullOrWhiteSpace(native.Agent.Adapter.Id) || string.IsNullOrWhiteSpace(native.Agent.Adapter.Version)
                || native.Agent.Adapter.Protocol != "sts2.policy-runtime/agent-session-ndjson-1"
                || !PlatformNativeWorkbenchPair.Hex(native.Agent.ArtifactSha256, 64)
                || !PlatformNativeWorkbenchPair.Hex(native.AgentManifestSha256, 64)
                || !PlatformNativeWorkbenchPair.Hex(native.Agent.Adapter.CodeSha256, 64))
                throw new JsonException("Native Agent identity is absent or invalid.");
            if (native.Errors is null || native.Invalidations is null
                || native.AutonomyBudget.ValueKind != JsonValueKind.Object)
                throw new JsonException("Native Agent operational fields are absent.");
            if (native.Session is { } session && (session.Profile != "native-logical-v1"
                || string.IsNullOrWhiteSpace(session.SessionId) || session.RecoveryEpoch < 0
                || session.StateVersion < 0 || session.AgentState is not ("known" or "uncertain")))
                throw new JsonException("Native Agent session binding is invalid.");
            if (native.PendingRequest is { } pending && (pending.RunId != native.RunId
                || string.IsNullOrWhiteSpace(pending.RequestId) || string.IsNullOrWhiteSpace(pending.RuntimeInstanceId)
                || string.IsNullOrWhiteSpace(pending.SessionId) || string.IsNullOrWhiteSpace(pending.BasisAcquisitionId)
                || string.IsNullOrWhiteSpace(pending.SnapshotId) || string.IsNullOrWhiteSpace(pending.ActionId)
                || pending.SubmissionEpoch < 0 || pending.Status is not ("pending" or "unresolved")))
                throw new JsonException("Native Agent original pending request is invalid.");
            if (native.LastResult is { } result && (string.IsNullOrWhiteSpace(result.RequestId)
                || result.Status is not ("pending" or "terminal")))
                throw new JsonException("Native Agent result status is invalid.");
            if (native.LastDirective is { } directive) EnsureNativeDirective(directive);
        }
        if (string.IsNullOrWhiteSpace(status.RunId))
            throw new JsonException("Policy Runtime run identity is absent.");
        if (status.Lifecycle is not ("running" or "stopped")
            || status.Mode is not ("human" or "shadow" or "one_step" or "auto")
            || (status.Controller is not ("held" or "released")
                && !(status is NativeAgentRuntimeStatus && status.Controller == "unknown")))
        {
            throw new JsonException("Policy Runtime lifecycle status is invalid.");
        }
    }

    private static void EnsureNativeDirective(JsonElement directive)
    {
        if (directive.ValueKind != JsonValueKind.Object || !directive.TryGetProperty("type", out JsonElement type))
            throw new JsonException("Native directive type is absent.");
        string[] fields = type.GetString() switch
        {
            "act" => ["type", "basis_acquisition_id", "selection", "scores"],
            "await" => ["type", "after_cursor", "condition", "timeout_ms"],
            "abstain" or "close" => ["type", "reason"],
            _ => throw new JsonException("Native directive is unsupported.")
        };
        if (directive.EnumerateObject().Count() != fields.Length || fields.Any(field => !directive.TryGetProperty(field, out _)))
            throw new JsonException("Native directive fields are invalid.");
        if (type.GetString() != "act") return;
        JsonElement selection = directive.GetProperty("selection"), scores = directive.GetProperty("scores");
        if (selection.ValueKind != JsonValueKind.Object || !selection.TryGetProperty("kind", out JsonElement kind)
            || kind.GetString() is not ("handle" or "expression"))
            throw new JsonException("Native action selection is invalid.");
        if (kind.GetString() == "handle" && (!selection.TryGetProperty("action_id", out JsonElement action)
            || action.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(action.GetString())))
            throw new JsonException("Native action handle is invalid.");
        if (scores.ValueKind == JsonValueKind.Null) return;
        if (scores.ValueKind != JsonValueKind.Object || !scores.TryGetProperty("catalog_digest", out JsonElement catalog)
            || !PlatformNativeWorkbenchPair.Hex(catalog.GetString(), 64) || !scores.TryGetProperty("values", out JsonElement values)
            || values.ValueKind != JsonValueKind.Array || values.GetArrayLength() > 65536
            || values.EnumerateArray().Any(value => value.ValueKind != JsonValueKind.Number
                || !value.TryGetDouble(out double score) || !double.IsFinite(score)))
            throw new JsonException("Native catalogue scores are invalid.");
    }

    private static void ValidateMode(string mode)
    {
        if (mode is not ("human" or "shadow" or "one_step" or "auto"))
            throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported Policy Runtime mode.");
    }

    private static bool IsLoopbackFailure(Exception exception) =>
        exception is HttpRequestException or TaskCanceledException or JsonException or InvalidOperationException;

    private static int ResolveConnectorPort()
    {
        string? configured = Environment.GetEnvironmentVariable("STS2_CONNECTOR_PORT");
        return int.TryParse(configured, out int port) && port is > 0 and <= 65535
            ? port
            : 15526;
    }

    private static Uri ResolvePolicyRuntimeAddress()
    {
        string? configuredUrl = Environment.GetEnvironmentVariable("STS2_POLICY_RUNTIME_URL");
        if (!string.IsNullOrWhiteSpace(configuredUrl)
            && Uri.TryCreate(configuredUrl, UriKind.Absolute, out Uri? configured)
            && configured.Scheme is "http" or "https"
            && string.IsNullOrEmpty(configured.UserInfo)
            && configured.Host is "127.0.0.1" or "localhost" or "::1")
        {
            return new Uri(configured, configured.AbsolutePath.TrimEnd('/') + "/");
        }

        string? configuredPort = Environment.GetEnvironmentVariable("STS2_POLICY_RUNTIME_PORT");
        int port = int.TryParse(configuredPort, out int parsedPort) && parsedPort is > 0 and <= 65535
            ? parsedPort
            : 15527;
        return new Uri($"http://127.0.0.1:{port}/");
    }
}
