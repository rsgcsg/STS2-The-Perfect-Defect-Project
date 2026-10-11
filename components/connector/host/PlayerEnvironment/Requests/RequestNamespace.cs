using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>The existing runtime-global request namespace owns one original
/// completion, bounded immutable bytes and permanently spent IDs for every
/// Player Environment profile. It neither decides legality nor retries input.</summary>
internal sealed class RequestNamespace : IDisposable
{
    internal const int MaximumRequestIds = 65_536;
    internal const int MaximumSenders = 128;
    internal const int NativeEnvelopeBytes = 2 * 1024 * 1024;
    internal const int NativeReservationBytes = 4 * 1024 * 1024;
    internal const int LegacyEnvelopeBytes = 8 * 1024 * 1024;
    internal const int LegacyReservationBytes = 16 * 1024 * 1024;
    internal const int TerminalRetentionMs = 30 * 60 * 1000;
    private readonly object gate = new();
    private readonly Dictionary<string, Entry> entries;
    private readonly Func<MutationAuthorizationRequest, MutationAdmission> validate, begin;
    private readonly Func<MutationAuthorizationRequest, MutationRequestAdmission> admit;
    private readonly Func<PlayerEnvironmentActionRequest, string> fingerprint;
    private readonly Action<PlayerEnvironmentActionRequest, NativeLogicalResult>? nativeTerminal;
    private readonly Func<long> clock;
    private readonly string runtime;
    private readonly int maximumIds, maximumSenders, retentionMs;
    private readonly bool enableTimer;
    private readonly int nativeReservation, legacyReservation, nativeEnvelope, legacyEnvelope;
    private Timer? timer;
    private int senders;
    private bool disposed;
    internal RequestResultArena Arena { get; }
    internal TerminalPayloadCodec Codec { get; }
    internal int SpentIdCount { get { lock (gate) return entries.Count; } }
    internal int SenderCount { get { lock (gate) return senders; } }

    internal RequestNamespace(string runtime,
        Func<MutationAuthorizationRequest, MutationAdmission> validate,
        Func<MutationAuthorizationRequest, MutationRequestAdmission> admit,
        Func<MutationAuthorizationRequest, MutationAdmission> begin,
        Action<PlayerEnvironmentActionRequest, NativeLogicalResult>? nativeTerminal = null,
        Func<PlayerEnvironmentActionRequest, string>? fingerprint = null,
        RequestResultArena? arena = null, TerminalPayloadCodec? codec = null,
        Func<long>? clock = null, int maximumIds = MaximumRequestIds,
        int maximumSenders = MaximumSenders, int retentionMs = TerminalRetentionMs,
        bool enableTimer = true, int nativeReservation = NativeReservationBytes,
        int legacyReservation = LegacyReservationBytes, int nativeEnvelope = NativeEnvelopeBytes,
        int legacyEnvelope = LegacyEnvelopeBytes)
    {
        if (maximumIds is <= 0 or > MaximumRequestIds || maximumSenders is <= 0 or > MaximumSenders
            || retentionMs <= 0 || string.IsNullOrWhiteSpace(runtime)) throw new ArgumentOutOfRangeException(nameof(maximumIds));
        this.runtime = runtime; this.validate = validate; this.admit = admit; this.begin = begin;
        this.nativeTerminal = nativeTerminal; this.fingerprint = fingerprint ?? Fingerprint;
        this.clock = clock ?? (() => Environment.TickCount64); this.maximumIds = maximumIds;
        this.maximumSenders = maximumSenders; this.retentionMs = retentionMs; this.enableTimer = enableTimer;
        this.nativeReservation = nativeReservation; this.legacyReservation = legacyReservation;
        this.nativeEnvelope = nativeEnvelope; this.legacyEnvelope = legacyEnvelope;
        Arena = arena ?? new(); Codec = codec ?? new();
        // Bounded metadata capacity is prepared before any admission linearizes.
        entries = new(maximumIds, StringComparer.Ordinal);
    }

    internal enum State { Queued, Started, Terminal, Expired }
    internal sealed class Entry(PlayerEnvironmentActionRequest request, string fingerprint,
        RequestResultArena.ResultReservation reservation)
    {
        internal readonly object PreparationGate = new();
        internal readonly PlayerEnvironmentActionRequest Request = request;
        internal readonly string Fingerprint = fingerprint;
        internal readonly RequestResultArena.ResultReservation Reservation = reservation;
        internal readonly TaskCompletionSource<TerminalReply> Completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal readonly CancellationTokenSource QueueCancellation = new();
        internal MutationClientLifetime Client = null!;
        internal MutationAttribution AdmissionAttribution = null!;
        internal State State;
        internal string? CancellationReason;
        internal RequestResultArena.FrozenTerminal? Payload;
        internal long Deadline;
        internal int StatusCode;
        internal bool OriginalSender = true;
    }

