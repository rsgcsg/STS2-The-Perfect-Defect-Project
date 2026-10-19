using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Diagnostics;
using STS2Connector.Authority;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment.Witness;

/// <summary>
/// A native action already accepted through the shipped UI. Native references
/// enter this process-local API only and are never serialized or made executable.
/// </summary>
public sealed record ProcessLocalObservedAction(
    string Verb,
    object? Subject,
    IReadOnlyDictionary<string, object> Arguments);

public sealed record ProcessLocalNativeMatch(
    string Status,
    int MatchCount,
    string? BoundActionId,
    PlayerEnvironmentBoundAction? BoundAction,
    string Evidence,
    string? Detail)
{
    public ProcessLocalNativeInputAction? NativeInput { get; init; }
}

public sealed record ProcessLocalNativeInputAction(string ActionKey, string Verb,
    string? SubjectReferentId, IReadOnlyDictionary<string, string> Arguments, string? Label);

public sealed record ProcessLocalReadCapture(
    string Kind,
    string Status,
    PlayerEnvironmentReadResponse? Read,
    string? ErrorCode,
    string? Detail);

public sealed record ProcessLocalCaptureTiming(string Phase, long ElapsedMicroseconds);
public sealed record ProcessLocalPublicBasisCapture(string SnapshotId, ReadOnlyMemory<byte> Bytes, DateTimeOffset CapturedAt);

internal sealed class ProcessLocalCaptureProfiler
{
    private readonly List<ProcessLocalCaptureTiming> _timings = new();

    internal T Measure<T>(string phase, Func<T> operation)
    {
        long started = Stopwatch.GetTimestamp();
        try
        {
            return operation();
        }
        finally
        {
            long microseconds = (long)Math.Ceiling(
                (Stopwatch.GetTimestamp() - started) * 1_000_000d / Stopwatch.Frequency);
            _timings.Add(new ProcessLocalCaptureTiming(phase, microseconds));
        }
    }

    internal IReadOnlyList<ProcessLocalCaptureTiming> Snapshot() => _timings.ToArray();
}

/// <summary>
/// One immutable public Snapshot plus the exact Host-local bindings from the
/// same observation. It can correlate an observed native action but cannot
/// authorize or deliver one.
/// </summary>
internal sealed record ProcessLocalNativeInputBinding(string BoundActionId, string Operation, IReadOnlyList<object> Owners, IReadOnlyList<object> Subjects);

public sealed class ProcessLocalNativeWitnessFrame
{
    private readonly IReadOnlyDictionary<string, object> _exactEntities;
    private readonly IReadOnlySet<string> _exactBindingIds;
    private readonly IReadOnlyList<ProcessLocalNativeInputBinding> _nativeInputs;

    internal ProcessLocalNativeWitnessFrame(
        PlayerEnvironmentSnapshot snapshot,
        PlayerEnvironmentCapabilitiesResponse capabilities,
        string sourceDigest,
        bool externalControllerActive,
        IReadOnlyDictionary<string, object> exactEntities,
        IReadOnlySet<string> exactBindingIds,
        IReadOnlyDictionary<string, ProcessLocalReadCapture>? reads = null,
        IReadOnlyList<ProcessLocalCaptureTiming>? captureTimings = null,
        IReadOnlyList<ProcessLocalNativeInputBinding>? nativeInputs = null)
    {
        _nativeInputs = nativeInputs ?? Array.Empty<ProcessLocalNativeInputBinding>();
        Snapshot = snapshot;
        Capabilities = capabilities;
        SourceDigest = sourceDigest;
        ExternalControllerActive = externalControllerActive;
        _exactEntities = exactEntities;
        _exactBindingIds = exactBindingIds;
        Reads = reads ?? new Dictionary<string, ProcessLocalReadCapture>(StringComparer.Ordinal);
        CaptureTimings = captureTimings ?? Array.Empty<ProcessLocalCaptureTiming>();
    }

    public PlayerEnvironmentSnapshot Snapshot { get; }

    public PlayerEnvironmentCapabilitiesResponse Capabilities { get; }

    public string SourceDigest { get; }

    public bool ExternalControllerActive { get; }

    public IReadOnlyDictionary<string, ProcessLocalReadCapture> Reads { get; }

    public IReadOnlyList<ProcessLocalCaptureTiming> CaptureTimings { get; }
    public ProcessLocalPublicBasisCapture? PublicBasis { get; internal init; }

