using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Threading;
using STS2Platform.NativeFoundation;

namespace STS2Connector.NativeUi;

internal sealed class NativeEntityRegistry : INativeReferentIdentity
{
    private const int PruneInterval = 256;
    private const int PruneBatchSize = 512;
    private sealed record Identity(string Value);

    private readonly string _sessionPrefix = Guid.NewGuid().ToString("N")[..8];
    private readonly ConditionalWeakTable<object, Identity> _identities = new();
    private readonly ConcurrentDictionary<string, WeakReference<object>> _entities =
        new(StringComparer.Ordinal);
    private readonly ConcurrentQueue<string> _pruneCandidates = new();
    private readonly object _pruneGate = new();
    private long _lastPrunedIdentity;
    private long _nextIdentity;

    public string GetId(object entity, string kind)
    {
        Identity identity = _identities.GetValue(entity, _ =>
        {
            long sequence = Interlocked.Increment(ref _nextIdentity);
            Identity identity = new($"{kind}_{_sessionPrefix}_{sequence:x}");
            _pruneCandidates.Enqueue(identity.Value);
            return identity;
        });
        _entities[identity.Value] = new WeakReference<object>(entity);
        PruneIfNeeded();
        return identity.Value;
    }

    // Diagnostic observation only: never allocate a referent or advance the
    // public identity sequence. An existing ID does not prove a prior Snapshot.
    internal bool TryGetExistingId(object entity, out string? id)
    {
        if (_identities.TryGetValue(entity, out Identity? identity))
        {
            id = identity.Value;
            return true;
        }
        id = null;
        return false;
    }

    internal int TrackedReferenceCount => _entities.Count;

    internal int PruneDeadEntries()
    {
        int removed = 0;
        for (int index = 0; index < PruneBatchSize; index++)
        {
            if (!_pruneCandidates.TryDequeue(out string? entityId))
                break;
            if (!_entities.TryGetValue(entityId, out WeakReference<object>? reference))
                continue;
            if (reference.TryGetTarget(out _))
            {
                _pruneCandidates.Enqueue(entityId);
                continue;
            }
            if (_entities.TryRemove(entityId, out _))
                removed++;
        }
        return removed;
    }

    public bool TryResolve<T>(string entityId, out T? entity) where T : class
    {
        entity = null;
        if (!_entities.TryGetValue(entityId, out WeakReference<object>? reference)
            || !reference.TryGetTarget(out object? target))
        {
            _entities.TryRemove(entityId, out _);
            return false;
        }
        if (target is not T typed)
            return false;

        entity = typed;
        return true;
    }

    public IReadOnlyDictionary<string, object> CaptureExactReferences(
        IEnumerable<string> entityIds)
    {
        return entityIds
            .Distinct(StringComparer.Ordinal)
            .Select(entityId =>
            {
                object? target = null;
                bool found = _entities.TryGetValue(
                                 entityId,
                                 out WeakReference<object>? reference)
                             && reference.TryGetTarget(out target);
                return (entityId, found, target);
            })
            .Where(entry => entry.found && entry.target != null)
            .ToDictionary(
                entry => entry.entityId,
                entry => entry.target!,
                StringComparer.Ordinal);
    }

    private void PruneIfNeeded()
    {
        long identityCount = Volatile.Read(ref _nextIdentity);
        if (identityCount - Volatile.Read(ref _lastPrunedIdentity) < PruneInterval)
            return;

        lock (_pruneGate)
        {
            identityCount = Volatile.Read(ref _nextIdentity);
            if (identityCount - _lastPrunedIdentity < PruneInterval)
                return;
            _lastPrunedIdentity = identityCount;
        }

        // This is bounded cache maintenance, not a gameplay or authority
        // decision. Avoid scanning the registry on every observation frame.
        PruneDeadEntries();
    }
}
