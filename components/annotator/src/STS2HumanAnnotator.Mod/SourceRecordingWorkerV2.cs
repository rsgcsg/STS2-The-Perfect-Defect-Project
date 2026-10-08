using System.Globalization;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

/// <summary>One bounded disk worker. Native callbacks admit immutable metadata only.</summary>
internal sealed class SourceRecordingWorkerV2 : INativeLogicalSourceSink, IDisposable
{
    private sealed class EpochWork(NativeLogicalSourceEpoch original)
    {
        internal readonly NativeLogicalSourceEpoch Original = original;
        internal string Cursor = original.Subscription.StartingCursor;
        internal SourceNativeSealV2? Seal;
        internal SourceNativeSealV2? Final;
        internal bool Released;
    }
    private sealed class Input(SourceInputTokenV2 token, string actionId, long deadline, string mechanism)
    {
        internal readonly SourceInputTokenV2 Token = token;
        internal readonly string ActionId = actionId;
        internal readonly long Deadline = deadline;
        internal readonly string Mechanism = mechanism;
        internal NativeLogicalSourceBasisMapping? Mapping;
        internal bool OrderReady;
        internal string? CaptureId, Missing;
        internal IDisposable? Retention, Budget;
        internal bool BasisReady, BasisBound, Complete;
        internal SourcePublicAction? Selected;
        internal NativeLogicalSourceInputTerminal? Terminal;
    }
    private readonly object metadataGate = new();
    private readonly RecordingSessionStore store;
    private readonly NativeLogicalSourceRecordingAttachment attachment;
    private readonly RecorderEnvironmentIdentity environment;
    private readonly Queue<Action> metadata = new();
    private readonly List<EpochWork> epochs = new();
    private readonly Dictionary<string, Input> inputs = new(StringComparer.Ordinal);
    private readonly CancellationTokenSource stopped = new();
    private bool paused, closing, disposed;
    private long closeDeadline;
    private string? failure;
    internal Task Completion { get; }
    public bool RequiresOrderedBasis => store.IsOrderedSourceSession;
    internal SourceSessionStatusV2 Status => store.GetSourceStatusV2()!;
    internal string? SnapshotId { get; private set; }