    internal sealed record Admission(string Status, string? Reason, TerminalReply? Reply,
        Task<TerminalReply>? OriginalCompletion, CancellationToken QueueCancellation = default);
    internal sealed record Lookup(string Status, string? Reason, TerminalReply? Reply);

    internal Admission Admit(PlayerEnvironmentActionRequest request)
    {
        if (!MutationControllerCoordinator.SafeIdentifier(request.RequestId, 128))
            return new("rejected", "invalid_request_id", null, null);
        string exactFingerprint = fingerprint(request);
        lock (gate)
        {
            if (disposed) return new("rejected", "runtime_closed", null, null);
            RetireDueLocked();
            if (entries.TryGetValue(request.RequestId!, out Entry? previous))
            {
                if (previous.Fingerprint != exactFingerprint)
                    return new("conflict", "request_id_conflict", null, null);
                Lookup replay = LookupLocked(previous);
                return new(replay.Status, replay.Reason, replay.Reply, null);
            }
            if (!ValidShape(request)) return new("rejected", "invalid_player_environment_action", null, null);
            if (!Codec.Supported) return new("rejected", "result_encoding_unsupported", null, null);
            MutationAuthorizationRequest authorization = Authorization(request);
            MutationAdmission eligible = validate(authorization);
            if (!eligible.Accepted) return new("rejected", eligible.ErrorCode ?? "controller_rejected", null, null);
            if (eligible.Attribution?.RuntimeInstanceId != runtime)
                return new("rejected", "runtime_instance_mismatch", null, null);
            if (entries.Count == maximumIds || senders == maximumSenders)
                return new("rejected", "request_capacity_exceeded", null, null);
            bool native = RequestResultFactory.Native(request);
            RequestResultArena.ResultReservation? reserved = Arena.TryReserve(native ? nativeReservation : legacyReservation,
                native ? nativeEnvelope : legacyEnvelope);
            if (reserved is null) return new("rejected", "request_capacity_exceeded", null, null);
            try
            {
                // One bounded rejection is always encodable without reading game
                // state, even if this original never reaches a game-thread worker.
                Codec.Encode(RequestResultFactory.BeforeInput(request, "request_preinput_rejection_shell"), reserved, native);
                reserved.ResetEncoding();
                var entry = new Entry(request, exactFingerprint, reserved);
                MutationRequestAdmission admitted = admit(authorization);
                if (!admitted.Accepted)
                { entry.QueueCancellation.Dispose(); reserved.Dispose(); return new("rejected", admitted.Admission.ErrorCode ?? "controller_rejected", null, null); }
                entry.Client = admitted.Client!; entry.AdmissionAttribution = admitted.Admission.Attribution!;
                entries.Add(request.RequestId!, entry); senders++;
                return new("admitted", null, null, entry.Completion.Task, entry.QueueCancellation.Token);
            }
            catch { reserved.Dispose(); throw; }
        }
    }

    internal Lookup Find(string id, string? profile)
    {
        lock (gate)
        {
            RetireDueLocked();
            if (!entries.TryGetValue(id, out Entry? entry)) return new("not_found", "request_not_found", null);
            if (entry.Request.InputProfile != profile) return new("conflict", "input_profile_mismatch", null);
            return LookupLocked(entry);
        }
    }
    private Lookup LookupLocked(Entry entry)
    {
        if (entry.State is State.Queued or State.Started) return new("pending", "request_pending", null);
        if (entry.State == State.Expired) return new("expired", "result_expired", null);
        if (senders == maximumSenders) return new("capacity", "result_sender_capacity_exceeded", null);
        senders++;
        return new("terminal", null, new(entry.StatusCode, entry.Payload!.Borrow(), ReleaseSender));
    }

    internal Preparation? PrepareOriginal(PlayerEnvironmentActionRequest request)
    {
        Entry? entry;
        lock (gate)
        {
            if (!entries.TryGetValue(request.RequestId ?? "", out entry)
                || entry.Fingerprint != fingerprint(request) || entry.State != State.Queued) return null;
        }
        Monitor.Enter(entry.PreparationGate);
        lock (gate)
        {
            if (entry.State != State.Queued)
            { Monitor.Exit(entry.PreparationGate); return null; }
        }
        return new(this, entry);
    }

