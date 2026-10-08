using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
namespace STS2Connector.Authority;

internal sealed class MutationWatchCapacityException : Exception { }

internal sealed partial class MutationControllerCoordinator
{
    public const int DefaultLeaseTtlMs = 30_000;
    public const int RecommendedRenewalMs = 10_000;
    // Registrations live only for one game runtime. Keep a generous runaway
    // bound without making normal repeated CLI/Agent launches exhaust control.
    private const int MaxRegisteredClients = 4_096;

    private readonly object _gate = new();
    private readonly string _runtimeInstanceId;
    private readonly TimeSpan _leaseTtl;
    private readonly Func<DateTimeOffset> _clock;
    private readonly Dictionary<string, MutableClient> _clientsBySession = new(StringComparer.Ordinal);
    private readonly Dictionary<string, string> _sessionByInstance = new(StringComparer.Ordinal);
    private MutableLease? _controller;
    private long _nextGeneration;
    private readonly Dictionary<long, Action> _controlWatches = new();
    private long _nextWatch;
    private Timer? _watchTimer;
    private const int MaxControlWatches = 128;

    public MutationControllerCoordinator(
        string runtimeInstanceId,
        int leaseTtlMs = DefaultLeaseTtlMs,
        Func<DateTimeOffset>? clock = null,
        Func<long>? monotonicClock = null,
        int clientIdleTtlMs = DefaultClientIdleTtlMs,
        bool enableDeadlineTimer = true,
        int maxClientSpentRequests = MaxClientSpentRequests)
    {
        if (string.IsNullOrWhiteSpace(runtimeInstanceId))
            throw new ArgumentException("A runtime instance id is required.", nameof(runtimeInstanceId));
        if (leaseTtlMs <= 0)
            throw new ArgumentOutOfRangeException(nameof(leaseTtlMs));

        _runtimeInstanceId = runtimeInstanceId;
        _leaseTtl = TimeSpan.FromMilliseconds(leaseTtlMs);
        _clock = clock ?? (() => DateTimeOffset.UtcNow);
        if (clientIdleTtlMs <= 0) throw new ArgumentOutOfRangeException(nameof(clientIdleTtlMs));
        _clientIdleTtlMs = clientIdleTtlMs;
        _monotonicClock = monotonicClock ?? (clock is null ? () => Environment.TickCount64
            : () => clock().UtcTicks / TimeSpan.TicksPerMillisecond);
        _enableDeadlineTimer = enableDeadlineTimer;
        if (maxClientSpentRequests is <= 0 or > MaxClientSpentRequests)
            throw new ArgumentOutOfRangeException(nameof(maxClientSpentRequests));
        _maxClientSpentRequests = maxClientSpentRequests;
    }

    public MutationClientRegistrationResult Register(MutationClientRegistrationRequest request)
    {
        ValidateRegistration(request);
        lock (_gate)
        {
            DateTimeOffset now = _clock();
            ExpireController(now);
            string instanceId = request.ClientInstanceId!;
            if (_sessionByInstance.TryGetValue(instanceId, out string? existingSession))
            {
                MutableClient existing = _clientsBySession[existingSession];
                if (!existing.Matches(request))
                    throw new InvalidOperationException(
                        "client_instance_id is already registered with different product metadata.");

                TouchClient(existing, now);
                return new MutationClientRegistrationResult(
                    MutationControlContract.ProtocolVersion,
                    _runtimeInstanceId,
                    existing.ToRecord(),
                    _controller?.ToInfo());
            }

            if (_clientsBySession.Count >= MaxRegisteredClients)
                throw new InvalidOperationException(
                    "The runtime client registry is full. Restart the Player Environment Host to clear local sessions.");

            string sessionId = "client_" + Guid.NewGuid().ToString("N");
            var client = new MutableClient(
                sessionId,
                instanceId,
                request.ProductId!,
                request.ProductName!,
                request.ProductVersion!,
                now);
            _clientsBySession[sessionId] = client;
            _sessionByInstance[instanceId] = sessionId;
            client.Lifetime = new(_runtimeInstanceId, sessionId);
            TouchClient(client, now);
            return new MutationClientRegistrationResult(
                MutationControlContract.ProtocolVersion,
                _runtimeInstanceId,
                client.ToRecord(),
                _controller?.ToInfo());
        }
    }

    public MutationControlSnapshot Snapshot()
    {
        lock (_gate)
        {
            ExpireController(_clock());
            return new MutationControlSnapshot(
                MutationControlContract.ProtocolVersion,
                _runtimeInstanceId,
                _clientsBySession.Values
                    .Where(client => !client.Lifetime.IsClosed)
                    .Select(client => client.ToRecord())
                    .OrderBy(client => client.RegisteredAt)
                    .ToArray(),
                _controller?.ToInfo());
        }
    }

