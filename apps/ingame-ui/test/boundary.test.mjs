import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "..");
const mod = fs.readFileSync(path.join(root, "PlatformLiveUiMod.cs"), "utf8");
const client = fs.readFileSync(path.join(root, "PlatformLiveStatusClient.cs"), "utf8");
const contracts = fs.readFileSync(path.join(root, "PlatformLiveContracts.cs"), "utf8");
const feed = fs.readFileSync(path.join(root, "PlatformLiveActionFeed.cs"), "utf8");
const presentation = fs.readFileSync(path.join(root, "PlatformLiveUiPresentation.cs"), "utf8");
const workbenchOpen = fs.readFileSync(path.join(root, "PlatformWorkbenchOpen.cs"), "utf8");
const taskBridge = fs.readFileSync(path.join(root, "../game-mod/PlatformTaskBridge.cs"), "utf8");

test("Live UI has a visible entry without keyboard or gameplay authority", () => {
  assert.match(mod, /internal Control Root.*Visible = true/su);
  assert.match(mod, /Root.*MouseFilterEnum\.Ignore/su);
  assert.match(mod, /BuildHeaderButton\("Platform", ShowPanel/u);
  assert.doesNotMatch(mod, /Key\.K|PlatformWorkspaceShortcut/u);
  assert.match(mod, /Key\.Escape/u);
  assert.match(mod, /tree\.ProcessFrame \+= _processFrameHandler/u);
  assert.doesNotMatch(`${mod}\n${client}`, /player-environment\/actions/u);
  assert.doesNotMatch(`${mod}\n${client}`, /bound_action_id/u);
  assert.doesNotMatch(`${mod}\n${client}`, /RecorderRuntime|HumanActionScope|AppendDecision/u);
  assert.doesNotMatch(mod, /override void _(Ready|Process|Input)/u);
});

test("Workbench browser opens after a user click and exact-instance health check", () => {
  assert.match(mod, /BuildHeaderButton\("外部窗口", BeginOpenWorkbench/u);
  assert.match(mod, /if \(_disposed \|\| _workbenchOpenCheck is \{ IsCompleted: false \}\)\s+return/u);
  assert.match(mod, /CompleteWorkbenchOpenCheck\(\);/u);
  assert.match(mod, /if \(!result\.CanOpen \|\| result\.Url is null\)[\s\S]*?return;[\s\S]*?OS\.ShellOpen\(result\.Url\)/u);
  assert.match(workbenchOpen, /GetAsync\(\s*GameStatusUrl/u);
  assert.match(workbenchOpen, /new Uri\(new Uri\(url, UriKind\.Absolute\), "health"\)/u);
  assert.match(workbenchOpen, /observedInstanceId == instanceId/u);
  assert.match(workbenchOpen, /AllowAutoRedirect = false, UseProxy = false/u);
  assert.match(mod, /CancellationToken token = _workbenchOpenLifetime\.Token;[\s\S]*?Task\.Run\(\(\) => PlatformWorkbenchOpenClient\.OpenAsync\(\s*_workbenchHttpClient, token\)/u);
  assert.match(mod, /_workbenchOpenLifetime\.Cancel\(\);[\s\S]*?pendingOpen\.ContinueWith\(task =>[\s\S]*?_workbenchOpenLifetime\.Dispose\(\)/u);
  assert.match(workbenchOpen, /initial\.State != PlatformWorkbenchOpenState\.NotRegistered/u);
  assert.match(workbenchOpen, /new ProcessStartInfo\(path\)/u);
  assert.match(workbenchOpen, /UseShellExecute = false/u);
  assert.doesNotMatch(workbenchOpen, /OS\.ShellOpen\(/u);
});

test("Workbench registration bridge is exact-loopback, game-instance-bound metadata only", () => {
  assert.match(taskBridge, /request\.UserHostName != Authority \|\| request\.Headers\["Origin"\] != null/u);
  assert.match(taskBridge, /!IPAddress\.IsLoopback\(request\.RemoteEndPoint\.Address\)/u);
  assert.match(taskBridge, /RawUrl == "\/v1\/workbench\/status"/u);
  assert.match(taskBridge, /RawUrl == "\/v1\/workbench\/register"/u);
  assert.match(taskBridge, /RawUrl == "\/v1\/workbench\/unregister"/u);
  assert.match(taskBridge, /_workbenchRegistration\.WorkbenchInstanceId != instanceId/u);
  assert.match(taskBridge, /_workbenchRegistration = null/u);
  assert.match(taskBridge, /request\.ContentLength64 is <= 0 or > MaximumWorkbenchRegistrationBytes/u);
  assert.match(taskBridge, /TryReadRegistration\([\s\S]*?document\.RootElement, runtime/u);
  assert.match(taskBridge, /lock \(Gate\)\s*\{[\s\S]*?_workbenchRegistration = registration;/u);
  assert.doesNotMatch(taskBridge, /OS\.ShellOpen|Process\.Start|WebBrowser/u);
});

test("Product navigation retains model and Human surfaces and adds the native Workbench", () => {
  assert.match(mod, /new\[\] \{ "模型实战", "真人采集", "工作台" \}/u);
  assert.match(mod, /BuildAgentRunPage\(_surfaceViewport\)/u);
  assert.match(mod, /BuildRecorderPage\(_surfaceViewport\)/u);
  assert.match(mod, /_surfaces\.Add\(_agentRunPage\)/u);
  assert.match(mod, /_surfaces\.Add\(_recorderPage\)/u);
  assert.doesNotMatch(mod, /"(?:Overview|Environment|Human Data|Diagnostics)"|AddPage/u);
  assert.doesNotMatch(`${mod}\n${presentation}`, /"(Overview|Environment|Human Data|Diagnostics)"|BodyCollapsed|ActiveTab|ToggleActiveTabBody/u);
});

test("Current layout is a fail-soft two-surface state", () => {
  assert.match(presentation, /CurrentVersion = 5/u);
  assert.match(presentation, /string ActiveSurface/u);
  assert.match(presentation, /"agent_run"/u);
  assert.match(presentation, /"human_recorder"/u);
  assert.match(presentation, /live-ui-layout-v5\.json/u);
  assert.match(presentation, /new Vector2\(760, 500\)/u);
  assert.match(mod, /new Vector2\(640, 420\)/u);
  assert.match(presentation, /fail-soft/u);
  assert.match(presentation, /bool Compact = false/u);
  assert.match(presentation, /CompactSize = new\(440, 174\)/u);
  assert.doesNotMatch(presentation, /BodyCollapsed|ActiveTab|CollapsedWorkspaceHeight/u);
});

test("Workspace stays bounded and click-through outside its controls", () => {
  assert.match(mod, /CustomMinimumSize = new Vector2\(640, 420\)/u);
  assert.match(mod, /_workspace.*ClipContents = true/su);
  assert.match(mod, /_workspaceSurface\.SetAnchorsAndOffsetsPreset\(LayoutPreset\.FullRect\)/u);
  assert.match(mod, /ClampWorkspace/u);
  assert.match(mod, /PlatformLiveLayout\.Load\(\)/u);
  assert.match(mod, /PlatformLiveLayout\.Save\(/u);
  assert.match(mod, /_workspaceSurface\.GuiInput \+= OnWorkspaceInput/u);
  assert.match(presentation, /viewport\.X - 32/u);
  assert.match(presentation, /viewport\.Y - 48/u);
});

test("Drag and resize use stable global pointer coordinates and persist on release", () => {
  assert.match(mod, /_dragStartPointerGlobal = mouseButton\.GlobalPosition/u);
  assert.match(mod, /_dragStartWorkspaceGlobal = _workspace\.GlobalPosition/u);
  assert.match(mod, /motion\.GlobalPosition - _dragStartPointerGlobal/u);
  assert.match(mod, /_resizeStartPointerGlobal = mouseButton\.GlobalPosition/u);
  assert.match(mod, /motion\.GlobalPosition - _resizeStartPointerGlobal/u);
  assert.match(mod, /if \(_resizingWorkspace \|\| _draggingWorkspace\)\s+PersistLayout\(\)/u);
  assert.doesNotMatch(mod, /motion[\s\S]{0,180}PersistLayout\(\)/u);
});

test("Recorder feed is read-only, RecordId-rooted, readable, and scroll-stable", () => {
  assert.match(mod, /QueryEvents\(/u);
  assert.match(mod, /RefreshActionFeed\(status\.Recording\)/u);
  assert.match(mod, /_actionFeed\.Recent\(PlatformLiveActionFeed\.MaxEntries, _actionFeedPage/u);
  assert.match(mod, /feedChanged \|= _actionFeed\.Apply\(value\)/u);
  assert.match(mod, /Text = PlatformLiveActionFeed\.FormatEntry\(value\)/u);
  assert.match(mod, /CustomMinimumSize = new Vector2\(0, 24\)/u);
  assert.match(mod, /VerticalAlignment = VerticalAlignment\.Center/u);
  assert.match(mod, /if \(feedChanged\)[\s\S]*?RenderActionFeed\(\)/u);
  assert.doesNotMatch(mod, /RenderActionFeed\(\)[\s\S]{0,120}_recorderScroll\.ScrollVertical = 0/u);
  assert.match(feed, /record:\{value\.RecordId\}/u);
  assert.match(feed, /RecordId action root unavailable/u);
  assert.doesNotMatch(feed, /return \(`bound-action:/u);
  assert.match(feed, /RootPending => "… Observed"/u);
  assert.match(feed, /DecisionRecorded => "✓ Recorded"/u);
  assert.match(feed, /DecisionInvalidated => "✕ Invalidated"/u);
  assert.match(feed, /Action unavailable/u);
  assert.match(feed, /SubjectReferentId/u);
  assert.match(feed, /Target IDs/u);
  assert.doesNotMatch(feed, /InputEventMouseButton|InputEventMouseMotion|AppendDecision|Execute\(/isu);
});

test("Session changes may reset scroll, normal feed updates do not", () => {
  assert.match(mod, /_actionFeedSessionId, sessionId/u);
  assert.match(mod, /_actionFeed\.Reset\(\)/u);
  assert.match(mod, /_recorderScroll\.ScrollVertical = 0/u);
  assert.match(mod, /ResetLayout[\s\S]*scroll\.ScrollVertical = 0/u);
});

test("Agent Run uses only existing typed Policy Runtime status and controls", () => {
  assert.match(mod, /FormatAgentRun\(status\)/u);
  assert.match(mod, /Policy Runtime: \{status\.PolicyRuntimeTransportStatus\}/u);
  assert.match(mod, /SetRuntimeModeAsync\(mode\)/u);
  assert.match(mod, /TickRuntimeAsync\(\)/u);
  assert.match(mod, /准备模型不会自动操作游戏/u);
  assert.match(mod, /PolicyUnavailableReason/u);
  assert.match(client, /sts2\.policy-runtime\/http-2/u);
  assert.match(client, /Headers\.Add\("X-STS2-Policy-Run-ID", expectedRunId\)/u);
  assert.match(client, /HttpMethod\.Post, "v2\/" \+ relativePath/u);
  assert.match(mod, /SetModeAsync\(mode, expected, binding\)/u);
  assert.match(mod, /TickAsync\(expected, binding/u);
  assert.match(contracts, /PolicyRuntime/u);
  assert.doesNotMatch(contracts, /ReadScoreNodes|Contains\("score"/u);
});

test("Recorder controls use the typed application boundary", () => {
  assert.match(mod, /PlatformRecordingCommands\.Execute\(new\(runtime, before\.Lifecycle\.SessionId, command\)/u);
  assert.match(mod, /owner\.QueryStatus, owner\.ExecuteForSession/u);
  assert.match(mod, /RecordingApplicationService\.Instance\.QueryStatus\(\)/u);
  assert.match(mod, /RecordingCommandKind\.StartNewSession/u);
  assert.match(mod, /RecordingLifecycleState\.Recording/u);
  assert.match(mod, /PlatformLiveActionFeed\.FormatCounters\(recording\.Counters\)/u);
  assert.doesNotMatch(mod, /Records = canonical session total/u);
});

test("Connector status is merged only after runtime/environment coherence", () => {
  assert.match(contracts, /EnsureConnectorCoherence\(/u);
  assert.match(contracts, /capabilities\.Host\.RuntimeInstanceId/u);
  assert.match(contracts, /snapshot\.Session\.RuntimeInstanceId/u);
  assert.match(contracts, /controller\.RuntimeInstanceId/u);
  assert.match(client, /PlatformLiveStatusProjection\.EnsureConnectorCoherence/u);
  assert.match(client, /capabilities = null;/u);
  assert.match(client, /snapshot = null;/u);
  assert.match(client, /controller = null;/u);
});

test("No standalone packaging/deployment authority exists in Live UI", () => {
  for (const file of ["build.mjs", "deploy.mjs", "mod_manifest.json", "STS2PlatformLiveUi.csproj"]) {
    assert.equal(fs.existsSync(path.join(root, file)), false);
  }
});

test("Current Action Feed lifecycle fixtures pass", () => {
  const result = spawnSync(
    "dotnet",
    ["test", path.join(root, "test", "STS2PlatformLiveUi.Tests.csproj"), "--configuration", "Release", "--nologo"],
    { cwd: root, encoding: "utf8", shell: false },
  );
  assert.equal(result.status, 0, `Action Feed fixtures failed.\n${result.stdout}\n${result.stderr}`);
});


test("Minimized Recorder has a separate bounded view and retains restore control", () => {
  assert.match(mod, /BuildCompactView\(\)/u);
  assert.match(mod, /_normalView\.Visible = !_layout\.Compact/u);
  assert.match(mod, /_compactView\.Visible = _layout\.Compact/u);
  assert.match(mod, /_resizeHandle\.Visible = !_layout\.Compact/u);
  assert.match(mod, /BuildHeaderButton\("↗", RestorePanel/u);
  assert.match(mod, /WorkspaceSize = _layout\.Compact \? _layout\.WorkspaceSize : _workspace\.Size/u);
  assert.match(mod, /FormatCompactCounters\(recording\.Counters\)/u);
  assert.match(mod, /FormatCompactRecent\(_actionFeed\.RecentDecisions\(3\)\)/u);
  assert.match(mod, /_compactHumanButton\.Visible = _layout\.ActiveSurface == "agent_run"/u);
  assert.doesNotMatch(mod, /Input\.Is(KeyPressed|PhysicalKeyPressed)|_kWasPressed|_escapeWasPressed/u);
});

test("Failure styling consumes owner disposition rather than invalidation kind", () => {
  assert.match(mod, /Color border = PlatformLiveActionFeed\.IsFailure\(value\)/u);
  assert.match(feed, /value\.Action\?\.Disposition is "unresolved" or "failed_closed"/u);
  assert.doesNotMatch(feed, /IsFailure[\s\S]{0,120}DecisionInvalidated/u);
});

test("Compact Policy preserves unavailable mode and Recorder detail label fits its header", () => {
  const summary = mod.split("\n").find((line) => line.includes("_compactSummary.Text") && line.includes("PolicyRuntime?.Mode"));
  assert.ok(summary, "compact Policy status must project the observed runtime mode");
  assert.match(summary, /PolicyRuntime\?\.Mode \?\? "unavailable"/u);
  assert.match(summary, /PolicyRuntime\?\.Controller \?\? "unavailable"/u);
  assert.doesNotMatch(summary, /\?\? "Human"/u);
  assert.match(mod, /recorderHeader\.AddChild\(BuildHeaderButton\("Details"/u);
  assert.doesNotMatch(mod, /BuildHeaderButton\("Session details"/u);
});

test("Closed UI does not poll and Recorder does not materialize Connector snapshots", () => {
  const refresh = mod.slice(mod.indexOf("private void RefreshVisibleStatus()"), mod.indexOf("private async Task SetRuntimeModeAsync"));
  assert.match(refresh, /if \(!_workspace\.Visible \|\| _disposed\)\s+return/u);
  assert.match(refresh, /ActiveSurface == "human_recorder"[\s\S]*?QueryStatus\(\)/u);
  assert.match(refresh, /else\s+_ = PollAsync\(\)/u);
  assert.match(mod, /Lazy<PlatformArtifactIdentity> ArtifactIdentity/u);
  assert.match(mod, /Root\.Resized \+= ApplyWorkspaceBounds/u);
  assert.doesNotMatch(mod.slice(mod.indexOf("private void OnProcessFrame()")), /ClampWorkspace/u);
});

test("Event retention is bounded and a reconnect gap is reread before cursor advance", () => {
  assert.match(feed, /RetainedLimit = 512/u);
  assert.match(feed, /value\.Sequence <= _lastAppliedSequence/u);
  assert.doesNotMatch(feed, /HashSet<string>/u);
  const gap = mod.slice(mod.indexOf("if (batch.Gap)"), mod.indexOf("foreach (STS2HumanAnnotator.Core.RecordingEvent value"));
  assert.match(gap, /OldestAvailableSequence - 1/u);
  assert.match(gap, /batch = STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.QueryEvents/u);
  assert.doesNotMatch(gap, /_lastRecordingEventSequence = Math.Max\(_lastRecordingEventSequence, batch.LatestSequence/u);
});


test("recording handoff validates expected session inside the Recorder owner lock", () => {
  const owner = fs.readFileSync(path.join(root, "../../components/annotator/src/STS2HumanAnnotator.Mod/RecorderRuntime.cs"), "utf8");
  const method = owner.slice(owner.indexOf("internal static RecordingCommandResult ExecuteRecordingCommand("));
  assert.match(method, /lock \(Gate\)[\s\S]*expectedSession.SessionId != _lifecycle.SessionId/u);
  assert.ok(method.indexOf("expectedSession.SessionId") < method.indexOf("CommandLedger.TryGet"));
  const bridge = fs.readFileSync(path.join(root, "../game-mod/PlatformTaskBridge.cs"), "utf8");
  assert.match(bridge, /ExecuteForSession/u);
  assert.match(mod, /_policyCommands.RunAsync/u);
});


test("model commands bind the observed Runtime to this game and recovery epoch", () => {
  assert.match(mod, /GetPlayerEnvironmentControlSnapshot\(\).RuntimeInstanceId/u);
  assert.match(mod, /ObserveBindingAsync\(expected, game\)/u);
  const bindingAt = mod.indexOf("binding = await _statusClient.ObserveBindingAsync");
  const observedSessionAt = mod.lastIndexOf("var recording = STS2HumanAnnotator.Mod.RecordingApplicationService.Instance.QueryStatus();", bindingAt);
  const dispatchAt = mod.indexOf("var prepared = await PlatformRecordingCommands.OnMainThread", bindingAt);
  const handoffAt = mod.indexOf("return PlatformCollectionHandoff.Prepare", dispatchAt);
  const callbackEnd = mod.indexOf("}, STS2Connector.ConnectorMod.RunOnMainThread);", handoffAt);
  assert.ok(observedSessionAt >= 0 && observedSessionAt < bindingAt && bindingAt < dispatchAt && dispatchAt < handoffAt && handoffAt < callbackEnd);
  const callback = mod.slice(dispatchAt, callbackEnd);
  assert.match(callback, /intent != Interlocked.Read\(ref _policyUiIntent\) \|\| _disposed/u);
  assert.match(callback, /GetPlayerEnvironmentControlSnapshot\(\)\.RuntimeInstanceId != game/u);
  assert.match(callback, /Prepare\(recording\.Lifecycle\.SessionId/u);
  assert.match(client, /X-STS2-Game-Instance-ID/u);
  assert.match(client, /X-STS2-Recovery-Epoch/u);
});


test("unknown model commands retain a run fence while Human and Stop remain available", () => {
  assert.match(client, /PlatformPolicyTransport.SendAsync/u);
  assert.match(client, /AllowAutoRedirect = false, UseProxy = false/u);
  assert.match(mod, /HasUnknownCommand\(_displayedPolicyRunId\)/u);
  assert.match(mod, /_compactHumanButton.Disabled = !\(available \|\| uncertain\)/u);
  assert.match(mod, /_endTestButton.Disabled = !\(available \|\| uncertain\)/u);
  assert.match(mod, /_tickButton.Disabled = !available \|\| uncertain/u);
  assert.match(mod, /status.PolicyRuntime\?\.RunId \?\? _displayedPolicyRunId/u);
  const preparation = mod.slice(mod.indexOf("binding = await _statusClient.ObserveBindingAsync"), mod.indexOf("return PlatformCollectionHandoff.Prepare"));
  assert.match(preparation, /intent != Interlocked.Read\(ref _policyUiIntent\) \|\| _disposed/u);
});
