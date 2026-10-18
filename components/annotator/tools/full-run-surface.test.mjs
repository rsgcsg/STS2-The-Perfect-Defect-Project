import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "..");
const patches = fs.readFileSync(
  path.join(root, "src", "STS2HumanAnnotator.Mod", "NativeUiPatches.cs"),
  "utf8"
);
const runtime = fs.readFileSync(
  path.join(root, "src", "STS2HumanAnnotator.Mod", "RecorderRuntime.cs"),
  "utf8"
);
const nestedSelectors = fs.readFileSync(
  path.join(root, "src", "STS2HumanAnnotator.Mod", "NativeNestedSelectorPatches.cs"),
  "utf8"
);
const exactAsyncBindings = fs.readFileSync(
  path.join(root, "src", "STS2HumanAnnotator.Core", "ExactAsyncOwnerBindingScope.cs"),
  "utf8"
);
const foundation = fs.readFileSync(
  path.resolve(root, "..", "native-foundation", "src", "NativeBossRelicDecisionProvider.cs"),
  "utf8"
);
const gameMod = fs.readFileSync(
  path.resolve(root, "..", "..", "apps", "game-mod", "NativeFoundationOwnerPatches.cs"),
  "utf8"
);

function section(source, start, end) {
  const begin = source.indexOf(start);
  assert.notEqual(begin, -1, `missing source section: ${start}`);
  const finish = end == null ? source.length : source.indexOf(end, begin);
  assert.notEqual(finish, -1, `missing source section end: ${end}`);
  return source.slice(begin, finish);
}