    public MutationLeaseResult Acquire(MutationLeaseRequest request)
    {
        lock (_gate)
        {
            DateTimeOffset now = _clock();
            ExpireController(now);
            if (!TryGetClient(request.ClientSessionId, now, out MutableClient? client))
                return Rejected("client_session_not_found", "Register this client in the current Host runtime before acquiring control.");
            MutableClient activeClient = client!;

            if (_controller != null)
            {
                if (string.Equals(_controller.ClientSessionId, activeClient.ClientSessionId, StringComparison.Ordinal))
                {
                    TouchClient(activeClient, now);
                    return Accepted("controller_already_held", "This client already holds the controller lease.", activeClient);
                }
                return Rejected(
                    "controller_lease_held",
                    $"Mutation control is currently held by {_clientsBySession[_controller.ClientSessionId].ProductName}.");
            }

            _nextGeneration++;
            _controller = new MutableLease(
                "lease_" + Guid.NewGuid().ToString("N"),
                _nextGeneration,
                activeClient.ClientSessionId,
                now,
                now + _leaseTtl);
            TouchClient(activeClient, now);
            return Accepted("controller_acquired", "Mutation control was acquired for this Host runtime.", activeClient);
        }
    }

    public MutationLeaseResult Renew(MutationLeaseRequest request)
    {
        lock (_gate)
        {
            DateTimeOffset now = _clock();
            ExpireController(now);
            if (!TryGetClient(request.ClientSessionId, now, out MutableClient? client))
                return Rejected("client_session_not_found", "The client session does not belong to this Host runtime.");
            MutableClient activeClient = client!;
            if (!MatchesCurrentLease(request))
                return Rejected("controller_lease_stale", "The controller lease id or generation is no longer current.");

            _controller!.ExpiresAt = now + _leaseTtl;
            TouchClient(activeClient, now);
            return Accepted("controller_renewed", "Mutation control was renewed.", activeClient);
        }
    }

    public MutationLeaseResult Release(MutationLeaseRequest request)
    {
        lock (_gate)
        {
            DateTimeOffset now = _clock();
            ExpireController(now);
            if (!TryGetClient(request.ClientSessionId, now, out MutableClient? client))
                return Rejected("client_session_not_found", "The client session does not belong to this Host runtime.");
            MutableClient activeClient = client!;
            if (!MatchesCurrentLease(request))
                return Rejected("controller_lease_stale", "The controller lease id or generation is no longer current.");

            RevokeController();
            TouchClient(activeClient, now);
            return new MutationLeaseResult(
                MutationControlContract.ProtocolVersion,
                _runtimeInstanceId,
                "controller_released",
                "Mutation control was released.",
                activeClient.ToRecord(),
                null);
        }
    }

    public MutationAdmission Authorize(MutationAuthorizationRequest request)
    {
        lock (_gate)
        {
            DateTimeOffset now = _clock();
            ExpireController(now);
            if (!TryGetClient(request.ClientSessionId, now, out MutableClient? client))
            {
                return MutationAdmission.Reject(
                    "client_session_not_found",
                    "Mutation commands require a client session registered in the current Host runtime.");
            }
            MutableClient activeClient = client!;
            if (!MatchesCurrentLease(new MutationLeaseRequest(
                    request.ClientSessionId,
                    request.ControllerLeaseId,
                    request.ControllerGeneration)))
            {
                return MutationAdmission.Reject(
                    "controller_lease_stale",
                    "Mutation commands require the current controller lease id and generation.");
            }

            MutableLease activeController = _controller!;
            TouchClient(activeClient, now);
            return MutationAdmission.Allow(new MutationAttribution(
                _runtimeInstanceId,
                activeClient.ClientSessionId,
                activeClient.ClientInstanceId,
                activeClient.ProductId,
                activeClient.ProductName,
                activeClient.ProductVersion,
                activeController.LeaseId,
                activeController.Generation));
        }
    }

    // This accepted return is the input-start linearization boundary. The
    // caller retains the immutable admission, then dispatches outside _gate.
    // A later release/expiry prevents new starts; it cannot undo this start.
    public MutationAdmission TryBegin(MutationAuthorizationRequest request)
    {
        lock (_gate) return Authorize(request);
    }

    public bool TryWatch(MutationAuthorizationRequest request, Action lost, out IDisposable? watch)
    {
        ArgumentNullException.ThrowIfNull(lost);
        lock (_gate)
        {
            if (!Authorize(request).Accepted) { watch = null; return false; }
            if (_controlWatches.Count >= MaxControlWatches) throw new MutationWatchCapacityException();
            long id = checked(++_nextWatch);
            _controlWatches.Add(id, lost);
            _watchTimer ??= new Timer(_ => { lock (_gate) ExpireController(_clock()); }, null, 25, 25);
            watch = new ControlWatch(this, id);
            return true;
        }
    }

