# Platform Live UI

The in-game **Platform** button opens two surfaces: **Human Recorder** and
**Agent Run**. There is no `K` shortcut. Close returns to the small launcher;
`Esc` closes the normal workspace. Drag the header to move the workspace and
use its corner to resize the normal view.

Both surfaces have a **Minimize** control and a separate, genuinely smaller
440 × 174 view. The arrow restores the normal workspace and its previous size.
The compact Recorder shows only **已录入 / 真实失败 / 最新 3 条**. Its totals come
from Annotator's session counters, not the retained UI list:

- 已录入 is the durable canonical decision count.
- 真实失败 is Annotator's versioned in-scope failure disposition total. Unknown
  older counters display `—`; normal cancellation, abort, diagnostic
  invalidation and unsupported/non-decision dispositions do not inflate it.
- The latest three rows omit native diagnostics and unsupported/non-decisions.
  They retain the owning decision status; pending and cancelled are distinct.

The normal Recorder contains lifecycle controls, session totals, a paginated
retained decision list and details revealed by selecting a row. Exact parent
and causal-root lineage, selector metadata, action and state identities remain
available in those details. Missing metadata stays unavailable. Canonical
recording does not itself establish Full-Run qualification or research admission.

The Recorder now offers **兼容语义录制** and **原生交互与观察**. The latter selects
the explicit `native-logical-source-v3` profile. Select a source declaration
(本人操作 / AI 界面操作 / Agent 协议 / 未知来源) and enter an operator ID before
Start; no Human source is selected automatically. IDs use 1–128 ASCII letters,
digits, `_`, `-` or `.`, excluding `.` and `..` alone. Pause before changing the
source, then Resume. ChangeSource carries the observed exact session and source
segment to the Recorder owner. Close remains Closing until the owner finishes
the original accounting and durable drain; a fresh session can then start.

Source views show public observations, inputs, pending inputs, gaps and accounting
health. These are separate from canonical Human decision counters. Source kind
is the operator's declaration with `machine_verifiable=false`; it does not prove
Human origin, physical coverage, Commit, causal successor or research admission.
Legacy controls retain their existing capture format. Source3 entry is a source
candidate until its exact package/native runtime canaries pass.

Direct native controls and the local TaskBridge share typed command composition.
The bridge and async model preparation use the existing bounded native queue;
queued cancellation can prove non-dispatch, while uncertain started work is never
automatically retried. The browser and paired native Workbench Data page use the same application methods
and fixed lifecycle API. Start/Change require an explicit source and actor; neither
form defaults to Human. Refresh never retries an unknown write. Explicit Close and
then a deliberate fresh session after durable Closed retain the earlier uncertainty
notice. A fresh runtime/session isolates the presentation fence; it does not prove
the prior request. Starting or changing away from Agent Protocol while a known
Workbench model is active requires explicit Human/Stop recovery first.

Model preparation uses `/v2/tasks/status` (`task-status-2`) and
`/v2/tasks/prepare-model` (`task-model-request-2` / `task-model-result-2`). Only a
healthy Recording Source3 declared `agent_protocol` may remain Recording with a
fresh compatible `agent-session-status-1` / `native-logical-v1` context. Paused
Agent Protocol remains paused and blocks preparation until explicit Resume/Close;
accounting errors block readiness. Other supported profiles/kinds or known legacy
model input use owner Close and wait for durable completion. The original v1
Ready/Closed readiness invariant is unchanged. Context is caller-provided
compatibility metadata, never authentication, model Consume or Human proof. Runtime
Mode/Tick still carry the original run/game/recovery epoch; preparation cannot grant
a newer epoch to a stale intent.

Agent Run uses only typed Policy Runtime controls. Its compact view keeps mode,
controller, policy, last selected action, Receipt and **Return to Human** visible.
It never resolves or submits a gameplay action directly. The Runtime loopback
defaults to `http://127.0.0.1:15527`; modes and model status require a compatible
Policy Runtime with an exact Policy Manifest and artifact.

## Unconfirmed model commands

Model mode and tick POSTs are sent once. A timeout, lost response, malformed
success, or unrecognized error leaves that Runtime run in an explicit
unconfirmed state. Status polling can describe what is currently visible but
cannot erase the uncertainty or re-enable model actions. **暂停并接管** and
**结束测试** remain available with the original exact Runtime run ID, even during
an unavailable status poll. Status reads have a 900 ms timeout; commands have a
45 s budget matching the Workbench and the Runtime's bounded model wait. Human
and Stop are submitted promptly without waiting for an earlier model HTTP
response; Runtime's owner controls effect ordering and recovery fencing. A late
old response cannot send a follow-up tick, overwrite the newer UI intent, or
re-lock a run after confirmed recovery. While the owner is still completing an
in-flight action, the UI honestly remains in recovering state. A confirmed
Human/Stop response clears that UI fence;
a failed recovery does not. An independently loaded Runtime has a different run
identity and does not inherit another run's UI uncertainty. Native unknown
delivery remains governed by Runtime's own taint and is never made retryable by
this presentation state.

Only strict, recognized owner precondition errors establish non-dispatch. The
client does not follow redirects or use ambient proxies for local commands.
Read-only preparation failure does not masquerade as a submitted operation, and
a cancelled local preparation cannot subsequently Close a new Human recording.

## Retention and display cost

The closed workspace performs no status polling. An open Recorder queries only
the typed Annotator status/event service once per second; it does not request a
Connector Snapshot or action catalog. Agent Run requests its typed live status
only while visible. Loaded assembly identity is immutable and cached once.
The UI retains at most 512 action rows; ordered event sequence is the replay
cursor. A reconnect gap refreshes the available batch and labels partial
history. Authoritative session totals are independent of this retention.
Minimized mode does not rebuild the hidden normal decision widgets.

Layout version 5 stores position, normal size, compact mode and selected surface
in local application data. This presentation-only state is fail-soft; it
contains no Human evidence, native operands, model weights or secrets. Old UI
layout files are not migrated because no evidence or consumer contract depends
on their geometry. See the canonical
[UI and interaction specification](../../docs/UI_INTERACTION_SPEC.md).

## Initial scene attachment

Initialization schedules one main-thread deferred mount, then verifies that the
layer and panel entered the captured SceneTree before preparing the panel. The
ready log is published only after preparation succeeds. Root exit cancels an
already queued callback; duplicate initialization/callbacks cannot mount twice.
Attachment or preparation failure disposes the panel and layer once, reports the
failure, and does not retry automatically. Portable lifecycle fixtures cover this
ordering; the exact candidate still needs a cold-start, busy-tree, shutdown and
open-panel runtime canary before the mount is qualified.

## Ownership and validation

This component consumes typed Connector, Policy Runtime and Annotator services.
It owns neither gameplay/recording authority nor packaging, deployment, loaded
identity or rollback. `apps/game-mod` compiles it into the single production
`STS2_PLATFORM` Mod. Portable boundary and event-projection tests run with:

```bash
npm run live-ui:check
```

Use [`apps/game-mod/README.md`](../game-mod/README.md) for the supported exact
build/install/cold-load/rollback path. Source tests and compilation do not prove
rendering, Human origin, policy operation or Full-Run qualification. Final UI
canary checks launcher, both compact views, drag/restore and Recorder counters
against the exact candidate's session audit.
