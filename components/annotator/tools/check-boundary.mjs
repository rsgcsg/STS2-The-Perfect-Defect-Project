import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const sourceDirs = [
  path.join(root, "src", "STS2HumanAnnotator.Mod"),
  path.join(root, "src", "STS2HumanAnnotator.Core")
];
const sources = sourceDirs.flatMap((directory) => fs.readdirSync(directory)
  .filter((name) => name.endsWith(".cs"))
  .map((name) => fs.readFileSync(path.join(directory, name), "utf8")))
  .join("\n");
const applicationService = fs.readFileSync(
  path.join(root, "src", "STS2HumanAnnotator.Mod", "RecordingApplicationService.cs"),
  "utf8"
);
const recorderRuntime = fs.readFileSync(
  path.join(root, "src", "STS2HumanAnnotator.Mod", "RecorderRuntime.cs"),
  "utf8"
);
const nativeUiPatches = fs.readFileSync(
  path.join(root, "src", "STS2HumanAnnotator.Mod", "NativeUiPatches.cs"),
  "utf8"
);
const sourceInput = fs.readFileSync(
  path.join(root, "src", "STS2HumanAnnotator.Mod", "RecorderRuntime.SourceInput.cs"),
  "utf8"
);

const forbidden = [
  ["HarmonyTranspiler", "transpiler patches are forbidden"],
  ["Input.ParseInputEvent", "synthetic coordinate/input injection is forbidden"],
  ["MousePosition", "mouse coordinates cannot identify actions"],
  ["SourceContract", "business source authority is forbidden"],
  ["Capabilities.ExecutionAvailable", "recording cannot depend on Connector mutation permission"],
  ["PlayerEnvironmentService.Submit", "the recorder cannot execute Connector actions"],
  ["RequestEnqueue(new", "the recorder cannot enqueue game actions"],
  ["_latestAuthoritativeFrame", "a generic latest-frame fallback cannot authorize a human action"]
];
const errors = [];
for (const [needle, detail] of forbidden) {
  if (sources.includes(needle)) errors.push(detail);
}
if (!sources.includes("[HarmonyPatch(typeof(GameAction), nameof(GameAction.OnEnqueued))]"))
  errors.push("accepted native actions must be observed after exact GameAction enqueue");
if (!sources.includes("NativeActionLifecycleSubscription"))
  errors.push("accepted native actions require a bounded process-local lifecycle witness");
if (!sources.includes("PlayerEnvironmentNativeWitness.Capture("))
  errors.push("the recorder must consume the process-local Connector witness");
if (!sources.includes("reference_equality_to_frozen_host_binding"))
  errors.push("the record gate must require exact frozen reference mapping");
if (!sources.includes("StageCardPlay(NHandCardHolder holder)"))
  errors.push("card play must stage its exact pre-action frame before native hand removal");
if (!sources.includes("ReferenceEquals(staged.Holder.CardModel, stagedCard)")
    || !sources.includes("StagedCardPlays.TryGet(stagedOwner, out staged)")
    || !nativeUiPatches.includes("RecorderRuntime.ForgetStagedCardPlay(__instance)")
    || sources.includes("TimeSpan.FromSeconds(30)"))
  errors.push("staged card frames must remain bound to the exact native card reference");
if (!sources.includes("context.AcceptsRootAction(nativeActionType)"))
  errors.push("same-type game actions must not claim the human root before exact mapping");
if (!sources.includes("!hasMapping || match == null || !(IsExact(match) || (context.NativeInputBinding && match.Status == \"exact_native_input\" && match.MatchCount == 1 && match.NativeInput != null))")
    || !sources.includes("context.TryClaimRootAction(nativeActionType)"))
  errors.push("the recorder must exact-match before claiming the human root action");
if (!applicationService.includes("RecordingCommandResult Execute(RecordingCommand command)"))
  errors.push("all recording views must use the typed RecordingService command boundary");
if (!applicationService.includes("RecordingEventBatch QueryEvents(long afterSequence)"))
  errors.push("recording views require the typed reconnectable event query boundary");
if (/HumanActionScope|GameAction|AppendDecision|PlayerEnvironmentService/u.test(applicationService))
  errors.push("application commands must not enter native witness or gameplay execution paths");
if (!recorderRuntime.includes("RecordingLifecycleSnapshot.Ready(now)"))
  errors.push("runtime initialization must stop at Ready without creating a session");
if (!recorderRuntime.includes("RecordingCommandKind.StartNewSession"))
  errors.push("recording session creation must be an explicit typed command");
if (!recorderRuntime.includes("TerminateClosePendingWork()"))
  errors.push("user Close must terminate pending work at the recording boundary");
