using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment.Witness;
using STS2Platform.NativeFoundation;

namespace STS2Connector.PlayerEnvironment;

internal sealed partial class NativeLogicalService
{
    private readonly object sourceGate = new();
    private long sourceCopiedAndScratchBytes;
    private const long MaxSourceCopies = 128L * 1024 * 1024, SourceEncodingScratch = 64L * 1024 * 1024;
    private const int SourceInputEncodingBytes = 1024 * 1024;
    private NativeLogicalSourceRecordingAttachment? sourceRecorder;
    private NativeLogicalSourceTransition? sourceTransition;
    private NativeLogicalSourceSeal? sourcePredecessorSeal;
    private bool sourceEpochTransitioning;
    private sealed record SourceInput(NativeLogicalSourceRecordingAttachment Owner, PlayerEnvironmentActionRequest Request, object? Token);
    private readonly Dictionary<string, SourceInput> sourceInputs = new(StringComparer.Ordinal);
    private static readonly IReadOnlyList<string> SourceScope = Array.AsReadOnly(new[] { "persistent", "interaction", "referents", "catalog" });

    internal NativeLogicalSourceRecordingAttachment AttachSource()
    {
        SynchronizeRun();
        if (sourceRecorder is not null) throw new NativeLogicalException("source_attachment_capacity", "An original source attachment is still retained.");
        var declaration = Hub.ReadDeclaration();
        if (declaration.PublicationProfileId is null || declaration.PublicationProfileDefinitionSha256 is null)
            throw new NativeLogicalException("source_publication_profile_unavailable", "Source recording requires its actual installed publication profile.");
        // Only an explicit new recording attachment registers a new original client.
        // Automatic epoch turnover reuses recorder.ClientId and never revives a closed owner.
        var sourceClient = registerSourceClient(new(NativeLogicalWire.Id("source_client_instance"), "sts2-platform-source-recorder", "Platform passive Source recorder", "2"));
        string client = sourceClient.Client.ClientSessionId;
        var attached = Hub.AttachSource(SourceRequest(client, declaration.Coverage), Bootstrap);
        var epoch = SourceEpoch(attached, declaration, runId, null, null, null);
        var identity = sourceIdentity();
        var recorder = new NativeLogicalSourceRecordingAttachment(this, attached.RegistrationId, client, epoch, identity.Capabilities, identity.SourceDigest);
        sourceRecorder = recorder;
        CaptureReservation(attached.InitialReservation); // Original N+1 retained before Activate; no sink runs yet.
        return recorder;
    }
    private static NativeLogicalAttachRequest SourceRequest(string client, IReadOnlyList<NativeLogicalSeamCoverage> coverage) =>
        new(client, SourceScope, Array.AsReadOnly(coverage.Where(s => s.Coverage != "unsupported").ToArray()), "full_reference");
    private static NativeLogicalSourceEpoch SourceEpoch(NativeLogicalSourceHubAttachment attached,
        NativeLogicalPublicationDeclaration declaration, string? continuityId, string? previous,
        NativeLogicalSourceSeal? seal, NativeLogicalSourceTransition? transition)
    {
        string id = NativeLogicalWire.Id("source_epoch");
        return new(id, previous, attached.Subscription, declaration.PublicationProfileId!, declaration.PublicationProfileDefinitionSha256!, continuityId,
            new(id, attached.Subscription.StreamGeneration, attached.StartingBoundary.StartingIndex),
            new(id, attached.Subscription.StreamGeneration, attached.InitialReservation.PublicationIndex), seal, transition);
    }
    internal void ActivateSource(NativeLogicalSourceRecordingAttachment recorder, INativeLogicalSourceSink sink)
    {
        AssertMainThread(); ArgumentNullException.ThrowIfNull(sink);
        lock (sourceGate)
        {
            ExactSource(recorder); if (recorder.Failure is not null) throw new NativeLogicalException(recorder.Failure, "Original source admission failed before activation.");
            if (recorder.Sink is not null) throw new NativeLogicalException("source_already_activated", "The sink activates once.");
            recorder.Sink = sink;
        }
    }
    private void ExactSource(NativeLogicalSourceRecordingAttachment recorder)
    {
        if (!ReferenceEquals(sourceRecorder, recorder) || recorder.Disposed)
            throw new NativeLogicalException("source_attachment_expired", "Only the exact original recorder may access its retained epochs.");
    }
    private void ExactSourceEpoch(NativeLogicalSourceRecordingAttachment recorder, NativeLogicalSourceEpoch original)
    {
        ExactSource(recorder);
        if (!recorder.IssuedEpochs.TryGetValue(original.EpochId, out var issued) || !ReferenceEquals(issued, original))
            throw new NativeLogicalException("source_epoch_mismatch", "Only the owner's original immutable epoch object can address its native positions.");
    }
    private static NativeLogicalSourceBoundary SourceBoundary(NativeLogicalSourceEpoch epoch, NativeLogicalSourceHubBoundary boundary) =>
        new(new(epoch.EpochId, boundary.StreamGeneration, boundary.ReservedThrough), boundary.CompletedThrough, boundary.EncodingDeadlineMonotonicMs);
    internal NativeLogicalSourceBoundary ReadSourceBoundary(NativeLogicalSourceRecordingAttachment recorder, NativeLogicalSourceEpoch? original = null)
    {
        lock (sourceGate)
        {
            ExactSource(recorder); var epoch = original ?? recorder.Current; ExactSourceEpoch(recorder, epoch);
            return SourceBoundary(epoch, Hub.ReadSourceBoundary(recorder.RegistrationId, epoch.Subscription.SubscriptionId));
        }
    }
    internal NativeLogicalEventBatch SourceEvents(NativeLogicalSourceRecordingAttachment recorder, NativeLogicalSourceEpoch original, string cursor)
    { lock (sourceGate) { ExactSourceEpoch(recorder, original); return Hub.SourceEvents(recorder.RegistrationId, original.Subscription.SubscriptionId, cursor); } }
    internal Task WaitSource(NativeLogicalSourceRecordingAttachment recorder, NativeLogicalSourceEpoch original, string cursor, CancellationToken cancellation)
    { lock (sourceGate) { ExactSourceEpoch(recorder, original); return Hub.WaitSourceProgressAsync(recorder.RegistrationId, original.Subscription.SubscriptionId, cursor, cancellation); } }
    internal void AcknowledgeSource(NativeLogicalSourceRecordingAttachment recorder, NativeLogicalSourceEpoch original, string cursor)
    { lock (sourceGate) { ExactSourceEpoch(recorder, original); Hub.AcknowledgeSource(recorder.RegistrationId, original.Subscription.SubscriptionId, cursor); } }
    internal bool ReleaseSourceEpoch(NativeLogicalSourceRecordingAttachment recorder, NativeLogicalSourceEpoch original)
    { lock (sourceGate) { ExactSourceEpoch(recorder, original); return Hub.ReleaseSourceEpoch(recorder.RegistrationId, original.Subscription.SubscriptionId); } }
    internal void RenewSource(NativeLogicalSourceRecordingAttachment recorder)
    {
        string? failure;
        lock (sourceGate)
        {
            ExactSource(recorder);
            if (sourceEpochTransitioning) return; // The native owner is between its exact seal and successor Attach; no lease renewal is inferred.
            failure = Hub.SourceFailure(recorder.RegistrationId);
            if (failure is null && !recorder.Closing && recorder.Failure is null)
            {
                try { Hub.RenewSource(recorder.RegistrationId, recorder.Current.Subscription.SubscriptionId); }
                catch (NativeLogicalException) when (Hub.SourceFailure(recorder.RegistrationId) is not null) { }
                failure = Hub.SourceFailure(recorder.RegistrationId);
            }
        }
        if (failure is not null) FailSource(recorder, failure);
    }
    private IDisposable ReserveSourceBytes(long charge)
    {
        lock (sourceGate)
        {
            if (charge <= 0 || charge > MaxSourceCopies - sourceCopiedAndScratchBytes)
                throw new NativeLogicalException("source_copied_payload_capacity", "Copied payload and encoding/parser scratch are charged before allocation.");
            sourceCopiedAndScratchBytes += charge;
            return new SourceInputRetention(() => { lock (sourceGate) sourceCopiedAndScratchBytes -= charge; });
        }
    }
    internal NativeLogicalSourceCopy CopySourceFrozen(NativeLogicalSourceRecordingAttachment recorder, string captureId)
    {
        IDisposable budget;
        lock (sourceGate)
        {
            ExactSource(recorder);
            var original = Store.FrozenExportMetadata(captureId);
            if (!recorder.IssuedEpochs.Values.Any(epoch => epoch.Subscription.ScopeId == original.Descriptor.ScopeId
                && epoch.Subscription.StreamGeneration == original.Descriptor.StreamGeneration))
                throw new NativeLogicalException("source_capture_scope_mismatch", "Only this original recorder's issued epoch scopes may be copied.");
            // Covers ExportFrozen wrappers, consumer ownership copies, Store clones and all catalog/capture validation scratch.
            // Oversize records become explicit original-position missing outcomes; they are never copied then measured.
            budget = ReserveSourceBytes(checked(original.ByteCount * 64L));
        }
        // Decoder/copy work never holds the gate needed by a native input prefix or lifecycle callback.
        try
        {
            var value = Store.ExportFrozen(captureId);
            lock (sourceGate) ExactSource(recorder);
            return new(value, budget);
        }
        catch { budget.Dispose(); throw; }
    }
    internal IReadOnlyList<NativeLogicalSourceSeal> CloseSource(NativeLogicalSourceRecordingAttachment recorder)
    {
        AssertMainThread();
        lock (sourceGate)
        {
            ExactSource(recorder);
            var current = recorder.Current;
            var seal = Hub.SealSource(recorder.RegistrationId, current.Subscription.SubscriptionId, close: true);
            recorder.Closing = true;
            return Array.AsReadOnly(new[] { new NativeLogicalSourceSeal(current.EpochId, seal.StreamGeneration, seal.ReservedThrough, seal.CompletedThrough) });
        }
    }
    internal void DisposeSource(NativeLogicalSourceRecordingAttachment recorder)
    {
        lock (sourceGate)
        {
            if (recorder.Disposed) return;
            ExactSource(recorder); recorder.Disposed = true; Hub.DisposeSource(recorder.RegistrationId); sourceRecorder = null;
            foreach (var key in sourceInputs.Where(x => ReferenceEquals(x.Value.Owner, recorder)).Select(x => x.Key).ToArray()) sourceInputs.Remove(key);
        }
    }
    private void ObserveSourceAccountingFailure(string code) { if (sourceRecorder is { } recorder) FailSource(recorder, code); }
    private static void FailSource(NativeLogicalSourceRecordingAttachment recorder, string code)
    {
        string originalFailure = Interlocked.CompareExchange(ref recorder.Failure, code, null) ?? code;
        try { recorder.Sink?.AccountingFailed(originalFailure); } catch { /* A passive failure never changes native behavior. */ }
    }
    private void ObserveSourceLifecycle(NativeRunLifecycleObservation observation)
    {
        if (sourceRecorder is not { Closing: false, Failure: null } recorder) return;
        try
        {
            AssertMainThread();
            if (!ReferenceEquals(observation.State, actualRunState()))
                throw new InvalidOperationException("source_native_observation_not_actual_state");
            string? actual = continuity();
            sourceTransition = new(observation.WitnessId, observation.Kind, observation.Mechanism,
                runId, actual, observation.StartProvenance, observation.Graceful, observation.Victory);
            SynchronizeRun();
            if (sourceTransition is { } sameEpoch)
            {
                if (sameEpoch.Kind is "launch" or "terminal")
                    recorder.Sink?.Boundary(sameEpoch, ReadSourceBoundary(recorder).Position);
                else if (sameEpoch.Kind != "cleanup") FailSource(recorder, "source_setup_actual_continuity_unchanged");
            }
        }
        catch { FailSource(recorder, "source_native_lifecycle_failed"); }
        finally { sourceTransition = null; sourcePredecessorSeal = null; }
    }
    private void SourceBeforeGenerationChange()
    {
        if (sourceRecorder is not { Closing: false, Failure: null } recorder) return;
        try
        {
            var original = recorder.Current;
            var seal = Hub.SealSource(recorder.RegistrationId, original.Subscription.SubscriptionId);
            sourcePredecessorSeal = new(original.EpochId, seal.StreamGeneration, seal.ReservedThrough, seal.CompletedThrough);
        }
        catch { FailSource(recorder, "source_original_epoch_seal_failed"); }
    }
    private void SourceAfterGenerationChange(string? next)
    {
        if (sourceRecorder is not { Closing: false, Failure: null } recorder) return;
        try
        {
            if (sourceTransition is not { Kind: "setup_handoff" or "cleanup" } transition || sourcePredecessorSeal is null)
            { FailSource(recorder, "source_native_epoch_transition_witness_missing"); return; }
            NativeLogicalSourceHubAttachment attached;
            NativeLogicalSourceEpoch epoch;
            lock (sourceGate)
            {
                ExactSource(recorder);
                if (recorder.IssuedEpochs.Count >= 256)
                    throw new NativeLogicalException("source_epoch_capacity", "An original source attachment admits at most 256 epochs.");
                var declaration = Hub.ReadDeclaration();
                attached = Hub.AttachSourceEpoch(recorder.RegistrationId, SourceRequest(recorder.ClientId, declaration.Coverage), Bootstrap);
                epoch = SourceEpoch(attached, declaration, next, recorder.Current.EpochId, sourcePredecessorSeal, transition);
                recorder.Current = epoch; recorder.IssuedEpochs.Add(epoch.EpochId, epoch);
            }
            recorder.Sink?.Epoch(epoch); // Pure admission before any initial reservation encoder or input callback.
            sourceTransition = null; sourcePredecessorSeal = null;
            CaptureReservation(attached.InitialReservation);
        }
        catch (NativeLogicalException error) { FailSource(recorder, error.Code); }
        catch { FailSource(recorder, "source_successor_epoch_admission_failed"); }
    }