    internal sealed class Preparation : IDisposable
    {
        private readonly RequestNamespace owner;
        private readonly Entry entry;
        private bool holdsGate = true;
        private Action? terminalNotice;
        internal Preparation(RequestNamespace owner, Entry entry) { this.owner = owner; this.entry = entry; }
        internal RequestResultArena.ResultReservation Reservation => entry.Reservation;
        internal PlayerEnvironmentActionRequest Request => entry.Request;
        internal PlayerEnvironmentAttribution AdmissionAttribution => Public(entry.AdmissionAttribution);
        internal MutationAttribution OriginalAttribution => entry.AdmissionAttribution;
        internal MutationAdmission TryBegin()
        {
            lock (owner.gate)
            {
                if (entry.State != State.Queued || entry.CancellationReason is not null || entry.Client.IsClosed)
                    return MutationAdmission.Reject(entry.CancellationReason ?? entry.Client.CloseReason ?? "request_not_started", "The original request closed before input start.");
                MutationAdmission started = owner.begin(Authorization(entry.Request));
                if (started.Accepted) entry.State = State.Started;
                return started;
            }
        }
        internal void Encode(object result) => owner.Codec.Encode(result, entry.Reservation, RequestResultFactory.Native(entry.Request));
        internal RequestResultArena.ResultReservation.PreparedTerminal PrepareTerminal(object result)
        { Encode(result); return entry.Reservation.Prepare(); }
        internal void Seal(object result)
        {
            Encode(result);
            SealFrozen(result, entry.Reservation.Freeze());
        }
        internal void SealFrozen(object result, RequestResultArena.FrozenTerminal frozen)
        {
            terminalNotice = owner.Seal(entry, result, frozen);
            if (!holdsGate) InvokeNotice();
        }
        internal void ReleasePreparation()
        {
            if (!holdsGate) return;
            holdsGate = false; Monitor.Exit(entry.PreparationGate);
            InvokeNotice();
        }
        private void InvokeNotice()
        { Action? notify = terminalNotice; terminalNotice = null; notify?.Invoke(); }
        public void Dispose() => ReleasePreparation();
    }

    private Action? Seal(Entry entry, object result, RequestResultArena.FrozenTerminal payload)
    {
        lock (gate)
        {
            if (entry.State is State.Terminal or State.Expired)
            { payload.Retire(); return null; }
            entry.StatusCode = RequestResultFactory.HttpStatus(result);
            entry.Payload = payload; entry.State = State.Terminal;
            entry.Deadline = checked(clock() + retentionMs);
            TerminalReply original = entry.OriginalSender
                ? new(entry.StatusCode, payload.Borrow(), ReleaseSender)
                : new(entry.StatusCode, null, null);
            entry.OriginalSender = false;
            entry.Completion.TrySetResult(original);
            if (entry.Client.IsClosed || disposed) RetireLocked(entry);
            ArmTimerLocked();
        }
        entry.QueueCancellation.Dispose();
        // Keep this transient typed object only until synchronous compact
        // extraction. It never enters a result map/completed task/byte owner.
        if (result is NativeLogicalResult native && nativeTerminal is not null)
            return () => { try { nativeTerminal(entry.Request, native); } catch { /* Already sealed. */ } };
        return null;
    }

    internal bool CancelQueued(string id, string reason)
    {
        Entry? entry;
        lock (gate)
        {
            if (!entries.TryGetValue(id, out entry) || entry.State != State.Queued) return false;
            entry.CancellationReason ??= reason;
        }
        // Remove the original still-pending game-thread job without waiting
        // for another drain. The queue itself preserves a started callback.
        // No namespace/Authority/preparation lock spans token callbacks.
        try { entry.QueueCancellation.Cancel(); } catch (ObjectDisposedException) { /* Original already sealed. */ }
        Action? notice = null;
        lock (entry.PreparationGate)
        {
            lock (gate) if (entry.State != State.Queued) return false;
            object result = RequestResultFactory.BeforeInput(entry.Request, entry.CancellationReason!);
            Codec.Encode(result, entry.Reservation, RequestResultFactory.Native(entry.Request));
            notice = Seal(entry, result, entry.Reservation.Freeze());
        }
        notice?.Invoke();
        return true;
    }

    internal void ClientClosed(MutationClientClosure closure)
    {
        if (closure.RuntimeInstanceId != runtime) return;
        var queued = new List<string>();
        lock (gate)
        {
            foreach (Entry entry in entries.Values)
                if (entry.Client.ClientSessionId == closure.ClientSessionId)
                {
                    if (entry.State == State.Terminal) RetireLocked(entry);
                    else if (entry.State == State.Queued)
                    { entry.CancellationReason ??= closure.Reason; queued.Add(entry.Request.RequestId!); }
                }
            ArmTimerLocked();
        }
        foreach (string id in queued) CancelQueued(id, closure.Reason);
    }

