using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2HumanAnnotator.Core;
using STS2Platform.NativeFoundation;
using Xunit;

namespace STS2HumanAnnotator.SourceNative.Tests;

public sealed class SourceNativeOrderedProducerTests
{
    private sealed class ObservedSink(INativeLogicalSourceSink inner) : INativeLogicalSourceSink
    {
        internal readonly Queue<TaskCompletionSource> basis = new();
        public bool RequiresOrderedBasis => inner.RequiresOrderedBasis;
        public void Epoch(NativeLogicalSourceEpoch epoch) => inner.Epoch(epoch);
        public void Boundary(NativeLogicalSourceTransition transition, NativeLogicalSourcePosition position) => inner.Boundary(transition, position);
        public object? AdmitInput(NativeLogicalSourceInputPrefix prefix) => inner.AdmitInput(prefix);
        public void InputFrozen(object original, NativeLogicalSourceBasisOrder order) => inner.InputFrozen(original, order);
        public void InputMapping(object original, NativeLogicalSourceBasisMapping mapping) => inner.InputMapping(original, mapping);
        public void InputBasis(object original, string? capture, IDisposable? retention, string? missing)
        {
            inner.InputBasis(original, capture, retention, missing);
            lock (basis) if (basis.Count > 0) basis.Dequeue().TrySetResult();
        }
        public void InputTerminal(object original, NativeLogicalSourceInputTerminal terminal) => inner.InputTerminal(original, terminal);
        public void AccountingFailed(string code) => inner.AccountingFailed(code);
        internal Task NextBasis()
        { var ready = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously); lock (basis) basis.Enqueue(ready); return ready.Task; }
    }
    private static NativeSourceInputInvocation Begin(SourceNativeProducerTests.Fixture f) =>
        Assert.IsType<NativeSourceInputInvocation>(NativeSourceInputProvider.Begin(new("confirm", f.PhysicalOwner, null,
            new Dictionary<string, object>(StringComparer.Ordinal), "fixture.actual_native_prefix", nameof(PlayCardAction), typeof(PlayCardAction))));
    private static GameAction Carrier() => (GameAction)RuntimeHelpers.GetUninitializedObject(typeof(PlayCardAction));
    private static void Passed(SourceNativeProducerTests.Fixture f)
    { var audit = SourceSessionAuditV3.Audit(f.Store.DirectoryPath); Assert.True(audit.Status == "pass", string.Join(",", audit.Errors)); }
    private static async Task Ready(SourceNativeProducerTests.Fixture f) =>
        Assert.True(await Task.Run(() => SpinWait.SpinUntil(() => f.Store.GetSourceStatusV2()!.Observations >= 1, TimeSpan.FromSeconds(3))));

    [Fact]
    public async Task ActualProducerPreservesSameWatermarkPrefixActorAndEpochDespiteReverseAcceptedTerminalOrder()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        var sink = new ObservedSink(f.Attachment.Sink!); f.Invoke(() => f.Attachment.Sink = sink);
        Task firstBasis = sink.NextBasis(); GameAction first = Carrier();
        f.Invoke(() => { f.Surface = "input-a"; var original = Begin(f); NativeSourceInputProvider.BindSubmitted(first); NativeSourceInputProvider.Finish(original); });
        await firstBasis.WaitAsync(TimeSpan.FromSeconds(2));
        f.Invoke(() =>
        {
            f.Worker.CommandBoundary("pause"); f.Worker.ChangeSource(new("declared_human", "actor-second", "declaration-second"), f.Worker.Status.SegmentId);
            f.Worker.CommandBoundary("resume");
        });
        Task secondBasis = sink.NextBasis(); GameAction second = Carrier();
        f.Invoke(() => { f.Surface = "input-b"; var original = Begin(f); NativeSourceInputProvider.BindSubmitted(second); NativeSourceInputProvider.Finish(original); });
        await secondBasis.WaitAsync(TimeSpan.FromSeconds(2));
        f.Invoke(() => Assert.True(NativeSourceInputProvider.ObserveAccepted(second)));
        Assert.True(await Task.Run(() => SpinWait.SpinUntil(() => f.Worker.Status.Inputs == 1, TimeSpan.FromSeconds(3))));
        f.Invoke(() => { f.Setup(true); Assert.True(NativeSourceInputProvider.ObserveAccepted(first)); });
        await f.Close(); Passed(f);
        var inputs = f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl");
        Assert.Equal(new[] { "2", "1" }, inputs.Select(row => row.InputPrefixOrdinal));
        Assert.All(inputs, row => Assert.Equal("1", row.PrePosition.PublicationIndex));
        Assert.All(inputs, row => Assert.Equal("native_prefix_frozen", row.BasisOrder!.Status));
        var segments = f.Rows<SourceSegmentV2>("source-segments.jsonl");
        Assert.Equal("actor-original", segments.Single(row => row.SegmentId == inputs[1].SegmentId).Declaration.ActorId);
        Assert.Equal("actor-second", segments.Single(row => row.SegmentId == inputs[0].SegmentId).Declaration.ActorId);
        Assert.Equal(inputs[0].EpochId, inputs[1].EpochId); Assert.NotEqual(inputs[0].EpochId, f.Attachment.Current.EpochId);
        var frames = inputs.OrderBy(row => SourceSessionContractV3.Ordinal(row.InputPrefixOrdinal!)).Select(row =>
            JsonSerializer.Deserialize<NativeLogicalObservation>(File.ReadAllBytes(Path.Combine(f.Store.DirectoryPath, row.PreCapture!.PayloadRef)), NativeLogicalWire.Options)!).ToArray();
        Assert.True(frames[0].Revision < frames[1].Revision);
        Assert.Equal("input-a", frames[0].Interaction!.Kind); Assert.Equal("input-b", frames[1].Interaction!.Kind);
        string bundle = Path.Combine(f.Root, "ordered-physical-bundle");
        Assert.Equal("pass", SourceSessionBundlePackerV3.Pack(f.Store.DirectoryPath, "worker-physical-synthetic", "campaign-v3", bundle, new string('e', 40)).Status);
        string? golden = Environment.GetEnvironmentVariable("STS2_SOURCE_V3_ORDERED_GOLDEN");
        if (!string.IsNullOrEmpty(golden)) SourceNativeProducerTests.CopyGolden(bundle, Path.GetFullPath(golden));
    }
    [Fact]
    public async Task ActualPhysicalInputImmediatelyAfterResumeUsesItsOriginalOrdinalAtTheUnchangedPausedEndWatermark()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        var sink = new ObservedSink(f.Attachment.Sink!); f.Invoke(() => f.Attachment.Sink = sink);
        Task originalBasis = sink.NextBasis(); GameAction pending = Carrier();
        f.Invoke(() => { var original = Begin(f); NativeSourceInputProvider.BindSubmitted(pending); NativeSourceInputProvider.Finish(original); });
        await originalBasis.WaitAsync(TimeSpan.FromSeconds(2));
        f.Invoke(() =>
        {
            f.Worker.CommandBoundary("pause");
            f.Surface = "paused-first-publication"; f.Owner.Publish("native_owner_ready", "original_paused_publication_first");
            f.Worker.ChangeSource(new("declared_human", "actor-after-resume", "declaration-after-resume"), f.Worker.Status.SegmentId);
            f.Surface = "paused-second-publication"; f.Owner.Publish("native_owner_ready", "original_paused_publication_second");
            f.Worker.CommandBoundary("resume");
        });
        Task resumedBasis = sink.NextBasis();
        f.Invoke(() =>
        {
            var before = f.Attachment.ReadBoundary().Position; Assert.Equal("3", before.PublicationIndex);
            f.Surface = "input-immediately-after-resume";
            var resumed = Begin(f); f.Dispatches++; NativeSourceInputProvider.Accepted(resumed); NativeSourceInputProvider.Finish(resumed);
            Assert.Equal(before, f.Attachment.ReadBoundary().Position);
        });
        await resumedBasis.WaitAsync(TimeSpan.FromSeconds(2));
        Assert.True(await Task.Run(() => SpinWait.SpinUntil(() => f.Worker.Status.Inputs == 1, TimeSpan.FromSeconds(3))));
        f.Invoke(() => Assert.True(NativeSourceInputProvider.ObserveAccepted(pending)));
        await f.Close(); Passed(f);
        var inputs = f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl");
        Assert.Equal(new[] { "2", "1" }, inputs.Select(row => row.InputPrefixOrdinal));
        Assert.Equal("3", inputs[0].PrePosition.PublicationIndex); Assert.All(inputs, row => Assert.Equal("delivered", row.Outcome.Delivery));
        Assert.All(inputs, row => Assert.Equal("exact", row.Outcome.MappingStatus));
        var resume = Assert.Single(f.Rows<SourceBoundaryV2>("source-boundaries.jsonl"), row => row.Kind == "resume");
        Assert.Equal(inputs[0].PrePosition, resume.Position); Assert.Equal("1", resume.AfterInputOrdinal);
        var interval = Assert.Single(resume.PausedIntervals); Assert.Equal("1", interval.AfterIndex); Assert.Equal("3", interval.ThroughIndex);
        Assert.Equal("1", interval.AfterInputOrdinal); Assert.Equal("1", interval.ThroughInputOrdinal);
        var segments = f.Rows<SourceSegmentV2>("source-segments.jsonl");
        Assert.Equal("actor-after-resume", segments.Single(row => row.SegmentId == inputs[0].SegmentId).Declaration.ActorId);
        Assert.Equal("actor-original", segments.Single(row => row.SegmentId == inputs[1].SegmentId).Declaration.ActorId);
        // Resume is a command boundary; no publication, Current response or
        // invented successor is added to make this original prefix pass.
        string bundle = Path.Combine(f.Root, "resume-tie-physical-bundle");
        Assert.Equal("pass", SourceSessionBundlePackerV3.Pack(f.Store.DirectoryPath, "worker-resume-physical", "campaign-resume-v3", bundle, new string('e', 40)).Status);
        string? golden = Environment.GetEnvironmentVariable("STS2_SOURCE_V3_RESUME_TIE_GOLDEN");
        if (!string.IsNullOrEmpty(golden)) SourceNativeProducerTests.CopyGolden(bundle, Path.GetFullPath(golden));
    }
    [Fact]
    public async Task PublicationInsideOriginalPhysicalPrepareFailsBothSourceAcquisitionsWithoutMovingItsInputCut()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        f.Invoke(() =>
        {
            f.OnCapture = () => { f.OnCapture = null; f.Surface = "nested-publication"; f.Owner.Publish("native_owner_ready", "nested_native_prepare"); };
            var original = Begin(f); f.Dispatches++; NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original);
        });
        await f.Close(); Passed(f);
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal("1", input.PrePosition.PublicationIndex); Assert.Equal("delivered", input.Outcome.Delivery);
        Assert.Equal("unproven", input.BasisOrder!.Status); Assert.Equal("capture_missing", input.Outcome.MappingStatus); Assert.Null(input.PreCapture);
        var missing = Assert.Single(f.Rows<SourcePublicObservationV2>("public-observations.jsonl"), row => row.Position.PublicationIndex == "2");
        Assert.Equal(SourceSessionContractV3.OrderUnprovenReason, missing.MissingReason); Assert.Null(missing.Capture); Assert.Equal(1, f.Dispatches);
    }
    [Fact]
    public async Task InputInsideAncestorReservedUnfrozenPublicationCannotAcquireItsPostInputFactsAsAPredecessor()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        f.Invoke(() =>
        {
            f.OnCapture = () =>
            {
                f.OnCapture = null; var original = Begin(f); f.Dispatches++; f.Surface = "after-input";
                NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original);
            };
            f.Owner.Publish("native_owner_ready", "outer_native_prepare");
        });
        await f.Close(); Passed(f);
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal("2", input.PrePosition.PublicationIndex); Assert.Equal("unproven", input.BasisOrder!.Status);
        Assert.Equal("delivered", input.Outcome.Delivery); Assert.Null(input.PreCapture);
        var missing = Assert.Single(f.Rows<SourcePublicObservationV2>("public-observations.jsonl"), row => row.Position.PublicationIndex == "2");
        Assert.Equal(SourceSessionContractV3.OrderUnprovenReason, missing.MissingReason); Assert.Equal(1, f.Dispatches);
    }
    [Fact]
    public async Task EncoderOnlyBacklogDoesNotBecomeAnAncestorNativeAcquisitionFailure()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        object projector = typeof(NativeLogicalService).GetField("projector", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(f.Owner)!;
        object gate = typeof(NativeLogicalProjector).GetField("gate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(projector)!;
        using var entered = new ManualResetEventSlim(); using var release = new ManualResetEventSlim();
        var encoder = Task.Run(() => { lock (gate) { entered.Set(); release.Wait(); } }); Assert.True(entered.Wait(TimeSpan.FromSeconds(2)));
        try
        {
            f.Invoke(() =>
            {
                f.Surface = "queued-publication"; f.Owner.Publish("native_owner_ready", "frozen_before_encoder");
                var watermark = f.Attachment.ReadBoundary(); Assert.NotEqual(watermark.Position.PublicationIndex, watermark.CompletedThrough);
                f.Surface = "queued-input"; var original = Begin(f); NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original);
            });
        }
        finally { release.Set(); await encoder; }
        await f.Close(); Passed(f);
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal("2", input.PrePosition.PublicationIndex); Assert.Equal("native_prefix_frozen", input.BasisOrder!.Status);
        Assert.Equal("exact", input.Outcome.MappingStatus); Assert.All(f.Rows<SourcePublicObservationV2>("public-observations.jsonl"), row => Assert.Null(row.MissingReason));
    }
    [Theory]
    [InlineData(0, "unmapped")]
    [InlineData(2, "ambiguous")]
    public async Task ActualPhysicalMappingUsesTheCompleteOriginalCatalogueAndKeepsZeroOrManyExplicit(int matches, string mapping)
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        f.Invoke(() => { f.PhysicalMatchCount = matches; var original = Begin(f); NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original); });
        await f.Close(); Passed(f);
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal(mapping, input.Outcome.MappingStatus); Assert.Equal(matches, input.Outcome.MatchCount); Assert.Null(input.Outcome.SelectedAction);
        Assert.NotNull(input.PreCapture); Assert.Equal(Math.Max(1, matches), input.Catalog!.TotalCount); Assert.Equal("delivered", input.Outcome.Delivery);
    }
    [Fact]
    public async Task ProtocolLexicalDispatchSuppressesExactlyItsPhysicalHookButEndsBeforeUnrelatedLaterInput()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        f.Invoke(() => f.OnDispatch = () => Assert.Null(NativeSourceInputProvider.Begin(new("confirm", f.PhysicalOwner, null,
            new Dictionary<string, object>(StringComparer.Ordinal), "fixture.protocol_native_hook", nameof(PlayCardAction), typeof(PlayCardAction)))));
        var result = f.Invoke(() => f.Input("protocol-original")); Assert.Equal("delivered", result.Delivery);
        f.Invoke(() => { var physical = Begin(f); NativeSourceInputProvider.Accepted(physical); NativeSourceInputProvider.Finish(physical); });
        await f.Close(); Passed(f);
        var inputs = f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"); Assert.Equal(2, inputs.Length);
        Assert.Single(inputs, row => row.InputId == "protocol-original"); Assert.Equal(new[] { "1", "2" }, inputs.Select(row => row.InputPrefixOrdinal).Order());
    }
    [Fact]
    public async Task ClosingPendingOriginalPhysicalCarrierMakesItUnknownAndLateDuplicateCannotRewriteClosure()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f); var exact = Carrier();
        f.Invoke(() => { var original = Begin(f); NativeSourceInputProvider.BindSubmitted(exact); NativeSourceInputProvider.Finish(original); });
        await f.Close(); Passed(f);
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal("1", input.InputPrefixOrdinal); Assert.Equal("unknown", input.Outcome.Delivery);
        string path = Path.Combine(f.Store.DirectoryPath, "native-input-witnesses.jsonl"); byte[] originalBytes = File.ReadAllBytes(path);
        f.Invoke(() => { Assert.True(NativeSourceInputProvider.ObserveAccepted(exact)); Assert.False(NativeSourceInputProvider.ObserveAccepted(exact)); });
        Assert.Equal(originalBytes, File.ReadAllBytes(path));
    }
    [Fact]
    public async Task ActualPhysicalPrefixAndNativeBodyRemainNonblockingWhileTheDiskWriterIsHeld()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        object gate = typeof(RecordingSessionStore).GetField("_gate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(f.Store)!;
        using var entered = new ManualResetEventSlim(); using var release = new ManualResetEventSlim();
        var disk = Task.Run(() => { lock (gate) { entered.Set(); release.Wait(); } }); Assert.True(entered.Wait(TimeSpan.FromSeconds(2)));
        try
        {
            await Task.Run(() => f.Invoke(() =>
            {
                f.Worker.CommandBoundary("pause"); f.Worker.CommandBoundary("resume");
                var original = Begin(f); f.Dispatches++; NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original);
            })).WaitAsync(TimeSpan.FromSeconds(2));
            Assert.Equal(1, f.Dispatches);
        }
        finally { release.Set(); await disk; }
        await f.Close(); Passed(f); Assert.Equal("1", Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl")).InputPrefixOrdinal);
    }
    [Fact]
    public async Task CopyCapacityFailureKeepsTheIssuedOrdinalAndOriginalDeliveredTerminalAsCaptureMissing()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        var reserve = typeof(NativeLogicalService).GetMethod("ReserveSourceBytes", BindingFlags.Instance | BindingFlags.NonPublic)!;
        using var full = (IDisposable)reserve.Invoke(f.Owner, new object[] { 128L * 1024 * 1024 })!;
        f.Invoke(() => { var original = Begin(f); f.Dispatches++; NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original); });
        await f.Close(); Passed(f);
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal("1", input.InputPrefixOrdinal); Assert.Equal("capture_missing", input.Outcome.MappingStatus);
        Assert.Equal("source_copied_payload_capacity", input.Outcome.ReasonCode); Assert.Equal("delivered", input.Outcome.Delivery);
        Assert.Equal("native_prefix_frozen", input.BasisOrder!.Status); Assert.Equal(1, f.Dispatches);
    }
    [Fact]
    public async Task PhysicalEncodingTimeoutUsesTheOriginalDeadlineAndDoesNotLoseItsNativeTerminal()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        object projector = typeof(NativeLogicalService).GetField("projector", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(f.Owner)!;
        object gate = typeof(NativeLogicalProjector).GetField("gate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(projector)!;
        using var entered = new ManualResetEventSlim(); using var release = new ManualResetEventSlim();
        var encoder = Task.Run(() => { lock (gate) { entered.Set(); release.Wait(); } }); Assert.True(entered.Wait(TimeSpan.FromSeconds(2)));
        try
        {
            f.Invoke(() => { var original = Begin(f); NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original); });
            await Task.Delay(2200);
        }
        finally { release.Set(); await encoder; }
        await f.Close(); Passed(f);
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal("1", input.InputPrefixOrdinal); Assert.Equal("delivered", input.Outcome.Delivery);
        Assert.Equal("capture_missing", input.Outcome.MappingStatus); Assert.Equal("source_input_basis_encoding_timeout", input.Outcome.ReasonCode);
        Assert.Equal("native_prefix_frozen", input.BasisOrder!.Status);
    }
    [Fact]
    public async Task PausedPhysicalPrefixIsExcludedAndNativeExceptionOrNoAcceptedCallbackRemainOriginalUnknowns()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        f.Invoke(() =>
        {
            f.Worker.CommandBoundary("pause");
            Assert.Null(NativeSourceInputProvider.Begin(new("confirm", f.PhysicalOwner, null,
                new Dictionary<string, object>(StringComparer.Ordinal), "fixture.paused_native_prefix", nameof(PlayCardAction), typeof(PlayCardAction))));
            f.Dispatches++; f.Worker.CommandBoundary("resume");
            var first = Begin(f); NativeSourceInputProvider.Finish(first, new InvalidOperationException("native-body-failed"));
        });
        // Let the first actual immutable basis release its encoder scratch before
        // admitting the second input under the announced shared copy budget.
        await Task.Delay(50);
        f.Invoke(() => { var second = Begin(f); NativeSourceInputProvider.Finish(second); });
        await f.Close(); Passed(f);
        var inputs = f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"); Assert.Equal(2, inputs.Length);
        Assert.Equal(new[] { "1", "2" }, inputs.Select(row => row.InputPrefixOrdinal).Order());
        Assert.All(inputs, row => Assert.Equal("unknown", row.Outcome.Delivery)); Assert.Equal(1, f.Dispatches);
        Assert.Contains(inputs, row => row.Outcome.ReasonCode == "native_source_input_boundary_threw");
        Assert.Contains(inputs, row => row.Outcome.ReasonCode == "native_source_scope_ended_without_acceptance");
    }
    [Fact]
    public async Task ObsoleteOriginalCarrierCannotReachAReplacementStoreOrConsumeItsFirstOrdinal()
    {
        var exact = Carrier();
        using (var previous = new SourceNativeProducerTests.Fixture(3))
        {
            await Ready(previous);
            previous.Invoke(() => { var original = Begin(previous); NativeSourceInputProvider.BindSubmitted(exact); NativeSourceInputProvider.Finish(original); });
            await previous.Close(); Passed(previous);
        }
        using var current = new SourceNativeProducerTests.Fixture(3); await Ready(current);
        current.Invoke(() =>
        {
            Assert.True(NativeSourceInputProvider.ObserveAccepted(exact)); Assert.True(current.Worker.Status.AccountingComplete);
            Assert.Equal(0, current.Worker.Status.Inputs); Assert.Equal(0, current.Worker.Status.PendingInputs);
            var original = Begin(current); NativeSourceInputProvider.Accepted(original); NativeSourceInputProvider.Finish(original);
        });
        await current.Close(); Passed(current);
        Assert.Equal("1", Assert.Single(current.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl")).InputPrefixOrdinal);
    }
    [Fact]
    public async Task SaturatedOriginalInputCapacityLeavesNativeBodiesRunningAndWithholdsSuccessfulAccounting()
    {
        using var f = new SourceNativeProducerTests.Fixture(3); await Ready(f);
        f.Invoke(() =>
        {
            for (int i = 0; i < 128; i++)
            { var original = Begin(f); NativeSourceInputProvider.BindSubmitted(Carrier()); NativeSourceInputProvider.Finish(original); f.Dispatches++; }
            Assert.Null(NativeSourceInputProvider.Begin(new("confirm", f.PhysicalOwner, null,
                new Dictionary<string, object>(StringComparer.Ordinal), "fixture.saturated_native_prefix", nameof(PlayCardAction), typeof(PlayCardAction))));
            f.Dispatches++;
        });
        await Assert.ThrowsAnyAsync<Exception>(() => f.Worker.Completion.WaitAsync(TimeSpan.FromSeconds(4)));
        Assert.Equal(129, f.Dispatches); Assert.False(f.Worker.Status.AccountingComplete);
        Assert.False(File.Exists(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")));
    }
}
