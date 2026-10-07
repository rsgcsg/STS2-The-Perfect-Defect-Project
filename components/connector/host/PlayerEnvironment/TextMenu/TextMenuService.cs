using System;
using System.Collections.Generic;
using System.Linq;
using STS2Connector.Authority;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Connector.PlayerEnvironment;

internal sealed class TextMenuRunContinuityChangedException : InvalidOperationException
{
    internal TextMenuRunContinuityChangedException()
        : base("run_continuity_changed_during_capture") { }
}

internal static partial class PlayerEnvironmentService
{
    private static readonly System.Lazy<TextMenuExecutor> TextMenus = new(() => new(
        SubmissionGate, RequestFingerprints, CaptureTextMenuFrame, MutationControlRuntime.Authorize,
        () =>
        {
            var lease = MutationControlRuntime.Snapshot().Controller;
            return lease == null ? null : $"{lease.ClientSessionId}:{lease.ControllerGeneration}";
        }));
    private static readonly System.Lazy<TextMenuV2Executor> TextMenusV2 = new(() => new(
        SubmissionGate, RequestFingerprints, CaptureTextMenuV2Frame,
        MutationControlRuntime.Authorize,
        () =>
        {
            var lease = MutationControlRuntime.Snapshot().Controller;
            return lease == null ? null : $"{lease.ClientSessionId}:{lease.ControllerGeneration}";
        }));

    private static TextMenuFrame CaptureTextMenuFrame()
        => CaptureTextMenuFrame(includeSemanticCardPlays: false);

    private static TextMenuFrame CaptureTextMenuV2Frame()
        => CaptureTextMenuFrame(includeSemanticCardPlays: true);

    private static TextMenuFrame CaptureTextMenuFrame(bool includeSemanticCardPlays)
    {
        return CaptureWithRunIdentity(
            () => RunManager.Instance.DebugOnlyGetState(),
            () =>
            {
                SnapshotBuildResult native = BuildSnapshot(textMenuCapture: true);
                TextMenuFrame frame = NativeTextMenuFrameBuilder.Capture(native, Entities,
                    binding => StartPlayerEnvironmentInput(
                        native, binding.NativeAction, binding.ExactOperands));
                if (!includeSemanticCardPlays) return frame;
                frame = frame with { Page = TextMenuV2Visibility.Sanitize(frame.Page) };
                return AttachSemanticCardPlays(native, frame);
            },
            run => Entities.GetId(run, "run"));
    }

    private static TextMenuFrame AttachSemanticCardPlays(
        SnapshotBuildResult native, TextMenuFrame frame)
    {
        if (native.HostObservation.Surface is not CombatTurnSurface combat
            || frame.Page.Interaction.Kind != "combat_turn"
            || frame.Page.Interaction.Stage != "ready"
            || frame.Page.Status != "interactive"
            || frame.Page.Completeness.Status != "complete")
            return frame;

        // BuildBindings is the existing native semantic card-to-target domain;
        // v1 deliberately projects only end turn to avoid an all-card 512 cap.
        NativeUiBoundAction[] bindings = NativeUiActionRuntime.BuildBindings(
                native.HostObservation)
            .Where(binding => binding.Candidate.Operation == "play_card")
            .ToArray();
        return AttachSemanticCardPlaysFromBindings(frame, combat, bindings,
            native.Snapshot.Interaction.InteractionId,
            captured => StartPlayerEnvironmentInput(
                native, captured.NativeAction, captured.ExactOperands));
    }

