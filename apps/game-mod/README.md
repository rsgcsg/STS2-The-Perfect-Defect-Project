# STS2 Platform Game Mod

This is the only production game-side package installed by STS2 AI Platform.
One `STS2_PLATFORM` manifest loads one assembly containing three separately
owned components:

1. Connector observes player-visible state and owns Reads/BoundActions.
2. Human Annotator witnesses native human input and writes immutable raw data.
3. Platform Live UI presents typed status and recording/runtime controls.

The explicit unified initializer starts them in that order. Packaging does not
merge their authority: the UI cannot submit gameplay actions, the Annotator
cannot authorize them, and the Connector cannot write Human evidence.

## Lifecycle

Fully close STS2 before build/deploy/rollback:

```bash
npm run game-mod:build
npm run game-mod:doctor
npm run game-mod:deploy
npm run game-mod:launch
npm run game-mod:verify-loaded
```

Deployment backs up the existing unified artifact, the predecessor three-Mod
files, component configuration, and the native Windows Mod settings before
replacement. On Windows it preserves every settings entry while enabling only
`STS2_PLATFORM`; rollback restores the exact prior settings bytes. It removes
the three predecessor manifests rather than retaining a silent fallback.
Restore with:

```bash
npm run game-mod:rollback
```

After cold load, click the visible Platform launcher to open the two-tab Workspace; `Escape`
also closes it. Verification requires one exact `STS2_PLATFORM` Modset, one
common loaded SHA/MVID for Connector/Annotator/UI, component-specific embedded
source provenance, a ready UI node, and Connector execution availability.
`verify-loaded` polls this exact readiness envelope for a bounded startup
window; timeout still fails closed with the latest observed identity state.
The unified load log and Connector capabilities own process-global artifact,
source and Modset evidence. Annotator status is bound to the current process
and load generation; its session-bound environment is an additional check when
a session exists, not a heartbeat or a prerequisite for the legal Ready state.

The UI is composed from built-in Godot nodes and driven by SceneTree signals.
Do not replace it with a custom `Node` callback unless the package explicitly
adds and validates Godot's C# source-generator toolchain; standard single-DLL
Mod builds do not generate those callbacks.

`installed`, `loaded`, `launcher-visible`, Human action evidence and Policy evidence
are separate claims.

The Connector's optional card-reward canary diagnostic is enabled only for a
deliberate local launch with `STS2_CONNECTOR_CARD_REWARD_CANARY_DIAGNOSTICS=1`.
Its bounded private `[STS2 Platform] card-reward-canary` log lines join actual
refresh begin/Create/finish observations to one current-page parent/binding
result. Preserve those lines with the exact launch and `verify-loaded` identity;
absence of lines, an incomplete result or budget exhaustion is not a successful
hook canary. `binding_facts_captured` is limited to the private parent/button
binding facts, not a complete actionable Snapshot or Human decision. The setting
does not enable input or recording.

## Installed collection setup

The fixed Collection Tool can include this component's bounded `collection-setup`
CLI, its Host process/discovery dependencies and exact native build provenance.
The Evidence `CollectionTool.setup_status` and `bind_recording_root` methods
verify that complete release inventory before invoking the owner. Consumers need
Node.js 20+ and the installed game; they do not need a Platform checkout.

```bash
node /absolute/tool/setup/apps/game-mod/collection-setup.mjs status \
  --game-dir /absolute/game --recordings-root /persistent/campaign-recordings \
  --mod-provenance /absolute/tool/game-mod/build-provenance.json
node /absolute/tool/setup/apps/game-mod/collection-setup.mjs bind \
  --game-dir /absolute/game --recordings-root /persistent/campaign-recordings \
  --mod-provenance /absolute/tool/game-mod/build-provenance.json
```

`bind` requires all STS2 processes stopped and no Annotator environment override.
It refuses symlinks, ambiguous/predecessor installations, session directories and
unsafe roots. It preserves unrelated configuration options, the status location
and old recording bytes, archives the exact previous config and atomically changes
only `recording_root`. A failed replacement leaves the old config intact. To undo
a binding, stop STS2 and restore the reported backup bytes. Setup never launches,
stops or controls the game, changes Mod settings or changes native compatibility.

`collection-setup-1` reports `configured` separately from `connected` and `bound`.
The latter require exact installed bytes, a live Connector capabilities response,
the current native status, loaded identity and OS process generation. Its native
destination is the current status `recording_directory`: an open store's exact
session ID identifies its parent as the root. Ready without a session and
`recording_closed` report the root directly; Close retains the old session ID
after disposing its store. Old receipts, directory scans and elapsed time do not
establish readiness. Native game SHA/MVID remain null when current native status
does not publish them. Errors retain a code and next action; local paths are private
operator data and should be omitted from browser-facing projections.