    internal SourceRecordingWorkerV2(RecordingSessionStore store, NativeLogicalSourceRecordingAttachment attachment,
        RecorderEnvironmentIdentity environment)
    {
        this.store = store; this.attachment = attachment; this.environment = environment;
        epochs.Add(new(attachment.InitialEpoch));
        attachment.Activate(this);
        Completion = Task.Run(Run);
    }
    internal static SourceNativePositionV2 Position(NativeLogicalSourcePosition p) => new(p.EpochId, p.StreamGeneration, p.PublicationIndex);
    internal static SourceNativeSealV2 Seal(NativeLogicalSourceSeal s) => new(s.EpochId, s.StreamGeneration, s.ReservedThrough, s.CompletedThrough);
    internal static SourceNativeTransitionV2 Transition(NativeLogicalSourceTransition t) => new(t.WitnessId, t.Kind, t.Mechanism,
        t.PreviousContinuityId, t.ContinuityId, t.StartProvenance, t.Graceful, t.Victory);
    internal static SourceEpochPacketV2 Packet(NativeLogicalSourceEpoch e, RecorderEnvironmentIdentity environment) => new(e.EpochId,
        e.PreviousEpochId, new(e.PublicationProfileId, e.PublicationProfileDefinitionSha256,
            e.Subscription.ScopeId, e.Subscription.StreamGeneration, e.Subscription.EagerScope,
            e.Subscription.Coverage.ToDictionary(x => x.SourceSeam, x => new SourceSeamCoverage(x.Version, x.Coverage), StringComparer.Ordinal),
            environment, e.ContinuityId), Position(e.StartingPosition), Position(e.InitialPosition),
        e.PredecessorSeal == null ? null : Seal(e.PredecessorSeal), e.Transition == null ? null : Transition(e.Transition));
    public void Epoch(NativeLogicalSourceEpoch epoch)
    {
        lock (metadataGate)
        {
            if (closing || disposed) { AccountingFailed("source_epoch_after_close"); return; }
            var admission = store.AdmitSourceEpochV2(Packet(epoch, environment));
            var previous = epochs[^1]; previous.Seal = epoch.PredecessorSeal == null ? null : Seal(epoch.PredecessorSeal);
            epochs.Add(new(epoch)); Enqueue(() => store.AppendSourceEpochV2(admission));
        }
    }
    public void Boundary(NativeLogicalSourceTransition transition, NativeLogicalSourcePosition position)
    {
        lock (metadataGate)
        {
            if (closing || disposed) return;
            var row = store.AdmitSourceBoundaryV2(transition.Kind, Position(position), transition: Transition(transition));
            Enqueue(() => store.AppendSourceBoundaryV2(row));
        }
    }
    public object? AdmitInput(NativeLogicalSourceInputPrefix prefix)
    {
        lock (metadataGate)
        {
            if (paused || closing || disposed || failure != null) return null;
            var token = store.ReserveSourceInputV2(prefix.RequestId, Position(prefix.PrePosition));
            var input = new Input(token, prefix.ActionId, prefix.EncodingDeadlineMonotonicMs, prefix.NativeMechanism);
            inputs.Add(token.InputId, input); return input;
        }
    }
    public void InputBasis(object originalToken, string? captureId, IDisposable? retainedInput, string? missingReason)
    {
        lock (metadataGate)
        {
            if (originalToken is not Input input || disposed || input.Complete || input.BasisBound
                || !inputs.TryGetValue(input.Token.InputId, out var issued) || !ReferenceEquals(issued, input))
            { retainedInput?.Dispose(); return; }
            if (Environment.TickCount64 >= input.Deadline)
            {
                // Deadline belongs to the original native prefix, not to when
                // the disk worker resumes or the encoder callback arrives.
                retainedInput?.Dispose();
                if (!input.BasisReady)
                { input.Missing = "source_input_basis_encoding_timeout"; input.BasisReady = true; }
                return;
            }
            if (input.BasisReady) { retainedInput?.Dispose(); AccountingFailed("source_input_basis_duplicate"); return; }
            input.CaptureId = captureId; input.Retention = retainedInput; input.Missing = missingReason; input.BasisReady = true;
        }
    }
    public void InputFrozen(object originalToken, NativeLogicalSourceBasisOrder order)
    {
        lock (metadataGate)
        {
            if (originalToken is not Input input || disposed || input.Complete
                || !inputs.TryGetValue(input.Token.InputId, out var issued) || !ReferenceEquals(issued, input)) return;
            store.SealSourceInputOrderV3(input.Token, new(order.Status, order.ReasonCode)); input.OrderReady = true;
        }
    }
    public void InputMapping(object originalToken, NativeLogicalSourceBasisMapping mapping)
    {
        lock (metadataGate)
        {
            if (originalToken is not Input input || disposed || input.Complete
                || !inputs.TryGetValue(input.Token.InputId, out var issued) || !ReferenceEquals(issued, input)) return;
            if (input.Mapping != null && input.Mapping != mapping) { AccountingFailed("source_input_mapping_changed"); return; }
            input.Mapping = mapping;
        }
    }
    public void InputTerminal(object originalToken, NativeLogicalSourceInputTerminal terminal)
    {
        lock (metadataGate)
        {
            if (originalToken is not Input input || disposed || input.Complete
                || !inputs.TryGetValue(input.Token.InputId, out var issued) || !ReferenceEquals(issued, input)) return;
            if (input.Terminal != null) { AccountingFailed("source_input_terminal_duplicate"); return; }
            input.Terminal = terminal;
        }
    }
    public void AccountingFailed(string code)
    {
        lock (metadataGate) failure ??= code;
        store.MarkSourceV2AccountingFailed(code); // Pure metadata failure; no disk gate is acquired by native observers.
    }
    private void Enqueue(Action write)
    {
        if (metadata.Count >= 32) { AccountingFailed("source_metadata_packet_capacity"); return; }
        metadata.Enqueue(write);
    }
    internal void CommandBoundary(string kind)
    {
        lock (metadataGate)
        {
            var boundary = attachment.ReadCommandBoundary();
            IReadOnlyList<SourceNativeSealV2>? seals = null;
            if (kind == "close")
            {
                seals = attachment.Close().Select(Seal).ToArray();
                epochs[^1].Seal = seals.Single(); closing = true;
                closeDeadline = checked(Math.Max(Environment.TickCount64, boundary.EncodingDeadlineMonotonicMs) + 3000);
            }
            var row = store.AdmitSourceBoundaryV2(kind, Position(boundary.Position), seals);
            if (kind == "pause") paused = true;
            if (kind == "resume") paused = false;
            Enqueue(() => store.AppendSourceBoundaryV2(row));
        }
    }
    internal void ChangeSource(SourceDeclaration source, string expectedSegment)
    {
        lock (metadataGate)
        {
            var row = store.AdmitSourceDeclarationV2(source, expectedSegment, Position(attachment.ReadCommandBoundary().Position));
            Enqueue(() => store.AppendSourceDeclarationV2(row));
        }
    }
    private async Task Run()
    {
        try
        {
            while (!stopped.IsCancellationRequested)
            {
                DrainMetadata();
                if (failure != null) throw new InvalidOperationException(failure);
                attachment.Renew();
                EpochWork[] views; Input[] pending;
                lock (metadataGate) { views = epochs.ToArray(); pending = inputs.Values.Where(x => !x.Complete).ToArray(); }
                foreach (var epoch in views.Where(x => !x.Released)) DrainEpoch(epoch);
                foreach (var input in pending) DrainInput(input);
                lock (metadataGate)
                    foreach (var input in pending.Where(x => x.Complete)) inputs.Remove(input.Token.InputId);
                bool ready;
                lock (metadataGate) ready = closing && metadata.Count == 0 && epochs.All(x => x.Final != null)
                    && (inputs.Values.All(x => x.Complete) || Environment.TickCount64 >= closeDeadline);
                if (ready)
                {
                    foreach (var input in pending.Where(x => !x.Complete)) CloseInput(input);
                    store.PrepareSourceCloseV2(epochs.Select(x => x.Final!).ToArray());
                    store.Dispose(); return;
                }
                if (closing && Environment.TickCount64 >= closeDeadline && epochs.Any(x => x.Final == null))
                    throw new InvalidDataException("source_original_close_drain_timeout");
                await Task.Delay(10, stopped.Token).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException) when (stopped.IsCancellationRequested) { }
        catch (Exception exception)
        {
            AccountingFailed(exception is InvalidDataException ? exception.Message : failure ?? "source_worker_failed");
            try { store.AbortSourceV2(failure!); } finally { attachment.Dispose(); }
            throw;
        }
        finally
        {
            lock (metadataGate)
            {
                foreach (var input in inputs.Values) { input.Retention?.Dispose(); input.Budget?.Dispose(); }
            }
        }
    }
    private void DrainMetadata()
    {
        while (true)
        {
            Action? write;
            lock (metadataGate) write = metadata.Count == 0 ? null : metadata.Dequeue();
            if (write is null) return;
            write(); // The metadata gate is never held across disk I/O.
        }
    }
    private void DrainEpoch(EpochWork epoch)
    {
        var batch = attachment.Events(epoch.Original, epoch.Cursor);
        if (batch.Gap is { } gap)
        {
            ulong from = ulong.Parse(gap.FromPublicationIndex, CultureInfo.InvariantCulture);
            var position = new SourceNativePositionV2(epoch.Original.EpochId, epoch.Original.Subscription.StreamGeneration, gap.ThroughPublicationIndex);
            store.AppendPublicObservationV2(new(position, "source_gap", "0", "retention_overflow", null, null,
                epoch.Original.ContinuityId, "failed", null, null, gap.Reason, (from - 1).ToString(CultureInfo.InvariantCulture)));
        }
        foreach (var availability in batch.Events)
        {
            var occurrence = availability.Event;
            var position = new SourceNativePositionV2(epoch.Original.EpochId, occurrence.StreamGeneration, occurrence.PublicationIndex);
            FrozenPublicCaptureV2? capture = null; FrozenPublicCatalogV2? catalog = null;
            string? missing = occurrence.MissingReason;
            IDisposable? budget = null;
            try
            {
                if (occurrence.CaptureRef is { } captureId && availability.Availability == "available"
                    && !store.IsSourceObservationPausedV2(position))
                {
                    var copy = attachment.CopyFrozen(captureId); budget = copy;
                    (capture, catalog) = Copies(epoch.Original.EpochId, copy.Value);
                }
                else if (occurrence.CaptureRef != null && !store.IsSourceObservationPausedV2(position)) missing ??= "payload_expired";
                store.AppendPublicObservationV2(new(position, occurrence.SourceSeam, occurrence.SourceIndex, occurrence.SourcePhase,
                    occurrence.PayloadReference?.SnapshotId, null, epoch.Original.ContinuityId,
                    capture != null && catalog != null ? "complete" : "failed", capture, catalog, capture != null && catalog != null ? null : missing ?? "source_payload_missing"));
                SnapshotId = occurrence.PayloadReference?.SnapshotId;
            }
            catch (NativeLogicalException exception) when (exception.Code is "source_copied_payload_capacity" or "payload_expired" or "not_captured")
            {
                store.AppendPublicObservationV2(new(position, occurrence.SourceSeam, occurrence.SourceIndex, occurrence.SourcePhase,
                    occurrence.PayloadReference?.SnapshotId, null, epoch.Original.ContinuityId, "failed", null, null, exception.Code));
            }
            finally { budget?.Dispose(); }
        }
        // Acknowledgement follows successful disk append/paused accounting only. Original hub pins remain otherwise.
        attachment.Acknowledge(epoch.Original, batch.NextCursor); epoch.Cursor = batch.NextCursor;
        SourceNativeSealV2? seal; lock (metadataGate) seal = epoch.Seal;
        if (seal != null)
        {
            var boundary = attachment.ReadEpochBoundary(epoch.Original);
            ulong reserved = ulong.Parse(seal.ReservedThrough, CultureInfo.InvariantCulture);
            if (ulong.Parse(boundary.CompletedThrough, CultureInfo.InvariantCulture) >= reserved && batch.NextCursor == batch.HighWatermark)
            {
                // Completion may advance between Events/Acknowledge and the
                // fresh boundary read. Only the Hub's atomic original ack+seal
                // check can retire this view; an older batch is not proof.
                try
                {
                    if (attachment.ReleaseEpoch(epoch.Original))
                    { epoch.Final = seal with { CompletedThrough = boundary.CompletedThrough }; epoch.Released = true; }
                }
                catch (NativeLogicalException error) when (error.Code == "source_epoch_not_drained") { }
            }
        }
    }
    private static (FrozenPublicCaptureV2 Capture, FrozenPublicCatalogV2 Catalog) Copies(string epoch, NativeLogicalFrozenInput frozen)
    {
        var descriptor = frozen.Capture; var relation = frozen.Catalog ?? throw new InvalidDataException("source_complete_catalog_missing");
        return (new(epoch, new(descriptor.CaptureId, descriptor.SnapshotId, descriptor.ScopeId, descriptor.StreamGeneration,
            descriptor.CapturedAt, frozen.CopyCaptureBytes(), descriptor.Sha256)),
            new(epoch, new(relation.Descriptor.CatalogRef, relation.Descriptor.SnapshotId, relation.Descriptor.ScopeId,
                relation.Descriptor.StreamGeneration, checked((int)relation.Descriptor.TotalCount!.Value), relation.Descriptor.Digest!, relation.CopyBytes(), relation.PayloadSha256)));
    }
    private void DrainInput(Input input)
    {
        lock (metadataGate)
        {
            if (!input.BasisReady && Environment.TickCount64 >= input.Deadline)
            { input.Missing = "source_input_basis_encoding_timeout"; input.BasisReady = true; }
            if (!input.BasisReady || RequiresOrderedBasis && !input.OrderReady) return;
        }
        if (!input.BasisBound)
        {
            FrozenPublicCaptureV2? capture = null; FrozenPublicCatalogV2? catalog = null;
            if (input.CaptureId != null)
            {
                try
                {
                    var copy = attachment.CopyFrozen(input.CaptureId); input.Budget = copy;
                    (capture, catalog) = Copies(input.Token.PrePosition.EpochId, copy.Value);
                    var actionId = input.Mapping?.ActionId ?? (input.ActionId.Length == 0 ? null : input.ActionId);
                    var matches = SourceCatalogCodec.Decode(catalog.Catalog.Bytes).Where(x => x.ActionId == actionId).ToArray();
                    if (input.Mapping?.MappingStatus == "exact" || input.Mapping == null)
                    {
                        if (matches.Length != 1) throw new InvalidDataException("source_original_selected_action_membership_missing");
                        input.Selected = matches[0];
                    }
                }
                catch (NativeLogicalException exception) when (exception.Code == "source_copied_payload_capacity")
                {
                    if (Environment.TickCount64 < input.Deadline) return;
                    input.Missing = exception.Code;
                }
                catch (NativeLogicalException exception) when (exception.Code is "payload_expired" or "not_captured") { input.Missing = exception.Code; }
            }
            store.BindSourceInputBasisV2(input.Token, capture, catalog); input.BasisBound = true;
            input.Retention?.Dispose(); input.Retention = null;
        }
        NativeLogicalSourceInputTerminal? terminal; lock (metadataGate) terminal = input.Terminal;
        if (terminal is null) return;
        string mapping = input.CaptureId == null || input.Missing != null ? "capture_missing" : input.Mapping?.MappingStatus ?? "exact";
        var outcome = new SourceInputOutcomeV2(mapping, mapping == "capture_missing" ? 0 : input.Mapping?.MatchCount ?? 1,
            mapping == "exact" ? input.Selected : null, input.Mechanism, terminal.Delivery, terminal.Reason ?? input.Missing,
            terminal.Stages.Select(x => new SourceInputStageV2(x.Stage, x.Delivery, x.Evidence)).ToArray());
        store.CompleteSourceInputV2(input.Token, outcome); input.Complete = true; input.Budget?.Dispose(); input.Budget = null;
    }
    private void CloseInput(Input input)
    {
        if (!input.BasisBound) { store.BindSourceInputBasisV2(input.Token, null, null); input.BasisBound = true; }
        store.CompleteSourceInputV2(input.Token, new(input.Selected == null ? "capture_missing" : "exact", input.Selected == null ? 0 : 1,
            input.Selected, input.Mechanism, "unknown", "session_closed_before_input_completion", Array.Empty<SourceInputStageV2>()));
        input.Complete = true; input.Retention?.Dispose(); input.Retention = null; input.Budget?.Dispose(); input.Budget = null;
    }
    public void Dispose()
    {
        lock (metadataGate) { if (disposed) return; disposed = true; stopped.Cancel(); }
        attachment.Dispose();
    }
}
