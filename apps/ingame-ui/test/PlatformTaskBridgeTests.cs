using System.Net;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using STS2HumanAnnotator.Core;
using STS2Platform.GameMod;
using STS2PlatformLiveUi;
using Xunit;

// Native-only dependencies are typed doubles; HTTP handler, strict codec,
// handoff and the bounded native queue are the exact production source.
namespace STS2Connector
{
    internal static class ConnectorMod
    {
        internal static MainThreadWorkQueue Queue = new();
        internal static Task<T> RunOnMainThread<T>(Func<T> work, CancellationToken cancellation = default) => Queue.Enqueue(work, cancellation);
    }
}
namespace STS2Connector.PlayerEnvironment
{
    internal sealed record TestControl(string RuntimeInstanceId);
    internal static class PlayerEnvironmentService
    {
        internal static string Runtime = "game-1";
        internal static TestControl GetPlayerEnvironmentControlSnapshot() => new(Runtime);
    }
}
namespace STS2HumanAnnotator.Mod
{
    public sealed class RecordingApplicationService
    {
        public static RecordingApplicationService Instance { get; } = new();
        internal RecordingApplicationStatus Status = STS2PlatformLiveUiTests.PlatformRecordingCommandsTests.Status();
        internal Func<RecordingCommand, string?, RecordingCommandResult> Execute = (_, _) => throw new InvalidOperationException("unexpected_test_mutation");
        public RecordingApplicationStatus QueryStatus() => Status;
        public RecordingCommandResult ExecuteForSession(RecordingCommand command, string? expected) => Execute(command, expected);
    }
}
namespace STS2PlatformLiveUi
{
    internal static class PlatformLiveUiMod
    {
        internal static (string? ArtifactSha256, string? ModuleVersionId) CurrentArtifactIdentity() => (null, null);
    }
}
namespace STS2PlatformLiveUiTests
{
    [CollectionDefinition("Recording task bridge", DisableParallelization = true)]
    public sealed class RecordingTaskBridgeCollection { }

    [Collection("Recording task bridge")]
    public sealed class PlatformTaskBridgeTests
    {
        private sealed class Call : IAsyncDisposable
        {
            private readonly HttpListener listener = new();
            private readonly HttpClient client = new(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false });
            internal readonly Task<HttpResponseMessage> Response;
            private readonly Task handled;
            internal Call(string route, string? body = null, string? origin = null, string? cookie = null)
            {
                using var port = new TcpListener(IPAddress.Loopback, 0); port.Start();
                int number = ((IPEndPoint)port.LocalEndpoint).Port; port.Stop();
                string address = $"http://127.0.0.1:{number}/";
                listener.Prefixes.Add(address); listener.Start();
                handled = Task.Run(async () => await PlatformTaskBridge.Handle(await listener.GetContextAsync()));
                var request = new HttpRequestMessage(body is null ? HttpMethod.Get : HttpMethod.Post, address.TrimEnd('/') + route);
                request.Headers.Host = "127.0.0.1:15528";
                if (origin is not null) request.Headers.Add("Origin", origin);
                if (cookie is not null) request.Headers.Add("Cookie", cookie);
                if (body is not null)
                {
                    request.Content = new ByteArrayContent(Encoding.UTF8.GetBytes(body));
                    request.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
                }
                Response = client.SendAsync(request);
            }
            public async ValueTask DisposeAsync()
            {
                try { await handled.WaitAsync(TimeSpan.FromSeconds(15)); }
                finally { listener.Close(); client.Dispose(); }
            }
            internal async Task<JsonElement> Body()
            { using var json = JsonDocument.Parse(await (await Response).Content.ReadAsStringAsync()); return json.RootElement.Clone(); }
        }

        private static void Reset(int capacity = 256)
        {
            STS2Connector.ConnectorMod.Queue = new(capacity);
            STS2Connector.PlayerEnvironment.PlayerEnvironmentService.Runtime = "game-1";
            STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.Status = PlatformRecordingCommandsTests.Status();
            STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.Execute = (_, _) => throw new InvalidOperationException("unexpected_test_mutation");
        }
        private static async Task Queued()
        {
            for (int attempt = 0; attempt < 200; attempt++)
            {
                if (STS2Connector.ConnectorMod.Queue.PendingCount > 0) return;
                await Task.Delay(5);
            }
            Assert.Fail("Actual HTTP bridge never queued its native command.");
        }

        [Fact]
        public async Task ActualHttpSourceMutationRunsOnlyWhenTheNativeQueueDrains()
        {
            Reset(); int calls = 0, nativeThread = 0;
            var owner = STS2HumanAnnotator.Mod.RecordingApplicationService.Instance;
            owner.Execute = (command, expected) => {
                calls++; Assert.Equal(nativeThread, Environment.CurrentManagedThreadId);
                Assert.Equal("recording-1", expected); Assert.Equal(SourceSessionContractV2.CommandSchema, command.Schema);
                Assert.Equal(SourceSessionContractV3.ProfileId, command.CaptureProfileId);
                Assert.Equal("agent_native_ui", command.SourceDeclaration!.SourceKind);
                return new(true, false, "recording", "private detail", owner.Status.Lifecycle);
            };
            await using var call = new Call("/v1/tasks/recording/command", PlatformRecordingCommandsTests.Request().ToJsonString());
            await Queued(); Assert.Equal(0, calls); Assert.False(call.Response.IsCompleted);
            nativeThread = Environment.CurrentManagedThreadId;
            Assert.Equal(1, STS2Connector.ConnectorMod.Queue.Drain(1));
            Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            var body = await call.Body(); Assert.True(body.GetProperty("accepted").GetBoolean());
            Assert.Equal(1, calls); Assert.DoesNotContain("private", body.GetRawText());
        }

