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
            internal Call(string route, string? body = null, string? origin = null, string? cookie = null,
                IReadOnlyDictionary<string, string>? headers = null, PlatformNativeWorkbenchConnection.Authority? authority = null)
            {
                using var port = new TcpListener(IPAddress.Loopback, 0); port.Start();
                int number = ((IPEndPoint)port.LocalEndpoint).Port; port.Stop();
                string address = $"http://127.0.0.1:{number}/";
                listener.Prefixes.Add(address); listener.Start();
                handled = Task.Run(async () => await PlatformTaskBridge.Handle(await listener.GetContextAsync(), authority));
                var request = new HttpRequestMessage(body is null ? HttpMethod.Get : HttpMethod.Post, address.TrimEnd('/') + route);
                request.Headers.Host = "127.0.0.1:15528";
                if (origin is not null) request.Headers.Add("Origin", origin);
                if (cookie is not null) request.Headers.Add("Cookie", cookie);
                if (headers is not null) foreach (var header in headers) request.Headers.Add(header.Key, header.Value);
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

        [Theory]
        [InlineData(RecordingLifecycleState.Recording, "retained_agent_protocol")]
        [InlineData(RecordingLifecycleState.Paused, "paused")]
        public async Task V2ActualHttpRetainsOriginalProtocolOrPausedSourceWithoutOwnerClose(RecordingLifecycleState state, string disposition)
        {
            Reset(); var owner = STS2HumanAnnotator.Mod.RecordingApplicationService.Instance;
            owner.Status = PlatformModelPreparationV2Tests.Source(state: state);
            await using var call = new Call("/v2/tasks/prepare-model", PlatformModelPreparationV2Tests.Request().ToJsonString());
            await Queued(); Assert.False(call.Response.IsCompleted);
            STS2Connector.ConnectorMod.Queue.Drain(1);
            var body = await call.Body(); Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            Assert.Equal(disposition, body.GetProperty("recording_disposition").GetString());
            Assert.Equal(state == RecordingLifecycleState.Recording, body.GetProperty("ready_for_model").GetBoolean());
            Assert.Equal(19, body.GetProperty("model_context").GetProperty("recovery_epoch").GetInt64());
        }

        [Theory]
        [InlineData("context")]
        [InlineData("cookie")]
        [InlineData("duplicate")]
        public async Task V2InvalidCallerContextAndTransportNeverQueue(string defect)
        {
            Reset(); var request = PlatformModelPreparationV2Tests.Request();
            if (defect == "context") request["model_context"]!["input_profile"] = null;
            string body = request.ToJsonString();
            if (defect == "duplicate") body = body.Replace("\"recovery_epoch\":19", "\"recovery_epoch\":1,\"recovery_epoch\":19", StringComparison.Ordinal);
            await using var call = new Call("/v2/tasks/prepare-model", body, cookie: defect == "cookie" ? "browser=1" : null);
            Assert.False((await call.Response).IsSuccessStatusCode);
            Assert.Equal(0, STS2Connector.ConnectorMod.Queue.PendingCount);
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
        private sealed class NativeFixture : IDisposable
        {
            internal string Secret = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            internal readonly string DirectoryPath;
            internal readonly string Runtime = "native-game-" + Guid.NewGuid().ToString("N");
            internal readonly string Artifact = new('8', 64);
            internal readonly PlatformNativeWorkbenchConnection.Authority Authority;
            internal NativeFixture()
            {
                Reset(); STS2Connector.PlayerEnvironment.PlayerEnvironmentService.Runtime = Runtime;
                string temp = OperatingSystem.IsMacOS() ? "/private/tmp" : Path.GetTempPath();
                DirectoryPath = Path.Combine(temp, "pair-bridge-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(DirectoryPath);
                string config = Path.Combine(DirectoryPath, "project.json");
                Write(config, "{}");
                string launcher = Path.Combine(DirectoryPath, "launcher.json");
                Write(launcher, JsonSerializer.Serialize(new { schema = "spireagent/workbench-launcher-v1", config_path = config }));
                string hash = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(launcher))).ToLowerInvariant();
                Write(Path.Combine(DirectoryPath, "native-access.json"), JsonSerializer.Serialize(new {
                    schema = PlatformNativeWorkbenchBootstrap.Schema, enabled = true, config_path = config,
                    launcher_sha256 = hash, game_mod_sha256 = Artifact, secret = Secret }));
                Authority = new(Runtime, Bootstrap);
            }
            private static void Write(string path, string value)
            {
                File.WriteAllText(path, value);
                if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
            }
            internal void Rotate(bool enabled = true)
            {
                string config = Path.Combine(DirectoryPath, "project.json");
                string launcher = Path.Combine(DirectoryPath, "launcher.json");
                if (enabled) Secret = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
                string hash = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(launcher))).ToLowerInvariant();
                Write(Path.Combine(DirectoryPath, "native-access.json"), JsonSerializer.Serialize(new {
                    schema = PlatformNativeWorkbenchBootstrap.Schema, enabled, config_path = config,
                    launcher_sha256 = hash, game_mod_sha256 = Artifact, secret = Secret }));
            }
            internal PlatformNativeWorkbenchBootstrap Bootstrap() => PlatformNativeWorkbenchBootstrap.Read(Artifact, DirectoryPath);
            internal PlatformNativeWorkbenchPair Pair(long? expiry = null, string? workbench = null, string? pair = null) => new(
                Runtime, workbench ?? new string('b', 32), new string('c', 64), "http://127.0.0.1:12345/",
                pair ?? new string('d', 32), expiry ?? DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 300);
            internal Dictionary<string, string> Headers(PlatformNativeWorkbenchPair pair) => new() {
                ["Authorization"] = "Bearer " + pair.Sign(Secret, "native-access-v1"),
                ["X-STS2-Game-Instance-ID"] = pair.RuntimeInstanceId,
                ["X-SpireAgent-Workbench-Instance-ID"] = pair.WorkbenchInstanceId,
                ["X-SpireAgent-Configuration-ID"] = pair.ConfigurationId,
                ["X-SpireAgent-Pair-ID"] = pair.PairId };
            internal Call Register(PlatformNativeWorkbenchPair pair) => new("/v1/workbench/native-register",
                JsonSerializer.Serialize(pair.Signed(PlatformNativeWorkbenchPair.Schema, "native-register-v1", Secret)), authority: Authority);
            internal Call Close(PlatformNativeWorkbenchPair pair) => new("/v1/workbench/native-unregister",
                JsonSerializer.Serialize(pair), headers: Headers(pair), authority: Authority);
            internal Call Current(PlatformNativeWorkbenchPair pair) => new("/v1/workbench/native-status", headers: Headers(pair), authority: Authority);
            public void Dispose() => Directory.Delete(DirectoryPath, true);
        }

        [Fact]
        public async Task RealBridgeForeignLegacyAndSignedNativeCoexistAndUnregisterIndependently()
        {
            using var fixture = new NativeFixture();
            string legacy = new string('a', 32);
            await using (var call = new Call("/v1/workbench/register", JsonSerializer.Serialize(new {
                schema = PlatformWorkbenchOpenClient.Schema, workbench_url = "http://127.0.0.1:23456/",
                workbench_instance_id = legacy, runtime_instance_id = fixture.Runtime, expected_workbench_instance_id = (string?)null })))
                Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            string before;
            await using (var call = new Call("/v1/workbench/status")) before = (await call.Body()).GetRawText();
            var pair = fixture.Pair();
            await using (var call = fixture.Register(pair))
            {
                Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
                Assert.Equal(pair, PlatformNativeWorkbenchPair.ReadSigned(await call.Body(),
                    PlatformNativeWorkbenchPair.AckSchema, "native-register-ack-v1", fixture.Secret));
            }
            await using (var call = new Call("/v1/workbench/status")) Assert.Equal(before, (await call.Body()).GetRawText());
            await using (var call = fixture.Current(pair)) Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            await using (var call = new Call("/v1/workbench/unregister", JsonSerializer.Serialize(new {
                schema = PlatformWorkbenchOpenClient.CloseSchema, workbench_instance_id = legacy, runtime_instance_id = fixture.Runtime })))
                Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            await using (var call = fixture.Current(pair)) Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            await using (var call = fixture.Close(pair)) Assert.Equal("closed", (await call.Body()).GetProperty("status").GetString());
            await using (var call = fixture.Register(pair)) Assert.Equal(HttpStatusCode.Conflict, (await call.Response).StatusCode);
            Assert.Equal(0, STS2Connector.ConnectorMod.Queue.PendingCount);
        }

        [Fact]
        public async Task RealBridgeFirstSignedPairRenewalCloseAndPendingReplayHaveTerminalDisposition()
        {
            using var fixture = new NativeFixture(); var pair = fixture.Pair();
            await using (var call = fixture.Register(pair)) Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            await using (var call = fixture.Register(pair)) Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            var equal = pair with { PairId = new string('e', 32) };
            await using (var call = fixture.Register(equal)) Assert.Equal(HttpStatusCode.Conflict, (await call.Response).StatusCode);
            var renewal = equal with { ExpiresAt = pair.ExpiresAt + 100 };
            await using (var call = fixture.Register(renewal)) Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            await using (var call = fixture.Close(pair)) Assert.Equal(HttpStatusCode.Conflict, (await call.Response).StatusCode);
            await using (var call = fixture.Current(renewal)) Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            await using (var call = fixture.Close(renewal)) Assert.Equal("closed", (await call.Body()).GetProperty("status").GetString());
            await using (var call = fixture.Close(renewal)) Assert.Equal("already_closed", (await call.Body()).GetProperty("status").GetString());
            await using (var call = fixture.Register(pair)) Assert.Equal(HttpStatusCode.Conflict, (await call.Response).StatusCode);
            var pending = renewal with { PairId = new string('f', 32), ExpiresAt = renewal.ExpiresAt + 50 };
            await using (var call = fixture.Register(pending)) Assert.Equal(HttpStatusCode.Conflict, (await call.Response).StatusCode);
            var newOwner = pending with { WorkbenchInstanceId = new string('1', 32) };
            await using (var call = fixture.Register(newOwner)) Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
        }

        [Fact]
        public void RetirementCapacityClockAndExplicitCredentialBoundaryNeverEvictClosedContexts()
        {
            using var fixture = new NativeFixture();
            long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            var authority = new PlatformNativeWorkbenchConnection.Authority(fixture.Runtime, fixture.Bootstrap, () => now);
            object Register(PlatformNativeWorkbenchPair pair) {
                using var body = JsonDocument.Parse(JsonSerializer.Serialize(pair.Signed(
                    PlatformNativeWorkbenchPair.Schema, "native-register-v1", fixture.Secret)));
                return authority.Register(body.RootElement);
            }
            object Close(PlatformNativeWorkbenchPair pair) {
                using var body = JsonDocument.Parse(JsonSerializer.Serialize(pair));
                var headers = new System.Collections.Specialized.NameValueCollection();
                foreach (var header in fixture.Headers(pair)) headers.Add(header.Key, header.Value);
                return authority.Close(body.RootElement, headers);
            }
            for (int index = 0; index < PlatformNativeWorkbenchConnection.Authority.MaximumRetiredContexts; index++)
            {
                var pair = fixture.Pair(now + 300, index.ToString("x32"));
                Register(pair);
                if (index == 31) {
                    var renewal = pair with { PairId = new string('e', 32), ExpiresAt = now + 400 };
                    Register(renewal); // The reserved32nd slot survives renewal.
                    Assert.Throws<InvalidOperationException>(() => Close(pair));
                    pair = renewal;
                }
                Close(pair); Close(pair); // Duplicate Close consumes no entry.
            }
            var blocked = fixture.Pair(now + 300, new string('f', 32));
            Assert.Throws<InvalidOperationException>(() => Register(blocked));
            Assert.Throws<InvalidOperationException>(() => Close(blocked));
            now += 601;
            var firstContext = fixture.Pair(now + 300, 0.ToString("x32"));
            Assert.Throws<InvalidOperationException>(() => Register(firstContext));
            now -= 601;
            Assert.Throws<InvalidOperationException>(() => Register(blocked));
            fixture.Rotate();
            Register(blocked);
        }

        [Fact]
        public async Task RealBridgeWrongRoleHeadersAndOversizedNativeBodyDoNotReplacePair()
        {
            using var fixture = new NativeFixture(); var pair = fixture.Pair();
            await using (var call = fixture.Register(pair)) Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            await using (var call = new Call("/v1/workbench/native-register", JsonSerializer.Serialize(
                pair.Signed(PlatformNativeWorkbenchPair.Schema, "native-access-v1", fixture.Secret)), authority: fixture.Authority))
                Assert.Equal(HttpStatusCode.Conflict, (await call.Response).StatusCode);
            var headers = fixture.Headers(pair); headers["X-SpireAgent-Pair-ID"] = new string('f', 32);
            await using (var call = new Call("/v1/workbench/native-unregister", JsonSerializer.Serialize(pair), headers: headers, authority: fixture.Authority))
                Assert.Equal(HttpStatusCode.Conflict, (await call.Response).StatusCode);
            await using (var call = new Call("/v1/workbench/native-register", new string(' ', 4097), authority: fixture.Authority))
                Assert.False((await call.Response).IsSuccessStatusCode);
            await using (var call = fixture.Current(pair)) Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
        }
        [Fact]
        public async Task DelayedOldRequestAndCurrentReadCannotResetNewCredentialRetirement()
        {
            using var fixture = new NativeFixture();
            var oldPair = fixture.Pair();
            string oldRequest = JsonSerializer.Serialize(oldPair.Signed(
                PlatformNativeWorkbenchPair.Schema, "native-register-v1", fixture.Secret));
            var ready = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            Task oldRequestTask = Task.Run(async () => {
                ready.SetResult(); await release.Task;
                await using var call = new Call("/v1/workbench/native-register", oldRequest, authority: fixture.Authority);
                Assert.Equal(HttpStatusCode.Conflict, (await call.Response).StatusCode);
            });
            Task oldReadTask = Task.Run(async () => { await release.Task; Assert.Null(fixture.Authority.Current); });
            await ready.Task;
            fixture.Rotate();
            var selected = fixture.Pair(workbench: new string('2', 32));
            await using (var call = fixture.Register(selected)) Assert.Equal(HttpStatusCode.OK, (await call.Response).StatusCode);
            await using (var call = fixture.Close(selected)) Assert.Equal("closed", (await call.Body()).GetProperty("status").GetString());
            release.SetResult(); await Task.WhenAll(oldRequestTask, oldReadTask);
            await using (var call = fixture.Register(selected)) Assert.Equal(HttpStatusCode.Conflict, (await call.Response).StatusCode);
            fixture.Rotate(enabled: false);
            Assert.Null(fixture.Authority.Current);
            await using (var call = fixture.Register(selected)) Assert.False((await call.Response).IsSuccessStatusCode);
        }

        [Fact]
        public void BoundProcessRuntimeCannotBeReplacedByRequestOrCurrentRead()
        {
            using var fixture = new NativeFixture();
            var wrong = fixture.Pair() with { RuntimeInstanceId = "different-process" };
            using var body = JsonDocument.Parse(JsonSerializer.Serialize(wrong.Signed(
                PlatformNativeWorkbenchPair.Schema, "native-register-v1", fixture.Secret)));
            Assert.Throws<InvalidOperationException>(() => fixture.Authority.Register(body.RootElement));
            Assert.Equal(fixture.Runtime, fixture.Authority.Runtime);
            Assert.Null(fixture.Authority.Current);
        }
        [Fact]
        public async Task AuthoritativeReaderIsSerializedUnlikeRetainedPreGateReadOrdering()
        {
            async Task Exercise(bool productionOwner)
            {
                using var fixture = new NativeFixture();
                using var firstCaptured = new ManualResetEventSlim();
                using var firstRelease = new ManualResetEventSlim();
                using var secondDispatched = new ManualResetEventSlim();
                using var secondReaderEntered = new ManualResetEventSlim();
                int reads = 0;
                PlatformNativeWorkbenchBootstrap Reader()
                {
                    // Capture the actual private fixture before waiting, exactly at
                    // the historical vulnerable pre-return authority-read boundary.
                    var snapshot = fixture.Bootstrap();
                    if (Interlocked.Increment(ref reads) == 1)
                    {
                        firstCaptured.Set();
                        Assert.True(firstRelease.Wait(TimeSpan.FromSeconds(3)));
                    }
                    else secondReaderEntered.Set();
                    return snapshot;
                }
                var authority = new PlatformNativeWorkbenchConnection.Authority(fixture.Runtime, Reader);
                object oldGate = new();
                PlatformNativeWorkbenchBootstrap RetainedPreGateRead()
                {
                    // Concrete ordering retained from6b: Bridge/Current read the
                    // bootstrap before Connection's Gate, then used that snapshot.
                    var snapshot = Reader();
                    lock (oldGate) return snapshot;
                }
                var pair = fixture.Pair();
                string signed = JsonSerializer.Serialize(pair.Signed(
                    PlatformNativeWorkbenchPair.Schema, "native-register-v1", fixture.Secret));
                Task first = Task.Run(() => {
                    if (productionOwner) {
                        using var body = JsonDocument.Parse(signed);
                        authority.Register(body.RootElement);
                    }
                    else _ = RetainedPreGateRead();
                });
                Task? second = null;
                try
                {
                    Assert.True(firstCaptured.Wait(TimeSpan.FromSeconds(2)));
                    second = Task.Run(() => {
                        secondDispatched.Set();
                        if (productionOwner) Assert.NotNull(authority.Current);
                        else _ = RetainedPreGateRead();
                    });
                    Assert.True(secondDispatched.Wait(TimeSpan.FromSeconds(2)));
                    if (productionOwner)
                    {
                        // Finite scheduler/lock falsifier, not a native timer,
                        // delivery/effect inference or source-text assertion.
                        Assert.False(secondReaderEntered.Wait(TimeSpan.FromMilliseconds(150)));
                    }
                    else Assert.True(secondReaderEntered.Wait(TimeSpan.FromSeconds(2)));
                }
                finally { firstRelease.Set(); }
                await first.WaitAsync(TimeSpan.FromSeconds(3));
                if (second is not null) await second.WaitAsync(TimeSpan.FromSeconds(3));
                Assert.True(secondReaderEntered.IsSet);
                if (!productionOwner) return;

                fixture.Rotate();
                var selected = fixture.Pair(workbench: new string('7', 32));
                using var newerBody = JsonDocument.Parse(JsonSerializer.Serialize(selected.Signed(
                    PlatformNativeWorkbenchPair.Schema, "native-register-v1", fixture.Secret)));
                authority.Register(newerBody.RootElement);
                using var closeBody = JsonDocument.Parse(JsonSerializer.Serialize(selected));
                var headers = new System.Collections.Specialized.NameValueCollection();
                foreach (var header in fixture.Headers(selected)) headers.Add(header.Key, header.Value);
                authority.Close(closeBody.RootElement, headers);
                using var oldBody = JsonDocument.Parse(signed);
                Assert.Throws<InvalidOperationException>(() => authority.Register(oldBody.RootElement));
                Assert.Null(authority.Current);
                Assert.Throws<InvalidOperationException>(() => authority.Register(newerBody.RootElement));
            }
            await Exercise(productionOwner: true);
            await Exercise(productionOwner: false);
        }
    }
}
