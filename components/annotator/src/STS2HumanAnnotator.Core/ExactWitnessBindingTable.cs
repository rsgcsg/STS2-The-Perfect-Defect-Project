using System;
using System.Collections.Generic;

namespace STS2HumanAnnotator.Core;

/// <summary>
/// Process-local exact witness-to-reference binding. A witness may be rebound
/// idempotently to the same object, but an alive different object is an
/// ambiguity and is rejected. This prevents a child callback from replacing
/// another root's carrier with a latest-wins mapping.
/// </summary>
public sealed class ExactWitnessBindingTable<T>
    where T : class
{
    private readonly object _gate = new();
    private readonly Dictionary<string, WeakReference<T>> _bindings =
        new(StringComparer.Ordinal);

    public void Clear()
    {
        lock (_gate)
            _bindings.Clear();
    }

    public bool TryBind(string witnessId, T owner)
    {
        if (string.IsNullOrWhiteSpace(witnessId) || owner == null)
            return false;
        lock (_gate)
        {
            if (_bindings.TryGetValue(witnessId, out WeakReference<T>? existing))
            {
                if (existing.TryGetTarget(out T? existingOwner))
                    return ReferenceEquals(existingOwner, owner);
                _bindings.Remove(witnessId);
            }
            _bindings[witnessId] = new WeakReference<T>(owner);
            return true;
        }
    }

    public bool TryGet(string witnessId, out T? owner)
    {
        owner = null;
        if (string.IsNullOrWhiteSpace(witnessId))
            return false;
        lock (_gate)
        {
            if (!_bindings.TryGetValue(witnessId, out WeakReference<T>? existing)
                || !existing.TryGetTarget(out owner))
            {
                _bindings.Remove(witnessId);
                return false;
            }
            return true;
        }
    }

    public bool Remove(string witnessId, T expectedOwner)
    {
        if (string.IsNullOrWhiteSpace(witnessId) || expectedOwner == null)
            return false;
        lock (_gate)
        {
            if (!_bindings.TryGetValue(witnessId, out WeakReference<T>? existing)
                || !existing.TryGetTarget(out T? owner)
                || !ReferenceEquals(owner, expectedOwner))
                return false;
            return _bindings.Remove(witnessId);
        }
    }
}