    private void RevokeController()
    {
        _controller = null;
        Action[] callbacks = _controlWatches.Values.ToArray();
        _controlWatches.Clear();
        _watchTimer?.Dispose(); _watchTimer = null;
        foreach (Action callback in callbacks)
            ThreadPool.QueueUserWorkItem(_ =>
            {
                // Cross the authority gate before invoking another owner.
                lock (_gate) { }
                try { callback(); } catch { /* Observer failure grants no control. */ }
            });
    }

    private sealed class ControlWatch(MutationControllerCoordinator owner, long id) : IDisposable
    {
        private MutationControllerCoordinator? coordinator = owner;
        public void Dispose()
        {
            var value = Interlocked.Exchange(ref coordinator, null);
            if (value == null) return;
            lock (value._gate)
            {
                value._controlWatches.Remove(id);
                if (value._controlWatches.Count == 0)
                { value._watchTimer?.Dispose(); value._watchTimer = null; }
            }
        }
    }

    public MutationControlCapability Capability() => new(
        "local_coordination_active",
        RegistrationRequiredForMutation: true,
        SingleController: true,
        ReadsRequireRegistration: false,
        LeaseTtlMs: (int)_leaseTtl.TotalMilliseconds,
        RecommendedRenewalMs,
        _runtimeInstanceId,
        new[]
        {
            "Client metadata is diagnostic attribution, not authentication.",
            "The lease coordinates local mutation clients; it does not defend against a malicious local process.",
            "Lease expiry blocks new commands but never cancels or retries a command already admitted to the ledger."
        });

    private bool TryGetClient(string? sessionId, DateTimeOffset now, out MutableClient? client)
    {
        if (sessionId != null && _clientsBySession.TryGetValue(sessionId, out client))
        {
            return !client.Lifetime.IsClosed;
        }
        client = null;
        return false;
    }

    private bool MatchesCurrentLease(MutationLeaseRequest request) =>
        _controller != null
        && string.Equals(_controller.ClientSessionId, request.ClientSessionId, StringComparison.Ordinal)
        && string.Equals(_controller.LeaseId, request.ControllerLeaseId, StringComparison.Ordinal)
        && _controller.Generation == request.ControllerGeneration;

    private void ExpireController(DateTimeOffset now)
    {
        ExpireClients(now);
        if (_controller != null && now >= _controller.ExpiresAt)
            RevokeController();
    }

    private MutationLeaseResult Accepted(
        string status,
        string detail,
        MutableClient client) =>
        new(
            MutationControlContract.ProtocolVersion,
            _runtimeInstanceId,
            status,
            detail,
            client.ToRecord(),
            _controller?.ToInfo());

    private MutationLeaseResult Rejected(string status, string detail) =>
        new(
            MutationControlContract.ProtocolVersion,
            _runtimeInstanceId,
            status,
            detail,
            null,
            _controller?.ToInfo());

    private sealed class MutableClient
    {
        public MutableClient(
            string clientSessionId,
            string clientInstanceId,
            string productId,
            string productName,
            string productVersion,
            DateTimeOffset registeredAt)
        {
            ClientSessionId = clientSessionId;
            ClientInstanceId = clientInstanceId;
            ProductId = productId;
            ProductName = productName;
            ProductVersion = productVersion;
            RegisteredAt = registeredAt;
            LastSeenAt = registeredAt;
        }

        public string ClientSessionId { get; }
        public string ClientInstanceId { get; }
        public string ProductId { get; }
        public string ProductName { get; }
        public string ProductVersion { get; }
        public DateTimeOffset RegisteredAt { get; }
        public DateTimeOffset LastSeenAt { get; private set; }
        internal MutationClientLifetime Lifetime = null!;
        internal long IdleDeadline;

        public bool Matches(MutationClientRegistrationRequest request) =>
            string.Equals(ProductId, request.ProductId, StringComparison.Ordinal)
            && string.Equals(ProductName, request.ProductName, StringComparison.Ordinal)
            && string.Equals(ProductVersion, request.ProductVersion, StringComparison.Ordinal);

        public void Touch(DateTimeOffset now) => LastSeenAt = now;

        public MutationClient ToRecord() => new(
            ClientSessionId,
            ClientInstanceId,
            ProductId,
            ProductName,
            ProductVersion,
            RegisteredAt,
            LastSeenAt);
    }

    private sealed class MutableLease
    {
        public MutableLease(
            string leaseId,
            long generation,
            string clientSessionId,
            DateTimeOffset acquiredAt,
            DateTimeOffset expiresAt)
        {
            LeaseId = leaseId;
            Generation = generation;
            ClientSessionId = clientSessionId;
            AcquiredAt = acquiredAt;
            ExpiresAt = expiresAt;
        }

        public string LeaseId { get; }
        public long Generation { get; }
        public string ClientSessionId { get; }
        public DateTimeOffset AcquiredAt { get; }
        public DateTimeOffset ExpiresAt { get; set; }

        public MutationLease ToInfo() => new(
            "active",
            LeaseId,
            Generation,
            ClientSessionId,
            AcquiredAt,
            ExpiresAt);
    }
}