test("act-ready binds one exact queued action and skips generic duplicate ingress", () => {
  const enqueue = section(patches, "internal static class NativeActChangeVoteEnqueuePatch", "internal static class NativeActChangeVoteCommitPatch");
  const commit = section(patches, "internal static class NativeActChangeVoteCommitPatch", "[HarmonyPatch(typeof(RewardsSetSynchronizer)");

  assert.match(enqueue, /private static void Prefix\(/u);
  assert.doesNotMatch(enqueue, /private static void Postfix\(/u);
  assert.match(enqueue, /NativeUiCompletionRootBindings\.TransferRewardScreenToAction\(action, actionWitnessId\)/u);
  assert.match(runtime, /if \(NativeUiCompletionRootBindings\.TryGet\(action, out string\? carrierRoot\)\)[\s\S]*?ObserveSubmittedUiCarrier\(action\);\s*return;/u);
  assert.match(commit, /NativeUiCompletionRootBindings\.TryGet\(__instance, out __state\)/u);
  assert.match(commit, /NativeUiCompletionRootBindings\.Transfer\([\s\S]*?RunManager\.Instance/u);
  assert.match(commit, /ObserveNativeActChangeOwnerReady\(__state, __instance\)/u);
  assert.equal((commit.match(/ObserveSemanticUiNativeCommit\(/gu) ?? []).length, 1);
});

test("boss relic uses the registered native parent exactly once", () => {
  const selection = section(patches, "internal static class NativeBossRelicSelectionPatch", "internal static class NativeBossRelicCommitPatch");
  const commit = section(patches, "internal static class NativeBossRelicCommitPatch", "internal static class NativeCombatHandSelectPatch");

  assert.doesNotMatch(patches, /class NativeBossRelicCommandPatch/u);
  assert.match(selection, /TryGetRegisteredChoiceCarrier\(/u);
  assert.match(selection, /carrier\.ParentLineage\.ParentAction/u);
  assert.match(selection, /bool accepted = RecorderRuntime\.ObserveAcceptedSemanticUiAction\(/u);
  assert.match(selection, /if \(accepted && __state\.Scope\.ActionWitnessId/u);
  assert.match(selection, /NativeUiCompletionRootBindings\.Remember\(/u);
  assert.doesNotMatch(selection, /NativePlayerChoiceLineage\.Capture\(/u);
  assert.match(commit, /TryGetRegisteredCurrentChoiceCarrier\(/u);
  assert.match(commit, /NativeUiCompletionRootBindings\.TryGet\(parent/u);
  assert.match(commit, /NativeUiCompletionRootBindings\.TakeIfMatches\(parent, actionWitnessId\)/u);
  assert.doesNotMatch(commit, /NativePlayerChoiceLineage\.Capture\(/u);
  assert.equal((foundation.match(/RegisterFromChooseARelicScreen\(/gu) ?? []).length, 1);
  assert.match(gameMod, /PatchBefore\([\s\S]*?NativeBossRelicCommandPatch/u);
});

test("boss relic carrier failures are one durable failed-closed occurrence", () => {
  const selection = section(patches, "internal static class NativeBossRelicSelectionPatch", "internal static class NativeBossRelicCommitPatch");
  const commit = section(patches, "internal static class NativeBossRelicCommitPatch", "internal static class NativeCombatHandSelectPatch");

  assert.match(selection, /ObserveAcceptedSemanticUiFailure\(/u);
  assert.match(selection, /boss_relic_accepted_carrier_unavailable/u);
  assert.match(commit, /ObserveSemanticUiNativeCommitBindingFailure\(/u);
  assert.match(commit, /ConsumeRegisteredChoice\(/u);
  assert.match(runtime, /ObserveAcceptedSemanticUiFailure\(/u);
  assert.match(runtime, /AcceptedDecisionObserver\.Observe\([\s\S]*hasMapping:\s*false/u);
  assert.match(runtime, /OutcomeKind\.Duplicate/u);
});

test("bound potion and act actions retain lifecycle without duplicate root ingress", () => {
  const enqueue = section(patches, "internal static class NativeRewardPotionDiscardEnqueuePatch", "internal static class NativeRewardPotionDiscardCommitPatch");
  const actEnqueue = section(patches, "internal static class NativeActChangeVoteEnqueuePatch", "internal static class NativeActChangeVoteCommitPatch");
  const subscription = fs.readFileSync(
    path.join(root, "src", "STS2HumanAnnotator.Mod", "NativeActionLifecycleSubscription.cs"),
    "utf8"
  );

  assert.match(enqueue, /NativeUiCompletionRootBindings\.Remember\(action, actionWitnessId\)/u);
  assert.match(actEnqueue, /NativeUiCompletionRootBindings\.TransferRewardScreenToAction\(action, actionWitnessId\)/u);
  assert.match(runtime, /TryGetAction\(actionWitnessId/u);
  assert.match(runtime, /finishIsNativeCommit:\s*completionExpectation == null/u);
  assert.match(runtime, /NativeActionLifecycleKinds\.Cancelled/u);
  assert.match(subscription, /FinishIsNativeCommit/u);
});

test("boss carrier registration is one-shot and lifecycle-cleaned", () => {
  assert.match(foundation, /ConsumeRegisteredChoice\(GameAction parentAction\)/u);
  assert.match(foundation, /ObserveParentLifecycle\(/u);
  assert.match(foundation, /NativeActionLifecyclePhase\.Cancelled/u);
  assert.match(foundation, /NativeActionLifecyclePhase\.Finished/u);
  assert.match(foundation, /Observer\?\.Dispose\(\)/u);
});

test("native observation callbacks contain exception barriers", () => {
  const callbacks = [
    ["NativeBossRelicSelectionPatch", "internal static class NativeBossRelicCommitPatch"],
    ["NativeBossRelicCommitPatch", "internal static class NativeCombatHandSelectPatch"],
    ["NativeRewardProceedPatch", "internal static class NativeRewardPotionDiscardPatch"],
    ["NativeRewardPotionDiscardPatch", "internal static class NativeRewardPotionDiscardEnqueuePatch"],
    ["NativeRewardPotionDiscardEnqueuePatch", "internal static class NativeRewardPotionDiscardCommitPatch"],
    ["NativeRewardPotionDiscardCommitPatch", "internal static class NativeActChangeVoteEnqueuePatch"],
    ["NativeActChangeVoteEnqueuePatch", "internal static class NativeActChangeVoteCommitPatch"],
    ["NativeActChangeVoteCommitPatch", "[HarmonyPatch(typeof(RewardsSetSynchronizer)"],
  ];
  for (const [name, end] of callbacks) {
    const body = section(patches, `internal static class ${name}`, end);
    assert.match(body, /try\s*\{/u, `${name} has no callback try barrier`);
    assert.match(body, /catch \(Exception/u, `${name} has no callback catch barrier`);
  }
  const safety = section(patches, "internal static class NativeUiObservationSafety", "[HarmonyPatch]");
  assert.match(safety, /catch\s*\{/u);
});

test("failed native carrier installation has a durable unknown disposition", () => {
  assert.match(runtime, /ObserveSemanticUiCarrierBindingFailure\(/u);
  assert.match(runtime, /PreviewUnknown\(/u);
  assert.match(runtime, /CommitUnknown\(/u);
  assert.match(runtime, /semantic_ui_action_start_persistence_failed/u);
  assert.match(runtime, /semantic_native_action_start_persistence_failed/u);
  assert.match(runtime, /CleanupUnstartedSemanticUiAction\(/u);
  assert.doesNotMatch(patches, /CurrentParentForCleanup\(/u);
});

test("all owner and witness collisions fail closed without replacement", () => {
  assert.match(patches, /ExactOwnerWitnessBindingTable<object>/u);
  assert.match(patches, /RememberOrFailClosed\(/u);
  assert.match(patches, /already bound to a different child action/u);
  assert.match(patches, /ObserveSemanticUiCarrierBindingFailure\(/u);
});

test("semantic mutations and task completions commit only after authoritative append", () => {
  assert.match(runtime, /PersistTrackerMutationOrUnknown\(/u);
  assert.match(runtime, /BeginDurableMutation\(/u);
  assert.match(runtime, /pending\.MarkAuthoritativeAppend\(\)/u);
  assert.match(runtime, /authoritativeAppend\s*=\s*true/u);
  assert.match(runtime, /if \(authoritativeAppend\)\s*return true/u);
  assert.match(runtime, /unknownAuthoritativeAppend\s*=\s*true/u);
  assert.match(runtime, /if \(!unknownAuthoritativeAppend\)/u);
  assert.match(runtime, /if \(unknownAuthoritativeAppend\)\s*\{[\s\S]*?semantic_unknown\.downstream[\s\S]*?return true;/u);
  assert.match(runtime, /if \(drafts\.Count == 0\)\s*return;/u);
  assert.match(runtime, /Semantic boundary trace is unavailable for a nonempty durable append/u);
  assert.match(runtime, /emptyMutationIsSuccess\s*=\s*true/u);
  assert.match(runtime, /PreviewTaskCompletion\(taskCompletion\)/u);
  assert.match(runtime, /CommitTaskCompletion\(taskCompletion\)/u);
  assert.doesNotMatch(runtime, /resolution = NativePostCommitCompletions\.CompleteTask\(taskCompletion\)/u);
});

test("nested accepted observation uses the durable append outcome", () => {
  const nestedObservation = section(runtime, "internal static bool ObserveAcceptedNestedHumanContinuation", "internal static bool ObserveNestedHumanContinuationUnavailable");
  assert.match(nestedObservation, /PersistTrackerMutationOrUnknown\(/u);
  assert.match(nestedObservation, /emptyMutationIsSuccess:\s*false/u);
  assert.match(nestedObservation, /persistUnknownOnFailure:\s*false/u);
  assert.doesNotMatch(nestedObservation, /PersistSemanticBoundaryDrafts\(drafts\)/u);
  assert.match(runtime, /catch \(Exception exception\)\s*\{[\s\S]*?if \(authoritativeAppend\)\s*return true;/u);
});

test("nonempty skipped writes retain the root on both primary and unknown paths", () => {
  const helper = section(runtime, "private static bool PersistTrackerMutationOrUnknown", "private static NativeCompletionEvidence");
  assert.match(helper, /PersistSemanticBoundaryDrafts\(\s*pending\.Drafts/u);
  assert.match(helper, /if \(authoritativeAppend\)\s*return true;[\s\S]*?Semantic boundary drafts were not durably appended/u);
  assert.match(helper, /PersistSemanticBoundaryDrafts\(\s*unknown\.Drafts/u);
  assert.match(helper, /if \(!unknownAuthoritativeAppend\)[\s\S]*?return false;/u);
  assert.match(helper, /if \(unknownAuthoritativeAppend\)[\s\S]*?semantic_unknown\.downstream[\s\S]*?return true;/u);
});

test("boss same-parent registration is idempotent or explicitly ambiguous", () => {
  assert.match(foundation, /sameParent/u);
  assert.match(foundation, /IsAmbiguous = true/u);
  assert.match(foundation, /Multiple RelicSelectCmd option registrations share the exact PlayerChoice parent/u);
});

test("semantic mutation and native carrier call matrix is transactionally covered", () => {
  for (const [start, end] of [
    ["internal static void ObservePlayCardExecutionAborted", "internal static void ObserveAcceptedUiAction"],
    ["private static void ObserveBeforeActionExecution", "private static void ObserveSemanticDecisionBoundary"],
    ["private static void ObserveSemanticDecisionBoundary", "private static SemanticBoundaryObservation"],
    ["private static bool StartSemanticUiAction", "private static void CleanupUnstartedSemanticUiAction"],
    ["private static void StartSemanticNativeAction", "private static SemanticActionReference"],
    ["private static void ObserveNativeActEntered", "private static void UpdateRunLifecycle"]
  ]) {
    const body = section(runtime, start, end);
    assert.match(body, /PersistTrackerMutationOrUnknown|BeginDurableMutation/u, `${start} bypasses durable mutation`);
  }

  assert.doesNotMatch(patches, /NativeUiCompletionRootBindings\.Take\(/u);
  for (const [start, end] of [
    ["internal static class NativeTreasureNormalRewardsPatch", "internal static class NativeTreasureProceedCompletionPatch"],
    ["internal static class NativeTreasureProceedCompletionPatch", "internal static class NativeRewardClaimStartPatch"],
    ["internal static class NativeRewardPotionDiscardCommitPatch", "internal static class NativeActChangeVoteEnqueuePatch"],
    ["internal static class NativeActChangeVoteCommitPatch", "internal static class NativeRewardSkipCommitPatch"],
    ["internal static class NativeRewardSkipCommitPatch", "internal static class NativeCardRewardSelectionPatch"],
    ["internal static class NativeRewardClaimCompletionPatch", "internal static class NativeEventOptionPatch"],
    ["internal static class NativeEventOptionCompletionPatch", "internal static class NativeRestSiteOptionPatch"]
  ]) {
    const body = section(patches, start, end);
    assert.match(body, /TryGet|completionRootOwner|Transfer/u, `${start} consumes or transfers without exact preview`);
  }
  assert.match(runtime, /RollbackTaskBinding\(taskWitnessId\)/u);
  assert.match(runtime, /catch \(AppendRollbackFailedException exception\)[\s\S]*?DisableSemanticBoundaryTrace\(exception\)/u);
});

test("nested selectors bind exact parent scope to exact screen without ambient guesses", () => {
  assert.match(nestedSelectors, /ExactAsyncOwnerBindingScope<object, Parent, Binding>/u);
  assert.match(nestedSelectors, /Screens\.TryBindCurrent\(/u);
  assert.match(exactAsyncBindings, /AsyncLocal<Frame\?>/u);
  assert.match(exactAsyncBindings, /ConditionalWeakTable<TKey, Holder>/u);
  assert.doesNotMatch(nestedSelectors, /NativePlayerChoiceLineage\.Capture\(\)/u);
  assert.match(nestedSelectors, /exact_parent_root_unavailable/u);
  assert.match(runtime, /StartSemanticNativeAction[\s\S]*?NativeUiCompletionRootBindings\.Remember\(/u);
  assert.match(nestedSelectors, /ObserveAcceptedNestedHumanContinuation\(/u);
  assert.match(nestedSelectors, /TryReadCompletedSelection\(/u);
  assert.doesNotMatch(nestedSelectors, /MoveNext|FIFO|latest[-_ ]frame|NOverlayStack\.Instance\?\.Peek|Task\.Delay|Timer/u);
  assert.doesNotMatch(exactAsyncBindings, /FIFO|latest[-_ ]|Task\.Delay|Timer/u);
});

test("card reward alternatives and removal use exact owner carriers", () => {
  assert.match(nestedSelectors, /CardReward\.OnSelect/u);
  assert.match(nestedSelectors, /NCardRewardSelectionScreen[\s\S]*?ShowScreen/u);
  assert.match(nestedSelectors, /ConditionalWeakTable<NCardRewardSelectionScreen, ScreenBinding>/u);
  assert.match(nestedSelectors, /OnAlternateRewardSelected/u);
  assert.match(nestedSelectors, /TryGetAlternative\(/u);
  assert.match(nestedSelectors, /CardReward\.Reroll/u);
  assert.match(nestedSelectors, /TwoSignalCommitGate CommitGate/u);
  assert.match(nestedSelectors, /if \(!accepted\)[\s\S]*?EndReroll/u);
  assert.match(nestedSelectors, /catch[\s\S]*?ExitNativeUiScope\(scope\)[\s\S]*?throw;/u);
  assert.match(nestedSelectors, /TryBeginCommit\(reward, binding\)/u);
  assert.match(nestedSelectors, /CommitFailed\(reward, binding\)/u);
  assert.match(nestedSelectors, /Reward\.SelectUnsynchronized/u);
  assert.match(nestedSelectors, /CardRemovalReward\.OnSelect/u);
  assert.match(nestedSelectors, /reward_card_removal\.nested_selector/u);
  assert.doesNotMatch(nestedSelectors, /CardRewardAlternative\.Generate\(__instance\)/u);
  assert.doesNotMatch(nestedSelectors, /Tasks\.Remove\(reward\);\s*Tasks\.Add/u);
  assert.match(nestedSelectors, /ArgumentNullException\.ThrowIfNull\(alternatives\)[\s\S]*?alternatives\.ToArray\(\)[\s\S]*?lock \(Gate\)/u);
});

test("merchant removal uses exact shipped three-argument outer carrier", () => {
  const purchase = section(patches, "internal static class NativeShopPurchasePatch", "internal static class NativeShopRoomOpenPatch");
  assert.match(purchase, /typeof\(MerchantCardRemovalEntry\)/u);
  assert.match(purchase, /typeof\(MerchantInventory\), typeof\(bool\), typeof\(bool\)/u);
  assert.match(purchase, /MerchantCardRemovalEntry\.OnTryPurchaseWrapper/u);
  assert.match(purchase, /NativeNestedSelectorBindings\.EnterParent\(/u);
});

test("event and rest outer owners carry selector lineage while parent Task retains disposition", () => {
  const event = section(patches, "internal static class NativeEventOptionPatch", "internal static class NativeEventOptionCompletionPatch");
  const eventCompletion = section(patches, "internal static class NativeEventOptionCompletionPatch", "internal static class NativeRestSiteOptionPatch");
  const rest = section(patches, "internal static class NativeRestSiteOptionPatch", "internal static class NativeRestSiteButtonPatch");
  assert.match(rest, /RestSiteSynchronizer\.ChooseLocalOption/u);
  assert.match(rest, /NativeNestedSelectorBindings\.EnterParent\(/u);
  assert.match(rest, /QueueNativePostCommitBoundary\(/u);
  const eventParent = section(nestedSelectors, "internal static class NativeEventNestedSelectorParentPatch", "internal static class NativeNestedSelectorFactoryPatch");
  assert.match(eventParent, /typeof\(EventOption\), nameof\(EventOption\.Chosen\)/u);
  assert.match(eventParent, /event_option\.nested_selector/u);
  assert.doesNotMatch(eventParent, /ObserveAcceptedSemanticUiAction\(/u);
  assert.match(eventCompletion, /ConditionalWeakTable<EventOption, TaskCarrier>/u);
  assert.match(eventCompletion, /Tasks\.Add\(__instance, new TaskCarrier\(__result\)\)/u);
  assert.match(event, /TryTakeTask\(option/u);
  assert.match(event, /QueueNativePostCommitBoundary\(/u);
});

test("only terminal selector callbacks create one child continuation", () => {
  const accepted = section(nestedSelectors, "internal static class NativeNestedSelectorAcceptedPatch", "internal static class NativeNestedSelectorExitPatch");
  assert.match(accepted, /CompleteSelection/u);
  assert.match(accepted, /ConfirmSelection/u);
  assert.match(accepted, /CloseSelection/u);
  assert.doesNotMatch(accepted, /CancelSelection/u);
  assert.match(accepted, /NativeNestedSelectorBindings\.TryReserve/u);
  assert.match(accepted, /private static void Prefix\([\s\S]*?TryReserve/u);
  assert.match(accepted, /private static void Postfix\([\s\S]*?Binding\? __state/u);
  assert.match(accepted, /private static Exception\? Finalizer\([\s\S]*?TryRelease/u);
  assert.match(accepted, /NativeNestedSelectorBindings\.TryConsume/u);
  assert.match(accepted, /NativeNestedSelectorBindings\.TryRelease/u);
  assert.match(accepted, /NativeTerminalTaskDisposition\.Classify\(task\)/u);
  assert.match(exactAsyncBindings, /holder\.Reserved/u);
  assert.doesNotMatch(accepted, /task\.IsFaulted[\s\S]*?cancelled\s*=\s*true/u);
});

test("a completed native empty selection is accepted while unreadable results fail closed", () => {
  const accepted = section(nestedSelectors, "internal static class NativeNestedSelectorAcceptedPatch", "internal static class NativeNestedSelectorExitPatch");
  assert.match(accepted, /NativeTerminalTaskDisposition\.Classify\(task\)/u);
  assert.match(accepted, /unavailable != null/u);
  assert.match(accepted, /selected = flattened\.ToArray\(\)/u);
  assert.doesNotMatch(accepted, /selected\.Length\s*==\s*0/u);
  assert.match(runtime, /new NativeHumanContinuationEvidence\(/u);
});

test("durable native root collisions retain carriers until unknown append succeeds", () => {
  const nativeStart = section(runtime, "private static void StartSemanticNativeAction", "private static SemanticActionReference CreateSemanticActionReference");
  assert.match(nativeStart, /bool durableFailure = ObserveSemanticUiCarrierBindingFailure\(/u);
  assert.match(nativeStart, /if \(durableFailure\)\s*CleanupUnstartedSemanticUiAction\(/u);
});

test("generic selector parents use exact native carriers without ambient action fallback", () => {
  const parents = section(nestedSelectors, "internal static class NativeNestedSelectorBindings", "internal static class NativeGameActionCardSelectorParentPatch");
  const gameAction = section(nestedSelectors, "internal static class NativeGameActionCardSelectorParentPatch", "internal static class NativeEventNestedSelectorParentPatch");
  const reward = section(nestedSelectors, "internal static class NativeRewardSelectTaskBindingPatch", "internal static class NativeCardRewardAlternativePatch");

  assert.match(gameAction, /CardSelectCmd\.FromSimpleGridForRewards/u);
  assert.match(gameAction, /CardSelectCmd\.FromSimpleGrid/u);
  assert.match(gameAction, /CardSelectCmd\.FromCombatPile/u);
  assert.match(gameAction, /EnterPlayerChoiceParent/u);
  assert.match(parents, /GameActionPlayerChoiceContext[\s\S]*?\.Action/u);
  assert.match(parents, /HookPlayerChoiceContext[\s\S]*?\.GameAction/u);
  assert.match(parents, /BranchingPlayerChoiceContext/u);
  assert.match(parents, /_createdContext/u);
  assert.match(parents, /_originalContext/u);
  assert.match(parents, /ReferenceEqualityComparer\.Instance/u);
  assert.doesNotMatch(parents, /NativePlayerChoiceLineage\.Capture\(\)/u);
  assert.match(reward, /Reward\.SelectUnsynchronized\.nested_parent/u);
  assert.match(reward, /EnterGenericSelectorParent/u);
});

test("binding collisions and rejected acceptance stay fail closed", () => {
  const roots = section(patches, "internal static class NativeUiCompletionRootBindings", "internal static class NativeTreasureChestChoicePatch");
  const reward = section(patches, "internal static class NativeRewardClaimStartPatch", "internal static class NativeRewardProceedPatch");
  const event = section(patches, "internal static class NativeEventOptionPatch", "internal static class NativeEventOptionCompletionPatch");
  const rest = section(patches, "internal static class NativeRestSiteOptionPatch", "internal static class NativeRestSiteButtonPatch");
  const merchant = section(patches, "internal static class NativeShopPurchasePatch", "internal static class NativeShopRoomOpenPatch");

  assert.match(roots, /ExactOwnerWitnessBindingTable<object>/u);
  assert.match(roots, /Bindings\.TryBind\(owner, actionWitnessId\)/u);
  assert.match(roots, /Bindings\.TryGetOwner\(actionWitnessId/u);
  for (const body of [reward, event]) {
    assert.match(body, /if \(!accepted\)\s*(?:return;|\{)|if \(accepted && !/u);
    assert.match(body, /RememberOrFailClosed|TakeIfMatches/u);
    assert.match(body, /if \(!__state\.RootBound\)|if \(!accepted\)|if \(accepted && !/u);
  }
  assert.match(rest, /if \(!accepted\)[\s\S]*?TakeIfMatches/u);
  assert.match(rest, /QueueNativePostCommitBoundary\(/u);
  assert.match(merchant, /if \(!accepted\)[\s\S]*?TakeIfMatches/u);
  assert.match(merchant, /QueueNativePostCommitBoundary\(/u);
});


test("act-ready transfers only the exact reward-screen carrier", () => {
  const transfer = section(patches, "internal static bool TransferRewardScreenToAction", "internal static bool Contains");
  assert.match(transfer, /Bindings\.TryGetOwner\(actionWitnessId/u);
  assert.match(transfer, /owner is NRewardsScreen/u);
  assert.match(transfer, /Bindings\.TryTransfer\(owner, action, actionWitnessId\)/u);
  assert.doesNotMatch(transfer, /Peek\(|CurrentSemantic|TryBind/u);
});


test("event selector owner is staged before Chosen can create its screen", () => {
  const event = section(patches, "internal static class NativeEventOptionPatch", "internal static class NativeEventOptionCompletionPatch");
  const prefix = section(event, "private static void Prefix", "private static void Postfix");
  const postfix = section(event, "private static void Postfix", "private static Exception? Finalizer");
  assert.match(prefix, /NativeUiCompletionRootBindings\.Remember\(option, __state\.Scope\.ActionWitnessId\)/u);
  assert.doesNotMatch(postfix, /RememberOrFailClosed|Bindings\.Remember\(/u);
  assert.match(postfix, /CarrierBindingFailed/u);
  assert.match(postfix, /TakeIfMatches\(option, __state\.Scope\.ActionWitnessId\)/u);
});

test("carrier failure before UI admission is retained on the exact scope", () => {
  const failure = section(runtime, "internal static bool ObserveSemanticUiCarrierBindingFailure", "internal static void ObserveSemanticUiNativeCommitBindingFailure");
  assert.match(failure, /!BoundaryTracker\.Contains\(actionWitnessId\)/u);
  assert.match(failure, /string\.Equals\(context\.ActionWitnessId, actionWitnessId, StringComparison\.Ordinal\)/u);
  assert.match(failure, /context\.DeferredCarrierBindingFailure \?\?= detail/u);
  const admission = section(runtime, "internal static bool ObserveAcceptedSemanticUiAction", "internal static bool ObserveSemanticUiCarrierBindingFailure");
  assert.match(admission, /bool accepted = StartSemanticUiAction/u);
  assert.match(admission, /if \(accepted && acceptedContext\.DeferredCarrierBindingFailure/u);
});


test("unowned native hook selectors retain actual action provenance without a Human parent", () => {
  const resolve = section(nestedSelectors, "private static Binding Resolve", "private static bool TryResolveExactAction");
  assert.match(resolve, /action is GenericHookGameAction hook && hook\.ExecutionStartedTask\.IsCompletedSuccessfully/u);
  assert.match(resolve, /NativeWitnessIdentity\.Get\(action, "game_action"\)/u);
  assert.match(resolve, /NativeOrigin = new NativeDecisionOriginEvidence/u);
  assert.match(resolve, /exact_native_owner_without_human_decision/u);
  assert.match(resolve, /action is PlayCardAction && action\.State == MegaCrit\.Sts2\.Core\.Entities\.Actions\.GameActionState\.GatheringPlayerChoice/u);
  assert.doesNotMatch(resolve, /HumanActionScope\.Current|CurrentAction|LastAction/u);
});

test("failed Human admission fences pending evidence and potion Commit waits for the native task", () => {
  assert.match(runtime, /tracker => tracker\.ObserveUnrecordedHumanEffect\(occurrenceId\)/u);
  assert.match(runtime, /human_effect_barrier_persistence_failed/u);
  const potion = section(patches, "internal static class NativeRewardPotionDiscardPatch", "internal static class NativeActChangeVoteEnqueuePatch");
  assert.doesNotMatch(potion, /Peek\(\) as NRewardsScreen/u);
  assert.match(potion, /SuccessfulDiscard\(__result, __instance\)/u);
  assert.match(potion, /GameActionState\.Canceled/u);
});


test("act-ready expectation and completion use the same owning seam", () => {
  const proceed = section(patches, "internal static class NativeRewardProceedPatch", "internal static class NativeRewardPotionDiscardPatch");
  const commit = section(patches, "internal static class NativeActChangeVoteCommitPatch", "[HarmonyPatch(typeof(RewardsSetSynchronizer)");
  assert.match(proceed, /new NativePostCommitCompletionExpectation\(\s*"act_change.ready",\s*NativeActChangeDecisionProvider\.CommitSeam\)/u);
  assert.match(commit, /"act_change.ready",\s*NativeActChangeDecisionProvider\.CommitSeam/u);
});

test("queued UI execution recaptures native operands instead of reusing the admission catalog", () => {
  const execution = section(runtime, "private static void ObserveBeforeActionExecution", "private static void ObserveSemanticDecisionBoundary");
  assert.match(execution, /semanticNativeActionType: subscription\?\.SemanticNativeActionType/u);
  assert.match(execution, /semanticSelection: subscription\?\.NativeSemanticSelection/u);
  const ingress = section(runtime, "private static bool StartSemanticUiAction", "internal static bool ObserveSemanticUiNativeCommit");
  assert.match(ingress, /match\.BoundAction!\.BoundActionId,\s*ObserveSemanticOnlyNativeActionLifecycle/u);
  assert.match(ingress, /nativeSemanticSelection: nativeSemanticSelection/u);
});

test("reward skip freezes exact owner before synchronous screen removal", () => {
  const code = patches.slice(patches.indexOf("internal static class NativeRewardSkipCommitPatch"),
    patches.indexOf("internal static class NativeCardRewardSelectionPatch"));
  const post = code.slice(code.indexOf("private static void Postfix"));
  assert.match(code, /private static void Prefix/);
  assert.match(code, /new\(owner, NativeRewardUiContext.CurrentRewardsSet\(\), actionWitnessId\)/);
  assert.match(post, /nativeOperand: __state.Rewards/);
  assert.doesNotMatch(post, /NOverlayStack|CurrentRewardsSet\(/);
});
test("queued potion discard selects its native belt provider before room overlays", () => {
  const code = fs.readFileSync(path.resolve(root, "../connector/host/PlayerEnvironment/Witness/ProcessLocalNativeSemanticWitness.cs"), "utf8");
  assert.match(code, /semanticNativeActionType == "NPotionPopup.OnDiscardButtonPressed"\s*\|\| run/);
  const start = code.indexOf("CaptureDomainDecision(\n");
  const scoped = code.slice(start);
  assert.ok(scoped.indexOf("NativePotionDiscardDecisionProvider.Capture") < scoped.indexOf("overlay is NRewardsScreen"));
});

test("matched synchronous Commit admits only its exact unclaimed scope before persistence", () => {
  const code = runtime.slice(runtime.indexOf("private static bool ObserveSemanticUiNativeCommitCore"),
    runtime.indexOf("private static void StartSemanticNativeAction"));
  assert.ok(code.indexOf("if (!resolution.IsMatched") < code.indexOf("bool accepted = ObserveAcceptedSemanticUiAction"));
  assert.match(code, /!context.RootActionClaimed/);
  assert.match(code, /context.ActionWitnessId == actionWitnessId/);
  assert.match(code, /if \(!accepted\) return false/);
  assert.ok(code.indexOf("bool accepted = ObserveAcceptedSemanticUiAction") < code.indexOf("return PersistTrackerMutationOrUnknown"));
});
