using System.Runtime.CompilerServices;

namespace STS2HumanAnnotator.Core;

/// <summary>
/// Process-local one-to-one binding between an exact native owner reference
/// and an exact Human witness. Neither side may replace a live binding.
/// </summary>
public sealed class ExactOwnerWitnessBindingTable<T>
    where T : class
{
    private sealed record OwnerBinding(string WitnessId);

    private readonly object _gate = new();
    private readonly ConditionalWeakTable<T, OwnerBinding> _owners = new();
    private readonly ExactWitnessBindingTable<T> _witnesses = new();

    /// <summary>End a recording lifetime, including carriers whose native owner
    /// outlives the run (for example RunManager after victory without ActEntered).</summary>
    public void Clear()
    {
        lock (_gate)
        {
            _owners.Clear();
            _witnesses.Clear();
        }
    }

    public bool TryBind(T? owner, string? witnessId)
    {
        if (owner == null || string.IsNullOrWhiteSpace(witnessId))
            return false;
        lock (_gate)
        {
            if (_owners.TryGetValue(owner, out OwnerBinding? existing))
                return string.Equals(existing.WitnessId, witnessId, StringComparison.Ordinal);
            if (!_witnesses.TryBind(witnessId, owner))
                return false;
            _owners.Add(owner, new OwnerBinding(witnessId));
            return true;
        }
    }

    public bool Contains(T? owner)
    {
        lock (_gate)
            return owner != null && _owners.TryGetValue(owner, out _);
    }

    public bool TryGetWitness(T? owner, out string? witnessId)
    {
        witnessId = null;
        if (owner == null)
            return false;
        lock (_gate)
            return _owners.TryGetValue(owner, out OwnerBinding? binding)
                && (witnessId = binding.WitnessId) != null;
    }

    public bool TryGetOwner(string witnessId, out T? owner)
    {
        lock (_gate)
            return _witnesses.TryGet(witnessId, out owner);
    }

    public string? Take(T? owner)
    {
        if (owner == null)
            return null;
        lock (_gate)
        {
            if (!_owners.TryGetValue(owner, out OwnerBinding? binding))
                return null;
            _owners.Remove(owner);
            _witnesses.Remove(binding.WitnessId, owner);
            return binding.WitnessId;
        }
    }

    public bool TakeIfMatches(T? owner, string? expectedWitnessId)
    {
        if (owner == null || string.IsNullOrWhiteSpace(expectedWitnessId))
            return false;
        lock (_gate)
        {
            if (!_owners.TryGetValue(owner, out OwnerBinding? binding)
                || !string.Equals(binding.WitnessId, expectedWitnessId, StringComparison.Ordinal))
                return false;
            _owners.Remove(owner);
            _witnesses.Remove(binding.WitnessId, owner);
            return true;
        }
    }

    public bool TryTransfer(T? source, T? destination, string? expectedWitnessId)
    {
        if (source == null || destination == null
            || string.IsNullOrWhiteSpace(expectedWitnessId)
            || ReferenceEquals(source, destination))
            return false;
        lock (_gate)
        {
            if (!_owners.TryGetValue(source, out OwnerBinding? sourceBinding)
                || !string.Equals(sourceBinding.WitnessId, expectedWitnessId, StringComparison.Ordinal)
                || _owners.TryGetValue(destination, out _))
                return false;
            _owners.Remove(source);
            _owners.Add(destination, sourceBinding);
            _witnesses.Remove(expectedWitnessId, source);
            if (_witnesses.TryBind(expectedWitnessId, destination))
                return true;

            // Restore the exact source carrier if the reverse index rejects
            // the destination unexpectedly. No witness is silently lost.
            _owners.Remove(destination);
            _owners.Add(source, sourceBinding);
            _witnesses.TryBind(expectedWitnessId, source);
            return false;
        }
    }
}
