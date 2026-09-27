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

    private static void Handle(HttpListenerContext context)
    {
        try
        {
            var request = context.Request;
            // This is a native-process endpoint, not a browser CORS API.
            if (request.UserHostName != Authority || request.Headers["Origin"] != null
                || request.RemoteEndPoint == null || !IPAddress.IsLoopback(request.RemoteEndPoint.Address))
            { Reply(context, 403, new { error = "native_loopback_required" }); return; }
            if (request.HttpMethod == "GET" && request.RawUrl == "/v1/tasks/status")
            { Reply(context, 200, Status(RecordingApplicationService.Instance.QueryStatus())); return; }
            if (request.HttpMethod == "GET" && request.RawUrl == "/v1/workbench/status")
            { Reply(context, 200, WorkbenchStatus()); return; }
            if (request.HttpMethod == "POST" && request.RawUrl == "/v1/workbench/register")
            { RegisterWorkbench(context); return; }
            if (request.HttpMethod == "POST" && request.RawUrl == "/v1/workbench/unregister")
            { UnregisterWorkbench(context); return; }
            if (request.HttpMethod != "POST" || request.RawUrl != "/v1/tasks/prepare-model"
                || request.ContentType != "application/json" || request.ContentLength64 is <= 0 or > 4096)
            { Reply(context, 400, new { error = "invalid_task_request" }); return; }
            using var reader = new StreamReader(request.InputStream);
            using var document = JsonDocument.Parse(reader.ReadToEnd());
            JsonElement body = document.RootElement;
            string[] keys = body.EnumerateObject().Select(p => p.Name).Order().ToArray();
            if (!keys.SequenceEqual(new[] { "command_id", "recording_session_id", "runtime_instance_id" })
                || !Guid.TryParse(body.GetProperty("command_id").GetString(), out _))
                throw new InvalidOperationException("invalid_task_fields");
            lock (Gate)
            {
                string runtime = PlayerEnvironmentService.GetPlayerEnvironmentControlSnapshot().RuntimeInstanceId;
                if (body.GetProperty("runtime_instance_id").GetString() != runtime)
                    throw new InvalidOperationException("game_instance_changed");
                RecordingApplicationStatus result = PlatformCollectionHandoff.Prepare(
                    body.GetProperty("recording_session_id").GetString(),
                    body.GetProperty("command_id").GetString()!,
                    RecordingApplicationService.Instance.QueryStatus,
                    RecordingApplicationService.Instance.ExecuteForSession);
                Reply(context, 200, Status(result));
            }
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException or ArgumentException)
        { Reply(context, 409, new { error = "task_handoff_rejected", detail = error.Message }); }
        catch (Exception)
        { Reply(context, 500, new { error = "task_handoff_unavailable" }); }
        finally { context.Response.Close(); }
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