if (recorderRuntime.includes("PendingDecision")
    || recorderRuntime.includes("AcceptedHumanActionLedger")
    || recorderRuntime.includes("SerializedEvidenceAdmission"))
  errors.push("current runtime must not retain a second mutable Human causal authority");
if (!recorderRuntime.includes("private static bool CanOpenSemanticEvidenceWindow()"))
  errors.push("current Human admission must use the semantic evidence-window gate");
const semanticAdmission = recorderRuntime.slice(
  recorderRuntime.indexOf("private static bool CanOpenSemanticEvidenceWindow"),
  recorderRuntime.indexOf("internal static IDisposable? StageCardPlay")
);
if (/BoundaryTracker\.(?:HasUnresolvedActions|CanOpenNextRoot)/u.test(semanticAdmission))
  errors.push("Human root capture must not be gated on prior successor readiness");
if (!recorderRuntime.includes("lifecycleState == RecordingLifecycleState.Recording"))
  errors.push("Human admission must remain bounded by recording lifecycle");
if (!recorderRuntime.includes("tracker.ObserveBeforeActionExecution("))
  errors.push("the exact next Human execution boundary must settle only through the semantic tracker");
if (recorderRuntime.includes("overlapping_action_before_successor"))
  errors.push("legacy overlap settlement reasons must not survive in the current causal path");
if (recorderRuntime.includes("TrySettle(pending, frame)")
    || recorderRuntime.includes("NativeActionLedger.CanAdmitStrictTransition"))
  errors.push("archival ledger settlement must not authorize semantic successors");
if (!recorderRuntime.includes("SemanticBoundaryTraceKinds.TransitionProved"))
  errors.push("compatibility transition persistence must begin only from a proved semantic draft");
if (!recorderRuntime.includes("PersistDerivedTransitionProjection("))
  errors.push("current decision and canonical outputs must be derived from semantic transition proof");
if (!sources.includes("SemanticTransitionProjection.CreateDecision("))
  errors.push("current decision output must use the non-authorizing semantic projection");
if (!sources.includes("SemanticTransitionProjection.CreateCanonical("))
  errors.push("canonical output must use the non-authorizing semantic projection");
if (!sources.includes("draft.Kind != SemanticBoundaryTraceKinds.TransitionProved"))
  errors.push("current projection must reject non-proved drafts");
const recordedApplicationProjection = `PublishApplicationEvent(
                RecordingEventKind.DecisionRecorded,
                draft.Action.RecordId,
                (canonical.Action?.Verb ?? canonical.NativeInput!.Verb),
                ToActionProjection(draft.Action, draft.SemanticPre, draft.SemanticSuccessor) with { Disposition = "recorded" });`;
if (!recorderRuntime.includes(recordedApplicationProjection))
  errors.push("recorded application events must correlate on the semantic decision RecordId");
if (recorderRuntime.includes(`PublishApplicationEvent(
                RecordingEventKind.DecisionRecorded,
                eventId,`))
  errors.push("canonical/journal event identity must never replace the semantic Human root in application events");
if (recorderRuntime.includes("TryObserveSemanticDecisionBoundary();"))
  errors.push("semantic successor collection must not poll a complete Snapshot every process frame");
if (!recorderRuntime.includes("if (!BoundaryTracker.HasUnresolvedActions)"))
  errors.push("semantic boundary materialization requires real tracker debt");
if (recorderRuntime.includes("_serializedCloseBoundaryRequested")
    || recorderRuntime.includes("_semanticCloseDrainDeadline")
    || recorderRuntime.includes("recording_close_drain_timeout"))
  errors.push("user Close must not wait on a semantic drain deadline or report a timeout");
if (!recorderRuntime.includes("RecordingClosePolicy.TerminalUnknownReason"))
  errors.push("user Close must preserve an explicit terminal successor-unknown reason");
if (/\b(?:internal|private)\s+static\s+bool\s+Prefix\s*\(/u.test(nativeUiPatches))
  errors.push("annotator Prefixes must never skip a native STS2 method");
if (/AllowMutation|BlockMutation/u.test(sources))
  errors.push("evidence admission must not create gameplay mutation authority");
if (!nativeUiPatches.includes("RecorderRuntime.StageNativeCardPlay(__instance, holder);")
    || !sourceInput.includes("StageNativeCardPlay(NPlayerHand hand, NHandCardHolder holder)")
    || !sourceInput.includes("if (!IsSourceRecording) return StageCardPlay(holder);"))
  errors.push("card staging must observe the exact pre-action frame without controlling native input");
if (!recorderRuntime.includes("native input continues without a canonical transition claim"))
  errors.push("unresolved evidence must fail closed for canonical claims without blocking native Human input");

console.log(JSON.stringify({ status: errors.length === 0 ? "pass" : "fail", errors }, null, 2));
if (errors.length) process.exit(1);