    /// <summary>Correlate an exact scoped native input with its accepted
    /// callback even when public delivery is settling. This never publishes a
    /// BoundAction, changes Snapshot or asserts an action space at Human time.</summary>
    public ProcessLocalNativeMatch ResolveAcceptedInput(
        ProcessLocalObservedAction expected, ProcessLocalObservedAction accepted)
    {
        if (ExternalControllerActive || Snapshot.Completeness.Status != "complete"
            || string.IsNullOrWhiteSpace(expected.Verb)
            || (expected.Subject == null || accepted.Subject == null)
                && !(expected.Verb == "end_turn" && expected.Subject == null && accepted.Subject == null
                    && expected.Arguments.Count == 0 && accepted.Arguments.Count == 0)
            || expected.Verb != accepted.Verb || !ReferenceEquals(expected.Subject, accepted.Subject)
            || expected.Arguments.Count != accepted.Arguments.Count
            || expected.Arguments.Any(pair => string.IsNullOrWhiteSpace(pair.Key) || pair.Value == null
                || !accepted.Arguments.TryGetValue(pair.Key, out object? value)
                || !ReferenceEquals(pair.Value, value)))
            return new("zero", 0, null, null, "scoped_native_input_reference_equality", "Exact accepted input did not match the scoped Human operands.");
        string? subject = expected.Subject == null ? null : NativeUiRuntime.Entities.GetId(expected.Subject,
            expected.Verb == "play" ? "card" : expected.Verb == "use" ? "potion" : "subject");
        var arguments = expected.Arguments.ToDictionary(pair => pair.Key,
            pair => NativeUiRuntime.Entities.GetId(pair.Value,
                pair.Value is MegaCrit.Sts2.Core.Entities.Creatures.Creature creature
                    ? creature.IsPlayer ? "player" : creature.CombatState?.PlayerCreatures.Contains(creature) == true ? "companion" : "enemy"
                    : "operand"), StringComparer.Ordinal);
        string key = STS2Platform.NativeFoundation.NativeSemanticActionCatalog.BuildKey(expected.Verb, subject, arguments);
        return new("exact_native_input", 1, null, null, "scoped_native_input_reference_equality", null)
        {
            NativeInput = new(key, expected.Verb, subject, arguments,
                Snapshot.Referents.FirstOrDefault(value => value.ReferentId == subject)?.Label
                    ?? expected.Verb + " (native accepted)")
        };
    }

    public ProcessLocalNativeMatch Resolve(ProcessLocalObservedAction observed)
    {
        if (!string.Equals(Snapshot.Status, "interactive", StringComparison.Ordinal)
            || !string.Equals(Snapshot.BoundActions.Status, "complete", StringComparison.Ordinal)
            || Snapshot.BoundActions.Actions.Count == 0)
        {
            return new ProcessLocalNativeMatch(
                "frame_not_authoritative",
                0,
                null,
                null,
                "no_match_attempted",
                "Only a complete interactive frozen catalog can be correlated.");
        }

        if (string.IsNullOrWhiteSpace(observed.Verb)
            || observed.Arguments == null
            || observed.Arguments.Any(pair =>
                string.IsNullOrWhiteSpace(pair.Key) || pair.Value == null))
        {
            return new ProcessLocalNativeMatch(
                "invalid_observed_action",
                0,
                null,
                null,
                "no_match_attempted",
                "The observed native action must have a verb and non-null named operands.");
        }

        PlayerEnvironmentBoundAction[] matches = Snapshot.BoundActions.Actions
            .Where(action => _exactBindingIds.Contains(action.BoundActionId))
            .Where(action => Matches(action, observed))
            .ToArray();
        if (matches.Length != 1)
        {
            return new ProcessLocalNativeMatch(
                matches.Length == 0 ? "zero" : "ambiguous",
                matches.Length,
                null,
                null,
                "reference_equality_to_frozen_host_binding",
                matches.Length == 0
                    ? "No frozen BoundAction matched the exact observed native references."
                    : "More than one frozen BoundAction matched; correlation is quarantined.");
        }

        return new ProcessLocalNativeMatch(
            "exact_unique",
            1,
            matches[0].BoundActionId,
            matches[0],
            "reference_equality_to_frozen_host_binding",
            null);
    }

    /// <summary>Correlates an exact native callback with a frozen Host binding.
    /// Owner and operation are process-local facts, never consumer operands.</summary>
    public bool HasNativeInputOwner(object owner) =>
        Snapshot.Status == "interactive" && Snapshot.BoundActions.Status == "complete"
        && _nativeInputs.Any(binding => binding.Owners.Any(value => ReferenceEquals(value, owner)));

