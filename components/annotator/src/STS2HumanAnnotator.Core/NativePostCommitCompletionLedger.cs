namespace STS2HumanAnnotator.Core;

/// <summary>
/// Describes the native completion that a direct UI root is waiting for. The
/// nullable native identities are optional constraints, never substitutes for
/// the root action identity carried by a completion signal.
/// </summary>
public sealed record NativePostCommitCompletionExpectation(
    string Family,
    string Kind,
    string? NativeOwnerWitnessId = null,
    string? NativeOperandWitnessId = null,
    string? NativeLineageWitnessId = null,
    IReadOnlyList<string>? AlternativeKinds = null)
{
    public bool AcceptsKind(string kind) =>
        string.Equals(Kind, kind, StringComparison.Ordinal)
        || AlternativeKinds?.Contains(kind, StringComparer.Ordinal) == true;
}

public sealed record NativePostCommitCompletionRegistration(
    string SessionId,
    long Generation,
    string ActionWitnessId,
    NativePostCommitCompletionExpectation Expectation);

/// <summary>
/// A completion signal captured at the exact native callback. It is queued
/// only to cross back to the game thread; the signal itself carries the
/// identity needed to reject stale, unmatched, or ambiguous completions.
/// </summary>
public sealed record NativePostCommitCompletion(
    string SessionId,
    long Generation,
    string CompletionId,
    string Family,
    string Kind,
    string TaskWitnessId,
    bool Succeeded,
    string? ActionWitnessId = null,
    string? NativeOwnerWitnessId = null,
    string? NativeOperandWitnessId = null,
    string? NativeLineageWitnessId = null);

public sealed record NativePostCommitCompletionResolution(
    string Status,
    NativePostCommitCompletion? Completion,
    NativePostCommitCompletionRegistration? Registration,
    string? Detail)
{
    public bool IsMatched => string.Equals(Status, "matched", StringComparison.Ordinal);

    public bool IsFailure => IsMatched && Completion?.Succeeded == false;
}

public sealed record NativeTaskObservation(
    string SessionId,
    long Generation,
    string Kind,
    string TaskWitnessId,
    string? NativeOwnerWitnessId = null,
    string? NativeOperandWitnessId = null,
    string? NativeLineageWitnessId = null);

public sealed record NativeTaskBinding(
    string SessionId,
    long Generation,
    string ActionWitnessId,
    string Family,
    string Kind,
    string TaskWitnessId,
    string? NativeOwnerWitnessId,
    string? NativeOperandWitnessId,
    string? NativeLineageWitnessId);

public sealed record NativeTaskBindingResolution(
    string Status,
    NativeTaskBinding? Binding,
    string? Detail)
{
    public bool IsMatched => string.Equals(Status, "matched", StringComparison.Ordinal);
}

public sealed record NativeTaskCompletion(
    string SessionId,
    long Generation,
    string CompletionId,
    string TaskWitnessId,
    bool Succeeded)
{
    public string? NativeTaskStatus { get; init; }
    public bool? NativeBooleanResult { get; init; }
}

/// <summary>
/// Binds native operations to the exact Human root that staged them, then
/// carries that identity through asynchronous completion. It deliberately has
/// no FIFO/current-waiting fallback.
/// </summary>
public sealed class NativePostCommitCompletionLedger
{
    private readonly int _capacity;
    private readonly Dictionary<string, NativePostCommitCompletionRegistration> _registrations =
        new(StringComparer.Ordinal);
    private readonly Dictionary<string, NativeTaskBinding> _taskBindings =
        new(StringComparer.Ordinal);

    public NativePostCommitCompletionLedger(int capacity = 128)
    {
        if (capacity <= 0)
            throw new ArgumentOutOfRangeException(nameof(capacity));
        _capacity = capacity;
    }

    public int Count => _registrations.Count + _taskBindings.Count;

    /// <summary>
    /// Reports whether this exact native Task kind is currently expected by a
    /// staged Human root in the same session generation. Native callbacks can
    /// legitimately occur without a Human root (for example, an internal
    /// continuation); those observations must not become phantom
    /// invalidations. An expectation that exists but fails identity matching
    /// remains fail-closed at BindTask.
    /// </summary>
    public bool HasPendingExpectation(
        string sessionId,
        long generation,
        string kind)
    {
        if (string.IsNullOrWhiteSpace(sessionId)
            || generation <= 0
            || string.IsNullOrWhiteSpace(kind))
        {
            return false;
        }

        return _registrations.Values.Any(registration =>
            string.Equals(registration.SessionId, sessionId, StringComparison.Ordinal)
            && registration.Generation == generation
            && registration.Expectation.AcceptsKind(kind));
    }

