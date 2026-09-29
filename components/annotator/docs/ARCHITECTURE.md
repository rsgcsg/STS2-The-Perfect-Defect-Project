# Architecture

## Ownership

```text
STS2 native UI and action queue  game truth and accepted action
Native Foundation               typed read-only semantic S, A_sem(S), lifecycle facts
STS2-Connector                  fair-player projection, A_public, exact Host bindings/delivery
Human Annotator                 correlation, causal boundary observation, raw evidence
STPD                            research projection and dataset admission
```

The current production Human-action path is:

```text
Human UI scope captures H and exact public BoundAction binding
-> STS2 accepts one exact native root
-> Native Foundation captures S and typed A_sem(S) at the exact native action-binding boundary
   (pre-admission for source-local callbacks, or BeforeActionExecuted for GameAction roots)
-> typed native lifecycle proves Commit
-> SemanticBoundaryTracker proves a separate causal successor boundary
-> one canonical projection writes canonical evidence; optional compatibility adapters follow
```

`SemanticBoundaryTracker` never publishes or executes actions. Human H is not
silently promoted to semantic S. It is the sole current authority for Human-root
causal order and successor settlement, not for semantic legality. The native
decision observation and fair-player state frame are related but distinct facts.
The Native Foundation provider captures the STS2-owned `S` and `A_sem(S)` at the
exact native action-binding boundary; the Connector separately supplies the
fair-player frame used for Human/public evidence. A source-local callback can
carry the typed native observation forward through admission, while a
`GameAction` root uses the exact `BeforeActionExecuted` boundary when no earlier
sidecar exists.
Connector's public catalog may already be empty because input is settling; that
does not erase the independently observed semantic action space and does not
make the frame publicly interactive. An arbitrary settling poll cannot prove a
causal successor. `GameAction.Finished` is lifecycle/Commit evidence, not
universal `S'`.

Acceptance sequence is evidence order, not causal execution order. Every action
binds S only at its real execution boundary, so a later accepted source-local
choice executing first naturally consumes the then-current S. The same complete
state captured before the following queued action both closes the prior causal
edge and becomes that action's S. There is no separate rebind exception. An
incomplete state capture remains unknown and cannot be repaired from a later
frame.

The observer uses Harmony Prefix/Finalizer only to establish a thread-local UI
scope and a Postfix to observe an action already accepted by STS2. It does not
skip a method, alter an argument/result, transpile game code, or enqueue an
action. Connector-origin actions call a different native entry path and do not
enter the human UI scope.

Starting a native card play removes its holder from the hand. The observer
captures H before `NPlayerHand.StartCardPlay`, carries that exact invocation to
the Mouse/Controller CardPlay factory, and binds the returned native object only
when its holder matches. TryPlayCard consumes its own binding; native Cleanup
forgets that owner. Session/lifecycle invalidation changes the binding generation.
No wall-clock expiry or global latest card frame grants correlation. Execution
still captures S and independently proves exact-once membership in A(S).

## Exact Mapping

The frozen Connector frame holds strong process-local references only for
referents and private owner/operand bindings in the frozen BoundAction catalog. A native card and target match by
`ReferenceEquals`; verb, subject, argument roles, and argument count must also
match. Native witness IDs are opaque recording evidence and cannot resolve back
to game objects.

## Authority

The observer Modset canary identifies exact provenance but does not enable
Connector mutation. Action publication still comes from current native UI
readiness; accepted human action still comes from STS2; correlation creates no
legality. An external Connector controller blocks recording.

## Lifecycle

Runtime initialization stops at `Ready`; it does not create evidence or bind a
session. `RecordingService` owns the application contract:

```text
Ready -> StartNewSession -> Recording <-> Paused -> Closing -> Closed
  ^                                                            |
  +--------------------- StartNewSession -----------------------+
```

