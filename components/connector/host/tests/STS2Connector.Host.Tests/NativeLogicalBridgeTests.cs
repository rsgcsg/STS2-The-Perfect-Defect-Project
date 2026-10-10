using System.Collections.Concurrent;
using System.Text;
using System.Text.Json.Nodes;
using STS2Connector.Authority;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

using NativeLogicalCapture = global::STS2Connector.PlayerEnvironment.NativeLogicalCapture;

public sealed class NativeLogicalBridgeTests
{
    private sealed class Fixture : IDisposable
    {
        internal TextMenuFrame Frame;
        internal int Captures, Dispatches;
        internal NativeInputResult Outcome = NativeInputResult.Delivered("legacy_acceptance");
        internal Action? OnDispatch, OnPrepared, OnCapture;
        internal readonly MutationControllerCoordinator Controller;
        internal readonly MutationClientRegistrationResult Client;
        internal readonly MutationLease Lease;
        internal readonly string ReaderId;
        internal readonly MainThreadWorkQueue Queue = new();
        internal readonly RequestNamespace Requests;
        internal readonly NativeLogicalService Owner;
        internal Fixture(Func<long>? monotonicClock = null, int clientIdleMs = 30 * 60 * 1000,
            NativeLogicalRevalidationDiagnostics? diagnostics = null)
        {
            Controller = new("runtime", monotonicClock: monotonicClock,
                clientIdleTtlMs: clientIdleMs, enableDeadlineTimer: false);
            ReaderId = Controller.Register(new("bridge-reader", "test", "Bridge reader", "1")).Client.ClientSessionId;
            Client = Controller.Register(new("bridge-test", "test", "Bridge test", "1"));
            Lease = Controller.Acquire(new(Client.Client.ClientSessionId, null, null)).Controller!;
            Frame = MakeFrame(() => { Dispatches++; OnDispatch?.Invoke(); return Outcome; });
            Requests = new("runtime", Controller.ValidateActiveControl, Controller.TryAdmitRequest, Controller.TryBegin,
                enableTimer: false);
            Controller.ClientClosed += Requests.ClientClosed;
            Owner = new(() => { Captures++; OnCapture?.Invoke(); return Frame; }, () => "run", Requests,
                (work, cancellation) => Queue.Enqueue(() => { var value = work(); OnPrepared?.Invoke(); return value; }, cancellation), () => true, clientActive: Controller.IsActiveClient, revalidationDiagnostics: diagnostics);
            Owner.Initialize();
        }
        internal NativeLogicalCurrentReply Current()
        {
            var task = Owner.CurrentAsync(new(ReaderId, NativeLogicalProjector.ScopeFields, null));
            Queue.Drain(1);
            return task.GetAwaiter().GetResult();
        }
        internal NativeLogicalResult Submit(PlayerEnvironmentActionRequest request) =>
            RequestTestDriver.Submit<NativeLogicalResult>(Requests, request, Owner.RunAdmitted);
        internal bool IsPending(string id) { var result = Requests.Find(id, NativeLogicalContract.Profile); result.Reply?.Dispose(); return result.Status == "pending"; }
        internal NativeLogicalResult? Find(string id)
        { var result = Requests.Find(id, NativeLogicalContract.Profile); return result.Reply is { } bytes ? RequestTestDriver.Decode<NativeLogicalResult>(bytes) : null; }
        public void Dispose() { Owner.Dispose(); Requests.Dispose(); }
    }
    [Fact]
    public async Task NoConsumerSourceNoticesKeepTheirClockWithoutCapturingOrEncodingFrames()
    {
        using var fixture = new Fixture();
        fixture.Owner.Publish("native_target_focus", "no_consumer");
        fixture.Owner.Publish("native_card_preview", "no_consumer");
        Assert.Equal(0, fixture.Captures); Assert.Equal(0, fixture.Queue.PendingCount);
        var attached = fixture.Owner.Attach(new(fixture.ReaderId, NativeLogicalProjector.ScopeFields,
            new[] { NativeLogicalService.Coverage[0] }, "full_reference"));
        Assert.Equal(1, fixture.Captures);
        var sub = attached.Subscription!;
        var next = await fixture.Owner.Hub.AwaitAsync(fixture.ReaderId, sub.SubscriptionId, sub.ScopeId,
            sub.StartingCursor, "00000000000000000000000000000003", "any_event", 2000);
        Assert.Equal("event", next.Status); Assert.Equal("3", next.Event!.Event.PublicationIndex);
        Assert.NotNull(next.Event.Event.CaptureRef);
    }
    [Fact]
    public void RealCompositionCapturesThenSealsOffThreadAndRevalidatesCurrentHandle()
    {
        using var fixture = new Fixture();
        var first = fixture.Current(); var second = fixture.Current();
        Assert.Equal("captured", first.Status);
        Assert.Equal(first.Capture!.SnapshotId, second.Capture!.SnapshotId);
        var catalog = fixture.Owner.Store.Catalog(first.Capture.CaptureId);
        var action = Assert.Single(catalog.Actions);
        var leaf = fixture.Owner.Revalidate(first.Capture.SnapshotId, action.ActionId);
        Assert.NotNull(leaf); Assert.Equal(0, fixture.Dispatches);
        fixture.Frame = fixture.Frame with { Page = fixture.Frame.Page with { Interaction = fixture.Frame.Page.Interaction with
        { Content = new(new JsonObject { ["kind"] = "held", ["preview"] = "changed" }, new JsonObject()) } } };
        Assert.Null(fixture.Owner.Revalidate(first.Capture.SnapshotId, action.ActionId));
        Assert.Equal(0, fixture.Dispatches);
    }
    [Theory]
    [InlineData("snapshot", "snapshot_mismatch", "stale_snapshot_or_binding")]
    [InlineData("action", "action_unknown", "stale_snapshot_or_binding")]
    [InlineData("facts", "public_facts_changed", "stale_snapshot_or_binding")]
    [InlineData("partial", "public_facts_changed", "stale_snapshot_or_binding")]
    [InlineData("capture_exception", "revalidation_exception", "native_revalidation_failed")]
    public void OriginalRejectionLogsExactGateWithOneCaptureAndUnchangedTerminal(string mode, string gate, string reason)
    {
        var lines = new List<string>();
        using var fixture = new Fixture(diagnostics: new(true, lines.Add));
        var current = fixture.Current();
        var action = Assert.Single(fixture.Owner.Store.Catalog(current.Capture!.CaptureId).Actions);
        var request = new PlayerEnvironmentActionRequest("diagnostic-original", current.Capture.SnapshotId, action.ActionId,
            fixture.Client.Client.ClientSessionId, fixture.Lease.ControllerLeaseId, fixture.Lease.ControllerGeneration, NativeLogicalContract.Profile);
        if (mode == "snapshot") request = request with { ExpectedSnapshotId = "old-snapshot" };
        if (mode == "action") request = request with { BoundActionId = "unknown-action" };
        if (mode == "facts") fixture.Frame = fixture.Frame with { Page = fixture.Frame.Page with
            { Interaction = fixture.Frame.Page.Interaction with { Prompt = "new public prompt" } } };
        if (mode == "partial") fixture.Frame = fixture.Frame with { Page = fixture.Frame.Page with
            { Completeness = fixture.Frame.Page.Completeness with { Status = "partial", Missing = new[] { "missing-public-field" } } } };
        if (mode == "capture_exception") fixture.OnCapture = () => throw new InvalidOperationException("private exception canary");
        int captures = fixture.Captures;
        var first = fixture.Submit(request);
        Assert.Equal("not_started", first.Delivery); Assert.Equal(reason, first.Reason); Assert.Null(first.Action);
        Assert.Equal(0, fixture.Dispatches); Assert.Equal(captures + 1, fixture.Captures);
        RequestTestDriver.AssertWireEqual(first, fixture.Submit(request));
        Assert.Equal(captures + 1, fixture.Captures);
        using var doc = System.Text.Json.JsonDocument.Parse(Assert.Single(lines)); var row = doc.RootElement;
        Assert.Equal(gate, row.GetProperty("rejection_gate").GetString());
        Assert.Equal(request.RequestId, row.GetProperty("request_id").GetString());
        Assert.Equal(request.ExpectedSnapshotId, row.GetProperty("expected_snapshot_id").GetString());
        Assert.Equal(request.BoundActionId, row.GetProperty("action_id").GetString());
        Assert.DoesNotContain("private exception", lines[0]);
        if (mode == "partial") Assert.Contains("source_completeness",
            row.GetProperty("changed_fact_groups").EnumerateArray().Select(x => x.GetString()));
    }
    [Fact]
    public void DiagnosticSinkFailureCannotChangeRejectionOrLaterDispatch()
    {
        int logs = 0;
        using var fixture = new Fixture(diagnostics: new(true, _ => { logs++; throw new IOException("log unavailable"); }));
        var current = fixture.Current();
        var action = Assert.Single(fixture.Owner.Store.Catalog(current.Capture!.CaptureId).Actions);
        var rejected = fixture.Submit(new("rejected", "old-snapshot", action.ActionId,
            fixture.Client.Client.ClientSessionId, fixture.Lease.ControllerLeaseId, fixture.Lease.ControllerGeneration, NativeLogicalContract.Profile));
        Assert.Equal("stale_snapshot_or_binding", rejected.Reason); Assert.Equal("not_started", rejected.Delivery);
        Assert.Equal(0, fixture.Dispatches); Assert.Equal(1, logs);
        var delivered = fixture.Submit(new("fresh", current.Capture.SnapshotId, action.ActionId,
            fixture.Client.Client.ClientSessionId, fixture.Lease.ControllerLeaseId, fixture.Lease.ControllerGeneration, NativeLogicalContract.Profile));
        Assert.Equal("delivered", delivered.Delivery); Assert.Equal(1, fixture.Dispatches); Assert.Equal(1, logs);
    }
    [Fact]
    public void LaterSourceCallbackCannotEncodeAheadOfAnOlderCapturedCurrent()
    {
        using var fixture = new Fixture();
        var attached = fixture.Owner.Attach(new(fixture.ReaderId, NativeLogicalProjector.ScopeFields,
            new[] { NativeLogicalService.Coverage[0] }, "full_reference"));
        var sub = attached.Subscription!;
        fixture.OnPrepared = () =>
        {
            fixture.Frame = fixture.Frame with { OwnerKey = "B" };
            fixture.Owner.Publish("native_target_focus", "later_source");
        };
        _ = fixture.Current();
        fixture.OnPrepared = null;
        var currentB = fixture.Current();
        var batch = fixture.Owner.Hub.Events(fixture.ReaderId, sub.SubscriptionId, sub.ScopeId, sub.StartingCursor);
        var sourceB = Assert.Single(batch.Events, e => e.Event.SourceSeam == "native_target_focus");
        Assert.Equal(currentB.Capture!.SnapshotId, sourceB.Event.PayloadReference!.SnapshotId);
    }
    [Fact]
    public void DetachedNativeFactsCannotBeChangedBeforeTheBackgroundEncoderRuns()
    {
        using var fixture = new Fixture();
        fixture.OnPrepared = () => fixture.Frame.Page.Interaction.Content.Surface["kind"] = "changed_after_native_seam";
        var current = fixture.Current();
        var frozen = fixture.Owner.Store.ExportFrozen(current.Capture!.CaptureId);
        var observation = NativeLogicalDecoder.Decode<NativeLogicalObservation>(frozen.CopyCaptureBytes());
        Assert.Equal("held", observation.Interaction!.Content.Surface["kind"]!.GetValue<string>());
        Assert.Equal("changed_after_native_seam", fixture.Frame.Page.Interaction.Content.Surface["kind"]!.GetValue<string>());
    }
    [Fact]
    public void EncodingAdmissionFailureAccountsInitialPositionWithoutCapturingOrHidingIt()
    {
        using var fixture = new Fixture(); using var cancellation = new CancellationTokenSource();
        var queued = Enumerable.Range(0, 4).Select(_ => fixture.Owner.CurrentAsync(
            new(fixture.ReaderId, NativeLogicalProjector.ScopeFields, null), cancellation.Token)).ToArray();
        Assert.Equal(4, fixture.Queue.PendingCount);
        var reply = fixture.Owner.Attach(new(fixture.ReaderId, NativeLogicalProjector.ScopeFields,
            new[] { NativeLogicalService.Coverage[0] }, "full_reference"));
        var subscription = reply.Subscription!;
        var batch = fixture.Owner.Hub.Events(fixture.ReaderId, subscription.SubscriptionId, subscription.ScopeId, subscription.StartingCursor);
        var missing = Assert.Single(batch.Events).Event;
        Assert.Equal("1", missing.PublicationIndex); Assert.Equal("encoding_capacity_exceeded", missing.MissingReason);
        Assert.Null(missing.CaptureRef); Assert.Equal(0, fixture.Captures);
        cancellation.Cancel();
        foreach (var task in queued) Assert.ThrowsAny<OperationCanceledException>(() => task.GetAwaiter().GetResult());
        Assert.Equal(0, fixture.Queue.PendingCount);
    }
    [Fact]
    public void ExactGameSourceHookSignaturesRemainTypedAndPresent()
    {
        const System.Reflection.BindingFlags flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
        var methods = new[]
        {
            typeof(MegaCrit.Sts2.Core.Nodes.Combat.NTargetManager).GetMethod("OnNodeHovered", flags, new[] { typeof(Godot.Node) }),
            typeof(MegaCrit.Sts2.Core.Nodes.Combat.NTargetManager).GetMethod("OnNodeUnhovered", flags, new[] { typeof(Godot.Node) }),
            typeof(MegaCrit.Sts2.Core.Nodes.Combat.NPlayerHand).GetMethod("OnHolderPressed", flags, new[] { typeof(MegaCrit.Sts2.Core.Nodes.Cards.Holders.NCardHolder) }),
            typeof(MegaCrit.Sts2.Core.Nodes.Cards.NCard).GetMethod("SetPreviewTarget", flags, new[] { typeof(MegaCrit.Sts2.Core.Entities.Creatures.Creature) }),
            typeof(MegaCrit.Sts2.Core.Nodes.Screens.NInspectCardScreen).GetMethod("UpdateCardDisplay", flags, Type.EmptyTypes),
            typeof(MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen.NGameOverScreen).GetMethod("OpenSummaryScreen", flags, new[] { typeof(MegaCrit.Sts2.Core.Nodes.GodotExtensions.NButton) })
        };
        Assert.All(methods, method => { Assert.NotNull(method); Assert.Equal(typeof(void), method.ReturnType); });
    }
    [Fact]
    public void SourceOwnerReentryAndPrivateBindingReplacementInvalidateOldHandles()
    {
        using var fixture = new Fixture();
        var first = fixture.Current();
        fixture.Frame = fixture.Frame with { OwnerKey = "B" };
        fixture.Owner.Publish("native_target_focus", "return");
        fixture.Frame = fixture.Frame with { OwnerKey = "A" };
        var next = fixture.Current();
        Assert.NotEqual(first.Capture!.SnapshotId, next.Capture!.SnapshotId);
        var old = Assert.Single(fixture.Owner.Store.Catalog(first.Capture.CaptureId).Actions);
        Assert.Null(fixture.Owner.Revalidate(first.Capture.SnapshotId, old.ActionId));
        fixture.Frame = fixture.Frame with { Leaves = new[] { fixture.Frame.Leaves[0] with { Key = "replacement" } } };
        var replacement = fixture.Current();
        Assert.NotEqual(next.Capture.SnapshotId, replacement.Capture!.SnapshotId);
    }
    [Theory]
    [InlineData("inspect", "visible_unsupported")]
    [InlineData("held", "visible_unsupported")]
    [InlineData("potion", "visible_unsupported")]
    [InlineData("inspect", "settling")]
    [InlineData("held", "settling")]
    [InlineData("potion", "settling")]
    public void ValidNativeDisplayAndControlsCannotSealAnInheritedIncompleteHud(string family, string sourceReadiness)
    {
        using var fixture = new Fixture();
        var before = fixture.Current();
        var oldAction = Assert.Single(fixture.Owner.Store.Catalog(before.Capture!.CaptureId).Actions);
        PlayerEnvironmentSnapshot source = fixture.Frame.Page with
        {
            Status = sourceReadiness, Persistent = null,
            Completeness = new("partial", "inherited", "inherited",
                new[] { "persistent_visible_state", "public_information_binding_power_owner",
                    "page_entities_catalog_consistency", "finite_bound_action_projection_incomplete" },
                new[] { "hidden_future_outcome" })
        };
        // These are the same production projections used after native label and
        // exact enabled-control extraction. No scene or native input is invoked.
        PlayerEnvironmentSnapshot page = ProjectEnteredPage(source, family, nativeLogical: true);
        var leaves = new[]
        {
            fixture.Frame.Leaves[0],
            fixture.Frame.Leaves[0] with { Key = "information", Verb = "show_card_tips" },
            fixture.Frame.Leaves[0] with { Key = "peek", Verb = "close_peek" },
            fixture.Frame.Leaves[0] with { Key = "return", Verb = "return_card_inspect" }
        };
        fixture.Frame = NativeLogicalCapture.Validate(fixture.Frame with
            { Page = page, OwnerKey = "entered:" + family, Leaves = leaves });
        Assert.Equal(sourceReadiness, fixture.Frame.Page.Status);
        Assert.Equal("partial", fixture.Frame.Page.Completeness.Status);
        Assert.Null(fixture.Frame.Page.Persistent);
        Assert.Equal(source.Completeness.Missing.Order(StringComparer.Ordinal), fixture.Frame.Page.Completeness.Missing);
        Assert.Equal(source.Completeness.HiddenByPolicy, fixture.Frame.Page.Completeness.HiddenByPolicy);
        Assert.Empty(fixture.Frame.Leaves); // Includes tips, Peek and return.
        NativeLogicalCurrentReply current = fixture.Current();
        Assert.Equal("source_capture_incomplete", current.Status);
        Assert.Null(current.Capture);
        Assert.Null(fixture.Owner.Revalidate(before.Capture.SnapshotId, oldAction.ActionId));
        Assert.Equal(0, fixture.Dispatches);
    }

