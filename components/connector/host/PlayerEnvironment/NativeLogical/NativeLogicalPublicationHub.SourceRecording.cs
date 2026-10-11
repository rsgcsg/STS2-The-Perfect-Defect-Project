using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

internal sealed record NativeLogicalSourceHubBoundary(string SubscriptionId, string ScopeId,
    string StreamGeneration, string StartingIndex, string ReservedThrough, string CompletedThrough,
    string RetainedStartIndex, long EncodingDeadlineMonotonicMs);
internal sealed record NativeLogicalSourceHubAttachment(string RegistrationId,
    NativeLogicalSubscription Subscription, NativeLogicalSourceHubBoundary StartingBoundary,
    NativeLogicalPublicationReservation InitialReservation);

public sealed partial class NativeLogicalPublicationHub
{
    internal const int MaxRetiringSourceViews = 2;
    private sealed class SourceRegistration(string id)
    {
        internal readonly string Id = id;
        internal string? ActiveSubscription, Failure;
        internal readonly Dictionary<string, SourceView> Retiring = new(StringComparer.Ordinal);
        internal SourceView? Closing;
        internal bool CloseRequested;
        internal TaskCompletionSource<bool> Progress = NewProgress();
    }
    private sealed class SourceView(Subscription subscription, string streamGeneration, ulong starting,
        ulong reservedThrough, ulong completedThrough, ulong retainedStartIndex, long encoderDeadline)
    {
        internal readonly Subscription Subscription = subscription;
        internal readonly string Generation = streamGeneration;
        internal readonly ulong Starting = starting, Reserved = reservedThrough, RetainedStart = retainedStartIndex;
        internal readonly long EncoderDeadline = encoderDeadline;
        internal ulong High = completedThrough, Acknowledged = starting;
        internal readonly Dictionary<ulong, Slot> Slots = new();
    }
    private SourceRegistration? sourceRegistration;
    private static TaskCompletionSource<bool> NewProgress() => new(TaskCreationOptions.RunContinuationsAsynchronously);
    private IEnumerable<string> SelectedSourceSubscriptionIds() => sourceRegistration?.ActiveSubscription is { } id
        && subscriptions.ContainsKey(id) ? new[] { id } : Array.Empty<string>();

