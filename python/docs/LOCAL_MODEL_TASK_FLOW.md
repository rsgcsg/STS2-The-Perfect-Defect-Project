# Local model tasks and explicit shared reports

This is the owning orchestration guide for `workbench.local_models`,
`workbench.native_tasks`, `workbench.evaluation_sharing`, and
`hub.live_evaluations`. Root workflow and existing Policy/Evidence contracts
remain authoritative. This flow does not make arbitrary downloaded checkpoints
executable and does not qualify Human data or model game performance.

The common executable `AgentComposition` / immutable `AgentRunRequest` contract
for this same orchestration owner is defined in
[AGENT_EXECUTION_CONTRACT.md](AGENT_EXECUTION_CONTRACT.md). It includes model-less
Teacher and Public M2 admission, request/run association and their different Task
witnesses. This guide continues to own the existing preparation, control, Stop,
sharing and recovery flow; the common contract adds no second execution owner.

The existing `LocalModelExport.start` owner accepts the explicit closed
`compatibility_profile="native-evidence-rc27-to-rc29-v1"` for a supported original
sampled native Model. Ordinary calls keep strict original export behavior. The
same export operation records original and derived IDs, and journals the derived
identity before immutable publication. It then uses the ordinary native export
and verification path, preserving any previous completed original receipt.
The existing clean-source identity owner captures the committed source/lock
producer and revalidates it immediately before derived publication intent;
dirty source or a changed producer fails before any derived publication.
Workspace inspection and download caches remain read-only. Explicit compatible
publication opens a writable store through the existing Dataset workspace owner,
requiring the same marker-bound root and original Model bytes as the reader.
Missing ownership, mismatched roots or read-only publication access rejects
before derived intent; method names alone never establish write authority.

An operator can call `reconcile_native_compatibility(operation_id,
original_model_id=..., derived_model_id=...)` against the existing export lock.
It preserves the active interrupted journal bytes and writes one immutable
receipt under `model-export-reconciliations`. That receipt binds the exact old
journal bytes/hash, IDs, profile, store root, old producer, current reconciliation
producer and observation time. If already present, the same receipt and original
observation are revalidated and reused. A present derived Manifest is verified
and exported through the ordinary native path, with no republishing; an
incompatible current loader leaves the interruption fenced.

An absent Manifest is recorded only as `manifest_absent_at_reconciliation`.
It does not prove that no publication or orphan payload ever existed, and no
payload is removed. The old outcome remains interrupted. A separately explicit
`start(original_id, compatibility_profile=..., prior_native_reconciliation=
receipt_sha256)` may start a new migration only when the original current journal,
receipt, workspace and IDs still match and the new clean producer differs from
the old one. Its new operation and derived IDs must differ, and the new journal
retains the prior receipt link. A Manifest appearing after the observation blocks
this path until its own bytes are reconciled. No other pending operation or model
family inherits this exception; startup and status never retry a migration.
An interruption after publication intent remains pending/unknown. A caller may
explicitly reconcile the recorded derived ID if its CAS Manifest exists; it
cannot automatically repeat the original migration or infer publication from
current files. This owner-call path adds no service, registry, trainer or UI
operation. Candidate publication is distinct from the original/target numerical
parity and native qualification gates in the native Model guide.

## Prepare and load

### Native episode preparation and explicit finish

For the in-game display on an already running ordinary game, `models.takeover`
uses the same Application preparation and Agent-request owners to attach a fresh
`cold_current_segment`. It does not seed/reset the game or change speed. After
explicit Stop, the original typed clean seal is checked before detaching the old
attached segment and preparing the current game again; changing Model or opening
a new game never rewrites the old request/evidence. Running, tainted or unknown
Actors require their original recovery and cannot be silently replaced. CLI/API
users can use the existing `native-episode attach`, `agent-request` and `run-agent`
steps over the same owners. A normal Game Mod launch with the selected workbench
is the current-game entry; a seeded `prepare-owned` Reference is a different,
explicit mode whose original seed/profile identity checks remain unchanged.

In the native panel, open 模型实战 and select a normally registered Model. On a
running game, 开始展示 requests Auto on that current game; it does not create or
reset a game. 暂停 returns control while retaining the same Actor, 继续 uses that
Actor, and 结束 seals the operation. Wait for the original stopped/sealed result
before selecting another Model and starting another segment. At the main menu,
enter a normal game first. Model registration and a visible selection alone do
not prove inference or a successful Start.

