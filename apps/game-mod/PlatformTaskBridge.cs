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

    internal static async Task Handle(HttpListenerContext context)
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
            { RegisterNativeWorkbench(context); return; }
            if (request.HttpMethod == "GET" && request.RawUrl == "/v1/workbench/native-status")
            { NativeWorkbenchStatus(context); return; }
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
            PlatformNativeWorkbenchConnection.Clear(instanceId!);
        }
        Reply(context, 200, new
        {
            schema = PlatformWorkbenchOpenClient.CloseSchema,
            status = "unregistered",
            runtime_instance_id = runtime,
            workbench_instance_id = instanceId
        });
    }

    private static void RegisterNativeWorkbench(HttpListenerContext context)
    {
        HttpListenerRequest request = context.Request;
        if (request.ContentType != "application/json" || request.ContentLength64 is <= 0 or > 4096
            || request.Headers["Cookie"] != null || request.Headers["Transfer-Encoding"] != null)
        { Reply(context, 400, new { error = "invalid_native_pair_request" }); return; }
        using var reader = new StreamReader(request.InputStream);
        using JsonDocument document = JsonDocument.Parse(reader.ReadToEnd());
        PlatformNativeWorkbenchBootstrap bootstrap = PlatformNativeWorkbenchBootstrap.Read(
            PlatformLiveUiMod.CurrentArtifactIdentity().ArtifactSha256 ?? "unavailable");
        PlatformNativeWorkbenchPair pair = PlatformNativeWorkbenchPair.ReadSigned(
            document.RootElement, PlatformNativeWorkbenchPair.Schema, "native-register-v1", bootstrap.Secret);
        long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        lock (Gate)
        {
            string runtime = PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId;
            if (pair.ExpiresAt <= now || pair.ExpiresAt > now + 600 || pair.RuntimeInstanceId != runtime
                || _workbenchRegistration?.RuntimeInstanceId != runtime
                || _workbenchRegistration.WorkbenchInstanceId != pair.WorkbenchInstanceId
                || _workbenchRegistration.Url != pair.WorkbenchUrl)
            { Reply(context, 409, new { error = "native_pair_identity_mismatch" }); return; }
            PlatformNativeWorkbenchConnection.Install(pair, bootstrap.Secret);
        }
        Reply(context, 200, pair.Signed(PlatformNativeWorkbenchPair.AckSchema,
            "native-register-ack-v1", bootstrap.Secret));
    }

    private static void NativeWorkbenchStatus(HttpListenerContext context)
    {
        PlatformNativeWorkbenchConnection? connection = PlatformNativeWorkbenchConnection.Current;
        if (connection is null || context.Request.Headers["Cookie"] != null)
        { Reply(context, 403, new { error = "native_pair_required" }); return; }
        PlatformNativeWorkbenchPair pair = connection.Binding;
        PlatformNativeWorkbenchBootstrap bootstrap = PlatformNativeWorkbenchBootstrap.Read(
            PlatformLiveUiMod.CurrentArtifactIdentity().ArtifactSha256 ?? "unavailable");
        string runtime = PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId;
        bool current;
        lock (Gate) current = _workbenchRegistration?.RuntimeInstanceId == runtime
            && _workbenchRegistration.WorkbenchInstanceId == pair.WorkbenchInstanceId
            && _workbenchRegistration.Url == pair.WorkbenchUrl;
        if (!current || pair.RuntimeInstanceId != runtime || pair.ExpiresAt <= DateTimeOffset.UtcNow.ToUnixTimeSeconds()
            || !PlatformNativeWorkbenchPair.EqualSecret(context.Request.Headers["Authorization"],
                "Bearer " + pair.Sign(bootstrap.Secret, "native-access-v1"))
            || context.Request.Headers["X-STS2-Game-Instance-ID"] != pair.RuntimeInstanceId
            || context.Request.Headers["X-SpireAgent-Workbench-Instance-ID"] != pair.WorkbenchInstanceId
            || context.Request.Headers["X-SpireAgent-Configuration-ID"] != pair.ConfigurationId
            || context.Request.Headers["X-SpireAgent-Pair-ID"] != pair.PairId)
        { Reply(context, 409, new { error = "native_pair_changed" }); return; }
        Reply(context, 200, pair.Signed(PlatformNativeWorkbenchPair.CurrentSchema,
            "native-current-v1", bootstrap.Secret));
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
