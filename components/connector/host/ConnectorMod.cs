using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using Godot;
using MegaCrit.Sts2.Core.Modding;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using STS2Connector.Authority;
using STS2Connector.HostControl;

namespace STS2Connector;

#if !STS2_PLATFORM_UNIFIED
[ModInitializer("Initialize")]
#endif
public static partial class ConnectorMod
{
    public const string Version = "1.2.0-rc.7";
    public const int DefaultPort = 15526;
    internal const string ConfigFileName = "STS2_MCP.conf";
    internal const string PortEnvironmentVariable = "STS2_CONNECTOR_PORT";
    internal const string HostControlTokenEnvironmentVariable =
        "STS2_CONNECTOR_HOST_CONTROL_TOKEN";
    internal const string RunSeedEnvironmentVariable = "STS2_CONNECTOR_RUN_SEED";

    private static HttpListener? _listener;
    private static Thread? _serverThread;
    private static readonly ConcurrentQueue<Action> _mainThreadQueue = new();
    internal static readonly JsonSerializerOptions _jsonOptions = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    private sealed record RuntimeConfig(
        int Port,
        bool NativePageEvidenceEnabled,
        string? HostControlToken,
        string? RunSeed);

    private static RuntimeConfig LoadRuntimeConfig()
    {
        RuntimeConfig fileConfig = LoadRuntimeConfigFile();
        int processPort = ResolveProcessPort(
            fileConfig.Port,
            System.Environment.GetEnvironmentVariable(PortEnvironmentVariable));
        string? hostControlToken = HostLifecycleControl.ResolveConfiguredToken(
            System.Environment.GetEnvironmentVariable(HostControlTokenEnvironmentVariable));
        string? runSeed = HostRunSeedControl.ResolveConfiguredSeed(
            System.Environment.GetEnvironmentVariable(RunSeedEnvironmentVariable));
        return fileConfig with
        {
            Port = processPort,
            HostControlToken = hostControlToken,
            RunSeed = runSeed
        };
    }

    internal static int ResolveProcessPort(int configuredPort, string? processPort)
    {
        if (string.IsNullOrWhiteSpace(processPort))
            return configuredPort;
        if (!int.TryParse(processPort, out int port) || port is <= 0 or > 65535)
        {
            throw new InvalidOperationException(
                $"{PortEnvironmentVariable} must be an integer from 1 through 65535.");
        }
        return port;
    }

    private static RuntimeConfig LoadRuntimeConfigFile()
    {
        try
        {
            string? modDir = Path.GetDirectoryName(
                System.Reflection.Assembly.GetExecutingAssembly().Location);
            if (modDir == null)
                return new RuntimeConfig(
                    DefaultPort,
                    NativePageEvidenceEnabled: false,
                    HostControlToken: null,
                    RunSeed: null);

            string operatorConfig = Path.Combine(
                System.Environment.GetFolderPath(System.Environment.SpecialFolder.LocalApplicationData),
                "spireagent", "connector", ConfigFileName);
            string configPath = File.Exists(operatorConfig)
                ? operatorConfig : Path.Combine(modDir, ConfigFileName);
            if (!File.Exists(configPath))
            {
                // A Workshop directory is release-managed. Defaults are in memory;
                // installing or launching a Mod must not create mutable files there.
                GD.Print($"[STS2 Connector] No config found at {configPath}; using default port {DefaultPort}");
                return new RuntimeConfig(
                    DefaultPort,
                    NativePageEvidenceEnabled: false,
                    HostControlToken: null,
                    RunSeed: null);
            }

            string content = File.ReadAllText(configPath);
            using var doc = JsonDocument.Parse(content);
            int configuredPort = DefaultPort;
            if (doc.RootElement.TryGetProperty("port", out var portElem)
                && portElem.TryGetInt32(out int port)
                && port is > 0 and <= 65535)
            {
                configuredPort = port;
            }
            else
            {
                GD.PrintErr(
                    $"[STS2 Connector] Invalid or missing 'port' in {configPath}, using default {DefaultPort}");
            }

            bool nativePageEvidenceEnabled = false;
            if (doc.RootElement.TryGetProperty(
                    "player_environment_native_page_evidence_enabled",
                    out JsonElement nativePageEvidenceElement))
            {
                if (nativePageEvidenceElement.ValueKind is JsonValueKind.True or JsonValueKind.False)
                {
                    nativePageEvidenceEnabled = nativePageEvidenceElement.GetBoolean();
                }
                else
                {
                    GD.PrintErr(
                        $"[STS2 Connector] Invalid player_environment_native_page_evidence_enabled in {configPath}; keeping the optional evidence profile disabled");
                }
            }
            return new RuntimeConfig(
                configuredPort,
                nativePageEvidenceEnabled,
                HostControlToken: null,
                RunSeed: null);
        }
        catch (Exception ex)
        {
            GD.PrintErr(
                $"[STS2 Connector] Failed to load config: {ex.Message}; using safe defaults");
            return new RuntimeConfig(
                DefaultPort,
                NativePageEvidenceEnabled: false,
                HostControlToken: null,
                RunSeed: null);
        }
    }

