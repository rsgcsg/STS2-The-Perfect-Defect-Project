using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Platform.NativeFoundation;

namespace STS2Connector.PlayerEnvironment;

internal static partial class PlayerEnvironmentService
{
    private static readonly Lazy<NativeLogicalService> NativeLogicalOwner = new(CreateNativeLogicalOwner);
    private static NativeLogicalService CreateNativeLogicalOwner() => new(
        CaptureNativeLogicalFrame, () => RunManager.Instance.DebugOnlyGetState() is { } run
            ? Entities.GetId(run, "run") : null, SubmissionGate, RequestFingerprints);
    internal static NativeLogicalService NativeLogical => NativeLogicalOwner.Value;
    internal static void InitializeNativeLogical() => NativeLogical.Initialize();
    internal static NativeLogicalCapabilities GetNativeLogicalCapabilities()
    {
        var game = EnvironmentIdentityRuntime.ReadGame();
        var host = EnvironmentIdentityRuntime.HostIdentity();
        var publication = NativeLogical.Hub.ReadDeclaration();
        return new(PlayerEnvironmentContract.ProtocolVersion, NativeLogicalContract.CapabilitiesSchema,
            NativeLogicalContract.Profile, ToHostIdentity(host), ToGameIdentity(game), ToSessionReference(host, game),
            publication.StreamGeneration,
            new[] { "capabilities", "current", "read", "catalog", "resolve", "attach", "events", "await", "cancel_wait", "detach", "renew", "retain", "release", "submit", "result" },
            new[] { "native_current_frame" }.Concat(publication.Coverage.Where(s => s.Coverage != "unsupported")
                .Select(s => s.SourceSeam)).Distinct(StringComparer.Ordinal).ToArray(),
            publication.Coverage,
            new("u64_decimal_string", "u64_decimal_string", "native_source_occurrence", "contiguous_completed_projections_not_causal_commit_order"),
            NativeLogical.Limits, new(MutationControlRuntime.Capability().RecommendedRenewalMs),
            new[] { "Coverage describes implemented source seams, not all L01-L64 obligations or runtime qualification.",
                "Input delivery never proves execution, Commit, effects or causal successors.",
                "Incomplete native sources remain explicit missing positions; Current cannot backfill history.",
                "Unimplemented native exposure families remain unsupported; no polling completeness is claimed.",
                "At most four pending or active native capture encodings are admitted; this is not a measured performance qualification." });
    }
}

// One native owner. Only the game thread touches current private closures;
// the serial encoder receives detached public DTOs and private binding strings.
internal sealed partial class NativeLogicalService : IDisposable
{
    internal const string Bootstrap = "connector_initial_observation";
    internal static readonly IReadOnlyList<NativeLogicalSeamCoverage> Coverage = Array.AsReadOnly(new[]
    {
        new NativeLogicalSeamCoverage(Bootstrap, "1", "complete_at_seam"),
        new NativeLogicalSeamCoverage("native_owner_ready", "1", "sampled"),
        new NativeLogicalSeamCoverage("native_target_focus", "1", "sampled"),
        new NativeLogicalSeamCoverage("native_card_preview", "1", "sampled"),
        new NativeLogicalSeamCoverage("native_inspect_preview", "1", "sampled"),
        new NativeLogicalSeamCoverage("native_input_callback", "1", "sampled"),
        new NativeLogicalSeamCoverage("connector_input_start", "1", "sampled"),
        new NativeLogicalSeamCoverage("native_terminal_entry", "1", "sampled"),
        new NativeLogicalSeamCoverage("other_native_exposures", "1", "unsupported")
    });
    internal NativeLogicalLimits Limits { get; } = new();
    internal NativeLogicalCaptureStore Store { get; }
    internal NativeLogicalPublicationHub Hub { get; }
    private readonly NativeLogicalProjector projector;
    private readonly Func<TextMenuFrame> capture;
    private readonly Func<string?> continuity;
    private readonly object basisGate = new();
    private readonly SemaphoreSlim encodingAdmission = new(4, 4);
    private readonly ConcurrentQueue<Action> encodings = new();
    private int encodingWorker;
    private int mainThread;
    private bool initialized, runKnown;
    private string? runId, ownerKey, focusedId;
    private string ownerId = NativeLogicalWire.Id("owner"), occurrence = NativeLogicalWire.Id("occurrence");
    private string bindingRevision = NativeLogicalWire.Id("binding"), focusOccurrence = NativeLogicalWire.Id("focus");
    private TextMenuFrame? currentNative;
    private NativeLogicalPublicFrame? currentFacts, projectedBasis;
    private IReadOnlyDictionary<string, string> currentActionKeys = new Dictionary<string, string>();
    private string? projectedSnapshot;
    private readonly NativeLogicalExecutor executor;
    internal Func<bool> ExecutionAllowed { get; }
    private readonly Func<Func<Prepared>, CancellationToken, Task<Prepared>> nativeQueue;