    public ProcessLocalNativeMatch ResolveNativeInput(object owner, string operation, object? subject = null)
    {
        var ids = _nativeInputs.Where(binding => binding.Operation == operation
                && binding.Owners.Any(value => ReferenceEquals(value, owner))
                && (subject == null || binding.Subjects.Any(value => ReferenceEquals(value, subject))))
            .Select(binding => binding.BoundActionId).ToHashSet(StringComparer.Ordinal);
        var matches = Snapshot.Status == "interactive" && Snapshot.BoundActions.Status == "complete"
            ? Snapshot.BoundActions.Actions.Where(action => ids.Contains(action.BoundActionId)).ToArray()
            : Array.Empty<PlayerEnvironmentBoundAction>();
        return new ProcessLocalNativeMatch(matches.Length == 1 ? "exact_unique" : matches.Length == 0 ? "zero" : "ambiguous",
            matches.Length, matches.Length == 1 ? matches[0].BoundActionId : null,
            matches.Length == 1 ? matches[0] : null, "exact_native_owner_operation_and_frozen_host_binding", null);
    }

    private bool Matches(
        PlayerEnvironmentBoundAction action,
        ProcessLocalObservedAction observed)
    {
        if (!string.Equals(action.Verb, observed.Verb, StringComparison.Ordinal))
            return false;
        if (action.SubjectReferentId == null)
        {
            if (observed.Subject != null)
                return false;
        }
        else if (observed.Subject == null
                 || !IsSameEntity(action.SubjectReferentId, observed.Subject))
        {
            return false;
        }

        if (action.Arguments.Count != observed.Arguments.Count)
            return false;
        foreach (PlayerEnvironmentBoundActionArgument argument in action.Arguments)
        {
            if (!observed.Arguments.TryGetValue(argument.Role, out object? native)
                || !IsSameEntity(argument.ReferentId, native))
                return false;
        }
        return true;
    }

    private bool IsSameEntity(string referentId, object entity) =>
        _exactEntities.TryGetValue(referentId, out object? frozen)
        && ReferenceEquals(frozen, entity);
}

/// <summary>
/// Generic process-local observation seam for recorders and conformance tools.
/// It is deliberately absent from REST/MCP and has no mutation methods.
/// </summary>
public static class PlayerEnvironmentNativeWitness
{
    /// <summary>One original P1 build supplies both exact native bindings and every advertised Read instance.</summary>
    public static ProcessLocalNativeWitnessFrame CaptureP1()
    {
        var profiler = new ProcessLocalCaptureProfiler();
        return PlayerEnvironmentService.CapturePublicBasisBuild((built, frozen) =>
            CreateFrame(built, Array.Empty<string>(), profiler,
                new(frozen.SnapshotId, frozen.Bytes, frozen.CapturedAt)), profiler);
    }
    public static ProcessLocalNativeWitnessFrame Capture(
        IReadOnlyCollection<string>? requiredReadKinds = null)
    {
        var profiler = new ProcessLocalCaptureProfiler();
        SnapshotBuildResult frame =
            STS2Connector.PlayerEnvironment.PlayerEnvironmentService.BuildSnapshot(
                requiredReadKinds: requiredReadKinds,
                captureProfiler: profiler);
        return CreateFrame(frame, requiredReadKinds, profiler);
    }

    public static ProcessLocalNativeWitnessFrame Capture(
        Func<string, IReadOnlyCollection<string>> requiredReadKindsForInteraction)
    {
        ArgumentNullException.ThrowIfNull(requiredReadKindsForInteraction);
        var profiler = new ProcessLocalCaptureProfiler();
        SnapshotBuildResult frame =
            STS2Connector.PlayerEnvironment.PlayerEnvironmentService.BuildSnapshot(
                requiredReadKindsForInteraction: requiredReadKindsForInteraction,
                captureProfiler: profiler);
        return CreateFrame(frame, frame.ReadBuilds.Keys.ToArray(), profiler);
    }

    private static ProcessLocalNativeWitnessFrame CreateFrame(
        SnapshotBuildResult frame,
        IReadOnlyCollection<string>? requiredReadKinds,
        ProcessLocalCaptureProfiler profiler,
        ProcessLocalPublicBasisCapture? publicBasis = null)
    {
        HashSet<string> referentIds = profiler.Measure(
            "witness_referent_index",
            () => frame.Snapshot.BoundActions.Actions
                .SelectMany(action => action.Arguments.Select(argument => argument.ReferentId)
                    .Append(action.SubjectReferentId))
                .Where(referentId => referentId != null)
                .Cast<string>()
                .Concat(frame.Bindings.Values.SelectMany(binding => binding.NativeAction.Candidate.EntityBindings.Select(entity => entity.EntityId)))
                .ToHashSet(StringComparer.Ordinal));
        IReadOnlyDictionary<string, ProcessLocalReadCapture> reads = profiler.Measure(
            "read_materialization",
            () => MaterializeReads(frame, requiredReadKinds));
        PlayerEnvironmentCapabilitiesResponse capabilities = profiler.Measure(
            "capabilities_identity",
            STS2Connector.PlayerEnvironment.PlayerEnvironmentService.GetCapabilities);
        bool externalControllerActive = profiler.Measure(
            "controller_status",
            () => MutationControlRuntime.Snapshot().Controller != null);
        IReadOnlyDictionary<string, object> exactReferences = profiler.Measure(
            "exact_reference_capture",
            () => NativeUiRuntime.Entities.CaptureExactReferences(referentIds));
        IReadOnlySet<string> exactBindingIds = profiler.Measure(
            "exact_binding_index",
            () => frame.Bindings.Keys.ToHashSet(StringComparer.Ordinal));
        return new ProcessLocalNativeWitnessFrame(
            frame.Snapshot,
            capabilities,
            ReadAssemblyMetadata("ConnectorPlayerEnvironmentSourceDigest", "PlayerEnvironmentSourceDigest"),
            externalControllerActive,
            exactReferences,
            exactBindingIds,
            reads,
            profiler.Snapshot(),
            CaptureNativeInputBindings(frame.Bindings, exactReferences))
        { PublicBasis = publicBasis };
    }