    public bool Register(NativePostCommitCompletionRegistration registration)
    {
        ArgumentNullException.ThrowIfNull(registration);
        if (string.IsNullOrWhiteSpace(registration.SessionId)
            || registration.Generation <= 0
            || string.IsNullOrWhiteSpace(registration.ActionWitnessId)
            || string.IsNullOrWhiteSpace(registration.Expectation.Family)
            || string.IsNullOrWhiteSpace(registration.Expectation.Kind))
        {
            return false;
        }
        if (_registrations.ContainsKey(registration.ActionWitnessId)
            || _taskBindings.Values.Any(binding => string.Equals(
                binding.ActionWitnessId,
                registration.ActionWitnessId,
                StringComparison.Ordinal))
            || Count >= _capacity)
        {
            return false;
        }
        _registrations.Add(registration.ActionWitnessId, registration);
        return true;
    }

    /// <summary>
    /// Binds a native Task to exactly one staged Human root while the native
    /// method's owner and operands are still available. The later Task
    /// continuation uses this durable binding and never reads an ambient UI
    /// scope or chooses a current/FIFO root.
    /// </summary>
    public NativeTaskBindingResolution BindTask(
        NativeTaskObservation observation,
        string? expectedActionWitnessId = null)
    {
        ArgumentNullException.ThrowIfNull(observation);
        if (string.IsNullOrWhiteSpace(observation.SessionId)
            || observation.Generation <= 0
            || string.IsNullOrWhiteSpace(observation.Kind)
            || string.IsNullOrWhiteSpace(observation.TaskWitnessId)
            || _taskBindings.ContainsKey(observation.TaskWitnessId))
        {
            return new NativeTaskBindingResolution(
                "no_match",
                null,
                "The native Task observation is malformed or was already bound.");
        }

        NativePostCommitCompletionRegistration[] matches = expectedActionWitnessId == null
            ? _registrations.Values
                .Where(registration => Matches(registration, observation))
                .ToArray()
            : _registrations.TryGetValue(
                    expectedActionWitnessId,
                    out NativePostCommitCompletionRegistration? exact)
                && Matches(exact, observation)
                ? new[] { exact }
                : Array.Empty<NativePostCommitCompletionRegistration>();
        if (matches.Length != 1)
        {
            return new NativeTaskBindingResolution(
                matches.Length == 0 ? "no_match" : "ambiguous",
                null,
                matches.Length == 0
                    ? expectedActionWitnessId == null
                        ? "No staged Human root matches the exact native Task identity."
                        : "The supplied Human root identity does not match the exact native Task identity."
                    : "More than one staged Human root matches the native Task identity.");
        }

        NativePostCommitCompletionRegistration registration = matches[0];
        _registrations.Remove(registration.ActionWitnessId);
        NativePostCommitCompletionExpectation expectation = registration.Expectation;
        var binding = new NativeTaskBinding(
            registration.SessionId,
            registration.Generation,
            registration.ActionWitnessId,
            expectation.Family,
            observation.Kind,
            observation.TaskWitnessId,
            observation.NativeOwnerWitnessId,
            observation.NativeOperandWitnessId,
            observation.NativeLineageWitnessId);
        _taskBindings.Add(binding.TaskWitnessId, binding);
        return new NativeTaskBindingResolution("matched", binding, null);
    }

