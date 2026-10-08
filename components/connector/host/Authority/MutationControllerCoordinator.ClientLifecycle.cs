using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;

namespace STS2Connector.Authority;

internal sealed partial class MutationControllerCoordinator
{
    internal const int DefaultClientIdleTtlMs = 30 * 60 * 1000;
    internal const int MaxClientSpentRequests = 65_536;
    private readonly Func<long> _monotonicClock;
    private readonly int _clientIdleTtlMs;
    private readonly bool _enableDeadlineTimer;
    private readonly int _maxClientSpentRequests;
    private Timer? _clientDeadlineTimer;
    internal event Action<MutationClientClosure>? ClientClosed;

    internal MutationAdmission ValidateActiveControl(MutationAuthorizationRequest request)
    {
        lock (_gate)
        {
            DateTimeOffset now = _clock();
            ExpireController(now);
            if (!TryGetClient(request.ClientSessionId, now, out MutableClient? client))
                return MutationAdmission.Reject("client_session_not_found", "The original client is not active in this Host runtime.");
            if (!MatchesCurrentLease(new(request.ClientSessionId, request.ControllerLeaseId, request.ControllerGeneration)))
                return MutationAdmission.Reject("controller_lease_stale", "The original controller lease and generation are required.");
            MutableLease controller = _controller!;
            return MutationAdmission.Allow(new(_runtimeInstanceId, client!.ClientSessionId, client.ClientInstanceId,
                client.ProductId, client.ProductName, client.ProductVersion, controller.LeaseId, controller.Generation));
        }
    }

    // New-request admission occurs under RequestNamespace -> Authority. The
    // caller has already reserved capacity. Closure callbacks run asynchronously
    // after Authority unlock and wait for the namespace's admission transaction.
    internal MutationRequestAdmission TryAdmitRequest(MutationAuthorizationRequest request)
    {
        lock (_gate)
        {
            DateTimeOffset now = _clock();
            ExpireController(now);
            if (!TryGetClient(request.ClientSessionId, now, out MutableClient? client))
                return new(MutationAdmission.Reject("client_session_not_found",
                    "The original client is not active in this Host runtime."), null);
            if (!MatchesCurrentLease(new(request.ClientSessionId, request.ControllerLeaseId,
                    request.ControllerGeneration)))
                return new(MutationAdmission.Reject("controller_lease_stale",
                    "The original controller lease and generation are required."), null);
            if (client!.Lifetime.SpentRequestCount >= _maxClientSpentRequests)
                return new(MutationAdmission.Reject("request_capacity_exceeded",
                    "This original client exhausted its runtime request-ID capacity."), null);
            client.Lifetime.SpendRequest();
            TouchClient(client, now);
            MutableLease controller = _controller!;
            return new(MutationAdmission.Allow(new(_runtimeInstanceId, client.ClientSessionId,
                client.ClientInstanceId, client.ProductId, client.ProductName, client.ProductVersion,
                controller.LeaseId, controller.Generation)), client.Lifetime);
        }
    }

    internal bool IsActiveClient(string clientSessionId)
    {
        lock (_gate)
        {
            ExpireController(_clock());
            return _clientsBySession.TryGetValue(clientSessionId, out MutableClient? client)
                && !client.Lifetime.IsClosed;
        }
    }

    internal bool TryTouchActiveClient(string clientSessionId)
    {
        lock (_gate)
        {
            DateTimeOffset now = _clock();
            ExpireController(now);
            if (!TryGetClient(clientSessionId, now, out MutableClient? client)) return false;
            TouchClient(client!, now);
            return true;
        }
    }

    internal MutationClientRevocationResult Revoke(MutationClientRevocationRequest request)
    {
        if (!SafeIdentifier(request.RuntimeInstanceId, 128)
            || !SafeIdentifier(request.ClientSessionId, 128))
            return new(_runtimeInstanceId, request.ClientSessionId ?? "", "invalid_client_revocation", false);
        lock (_gate)
        {
            DateTimeOffset now = _clock();
            ExpireController(now);
            if (request.RuntimeInstanceId != _runtimeInstanceId)
                return new(_runtimeInstanceId, request.ClientSessionId!, "runtime_instance_mismatch", false);
            if (!_clientsBySession.TryGetValue(request.ClientSessionId!, out MutableClient? client))
                return new(_runtimeInstanceId, request.ClientSessionId!, "client_session_not_found", false);
            CloseClient(client, "client_session_revoked");
            ArmClientDeadlineTimer();
            return new(_runtimeInstanceId, client.ClientSessionId, "client_revoked", true);
        }
    }