    // Native owner invokes this in its atomic main-thread attachment turn. It later freezes the returned reservation.
    internal NativeLogicalSourceHubAttachment AttachSource(NativeLogicalAttachRequest request, string sourceSeam)
    {
        lock (gate)
        {
            TickLocked();
            if (sourceRegistration is not null)
                throw new NativeLogicalException("source_attachment_capacity", "One source recording registration is admitted per owner.");
            sourceRegistration = new(NativeLogicalWire.Id("recording_source"));
            try { return AttachSourceEpochLocked(sourceRegistration, request, sourceSeam); }
            catch { DisposeSourceRegistration(); throw; }
        }
    }
    internal NativeLogicalSourceHubAttachment AttachSourceEpoch(string registrationId,
        NativeLogicalAttachRequest request, string sourceSeam)
    {
        lock (gate)
        {
            TickLocked(); var owner = GetSourceRegistration(registrationId);
            if (owner.Failure is not null) throw new NativeLogicalException(owner.Failure, "Source accounting already failed.");
            if (owner.CloseRequested) throw new NativeLogicalException("source_closing", "The original source registration is closing.");
            if (owner.ActiveSubscription is not null)
                throw new NativeLogicalException("source_epoch_not_sealed", "Seal the original source epoch before attaching its successor.");
            return AttachSourceEpochLocked(owner, request, sourceSeam);
        }
    }
    private NativeLogicalSourceHubAttachment AttachSourceEpochLocked(SourceRegistration owner,
        NativeLogicalAttachRequest request, string sourceSeam)
    {
        ulong starting = reserved, completed = high, floor = retainedStart;
        var attached = AttachWithInitialReservation(request, sourceSeam);
        if (attached.Attach.Subscription is not { } sub || attached.InitialReservation is not { } initial)
            throw new NativeLogicalException("source_attach_failed", attached.Attach.Status);
        owner.ActiveSubscription = sub.SubscriptionId;
        return new(owner.Id, sub, new(sub.SubscriptionId, sub.ScopeId, sub.StreamGeneration,
            NativeLogicalWire.Number(starting), NativeLogicalWire.Number(starting), NativeLogicalWire.Number(completed),
            NativeLogicalWire.Number(floor), checked(clock() + limits.EncodingDeadlineMs)), initial);
    }
    internal NativeLogicalSourceHubBoundary ReadSourceBoundary(string registrationId, string subscriptionId)
    {
        lock (gate)
        {
            TickLocked(); var owner = GetSourceRegistration(registrationId);
            if (SourceViewFor(owner, subscriptionId) is { } old) return SourceBoundary(old);
            var sub = SourceActive(owner, subscriptionId);
            ulong starting = Parse(sub, sub.Value.StartingCursor);
            long deadline = ring.OfType<Slot>().Where(s => s.Subscriptions.Contains(subscriptionId) && !s.Completed)
                .Select(s => s.Deadline).DefaultIfEmpty(clock()).Max();
            return new(subscriptionId, sub.Value.ScopeId, generation, NativeLogicalWire.Number(starting),
                NativeLogicalWire.Number(reserved), NativeLogicalWire.Number(high), NativeLogicalWire.Number(retainedStart), deadline);
        }
    }
    internal NativeLogicalSourceHubBoundary SealSource(string registrationId, string subscriptionId, bool close = false)
    {
        lock (gate)
        {
            TickLocked(); var owner = GetSourceRegistration(registrationId);
            if (SourceViewFor(owner, subscriptionId) is { } old)
            {
                if (close)
                {
                    if (owner.ActiveSubscription is not null)
                        throw new NativeLogicalException("source_close_epoch_mismatch", "Close must seal the actual active source epoch.");
                    owner.CloseRequested = true; SignalSourceProgress();
                }
                return SourceBoundary(old);
            }
            return SourceBoundary(RetireSourceLocked(owner, SourceActive(owner, subscriptionId), close));
        }
    }
    private SourceView RetireSourceLocked(SourceRegistration owner, Subscription sub, bool close = false)
    {
        if (!close && owner.Retiring.Count >= MaxRetiringSourceViews)
            throw new NativeLogicalException("source_retiring_epoch_capacity", "The original two retiring source epochs remain retained.");
        ulong start = Parse(sub, sub.Value.StartingCursor);
        long deadline = ring.OfType<Slot>().Where(s => !s.Completed)
            .Select(s => s.Deadline).DefaultIfEmpty(clock()).Max();
        var view = new SourceView(sub, generation, start, reserved, high, retainedStart, deadline);
        // Preserve bounded original metadata even for a pending pre-attachment/non-selected prefix.
        // Its completion watermark cannot be fabricated merely because this recorder selected later positions.
        foreach (var original in ring.OfType<Slot>().Where(s => s.Index <= reserved))
        {
            bool selected = original.Subscriptions.Contains(sub.Value.SubscriptionId);
            var copy = new Slot(original.Index, original.Seam, original.Phase, original.SourceIndex, original.Kind,
                original.Deadline, selected ? new[] { sub.Value.SubscriptionId } : Array.Empty<string>(), original.OriginalScopes)
                { Completed = original.Completed };
            if (original.Outcomes.Remove(sub.Value.SubscriptionId, out var outcome)) copy.Outcomes.Add(sub.Value.SubscriptionId, outcome);
            if (original.Pins.Remove(sub.Value.SubscriptionId, out var pin)) copy.Pins.Add(sub.Value.SubscriptionId, pin);
            view.Slots.Add(copy.Index, copy);
        }
        if (close) { owner.CloseRequested = true; owner.Closing = view; }
        else owner.Retiring.Add(sub.Value.SubscriptionId, view);
        owner.ActiveSubscription = null;
        subscriptions.Remove(sub.Value.SubscriptionId);
        waitIdMetadataBytes -= (long)sub.WaitIds.Count * WaitIdMetadataCharge;
        foreach (var waiter in waiters.Values.Where(w => w.Subscription == sub.Value.SubscriptionId).ToArray())
            Finish(waiter, new("generation_changed", null, null, null));
        SignalSourceProgress(); return view;
    }
    private static NativeLogicalSourceHubBoundary SourceBoundary(SourceView view) => new(
        view.Subscription.Value.SubscriptionId, view.Subscription.Value.ScopeId, view.Generation,
        NativeLogicalWire.Number(view.Starting), NativeLogicalWire.Number(view.Reserved), NativeLogicalWire.Number(view.High),
        NativeLogicalWire.Number(view.RetainedStart), view.EncoderDeadline);
    private static IEnumerable<SourceView> SourceViews(SourceRegistration owner) =>
        owner.Closing is { } current ? owner.Retiring.Values.Append(current) : owner.Retiring.Values;
    private static SourceView? SourceViewFor(SourceRegistration owner, string subscriptionId) =>
        owner.Closing?.Subscription.Value.SubscriptionId == subscriptionId ? owner.Closing
            : owner.Retiring.GetValueOrDefault(subscriptionId);
    private void RetireSourceBeforeGenerationChange()
    {
        if (sourceRegistration is not { ActiveSubscription: { } id } owner || !subscriptions.TryGetValue(id, out var sub)) return;
        try { RetireSourceLocked(owner, sub); }
        catch (NativeLogicalException error) when (error.Code == "source_retiring_epoch_capacity")
        {
            // No oldest-view eviction and no third retirement allocation. Gameplay generation turnover still proceeds.
            owner.Failure ??= error.Code; owner.ActiveSubscription = null;
            RemoveSubscription(id, "generation_changed"); SignalSourceProgress();
        }
    }
    private IEnumerable<string> RetiringSourceScopes(NativeLogicalPublicationReservation reservation) =>
        (sourceRegistration is { } owner ? SourceViews(owner) : Array.Empty<SourceView>()).Where(v => v.Generation == reservation.StreamGeneration
            && ulong.TryParse(reservation.PublicationIndex, out var index) && v.Slots.TryGetValue(index, out var slot)
            && slot.Subscriptions.Contains(v.Subscription.Value.SubscriptionId))
            .Select(v => v.Subscription.Value.ScopeId);
    private bool CompleteRetiringSourceReservation(NativeLogicalPublicationReservation reservation,
        IReadOnlyList<NativeLogicalProjectionOutcome> outcomes)
    {
        if (sourceRegistration is null || !ulong.TryParse(reservation.PublicationIndex, out var index)) return false;
        var originalViews = SourceViews(sourceRegistration).Where(v => v.Generation == reservation.StreamGeneration
            && v.Slots.TryGetValue(index, out var slot) && !slot.Completed).ToArray();
        if (originalViews.Length == 0) return false;
        if (outcomes.Select(o => o.ScopeId).Distinct(StringComparer.Ordinal).Count() != outcomes.Count)
            throw new NativeLogicalException("invalid_projection", "Duplicate original scope outcomes.");
        var originalScopes = originalViews.SelectMany(view => view.Slots[index].OriginalScopes).ToHashSet(StringComparer.Ordinal);
        foreach (var outcome in outcomes)
            if (!originalScopes.Contains(outcome.ScopeId))
                throw new NativeLogicalException("invalid_projection", "Outcome is outside the original reservation scopes.");
        // Preflight every retained original projection before modifying any view or acquiring its pins.
        foreach (var view in originalViews)
        {
            var slot = view.Slots[index];
            if (slot.Seam != reservation.SourceSeam || slot.Phase != reservation.SourcePhase || slot.SourceIndex != reservation.SourceIndex)
                throw new NativeLogicalException("invalid_projection", "Original reservation metadata changed.");
            var outcome = outcomes.SingleOrDefault(o => o.ScopeId == view.Subscription.Value.ScopeId);
            if (!slot.Subscriptions.Contains(view.Subscription.Value.SubscriptionId) || outcome is null) continue;
            if (outcome.Capture is null && outcome.MissingReason is null || outcome.Capture is not null && outcome.MissingReason is not null)
                throw new NativeLogicalException("invalid_projection", "Each original projection has exactly one capture or missing outcome.");
            if (outcome.MissingReason is not null) NativeLogicalWire.Text(outcome.MissingReason, limits.MaxMetadataFieldBytes);
            if (outcome.Capture is not null && (outcome.Capture.StreamGeneration != view.Generation
                || outcome.Capture.ScopeId != view.Subscription.Value.ScopeId))
                throw new NativeLogicalException("invalid_projection", "Capture is not bound to the original retiring scope.");
        }
        bool completed = false;
        foreach (var view in originalViews)
        {
            if (!view.Slots.TryGetValue(index, out var slot) || slot.Completed) continue;
            if (slot.Seam != reservation.SourceSeam || slot.Phase != reservation.SourcePhase || slot.SourceIndex != reservation.SourceIndex)
                throw new NativeLogicalException("invalid_projection", "Original reservation metadata changed.");
            if (outcomes.Select(o => o.ScopeId).Distinct(StringComparer.Ordinal).Count() != outcomes.Count)
                throw new NativeLogicalException("invalid_projection", "Duplicate original scope outcomes.");
            if (!slot.Subscriptions.Contains(view.Subscription.Value.SubscriptionId))
            {
                slot.Completed = true; AdvanceSourceHigh(view); completed = true; continue;
            }
            var outcome = outcomes.SingleOrDefault(o => o.ScopeId == view.Subscription.Value.ScopeId)
                ?? new(view.Subscription.Value.ScopeId, null, "not_eager");
            if (outcome.Capture is null && outcome.MissingReason is null || outcome.Capture is not null && outcome.MissingReason is not null)
                throw new NativeLogicalException("invalid_projection", "Each original projection has exactly one capture or missing outcome.");
            if (outcome.MissingReason is not null) NativeLogicalWire.Text(outcome.MissingReason, limits.MaxMetadataFieldBytes);
            if (outcome.Capture is null) outcome = outcome with { CatalogNonempty = false };
            if (outcome.Capture is not null)
            {
                if (outcome.Capture.StreamGeneration != view.Generation || outcome.Capture.ScopeId != view.Subscription.Value.ScopeId)
                    throw new NativeLogicalException("invalid_projection", "Capture is not bound to the original retiring scope.");
                try
                {
                    if (store.CaptureDescriptor(outcome.Capture.CaptureId) != outcome.Capture)
                        throw new NativeLogicalException("invalid_projection", "Capture descriptor differs from the original immutable bytes.");
                    slot.Pins.Add(view.Subscription.Value.SubscriptionId, store.Retain("hub:" + view.Subscription.Value.SubscriptionId, outcome.Capture.CaptureId));
                }
                catch (NativeLogicalException e) when (e.Code is "payload_expired" or "capacity_exceeded")
                { outcome = new(view.Subscription.Value.ScopeId, null, e.Code); }
            }
            slot.Outcomes.Add(view.Subscription.Value.SubscriptionId, outcome); slot.Completed = true;
            AdvanceSourceHigh(view); completed = true;
        }
        if (completed) SignalSourceProgress(); return completed;
    }
    private void TickRetiringSourceViews(long now)
    {
        if (sourceRegistration is null) return;
        bool changed = false;
        foreach (var view in SourceViews(sourceRegistration))
        {
            foreach (var slot in view.Slots.Values.Where(s => !s.Completed && now >= s.Deadline))
            {
                if (slot.Subscriptions.Contains(view.Subscription.Value.SubscriptionId))
                    slot.Outcomes[view.Subscription.Value.SubscriptionId] = new(view.Subscription.Value.ScopeId, null, "encoding_timeout");
                slot.Completed = true; changed = true;
            }
            AdvanceSourceHigh(view);
        }
        if (changed) SignalSourceProgress();
    }
    private static void AdvanceSourceHigh(SourceView view)
    {
        view.High = Math.Max(view.High, view.RetainedStart - 1);
        while (view.High < view.Reserved)
        {
            ulong next = view.High + 1;
            if (view.Slots.TryGetValue(next, out var slot) && slot.Completed) view.High = next;
            else break;
        }
    }
    internal NativeLogicalEventBatch SourceEvents(string registrationId, string subscriptionId,
        string afterCursor, int limit = 32)
    {
        lock (gate)
        {
            TickLocked(); var owner = GetSourceRegistration(registrationId);
            if (limit is < 1 or > 32) throw new NativeLogicalException("invalid_limit", "Source replay admits at most 32 metadata events.");
            var view = SourceViewFor(owner, subscriptionId);
            if (view is null)
            {
                var active = SourceActive(owner, subscriptionId);
                return Events(active.Client, subscriptionId, active.Value.ScopeId, afterCursor, Math.Min(limit, limits.MaxEvents));
            }
            ulong after = cursors.Parse(afterCursor, Binding(view.Subscription), clock(), checkExpiry: false);
            if (after > view.Reserved) throw new NativeLogicalException("cursor_mismatch", "Cursor is above the original source seal.");
            var gap = after < view.RetainedStart - 1 ? new NativeLogicalGap("retention_overflow",
                NativeLogicalWire.Number(after + 1), NativeLogicalWire.Number(view.RetainedStart - 1)) : null;
            ulong next = Math.Max(after, view.RetainedStart - 1);
            var events = new List<NativeLogicalEventAvailability>();
            for (ulong index = next + 1; index <= view.High && events.Count < limit; index++)
            {
                next = index;
                if (!view.Slots.TryGetValue(index, out var slot) || !slot.Outcomes.TryGetValue(subscriptionId, out var outcome)) continue;
                var original = Event(view.Subscription, slot, outcome);
                var row = original with { Event = original.Event with { StreamGeneration = view.Generation } };
                events.Add(row);
            }
            return new(Array.AsReadOnly(events.ToArray()), Cursor(view.Subscription, Math.Max(next, after)),
                Cursor(view.Subscription, view.High), Cursor(view.Subscription, view.RetainedStart - 1), gap);
        }
    }
    internal void AcknowledgeSource(string registrationId, string subscriptionId, string throughCursor)
    {
        lock (gate)
        {
            TickLocked(); var owner = GetSourceRegistration(registrationId);
            if (SourceViewFor(owner, subscriptionId) is { } view)
            {
                ulong through = cursors.Parse(throughCursor, Binding(view.Subscription), clock(), checkExpiry: false);
                if (through < view.Acknowledged || through > view.High)
                    throw new NativeLogicalException("cursor_mismatch", "Acknowledgement is outside the completed original source prefix.");
                foreach (var slot in view.Slots.Values.Where(s => s.Index <= through)) ReleasePins(slot);
                view.Acknowledged = through;
            }
            else
            {
                var sub = SourceActive(owner, subscriptionId); ulong through = Parse(sub, throughCursor);
                if (through > high) throw new NativeLogicalException("cursor_mismatch", "Acknowledgement is above completed source positions.");
                foreach (var slot in ring.OfType<Slot>().Where(s => s.Index <= through))
                    if (slot.Pins.Remove(subscriptionId, out var pin)) store.Release("hub:" + subscriptionId, pin);
            }
        }
    }
    internal bool ReleaseSourceEpoch(string registrationId, string subscriptionId)
    {
        lock (gate)
        {
            TickLocked(); var owner = GetSourceRegistration(registrationId);
            var view = SourceViewFor(owner, subscriptionId);
            if (view is null) return false;
            if (view.High < view.Reserved || view.Acknowledged < view.Reserved)
                throw new NativeLogicalException("source_epoch_not_drained", "Original source reservations must be completed and acknowledged before release.");
            foreach (var slot in view.Slots.Values) ReleasePins(slot);
            if (ReferenceEquals(owner.Closing, view)) owner.Closing = null;
            else owner.Retiring.Remove(subscriptionId);
            return true;
        }
    }
    internal Task SourceProgressAsync(string registrationId, CancellationToken cancellationToken)
    {
        lock (gate) return GetSourceRegistration(registrationId).Progress.Task.WaitAsync(cancellationToken);
    }
    internal Task WaitSourceProgressAsync(string registrationId, string subscriptionId,
        string afterCursor, CancellationToken cancellationToken)
    {
        lock (gate)
        {
            TickLocked(); var owner = GetSourceRegistration(registrationId);
            var available = SourceEvents(registrationId, subscriptionId, afterCursor);
            if (owner.Failure is not null || available.Gap is not null || available.NextCursor != afterCursor
                || SourceViewFor(owner, subscriptionId) is { } sealedView && sealedView.High >= sealedView.Reserved)
                return Task.CompletedTask;
            // Inspect and register under the same metadata gate; completion cannot be lost between Events and Await.
            return owner.Progress.Task.WaitAsync(cancellationToken);
        }
    }
    internal string? SourceFailure(string registrationId)
    { lock (gate) return GetSourceRegistration(registrationId).Failure; }
    internal NativeLogicalSubscription RenewSource(string registrationId, string subscriptionId)
    {
        lock (gate)
        {
            TickLocked(); var owner = GetSourceRegistration(registrationId);
            var sub = SourceActive(owner, subscriptionId);
            if (!TryTouchClient(sub.Client))
            {
                owner.Failure ??= "source_client_expired"; SignalSourceProgress();
                throw new NativeLogicalException("source_client_expired", "The original source client is no longer active.");
            }
            sub.Deadline = checked(clock() + limits.RetentionMs);
            sub.Value = sub.Value with { ExpiresAt = DateTimeOffset.UtcNow.AddMilliseconds(limits.RetentionMs) };
            return sub.Value;
        }
    }
    private void SignalSourceProgress()
    {
        if (sourceRegistration is not { } owner) return;
        var old = owner.Progress; owner.Progress = NewProgress(); old.TrySetResult(true);
    }
    partial void OnClientClosingLocked(string clientSessionId, string reason)
    {
        if (sourceRegistration is not { } owner) return;
        bool originalOwner = owner.ActiveSubscription is { } active && subscriptions.TryGetValue(active, out var sub) && sub.Client == clientSessionId
            || SourceViews(owner).Any(view => view.Subscription.Client == clientSessionId);
        if (!originalOwner) return;
        owner.Failure ??= "source_client_expired";
        SignalSourceProgress();
    }
    private SourceRegistration GetSourceRegistration(string id) => sourceRegistration is { } owner && owner.Id == id
        ? owner : throw new NativeLogicalException("source_attachment_expired", "No exact live source registration exists.");
    private Subscription SourceActive(SourceRegistration owner, string subscriptionId)
    {
        if (owner.ActiveSubscription != subscriptionId || !subscriptions.TryGetValue(subscriptionId, out var sub))
            throw new NativeLogicalException("source_epoch_expired", "No exact active source epoch exists.");
        return sub;
    }
    internal void DisposeSource(string registrationId)
    { lock (gate) { GetSourceRegistration(registrationId); DisposeSourceRegistration(); } }
    private void DisposeSourceRegistration()
    {
        if (sourceRegistration is not { } owner) return;
        if (owner.ActiveSubscription is { } active) RemoveSubscription(active, "cancelled");
        foreach (var view in SourceViews(owner))
            foreach (var slot in view.Slots.Values) ReleasePins(slot);
        owner.Retiring.Clear(); owner.Closing = null; owner.Progress.TrySetResult(true); sourceRegistration = null;
    }
}