    // RequestLifetime owns these calls. The original provenance map is bounded and contains no result/action DTO graph.
    partial void ObserveOriginalInputPrefix(PlayerEnvironmentActionRequest originalRequest) => CaptureSourceInputPrefix(originalRequest);
    partial void ObserveOriginalInputTerminal(PlayerEnvironmentActionRequest originalRequest, NativeLogicalResult sealedResult) =>
        CaptureSourceInputTerminal(originalRequest, sealedResult);

    private void CaptureSourceInputPrefix(PlayerEnvironmentActionRequest request)
    {
        if (sourceRecorder is not { Closing: false, Failure: null } recorder) return;
        try
        {
            AssertMainThread();
            var boundary = ReadSourceBoundary(recorder);
            long encodingDeadline = checked(Environment.TickCount64 + Limits.EncodingDeadlineMs);
            object? token = recorder.Sink?.AdmitInput(new(request.RequestId!, request.ClientSessionId!, request.BoundActionId!, boundary.Position, encodingDeadline));
            bool provenanceCapacityExceeded;
            lock (sourceGate)
            {
                provenanceCapacityExceeded = sourceInputs.Count >= 128;
                if (!provenanceCapacityExceeded) sourceInputs.Add(request.RequestId!, new(recorder, request, token));
            }
            if (provenanceCapacityExceeded) { FailSource(recorder, "source_input_provenance_capacity"); return; }
            if (recorder.Sink is null) { FailSource(recorder, "source_input_before_activation"); return; }
            if (token is null) return; // Explicit paused/closing/excluded admission; retain only its original marker.
            Prepared prepared;
            lock (basisGate) prepared = new(currentFacts ?? throw new InvalidOperationException("source_input_pre_facts_missing"), runId, DateTimeOffset.UtcNow);
            IDisposable scratch;
            try { scratch = ReserveSourceBytes(SourceEncodingScratch); }
            catch (NativeLogicalException) { recorder.Sink.InputBasis(token, null, null, "source_copied_payload_capacity"); return; }
            if (!encodingAdmission.Wait(0)) { scratch.Dispose(); recorder.Sink.InputBasis(token, null, null, "encoding_capacity_exceeded"); return; }
            var originalEpoch = recorder.Current;
            Enqueue(() =>
            {
                string? captureId = null; IDisposable? retention = null;
                try
                {
                    if (Environment.TickCount64 >= encodingDeadline)
                    { recorder.Sink.InputBasis(token, null, null, "source_input_basis_encoding_timeout"); return; }
                    var projection = projector.Capture(prepared.Facts, SourceScope, originalEpoch.Subscription.ScopeId,
                        prepared.Time, Environment.TickCount64 + Limits.RetentionMs, () => Environment.TickCount64, Store, SourceInputEncodingBytes);
                    captureId = projection.Capture.CaptureId;
                    if (Environment.TickCount64 >= encodingDeadline)
                    { recorder.Sink.InputBasis(token, null, null, "source_input_basis_encoding_timeout"); return; }
                    AcceptBasis(prepared, projection.Catalog);
                    string handle = Store.Retain("source-input:" + recorder.RegistrationId, captureId);
                    retention = new SourceInputRetention(() => Store.Release("source-input:" + recorder.RegistrationId, handle));
                    recorder.Sink.InputBasis(token, captureId, retention, null); retention = null;
                }
                catch { recorder.Sink.InputBasis(token, null, null, "source_input_basis_encoding_failed"); }
                finally { retention?.Dispose(); if (captureId is not null) Store.ReleaseCapture(captureId); scratch.Dispose(); }
            });
        }
        catch { FailSource(recorder, "source_input_prefix_admission_failed"); }
    }
    private sealed class SourceInputRetention(Action release) : IDisposable
    { private Action? callback = release; public void Dispose() => Interlocked.Exchange(ref callback, null)?.Invoke(); }
    private void CaptureSourceInputTerminal(PlayerEnvironmentActionRequest request, NativeLogicalResult result)
    {
        SourceInput? input;
        lock (sourceGate)
        {
            if (!sourceInputs.TryGetValue(request.RequestId ?? "", out input)
                || !ReferenceEquals(input.Request, request)) return;
            sourceInputs.Remove(request.RequestId!);
        }
        if (input.Token is null || input.Owner.Disposed) return;
        try
        {
            if (result.Delivery == "not_started") { FailSource(input.Owner, "source_admitted_input_not_started"); return; }
            NativeLogicalWire.Text(result.Delivery, 128);
            if (result.Reason is not null) NativeLogicalWire.Text(result.Reason, 128);
            if (result.Stages.Count > 16) throw new InvalidOperationException("source_input_stage_capacity");
            var stages = result.Stages.Select(s =>
            {
                NativeLogicalWire.Text(s.Stage, 128); NativeLogicalWire.Text(s.Delivery, 128); NativeLogicalWire.Text(s.Evidence, 128); return s with { };
            }).ToArray();
            input.Owner.Sink?.InputTerminal(input.Token, new(result.Delivery, result.Reason, Array.AsReadOnly(stages)));
        }
        catch { FailSource(input.Owner, "source_input_terminal_metadata_failed"); }
    }
}
