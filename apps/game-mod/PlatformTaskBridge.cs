using System.Net;
using System.Text.Json;
using STS2Connector.PlayerEnvironment;
using STS2HumanAnnotator.Core;
using STS2HumanAnnotator.Mod;
using STS2PlatformLiveUi;

namespace STS2Platform.GameMod;

/// <summary>Bounded loopback application commands, never gameplay actions.</summary>
internal static class PlatformTaskBridge
{
    private static readonly object Gate = new();
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private static HttpListener? _listener;
    private const string Authority = "127.0.0.1:15528";
    private const int MaximumWorkbenchRegistrationBytes = 2048;
    private static PlatformWorkbenchOpenRegistration? _workbenchRegistration;

    internal static void Start()
    {
        if (_listener != null) return;
        PlatformNativeWorkbenchConnection.Initialize(
            PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId);
        var listener = new HttpListener();
        listener.Prefixes.Add("http://" + Authority + "/");
        listener.Start();
        _listener = listener;
        _ = Task.Run(async () =>
        {
            while (listener.IsListening)
            {
                try
                {
                    HttpListenerContext context = await listener.GetContextAsync();
                    _ = Task.Run(() => Handle(context));
                }
                catch (HttpListenerException) { break; }
                catch (ObjectDisposedException) { break; }
            }
        });
    }

    private static object Status(RecordingApplicationStatus recording) => new
    {
        schema = "sts2.platform/task-status-1",
        runtime_instance_id = PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId,
        recording_session_id = recording.Lifecycle.SessionId,
        recording_lifecycle = recording.Lifecycle.State.ToString().ToLowerInvariant(),
        closeout_status = recording.Closeout.State,
        ready_for_model = PlatformCollectionHandoff.Ready(recording)
    };