        [Theory]
        [InlineData(SourceSessionContract.ProfileId, RecordingApplicationContract.SourceCommandSchema)]
        [InlineData(SourceSessionContractV2.ProfileId, SourceSessionContractV2.CommandSchema)]
        [InlineData(SourceSessionContractV3.ProfileId, SourceSessionContractV2.CommandSchema)]
        public async Task ExistingPrepareModelHttpUsesNativeQueueAndCorrectSourceCloseSchema(string profile, string schema)
        {
            Reset(); var owner = STS2HumanAnnotator.Mod.RecordingApplicationService.Instance;
            owner.Status = PlatformRecordingCommandsTests.Status(profile); int calls = 0;
            owner.Execute = (command, expected) => {
                calls++; Assert.Equal(schema, command.Schema); Assert.Equal(RecordingCommandKind.Close, command.Kind);
                Assert.Equal("recording-1", expected);
                owner.Status = owner.Status with { Lifecycle = owner.Status.Lifecycle with { State = RecordingLifecycleState.Closing },
                    Closeout = new("closing", DateTimeOffset.UtcNow, null, null) };
                return new(true, true, "closing", "pending", owner.Status.Lifecycle);
            };
            await using var call = new Call("/v1/tasks/prepare-model", JsonSerializer.Serialize(new {
                runtime_instance_id = "game-1", recording_session_id = "recording-1", command_id = Guid.NewGuid().ToString("D") }));
            await Queued(); Assert.Equal(0, calls); STS2Connector.ConnectorMod.Queue.Drain(1);
            var body = await call.Body(); Assert.Equal(1, calls);
            Assert.Equal("closing", body.GetProperty("recording_lifecycle").GetString());
            Assert.False(body.GetProperty("ready_for_model").GetBoolean());
        }

        [Fact]
        public async Task RuntimeChangeBeforeNativeDispatchNeverReachesRecordingOwner()
        {
            Reset(); int calls = 0;
            STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.Execute = (_, _) => { calls++; throw new Exception(); };
            await using var call = new Call("/v1/tasks/recording/command", PlatformRecordingCommandsTests.Request("close").ToJsonString());
            await Queued(); STS2Connector.PlayerEnvironment.PlayerEnvironmentService.Runtime = "replacement";
            STS2Connector.ConnectorMod.Queue.Drain(1);
            Assert.Equal(HttpStatusCode.Conflict, (await call.Response).StatusCode);
            Assert.Equal(0, calls); Assert.Equal("recording_game_instance_changed", (await call.Body()).GetProperty("detail").GetString());
        }

        [Theory]
        [InlineData("origin")]
        [InlineData("cookie")]
        [InlineData("oversize")]
        [InlineData("old_schema")]
        public async Task InvalidTransportOrOldSourceCommandNeverQueues(string defect)
        {
            Reset(); var request = PlatformRecordingCommandsTests.Request();
            if (defect == "old_schema") request["command"]!["schema"] = RecordingApplicationContract.CommandSchema;
            string body = defect == "oversize" ? new string('x', 4097) : request.ToJsonString();
            await using var call = new Call("/v1/tasks/recording/command", body,
                defect == "origin" ? "http://localhost" : null, defect == "cookie" ? "session=browser" : null);
            Assert.False((await call.Response).IsSuccessStatusCode);
            Assert.Equal(0, STS2Connector.ConnectorMod.Queue.PendingCount);
        }

        [Fact]
        public async Task QueueDeadlineBeforeStartIsDefiniteNonDispatchAndRemovesPendingWork()
        {
            Reset(); int calls = 0;
            STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.Execute = (_, _) => { calls++; throw new Exception(); };
            await using var call = new Call("/v1/tasks/recording/command", PlatformRecordingCommandsTests.Request("close").ToJsonString());
            await Queued();
            Assert.Equal(HttpStatusCode.ServiceUnavailable, (await call.Response).StatusCode);
            Assert.Equal("native_task_not_dispatched", (await call.Body()).GetProperty("error").GetString());
            Assert.Equal(0, STS2Connector.ConnectorMod.Queue.Drain(1)); Assert.Equal(0, calls);
        }

        [Fact]
        public async Task CancellationThrownAfterStartedOwnerCallIsUnconfirmedNotNonDispatch()
        {
            Reset(); int calls = 0;
            STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.Execute = (_, _) => {
                calls++; throw new OperationCanceledException("started owner result unavailable"); };
            await using var call = new Call("/v1/tasks/recording/command", PlatformRecordingCommandsTests.Request("close").ToJsonString());
            await Queued(); STS2Connector.ConnectorMod.Queue.Drain(1);
            Assert.Equal(HttpStatusCode.InternalServerError, (await call.Response).StatusCode);
            Assert.Equal("task_handoff_unavailable", (await call.Body()).GetProperty("error").GetString());
            Assert.Equal(1, calls);
        }

        [Fact]
        public async Task SaturatedExistingQueueDoesNotDispatchRecordingWork()
        {
            Reset(1);
            using var cancel = new CancellationTokenSource();
            var blocker = STS2Connector.ConnectorMod.Queue.Enqueue(() => true, cancel.Token);
            await using var call = new Call("/v1/tasks/recording/command", PlatformRecordingCommandsTests.Request("close").ToJsonString());
            Assert.Equal(HttpStatusCode.ServiceUnavailable, (await call.Response).StatusCode);
            Assert.Equal("native_task_not_dispatched", (await call.Body()).GetProperty("error").GetString());
            cancel.Cancel(); await Assert.ThrowsAnyAsync<OperationCanceledException>(() => blocker);
            Assert.Equal(0, STS2Connector.ConnectorMod.Queue.PendingCount);
        }
    }
}