    public static void Initialize()
    {
        try
        {
            // Connect to main thread process frame for action execution
            var tree = (SceneTree)Engine.GetMainLoop();
            tree.Connect(SceneTree.SignalName.ProcessFrame, Callable.From(ProcessMainThreadQueue));

            RuntimeConfig config = LoadRuntimeConfig();
            PlayerEnvironment.PlayerEnvironmentService.ConfigureNativePageEvidence(
                config.NativePageEvidenceEnabled);
            HostLifecycleControl.Configure(config.HostControlToken);
            HostRunSeedControl.Configure(config.RunSeed);
            int port = config.Port;

            _listener = new HttpListener();
            _listener.Prefixes.Add($"http://localhost:{port}/");
            _listener.Prefixes.Add($"http://127.0.0.1:{port}/");
            _listener.Start();

            _serverThread = new Thread(ServerLoop)
            {
                IsBackground = true,
                Name = "STS2Connector_Server"
            };
            _serverThread.Start();

            GD.Print($"[STS2 Connector] v{Version} server started on http://localhost:{port}/");
            if (!string.IsNullOrWhiteSpace(System.Environment.GetEnvironmentVariable(PortEnvironmentVariable)))
            {
                GD.Print($"[STS2 Connector] Port selected by process-local {PortEnvironmentVariable}");
            }
            GD.Print(
                $"[STS2 Connector] Player Environment native-page evidence: {(config.NativePageEvidenceEnabled ? "enabled" : "disabled")}");
            GD.Print(
                $"[STS2 Connector] Host lifecycle control: {(HostLifecycleControl.Enabled ? "enabled for this process" : "disabled")}");
            GD.Print(
                $"[STS2 Connector] Host run seed: {(HostRunSeedControl.Enabled ? "configured for this process" : "not configured")}");
        }
        catch (Exception ex)
        {
            GD.PrintErr($"[STS2 Connector] Failed to start: {ex}");
        }
    }

    private static void ProcessMainThreadQueue()
    {
        int processed = 0;
        while (_mainThreadQueue.TryDequeue(out var action) && processed < 10)
        {
            try { action(); }
            catch (Exception ex) { GD.PrintErr($"[STS2 Connector] Main thread action error: {ex}"); }
            processed++;
        }
    }

    internal static Task<T> RunOnMainThread<T>(Func<T> func)
    {
        var tcs = new TaskCompletionSource<T>();
        _mainThreadQueue.Enqueue(() =>
        {
            try { tcs.SetResult(func()); }
            catch (Exception ex) { tcs.SetException(ex); }
        });
        return tcs.Task;
    }

    internal static Task RunOnMainThread(Action action)
    {
        var tcs = new TaskCompletionSource<bool>();
        _mainThreadQueue.Enqueue(() =>
        {
            try { action(); tcs.SetResult(true); }
            catch (Exception ex) { tcs.SetException(ex); }
        });
        return tcs.Task;
    }

    private static void ServerLoop()
    {
        while (_listener?.IsListening == true)
        {
            try
            {
                var context = _listener.GetContext();
                // Handle each request asynchronously so we don't block the listener
                ThreadPool.QueueUserWorkItem(_ => HandleRequest(context));
            }
            catch (HttpListenerException) { break; }
            catch (ObjectDisposedException) { break; }
        }
    }