    internal static async Task Handle(HttpListenerContext context,
        PlatformNativeWorkbenchConnection.Authority? nativeAuthority = null)
    {
        int nativeWorkStarted = 0;
        try
        {
            var request = context.Request;
            // This is a native-process endpoint, not a browser CORS API.
            if (request.UserHostName != Authority || request.Headers["Origin"] != null
                || request.RemoteEndPoint == null || !IPAddress.IsLoopback(request.RemoteEndPoint.Address))
            { Reply(context, 403, new { error = "native_loopback_required" }); return; }
            if (request.HttpMethod == "GET" && request.RawUrl == "/v1/tasks/status")
            { Reply(context, 200, Status(RecordingApplicationService.Instance.QueryStatus())); return; }
            if (request.RawUrl is "/v2/tasks/status" or "/v2/tasks/prepare-model")
            {
                if (request.Headers["Cookie"] != null || request.Headers["Transfer-Encoding"] != null)
                { Reply(context, 400, new { error = "invalid_task_request" }); return; }
                if (request.HttpMethod == "GET" && request.RawUrl == "/v2/tasks/status")
                {
                    Reply(context, 200, PlatformCollectionHandoff.ProjectV2(
                        PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId,
                        RecordingApplicationService.Instance.QueryStatus())); return;
                }
                if (request.HttpMethod != "POST" || request.RawUrl != "/v2/tasks/prepare-model")
                { Reply(context, 400, new { error = "invalid_task_request" }); return; }
                using JsonDocument modelBody = await ReadBoundedBody(request, PlatformRecordingCommands.MaximumRequestBytes);
                PlatformModelPreparationRequestV2 modelRequest = PlatformRecordingCommands.ReadModelPreparationV2(modelBody.RootElement);
                PlatformModelPreparationResultV2 modelResult = await Dispatch(() => {
                    Interlocked.Exchange(ref nativeWorkStarted, 1);
                    return PlatformCollectionHandoff.PrepareForModel(modelRequest,
                        () => PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId,
                        RecordingApplicationService.Instance.QueryStatus, RecordingApplicationService.Instance.ExecuteForSession);
                });
                Reply(context, 200, modelResult); return;
            }
            if (request.RawUrl is "/v1/tasks/recording/status" or "/v1/tasks/recording/command")
            {
                if (request.Headers["Cookie"] != null || request.Headers["Transfer-Encoding"] != null)
                { Reply(context, 400, new { error = "invalid_recording_request" }); return; }
                if (request.HttpMethod == "GET" && request.RawUrl == "/v1/tasks/recording/status")
                {
                    Reply(context, 200, PlatformRecordingCommands.Project(
                        PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId,
                        RecordingApplicationService.Instance.QueryStatus())); return;
                }
                if (request.HttpMethod != "POST" || request.RawUrl != "/v1/tasks/recording/command")
                { Reply(context, 400, new { error = "invalid_recording_request" }); return; }
                using JsonDocument recordingBody = await ReadBoundedBody(request, PlatformRecordingCommands.MaximumRequestBytes);
                PlatformRecordingRequest recordingRequest = PlatformRecordingCommands.Read(recordingBody.RootElement);
                PlatformRecordingResult recordingResult = await Dispatch(() => {
                    Interlocked.Exchange(ref nativeWorkStarted, 1);
                    return PlatformRecordingCommands.Execute(
                        recordingRequest, () => PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId,
                        RecordingApplicationService.Instance.QueryStatus, RecordingApplicationService.Instance.ExecuteForSession);
                });
                Reply(context, 200, recordingResult); return;
            }
            if (request.HttpMethod == "GET" && request.RawUrl == "/v1/workbench/status")
            { Reply(context, 200, WorkbenchStatus()); return; }
            if (request.HttpMethod == "POST" && request.RawUrl == "/v1/workbench/register")
            { RegisterWorkbench(context); return; }
            if (request.HttpMethod == "POST" && request.RawUrl == "/v1/workbench/unregister")
            { UnregisterWorkbench(context); return; }
            if (request.HttpMethod == "POST" && request.RawUrl == "/v1/workbench/native-register")
            { await RegisterNativeWorkbench(context, nativeAuthority ?? PlatformNativeWorkbenchConnection.Production); return; }
            if (request.HttpMethod == "GET" && request.RawUrl == "/v1/workbench/native-status")
            { NativeWorkbenchStatus(context, nativeAuthority ?? PlatformNativeWorkbenchConnection.Production); return; }
            if (request.HttpMethod == "POST" && request.RawUrl == "/v1/workbench/native-unregister")
            { await CloseNativeWorkbench(context, nativeAuthority ?? PlatformNativeWorkbenchConnection.Production); return; }
            if (request.HttpMethod != "POST" || request.RawUrl != "/v1/tasks/prepare-model"
                || request.ContentType != "application/json" || request.ContentLength64 is <= 0 or > 4096
                || request.Headers["Cookie"] != null || request.Headers["Transfer-Encoding"] != null)
            { Reply(context, 400, new { error = "invalid_task_request" }); return; }
            using var document = await ReadBoundedBody(request, 4096);
            PlatformModelPreparationRequest preparation = PlatformRecordingCommands.ReadModelPreparation(document.RootElement);
            // Both legacy and Source Close use the same native queue; never wait while holding Gate.
            object handoff = await Dispatch(() =>
            {
                Interlocked.Exchange(ref nativeWorkStarted, 1);
                string runtime = PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId;
                if (preparation.RuntimeInstanceId != runtime)
                    throw new PlatformRecordingPreconditionException("game_instance_changed");
                RecordingApplicationStatus result = PlatformCollectionHandoff.Prepare(
                    preparation.RecordingSessionId, preparation.CommandId,
                    RecordingApplicationService.Instance.QueryStatus,
                    RecordingApplicationService.Instance.ExecuteForSession);
                return Status(result);
            });
            Reply(context, 200, handoff);
        }
        catch (PlatformRecordingNotDispatchedException)
        { Reply(context, 503, new { error = "native_task_not_dispatched" }); }
        catch (TimeoutException)
        { Reply(context, 504, new { error = "native_task_command_unknown" }); }
        catch (STS2Connector.MainThreadQueueFullException) when (Volatile.Read(ref nativeWorkStarted) == 0)
        { Reply(context, 503, new { error = "native_task_not_dispatched" }); }
        catch (PlatformRecordingPreconditionException error)
        { Reply(context, 409, new { error = "task_handoff_rejected", detail = PlatformRecordingCommands.PublicCode(error.Message) }); }
        catch (Exception error) when (Volatile.Read(ref nativeWorkStarted) == 0
            && error is JsonException or InvalidOperationException or ArgumentException)
        { Reply(context, 409, new { error = "task_handoff_rejected", detail = PlatformRecordingCommands.PublicCode(error.Message, "invalid_task_request") }); }
        catch (Exception)
        { Reply(context, 500, new { error = "task_handoff_unavailable" }); }
        finally { context.Response.Close(); }
    }