When public input is unavailable (for example `selector:invalid_content`), the
Runtime hands control back. Inspect the reason and resolve the native screen
manually only when the original operation is known and released; then use its
explicit Continue. Unknown delivery or taint instead uses original recovery;
do not click Start repeatedly. A clean Stop and a loss screenshot do not prove
a qualified game outcome or successful Task. These control semantics describe
the supported path; a version's actual canary limitations stay in its task and
recording, including buttons that were not exercised before the game ended.

Native Start admits the same declared `ACK_committed_session_rng` used by the
random/frequency/linear baselines. A fresh attached segment creates a fresh Agent
session and seeded RNG; Pause/Continue retains the same Actor and committed state.
The GUI adds no RNG owner. A rejected `current_start_unsupported` is an admission
failure, not an unknown delivery. When a Model POST response is lost, use Stop on
the original task and wait for its actual stopped/sealed result. Only the Model
owner's exact request/binding/run terminal card clears that presentation fence;
ACK, a renewed connection or another task's completion does not. If no admitted
Actor or clean original seal can be proven, keep the uncertainty and use the
existing diagnostics/recovery path instead of repeatedly clicking Start.

Checkpoint publication is CPU/Store work, independent of a running game. Use
`python -m spireagent.workbench project model --config <project.json>
--action export-checkpoint --artifact <checkpoint-id>
--checkpoint-selection best|current` from the locked `python/` environment; inspect that
same Model operation's completed `last_checkpoint_export`, then use the normal
Model download and registration commands. The recipe adapter owns publication;
GUI/CLI/API only map to it. Download does not establish training ancestry or
qualify gameplay. Registrations label available authoritative selected steps and
distinguish final training results from best/current checkpoint previews.

The existing Application exposes prepare/status/finish in its local-environment
namespace and continues `/api/local-models/run-agent` for execution. Bodies
select only owned or attached preparation, an intent ID, explicit canonical seed
for the fixed Defect A0 owned start, and the existing closed presentation/speed
choices. They cannot select a URL, executable, module, env, PID, token or path.
An operator-owned `native-host-profile-v1.json` under the existing state root
pins the installed public Host package, game installation/template/endpoint,
exact loaded game/Mod tuple and finite preparation/cleanup limits. Owned
`speed: default` resolves to the user-selected Instant project default; explicit
Normal/Fast/Instant share the same native Host and public contracts. The binding
records `{mode: instant, basis: project_default}` alongside the actual typed native
readback. A default choice is not mode/presentation/package/tuple qualification;
final-candidate live mode verification remains pending until separately recorded.
Owned preparation derives the external Host state directory as
`<ProjectConfig.state_dir>/native-host`; the request body and trusted profile do
not add another caller-supplied path. The operator stages the existing template
at `native-host/profile-templates/defect-a0-s0/{template.json,user-data/}` through
the existing Host template owner. The directory must be canonical, ordinary and
disjoint from the immutable installed package. The reference CLI receives
`--local-root` and its actual ready readback must match both this local root and
its `evidence` child before reset. An older CLI without the capability, or a
wrong/unsafe root, rejects before offering native preparation. Package hashing
still covers every installed file; mutable state is never excluded to make a
modified installation pass. Template absence/drift remains an owning template
failure, not permission to create replacement game state.
The profile's closed `candidate_admission` selects `sealed` or `exact_canary`
separately for game and Connector; only this trusted configuration can forward
the existing process opt-ins. Its `installed_tuple` is checked by the Host before
native spawn and against loaded capabilities before bootstrap. The profile pins
game version/commit/assembly SHA, Host source/artifact SHA/MVID and Modset
fingerprint. The profile's retained `default_speed` field does not supply
qualification authority or override the code-owned project default. Attached
preparation never writes speed or creates a Host, profile or seed; personal game
preferences remain untouched.

`POST /api/local-environment/native/prepare`, `GET /api/local-environment/native`
and `POST /api/local-environment/native/finish` expose that one lifecycle.
The ordinary signed local control client and browser origin/CSRF rules apply.
`POST /api/local-models/request` resolves a closed selection/operation/run profile
and optional opaque `native_episode_ref` through the trusted owner; its effective
v1 request is then submitted to the existing `/api/local-models/run-agent` route.