    [Theory]
    [InlineData("inspect")]
    [InlineData("held")]
    [InlineData("potion")]
    public void NativePageProjectionRetainsUnexplainedInheritedFailureButTextCompatibilityDoesNotChange(string family)
    {
        using var fixture = new Fixture();
        PlayerEnvironmentSnapshot source = fixture.Frame.Page with
        {
            Status = "visible_unsupported",
            Completeness = new("visible_unmapped", "unknown", "unknown", Array.Empty<string>(), new[] { "hidden_future_outcome" })
        };
        PlayerEnvironmentSnapshot native = ProjectEnteredPage(source, family, nativeLogical: true);
        Assert.Equal("partial", native.Completeness.Status);
        Assert.Equal("visible_unsupported", native.Status);
        Assert.Equal(source.Completeness.HiddenByPolicy, native.Completeness.HiddenByPolicy);
        PlayerEnvironmentSnapshot compatibility = ProjectEnteredPage(source, family, nativeLogical: false);
        Assert.Equal("complete", compatibility.Completeness.Status);
        Assert.Equal("interactive", compatibility.Status);
        Assert.Empty(compatibility.Completeness.Missing);
        Assert.Empty(compatibility.Completeness.HiddenByPolicy);
    }

    private static PlayerEnvironmentSnapshot ProjectEnteredPage(PlayerEnvironmentSnapshot source, string family, bool nativeLogical)
    {
        var surface = new JsonObject { ["kind"] = "current_display", ["displayed_title"] = "Defend",
            ["displayed_cost"] = "1", ["displayed_description"] = "Gain 5 Block." };
        return family switch
        {
            "inspect" => NativeTextMenuInformation.PreserveInformationScope(source,
                new(NativeTextMenuInformation.ProjectCardInspectPage(source, "Defend", "1", "Gain 5 Block.", true),
                    "entered_inspect", Array.Empty<NativeTextMenuInformationLeaf>()), nativeLogical).Page,
            "held" => NativeTextMenuFrameBuilder.ProjectHeldCardPage(source, source.Referents, surface,
                "native_targeting", displayComplete: true, nativeLogical),
            "potion" => NativeLogicalCapturePolicy.PreserveNativeScope(source,
                NativeTextMenuFrameBuilder.ProjectPotionTargetPage(source, source.Referents, surface),
                NativeLogicalProjectionReplacement.PotionTarget, nativeLogical),
            _ => throw new ArgumentOutOfRangeException(nameof(family))
        };
    }