Every session receives a new session ID, timeline, store, counters and run
sequence. Pause blocks new witness scopes while already admitted work settles.
Close blocks new scopes, immediately disposes any still-unproved final root as
`session_closed_before_successor_boundary`, then durably flushes and disposes
the RunJournal/evidence streams. Close does not wait for a semantic drain and
never captures or promotes a replacement `S'`. Audit/pack/verify/store/transfer
remain offline Evidence operations.

Automatic terminal Close waits for the exact native Game Over Continue-ready
callback to finish its synchronous boundary persistence. OnEnded records native
terminal lifecycle only; intervening process frames cannot seal a normal
victory/defeat session. Explicit cleanup, manual Close and a later native Launch
still close an unready segment with unknown successor. No timer or frame poll
supplies readiness.

The frame loop is not observation authority. It performs no Player Environment
capture while an idle recording has no explicit status or Close work. Canonical
families capture one complete boundary at the mutation edge; that same frame
settles the predecessor and becomes the next action's pre-frame. Native
lifecycle callbacks carry identity and terminal facts without rebuilding a
Snapshot. The remaining schema-3-only Full-Run adapters may request a bounded
boundary until they migrate; they cannot authorize canonical rows.

Evidence streams append and flush to the OS on the native callback path so write
errors remain immediate, but they do not fsync each lifecycle fact. Close is the
durability boundary: it blocks new witnesses, drains causal work, writes the
derived coverage summary, then `Flush(true)` seals every stream before Closed is
published. An interrupted session is partial inspectable evidence, not a durable
Human evidence seal.

Every exact-correlated native root enters `SemanticBoundaryTracker` at
`GameAction.OnEnqueued`, after STS2 assigns its queue ID and before queue
notification or execution. The observer subscribes to the action's game-owned
started, PlayerChoice pause/resume, cancelled and finished events without
changing the action. A later root may be accepted and tracked without blocking
Human input. Its exact pre-execution frame may settle only the immediately
preceding committed root and simultaneously becomes that later root's own S;
proof never crosses the later Human effect.

STS2 `GameAction.PauseForPlayerChoice` is a typed continuation seam, not a
second Human root and not a completed parent. The exact
`BeforePausedForPlayerChoice` lifecycle callback identifies the still-running
parent; it permits the immediately nested choice's pre-execution boundary to
settle `S0 + parent -> S_choice`. The later
`BeforeReadyToResumeAfterPlayerChoice`/resume callback references that same
parent and is lifecycle evidence only: it cannot rebind semantic pre-state or
self-settle `P -> P`. The generated-card select/skip callbacks are narrow
STS2 source-local mutation seams. When Connector's exact pre-choice frame is
complete they enter the normal direct-UI root path; when it is not, their
accepted mutation is durably retained as a fail-closed occurrence with exact
subject, choice owner and paused-parent lineage rather than silently dropped.
Neither path uses screen visibility, timing, polling, queue-idle or a later
frame as semantic proof.

Cancellation, runtime drift, lifecycle persistence uncertainty, root-contract
error, mapping failure, incomplete semantic action space or missing successor
is fail-closed. Schema-4 events reference exact content-addressed H/S/S' frames
and, when present, one exact execution semantic action-space object. Historical
schema-1/2/3 rows and native ledgers remain readable only through explicit
archival readers; they are not current mutable admission or causal authorities.
Persistence failure disables the modern trace for the session, surfaces
`semantic_boundary_trace_unavailable`, and never retries or invents a later
boundary.
Audit rejects a proved transition whose semantic pre does not match its complete
pre-execution boundary, or whose causal window contains another Human action
start after A begins and before S'.

The current combat implementation uses existing typed `GameAction` lifecycle
events and carries an admission-boundary semantic sidecar through staging when
available; otherwise it captures at `ActionExecutor.BeforeActionExecuted`. One exact-build read-only
Prefix observes `NCardPlayQueue.RemoveCardFromQueueForCancellation(PlayCardAction)`
because the pile-missing execution branch returns with native state `Finished`
without spending resources or running `OnPlay`; this is classified as
`not_a_successful_action`. No scheduler, argument, result or gameplay behavior
is changed.