    private static void HandleRequest(HttpListenerContext context)
    {
        try
        {
            var request = context.Request;
            var response = context.Response;
            string? origin = request.Headers["Origin"];
            if (!LoopbackOriginPolicy.IsAllowed(origin))
            {
                SendError(response, 403, "Browser origin is not allowed");
                return;
            }

            if (!string.IsNullOrWhiteSpace(origin))
            {
                response.Headers.Add("Access-Control-Allow-Origin", origin);
                response.Headers.Add("Vary", "Origin");
                response.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
                response.Headers.Add("Access-Control-Allow-Headers", "Content-Type");
            }

            if (request.HttpMethod == "OPTIONS")
            {
                response.StatusCode = 204;
                response.Close();
                return;
            }

            string path = request.Url?.AbsolutePath ?? "/";

            if (path == "/")
            {
                SendJson(response, new
                {
                    message = $"STS2 Player Environment Host v{Version}",
                    status = "ok",
                    contract = "/api/player-environment"
                });
            }
            else if (path == "/api/player-environment/capabilities")
            {
                if (request.HttpMethod == "GET")
                    HandleGetCapabilities(request, response);
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path == "/api/player-environment/snapshot")
            {
                if (request.HttpMethod == "GET")
                    HandleGetPlayerEnvironmentSnapshot(request, response);
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path.StartsWith("/api/player-environment/reads/", StringComparison.Ordinal))
            {
                if (request.HttpMethod == "GET")
                    HandleGetPlayerEnvironmentRead(
                        path["/api/player-environment/reads/".Length..],
                        request,
                        response);
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path == "/api/player-environment/clients/register")
            {
                if (request.HttpMethod == "POST")
                    HandlePostPlayerEnvironmentClientRegistration(request, response);
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path == "/api/player-environment/controller")
            {
                if (request.HttpMethod == "GET")
                    HandleGetPlayerEnvironmentControl(response);
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path.StartsWith("/api/player-environment/controller/", StringComparison.Ordinal))
            {
                string operation = path["/api/player-environment/controller/".Length..];
                if (request.HttpMethod == "POST" && operation is "acquire" or "renew" or "release")
                    HandlePostPlayerEnvironmentController(operation, request, response);
                else if (request.HttpMethod == "POST")
                    SendError(response, 404, "Unknown controller operation");
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path == "/api/player-environment/actions")
            {
                if (request.HttpMethod == "POST")
                    HandlePostPlayerEnvironmentAction(request, response);
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path.StartsWith("/api/player-environment/actions/", StringComparison.Ordinal))
            {
                if (request.HttpMethod == "GET")
                    HandleGetPlayerEnvironmentAction(path["/api/player-environment/actions/".Length..], request, response);
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path == "/api/player-environment/evidence/native-pages/sessions")
            {
                if (request.HttpMethod == "POST")
                    HandlePostPlayerEnvironmentNativePageEvidenceOpen(request, response);
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path.StartsWith(
                         "/api/player-environment/evidence/native-pages/sessions/",
                         StringComparison.Ordinal))
            {
                string operation = path[
                    "/api/player-environment/evidence/native-pages/sessions/".Length..];
                if (request.HttpMethod == "POST"
                    && operation.EndsWith("/return", StringComparison.Ordinal))
                {
                    HandlePostPlayerEnvironmentNativePageEvidenceReturn(
                        operation[..^"/return".Length],
                        request,
                        response);
                }
                else if (request.HttpMethod == "GET"
                         && !operation.Contains('/'))
                {
                    HandleGetPlayerEnvironmentNativePageEvidence(
                        operation,
                        request,
                        response);
                }
                else if (request.HttpMethod is "GET" or "POST")
                    SendError(response, 404, "Unknown native-page evidence operation");
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path == "/api/host-control/shutdown")
            {
                if (request.HttpMethod == "POST")
                    HandlePostHostShutdown(request, response);
                else
                    SendError(response, 405, "Method not allowed");
            }
            else if (path == "/api/host-control/provenance")
            {
                if (request.HttpMethod == "POST")
                    HandlePostHostProvenance(request, response);
                else
                    SendError(response, 405, "Method not allowed");
            }
            else
            {
                SendError(response, 404, "Not found");
            }
        }
        catch (Exception ex)
        {
            try
            {
                SendError(context.Response, 500, $"Internal error: {ex.Message}");
            }
            catch { /* response may already be closed */ }
        }
    }

    // Called on HTTP thread (not main thread) as a best-effort guard.
    // The try/catch handles race conditions during run transitions.
    // Authoritative checks happen inside RunOnMainThread lambdas.
    internal static bool IsMultiplayerRun()
    {
        try
        {
            return MegaCrit.Sts2.Core.Runs.RunManager.Instance.IsInProgress
                && MegaCrit.Sts2.Core.Runs.RunManager.Instance.NetService.Type.IsMultiplayer();
        }
        catch { return false; }
    }

}