One native preparation intent binds its canonical body before spawn; same
intent/body returns its original resource operation, changed body conflicts.
The same Environment owner retains immutable per-operation request/body digests
and original terminal results through its existing LocalBlobStore primitive,
with atomic recorded status and one live pointer. Completing A, then B, does not
permit replaying A to reset another world. Finishing historical A returns its
original recorded cleanup and cannot Stop or close active B. Original records
survive service restart; active/partial records have no adopted process handle
and remain recovery-required. There is no automatic history expiry or deletion.
Missing or damaged retained identities fail closed instead of becoming fresh
preparation authority. If an operator erases the whole project state, original
results cannot be reconstructed; that is not an automatic retry/recovery path.
Immutable request and recorded status publication both precede worker/process
startup. Storage failure during finish does not skip exact owned cleanup and is
reported separately from original Actor, native-child and driver exit facts.
The public Host client is registered before readiness/reset awaits so finish
can cancel it without queueing behind preparation. Ready is published only
after exact bootstrap release and unheld-runtime proof. Unknown remains unknown
and blocks Agent admission; no management retry/query framework is provided.
Resource phase and original Actor/run results are distinct. Mode Human or panel
closure does not auto-close the Host. Explicit finish and App shutdown use the
same Actor Stop/seal then owned-close sequence. The existing Managed lifecycle,
records and APIs retain their semantics.

### Continuous display and changing Model in the same owned world

The same Application `agent-request` / `run-agent` flow accepts
`run_profile: "continuous"` only with the explicitly supported Runtime rc40
installation pin. CLI selects `--run-profile continuous`; direct Runtime CLI uses
`--autonomy-mode continuous` and rejects finite cap flags alongside it. Omitted
Application profiles remain the original finite `short`, and `extended`, Source3
collection and frozen evaluation suites retain their finite limits.

Runtime rc40 emits startup/status/event-2 for both modes. Continuous config is
only `{mode: "continuous"}`. Status caps and remaining are null, while actual
submission/call counts and elapsed time remain visible. Human pauses the current
segment and releases control; explicit Auto continues the same run with a new
segment under its retained configuration. Stop ends that Runtime run and seals
its original bytes. Local call/recovery/no-progress limits, unknown delivery and
release uncertainty retain their existing fail-closed behavior; no automatic
rearm, resubmit or new game is added.

The native play page selects a real registered catalog entry. Its ready Reference
is passed separately from the signed local pair to the same Application request
owner, which creates the fresh Reference selection binding. A new explicit Model
start may retire the existing Actor only after the original typed verifier
rechecks its exact stopped run/request, released controller, no taint and no
pending request. The original Host `episode_identity` must also confirm the same
runtime, profile generation/template and requested/actual seed before retirement,
and again before creating the replacement Actor. A changed or unavailable readback
rejects reuse and retains the original Actor/report. The Environment's original
private operation store immutably retains the resource, request and verified
report before the live Actor pointer becomes ready. Persistence failure leaves
creation fenced. No new seed, reset or game closure is part of changing Model.
A same-seed manual restart is not independently proven to be the same native run
by this provenance check; original native generation/epoch gates still apply.

Closing/reopening a panel or renewing its pair keeps the same live App/run.
Restarting the App process permits only exact original Runtime Human/Stop/seal
recovery. Lost Host stdio ownership remains recovery-required; it does not enable
Continue/new Actor or adopt a process by PID. The game may remain manually usable;
that does not confirm its original world or cleanup lifecycle. Explicit native
finish remains separate from Model Stop.

For a source candidate already selected through the original installer, owned
native preparation verifies the returned Host profile path and generation, then
copies the same private launcher/open/native-access bytes to that isolated game
HOME. It creates no credential and never edits the daily selected slot. The
fixed native bridge is paired only when its runtime matches the selected local
Connector endpoint, including a custom loopback Reference port. A different
running game or conflicting existing bootstrap is rejected. Ordinary Host start
and old-save adoption do not gain this owned-preparation route implicitly.