    internal static Task<T> Dispatch<T>(Func<T> work) =>
        PlatformRecordingCommands.OnMainThread(work, STS2Connector.ConnectorMod.RunOnMainThread);

    private static async Task<JsonDocument> ReadBoundedBody(HttpListenerRequest request, int maximum)
    {
        if (request.ContentType != "application/json" || request.ContentLength64 is <= 0
            || request.ContentLength64 > maximum || request.Headers["Transfer-Encoding"] != null)
            throw new ArgumentException("invalid_task_request");
        using var bodyDeadline = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        using var bytes = new MemoryStream();
        byte[] buffer = new byte[1024];
        while (bytes.Length <= maximum)
        {
            int count;
            try { count = await request.InputStream.ReadAsync(buffer.AsMemory(0,
                Math.Min(buffer.Length, maximum + 1 - (int)bytes.Length)), bodyDeadline.Token); }
            catch (OperationCanceledException) { throw new ArgumentException("invalid_task_request"); }
            if (count == 0) break;
            bytes.Write(buffer, 0, count);
        }
        if (bytes.Length != request.ContentLength64 || bytes.Length > maximum)
            throw new ArgumentException("invalid_task_request");
        return JsonDocument.Parse(bytes.ToArray(), new JsonDocumentOptions { MaxDepth = 8 });
    }

    private static object WorkbenchStatus()
    {
        string runtime = PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId;
        PlatformWorkbenchOpenRegistration? registration;
        lock (Gate) registration = _workbenchRegistration;
        bool current = registration?.RuntimeInstanceId == runtime;
        return new
        {
            schema = PlatformWorkbenchOpenClient.StatusSchema,
            status = current ? "registered" : "unregistered",
            runtime_instance_id = runtime,
            workbench_url = current ? registration!.Url : null,
            workbench_instance_id = current ? registration!.WorkbenchInstanceId : null
        };
    }

    private static void RegisterWorkbench(HttpListenerContext context)
    {
        HttpListenerRequest request = context.Request;
        if (request.ContentType != "application/json"
            || request.ContentLength64 is <= 0 or > MaximumWorkbenchRegistrationBytes
            || request.HasEntityBody is false)
        { Reply(context, 400, new { error = "invalid_workbench_request" }); return; }

        using var reader = new StreamReader(request.InputStream);
        using JsonDocument document = JsonDocument.Parse(reader.ReadToEnd());
        string runtime = PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId;
        if (!PlatformWorkbenchOpenClient.TryReadRegistration(
                document.RootElement, runtime, out PlatformWorkbenchOpenRegistration? registration, out string error)
            || registration is null)
        { Reply(context, 409, new { error }); return; }

        lock (Gate)
        {
            PlatformWorkbenchOpenRegistration? active =
                _workbenchRegistration?.RuntimeInstanceId == runtime ? _workbenchRegistration : null;
            if (active is null)
            {
                if (registration.ExpectedWorkbenchInstanceId is not null)
                { Reply(context, 409, new { error = "workbench_instance_conflict" }); return; }
            }
            else if (registration.ExpectedWorkbenchInstanceId != active.WorkbenchInstanceId
                || registration.WorkbenchInstanceId != active.WorkbenchInstanceId
                || registration.Url != active.Url)
            { Reply(context, 409, new { error = "workbench_instance_conflict" }); return; }

            _workbenchRegistration = registration;
        }
        Reply(context, 200, new
        {
            schema = PlatformWorkbenchOpenClient.Schema,
            status = "registered",
            runtime_instance_id = registration.RuntimeInstanceId,
            workbench_instance_id = registration.WorkbenchInstanceId
        });
    }

