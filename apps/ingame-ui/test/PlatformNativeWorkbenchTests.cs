using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using STS2PlatformLiveUi;
using Xunit;

public sealed class PlatformNativeWorkbenchTests
{
    private const string Secret = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private static PlatformNativeWorkbenchPair Pair(long? expiry = null) => new("fixture-game", new string('1', 32),
        new string('2', 64), "http://127.0.0.1:12345/", new string('3', 32), expiry ?? DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 500);
    private static PlatformNativeWorkbenchConnection Connection(PlatformNativeWorkbenchPair? pair = null)
    { pair ??= Pair(); return new(pair, pair.Sign(Secret, "native-access-v1")); }
    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> handler) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => handler(request, token);
    }
    private static HttpResponseMessage Response(object value) => new(HttpStatusCode.OK)
    { Content = new StringContent(JsonSerializer.Serialize(value), Encoding.UTF8, "application/json") };
    private static object Result(PlatformNativeWorkbenchPair pair, string requestId, string status = "accepted", object? owner = null) => new
    { schema = PlatformNativeWorkbenchClient.ResultSchema, binding = pair, request_id = requestId, status, owner_response = owner ?? new { status = "pending" }, error = (object?)null };

    [Fact]
    public void SharedPythonAndCsharpSigningBytesAndDistinctRolesMatch()
    {
        using JsonDocument fixture = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(AppContext.BaseDirectory, "native_workbench_pair_v1.json")));
        Assert.True(fixture.RootElement.GetProperty("fixture_only").GetBoolean());
        PlatformNativeWorkbenchPair pair = PlatformNativeWorkbenchPair.Read(fixture.RootElement.GetProperty("binding"));
        foreach (JsonProperty role in fixture.RootElement.GetProperty("roles").EnumerateObject())
            Assert.Equal(role.Value.GetProperty("hmac_sha256").GetString(), pair.Sign(Secret, role.Name));
        Assert.Throws<InvalidOperationException>(() => pair.Sign(Secret, "native-register-v1\ninjected"));
        Assert.Throws<InvalidOperationException>(() => (pair with { RuntimeInstanceId = "game\nother" }).Validate());
        Assert.Throws<InvalidOperationException>(() => (pair with { WorkbenchUrl = "https://other.example/" }).Validate());
    }

    [Fact]
    public void SignedPairRejectsTamperingDuplicatesAndDifferentRoles()
    {
        PlatformNativeWorkbenchPair pair = Pair();
        string body = JsonSerializer.Serialize(pair.Signed(PlatformNativeWorkbenchPair.Schema, "native-register-v1", Secret));
        using JsonDocument original = JsonDocument.Parse(body);
        Assert.Equal(pair, PlatformNativeWorkbenchPair.ReadSigned(original.RootElement, PlatformNativeWorkbenchPair.Schema, "native-register-v1", Secret));
        using JsonDocument tampered = JsonDocument.Parse(body.Replace("fixture-game", "different-game", StringComparison.Ordinal));
        Assert.Throws<InvalidOperationException>(() => PlatformNativeWorkbenchPair.ReadSigned(tampered.RootElement, PlatformNativeWorkbenchPair.Schema, "native-register-v1", Secret));
        Assert.Throws<InvalidOperationException>(() => PlatformNativeWorkbenchPair.ReadSigned(original.RootElement, PlatformNativeWorkbenchPair.Schema, "native-access-v1", Secret));
        using JsonDocument duplicate = JsonDocument.Parse(body.Replace("{", "{\"pair_id\":\"duplicate\",", StringComparison.Ordinal));
        Assert.Throws<InvalidOperationException>(() => PlatformNativeWorkbenchPair.ReadSigned(duplicate.RootElement, PlatformNativeWorkbenchPair.Schema, "native-register-v1", Secret));
        Assert.DoesNotContain(Secret, JsonSerializer.Serialize(pair.Signed(PlatformNativeWorkbenchPair.CurrentSchema, "native-current-v1", Secret)));
        Assert.DoesNotContain(Connection(pair).Token, JsonSerializer.Serialize(Connection(pair)));
    }

    [Fact]
    public void BootstrapRequiresExactPrivateSelectedLauncherAndLoadedArtifact()
    {
        string temp = OperatingSystem.IsMacOS() ? "/private/tmp" : Path.GetTempPath();
        string root = Path.Combine(temp, "native-workbench-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string config = Path.Combine(root, "project.json");
            void Private(string path, string value)
            { File.WriteAllText(path, value); if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite); }
            Private(config, "{}");
            string launcherPath = Path.Combine(root, "launcher.json");
            Private(launcherPath, JsonSerializer.Serialize(new { schema = "spireagent/workbench-launcher-v1", config_path = config }));
            string launcherHash = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(launcherPath))).ToLowerInvariant();
            string artifact = new string('8', 64);
            string path = Path.Combine(root, "native-access.json");
            Private(path, JsonSerializer.Serialize(new { schema = PlatformNativeWorkbenchBootstrap.Schema, enabled = true,
                config_path = config, launcher_sha256 = launcherHash, game_mod_sha256 = artifact, secret = Secret }));
            Assert.Equal(config, PlatformNativeWorkbenchBootstrap.Read(artifact, root).ConfigPath);
            Assert.DoesNotContain(Secret, PlatformNativeWorkbenchBootstrap.Read(artifact, root).ToString());
            Assert.Throws<InvalidOperationException>(() => PlatformNativeWorkbenchBootstrap.Read(new string('9', 64), root));
            File.WriteAllText(launcherPath, "{\"changed\":true}");
            Assert.Throws<InvalidOperationException>(() => PlatformNativeWorkbenchBootstrap.Read(artifact, root));
            if (!OperatingSystem.IsWindows())
            {
                File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.OtherRead);
                Assert.Throws<InvalidOperationException>(() => PlatformNativeWorkbenchBootstrap.Read(artifact, root));
            }
            File.Delete(path);
            Assert.Throws<FileNotFoundException>(() => PlatformNativeWorkbenchBootstrap.Read(artifact, root));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task ViewBindsExactPairAndExternalContextNeverCarriesCredential()
    {
        PlatformNativeWorkbenchConnection connection = Connection();
        using var http = new HttpClient(new Handler((request, _) => {
            Assert.Equal("Bearer " + connection.Token, request.Headers.GetValues("Authorization").Single());
            Assert.False(request.Headers.Contains("Cookie")); Assert.False(request.Headers.Contains("Origin"));
            return Task.FromResult(Response(new { schema = PlatformNativeWorkbenchClient.ViewSchema, binding = connection.Binding, page = "data",
                observed_at = 1, availability = "ready", reason = (string?)null, capabilities = new { actions = Array.Empty<object>() },
                cards = Array.Empty<object>(), items = Array.Empty<object>(), pagination = new { total = 0 },
                context = new { external = new { view = "local-workspace", id = new string('f', 64) } } }));
        }));
        using var client = new PlatformNativeWorkbenchClient(http);
        JsonElement view = await client.ViewAsync(connection, "data", 0, new string('f', 64));
        string external = PlatformNativeWorkbenchClient.ExternalUrl(connection, view.GetProperty("context"));
        Assert.Equal(connection.Binding.WorkbenchUrl + "?view=local-workspace&id=" + new string('f', 64), external);
        Assert.DoesNotContain(connection.Token, external);
        Assert.False(PlatformNativeWorkbenchClient.TrustedLoginUrl("https://hub.example", "https://evil.example/app/?view=connect&flow=" + new string('a', 32)));
    }

    [Fact]
    public async Task UncertainMutationSendsOnceAndRenewedPairDoesNotClearOwnerFence()
    {
        int posts = 0;
        using var http = new HttpClient(new Handler((request, _) => { posts++; throw new HttpRequestException("lost response"); }));
        using var client = new PlatformNativeWorkbenchClient(http);
        var commands = new PlatformNativeWorkbenchCommands();
        PlatformNativeWorkbenchConnection connection = Connection();
        var result = await commands.RunAsync(client, connection, "training.start", new { source_id = new string('f', 64) }, CancellationToken.None);
        Assert.Equal("unconfirmed", result.Status); Assert.Equal(1, posts);
        PlatformNativeWorkbenchConnection renewed = Connection(connection.Binding with { PairId = new string('4', 32) });
        Assert.False(commands.CanSubmit(renewed.Binding.ConfigurationId, "training.start"));
        var duplicate = await commands.RunAsync(client, renewed, "training.start", new { }, CancellationToken.None);
        Assert.Equal("rejected", duplicate.Status); Assert.Equal(1, posts);
        Assert.Single(commands.Unconfirmed);
    }

    [Fact]
    public async Task StopIsAnIndependentLaneDuringUnresolvedLoad()
    {
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var order = new List<string>();
        PlatformNativeWorkbenchConnection connection = Connection();
        using var http = new HttpClient(new Handler(async (request, token) => {
            using JsonDocument requestBody = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            string id = requestBody.RootElement.GetProperty("request_id").GetString()!;
            string action = request.RequestUri!.AbsolutePath.Split('/')[^1];
            order.Add(action);
            if (action == "models.takeover") { entered.SetResult(); await release.Task.WaitAsync(token); }
            return Response(Result(connection.Binding, id));
        }));
        using var client = new PlatformNativeWorkbenchClient(http);
        var commands = new PlatformNativeWorkbenchCommands();
        Task<PlatformNativeWorkbenchCommandResult> load = commands.RunAsync(client, connection, "models.takeover", new { }, CancellationToken.None);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(3));
        Assert.True(commands.CanSubmit(connection.Binding.ConfigurationId, "models.stop"));
        var stop = await commands.RunAsync(client, connection, "models.stop", new { }, CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(3));
        Assert.Equal("accepted", stop.Status); Assert.False(load.IsCompleted);
        release.SetResult(); await load;
        Assert.Equal(new[] { "models.takeover", "models.stop" }, order);
    }
    [Fact]
    public async Task LostNativeLoadRetainsKnownRequestForOnlyBoundedExpiredRecovery()
    {
        int posts = 0;
        string? submittedId = null;
        PlatformNativeWorkbenchConnection connection = Connection();
        using var http = new HttpClient(new Handler(async (request, token) => {
            posts++;
            using JsonDocument body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            submittedId = body.RootElement.GetProperty("request_id").GetString();
            throw new HttpRequestException("load reply lost");
        }));
        using var client = new PlatformNativeWorkbenchClient(http);
        var commands = new PlatformNativeWorkbenchCommands();
        await commands.RunAsync(client, connection, "models.takeover", new { selection_id = "fixture" }, CancellationToken.None);
        Assert.Equal(submittedId, commands.SubmittedModelIntent!.RequestId);
        Assert.Equal(connection, commands.SubmittedModelIntent.Connection);
        Assert.Equal(1, posts);

        PlatformNativeWorkbenchConnection expired = Connection(connection.Binding with {
            ExpiresAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() - 1 });
        using var recoveryHttp = new HttpClient(new Handler(async (request, token) => {
            posts++;
            using JsonDocument body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            Assert.Equal(submittedId, body.RootElement.GetProperty("payload").GetProperty("native_request_id").GetString());
            return Response(Result(expired.Binding, body.RootElement.GetProperty("request_id").GetString()!));
        }));
        using var recoveryClient = new PlatformNativeWorkbenchClient(recoveryHttp);
        var refused = await recoveryClient.CommandAsync(expired, "training.start", new { native_request_id = submittedId }, new string('6', 32));
        Assert.Equal("rejected", refused.Status); Assert.Equal(1, posts);
        var recovery = await recoveryClient.CommandAsync(expired, "models.stop", new { native_request_id = submittedId }, new string('7', 32));
        Assert.Equal("accepted", recovery.Status); Assert.Equal(2, posts);
        PlatformNativeWorkbenchConnection overGrace = Connection(expired.Binding with {
            ExpiresAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() - 601 });
        var tooOld = await recoveryClient.CommandAsync(overGrace, "models.stop", new { native_request_id = submittedId }, new string('8', 32));
        Assert.Equal("rejected", tooOld.Status); Assert.Equal(2, posts);
        Assert.Single(commands.Unconfirmed); // Recovery ACK did not invent termination.
    }

    [Theory]
    [InlineData("recordings.refresh", "accepted")]
    [InlineData("recordings.refresh", "rejected")]
    [InlineData("identity.poll", "accepted")]
    [InlineData("identity.poll", "rejected")]
    public async Task UnchangedOwnerAllowsAnotherExplicitRefreshAfterConfirmedReply(
        string action, string outcome)
    {
        PlatformNativeWorkbenchConnection connection = Connection();
        var commands = new PlatformNativeWorkbenchCommands();
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        int posts = 0;
        using var http = new HttpClient(new Handler(async (request, token) => {
            posts++;
            using JsonDocument body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            string id = body.RootElement.GetProperty("request_id").GetString()!;
            if (posts == 1) { entered.SetResult(); await release.Task.WaitAsync(token); }
            return Response(new { schema = PlatformNativeWorkbenchClient.ResultSchema,
                binding = connection.Binding, request_id = id, status = outcome,
                owner_response = outcome == "accepted" ? new { status = "pending" } : null,
                error = outcome == "rejected" ? new { code = "invalid_native_payload" } : null });
        }));
        using var client = new PlatformNativeWorkbenchClient(http);
        Assert.True(commands.CanSubmit(connection.Binding.ConfigurationId, action));
        Task<PlatformNativeWorkbenchCommandResult> first = commands.RunAsync(
            client, connection, action, new { }, CancellationToken.None);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(3));
        Assert.False(commands.CanSubmit(connection.Binding.ConfigurationId, action));
        release.SetResult();
        Assert.Equal(outcome, (await first).Status);
        // The owner reply stays pending and its identity/capabilities did not change.
        // No status refresh or automatic POST is needed to release this local guard.
        Assert.True(commands.CanSubmit(connection.Binding.ConfigurationId, action));
        Assert.Equal(1, posts);
        Assert.Empty(commands.Unconfirmed);
        Assert.Equal(outcome, (await commands.RunAsync(
            client, connection, action, new { }, CancellationToken.None)).Status);
        Assert.Equal(2, posts); // A second explicit user submission, never an automatic retry.
    }

    [Fact]
    public async Task UnconfirmedRecordingRefreshKeepsOwnerDisabledAfterTransportCompletes()
    {
        PlatformNativeWorkbenchConnection connection = Connection();
        var commands = new PlatformNativeWorkbenchCommands();
        int posts = 0;
        using var http = new HttpClient(new Handler((_, _) => {
            posts++; throw new HttpRequestException("refresh reply lost");
        }));
        using var client = new PlatformNativeWorkbenchClient(http);
        Assert.Equal("unconfirmed", (await commands.RunAsync(
            client, connection, "recordings.refresh", new { }, CancellationToken.None)).Status);
        Assert.False(commands.CanSubmit(connection.Binding.ConfigurationId, "recordings.refresh"));
        Assert.Equal("rejected", (await commands.RunAsync(
            client, connection, "recordings.refresh", new { }, CancellationToken.None)).Status);
        Assert.Equal(1, posts);
        Assert.Single(commands.Unconfirmed);
    }

    [Fact]
    public async Task RecordingUnknownIsScopedToOriginalRuntimeSessionAndPreservedAfterRenewal()
    {
        var connection = Connection(); var commands = new PlatformNativeWorkbenchCommands(); int posts = 0;
        using var http = new HttpClient(new Handler((_, _) => { posts++; throw new HttpRequestException("reply lost"); }));
        using var client = new PlatformNativeWorkbenchClient(http);
        var payload = new { runtime_instance_id = "game-1", recording_session_id = "recording-1" };
        var result = await commands.RunAsync(client, connection, "recording.pause", payload, CancellationToken.None);
        Assert.Equal("unconfirmed", result.Status);
        string original = PlatformNativeWorkbenchCommands.RecordingContext("game-1", "recording-1");
        var renewed = Connection(connection.Binding with { PairId = new string('4', 32) });
        Assert.False(commands.CanSubmit(renewed.Binding.ConfigurationId, "recording.pause", original));
        Assert.True(commands.CanSubmit(renewed.Binding.ConfigurationId, "recording.close", original));
        Assert.True(commands.CanSubmit(renewed.Binding.ConfigurationId, "recording.start", original));
        Assert.Equal("rejected", (await commands.RunAsync(client, renewed, "recording.pause", payload, CancellationToken.None)).Status);
        Assert.Equal(1, posts); Assert.Single(commands.Unconfirmed);
        // A fresh session may proceed; the server still requires the deliberate known-Closed Start.
        Assert.True(commands.CanSubmit(renewed.Binding.ConfigurationId, "recording.pause",
            PlatformNativeWorkbenchCommands.RecordingContext("game-1", "new-isolated-session")));
        Assert.Single(commands.Unconfirmed);
    }

}
