using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Threading;
using STS2Platform.NativeFoundation;

namespace STS2Connector.NativeUi;

internal sealed class NativeEntityRegistry : INativeReferentIdentity
{
    private const int PruneInterval = 256;
    private const int PruneBatchSize = 512;
    private sealed record Identity(string Value);

    private const int AliasEntropyBytes = 24;
    private readonly Func<byte[]> _aliasEntropy;
    private readonly object _identityGate = new();
    private readonly ConditionalWeakTable<object, Identity> _identities = new();
    private readonly ConcurrentDictionary<string, WeakReference<object>> _entities =
        new(StringComparer.Ordinal);
    private readonly ConcurrentQueue<string> _pruneCandidates = new();
    private readonly object _pruneGate = new();
    private long _lastPrunedIdentity;
    private long _nextIdentity;

    public NativeEntityRegistry() : this(() => RandomNumberGenerator.GetBytes(AliasEntropyBytes)) { }

    // Deterministic entropy injection is for contract tests only; production
    // always uses the cryptographic constructor above.
    internal NativeEntityRegistry(Func<byte[]> aliasEntropy) =>
        _aliasEntropy = aliasEntropy ?? throw new ArgumentNullException(nameof(aliasEntropy));

    public string GetId(object entity, string kind)
    {
        Identity identity;
        lock (_identityGate)
        {
            identity = _identities.GetValue(entity, _ =>
            {
                // No native enumeration, allocation counter, session sequence,
                // object hash or hidden order contributes to a public alias.
                for (int attempt = 0; attempt < 8; attempt++)
                {
                    byte[] entropy = _aliasEntropy();
                    if (entropy.Length != AliasEntropyBytes)
                        throw new InvalidOperationException("Native identity requires 192 bits of entropy.");
                    Identity created = new($"{kind}_{Convert.ToHexString(entropy).ToLowerInvariant()}");
                    if (!_entities.TryAdd(created.Value, new WeakReference<object>(entity))) continue;
                    Interlocked.Increment(ref _nextIdentity);
                    _pruneCandidates.Enqueue(created.Value);
                    return created;
                }
                throw new InvalidOperationException("Native identity entropy repeated; no alias was assigned.");
            });
        }
        PruneIfNeeded();
        return identity.Value;
    }

    // Diagnostic observation only: never allocate a referent or advance the
    // public alias. An existing ID does not prove a prior Snapshot.
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