    /// <summary>
    /// Resolves an exact task completion without consuming its binding. The
    /// runtime removes the binding only after the resulting terminal semantic
    /// event is durably appended.
    /// </summary>
    public NativePostCommitCompletionResolution PreviewTaskCompletion(
        NativeTaskCompletion completion)
    {
        ArgumentNullException.ThrowIfNull(completion);
        if (string.IsNullOrWhiteSpace(completion.SessionId)
            || completion.Generation <= 0
            || string.IsNullOrWhiteSpace(completion.CompletionId)
            || string.IsNullOrWhiteSpace(completion.TaskWitnessId)
            || !_taskBindings.TryGetValue(completion.TaskWitnessId, out NativeTaskBinding? binding)
            || !string.Equals(binding.SessionId, completion.SessionId, StringComparison.Ordinal)
            || binding.Generation != completion.Generation)
        {
            return new NativePostCommitCompletionResolution(
                "no_match",
                null,
                null,
                "No durable native Task binding matches this completion.");
        }

        var signal = new NativePostCommitCompletion(
            completion.SessionId,
            completion.Generation,
            completion.CompletionId,
            binding.Family,
            binding.Kind,
            binding.TaskWitnessId,
            completion.Succeeded,
            binding.ActionWitnessId,
            binding.NativeOwnerWitnessId,
            binding.NativeOperandWitnessId,
            binding.NativeLineageWitnessId);
        return new NativePostCommitCompletionResolution(
            "matched",
            signal,
            new NativePostCommitCompletionRegistration(
                binding.SessionId,
                binding.Generation,
                binding.ActionWitnessId,
                new NativePostCommitCompletionExpectation(
                    binding.Family,
                    binding.Kind,
                    binding.NativeOwnerWitnessId,
                    binding.NativeOperandWitnessId,
                    binding.NativeLineageWitnessId)),
            null);
    }

    public bool CommitTaskCompletion(NativeTaskCompletion completion)
    {
        ArgumentNullException.ThrowIfNull(completion);
        if (!_taskBindings.TryGetValue(completion.TaskWitnessId, out NativeTaskBinding? binding)
            || !string.Equals(binding.SessionId, completion.SessionId, StringComparison.Ordinal)
            || binding.Generation != completion.Generation)
        {
            return false;
        }
        return _taskBindings.Remove(binding.TaskWitnessId);
    }

    /// <summary>
    /// Reverses only the in-memory registration-to-Task transfer when the
    /// caller cannot complete its exact owner-carrier compare/remove. No
    /// semantic event has been appended at this point.
    /// </summary>
    public bool RollbackTaskBinding(string taskWitnessId)
    {
        if (string.IsNullOrWhiteSpace(taskWitnessId)
            || !_taskBindings.TryGetValue(taskWitnessId, out NativeTaskBinding? binding)
            || _registrations.ContainsKey(binding.ActionWitnessId))
            return false;
        var registration = new NativePostCommitCompletionRegistration(
            binding.SessionId,
            binding.Generation,
            binding.ActionWitnessId,
            new NativePostCommitCompletionExpectation(
                binding.Family,
                binding.Kind,
                binding.NativeOwnerWitnessId,
                binding.NativeOperandWitnessId,
                binding.NativeLineageWitnessId));
        _taskBindings.Remove(taskWitnessId);
        _registrations.Add(registration.ActionWitnessId, registration);
        return true;
    }

    /// <summary>
    /// Compatibility convenience for callers that have no external durable
    /// transaction. Runtime evidence ingress uses Preview + Commit instead.
    /// </summary>
    public NativePostCommitCompletionResolution CompleteTask(NativeTaskCompletion completion)
    {
        NativePostCommitCompletionResolution resolution = PreviewTaskCompletion(completion);
        if (resolution.IsMatched)
            CommitTaskCompletion(completion);
        return resolution;
    }

    public bool Remove(string actionWitnessId)
    {
        if (string.IsNullOrWhiteSpace(actionWitnessId))
            return false;
        bool removed = _registrations.Remove(actionWitnessId);
        foreach (string taskWitnessId in _taskBindings
                     .Where(value => string.Equals(
                         value.Value.ActionWitnessId,
                         actionWitnessId,
                         StringComparison.Ordinal))
                     .Select(value => value.Key)
                     .ToArray())
        {
            removed |= _taskBindings.Remove(taskWitnessId);
        }
        return removed;
    }

    public void Reset()
    {
        _registrations.Clear();
        _taskBindings.Clear();
    }

    private static bool Matches(
        NativePostCommitCompletionRegistration registration,
        NativeTaskObservation observation)
    {
        NativePostCommitCompletionExpectation expectation = registration.Expectation;
        return string.Equals(registration.SessionId, observation.SessionId, StringComparison.Ordinal)
               && registration.Generation == observation.Generation
               && expectation.AcceptsKind(observation.Kind)
               && MatchesOptional(expectation.NativeOwnerWitnessId, observation.NativeOwnerWitnessId)
               && MatchesOptional(expectation.NativeOperandWitnessId, observation.NativeOperandWitnessId)
               && MatchesOptional(expectation.NativeLineageWitnessId, observation.NativeLineageWitnessId);
    }

    private static bool MatchesOptional(string? expected, string? actual) =>
        expected == null
        || string.Equals(expected, actual, StringComparison.Ordinal);
}
