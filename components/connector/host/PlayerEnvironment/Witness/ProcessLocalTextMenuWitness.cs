using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment.Witness;

namespace STS2Connector.PlayerEnvironment
{
    internal static partial class PlayerEnvironmentService
    {
        internal static ProcessLocalTextMenuWitnessFrame CaptureTextMenuWitness()
        {
            lock (SubmissionGate)
            {
                SnapshotBuildResult native = BuildSnapshot(textMenuCapture: true);
                TextMenuFrame frame = NativeTextMenuFrameBuilder.Capture(
                    native, Entities, _ => throw new InvalidOperationException(
                        "A process-local text-menu witness cannot dispatch native input."));
                return new ProcessLocalTextMenuWitnessFrame(
                    frame, GetCapabilities(TextMenuContract.Profile),
                    PlayerEnvironmentTextMenuWitness.SourceDigest(),
                    PlayerEnvironmentTextMenuWitness.IsExternalControllerActive);
            }
        }
    }
}

namespace STS2Connector.PlayerEnvironment.Witness
{
    /// <summary>Native references are process-local correlation inputs only.</summary>
    public sealed record ProcessLocalObservedTextMenuAction(
        string Verb, object Owner, object? Subject,
        IReadOnlyDictionary<string, object> Arguments);

    public sealed record ProcessLocalTextMenuMatch(
        string Status, int MatchCount, TextMenuAction? Action,
        string Evidence, string? Detail);

    /// <summary>One independent root menu and its exact native leaf bindings.
    /// This object has no navigation, controller or native delivery operation.</summary>
    public sealed class ProcessLocalTextMenuWitnessFrame
    {
        private sealed record FrozenBinding(
            TextMenuAction Action, string Verb, object Owner, object? Subject,
            IReadOnlyDictionary<string, object> Arguments);

        private readonly IReadOnlyList<FrozenBinding> bindings;

        internal ProcessLocalTextMenuWitnessFrame(
            TextMenuFrame frame, PlayerEnvironmentCapabilitiesResponse capabilities,
            string sourceDigest, bool externalControllerActive)
        {
            TextMenuProjection projection = new TextMenuSession().Observe(frame);
            Snapshot = projection.Snapshot;
            Capabilities = capabilities;
            SourceDigest = sourceDigest;
            ExternalControllerActive = externalControllerActive;
            bindings = projection.Choices.Values
                .Where(choice => choice.Action.Kind == "native_input"
                    && choice.Leaf?.NativeWitness != null)
                .Select(choice =>
                {
                    TextMenuNativeWitnessBinding native = choice.Leaf!.NativeWitness!;
                    return new FrozenBinding(
                        choice.Action, choice.Action.Verb, native.Owner, native.Subject,
                        new Dictionary<string, object>(native.Arguments,
                            StringComparer.Ordinal));
                }).ToArray();
        }

        public TextMenuSnapshot Snapshot { get; }
        public PlayerEnvironmentCapabilitiesResponse Capabilities { get; }
        public string SourceDigest { get; }
        public bool ExternalControllerActive { get; }

        public ProcessLocalTextMenuMatch Resolve(ProcessLocalObservedTextMenuAction observed)
        {
            const string evidence = "reference_equality_to_frozen_text_menu_leaf";
            if (ExternalControllerActive)
                return new("controller_active", 0, null, evidence,
                    "An external controller holds input; no Human correlation is attempted.");
            if (Snapshot.Status != "interactive"
                || Snapshot.Completeness.Status != "complete"
                || Snapshot.Menu.Cursor != "root"
                || Snapshot.MenuActions.Status != "complete"
                || Snapshot.MenuActions.Actions.Count == 0
                || Snapshot.MenuActions.Actions.Count != Snapshot.MenuActions.MaterializedCount
                || Snapshot.MenuActions.MaterializedCount != Snapshot.MenuActions.TotalCount)
                return new("frame_not_authoritative", 0, null, evidence,
                    "The frozen root menu is not complete and interactive.");
            if (observed == null || string.IsNullOrWhiteSpace(observed.Verb)
                || observed.Owner == null || observed.Arguments == null
                || observed.Arguments.Any(pair => string.IsNullOrWhiteSpace(pair.Key)
                    || pair.Value == null))
                return new("invalid_observed_action", 0, null, evidence,
                    "A verb, exact owner and non-null named operands are required.");

            FrozenBinding[] matches = bindings.Where(binding =>
                binding.Verb == observed.Verb
                && ReferenceEquals(binding.Owner, observed.Owner)
                && ReferenceEquals(binding.Subject, observed.Subject)
                && binding.Arguments.Count == observed.Arguments.Count
                && binding.Arguments.All(pair =>
                    observed.Arguments.TryGetValue(pair.Key, out object? value)
                    && ReferenceEquals(pair.Value, value))).ToArray();
            return matches.Length == 1
                ? new("exact_unique", 1, matches[0].Action, evidence, null)
                : new(matches.Length == 0 ? "zero" : "ambiguous", matches.Length,
                    null, evidence, matches.Length == 0
                        ? "No frozen native leaf matched the exact references."
                        : "Multiple frozen native leaves matched; correlation is quarantined.");
        }
    }

    /// <summary>Read-only process-local bridge, absent from REST, MCP and SDK.</summary>
    public static class PlayerEnvironmentTextMenuWitness
    {
        public static ProcessLocalTextMenuWitnessFrame Capture() =>
            PlayerEnvironmentService.CaptureTextMenuWitness();

        /// <summary>Read-only current controller observation for a recorder's
        /// post-callback exclusion check; it grants no input authority.</summary>
        public static bool IsExternalControllerActive =>
            MutationControlRuntime.Snapshot().Controller != null;

        internal static string SourceDigest()
        {
            AssemblyMetadataAttribute[] metadata = typeof(PlayerEnvironmentTextMenuWitness)
                .Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().ToArray();
            foreach (string key in new[]
                     { "ConnectorPlayerEnvironmentSourceDigest", "PlayerEnvironmentSourceDigest" })
            {
                string? value = metadata.FirstOrDefault(attribute =>
                    string.Equals(attribute.Key, key, StringComparison.Ordinal))?.Value;
                if (!string.IsNullOrWhiteSpace(value)) return value;
            }
            return "unavailable";
        }
    }
}
