using System.Reflection;
using HarmonyLib;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

[HarmonyPatch]
internal static class NativeRewardInputOwnerPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NRewardsScreen), nameof(NRewardsScreen.ShowScreen))
        ?? throw new MissingMethodException(typeof(NRewardsScreen).FullName, nameof(NRewardsScreen.ShowScreen));

    [HarmonyAfter("rsgcsg.sts2-platform.native-foundation")]
    private static void Postfix(NRewardsScreen __result, MethodBase __originalMethod) =>
        NativeNestedCallbackSafety.Run("reward_screen.exact_parent", () => {
            NativeNestedSelectorBindings.RegisterOptionalInputOwner(__result, __originalMethod);
            RecorderRuntime.ObserveOpenedRewardInputOwner(__result);
        });
}

internal static partial class RecorderRuntime
{
    // ShowScreen returns only after native Push/AfterOverlayShown. Observe the
    // exact opening owner's handoff now, before an unrelated potion input can
    // mutate the state. A reward click is not the first possible Human effect.
    internal static void ObserveOpenedRewardInputOwner(NRewardsScreen screen,
        string? requiredParentActionId = null)
    {
        if (!_semanticBoundaryTraceHealthy || _store == null
            || !ReferenceEquals(NOverlayStack.Instance?.Peek(), screen)
            || !ActiveScreenContext.Instance.IsCurrent(screen) || !screen.IsVisibleInTree()) return;
        if (!NativeNestedSelectorBindings.TryGet(screen, out var binding) || binding == null
            || binding.RecordingSessionId != SessionId
            || !BoundaryTracker.Contains(binding.ActionWitnessId)) return;
        NestedUiInput? input = ResolveNestedUiInput(screen);
        if (input == null || (requiredParentActionId != null
                && input.Binding.ActionWitnessId != requiredParentActionId)
            || !BoundaryTracker.Contains(input.ParentActionId)) return;
        ProcessLocalNativeWitnessFrame frame = CaptureSemanticFrame();
        if (frame.ExternalControllerActive || frame.Snapshot.Interaction.Kind != "reward_claim") return;
        RecorderEnvironmentIdentity environment = BuildEnvironment(frame);
        if (SemanticWitnessBlockers(frame, environment).Count != 0) return;
        CurrentDecisionFrame pre = FreezeSemanticBoundary(frame, environment);
        PersistTrackerMutationOrUnknown(input.ParentActionId,
            tracker => ObserveNestedUiInputBoundary(tracker, input, frame, pre,
                "NRewardsScreen.ShowScreen->native_input_owner_handoff"),
            "reward_owner_boundary_persistence_failed",
            "The exact reward opening boundary was not persisted.", "NRewardsScreen.ShowScreen");
    }

    private static void ObserveSynchronousRewardInputOwner(string parentActionId)
    {
        // A synchronous factory may return before its outer Human callback is
        // admitted. Still on that callback's return seam, the registered owner
        // must name this exact parent. The overlay lookup never creates lineage.
        if (NOverlayStack.Instance?.Peek() is NRewardsScreen screen)
            NativeNestedCallbackSafety.Run("reward_screen.synchronous_parent", () =>
                ObserveOpenedRewardInputOwner(screen, parentActionId));
    }

    internal sealed record NestedUiInput(object Owner,
        NativeNestedSelectorBindings.Binding Binding, DecisionOccurrenceIdentity Parent, string ParentActionId);

    private static NestedUiInput? ResolveNestedUiInput(object? owner)
    {
        // P1 records each exact UI frame/root and native completion directly.
        // Its accepted decisions never enter the legacy canonical tracker.
        if (IsP1Recording) return null;
        if (owner == null || !NativeNestedSelectorBindings.TryGet(owner, out var binding)
            || binding == null) return null;
        if (binding.RecordingSessionId != SessionId || binding.ActionWitnessId == "unavailable")
            throw new InvalidOperationException("Reward input owner has no current exact Human parent binding.");
        DecisionOccurrenceIdentity? parent = binding.ParentDecision
            ?? BoundaryTracker.DecisionIdentity(binding.ActionWitnessId);
        if (parent == null)
            throw new InvalidOperationException("Reward input owner has no exact Human parent decision.");
        binding.ParentDecision = parent;
        return new(owner, binding, parent, binding.DecisionHeadActionId ?? binding.ActionWitnessId);
    }

    private static IReadOnlyList<SemanticBoundaryTraceDraft> ObserveNestedUiInputBoundary(
        SemanticBoundaryTracker tracker, NestedUiInput input,
        ProcessLocalNativeWitnessFrame frame, CurrentDecisionFrame pre, string mechanism)
    {
        string parentActionId = input.ParentActionId;
        if (!tracker.Contains(parentActionId)) return Array.Empty<SemanticBoundaryTraceDraft>();
        string ownerId = NativeWitnessIdentity.Get(input.Owner, "selector_owner");
        var boundary = CreateSemanticBoundaryObservation(frame,
            SemanticBoundaryWitnessKinds.NativeDecisionOwnerReady, null, pre) with {
            NativeDecisionOwnerReady = new NativeDecisionOwnerReadyEvidence(
                pre.InteractionKind, ownerId, input.Owner.GetType().FullName!, mechanism) };
        return tracker.ObserveNestedInputBoundary(parentActionId, boundary,
            new NativeContinuationEvidence($"reward-input-boundary-{Guid.NewGuid():N}",
                "exact_selector_input_owner", parentActionId, ownerId,
                NativeWitnessIdentity.Get(input.Binding.ParentOwner, "native_owner"), true));
    }
}