Passive collection reports `execution_available` and game `compatibility`
independently. An unadmitted mutation tuple does not disconnect an otherwise exact
passive recorder. The existing `verify-loaded` mutation/readiness gate still
requires Connector execution availability; collection binding cannot grant it.

The rc.5 setup candidate uses Annotator rc.5, Evidence rc.8 and Host tooling rc.8.
The version updates preserve current recording schemas and all native decision,
causal and mutation-admission behavior. New SHA/MVID and source identities still
require their own exact build/install/load evidence. The historical rc.4 Human
artifact and published Host rc.7 package remain pinned to their original bytes;
their qualification/publication is not transferred to this candidate.

New build provenance publishes only assembly SHA256/MVID for the Mod and game assembly.
The identity CLI can still return a private operator path, but the distribution builder
selects only those identity fields. Existing build/installed evidence is not rewritten.
The owning lifecycle compares exact hashes/MVID and compiled source, not builder paths.


## Unified task handoff

The two presentation tabs are **真人采集** and **模型实战**. Preparing a model in
Workbench keeps Human control. Starting a test closes the observed Human recording
through the typed Recorder owner and waits for durable Close. Human/Stop always
remain recovery commands; a pending or unknown Close cannot authorize model control.

`PlatformTaskBridge` serves `sts2.platform/task-status-1` on loopback port 15528.
GET `/v1/tasks/status` observes the current game instance and recorder lifecycle.
POST `/v1/tasks/prepare-model` accepts only `runtime_instance_id`, nullable
`recording_session_id`, and UUID `command_id`. Exact Host, no Origin, bounded JSON,
loopback peer and current game identity are checked. Recorder compares the expected
session under its own mutation lock, preventing a stale request from closing a new
recording. This endpoint never creates actions, grants gameplay authority, retries
unknown delivery, or converts Agent evidence into Human data. Workbench additionally
compares the actual configured Connector identity; an arbitrary local process is
not an authenticated remote browser.

The UI serializes Runtime mutations and cancels superseded intents. A Human/Stop
request prevents older preparation/mode responses from submitting a late Auto/Tick.
Already submitted actions remain owned by Runtime/Connector and require their actual
receipt. Test records can be shared separately from Human collection; native victory
and full-run qualification are not inferred from Runtime termination.

### Source recording application entry

The existing bridge additionally exposes GET `/v1/tasks/recording/status` and
POST `/v1/tasks/recording/command`. These are local application lifecycle commands,
not gameplay input. New mutations reject Origin, cookies, Transfer-Encoding,
wrong Host/non-loopback peers, unknown or duplicate fields and JSON bodies over
4096 bytes. Read and queue admission are bounded. Mutations run on the existing
native main-thread queue; runtime identity is compared there immediately before
the typed Recorder call, which atomically compares the expected session.
Model preparation now chooses command-1/2/3 according to the current legacy,
Source1 or Source2/3 profile and uses that same queue.

The POST exact envelope is `{schema,runtime_instance_id,recording_session_id,command}`
with schema `sts2.platform/recording-request-1`; the session is explicitly nullable.
Its exact command fields are `{schema,command_id,kind,capture_profile_id,source_declaration,expected_source_segment_id}`.
Schema is `sts2.ai-platform/recording-command-3`, command ID a UUID, and kind one
of `start_new_session`, `pause`, `resume`, `change_source`, `close`. Unused fields
are explicitly null. Start requires profile `native-logical-source-v3` and an
explicit declaration. ChangeSource requires that declaration and the observed
exact segment, with no profile. Pause/Resume/Close carry none of those optional
values. Declaration is exactly `{source_kind,actor_id,declaration_id,machine_verifiable}`;
existing kinds are `declared_human`, `agent_native_ui`, `agent_protocol`, `unknown`,
identifiers use the Recorder's bounded ASCII rule, and machine_verifiable is false.
Source changes are Paused-only under the owner contract.

The path-free status schema `sts2.platform/recording-status-1` has exactly
`schema,runtime_instance_id,recording_session_id,recording_lifecycle,capture_profile_id,closeout_status,source,health,non_claims`.
Source is nullable or the current epoch/segment/declaration and owner observation,
input, pending, epoch, gap and accounting counts/error. Health contains
`append_health,disk_health,error`; public errors contain only sanitized codes.
No raw stream bytes, native operands or recording directories are exposed.
Result schema `sts2.platform/recording-result-1` has exactly
`schema,command_id,accepted,pending,code,status`. Pending Close is not durable Closed.

`NativeTasks.recording_status(connector_endpoint)` and
`recording_command(connector_endpoint, observed, kind, source_declaration=...)`
are the typed Python entry. They compare the configured public Connector's
runtime identity, preserve the observed session/segment and issue each command
once with proxy/redirect avoidance. Lost or malformed responses remain unknown;
a subsequent status read is not proof that the original command executed and
does not retry it. Source declaration never attests Human origin. These new
source/test interfaces require their own clean package/install/load and bounded
physical/protocol canaries before runtime qualification.