An operator can explicitly change one private text Runtime profile while the
Workbench is closed and port 15527 is free. `developer model --action
initialize-runtime-generation` is the first-install path when the selected
profile's active private file is absent. It requires `--runtime-profile`, an
operator-selected strict `--new-runtime-profile` file, its exact raw SHA256 in
`--expected-new-profile-sha256`, and the matching `--runtime-archive`. It has
no `--expected-active-sha256` or rollback-to-absent operation. The owner checks
the stopped Workbench, lock, recovery state and Runtime port, verifies the
archive, bundled SDK and complete installed generation, then creates the active
name exclusively. A racing active file is never replaced. An unactivated
generation may be explicitly retried only if its installed contents still pass
full verification; unknown or damaged content is not repaired or deleted.
If the selected slot contains a legacy install without an active profile,
initialization rejects it as an unknown recovery state; other slots, existing
model registrations and v1 profile bytes remain unchanged. The profile and
archive are operator-selected inputs, not automatic
kit provenance. Filesystem sync uncertainty reports `BLOCKED` with active-file
readback and a nonzero CLI exit; it does not invent rollback or activation.

`developer model --action
upgrade-runtime-generation` requires `--runtime-profile`, the SHA256 of the
current active profile bytes (`--expected-active-sha256`), an approved strict
profile file (`--new-runtime-profile`) and its raw SHA256
(`--expected-new-profile-sha256`), plus its pinned local archive
(`--runtime-archive`). The owner verifies the archive and a separate installed
generation before archiving the current active profile **as original bytes**
and replacing the one active name. The receipt lists selections sharing that
profile; existing model registrations and manifests are unchanged. The
generation name is the SHA256 of canonical profile JSON under the fixed private
slot, never an operator-supplied path.

`developer model --action rollback-runtime-generation` requires the same
`--runtime-profile` and current `--expected-active-sha256`, plus
`--archived-profile-sha256`. It verifies the archived bytes and their installed
target before switching. A damaged current generation can be rolled back from;
neither operation repairs or deletes an existing generation. Legacy active
profile bytes remain valid and unchanged until an explicit switch. Normal
preparation and offline installation only verify/reuse an active generation.
If replacing the active name is followed by a filesystem sync error, the
command reports `BLOCKED`, nonzero exit, and exact active-profile readback
(`true`, `false`, or `unknown` activation). Inspect that receipt before any
further explicit operation; no automatic rollback occurs.