    [Fact]
    public async Task InitialAttachReservesItsOwnPositionAndPartialSourceIsExplicitMissing()
    {
        using var fixture = new Fixture();
        fixture.Frame = fixture.Frame with { Page = fixture.Frame.Page with { Completeness = fixture.Frame.Page.Completeness with
        { Status = "partial", Missing = new[] { "native_logical_public_list_action_bindings_incomplete" } } } };
        var attached = fixture.Owner.Attach(new(fixture.ReaderId, NativeLogicalProjector.ScopeFields,
            new[] { NativeLogicalService.Coverage[0] }, "full_reference"));
        var sub = Assert.IsType<NativeLogicalSubscription>(attached.Subscription);
        var current = fixture.Current();
        var available = await fixture.Owner.Hub.AwaitAsync(fixture.ReaderId, sub.SubscriptionId, sub.ScopeId, sub.StartingCursor,
            "00000000000000000000000000000001", "any_event", 10_000);
        Assert.Equal("event", available.Status);
        Assert.Equal("1", available.Event!.Event.PublicationIndex);
        Assert.Null(available.Event.Event.CaptureRef);
        Assert.Equal("source_capture_incomplete", available.Event.Event.MissingReason);
        Assert.Equal("source_capture_incomplete", current.Status);
        Assert.Null(current.Capture);
        Assert.Equal("native_logical_public_list_action_bindings_incomplete", current.Reason);
    }
    [Fact]
    public void CancelledUnstartedCurrentNeverCapturesAndSealedQueriesNeverCaptureAgain()
    {
        using var fixture = new Fixture();
        using var cancellation = new CancellationTokenSource();
        var waiting = fixture.Owner.CurrentAsync(new(fixture.ReaderId, NativeLogicalProjector.ScopeFields, null), cancellation.Token);
        cancellation.Cancel();
        Assert.ThrowsAny<OperationCanceledException>(() => waiting.GetAwaiter().GetResult());
        Assert.Equal(0, fixture.Queue.Drain(1)); Assert.Equal(0, fixture.Captures);
        var reply = fixture.Current(); int count = fixture.Captures;
        var capture = reply.Capture!;
        var bytes = fixture.Owner.Store.Read(capture.CaptureId, capture.ReadCursor);
        Assert.True(bytes.Complete);
        var catalog = fixture.Owner.Store.CatalogByReference(fixture.Owner.Store.Catalog(capture.CaptureId).Descriptor.CatalogRef);
        Assert.Single(catalog.List(capture.StreamGeneration).Actions);
        Assert.Equal(count, fixture.Captures); Assert.Equal(0, fixture.Queue.PendingCount);
    }
    [Fact]
    public async Task SharedRequestNamespaceRejectsCrossProfileReuseBeforeDispatch()
    {
        using var fixture = new Fixture();
        var legacy = new PlayerEnvironmentActionRequest("existing", "snapshot", "action",
            fixture.Client.Client.ClientSessionId, fixture.Lease.ControllerLeaseId, fixture.Lease.ControllerGeneration, null);
        var admitted = fixture.Requests.Admit(legacy); Assert.Equal("admitted", admitted.Status);
        fixture.Requests.CancelQueued("existing", "fixture_preinput"); (await admitted.OriginalCompletion!).Dispose();
        var result = fixture.Submit(legacy with { InputProfile = NativeLogicalContract.Profile });
        Assert.Equal("request_id_conflict", result.Reason); Assert.Equal("not_started", result.Delivery);
        Assert.Equal("never_automatic", result.Retry); Assert.Equal(0, fixture.Dispatches);
    }
    [Fact]
    public async Task QueuedAdmissionIsPendingAndQueueRejectionBecomesOneOriginalTerminalResult()
    {
        using var fixture = new Fixture();
        var current = fixture.Current();
        var action = Assert.Single(fixture.Owner.Store.Catalog(current.Capture!.CaptureId).Actions);
        var request = new PlayerEnvironmentActionRequest("queued", current.Capture.SnapshotId, action.ActionId,
            fixture.Client.Client.ClientSessionId, fixture.Lease.ControllerLeaseId, fixture.Lease.ControllerGeneration, NativeLogicalContract.Profile);
        var original = fixture.Requests.Admit(request); Assert.Equal("admitted", original.Status);
        Assert.True(fixture.IsPending("queued")); Assert.Null(fixture.Find("queued"));
        var duplicate = fixture.Requests.Admit(request);
        Assert.Equal("pending", duplicate.Status); Assert.Null(duplicate.Reply);
        Assert.True(fixture.Requests.CancelQueued(request.RequestId!, "main_thread_queue_full"));
        var failure = fixture.Find(request.RequestId!)!; (await original.OriginalCompletion!).Dispose();
        Assert.Equal("not_started", failure.Delivery); Assert.False(fixture.IsPending("queued"));
        RequestTestDriver.AssertWireEqual(failure, fixture.Submit(request));
        RequestTestDriver.AssertWireEqual(failure, RequestTestDriver.Decode<NativeLogicalResult>(fixture.Requests.Admit(request).Reply!)); Assert.Equal(0, fixture.Dispatches);
    }
    [Fact]
    public void OriginalPartialResultReplaysWithoutRedispatchAndKeepsUnknownExecution()
    {
        using var fixture = new Fixture();
        var current = fixture.Current();
        var action = Assert.Single(fixture.Owner.Store.Catalog(current.Capture!.CaptureId).Actions);
        fixture.Outcome = NativeInputResult.PartiallyDelivered("later_input_failed", "Known earlier input",
            new NativeInputStage(NativeInputStageKind.TargetFocus, NativeInputDelivery.Delivered, "focus_delivered"));
        var request = new PlayerEnvironmentActionRequest("partial", current.Capture.SnapshotId, action.ActionId,
            fixture.Client.Client.ClientSessionId, fixture.Lease.ControllerLeaseId, fixture.Lease.ControllerGeneration, NativeLogicalContract.Profile);
        var first = fixture.Submit(request); var replay = fixture.Submit(request);
        RequestTestDriver.AssertWireEqual(first, replay); Assert.Equal(1, fixture.Dispatches);
        Assert.Equal("partially_delivered", first.Delivery); Assert.Equal("unknown", first.Execution);
        Assert.Equal("unknown", first.Effect); Assert.Equal("unknown", first.Cancel);
        Assert.Equal("never_automatic", first.Retry); Assert.Equal("delivered", Assert.Single(first.Stages).Delivery);
        Assert.False(fixture.IsPending("partial")); RequestTestDriver.AssertWireEqual(first, fixture.Find("partial"));
    }
    [Fact]
    public async Task StopAndWatchRemainPromptWhileStartedNativeCallbackIsBlocked()
    {
        using var fixture = new Fixture();
        var current = fixture.Current();
        var action = Assert.Single(fixture.Owner.Store.Catalog(current.Capture!.CaptureId).Actions);
        using var entered = new ManualResetEventSlim(); using var released = new ManualResetEventSlim();
        using var lost = new ManualResetEventSlim();
        var authorization = new MutationAuthorizationRequest(fixture.Client.Client.ClientSessionId,
            fixture.Lease.ControllerLeaseId, fixture.Lease.ControllerGeneration);
        Assert.True(fixture.Controller.TryWatch(authorization, lost.Set, out var watch));
        using (watch)
        {
            fixture.OnDispatch = () => { entered.Set(); Assert.True(released.Wait(2_000)); };
            var stop = Task.Run(() =>
            {
                Assert.True(entered.Wait(2_000)); Assert.True(fixture.IsPending("started"));
                var receipt = fixture.Controller.Release(new(authorization.ClientSessionId, authorization.ControllerLeaseId, authorization.ControllerGeneration));
                Assert.Equal("controller_released", receipt.Status);
                Assert.False(fixture.Controller.TryBegin(authorization).Accepted);
                Assert.True(lost.Wait(2_000)); released.Set();
            });
            var result = fixture.Submit(new("started", current.Capture.SnapshotId, action.ActionId,
                authorization.ClientSessionId, authorization.ControllerLeaseId, authorization.ControllerGeneration, NativeLogicalContract.Profile));
            await stop; Assert.Equal("delivered", result.Delivery);
            Assert.Equal("unknown", result.Execution); Assert.Equal(1, fixture.Dispatches);
        }
    }
    [Theory]
    [InlineData("revoked")]
    [InlineData("expired")]
    [InlineData("foreign")]
    public async Task ClosedOrForeignOriginalCannotAllocateCurrentAttachOrRetentionButKnownReadsRemain(string mode)
    {
        long mono = 0;
        using var fixture = new Fixture(() => mono, 100);
        var captured = fixture.Current().Capture!;
        var lastSeen = fixture.Controller.Snapshot().Clients.Single(c => c.ClientSessionId == fixture.ReaderId).LastSeenAt;
        mono = 90;
        var retained = fixture.Owner.Retain(new(fixture.ReaderId, captured.CaptureId));
        Assert.Equal("retained", retained.Status);
        Assert.Equal(lastSeen, fixture.Controller.Snapshot().Clients.Single(c => c.ClientSessionId == fixture.ReaderId).LastSeenAt);
        string target = fixture.ReaderId;
        if (mode == "expired") mono = 100;
        else if (mode == "revoked") fixture.Controller.Revoke(new("runtime", target));
        else target = "foreign-unregistered";
        int captures = fixture.Captures;
        Assert.Equal("client_session_expired", (await Assert.ThrowsAsync<NativeLogicalException>(() => fixture.Owner.CurrentAsync(
            new(target, NativeLogicalProjector.ScopeFields, null)))).Code);
        Assert.Equal("client_session_expired", Assert.Throws<NativeLogicalException>(() => fixture.Owner.Attach(
            new(target, NativeLogicalProjector.ScopeFields, new[] { NativeLogicalService.Coverage[0] }, "full_reference"))).Code);
        Assert.Equal("client_session_expired", Assert.Throws<NativeLogicalException>(() => fixture.Owner.Retain(
            new(target, captured.CaptureId))).Code);
        Assert.Equal(captures, fixture.Captures); Assert.Equal(0, fixture.Queue.PendingCount);
        var probe = fixture.Owner.Hub.Reserve("native_target_focus", "1", "negative_admission_probe");
        Assert.Empty(probe.Subscriptions);
        fixture.Owner.Hub.Complete(probe, Array.Empty<NativeLogicalProjectionOutcome>());
        Assert.True(fixture.Owner.Store.Read(captured.CaptureId, captured.ReadCursor).Complete);
        fixture.Owner.Store.ReleasePublic(new(fixture.ReaderId, retained.Retention!.RetentionHandleId));
    }