    internal NativeLogicalService(Func<TextMenuFrame> capture, Func<string?> continuity,
        object submissionGate, ConcurrentDictionary<string, string> fingerprints,
        Func<Func<Prepared>, CancellationToken, Task<Prepared>>? nativeQueue = null, Func<bool>? executionAllowed = null,
        Func<MutationAuthorizationRequest, MutationAdmission>? begin = null)
    {
        this.capture = capture; this.continuity = continuity;
        ExecutionAllowed = executionAllowed ?? (() => EnvironmentIdentityRuntime.ExecutionAvailable(EnvironmentIdentityRuntime.ReadGame()));
        this.nativeQueue = nativeQueue ?? ((work, cancellation) => ConnectorMod.RunOnMainThread(work, cancellation));
        Store = new(limits: Limits); projector = new(Limits);
        Hub = new(Store, Coverage, limits: Limits, controls: new ControlDependency());
        executor = new(submissionGate, fingerprints, this, begin);
    }
    internal void Initialize()
    {
        if (initialized) return;
        mainThread = Environment.CurrentManagedThreadId; initialized = true;
        NativeDecisionOwnerReadyProvider.Observed += ObserveOwnerReady;
    }
    private void AssertMainThread()
    {
        if (!initialized || Environment.CurrentManagedThreadId != mainThread)
            throw new InvalidOperationException("Native logical capture requires the initialized game main thread.");
    }
    private void SynchronizeRun()
    {
        AssertMainThread();
        string? next = continuity();
        if (runKnown && runId != next)
        {
            Hub.ChangeGeneration();
            lock (basisGate) { projectedBasis = null; projectedSnapshot = null; currentActionKeys = new Dictionary<string, string>(); currentFacts = null; }
            ownerKey = null;
        }
        runKnown = true; runId = next;
    }
    internal sealed record Prepared(NativeLogicalPublicFrame Facts, string? Continuity, DateTimeOffset Time);
    private Prepared Prepare()
    {
        AssertMainThread();
        TextMenuFrame native = capture();
        if (native.GameContinuityId != runId) throw new TextMenuRunContinuityChangedException();
        var page = native.Page;
        string? focus = page.Interaction.Content.Surface["focused_target_referent_id"]?.GetValue<string>();
        if (ownerKey != native.OwnerKey)
        { ownerKey = native.OwnerKey; ownerId = NativeLogicalWire.Id("owner"); occurrence = NativeLogicalWire.Id("occurrence"); }
        if (focusedId != focus)
        { focusedId = focus; focusOccurrence = NativeLogicalWire.Id("focus"); }
        var missing = page.Completeness.Missing.ToList();
        if (page.Completeness.Status != "complete") missing.Add("native_public_frame_incomplete");
        if (page.BoundActions.Status != "complete") missing.Add("native_relation_incomplete");
        if (page.Status is "visible_unsupported" or "unsupported") missing.Add("native_owner_unsupported");
        if (native.Leaves.Select(l => l.Key).Distinct(StringComparer.Ordinal).Count() != native.Leaves.Count)
            missing.Add("native_binding_ambiguous");
        var facts = new NativeLogicalPublicFrame(Hub.StreamGeneration, page.Session,
            new(ownerId, occurrence, bindingRevision, focus, focus is null ? null : focusOccurrence),
            page.Status, page.Persistent is null ? null : page.Persistent with { Content = page.Persistent.Content.DeepClone() },
            page.Interaction with
            {
                Content = new(page.Interaction.Content.Surface.DeepClone(), page.Interaction.Content.Context.DeepClone()),
                Capabilities = Array.AsReadOnly(page.Interaction.Capabilities.Select(c => c with { Arguments = Array.AsReadOnly(c.Arguments.ToArray()) }).ToArray())
            },
            Array.AsReadOnly(page.Referents.Select(r => r with { Properties = r.Properties?.DeepClone() }).ToArray()),
            page.InformationPolicy,
            Array.AsReadOnly(native.Leaves.Select(l => new NativeLogicalLeaf(l.Verb, l.Label, l.SubjectReferentId,
                Array.AsReadOnly(l.Arguments.Select(a => new NativeLogicalArgument(a.Role, a.ReferentId)).ToArray()), "native_ui") { BindingKey = l.Key }).ToArray()),
            new(missing.Count == 0 ? "complete" : "partial", Array.AsReadOnly(missing.Distinct(StringComparer.Ordinal).ToArray())));
        lock (basisGate)
        {
            if (currentFacts is not null && !SameFacts(currentFacts, facts))
            { bindingRevision = NativeLogicalWire.Id("binding"); facts = facts with { OwnerOccurrence = facts.OwnerOccurrence with { BindingRevision = bindingRevision } }; }
            currentFacts = facts; currentNative = native;
        }
        return new(facts, native.GameContinuityId, DateTimeOffset.UtcNow);
    }
    // Equality only revalidates an already generated current snapshot. The
    // existing projector alone assigns snapshots, catalogs and action handles.
    internal static bool SameFacts(NativeLogicalPublicFrame a, NativeLogicalPublicFrame b)
    {
        if (a.StreamGeneration != b.StreamGeneration || a.Session != b.Session || a.OwnerOccurrence != b.OwnerOccurrence
            || a.Status != b.Status || a.InformationPolicy != b.InformationPolicy || a.SourceCompleteness.Status != b.SourceCompleteness.Status
            || !a.SourceCompleteness.Missing.SequenceEqual(b.SourceCompleteness.Missing)
            || a.Persistent?.ContentSchema != b.Persistent?.ContentSchema || !JsonNode.DeepEquals(a.Persistent?.Content, b.Persistent?.Content)
            || a.Interaction.InteractionId != b.Interaction.InteractionId || a.Interaction.Kind != b.Interaction.Kind
            || a.Interaction.Stage != b.Interaction.Stage || a.Interaction.Prompt != b.Interaction.Prompt || a.Interaction.ContentSchema != b.Interaction.ContentSchema
            || !JsonNode.DeepEquals(a.Interaction.Content.Surface, b.Interaction.Content.Surface)
            || !JsonNode.DeepEquals(a.Interaction.Content.Context, b.Interaction.Content.Context)
            || a.Referents.Count != b.Referents.Count || a.Leaves.Count != b.Leaves.Count || a.Interaction.Capabilities.Count != b.Interaction.Capabilities.Count) return false;
        for (int i = 0; i < a.Referents.Count; i++)
        {
            var x = a.Referents[i]; var y = b.Referents[i];
            if (x.ReferentId != y.ReferentId || x.Role != y.Role || x.Kind != y.Kind || x.Label != y.Label || x.State != y.State
                || x.PropertiesSchema != y.PropertiesSchema || !JsonNode.DeepEquals(x.Properties, y.Properties)) return false;
        }
        for (int i = 0; i < a.Leaves.Count; i++)
        {
            var x = a.Leaves[i]; var y = b.Leaves[i];
            if (x.BindingKey != y.BindingKey || x.Verb != y.Verb || x.Label != y.Label || x.SubjectReferentId != y.SubjectReferentId
                || x.EffectDomain != y.EffectDomain || !x.Arguments.SequenceEqual(y.Arguments)) return false;
        }
        for (int i = 0; i < a.Interaction.Capabilities.Count; i++)
        {
            var x = a.Interaction.Capabilities[i]; var y = b.Interaction.Capabilities[i];
            if (x.Verb != y.Verb || x.SubjectRole != y.SubjectRole || x.AvailabilityBasis != y.AvailabilityBasis || !x.Arguments.SequenceEqual(y.Arguments)) return false;
        }
        return true;
    }
    private void Enqueue(Action encode)
    {
        encodings.Enqueue(() => { try { encode(); } finally { encodingAdmission.Release(); } });
        if (Interlocked.CompareExchange(ref encodingWorker, 1, 0) != 0) return;
        ThreadPool.QueueUserWorkItem(_ =>
        {
            do
            {
                while (encodings.TryDequeue(out var work)) { try { work(); } catch { /* Each job accounts its own failure. */ } }
                Volatile.Write(ref encodingWorker, 0);
            } while (!encodings.IsEmpty && Interlocked.CompareExchange(ref encodingWorker, 1, 0) == 0);
        });
    }
    private void AcceptBasis(Prepared prepared, NativeLogicalCatalog? catalog)
    {
        if (catalog is null) return;
        var actions = catalog.Actions;
        var keys = actions.Select((action, i) => (action.ActionId, prepared.Facts.Leaves[i].BindingKey)).ToDictionary(x => x.ActionId, x => x.BindingKey, StringComparer.Ordinal);
        lock (basisGate)
            if (currentFacts is not null && SameFacts(currentFacts, prepared.Facts))
            { projectedBasis = prepared.Facts; projectedSnapshot = catalog.Descriptor.SnapshotId; currentActionKeys = keys; }
    }
    internal async Task<NativeLogicalCurrentReply> CurrentAsync(NativeLogicalCurrentRequest request, CancellationToken cancellation = default)
    {
        if (!encodingAdmission.Wait(0)) return new(NativeLogicalContract.CurrentSchema, NativeLogicalContract.Profile, "capacity_exceeded", null, null, null, "encoding_capacity_exceeded");
        request = request with { EagerScope = Array.AsReadOnly(request.EagerScope.ToArray()) };
        var source = new TaskCompletionSource<NativeLogicalCurrentReply>(TaskCreationOptions.RunContinuationsAsynchronously);
        bool queued = false;
        try
        {
            await nativeQueue(() =>
            {
                SynchronizeRun(); var prepared = Prepare();
                // Enqueue in the same native turn as capture. A continuation
                // on an HTTP/worker thread cannot reorder an older Current
                // behind a later source callback and change handle identity.
                Enqueue(() =>
                {
                    try
                    {
                        var reply = projector.Current(prepared.Facts, request, prepared.Time, Environment.TickCount64 + Limits.RetentionMs,
                            () => Environment.TickCount64, Store, prepared.Continuity);
                        if (reply.Capture is { } value)
                            AcceptBasis(prepared, request.EagerScope.Contains("catalog") ? Store.Catalog(value.CaptureId) : null);
                        if (reply.Status == "source_capture_incomplete") reply = reply with { Reason = prepared.Facts.SourceCompleteness.Missing.FirstOrDefault() ?? reply.Reason };
                        source.SetResult(reply);
                    }
                    catch (Exception e) { source.SetException(e); }
                });
                queued = true;
                return prepared;
            }, cancellation).ConfigureAwait(false);
        }
        catch { if (!queued) encodingAdmission.Release(); throw; }
        return await source.Task.ConfigureAwait(false);
    }
    internal NativeLogicalAttachReply Attach(NativeLogicalAttachRequest request)
    {
        SynchronizeRun();
        var initial = Hub.AttachWithInitialReservation(request, Bootstrap);
        if (initial.InitialReservation is { } reservation) CaptureReservation(reservation);
        return initial.Attach;
    }
    private void ObserveOwnerReady(NativeDecisionOwnerReadyObservation observed) =>
        Publish("native_owner_ready", observed.Domain, observed.Domain == NativeDecisionOwnerReadyProvider.GameOverDomain ? "terminal" : "observation");
    internal void Publish(string seam, string phase, string kind = "observation")
    {
        try { SynchronizeRun(); CaptureReservation(Hub.Reserve(seam, phase, kind)); }
        catch (Exception) { /* Native diagnostics cannot interfere with gameplay. */ }
    }
    private void CaptureReservation(NativeLogicalPublicationReservation reservation)
    {
        // No observer has acquired Current and no subscription selected this
        // position: keep the source clock without materializing a game frame.
        // A standalone Current basis still needs observed owner re-entry.
        if (reservation.Subscriptions.Count == 0 && currentFacts is null)
        { Hub.Complete(reservation, Array.Empty<NativeLogicalProjectionOutcome>()); return; }
        void Missing(string reason) => Hub.Complete(reservation, reservation.Subscriptions.Select(s => new NativeLogicalProjectionOutcome(s.ScopeId, null, reason)).ToArray());
        if (!encodingAdmission.Wait(0)) { Missing("encoding_capacity_exceeded"); return; }
        Prepared prepared;
        try { prepared = Prepare(); }
        catch { encodingAdmission.Release(); Missing("native_capture_failed"); return; }
        Enqueue(() =>
        {
            var outcomes = new List<NativeLogicalProjectionOutcome>();
            foreach (var subscription in reservation.Subscriptions)
            {
                try
                {
                    var projection = projector.Capture(prepared.Facts, subscription.EagerScope, subscription.ScopeId,
                        prepared.Time, Environment.TickCount64 + Limits.RetentionMs, () => Environment.TickCount64, Store);
                    AcceptBasis(prepared, projection.Catalog);
                    outcomes.Add(new(subscription.ScopeId, projection.Capture, null, projection.Catalog?.Descriptor.TotalCount > 0));
                }
                catch (NativeLogicalException e) { outcomes.Add(new(subscription.ScopeId, null, e.Code)); }
                catch { outcomes.Add(new(subscription.ScopeId, null, "encoding_failed")); }
            }
            try { Hub.Complete(reservation, outcomes); }
            finally
            {
                // Successful event publication now owns independent hub pins;
                // late/abandoned encodes have no reason to retain an owner pin.
                foreach (var outcome in outcomes)
                    if (outcome.Capture is { } value) Store.ReleaseCapture(value.CaptureId);
            }
        });
    }
    internal TextMenuLeaf? Revalidate(string expectedSnapshot, string actionId)
    {
        SynchronizeRun(); var prepared = Prepare();
        lock (basisGate)
        {
            if (projectedSnapshot != expectedSnapshot || projectedBasis is null || !SameFacts(projectedBasis, prepared.Facts)
                || prepared.Facts.SourceCompleteness.Status != "complete" || !currentActionKeys.TryGetValue(actionId, out var key)) return null;
            return currentNative!.Leaves.SingleOrDefault(l => l.Key == key);
        }
    }
    internal NativeLogicalExecutor.Admission Admit(PlayerEnvironmentActionRequest request) => executor.Admit(request);
    internal NativeLogicalResult RejectQueued(PlayerEnvironmentActionRequest request, string reason) => executor.RejectQueued(request, reason);
    internal NativeLogicalResult Submit(PlayerEnvironmentActionRequest request) => executor.Submit(request);
    internal NativeLogicalResult? Find(string id) => executor.Find(id);
    internal bool IsPending(string id) => executor.IsPending(id);
    public void Dispose()
    {
        if (initialized) NativeDecisionOwnerReadyProvider.Observed -= ObserveOwnerReady;
        Hub.Dispose();
        lock (basisGate) { currentNative = null; currentFacts = null; projectedBasis = null; currentActionKeys = new Dictionary<string, string>(); }
    }

    private sealed class ControlDependency : INativeLogicalControlDependency
    {
        public bool TryWatch(string clientSessionId, NativeLogicalControlBinding binding, Action controlLost, out IDisposable? watch)
        {
            try { return MutationControlRuntime.TryWatch(new(clientSessionId, binding.ControllerLeaseId, binding.ControllerGeneration), controlLost, out watch); }
            catch (MutationWatchCapacityException) { throw new NativeLogicalException("capacity_exceeded", "Authority control watch admission is full."); }
        }
    }
}