    internal void TickClientDeadlines()
    {
        lock (_gate)
        {
            ExpireController(_clock());
            ArmClientDeadlineTimer();
        }
    }

    private void TouchClient(MutableClient client, DateTimeOffset now)
    {
        if (client.Lifetime.IsClosed) return;
        client.Touch(now);
        client.IdleDeadline = checked(_monotonicClock() + _clientIdleTtlMs);
        ArmClientDeadlineTimer();
    }

    private void ExpireClients(DateTimeOffset now)
    {
        long monotonicNow = _monotonicClock();
        bool changed = false;
        foreach (MutableClient client in _clientsBySession.Values)
            if (!client.Lifetime.IsClosed && monotonicNow >= client.IdleDeadline)
            { CloseClient(client, "client_session_expired"); changed = true; }
        if (changed) ArmClientDeadlineTimer();
    }

    private void CloseClient(MutableClient client, string reason)
    {
        if (!client.Lifetime.Close(reason)) return;
        if (_sessionByInstance.GetValueOrDefault(client.ClientInstanceId) == client.ClientSessionId)
            _sessionByInstance.Remove(client.ClientInstanceId);
        if (_controller?.ClientSessionId == client.ClientSessionId) RevokeController();
        var closure = new MutationClientClosure(_runtimeInstanceId, client.ClientSessionId, reason);
        Action<MutationClientClosure>? observer = ClientClosed;
        if (observer is not null) ThreadPool.QueueUserWorkItem(_ =>
        {
            // The originating API may itself be called under a Namespace/Hub
            // gate. Never invoke an owner callback synchronously from Authority.
            lock (_gate) { }
            foreach (Action<MutationClientClosure> handler in observer.GetInvocationList())
                try { handler(closure); } catch { /* Closure is already permanent. */ }
        });
    }

    private void ArmClientDeadlineTimer()
    {
        if (!_enableDeadlineTimer) return;
        long next = _clientsBySession.Values.Where(client => !client.Lifetime.IsClosed)
            .Select(client => client.IdleDeadline).DefaultIfEmpty(long.MaxValue).Min();
        if (next == long.MaxValue)
        { _clientDeadlineTimer?.Dispose(); _clientDeadlineTimer = null; return; }
        _clientDeadlineTimer ??= new Timer(_ => TickClientDeadlines(), null,
            Timeout.Infinite, Timeout.Infinite);
        long remaining = Math.Max(1, next - _monotonicClock());
        _clientDeadlineTimer.Change((int)Math.Min(int.MaxValue, remaining), Timeout.Infinite);
    }

    internal static bool SafeIdentifier(string? value, int maxLength) =>
        !string.IsNullOrWhiteSpace(value) && value.Length <= maxLength
        && value.All(character => char.IsAsciiLetterOrDigit(character)
            || character is '-' or '_' or '.');

    private static void ValidateRegistration(MutationClientRegistrationRequest request)
    {
        if (!SafeIdentifier(request.ClientInstanceId, 128) || !SafeIdentifier(request.ProductId, 64)
            || !SafeIdentifier(request.ProductVersion, 64) || request.ProductName is not { Length: > 0 and <= 128 } name
            || string.IsNullOrWhiteSpace(name) || name.Any(char.IsControl))
            throw new ArgumentException("Client metadata must obey the bounded registration contract.", nameof(request));
        for (int index = 0; index < name.Length; index++)
            if (char.IsSurrogate(name[index]))
            {
                if (!char.IsHighSurrogate(name[index]) || index + 1 == name.Length
                    || !char.IsLowSurrogate(name[index + 1]))
                    throw new ArgumentException("Client metadata must contain Unicode scalar values.", nameof(request));
                index++;
            }
    }
}
