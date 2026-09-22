# Local model tasks and explicit shared reports

This is the owning orchestration guide for `workbench.local_models`,
`workbench.native_tasks`, `workbench.evaluation_sharing`, and
`hub.live_evaluations`. Root workflow and existing Policy/Evidence contracts
remain authoritative. This flow does not make arbitrary downloaded checkpoints
executable and does not qualify Human data or model game performance.

## Prepare and load

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
