using Xunit;
namespace STS2HumanAnnotator.Core.Tests;

public sealed class CarrierIngressSourceTests
{
    [Fact]
    public void SettlingCardObservationKeepsExactFactoryFrameAndExecutionAuthority()
    {
        string runtime = Source("RecorderRuntime.cs");
        int stage = runtime.IndexOf("else if (staged != null && sameGeneration");
        int capture = runtime.IndexOf("current = CaptureReadRichFrame()", stage);
        string branch = runtime[stage..capture];
        Assert.Contains("ReferenceEquals(staged.Holder.CardModel, stagedCard)", branch);
        Assert.Contains("staged.SemanticBlockers.Count == 0", branch);
        Assert.Contains("staged.Decision.Frame, occurrence: occurrence, nativeInputBinding: true", branch);
        Assert.DoesNotContain("semanticSelection:", branch);
    }

    [Fact]
    public void SynchronousProceedUsesExactOwnerCommitBeforeMapBoundary()
    {
        string source = Source("NativeUiPatches.cs");
        int start = source.IndexOf("internal static class NativeTreasureProceedCompletionPatch");
        int end = source.IndexOf("internal static class NativeRewardClaimStartPatch", start);
        string patch = source[start..end];
        Assert.Contains("NativeSynchronousOwnerHandoff.Matches(__result, map, __state.MapWasOpen", patch);
        Assert.Contains("NMapScreen.Instance, map.IsOpen, root, context.ActionWitnessId", patch);
        int commit = patch.IndexOf("RecorderRuntime.ObserveSemanticUiNativeCommit");
        int boundary = patch.IndexOf("NativeDecisionOwnerReadyProvider.ObserveMapProceedReady");
        int queued = patch.IndexOf("RecorderRuntime.QueueNativePostCommitBoundary");
        Assert.True(commit >= 0 && boundary > commit && queued > boundary);
        Assert.Contains("return;", patch[boundary..queued]);
    }

    [Fact]
    public void EventProceedCarriesItsExactMapAndNativeFamilyBeforeReturningToHumanInput()
    {
        string source = Source("NativeUiPatches.cs");
        int start = source.IndexOf("internal static class NativeEventOptionPatch");
        int end = source.IndexOf("internal static class NativeEventOptionCompletionPatch", start);
        string patch = source[start..end];
        Assert.Contains("option.IsProceed ? \"proceed_event\" : \"choose_event_option\"", patch);
        Assert.Contains("NativeUiCompletionRootBindings.TryGet(\n                        option", patch);
        Assert.Contains("actionWitnessId == __state.Scope.ActionWitnessId", patch);
        Assert.Contains("NativeSynchronousOwnerHandoff.Matches(task, map, __state.MapWasOpen", patch);
        Assert.Contains("NMapScreen.Instance, map.IsOpen, actionWitnessId, HumanActionScope.Current?.ActionWitnessId", patch);
        int accepted = patch.IndexOf("ObserveAcceptedSemanticUiAction");
        int commit = patch.IndexOf("ObserveSemanticUiNativeCommit");
        int ready = patch.IndexOf("ObserveEventProceedReady");
        int queued = patch.IndexOf("QueueNativePostCommitBoundary");
        Assert.True(accepted >= 0 && commit > accepted && ready > commit && queued > ready);
        Assert.Contains("NativeUiCompletionRootBindings.TakeIfMatches(option, actionWitnessId)", patch[commit..ready]);
        Assert.Contains("return;", patch[ready..queued]);

        string runtime = Source("RecorderRuntime.cs");
        Assert.Contains("nativeSemanticSelection?.Verb == \"proceed_event\" ? \"event_option.proceed\" : null", runtime);
        Assert.Contains("Decision = action.Decision! with { Family = nativeFamily }", runtime);
    }

