using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

using NativeLogicalCapture = global::STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCapture;

/// <summary>The controller owner validates and atomically subscribes under its own authority lock.
/// Revocation callbacks must run after releasing that lock. Disposing a watch never releases a lease.</summary>
public interface INativeLogicalControlDependency
{
    bool TryWatch(string clientSessionId, NativeLogicalControlBinding binding, Action controlLost, out IDisposable? watch);
}

public sealed record NativeLogicalPublicationReservation(string StreamGeneration,
    string PublicationIndex, string SourceSeam, string SourcePhase, string SourceIndex,
    IReadOnlyList<NativeLogicalSubscription> Subscriptions);
public sealed record NativeLogicalInitialAttachment(NativeLogicalAttachReply Attach, NativeLogicalPublicationReservation? InitialReservation);
public sealed record NativeLogicalProjectionOutcome(string ScopeId, NativeLogicalCapture? Capture,
    string? MissingReason, bool CatalogNonempty = false);

/// <summary>Bounded source metadata and immutable scoped projections. No native capture or dispatch occurs here.</summary>
public sealed partial class NativeLogicalPublicationHub : IDisposable
{
    private sealed class Subscription(string client, NativeLogicalSubscription value, long deadline)
    {
        internal string Client = client; internal NativeLogicalSubscription Value = value; internal long Deadline = deadline;
        internal readonly HashSet<string> WaitIds = new(StringComparer.Ordinal);
        internal IReadOnlyDictionary<string, NativeLogicalSeamCoverage> AdvertisedCoverage = new Dictionary<string, NativeLogicalSeamCoverage>();
    }
    private sealed class Slot(ulong index, string seam, string phase, string sourceIndex, string kind, long deadline, string[] subscriptionIds)
    {
        internal ulong Index = index; internal string Seam = seam, Phase = phase, SourceIndex = sourceIndex, Kind = kind;
        internal long Deadline = deadline; internal string[] Subscriptions = subscriptionIds;
        internal bool Completed;
        internal readonly Dictionary<string, NativeLogicalProjectionOutcome> Outcomes = new(StringComparer.Ordinal);
        internal readonly Dictionary<string, string> Pins = new(StringComparer.Ordinal);
    }
    private sealed class Waiter(string client, string subscription, string id, ulong after, string condition, long deadline)
    {
        internal string Client = client, Subscription = subscription, Id = id, Condition = condition;
        internal ulong After = after; internal long Deadline = deadline;
        internal TaskCompletionSource<NativeLogicalAwaitReply> Source = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal IDisposable? ControlWatch; internal CancellationTokenRegistration Cancellation;
    }
    private readonly object gate = new();
    private readonly NativeLogicalLimits limits;
    private readonly Func<long> clock;
    private readonly NativeLogicalCaptureStore store;
    private readonly NativeLogicalCursor cursors = new();
    private readonly Dictionary<string, Subscription> subscriptions = new(StringComparer.Ordinal);
    private readonly Dictionary<string, ulong> seamIndices = new(StringComparer.Ordinal);
    private readonly Dictionary<string, NativeLogicalSeamCoverage> coverage;
    private string? publicationProfileId, publicationProfileDefinitionSha256;
    private readonly Dictionary<string, Waiter> waiters = new(StringComparer.Ordinal);
    private readonly Slot?[] ring;
    private readonly Timer timer;
    private readonly INativeLogicalControlDependency? controls;
    private readonly SemaphoreSlim controlWatches;
    private string generation = NativeLogicalWire.Id("stream");
    private ulong reserved, high, retainedStart = 1;
    private bool disposed;
    private long dependencyCleanupFailures;
    public long DependencyCleanupFailures => Interlocked.Read(ref dependencyCleanupFailures);
    private long waitIdMetadataBytes;
    // Conservative bounded admission charge for each retained ID and hash-set entry.
    public const int WaitIdMetadataCharge = 128;
    public long WaitIdMetadataBytes { get { lock (gate) return waitIdMetadataBytes; } }
    public string StreamGeneration { get { lock (gate) return generation; } }
    public int WaiterCount { get { lock (gate) return waiters.Count; } }
    public int MetadataCount { get { lock (gate) return ring.Count(s => s is not null); } }
    internal NativeLogicalPublicationDeclaration ReadDeclaration()
    {
        lock (gate)
            return new(generation, publicationProfileId, publicationProfileDefinitionSha256,
                Array.AsReadOnly(coverage.Values.ToArray()));
    }
    internal void InstallPublicationProfile(string profileId, string definitionSha256,
        IReadOnlyList<NativeLogicalSeamCoverage> confirmedCoverage)
    {
        var canonical = NativeLogicalPublicationProfile.ValidateConfirmation(profileId, definitionSha256, confirmedCoverage);
        lock (gate)
        {
            TickLocked();
            // Existing accepted scopes keep their original advertised declaration. Installation never rewrites an epoch.
            var unsupported = coverage.Values.Where(s => s.Coverage == "unsupported"
                && !canonical.Any(t => t.SourceSeam == s.SourceSeam)).ToArray();
            if (canonical.Count + unsupported.Length > limits.MaxSourceSeams)
                throw new NativeLogicalException("capacity_exceeded", "Publication declaration exceeds announced seam capacity.");
            coverage.Clear();
            foreach (var seam in canonical.Concat(unsupported)) coverage.Add(seam.SourceSeam, seam);
            publicationProfileId = profileId; publicationProfileDefinitionSha256 = definitionSha256;
        }
    }
    public NativeLogicalPublicationHub(NativeLogicalCaptureStore store,
        IEnumerable<NativeLogicalSeamCoverage> advertisedCoverage,
        Func<long>? monotonicMs = null, NativeLogicalLimits? limits = null,
        INativeLogicalControlDependency? controls = null)
    {
        this.store = store; this.limits = limits ?? new(); clock = monotonicMs ?? (() => Environment.TickCount64);
        this.controls = controls;
        controlWatches = new SemaphoreSlim(this.limits.MaxWaiters, this.limits.MaxWaiters);
        coverage = advertisedCoverage.ToDictionary(v => v.SourceSeam, StringComparer.Ordinal);
        if (coverage.Count > this.limits.MaxSourceSeams || this.limits.MaxEvents <= 0) throw new ArgumentOutOfRangeException(nameof(advertisedCoverage));
        foreach (var seam in coverage.Values)
        {
            foreach (string field in new[] { seam.SourceSeam, seam.Version, seam.Coverage }) NativeLogicalWire.Text(field, this.limits.MaxMetadataFieldBytes);
            if (seam.Coverage is not ("complete_at_seam" or "sampled" or "unsupported")) throw new NativeLogicalException("unsupported_seam", "Unknown coverage disposition.");
        }
        ring = new Slot[this.limits.MaxEvents];
        timer = new(_ => Tick(), null, 25, 25);
    }
    private string Binding(Subscription s) => s.Client + "|" + s.Value.SubscriptionId + "|" + s.Value.ScopeId + "|" + s.Value.StreamGeneration;
    private string Cursor(Subscription s, ulong index) => cursors.Create(Binding(s), index, 0);
    public NativeLogicalAttachReply Attach(NativeLogicalAttachRequest request)
    {
        lock (gate)
        {
            TickLocked();
            NativeLogicalWire.Text(request.ClientSessionId, limits.MaxMetadataFieldBytes);
            try { NativeLogicalProjector.ValidateScope(request.EagerScope); }
            catch (NativeLogicalException) { return new("unsupported_scope", null); }
            if (request.DeliveryMode is not ("scoped" or "full_reference")) return new("unsupported_scope", null);
            if (request.RequiredSeams.Select(s => s.SourceSeam).Distinct().Count() != request.RequiredSeams.Count) return new("unsupported_seam", null);
            var accepted = new List<NativeLogicalSeamCoverage>();
            foreach (var required in request.RequiredSeams)
            {
                if (!coverage.TryGetValue(required.SourceSeam, out var advertised) || advertised.Version != required.Version) return new("unsupported_seam", null);
                if (request.DeliveryMode == "full_reference" && advertised.Coverage != "complete_at_seam") return new("coverage_insufficient", null);
                accepted.Add(advertised);
            }
            if (request.DeliveryMode == "full_reference" && request.EagerScope.Count != 4) return new("unsupported_scope", null);
            if (subscriptions.Count >= limits.MaxSubscriptions || subscriptions.Values.Count(s => s.Client == request.ClientSessionId) >= limits.MaxClientSubscriptions) return new("capacity_exceeded", null);
            long deadline = checked(clock() + limits.RetentionMs);
            var value = new NativeLogicalSubscription(NativeLogicalWire.Id("subscription"), NativeLogicalWire.Id("scope"),
                Array.AsReadOnly(request.EagerScope.ToArray()), Array.AsReadOnly(accepted.ToArray()), request.DeliveryMode,
                generation, "", DateTimeOffset.UtcNow.AddMilliseconds(limits.RetentionMs));
            var subscription = new Subscription(request.ClientSessionId, value, deadline);
            subscription.AdvertisedCoverage = new Dictionary<string, NativeLogicalSeamCoverage>(coverage, StringComparer.Ordinal);
            subscription.Value = value with { StartingCursor = Cursor(subscription, reserved) };
            subscriptions.Add(value.SubscriptionId, subscription);
            return new("attached", subscription.Value);
        }
    }
    public NativeLogicalRenewReply Renew(string clientSessionId, string subscriptionId,
        string scopeId, string afterCursor)
    {
        lock (gate)
        {
            TickLocked();
            Subscription sub;
            try { sub = Get(clientSessionId, subscriptionId, scopeId); }
            catch (NativeLogicalException e) when (e.Code == "subscription_expired")
            { return new(NativeLogicalContract.RenewSchema, NativeLogicalContract.Profile, "subscription_expired", null, null, null, null, null, "subscription_expired"); }
            ulong after = Parse(sub, afterCursor);
            sub.Deadline = checked(clock() + limits.RetentionMs);
            sub.Value = sub.Value with { ExpiresAt = DateTimeOffset.UtcNow.AddMilliseconds(limits.RetentionMs) };
            return new(NativeLogicalContract.RenewSchema, NativeLogicalContract.Profile, "renewed", sub.Value,
                Cursor(sub, after), Cursor(sub, high), Cursor(sub, retainedStart - 1), Gap(after), null);
        }
    }
    public NativeLogicalRenewReply Renew(NativeLogicalRenewRequest request) =>
        Renew(request.ClientSessionId, request.SubscriptionId, request.ScopeId, request.AfterCursor);
    // The native owner invokes this and freezes its initial frame in one main-thread turn.
    // No source callback can interleave registration and the new subscription's reserved initial position.
    public NativeLogicalInitialAttachment AttachWithInitialReservation(NativeLogicalAttachRequest request,
        string sourceSeam, string sourcePhase = "initial_observation")
    {
        lock (gate)
        {
            if (!coverage.ContainsKey(sourceSeam)) return new(new("unsupported_seam", null), null);
            NativeLogicalWire.Text(sourcePhase, limits.MaxMetadataFieldBytes);
            var reply = Attach(request);
            if (reply.Subscription is null) return new(reply, null);
            try
            {
                var reservation = ReserveFor(sourceSeam, sourcePhase, "observation", new[] { reply.Subscription.SubscriptionId });
                return new(reply, reservation);
            }
            catch (NativeLogicalException e) when (e.Code == "generation_changed")
            { return new(new("generation_changed", null), null); }
        }
    }
    public NativeLogicalPublicationReservation Reserve(string sourceSeam, string phase, string kind) => ReserveFor(sourceSeam, phase, kind, null);
    private NativeLogicalPublicationReservation ReserveFor(string sourceSeam, string phase, string kind, string[]? onlySubscriptions)
    {
        lock (gate)
        {
            TickLocked();
            NativeLogicalWire.Text(sourceSeam, limits.MaxMetadataFieldBytes); NativeLogicalWire.Text(phase, limits.MaxMetadataFieldBytes); NativeLogicalWire.Text(kind, limits.MaxMetadataFieldBytes);
            if (!coverage.ContainsKey(sourceSeam)) throw new NativeLogicalException("unsupported_seam", "Seam is not advertised.");
            if (reserved == ulong.MaxValue || seamIndices.GetValueOrDefault(sourceSeam) == ulong.MaxValue)
            { ChangeGenerationLocked(); throw new NativeLogicalException("generation_changed", "Source clock rolled over; reattach required."); }
            ulong index = ++reserved, source = seamIndices.GetValueOrDefault(sourceSeam) + 1;
            seamIndices[sourceSeam] = source;
            int position = (int)((index - 1) % (ulong)ring.Length);
            if (ring[position] is Slot previous)
            {
                ReleasePins(previous); retainedStart = previous.Index + 1;
                if (high < previous.Index) high = previous.Index; // overwritten reserved interval is now an explicit gap
            }
            // A recorder observes the same original bootstrap occurrence as another attaching reader.
            // No extra source position or later Current repair is introduced.
            string[] ids = onlySubscriptions is null ? subscriptions.Keys.ToArray()
                : onlySubscriptions.Concat(SelectedSourceSubscriptionIds()).Distinct(StringComparer.Ordinal).ToArray();
            ring[position] = new Slot(index, sourceSeam, phase, NativeLogicalWire.Number(source), kind,
                checked(clock() + limits.EncodingDeadlineMs), ids);
            AdvanceHigh(); EvaluateWaiters();
            return new(generation, NativeLogicalWire.Number(index), sourceSeam, phase,
                NativeLogicalWire.Number(source), Array.AsReadOnly(ids.Select(id => subscriptions[id].Value).ToArray()));
        }
    }
    // An encoder supplies frozen capture references only. Missing projections are sealed explicitly.
    public bool Complete(NativeLogicalPublicationReservation reservation, IReadOnlyList<NativeLogicalProjectionOutcome> outcomes)
    {
        lock (gate)
        {
            TickLocked();
            if (reservation.StreamGeneration != generation)
                return CompleteRetiringSourceReservation(reservation, outcomes);
            if (!ulong.TryParse(reservation.PublicationIndex, out ulong index)) return false;
            Slot? slot = FindSlot(index);
            if (slot is null || slot.Completed) return CompleteRetiringSourceReservation(reservation, outcomes);
            if (outcomes.Select(o => o.ScopeId).Distinct().Count() != outcomes.Count) throw new NativeLogicalException("invalid_projection", "Duplicate scope outcomes.");
            // Validate the whole response before any pin or event state changes.
            var expectedScopes = slot.Subscriptions.Where(subscriptions.ContainsKey).Select(id => subscriptions[id].Value.ScopeId).ToHashSet(StringComparer.Ordinal);
            expectedScopes.UnionWith(RetiringSourceScopes(reservation));
            foreach (var outcome in outcomes)
            {
                if (!expectedScopes.Contains(outcome.ScopeId)) throw new NativeLogicalException("invalid_projection", "Outcome is not bound to an accepted projection scope.");
                if (outcome.Capture is null && outcome.MissingReason is null || outcome.Capture is not null && outcome.MissingReason is not null)
                    throw new NativeLogicalException("invalid_projection", "Each projection has exactly one capture or missing outcome.");
                if (outcome.MissingReason is not null) NativeLogicalWire.Text(outcome.MissingReason, limits.MaxMetadataFieldBytes);
                if (outcome.Capture is not null && (outcome.Capture.StreamGeneration != generation || outcome.Capture.ScopeId != outcome.ScopeId))
                    throw new NativeLogicalException("invalid_projection", "Frozen capture belongs to another scope or generation.");
                if (outcome.Capture is not null)
                    try
                    {
                        if (store.CaptureDescriptor(outcome.Capture.CaptureId) != outcome.Capture) throw new NativeLogicalException("invalid_projection", "Capture descriptor differs from immutable retained bytes.");
                    }
                    catch (NativeLogicalException e) when (e.Code == "payload_expired") { }
            }
            foreach (string id in slot.Subscriptions)
            {
                if (!subscriptions.TryGetValue(id, out Subscription? sub)) continue;
                NativeLogicalProjectionOutcome outcome = outcomes.SingleOrDefault(o => o.ScopeId == sub.Value.ScopeId) ?? new(sub.Value.ScopeId, null, "not_eager");
                if (outcome.Capture is not null)
                {
                    try
                    {
                        if (store.CaptureDescriptor(outcome.Capture.CaptureId) != outcome.Capture) throw new NativeLogicalException("invalid_projection", "Capture descriptor differs from immutable retained bytes.");
                        slot.Pins.Add(id, store.Retain("hub:" + id, outcome.Capture.CaptureId));
                    }
                    catch (NativeLogicalException e) { outcome = new(sub.Value.ScopeId, null, e.Code == "payload_expired" ? "payload_expired" : "capacity_exceeded"); }
                }
                else if (outcome.CatalogNonempty) outcome = outcome with { CatalogNonempty = false };
                slot.Outcomes.Add(id, outcome);
            }
            CompleteRetiringSourceReservation(reservation, outcomes);
            slot.Completed = true; AdvanceHigh(); EvaluateWaiters(); SignalSourceProgress(); return true;
        }
    }
    public NativeLogicalEventBatch Events(string client, string subscriptionId, string scopeId, string afterCursor, int? limit = null)
    {
        lock (gate)
        {
            TickLocked(); Subscription sub = Get(client, subscriptionId, scopeId);
            ulong after = Parse(sub, afterCursor);
            int pageLimit = limit ?? Math.Min(100, limits.MaxEvents);
            if (pageLimit <= 0 || pageLimit > limits.MaxEvents) throw new NativeLogicalException("invalid_limit", "Invalid event page count.");
            NativeLogicalGap? gap = Gap(after);
            var events = new List<NativeLogicalEventAvailability>();
            ulong next = Math.Max(after, retainedStart - 1);
            for (ulong i = next + 1; i <= high && events.Count < pageLimit; i++)
            {
                Slot? slot = FindSlot(i); next = i;
                if (slot is not null && slot.Completed && slot.Outcomes.TryGetValue(subscriptionId, out var outcome)) events.Add(Event(sub, slot, outcome));
            }
            // Starting cursor may legitimately exceed contiguous high while pre-attach encodes finish.
            next = Math.Max(next, after);
            return new(Array.AsReadOnly(events.ToArray()), Cursor(sub, next), Cursor(sub, high), Cursor(sub, retainedStart - 1), gap);
        }
    }
    public Task<NativeLogicalAwaitReply> AwaitAsync(string client, string subscriptionId, string scopeId,
        string afterCursor, string waitId, string condition, int timeoutMs,
        NativeLogicalControlBinding? controlBinding = null, CancellationToken cancellationToken = default)
    {
        if (waitId.Length != 32 || waitId.Any(c => c is not (>= '0' and <= '9') and not (>= 'a' and <= 'f')))
            throw new NativeLogicalException("invalid_wait_id", "wait_id must be 32 lowercase hexadecimal characters.");
        if (condition is not ("any_event" or "observation" or "catalog_nonempty" or "terminal")) throw new NativeLogicalException("invalid_condition", "Unknown public Await condition.");
        if (timeoutMs < 0 || timeoutMs > limits.MaxWaitMs) throw new NativeLogicalException("invalid_limit", "Wait deadline exceeds announced limit.");
        int lost = 0; IDisposable? watch = null;
        if (controlBinding is not null)
        {
            if (!controlWatches.Wait(0)) return Task.FromResult(new NativeLogicalAwaitReply("capacity_exceeded", null, null, null));
            try
            {
                if (controls is null || !controls.TryWatch(client, controlBinding,
                    () => { Interlocked.Exchange(ref lost, 1); CancelWait(client, subscriptionId, waitId, "control_lost"); }, out watch))
                { DisposeWatch(new ControlWatchLease(watch, controlWatches)); return Task.FromResult(new NativeLogicalAwaitReply("cancelled", null, null, "control_lost")); }
                watch = new ControlWatchLease(watch, controlWatches);
            }
            catch { controlWatches.Release(); throw; }
        }
        lock (gate)
        {
            try
            {
                TickLocked(); Subscription sub = Get(client, subscriptionId, scopeId);
                ulong after = Parse(sub, afterCursor);
                if (sub.WaitIds.Contains(waitId)) throw new NativeLogicalException("duplicate_wait_id", "wait_id is unique for this live subscription.");
                if (sub.WaitIds.Count >= limits.MaxWaitIdsPerSubscription || waitIdMetadataBytes + WaitIdMetadataCharge > limits.MaxWaitIdMetadataBytes) return Immediate("capacity_exceeded");
                sub.WaitIds.Add(waitId); waitIdMetadataBytes += WaitIdMetadataCharge;
                if (Volatile.Read(ref lost) != 0) return Immediate("cancelled", "control_lost");
                if (cancellationToken.IsCancellationRequested) return Immediate("cancelled", "caller_cancelled");
                NativeLogicalAwaitReply? ready = Inspect(sub, after, condition);
                if (ready is not null) { DisposeWatch(watch); return Task.FromResult(ready); }
                if (timeoutMs == 0) return Immediate("timeout");
                if (waiters.Count >= limits.MaxWaiters || waiters.Values.Count(w => w.Client == client) >= limits.MaxClientWaiters) return Immediate("capacity_exceeded");
                var waiter = new Waiter(client, subscriptionId, waitId, after, condition, checked(clock() + timeoutMs)) { ControlWatch = watch };
                waiters.Add(Key(subscriptionId, waitId), waiter);
                waiter.Cancellation = cancellationToken.Register(() => CancelWait(client, subscriptionId, waitId, "caller_cancelled"));
                if (!waiters.ContainsKey(Key(subscriptionId, waitId))) waiter.Cancellation.Unregister();
                return waiter.Source.Task;
            }
            catch (NativeLogicalException e) when (e.Code == "subscription_expired") { DisposeWatch(watch); return Task.FromResult(new NativeLogicalAwaitReply("subscription_expired", null, null, null)); }
            catch { DisposeWatch(watch); throw; }
            Task<NativeLogicalAwaitReply> Immediate(string status, string? reason = null) { DisposeWatch(watch); return Task.FromResult(new NativeLogicalAwaitReply(status, null, null, reason)); }
        }
    }
    public bool CancelWait(string client, string subscriptionId, string waitId, string reason = "caller_cancelled")
    {
        lock (gate)
        {
            if (!waiters.TryGetValue(Key(subscriptionId, waitId), out Waiter? waiter) || waiter.Client != client) return false;
            if (FinishExpired(waiter, clock())) return false;
            Finish(waiter, new("cancelled", null, null, reason)); return true;
        }
    }
    public NativeLogicalCancelWaitReply CancelWaitPublic(NativeLogicalCancelWaitRequest request)
    {
        bool cancelled = CancelWait(request.ClientSessionId, request.SubscriptionId, request.WaitId);
        return new(NativeLogicalContract.CancelWaitSchema, NativeLogicalContract.Profile,
            cancelled ? "cancelled" : "not_pending", request.SubscriptionId, request.WaitId, cancelled, null);
    }
    public bool Detach(string client, string subscriptionId)
    {
        lock (gate)
        {
            TickLocked();
            if (!subscriptions.TryGetValue(subscriptionId, out var sub)) return false;
            if (sub.Client != client) throw new NativeLogicalException("cursor_mismatch", "This subscription belongs to another client.");
            RemoveSubscription(subscriptionId, "cancelled"); return true;
        }
    }
    public NativeLogicalDetachReply DetachPublic(NativeLogicalDetachRequest request) =>
        new(NativeLogicalContract.DetachSchema, NativeLogicalContract.Profile, "detached",
            request.SubscriptionId, Detach(request.ClientSessionId, request.SubscriptionId), null);
    public void Tick() { lock (gate) if (!disposed) TickLocked(); }
    private void TickLocked()
    {
        if (disposed) throw new ObjectDisposedException(nameof(NativeLogicalPublicationHub));
        long now = clock();
        foreach (Waiter waiter in waiters.Values.ToArray()) FinishExpired(waiter, now);
        foreach (var pair in subscriptions.Where(s => now >= s.Value.Deadline).ToArray()) RemoveSubscription(pair.Key, "subscription_expired");
        bool projectionsTimedOut = false;
        foreach (Slot slot in ring.OfType<Slot>())
            if (!slot.Completed && now >= slot.Deadline)
            {
                foreach (string id in slot.Subscriptions)
                    if (subscriptions.TryGetValue(id, out var sub)) slot.Outcomes[id] = new(sub.Value.ScopeId, null, "encoding_timeout");
                slot.Completed = true;
                projectionsTimedOut = true;
            }
        AdvanceHigh(); EvaluateWaiters();
        if (projectionsTimedOut) SignalSourceProgress();
        TickRetiringSourceViews(now);

    }
    private void AdvanceHigh()
    { while (high < reserved && FindSlot(high + 1) is { Completed: true }) high++; }
    private Slot? FindSlot(ulong index)
    { if (index == 0) return null; Slot? slot = ring[(int)((index - 1) % (ulong)ring.Length)]; return slot?.Index == index ? slot : null; }
    private Subscription Get(string client, string id, string scope)
    {
        if (!subscriptions.TryGetValue(id, out var sub)) throw new NativeLogicalException("subscription_expired", "No live subscription exists.");
        if (sub.Client != client || sub.Value.ScopeId != scope) throw new NativeLogicalException("cursor_mismatch", "Subscription and scope are client-bound.");
        return sub;
    }
    private ulong Parse(Subscription sub, string cursor)
    {
        ulong position = cursors.Parse(cursor, Binding(sub), clock(), checkExpiry: false);
        if (position > reserved) throw new NativeLogicalException("cursor_mismatch", "Cursor is above the reserved source watermark.");
        return position;
    }
    private NativeLogicalGap? Gap(ulong after) => after < retainedStart - 1 ? new("retention_overflow", NativeLogicalWire.Number(after + 1), NativeLogicalWire.Number(retainedStart - 1)) : null;
    private NativeLogicalEventAvailability Event(Subscription sub, Slot slot, NativeLogicalProjectionOutcome outcome)
    {
        string seamCoverage = sub.AdvertisedCoverage.TryGetValue(slot.Seam, out var accepted) ? accepted.Coverage : "unsupported";
        var value = new NativeLogicalEvent(NativeLogicalContract.EventSchema, Cursor(sub, slot.Index), generation,
            NativeLogicalWire.Number(slot.Index), slot.Kind, slot.Seam, slot.Phase, slot.SourceIndex,
            sub.Value.ScopeId, outcome.Capture?.CaptureId, outcome.MissingReason, seamCoverage, outcome.Capture);
        return new(value, outcome.Capture is null ? "missing" : store.IsAvailable(outcome.Capture.CaptureId) ? "available" : "payload_expired");
    }
    private NativeLogicalAwaitReply? Inspect(Subscription sub, ulong after, string condition)
    {
        NativeLogicalGap? gap = Gap(after);
        if (gap is not null) return new("gap", null, gap, null);
        for (ulong i = after + 1; i <= high; i++)
        {
            Slot? slot = FindSlot(i);
            if (slot is null || !slot.Outcomes.TryGetValue(sub.Value.SubscriptionId, out var outcome)) continue;
            if (condition == "any_event" || condition == "observation" && slot.Kind == "observation"
                || condition == "terminal" && slot.Kind == "terminal" || condition == "catalog_nonempty" && outcome.CatalogNonempty)
                return new("event", Event(sub, slot, outcome), null, null);
        }
        return null;
    }
    private bool FinishExpired(Waiter waiter, long now)
    {
        if (subscriptions.TryGetValue(waiter.Subscription, out var subscription)
            && now >= subscription.Deadline && subscription.Deadline < waiter.Deadline)
        { Finish(waiter, new("subscription_expired", null, null, null)); return true; }
        if (now >= waiter.Deadline)
        { Finish(waiter, new("timeout", null, null, null)); return true; }
        if (subscriptions.TryGetValue(waiter.Subscription, out subscription) && now >= subscription.Deadline)
        { Finish(waiter, new("subscription_expired", null, null, null)); return true; }
        return false;
    }
    private void EvaluateWaiters()
    {
        foreach (Waiter waiter in waiters.Values.ToArray())
        {
            if (FinishExpired(waiter, clock())) continue;
            if (subscriptions.TryGetValue(waiter.Subscription, out var sub) && Inspect(sub, waiter.After, waiter.Condition) is { } reply) Finish(waiter, reply);
        }
    }
    private static string Key(string subscription, string waitId) => subscription + ":" + waitId;
    private void Finish(Waiter waiter, NativeLogicalAwaitReply reply)
    {
        waiters.Remove(Key(waiter.Subscription, waiter.Id));
        waiter.Source.TrySetResult(reply);
        // Never block the source/Stop lock waiting for a concurrent cancellation callback or authority watch.
        waiter.Cancellation.Unregister();
        DisposeWatch(waiter.ControlWatch);
    }
    private void DisposeWatch(IDisposable? watch)
    {
        if (watch is not null) ThreadPool.QueueUserWorkItem(_ =>
        {
            try { watch.Dispose(); }
            catch { Interlocked.Increment(ref dependencyCleanupFailures); }
        });
    }
    // A bounded admission token stays held until actual watch cleanup completes.
    private sealed class ControlWatchLease(IDisposable? watch, SemaphoreSlim admission) : IDisposable
    {
        private IDisposable? value = watch; private SemaphoreSlim? owner = admission;
        public void Dispose()
        {
            var gate = Interlocked.Exchange(ref owner, null);
            if (gate is null) return;
            // Failure remains capacity-charged; a failed owner cleanup cannot silently renew admission.
            Interlocked.Exchange(ref value, null)?.Dispose();
            gate.Release();
        }
    }
    private void RemoveSubscription(string id, string status)
    {
        if (status == "subscription_expired" && sourceRegistration?.ActiveSubscription == id)
        {
            sourceRegistration.Failure = "source_subscription_expired";
            sourceRegistration.ActiveSubscription = null;
            SignalSourceProgress();
        }
        foreach (Waiter waiter in waiters.Values.Where(w => w.Subscription == id).ToArray())
            if (!FinishExpired(waiter, clock())) Finish(waiter, new(status, null, null, null));
        if (subscriptions.Remove(id, out var removed)) waitIdMetadataBytes -= (long)removed.WaitIds.Count * WaitIdMetadataCharge;
        foreach (Slot slot in ring.OfType<Slot>())
        { if (slot.Pins.Remove(id, out string? pin)) store.Release("hub:" + id, pin); slot.Outcomes.Remove(id); }
    }
    private void ReleasePins(Slot slot)
    { foreach (var pin in slot.Pins) store.Release("hub:" + pin.Key, pin.Value); slot.Pins.Clear(); }
    public void ChangeGeneration() { lock (gate) ChangeGenerationLocked(); }
    private void ChangeGenerationLocked()
    {
        RetireSourceBeforeGenerationChange();
        foreach (Waiter waiter in waiters.Values.ToArray())
            if (!FinishExpired(waiter, clock())) Finish(waiter, new("generation_changed", null, null, null));
        foreach (Slot slot in ring.OfType<Slot>()) ReleasePins(slot);
        subscriptions.Clear(); waitIdMetadataBytes = 0; seamIndices.Clear(); Array.Clear(ring); reserved = high = 0; retainedStart = 1;
        generation = NativeLogicalWire.Id("stream");
        SignalSourceProgress();
    }
    public void Dispose()
    { lock (gate) { if (disposed) return; DisposeSourceRegistration(); ChangeGenerationLocked(); disposed = true; } timer.Dispose(); }
}