Full-Run expansion does not add a surface switch to the causal tracker. Map
selection enters the same lifecycle path through STS2's
`VoteForMapCoordAction`. Reward claim/proceed and card-reward selection have no
equivalent root `GameAction`, so narrow source-local Prefix/Postfix observers
record their exact native UI delivery while Connector still supplies the frozen
complete BoundAction. These observers neither await business reward completion
nor create legality. Semantic state Reads are selected by interaction kind and
remain information completeness only; they cannot publish or authorize an
action.

## Canonical Sequential Collection Decision

Exact runtime evidence shows that native UI staging removes PlayCard,
UsePotion and EndTurn from the public catalog before execution even though the
STS2-owned semantic decision still contains them. Current canonical evidence
therefore binds execution S to a content-addressed typed `A_sem(S)` observation
for those families, joined to the exact Human `BoundActionId` captured at the
native binding boundary. Direct UI domains may use their exact complete public
execution catalog when that catalog itself is the typed delivery surface.
The current Decision record separately preserves the frozen Human-time public
frame for durable compatibility. The projector only joins already-proved facts and cannot settle
the root or manufacture action-space membership. ADR 0003 remains a withdrawn
serialized-input candidate, not current authority.

The application event stream is typed, process-local and bounded. A consumer
queries current status, then requests events after sequence N. A gap means the
consumer must query status again. Application events are operational state, not
Human evidence and not action authority.

## Decision occurrences

The [decision identity ADR](../../../docs/adr/0006-decision-occurrences-within-causal-roots.md)
adds first-class nested decisions to the same tracker and canonical stream.
The screen/hand factory inherits an exact native parent binding; each Human
input observes its own complete pre-state and native dispatch. Inputs inside
that selector share the opening parent's causal lineage and do not invalidate
it as external Human effects. An actual unrelated next Human effect still does.
The enclosing native Task/action remains subscribed through its real finish.

A frozen process-local Connector query resolves exact owner, operation and
operand; it cannot execute or publish an action. Post-input state is a successor
only while the same native selector owns a complete catalog. Terminal selection
and native Task completion alone do not provide S'. Automatic revalidation and
selection completion never establish Human origin.

Run observation provenance is independent of run activity: a RunState poll may
establish an observed run, and a subsequent native Launch records its start
without creating another run ID. Polling never manufactures native start/end.

Reward claim/proceed inputs opened by an exact event async owner use the same
nested decision lineage as card selectors. The native NRewardsScreen factory
binds its actual owner, and each input's frozen state hands off through the sole
tracker before its own admission. Ordinary combat rewards remain independent
roots. A rest Proceed callback when the native map is already open follows the
native no-op branch and creates no accepted Human mutation or failure barrier.

Reward-screen ShowScreen returns after native Push, input setup and active-screen
update. Its exact async-bound parent receives that owner-ready boundary before
an independent potion input can intervene. A synchronously opened screen is
observed on the same outer callback return after parent admission, only if its
registered parent matches that callback exactly. Neither path waits for the
first reward click, reassigns a potion input as a child, or repairs an old unknown.

A state-complete combat H captured at the exact CardPlay factory remains the
Human observation even if public delivery is settling and combat ends before
release. Exact owner, generation, card, operand mapping, identity and Reads
still gate admission. That H never supplies execution S or legality. Native
queued cancellation remains cancellation; late cancellation/abort cannot
append another disposition after a durable unknown or restore an old state.

For the synchronous terminal-reward/treasure and Event Proceed branches, native completion
and closed-to-open map ownership are observed at the same native return. The
existing exact completion binding admits only its own Human scope; the shared
owner-ready provider independently exposes the current map owner. Connector
must supply a complete map frame at that seam. The EventOption's native
`IsProceed` selection is frozen at ingress and supplies `event_option.proceed`
even though the public delivery verb is `activate`. Each observer names its
actual native mechanism. Async event return, already-open map, missing binding
or incomplete state remains on the existing fail-closed path. A completed Task
alone never supplies successor state, and Close never captures a replacement
boundary. Closing the map for presentation cannot erase an already proved boundary.