    private static void UnregisterWorkbench(HttpListenerContext context)
    {
        HttpListenerRequest request = context.Request;
        if (request.ContentType != "application/json"
            || request.ContentLength64 is <= 0 or > MaximumWorkbenchRegistrationBytes
            || request.HasEntityBody is false)
        { Reply(context, 400, new { error = "invalid_workbench_request" }); return; }

        using var reader = new StreamReader(request.InputStream);
        using JsonDocument document = JsonDocument.Parse(reader.ReadToEnd());
        JsonElement body = document.RootElement;
        string[] keys = body.EnumerateObject().Select(p => p.Name).Order().ToArray();
        if (!keys.SequenceEqual(new[] { "runtime_instance_id", "schema", "workbench_instance_id" })
            || body.GetProperty("schema").GetString() != PlatformWorkbenchOpenClient.CloseSchema
            || !PlatformWorkbenchOpenClient.IsWorkbenchInstanceId(body.GetProperty("workbench_instance_id").GetString())
            || string.IsNullOrEmpty(body.GetProperty("runtime_instance_id").GetString()))
        { Reply(context, 400, new { error = "invalid_workbench_request" }); return; }

        string runtime = PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId;
        string? instanceId = body.GetProperty("workbench_instance_id").GetString();
        lock (Gate)
        {
            if (body.GetProperty("runtime_instance_id").GetString() != runtime
                || _workbenchRegistration?.RuntimeInstanceId != runtime
                || _workbenchRegistration.WorkbenchInstanceId != instanceId)
            { Reply(context, 409, new { error = "workbench_instance_conflict" }); return; }
            _workbenchRegistration = null;
        }
        Reply(context, 200, new
        {
            schema = PlatformWorkbenchOpenClient.CloseSchema,
            status = "unregistered",
            runtime_instance_id = runtime,
            workbench_instance_id = instanceId
        });
    }

    private static bool NativeTransport(HttpListenerContext context)
    {
        if (context.Request.Headers["Cookie"] is null
            && context.Request.Headers["Transfer-Encoding"] is null) return true;
        Reply(context, 400, new { error = "invalid_native_pair_request" });
        return false;
    }

    private static async Task RegisterNativeWorkbench(HttpListenerContext context,
        PlatformNativeWorkbenchConnection.Authority authority)
    {
        if (!NativeTransport(context)) return;
        using JsonDocument document = await ReadBoundedBody(context.Request, 4096);
        try
        {
            object ack = authority.Register(document.RootElement);
            Reply(context, 200, ack);
        }
        catch (InvalidOperationException error)
        { Reply(context, 409, new { error = PlatformRecordingCommands.PublicCode(error.Message, "native_pair_identity_mismatch") }); }
    }

    private static void NativeWorkbenchStatus(HttpListenerContext context,
        PlatformNativeWorkbenchConnection.Authority authority)
    {
        if (!NativeTransport(context)) return;
        try
        {
            object proof = authority.CurrentProof(context.Request.Headers);
            Reply(context, 200, proof);
        }
        catch (InvalidOperationException error)
        { Reply(context, 409, new { error = PlatformRecordingCommands.PublicCode(error.Message, "native_pair_changed") }); }
    }

    private static async Task CloseNativeWorkbench(HttpListenerContext context,
        PlatformNativeWorkbenchConnection.Authority authority)
    {
        if (!NativeTransport(context)) return;
        using JsonDocument document = await ReadBoundedBody(context.Request, 4096);
        try
        {
            object result = authority.Close(document.RootElement, context.Request.Headers);
            Reply(context, 200, result);
        }
        catch (InvalidOperationException error)
        { Reply(context, 409, new { error = PlatformRecordingCommands.PublicCode(error.Message, "native_pair_changed") }); }
    }

    private static void Reply(HttpListenerContext context, int code, object value)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(value, Json);
        context.Response.StatusCode = code;
        context.Response.ContentType = "application/json";
        context.Response.Headers["Cache-Control"] = "no-store";
        context.Response.ContentLength64 = bytes.Length;
        context.Response.OutputStream.Write(bytes);
    }
}