    internal static IReadOnlyList<ProcessLocalNativeInputBinding> CaptureNativeInputBindings(
        IReadOnlyDictionary<string, PlayerEnvironmentNativeBinding> bindings, IReadOnlyDictionary<string, object> exactReferences) =>
            bindings.SelectMany(pair => new[] { pair.Value.NativeAction.Candidate.Operation,
                pair.Value.NativeAction.GridInput?.NativeCallbackVerb }.OfType<string>().Distinct(StringComparer.Ordinal)
                .Select(operation => new ProcessLocalNativeInputBinding(
                pair.Key, operation,
                pair.Value.NativeAction.Candidate.EntityBindings
                    .Where(entity => entity.Role is "screen" or "hand" or "owner" || entity.Role.EndsWith("_screen", StringComparison.Ordinal))
                    .Where(entity => exactReferences.ContainsKey(entity.EntityId)).Select(entity => exactReferences[entity.EntityId]).ToArray(),
                pair.Value.NativeAction.Candidate.EntityBindings
                    .Where(entity => entity.Role is not ("screen" or "hand" or "owner"))
                    .Where(entity => exactReferences.ContainsKey(entity.EntityId)).Select(entity => exactReferences[entity.EntityId]).ToArray()))).ToArray();

    private static IReadOnlyDictionary<string, ProcessLocalReadCapture> MaterializeReads(
        SnapshotBuildResult frame,
        IReadOnlyCollection<string>? requiredReadKinds)
    {
        if (requiredReadKinds == null || requiredReadKinds.Count == 0)
            return new Dictionary<string, ProcessLocalReadCapture>(StringComparer.Ordinal);
        var result = new Dictionary<string, ProcessLocalReadCapture>(StringComparer.Ordinal);
        foreach (string kind in requiredReadKinds
                     .Where(kind => !string.IsNullOrWhiteSpace(kind))
                     .Distinct(StringComparer.Ordinal))
        {
            string readId = $"read:{kind}";
            PlayerEnvironmentReadResolution resolution =
                STS2Connector.PlayerEnvironment.PlayerEnvironmentService.ResolveReadMaterialization(
                    frame.Snapshot,
                    readId,
                    frame.Snapshot.SnapshotId,
                    frame.ReadBuilds);
            if (resolution.ErrorCode != null)
            {
                result[kind] = new ProcessLocalReadCapture(
                    kind,
                    resolution.ErrorCode == "read_not_available" ? "not_available" : "failed",
                    null,
                    resolution.ErrorCode,
                    resolution.Detail);
                continue;
            }
            PlayerEnvironmentReadResult built =
                STS2Connector.PlayerEnvironment.PlayerEnvironmentService.BuildResolvedRead(
                    frame,
                    resolution,
                    frame.Snapshot.SnapshotId);
            result[kind] = built.Read == null
                ? new ProcessLocalReadCapture(
                    kind,
                    "failed",
                    null,
                    built.ErrorCode ?? "read_materialization_failed",
                    built.Detail)
                : new ProcessLocalReadCapture(kind, "materialized", built.Read, null, null);
        }
        return result;
    }

    private static string ReadAssemblyMetadata(params string[] keys)
    {
        AssemblyMetadataAttribute[] metadata = typeof(PlayerEnvironmentNativeWitness).Assembly
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .ToArray();
        foreach (string key in keys)
        {
            string? value = metadata.FirstOrDefault(attribute =>
                    string.Equals(attribute.Key, key, StringComparison.Ordinal))
                ?.Value;
            if (!string.IsNullOrWhiteSpace(value))
                return value;
        }
        return "unavailable";
    }
}