`LocalModelService.prepare_and_load(selection_id)` accepts only a reviewed registry
selection. It runs existing adapter, artifact, backend and package readiness
checks; missing weights, unsupported hardware or adapter mismatch remain visible.
If the only missing infrastructure is the fixed Runtime package, the existing
hash-pinned Runtime installer prepares it. The existing exact startup and
attestation path then loads the policy in **Human mode**. It never starts a game,
paid compute, training, or an unbounded model download. Inference children set
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`; model weights must already exist.
The current S1 adapter's CUDA requirement is unchanged.

This method starts an asynchronous local operation. Existing `status()` reports
`operation.status`, `preparation_stage` and `readiness`. After fixing a displayed
prerequisite, failed preparation can be retried. An active or unresolved Runtime
must first be stopped/recovered before preparing a different one.

Public-snapshot D-Simple M0 registration now defaults to the explicit
`ready-standard-summary-v1` task, shown as **至结算页**. Its config-v2 and adapter
1.1.0 pin the exact `stpd/public-m0-task-spec-v1` descriptor in both config and
manifest. This is a new Agent program composition using the same Model artifact,
renderer and unchanged neural scorer; it does not retrain or rewrite model bytes.
The catalog exposes `task_spec` and `agent_version` for that selection.

The task consumes only a complete interactive generic Snapshot with its original
ordered action catalog. After the normal public representation/binding checks,
the exact standard `game_over` ready summary (win or loss, summary stage, return
enabled, advance disabled, main-menu/timeline destination and typed public
score/floor/ascension) produces full-length zero placeholders and no selected
index. These zeros are **program abstention**, not neural scores. Existing Runtime
Auto handling of `policy_abstained` returns to Human and releases control; the
task does not choose a return-to-menu action. Intro/animation continues through
the original scorer, and malformed summary facts fail closed. Nonterminal inputs
keep the original scorer and complete catalog.

Low-level `bind_public_m0_export` still defaults to `task=None`; config-v1 and
adapter 1.0.0 keep their original task-free behavior. Explicit
`LocalModelRegistration.register(model_id, task=None)` registers that baseline,
and `status(model_id, task=None)` queries it. Default registration/status selects
the declared summary composition and never adopts or mutates a legacy binding.
The catalog labels a newly registered task-free selection **无任务基线**. Each
selection retains its own config/manifest and can be loaded through the existing
common owner. A task descriptor or green source test does not prove a native
summary journey, decision quality, whole-game coverage or G2/V1 acceptance.

## Recorder-to-model handoff

Before `shadow`, `one_step` or `auto`, Workbench reads exact Runtime status and
uses the game Mod's fixed loopback task bridge at `127.0.0.1:15528`:

1. Read the exact Runtime status, then GET its `/v2/environment`. Require the
   exact `sts2.policy-runtime/environment-1` response with this immutable `run_id`,
   the Runtime owner's fresh Connector `runtime_instance_id`, and a safe integer
   `recovery_epoch`. Capture this epoch **before** any native preparation. A 404
   reports `runtime_upgrade_required_for_model_control`; there is no legacy
   Auto/Shadow/One-Step fallback. Human/Stop remain available for exact old sessions.
2. Read capabilities from the Connector endpoint persisted when this Runtime was
   launched. Its instance must match the Runtime environment observation. A
   non-null cached Runtime environment must also agree. Existing `NativeTasks`
   compares that endpoint with the game Mod's task bridge before closing anything.
3. If recording is not ready for a model, POST `/v1/tasks/prepare-model` once,
   carrying the observed `runtime_instance_id`, `recording_session_id` and a new
   UUID `command_id`. The native Recorder owner closes the actual recording.
4. Continue only with `ready_for_model=true` and consistent `ready`/`closed`
   lifecycle. Recheck the game and Runtime identity, then send the existing exact
   Runtime run ID plus `X-STS2-Game-Instance-ID` and `X-STS2-Recovery-Epoch` on
   every model mode and tick mutation. One-Step mode and its later tick use the
   **same original epoch**; preparation or a late response never refreshes it.

Bridge schema is `sts2.platform/task-status-1`. Pending/failed Close does not
acquire control. A lost POST response is unknown and is never automatically
retried. Human/Stop recovery does not depend on this bridge. The client bypasses
ambient HTTP proxies and refuses redirects; native server policy owns bridge
request validation.

The Connector endpoint is persisted in Workbench's session alongside the exact
Runtime startup identity; it is not guessed from the current project config after
recovery. Older sessions without this field can still be returned to Human or
stopped. They report `runtime_connector_binding_required` and must be stopped and
loaded afresh before model control is available. A missing/malformed endpoint,
unsupported identity contract, mismatched game instance or unavailable Connector
blocks model control without retrying Close or changing mode.

Human/Stop immediately invalidate earlier local control intents. Model mutations
are serialized in Workbench; recovery bypasses that queue so it reaches Runtime
without waiting for an earlier model HTTP response. Shutdown uses the same
recovery path. A late success or unknown response cannot replace a newer recovery
result, and a superseded One-Step never sends its later tick. The Runtime owner additionally
advances a shared recovery epoch when either Workbench or the in-game UI requests
Human/Stop. A different caller's recovery therefore rejects delayed model entry
or a follow-up tick even though Workbench's own generation did not change.
Stop during local preparation cancels a late load.

`RuntimeControlBinding` accepts only the typed game identity and safe epoch; it
cannot override the separate immutable Runtime run header. The Runtime validates
these facts against its own Connector and recovery state before effects. Human
and Stop require neither game nor epoch headers, so missing native preparation
cannot prevent recovery. Known structured owner precondition rejections are
reported as failed non-dispatch; malformed responses and lost POST responses
remain unknown. Neither is automatically retried. After an explicit new start
request, a new environment observation may be obtained. Closing the page or a
background status GET does not create another model intent.

## Finalization without a page request

An independent observer starts after attested Runtime load or explicit recovery.
It observes the exact Runtime's stopped lifecycle, or exit of its locally owned
child process, and runs the existing public `verify_agent_run_evidence` handoff.
It does not send gameplay commands or retry unknown actions. A lost connection
to an unowned/recovered process does not prove termination. Missing, unfinalized
or invalid evidence remains failed. GET status/page reads do not generate reports.

Reports cover bounded Runtime operations. Native victory, complete-run coverage
and autonomous-control coverage remain separate facts. `game_outcome=not_measured`
is not a win, a loss, or an incomplete-game verdict.

## Explicit project sharing

A separate **Share this game-test record** button explains that this finalized
Agent run (model/Runtime identity and recorded observations/actions) will be
shared with approved project members. It calls
`EvaluationSharing.share(evaluation_id, authorized=True)`; Human collection
consent is not reused. This module enables neither automatic sharing nor a
persistent sharing preference. A valid personal member session and owned active
device are required. Account changes before submission cancel it. Logging out
while an already submitted request completes cannot retract submitted bytes;
the exact receipt is retained but hidden from a different session's operation view.

`EvaluationSharing.status()` reports `preparing`, `uploading`, `shared`, `failed`
or `unknown`. Call `close()` on application shutdown. A transport timeout never
causes automatic retry. Explicit retries republish the same immutable content;
Hub deduplicates its manifest and audit event.

The authenticated Hub member write route is
`POST /v1/identity/member/live-evaluations`:

```text
{
  schema: "stpd/agent-evaluation-share-v1",
  device_id: owned active device ID,
  share_authorized: true,
  expected: {
    run_id, manifest_id, policy_manifest_sha256, policy_artifact_sha256,
    runtime_version, runtime_code_sha256
  },
  files: { fixed filename: base64 bytes, ... }
}
```

Exactly six files are accepted: `adapter-attestation.json`, `checksums.sha256`,
`events.jsonl`, `evidence-manifest.json`, `manifest.json`, `policy-manifest.json`.
Decoded evidence is limited to 16 MiB, within the existing 32 MiB HTTP body cap.
Oversize evidence is rejected with a clear error, never truncated. This is a
bounded first sharing path, not streaming arbitrary archives.

`LiveEvaluations(upload_service, identity.membership).publish(principal, body)`
checks current membership/device ownership, decodes only the fixed names and
reruns the public verifier on Hub. It derives counts and report fields rather
than trusting submitted wins. Current authorization is rechecked immediately
before immutable publication. Existing ArtifactStore stores the six payloads
and report as `live_evaluation`; Operations records one `live_evaluation_shared`
event and ConsoleIndex indexes it. Nothing enters Human upload tables, and no
second upload ledger is created.

Response schema is `stpd/shared-evaluation-receipt-v1`: `artifact_id`,
`evidence_content_id`, `status=verified`, and derived `report`. Verified means
**member-submitted Agent evidence integrity and internal consistency**. It does
not independently prove an untrusted member's native gameplay, autonomous victory,
research qualification or Human origin. `native_outcome_status`,
`native_run_completeness` and `game_outcome` remain `not_measured`; scientific and
training admission remain `not_claimed`. Exact Hub producer contributes to the
immutable artifact identity. Local receipts are retained by evaluation ID and
artifact ID, so a future verifier/producer can derive a new report without
overwriting prior receipts.

## Integration and tests

Workbench routes retain local Origin/CSRF protection, strict body shapes and
personal-session checks. Construct one sharing service from existing models and
member client; wire prepare/load to the new method, GET share status and explicit
POST share. Existing catalog/status/command routes remain usable. Hub's member
router retains authentication and browser Origin/CSRF, then delegates publication.
Never put mutations into GET or page rendering.

Focused regressions cover actual verifier bytes, exact native Close binding,
pending/unknown transitions, two-client recovery during native preparation or after
One-Step mode, strict shared-epoch headers, old-runtime model-entry rejection with
Human/Stop retained, background finalization, missing
model prerequisites, explicit sharing, member/device revocation including during
verification, invalid bytes/paths/limits, account changes, double clicks, lost
responses and idempotent manual recovery. Portable synthetic tests do not replace
native load, real Human Close or genuine supported-model game operation evidence.


## Stage 1a token models (source implementation, native qualification pending)

The shipped S1 catalog and its CUDA checks retain their original meaning. The
additive trusted adapter `token-v1` runs `python -m stpd.policy.token_port` with a
pinned configuration and public Policy Manifest. It supports the existing public
snapshot exports, CPU/MPS according to the saved model configuration, and a local
pinned Qwen snapshot only for PF. Preparation never downloads a model implicitly.

The optional operator-owned `python/.local/token-policies-v1.json` has schema
`stpd/local-token-policies-v1` and a `policies` array. Entries have exactly `id`,
`label`, `adapter` (`token-v1` only), `manifest`, and `config`; the last two are paths
inside the Python root. They merge into the existing catalog, reject duplicate IDs,
and cannot override a command or the pinned Runtime package. Merely adding an entry
does not load it or authorize gameplay. Raw models and machine paths stay private.

`stpd/token-policy-config-v1` pins the absolute `export_path`, `export_manifest_sha256`,
`model_id` and nullable `qwen_snapshot`. Its own file digest/path/schema are pinned by
`adapter_config.stage1a.config`. The public artifact references the export envelope;
that immutable envelope binds model/weights/tokenizer identities, verified at loading.
`representation` must match the saved public serializer. Semantic-execution exports
are rejected. No extra Reads, filtered catalogs or native operands are introduced.

The adapter code scope `python-owner-source-and-lock-v1` conservatively hashes all
Python sources under `stpd/` and `spireagent/`, `uv.lock` and the existing Qwen pin.
This is a broad reproducibility pin, not a minimal import closure or a new component
semantic identity. Code changes require explicit manifest regeneration; documentation,
private exports and registry edits do not. S1's existing code digest remains unchanged.

Before live registration, use the current qualified environment identity and explicitly
review supported interactions/verbs; never copy an old environment pin just to load.
Runtime remains responsible for exact environment compatibility, complete public
schema admission, timeouts, controller acquisition and delivery. The token process
checks action-ID order/count and manifest equality, then returns finite scores and an
index. Unexpected requests fail without a partial decision. Workbench continues to
load in Human mode and use the existing Recorder handoff and Stop lifecycle.

## Unified workbench target and restart repair

The [root UI specification](../../docs/UI_INTERACTION_SPEC.md) owns the accepted
unified local/cloud/in-game workbench target: all functions and login accessible
from the Mod by default, optional external presentations, independent background
services, and one account/task/data system. The current Mod is not yet this full
workbench. Cloud-backed tasks remain in the same user workflow.

Runtime port readiness now distinguishes a live listener from closed-connection
TIME_WAIT using platform-appropriate bind semantics, without REUSEPORT or killing
unrelated processes. Runtime startup/attestation remains authoritative if another
process races the check. Operator-local model catalogs must not leak into test
fixtures; tests read the shipped registry explicitly before adding synthetic entries.

After an application/package upgrade, explicit **结束测试** can retire an older
session using its sealed Agent-run evidence even when its model registration or
Runtime package is no longer current. This requires exact persisted startup
identity, a successful owning Evidence verification, a terminal `stopped` event,
and a free Runtime port. The old session is archived by content hash and its
original taint retained; a new evaluation is separate from prior reports. Missing,
unsealed, mismatched or tampered evidence and an occupied port cannot use this
path. It sends no gameplay request, performs no automatic action retry, and does
not turn port absence into proof of past delivery. Normal live recovery retains
its exact-runtime checks. Successful retirement permits a new explicit load.

## Common recorded Agent task outcome

Local generic and native handoffs add the versioned application DTO
`agent_task_outcome.schema=spireagent/agent-task-outcome-v1`. Their existing
outer schemas, scalar fields, immutable evaluation IDs and evidence scopes stay
compatible. New derived reports have new content-based IDs; old evaluations are
not rewritten. The canonical executable shape is
`spireagent.workbench.agent_task_outcome.AgentTaskOutcome`.

The DTO keeps independent facts:

| Axis | Values and meaning |
| --- | --- |
| `task_completion.status` | `completed`: exact supported original Task/Agent composition, qualified endpoint witness and declared program end join. `unproved`: known Task and bound input, but no qualifying completion witness. `unknown`: Task/composition/input binding unavailable or conflicting. Neither unproved nor unknown asserts that the game never completed. |
| `operation_end.status` | `task_ended`, `censored`, `failed`, or `unknown`, projected from recorded program end, stop/budget, fail-closed and uncertainty facts. A proved summary followed by cleanup failure retains completed Task and failed operation end. |
| `terminal_observation` | Original public win/loss, conflicting observations, absence from available inputs, or unavailable input. Ready summary is a separate predicate. Receipt, HP, a later Current and process exit are not substitutes. |
| `agent_composition` | Original manifest identity/hash, Agent or policy, adapter, runtime-declared Model artifact reference, input/profile and exact Task/program metadata when bound. These refs do not carry private artifact paths or grant use/control. |
| `evidence` | Owning verifier status/content identity and original events file identity. Integrity/internal consistency is not native-world authenticity or scientific qualification. |
| `strategy_quality` | Always `not_assessed`; recorded endpoint/loss and mechanical Task completion do not assess gameplay strategy. |

Public M2 Port4 records full qualified public input and exact decision/control
association. Its supported ready-summary Task is read from the sealed Policy
Manifest and uses the existing domain predicate; matching declared program-zero
abstention proves recorded mechanical completion. Terminal win/loss remains an
observation and does not prove numerical W behavior. Public M0 Port1 has a closed
legacy mechanical Goal mapping for the sealed `stpd-public-m0-decision-adapter`,
`decision-only-ndjson-1` and exact owning `ready-standard-summary-v1` TaskSpec.
After the existing typed Evidence verifier and bounded immutable-file checks,
the application may join a prior sealed Snapshot1 `successor` or
`receipt.successor` to a later original decision. The body must be complete,
interactive and public under the existing input validator, from the same recorded
Runtime/environment and exact Snapshot ID. All prior bodies for that ID must
agree except their top-level observation time; future observations never supply
the join. The independently derived complete ordered candidate digest and count
must match the full zero-placeholder score vector, null selection/resolved action
and abstain disposition. The owning ready-summary predicate must hold.

Mechanical completion additionally requires the exact `policy_abstained` handoff
and its terminal Stop tail. Only controller release may precede that handoff;
after it, controller release and recorded cleanup failure/taint may precede Stop.
Later decisions, modes, submissions or other substantive activity invalidate the
earlier end join. Recorded failure/uncertainty/budget precedence remains separate
in `operation_end`; completed Task followed by failed cleanup stays completed
Task and failed operation. Qualified original terminal win/loss observation is
independent of whether this declared Task/end join succeeds.

The witness basis is `prior_sealed_public_observation`, not consumed input,
Port1 input instrumentation, numerical inference/history or native causal proof.
Missing, future-only, conflicting, foreign, incomplete, unsupported or mismatched
facts remain unknown/unproved. Ordinary M0 abstention/Stop without the original
witness is never promoted to completion. A new application projection receives
its own content-based evaluation ID; prior evaluations and sealed inputs remain
immutable. No live Current, replay, extra witness writer or caller backfill is used.
Existing text terminal observations retain their original meaning and do not
invent a Task descriptor.

Native sampled-current completion requires the original stored observation,
accepted consume/ACK, matching Close watermark and supported original Task.
Terminal win/loss observation uses the acknowledged original body independently
of Close, so Await, ordinary abstention or an operator Stop can retain an observed
terminal result without proving Task completion. Stop before a completed Next
retains the owning verifier's recorded uncertainty in the operation axis.
Native Task metadata is read only when existing registered owner metadata and
package bytes match the sealed Agent artifact and Agent/InputSpec identities;
no current/default Task or version-only lookup is substituted. Legacy native
full-reference runs retain capture references/ACKs but no sealed original body;
completion and terminal facts remain unknown where that body is missing. No new
Runtime witness, backfill, current-game lookup, NN loader or weight read occurs.

Interpreted files are rechecked against the owning verifier's accepted inventory.
Projection currently bounds events to 64 MiB, one native observation to 16 MiB and
package metadata to 1 MiB; exceeding that application projection envelope reports
an explicit unknown reason instead of fabricating or truncating a witness.
Terminal references retain the latest original witness for each result/readiness
combination; repeated screens do not enlarge the DTO or conceal conflicting results.
Actor/Human classification, data/training admission, legality, native delivery,
causal successor and research qualification remain with their existing owners.
An original `fail_closed` or `runtime_tainted` event establishes a recorded
operation failure and retains its reason. This does not settle native delivery,
the successor, or uncertain cleanup. Taint/pending/unknown control without a
corresponding failure event remains unknown. Recorded stopped-budget facts are
retained in the independent operation axis.
Task/package/body projection errors leave recorded operation facts available
when the original events still match the accepted inventory. Changed events
cannot support any axis. The generic handoff freezes and hash-checks one events
byte sequence for both the common DTO and existing scalar counters/observations,
so reopening a changed file cannot mix foreign facts with the old evidence ID.
A reasonless final Stop retains the exact immediately preceding operative
`handoff_to_human` reason and event sequence; only controller-release cleanup may
intervene. Controller and budget facts still come from the final Stop.
A later mode, decision, submission, consumption, directive or unknown
substantive event blocks reuse of an older handoff. Explicit Stop reasons and
existing failure, uncertainty and budget precedence remain unchanged; the reason
does not prove Task completion or a terminal game result.