    [Fact]
    public void RewardOpeningObservesExactOwnerBeforeAnyRewardOrPotionInput()
    {
        string source = Source("NativeRewardDecisionLineage.cs");
        Assert.Contains("HarmonyAfter(\"rsgcsg.sts2-platform.native-foundation\")", source);
        Assert.Contains("RecorderRuntime.ObserveOpenedRewardInputOwner(__result)", source);
        Assert.Contains("ReferenceEquals(NOverlayStack.Instance?.Peek(), screen)", source);
        Assert.Contains("ActiveScreenContext.Instance.IsCurrent(screen)", source);
        Assert.Contains("binding.RecordingSessionId != SessionId", source);
        Assert.Contains("input.Binding.ActionWitnessId != requiredParentActionId", source);
        Assert.Contains("frame.Snapshot.Interaction.Kind != \"reward_claim\"", source);
        Assert.Contains("ObserveSynchronousRewardInputOwner(acceptedContext.ActionWitnessId)", Source("RecorderRuntime.cs"));
    }

    private static string Source(string name)
    {
        for (DirectoryInfo? dir = new(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            string path = Path.Combine(dir.FullName, "src", "STS2HumanAnnotator.Mod", name);
            if (File.Exists(path)) return File.ReadAllText(path);
        }
        throw new FileNotFoundException(name);
    }

    [Fact]
    public void CardObservationUsesExactNativeFactoryOwnerAndCleanupWithoutAgeHeuristic()
    {
        string runtime = Source("RecorderRuntime.cs");
        string patches = Source("NativeUiPatches.cs");
        Assert.DoesNotContain("_stagedCardFrame", runtime);
        Assert.DoesNotContain("staged_frame_expired", runtime);
        Assert.Contains("StagedCardPlays.Enter(staged)", runtime);
        Assert.Contains("ReferenceEquals(play.Holder, staged.Holder)", runtime);
        Assert.Contains("StagedCardPlays.TryGet(stagedOwner, out staged)", runtime);
        Assert.Contains("staged?.Generation == Volatile.Read(ref _cardStageGeneration)", runtime);
        Assert.Contains("typeof(NMouseCardPlay)", patches);
        Assert.Contains("typeof(NControllerCardPlay)", patches);
        Assert.Contains("RecorderRuntime.TryEnterCardScope(__instance, card, target)", patches);
        Assert.Contains("RecorderRuntime.ForgetStagedCardPlay(__instance)", patches);
    }

    [Fact]
    public void MouseContinuationRequiresPhysicalIngressAndSameCallNativeProof()
    {
        string patches = Source("HumanTextInputPatches.cs");
        string continuation = Source("RecorderRuntime.TextInput.Continuation.cs");
        Assert.Contains("typeof(NMouseCardPlay), \"SingleCreatureTargeting\"", patches);
        Assert.Contains("ParameterType == typeof(Vector2)", patches);
        Assert.Contains("RecorderRuntime.InvalidateHumanTextTargetManager(__instance)", patches);
        Assert.Contains("RecorderRuntime.BeginHumanTextMouseInput(__instance, inputEvent)", patches);
        Assert.Contains("RecorderRuntime.BeginHumanTextTargetInput(__instance, inputEvent)", patches);
        Assert.Contains("ButtonIndex: MouseButton.Right", continuation);
        Assert.Contains("HumanTextMouseCancelShortcutField?.GetValue(carrier)", continuation);
        Assert.Contains("HumanTextInputNativeProof.IsMouseNoHoverCancelCandidate(", continuation);
        Assert.Contains("if (confirm && target == null && !noHoverMouseCancel) return null;", continuation);
        Assert.Contains("scope.NativeFinishMatched = HumanTextInputNativeProof.MatchesTargetFinish(", continuation);
        Assert.Contains("scope.NativeContinuationCalled = true;", continuation);
        Assert.Contains("scope == null || scope.Finished || !scope.RequestedCancel", continuation);
        Assert.Contains("!HumanTextConsumedInputs.TryClaim(scope.Input, scope.SessionId)", continuation);
        Assert.DoesNotContain("BeginHumanTextContinuation(",
            continuation[continuation.IndexOf("internal static void ObserveHumanTextTryPlay")..]);
    }

    [Fact]
    public void CanonicalSuccessIsPublishedBeforeOptionalCompatibilityAndUnavailableAccountingIsExplicit()
    {
        string runtime = Source("RecorderRuntime.cs");
        int begin = runtime.IndexOf("private static bool TryPersistDerivedTransitionProjection");
        int append = runtime.IndexOf("store.AppendCanonicalTransition(canonical)", begin);
        int publish = runtime.IndexOf("RecordingEventKind.DecisionRecorded", append);
        int adapter = runtime.IndexOf("SemanticTransitionProjection.CreateDecision", append);
        Assert.True(append >= 0 && publish > append && adapter > publish);
        Assert.Contains("catch (Exception compatibilityException)", runtime);
        Assert.Contains("return canonicalAppended;", runtime);
        Assert.Contains("_store?.MarkDecisionAccountingUnavailable();", runtime);
        int start = runtime.IndexOf("private static bool StartSemanticUiAction");
        int witness = runtime.IndexOf("var acceptedOccurrence = new HumanActionOccurrenceEvidence", start);
        int freeze = runtime.IndexOf("FreezeSemanticBoundary(frame, environment)", start);
        Assert.True(witness > start && freeze > witness);
        Assert.Contains("selector_acceptance_persistence_failed", Source("NativeSelectorDecisionRuntime.cs"));
    }

    [Fact]
    public void GeneratedChoiceCommandCarriesExactContextAndPreservesEnclosingParent()
    {
        string source = Source("NativeNestedSelectorPatches.cs");
        Assert.Contains("nameof(CardSelectCmd.FromChooseACardScreen)", source);
        Assert.Contains("nameof(CardSelectCmd.FromChooseACardScreen) => \"native_generated_card_choice\"", source);
        Assert.Contains("Screens.EnterIfAbsent(new Parent(null, null, context, family, nativeMechanism))", source);
        Assert.Contains("NativeWitnessIdentity.Get(blocking, \"choice_context\")", source);
        int fixedParent = source.IndexOf("parent.ActionWitnessId is { Length: > 0 } fixedRoot");
        int nativeContext = source.IndexOf("parent.ChoiceContext is BlockingPlayerChoiceContext blocking");
        Assert.True(fixedParent >= 0 && nativeContext > fixedParent);
        Assert.Contains("string.Equals(parent.Family, actualFamily, StringComparison.Ordinal)", source[nativeContext..]);
    }

    [Fact]
    public void RestProceedNativeNoOpDoesNotOpenHumanScope()
    {
        string source = Source("NativeUiPatches.cs");
        int start = source.IndexOf("internal static class NativeRestSiteProceedPatch");
        int end = source.IndexOf("internal static class NativeShopPurchasePatch", start);
        string patch = source[start..end];
        int guard = patch.IndexOf("NMapScreen.Instance?.IsOpen == true");
        int capture = patch.IndexOf("RecorderRuntime.TryEnterSemanticScope");
        Assert.True(guard >= 0 && capture > guard);
        Assert.Contains("__state = default;", patch[guard..capture]);
        Assert.Contains("return;", patch[guard..capture]);
    }

    [Fact]
    public void RewardFactoryLineageReachesExistingAtomicTrackerMutation()
    {
        string patch = Source("NativeRewardDecisionLineage.cs");
        Assert.Contains("nameof(NRewardsScreen.ShowScreen)", patch);
        Assert.Contains("RegisterOptionalInputOwner(__result, __originalMethod)", patch);
        Assert.Contains("binding.RecordingSessionId != SessionId", patch);
        Assert.Contains("BoundaryTracker.DecisionIdentity(binding.ActionWitnessId)", patch);
        Assert.Contains("tracker.ObserveNestedInputBoundary", patch);
        string runtime = Source("RecorderRuntime.cs");
        Assert.Contains("nestedInput: acceptedContext.NestedInput", runtime);
        int begin = runtime.IndexOf("private static bool StartSemanticUiAction(");
        int handoff = runtime.IndexOf("result.AddRange(ObserveNestedUiInputBoundary", begin);
        int accept = runtime.IndexOf("result.AddRange(tracker.Accept", begin);
        Assert.True(handoff > begin && accept > handoff);
        Assert.Contains("if (nestedInput != null && lifecycleAction == null)", runtime);
        Assert.Contains("{ NestedInput = nestedInput }", runtime);
        Assert.Contains("subscription?.NestedInput is { } nested && boundary.State != null", runtime);
        string patches = Source("NativeUiPatches.cs");
        Assert.Contains("nestedInputOwner: NOverlayStack.Instance?.Peek() as NRewardsScreen", patches);
        Assert.Contains("nestedInputOwner: __instance", patches);
    }

    [Fact]
    public void DeferredInputIsBoundBeforeRequestAndReusedByTrueAcceptedCallback()
    {
        string patches = Source("NativeUiPatches.cs");
        int start = patches.IndexOf("internal static class NativeSubmittedHumanInputPatch");
        int end = patches.IndexOf("internal static class AcceptedGameActionPatch", start);
        Assert.Contains("private static void Prefix", patches[start..end]);
        Assert.Contains("BindSubmittedHumanInput(action)", patches[start..end]);
        string runtime = Source("RecorderRuntime.cs");
        Assert.Contains("submitted != null ? submitted.Context : HumanActionScope.Current", runtime);
        Assert.Contains("submitted.SessionId != SessionId || submitted.TimelineId != TimelineId", runtime);
        Assert.Contains("hasMapping, submittedFailure", runtime);
        int duplicate = runtime.IndexOf("if (outcome.Kind == AcceptedDecisionObserver.OutcomeKind.Duplicate)");
        int noScope = runtime.IndexOf("if (outcome.Kind == AcceptedDecisionObserver.OutcomeKind.NoScope)", duplicate);
        Assert.Contains("return;", runtime[duplicate..noScope]);
        Assert.DoesNotContain("TryQuarantine", runtime[duplicate..noScope]);
        Assert.Contains("submittedFailure: submittedFailure", runtime);
        Assert.Contains("NativePotionUseDecisionProvider.ResolveTarget(use, potion)", runtime);
        Assert.Contains("semanticDecision, nativeInputBinding: nativeInput", runtime);
        string binding = Source("NativeSubmittedInputBindings.cs");
        Assert.DoesNotContain("ObserveAcceptedAction", binding); // no speculative acceptance at request
        Assert.DoesNotContain("CurrentRoot", binding);
        Assert.Contains("if (SubmittedInputs.TryGet(action, out _)) return", binding);
    }

    [Fact]
    public void AcceptedMappingFailuresUseTheEffectBarrierNotReasonWhitelist()
    {
        string source = Source("RecorderRuntime.cs");
        int start = source.IndexOf("internal static void ObserveAcceptedAction(");
        int end = source.IndexOf("internal static void ObservePlayCardExecutionAborted", start);
        string ingress = source[start..end];
        Assert.Contains("QuarantineAcceptedHumanEffect(", ingress);
        Assert.DoesNotContain("\n                Quarantine(", ingress);
        Assert.Contains("acceptedHumanEffect: true", source);
        Assert.Contains("!diagnostic && (acceptedHumanEffect", source);
    }

    [Fact]
    public void ExactCarrierIsSubscribedBeforeTheNativeRequestCanDeferOrCancel()
    {
        string patches = Source("NativeUiPatches.cs");
        int start = patches.IndexOf("internal static class NativeRewardPotionDiscardEnqueuePatch");
        int end = patches.IndexOf("internal static class NativeRewardPotionDiscardCommitPatch", start);
        Assert.Contains("RecorderRuntime.ObserveSubmittedUiCarrier(action)", patches[start..end]);
        string runtime = Source("RecorderRuntime.cs");
        Assert.Contains("if (lifecycleAction == null)", runtime);
        Assert.Contains("? exactSubscription.ActionWitnessId", runtime);
    }
}