    internal static TextMenuFrame AttachSemanticCardPlaysFromBindings(
        TextMenuFrame frame, CombatTurnSurface combat,
        IReadOnlyList<NativeUiBoundAction> bindings, string interactionId,
        Func<PlayerEnvironmentNativeBinding, NativeInputResult> dispatch)
    {
        var optionGroups = combat.PlayableCards.GroupBy(
            option => option.EntityId, StringComparer.Ordinal).ToArray();
        if (optionGroups.Any(group => group.Count() != 1))
            return frame;
        var options = optionGroups.ToDictionary(
            group => group.Key, group => group.Single(), StringComparer.Ordinal);
        string[] boundCards = bindings.Select(binding =>
                binding.Candidate.Operands.GetValueOrDefault("card_id") ?? "")
            .ToArray();
        if (bindings.Count != combat.PlayableCards.Count
            || bindings.Count > MaxBoundActions
            || options.Count != combat.PlayableCards.Count
            || boundCards.Any(id => !options.ContainsKey(id))
            || boundCards.Distinct(StringComparer.Ordinal).Count() != boundCards.Length
            || frame.Page.Referents.GroupBy(item => item.ReferentId,
                StringComparer.Ordinal).Any(group => group.Count() != 1))
            return frame;

        Dictionary<string, PlayerEnvironmentReferent> visible = frame.Page.Referents
            .Where(item => item.State.Visible)
            .ToDictionary(item => item.ReferentId, StringComparer.Ordinal);
        var leaves = new List<TextMenuLeaf>();
        foreach (NativeUiBoundAction binding in bindings)
        {
            string cardId = binding.Candidate.Operands["card_id"];
            string[] expectedTargets = options[cardId].TargetEntityIds
                .Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray();
            if (expectedTargets.Length != options[cardId].TargetEntityIds.Count)
                return frame;
            BoundActionProjectionResult projected = ProjectBoundActions(
                new[] { binding }, interactionId, visible);
            if (projected.Projection.Status != "complete"
                || projected.Projection.Actions.Count == 0)
                return frame;
            string[] projectedTargets = projected.Projection.Actions
                .Select(action => action.Arguments.Count switch
                {
                    0 => "",
                    1 => action.Arguments[0].ReferentId,
                    _ => "#invalid"
                })
                .Order(StringComparer.Ordinal).ToArray();
            if (projected.Projection.Actions.Any(action =>
                    action.SubjectReferentId != cardId)
                || !projectedTargets.SequenceEqual(expectedTargets.Length == 0
                    ? new[] { "" } : expectedTargets, StringComparer.Ordinal))
                return frame;
            foreach (PlayerEnvironmentBoundAction action in projected.Projection.Actions)
            {
                if (action.Verb != "play" || action.SubjectReferentId == null
                    || !visible.ContainsKey(action.SubjectReferentId)
                    || action.Arguments.Count > 1
                    || action.Arguments.Any(argument => argument.Role != "target"
                        || !visible.ContainsKey(argument.ReferentId))
                    || !projected.Bindings.TryGetValue(action.BoundActionId,
                        out PlayerEnvironmentNativeBinding? exact))
                    return frame;
                PlayerEnvironmentNativeBinding captured = exact;
                leaves.Add(new TextMenuLeaf(action.BoundActionId, "root", "play",
                    action.Label, action.SubjectReferentId, action.Arguments,
                    () => dispatch(captured)));
            }
        }
        return frame with { CardPlays = leaves, CardPlayCatalogComplete = true };
    }

    internal static TextMenuFrame CaptureWithRunIdentity(
        System.Func<object?> currentRun, System.Func<TextMenuFrame> capture,
        System.Func<object, string> identity)
    {
        object? before = currentRun();
        TextMenuFrame frame = capture();
        if (!System.Object.ReferenceEquals(before, currentRun()))
            throw new TextMenuRunContinuityChangedException();
        return frame with { GameContinuityId = before == null ? null : identity(before) };
    }

    public static TextMenuSnapshot ObserveTextMenu() => TextMenus.Value.Observe();
    public static TextMenuObservationContext ObserveTextMenuContext() =>
        TextMenus.Value.ObserveContext();
    public static TextMenuActionResult SubmitTextMenu(PlayerEnvironmentActionRequest request) =>
        TextMenus.Value.Submit(request);
    public static TextMenuActionResult? FindTextMenuResult(string requestId) => TextMenus.Value.Find(requestId);
    public static TextMenuV2Snapshot ObserveTextMenuV2() => TextMenusV2.Value.Observe();
    public static TextMenuV2ObservationContext ObserveTextMenuV2Context() =>
        TextMenusV2.Value.ObserveContext();
    public static TextMenuV2ActionResult SubmitTextMenuV2(PlayerEnvironmentActionRequest request) =>
        TextMenusV2.Value.Submit(request);
    public static TextMenuV2ActionResult? FindTextMenuV2Result(string requestId) =>
        TextMenusV2.Value.Find(requestId);
}