    [Theory]
    [InlineData("{\"capture_id\":\"a\",\"cursor\":\"b\",\"max_bytes\":1,\"extra\":0}")]
    [InlineData("{\"capture_id\":\"a\",\"capture_id\":\"b\",\"cursor\":\"c\",\"max_bytes\":1}")]
    [InlineData("{\"capture_id\":\"a\",\"cursor\":null,\"max_bytes\":1}")]
    public void NativeTransportRejectsUnknownDuplicateAndNullRequiredFields(string body)
    {
        Assert.Throws<NativeLogicalException>(() => ConnectorMod.DecodeNativeLogicalRequest<ConnectorMod.NativeLogicalReadRequest>(Encoding.UTF8.GetBytes(body)));
    }
    [Fact]
    public void RichNativeDeliveryAndLegacyAcceptanceNeverProveExecution()
    {
        Assert.Equal("delivered", NativeLogicalExecutor.Delivery(NativeInputResult.Delivered("accepted")));
        Assert.Equal("partially_delivered", NativeLogicalExecutor.Delivery(NativeInputResult.PartiallyDelivered("part", "detail")));
        Assert.Equal("unknown", NativeLogicalExecutor.Delivery(NativeInputResult.Unknown("unknown", "detail")));
    }
    private static TextMenuFrame MakeFrame(Func<NativeInputResult> dispatch)
    {
        var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "source", 1,
            DateTimeOffset.UnixEpoch, "interactive", null,
            new("interaction", "held", "ready", null, "surface", new(new JsonObject { ["kind"] = "held" }, new JsonObject()),
                Array.Empty<PlayerEnvironmentInteractionCapability>()), Array.Empty<PlayerEnvironmentReferent>(),
            new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 65536, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "public", "complete", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
        return new(page, "A", new[] { new TextMenuLeaf("binding", "root", "confirm", "Confirm", null,
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatch) }) { GameContinuityId = "run" };
    }
}