    private void RetireDueLocked()
    {
        long now = clock();
        foreach (Entry entry in entries.Values)
            if (entry.State == State.Terminal && (entry.Client.IsClosed || now >= entry.Deadline)) RetireLocked(entry);
    }
    private static void RetireLocked(Entry entry)
    {
        entry.State = State.Expired;
        entry.Payload!.Retire(); entry.Payload = null;
    }
    private void ArmTimerLocked()
    {
        if (!enableTimer || disposed) return;
        long next = long.MaxValue;
        foreach (Entry entry in entries.Values) if (entry.State == State.Terminal) next = Math.Min(next, entry.Deadline);
        if (next == long.MaxValue) { timer?.Dispose(); timer = null; return; }
        timer ??= new Timer(_ => Tick(), null, Timeout.Infinite, Timeout.Infinite);
        timer.Change((int)Math.Min(int.MaxValue, Math.Max(1, next - clock())), Timeout.Infinite);
    }
    internal void Tick() { lock (gate) { if (disposed) return; RetireDueLocked(); ArmTimerLocked(); } }
    private void ReleaseSender() { lock (gate) { if (--senders < 0) throw new InvalidOperationException("A sender admission was released twice."); } }
    public void Dispose()
    {
        var queued = new List<string>();
        lock (gate)
        {
            if (disposed) return; disposed = true; timer?.Dispose(); timer = null;
            foreach (Entry entry in entries.Values)
            {
                if (entry.State == State.Terminal) RetireLocked(entry);
                else if (entry.State == State.Queued)
                { entry.CancellationReason ??= "runtime_closed"; queued.Add(entry.Request.RequestId!); }
            }
        }
        foreach (string id in queued) CancelQueued(id, "runtime_closed");
    }

    internal sealed class TerminalReply : IDisposable
    {
        private RequestResultArena.FrozenTerminal.SenderLoan? bytes;
        private Action? release;
        internal int StatusCode { get; }
        internal int Length => (bytes ?? throw new ObjectDisposedException(nameof(TerminalReply))).Length;
        internal TerminalReply(int statusCode, RequestResultArena.FrozenTerminal.SenderLoan? bytes, Action? release)
        { StatusCode = statusCode; this.bytes = bytes; this.release = release; }
        internal void WriteTo(System.IO.Stream stream) =>
            (bytes ?? throw new ObjectDisposedException(nameof(TerminalReply))).WriteTo(stream);
        public void Dispose()
        {
            Interlocked.Exchange(ref bytes, null)?.Dispose();
            Interlocked.Exchange(ref release, null)?.Invoke();
        }
    }

    private static MutationAuthorizationRequest Authorization(PlayerEnvironmentActionRequest request) =>
        new(request.ClientSessionId, request.ControllerLeaseId, request.ControllerGeneration);
    private static PlayerEnvironmentAttribution Public(MutationAttribution value) => new(value.RuntimeInstanceId,
        value.ClientSessionId, value.ClientInstanceId, value.ProductId, value.ProductName, value.ProductVersion,
        value.ControllerLeaseId, value.ControllerGeneration);
    private static bool ValidShape(PlayerEnvironmentActionRequest request) =>
        MutationControllerCoordinator.SafeIdentifier(request.ExpectedSnapshotId, 128)
        && MutationControllerCoordinator.SafeIdentifier(request.BoundActionId, 128)
        && MutationControllerCoordinator.SafeIdentifier(request.ClientSessionId, 128)
        && MutationControllerCoordinator.SafeIdentifier(request.ControllerLeaseId, 128)
        && request.ControllerGeneration is > 0
        && request.InputProfile is null or NativeLogicalContract.Profile or TextMenuContract.Profile
            or TextMenuV2Contract.Profile or PlayerEnvironmentContract.OrdinaryRewardPageProfile
            or PlayerEnvironmentContract.RewardPotionPageProfile;
    private static string Fingerprint(PlayerEnvironmentActionRequest request) => Convert.ToHexString(SHA256.HashData(
        JsonSerializer.SerializeToUtf8Bytes(new { request.ExpectedSnapshotId, request.BoundActionId,
            request.ClientSessionId, request.ControllerLeaseId, request.ControllerGeneration, request.InputProfile }))).ToLowerInvariant();
}
