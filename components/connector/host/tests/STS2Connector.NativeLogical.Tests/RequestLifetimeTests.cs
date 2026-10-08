using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class RequestLifetimeTests
{
    private readonly Xunit.Abstractions.ITestOutputHelper output;
    public RequestLifetimeTests(Xunit.Abstractions.ITestOutputHelper output) { this.output = output; }
    private static readonly JsonSerializerOptions Legacy = new()
    {
        WriteIndented = true, DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true, Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };
    private static NativeLogicalResult Result(PlayerEnvironmentActionRequest? request = null,
        string delivery = "not_started", string? reason = "fixture", NativeLogicalAction? action = null) => new(
        "1.0.0", NativeLogicalContract.ResultSchema, NativeLogicalContract.Profile,
        request?.RequestId ?? "request", request?.ExpectedSnapshotId ?? "snapshot", action,
        delivery, "unknown", "unknown", "unknown", Array.Empty<NativeLogicalInputStage>(), reason,
        "never_automatic", null, null);
    private static byte[] Copy(RequestNamespace.TerminalReply reply)
    { using (reply) { using var stream = new MemoryStream(); reply.WriteTo(stream); return stream.ToArray(); } }
    private static byte[] Encode(object result, bool native, RequestResultArena? arena = null,
        int maximum = 2 * 1024 * 1024)
    {
        arena ??= new RequestResultArena(8 * 1024 * 1024);
        using var reservation = arena.TryReserve(4 * 1024 * 1024, maximum)!;
        var codec = new TerminalPayloadCodec(); Assert.True(codec.Supported, codec.FrameworkIdentity);
        codec.Encode(result, reservation, native);
        var frozen = reservation.Freeze();
        using var loan = frozen.Borrow(); using var stream = new MemoryStream(); loan.WriteTo(stream);
        frozen.Retire(); return stream.ToArray();
    }
    private sealed class Fixture : IDisposable
    {
        internal long Now;
        internal readonly MutationControllerCoordinator Authority;
        internal readonly MutationClientRegistrationResult Client;
        internal readonly MutationLease Lease;
        internal readonly RequestNamespace Requests;
        internal int Notices;
        internal Fixture(int maximumIds = 4, int maximumSenders = 4, int retention = 100,
            bool timer = false, TerminalPayloadCodec? codec = null)
        {
            Authority = new("runtime", monotonicClock: () => Now, enableDeadlineTimer: false);
            Client = Authority.Register(new("instance", "test", "Test", "1"));
            Lease = Authority.Acquire(new(Client.Client.ClientSessionId, null, null)).Controller!;
            Requests = new("runtime", Authority.ValidateActiveControl, Authority.TryAdmitRequest,
                Authority.TryBegin, (_, _) => Notices++, arena: new(16 * 1024 * 1024), codec: codec,
                clock: timer ? null : () => Now, maximumIds: maximumIds, maximumSenders: maximumSenders,
                retentionMs: retention, enableTimer: timer);
            Authority.ClientClosed += Requests.ClientClosed;
        }
        internal PlayerEnvironmentActionRequest Request(string id, string? profile = NativeLogicalContract.Profile) =>
            new(id, "snapshot", "action", Client.Client.ClientSessionId, Lease.ControllerLeaseId,
                Lease.ControllerGeneration, profile);
        internal void Seal(PlayerEnvironmentActionRequest request, string delivery = "not_started")
        { using var prep = Requests.PrepareOriginal(request)!; prep.Seal(Result(request, delivery)); }
        public void Dispose() { Requests.Dispose(); }
    }
    [Fact]
    public void ClosedFrameworkAbiMatchesOriginalNativeEscapingBytesAndFailsUnsupportedBeforeIdAdmission()
    {
        output.WriteLine(new TerminalPayloadCodec().FrameworkIdentity);
        var action = new NativeLogicalAction("action", "native_input", "inspect", "甲\"\\\n\u0000😀\u2028",
            null, Array.Empty<NativeLogicalArgument>(), "native_ui");
        var result = Result(reason: "\u0000\t\b\f\r\n\"\\<>&é中😀", action: action);
        Assert.Equal(NativeLogicalWire.Encode(result), Encode(result, true));
        using var fixture = new Fixture(codec: new(new UnsupportedRaw()));
        var rejected = fixture.Requests.Admit(fixture.Request("abi"));
        Assert.Equal("result_encoding_unsupported", rejected.Reason);
        Assert.Equal(0, fixture.Requests.SpentIdCount); Assert.Equal(0, fixture.Requests.Arena.AllocatedCapacityBytes);
    }
    private sealed class UnsupportedRaw : IResultJsonElementRawView
    {
        public bool Supported => false;
        public string FrameworkIdentity => "unsupported-test-framework";
        public ReadOnlyMemory<byte> Borrow(JsonElement element) => throw new InvalidOperationException("No fallback may run.");
    }
    [Fact]
    public void WholeLegacySuccessorHasOriginalNullDateNestedPropertyAndRawNumericBytes()
    {
        JsonNode parsed = JsonNode.Parse("{\"\\u4E2D\\n\\\"\": [1.00e+2,-0.000,\"\\uD83D\\uDE00\\n\",true,null,{\"quote\\\"\":\"x\\/y\"}]}")!;
        var snapshot = new TextMenuV2Snapshot("1.0.0", TextMenuV2Contract.SnapshotSchema, TextMenuV2Contract.Profile,
            "snapshot", 7, DateTimeOffset.Parse("2026-10-08T11:12:13.1234000+10:00"), "interactive", null,
            new("i", "selector", "ready", null, "surface", new(parsed, new JsonObject()), Array.Empty<PlayerEnvironmentInteractionCapability>()),
            Array.Empty<PlayerEnvironmentReferent>(), new("complete", "complete", "complete", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "env"), new("player_visible_v1", "current_page", false, "omit"),
            new("root", 1, "source", Array.Empty<TextMenuV2Selection>()), new("complete", 0, 0, "native", Array.Empty<TextMenuAction>()));
        var result = new TextMenuV2ActionResult("1.0.0", TextMenuV2Contract.ResultSchema, TextMenuV2Contract.Profile,
            "r", "applied", "text_menu", null, null, null, "selection", "never", snapshot, null);
        Assert.Equal(JsonSerializer.SerializeToUtf8Bytes(result, Legacy), Encode(result, false));
        parsed.AsObject()["fresh-number"] = JsonValue.Create(1.25f);
        parsed.AsObject()["fresh-double"] = JsonValue.Create(1.2345678901234567);
        parsed.AsObject()["fresh-decimal"] = JsonValue.Create(1.2300m);
        Assert.Equal(JsonSerializer.SerializeToUtf8Bytes(result, Legacy), Encode(result, false));
    }
    [Fact]
    public void MaximumControlEscapesFitDeclaredNativeCoreAndExactEnvelopeBoundaryClosesWithoutExtraAllocation()
    {
        string controls = new('\0', 65_536);
        var stages = Enumerable.Range(0, 16).Select(_ => new NativeLogicalInputStage(new('\0', 128),
            "partially_delivered", new('\0', 128))).ToArray();
        var maximum = Result(delivery: "partially_delivered", reason: controls) with { Stages = stages };
        Assert.Equal(NativeLogicalWire.Encode(maximum), Encode(maximum, true));
        var arena = new RequestResultArena(512, 64);
        using var reservation = arena.TryReserve(256, 128)!;
        reservation.Append(new byte[128]);
        Assert.Throws<ResultPayloadCapacityException>(() => reservation.Append((byte)0));
        Assert.Equal(256, arena.ChargedBytes); Assert.Equal(256, arena.AllocatedCapacityBytes);
        var frozen = reservation.Freeze(); Assert.Equal(128, arena.ChargedBytes);
        frozen.Retire(); Assert.Equal(0, arena.ChargedBytes); Assert.Equal(256, arena.AllocatedCapacityBytes);
    }
    [Fact]
    public void ExpiredPayloadRemainsChargedUntilEveryExistingLoanReleasesAndBlocksAreReused()
    {
        int allocations = 0;
        var arena = new RequestResultArena(512, 64, length => { allocations++; return new byte[length]; });
        using var first = arena.TryReserve(256, 128)!; first.Append(new byte[70]);
        var frozen = first.Freeze(); using var loan = frozen.Borrow(); frozen.Retire();
        Assert.Equal(128, arena.ChargedBytes); Assert.Throws<ObjectDisposedException>(() => frozen.Borrow());
        using var stream = new MemoryStream(); loan.WriteTo(stream); Assert.Equal(70, stream.Length);
        loan.Dispose(); Assert.Equal(0, arena.ChargedBytes);
        using var second = arena.TryReserve(256, 128)!;
        Assert.Equal(4, allocations); Assert.Equal(256, arena.AllocatedCapacityBytes);
    }
    [Fact]
    public void MandatoryPreparedResultRetainsAllSpareBytesUntilCommitAndCanDiscardWithoutQuotaReacquisition()
    {
        var arena = new RequestResultArena(256, 64);
        using var reservation = arena.TryReserve(256, 128)!; reservation.Append("required"u8);
        using var staged = reservation.Prepare();
        Assert.Equal(256, arena.ChargedBytes); Assert.Null(arena.TryReserve(128, 64));
        Assert.Throws<InvalidOperationException>(() => reservation.Append((byte)'x'));
        staged.Dispose(); reservation.ResetEncoding(); reservation.Append("rejected"u8);
        var frozen = reservation.Freeze(); using var loan = frozen.Borrow();
        using var stream = new MemoryStream(); loan.WriteTo(stream); Assert.Equal("rejected", Encoding.UTF8.GetString(stream.ToArray()));
        frozen.Retire(); Assert.Equal(64, arena.ChargedBytes); loan.Dispose(); Assert.Equal(0, arena.ChargedBytes);
    }
    [Fact]
    public async Task DuplicatesReplayOriginalFrozenBytesAcrossAllProfilesWithoutNewTouchAllocationOrDispatch()
    {
        using var f = new Fixture(); var request = f.Request("original");
        var original = f.Requests.Admit(request); Assert.Equal("admitted", original.Status);
        long capacity = f.Requests.Arena.AllocatedCapacityBytes;
        Assert.Equal("pending", f.Requests.Admit(request).Status);
        Assert.Equal("conflict", f.Requests.Admit(request with { InputProfile = TextMenuV2Contract.Profile }).Status);
        f.Seal(request); var expected = Copy(await original.OriginalCompletion!);
        Assert.Equal(expected, Copy(f.Requests.Admit(request).Reply!));
        Assert.Equal(expected, Copy(f.Requests.Find(request.RequestId!, request.InputProfile).Reply!));
        Assert.Equal(capacity, f.Requests.Arena.AllocatedCapacityBytes); Assert.Equal(1, f.Requests.SpentIdCount);
        Assert.Equal(1, f.Notices); Assert.Equal(0, f.Requests.SenderCount);
    }
    [Fact]
    public async Task TerminalExpiryNeverRevivesAndClientRenewalDoesNotExtendPayloadRetention()
    {
        using var f = new Fixture(); var request = f.Request("expired"); var original = f.Requests.Admit(request);
        f.Seal(request); byte[] expected = Copy(await original.OriginalCompletion!);
        f.Now = 99; f.Authority.Renew(new(f.Client.Client.ClientSessionId, f.Lease.ControllerLeaseId, f.Lease.ControllerGeneration));
        Assert.Equal(expected, Copy(f.Requests.Find("expired", NativeLogicalContract.Profile).Reply!));
        f.Now = 100; f.Requests.Tick();
        Assert.Equal("expired", f.Requests.Find("expired", NativeLogicalContract.Profile).Status);
        Assert.Equal("expired", f.Requests.Admit(request).Status);
        Assert.Equal("conflict", f.Requests.Admit(request with { BoundActionId = "different" }).Status);
        Assert.Equal(1, f.Requests.SpentIdCount); Assert.Equal(0, f.Requests.Arena.ChargedBytes);
    }
    [Fact]
    public async Task TerminalTimerRetiresWithoutAnotherLookupOrTick()
    {
        using var f = new Fixture(retention: 20, timer: true); var request = f.Request("timer");
        var original = f.Requests.Admit(request); f.Seal(request); Copy(await original.OriginalCompletion!);
        for (int i = 0; i < 100 && f.Requests.Arena.ChargedBytes > 0; i++) await Task.Delay(5);
        Assert.Equal(0, f.Requests.Arena.ChargedBytes); Assert.Equal("expired", f.Requests.Find("timer", request.InputProfile).Status);
    }
    [Fact]
    public async Task QuotaDenialAndClosedClientCannotSpendNewIdOrEvictPendingButMatchingDuplicateSurvivesClosure()
    {
        using var f = new Fixture(maximumIds: 1); var request = f.Request("pending"); var original = f.Requests.Admit(request);
        Assert.Equal("request_capacity_exceeded", f.Requests.Admit(f.Request("other")).Reason);
        Assert.Equal(1, f.Requests.SpentIdCount); Assert.Equal("pending", f.Requests.Find("pending", request.InputProfile).Status);
        Assert.True(f.Requests.CancelQueued("pending", "client_revoked"));
        var bytes = Copy(await original.OriginalCompletion!); var result = NativeLogicalDecoder.Decode<NativeLogicalResult>(bytes);
        Assert.Equal("not_started", result.Delivery); Assert.Equal("client_revoked", result.Reason);
        f.Authority.Revoke(new("runtime", f.Client.Client.ClientSessionId));
        Assert.Equal("client_session_not_found", f.Requests.Admit(f.Request("new")).Reason);
        Assert.Equal(1, f.Requests.SpentIdCount);
    }
    [Fact]
    public async Task StartedOriginalPreservesActualOutcomeAfterCloseAndOnlyItsExistingSenderMayBorrowClosedTerminal()
    {
        using var f = new Fixture(); var request = f.Request("started"); var original = f.Requests.Admit(request);
        using var prepared = f.Requests.PrepareOriginal(request)!;
        Assert.True(prepared.TryBegin().Accepted); prepared.ReleasePreparation();
        f.Authority.Revoke(new("runtime", f.Client.Client.ClientSessionId));
        Assert.False(f.Requests.CancelQueued("started", "client_revoked"));
        Assert.Equal("pending", f.Requests.Find("started", request.InputProfile).Status);
        prepared.Seal(Result(request, "delivered"));
        byte[] actual = Copy(await original.OriginalCompletion!);
        Assert.Equal("delivered", NativeLogicalDecoder.Decode<NativeLogicalResult>(actual).Delivery);
        Assert.Equal("expired", f.Requests.Find("started", request.InputProfile).Status);
        Assert.Equal(1, f.Notices); Assert.Equal(0, f.Requests.Arena.ChargedBytes);
    }
    [Fact]
    public void ClosureWinningAfterEligibilityButBeforeReservedAdmissionSpendsNoIdAndReturnsEveryActiveBlock()
    {
        var authority = new MutationControllerCoordinator("runtime", enableDeadlineTimer: false);
        var client = authority.Register(new("instance", "test", "Test", "1")).Client;
        var lease = authority.Acquire(new(client.ClientSessionId, null, null)).Controller!;
        bool closed = false;
        var arena = new RequestResultArena(8 * 1024 * 1024, allocator: length =>
        {
            if (!closed) { closed = true; Assert.True(authority.Revoke(new("runtime", client.ClientSessionId)).Closed); }
            return new byte[length];
        });
        using var requests = new RequestNamespace("runtime", authority.ValidateActiveControl, authority.TryAdmitRequest,
            authority.TryBegin, arena: arena, enableTimer: false);
        var rejected = requests.Admit(new("close-before-admit", "snapshot", "action", client.ClientSessionId,
            lease.ControllerLeaseId, lease.ControllerGeneration, NativeLogicalContract.Profile));
        Assert.Equal("client_session_not_found", rejected.Reason); Assert.Equal(0, requests.SpentIdCount);
        Assert.Equal(0, requests.SenderCount); Assert.Equal(0, arena.ChargedBytes);
        Assert.Equal("not_found", requests.Find("close-before-admit", NativeLogicalContract.Profile).Status);
    }
    [Fact]
    public async Task ClosureWinningJustAfterAdmissionStillSpendsOriginalIdAndTimerCallbackSealsQueuedWithoutWorkerOrLookup()
    {
        var authority = new MutationControllerCoordinator("runtime", enableDeadlineTimer: false);
        var client = authority.Register(new("instance", "test", "Test", "1")).Client;
        var lease = authority.Acquire(new(client.ClientSessionId, null, null)).Controller!;
        MutationRequestAdmission AdmitAndClose(MutationAuthorizationRequest request)
        {
            var accepted = authority.TryAdmitRequest(request); Assert.True(accepted.Accepted);
            Assert.True(authority.Revoke(new("runtime", client.ClientSessionId)).Closed);
            return accepted;
        }
        using var requests = new RequestNamespace("runtime", authority.ValidateActiveControl, AdmitAndClose,
            authority.TryBegin, enableTimer: false);
        authority.ClientClosed += requests.ClientClosed;
        var request = new PlayerEnvironmentActionRequest("close-after-admit", "snapshot", "action", client.ClientSessionId,
            lease.ControllerLeaseId, lease.ControllerGeneration, NativeLogicalContract.Profile);
        var original = requests.Admit(request); Assert.Equal("admitted", original.Status);
        var bytes = Copy(await original.OriginalCompletion!.WaitAsync(TimeSpan.FromSeconds(2)));
        var result = NativeLogicalDecoder.Decode<NativeLogicalResult>(bytes);
        Assert.Equal("not_started", result.Delivery); Assert.Equal("client_session_revoked", result.Reason);
        Assert.Equal(1, requests.SpentIdCount); Assert.Equal("expired", requests.Admit(request).Status);
        Assert.Equal(0, requests.Arena.ChargedBytes); Assert.Equal(0, requests.SenderCount);
    }
    [Fact]
    public async Task ClientClosureCancelsActualUnstartedQueueAndOriginalHttpWaitWithoutAnyDrain()
    {
        using var f = new Fixture();
        var queue = new STS2Connector.MainThreadWorkQueue();
        var request = f.Request("no-drain");
        var admission = f.Requests.Admit(request);
        int dispatches = 0;
        var queued = queue.Enqueue(() => { dispatches++; return true; }, admission.QueueCancellation);
        Assert.Equal(1, queue.PendingCount);
        f.Authority.Revoke(new("runtime", f.Client.Client.ClientSessionId));
        await Assert.ThrowsAnyAsync<OperationCanceledException>(async () => await queued);
        Assert.Equal(0, queue.PendingCount); Assert.Equal(0, dispatches);
        var terminal = await admission.OriginalCompletion!;
        Assert.Equal("not_started", NativeLogicalDecoder.Decode<NativeLogicalResult>(Copy(terminal)).Delivery);
        Assert.Equal(0, f.Requests.SenderCount);
        Assert.Equal(0, f.Requests.Arena.ChargedBytes);
        Assert.Equal("expired", f.Requests.Find(request.RequestId!, NativeLogicalContract.Profile).Status);
    }
    [Fact]
    public async Task StartedActualQueueKeepsOriginalNativeResultWhenClientClosesMidCallback()
    {
        using var f = new Fixture();
        var queue = new STS2Connector.MainThreadWorkQueue();
        var request = f.Request("started-queue");
        var admission = f.Requests.Admit(request);
        var started = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var finish = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var queued = queue.Enqueue(() =>
        {
            using var preparation = f.Requests.PrepareOriginal(request)!;
            Assert.True(preparation.TryBegin().Accepted);
            preparation.ReleasePreparation(); started.SetResult();
            finish.Task.GetAwaiter().GetResult();
            preparation.Seal(Result(request, "delivered", null)); return true;
        }, admission.QueueCancellation);
        var drain = Task.Run(() => queue.Drain(1));
        await started.Task;
        f.Authority.Revoke(new("runtime", f.Client.Client.ClientSessionId));
        Assert.False(queued.IsCompleted); Assert.False(admission.OriginalCompletion!.IsCompleted);
        finish.SetResult(); Assert.True(await queued); Assert.Equal(1, await drain);
        Assert.Equal("delivered", NativeLogicalDecoder.Decode<NativeLogicalResult>(Copy(await admission.OriginalCompletion)).Delivery);
        Assert.Equal("expired", f.Requests.Find(request.RequestId!, NativeLogicalContract.Profile).Status);
    }
    [Fact]
    public async Task SourceTerminalObserverRunsOnceOnlyAfterPreparationAndNamespaceGatesRelease()
    {
        using var f = new Fixture();
        RequestNamespace? requests = null; int observed = 0;
        var request = f.Request("observer");
        requests = new("runtime", f.Authority.ValidateActiveControl, f.Authority.TryAdmitRequest,
            f.Authority.TryBegin, (originalRequest, result) =>
            {
                observed++;
                Task.Run(() => { var replay = requests!.Find("observer", NativeLogicalContract.Profile); replay.Reply!.Dispose();
                    _ = f.Authority.Snapshot(); }).WaitAsync(TimeSpan.FromSeconds(1)).GetAwaiter().GetResult();
                Assert.Equal("not_started", result.Delivery);
            }, enableTimer: false);
        using (requests)
        {
            var original = requests.Admit(request);
            using var preparation = requests.PrepareOriginal(request)!;
            preparation.Seal(Result(request)); Assert.Equal(0, observed);
            preparation.ReleasePreparation(); Assert.Equal(1, observed);
            Copy(await original.OriginalCompletion!);
            Copy(requests.Admit(request).Reply!); Assert.Equal(1, observed);
        }
    }
    [Fact]
    public async Task DisposingAnInFlightSenderWaitsForItsActualWriteAndRetiredBytesCannotRecycleDuringIo()
    {
        var arena = new RequestResultArena(512, 64);
        using var reservation = arena.TryReserve(256, 128)!; reservation.Append(Enumerable.Repeat((byte)7, 70).ToArray());
        var frozen = reservation.Freeze(); var loan = frozen.Borrow();
        using var entered = new ManualResetEventSlim(); using var released = new ManualResetEventSlim();
        using var stream = new BlockingWriteStream(entered, released);
        var send = Task.Run(() => loan.WriteTo(stream)); Assert.True(entered.Wait(2000));
        frozen.Retire(); Assert.Equal(128, arena.ChargedBytes);
        var dispose = Task.Run(loan.Dispose);
        Assert.NotSame(dispose, await Task.WhenAny(dispose, Task.Delay(20)));
        Assert.Equal(128, arena.ChargedBytes);
        released.Set(); await send.WaitAsync(TimeSpan.FromSeconds(2)); await dispose.WaitAsync(TimeSpan.FromSeconds(2));
        Assert.Equal(70, stream.Length); Assert.All(stream.ToArray(), value => Assert.Equal(7, value));
        Assert.Equal(0, arena.ChargedBytes);
    }
    private sealed class BlockingWriteStream(ManualResetEventSlim entered, ManualResetEventSlim released) : MemoryStream
    {
        public override void Write(byte[] buffer, int offset, int count)
        { entered.Set(); if (!released.Wait(2000)) throw new TimeoutException(); base.Write(buffer, offset, count); }
        public override void Write(ReadOnlySpan<byte> bytes)
        { entered.Set(); if (!released.Wait(2000)) throw new TimeoutException(); base.Write(bytes); }
    }

}
