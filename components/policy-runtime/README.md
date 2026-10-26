# Policy Runtime

The Policy Runtime is a model-neutral Connector or Managed Host consumer. It owns controller
lifecycle, Human/Shadow/One-Step/Auto modes, stale refresh, delivery safety,
stable-successor polling, and Agent-run evidence. It does not own game legality,
model inference, or native operands.

`--listen-port 0` asks the OS for an available loopback port. The startup line
reports the actual address; callers must use that address. The default remains
15527. This lets isolated evaluation workers use the same CLI without racing to
reserve a port in a separate process.

The manual P1 chooser (`examples/p1/human-chooser.mjs`, Agent version 4) starts
each choice with the complete action list. The person consults the real game
screen; `help` lists the retained public-observation browsing commands and
`overview` opens the former detailed home view. Action order, numeric bindings,
IDs, Agent observations and submission semantics are unchanged. Recordings made
this way use the game screen and optional P1 browsing information; they do not
establish that the person saw only P1 observations or independent Human origin.

An explicit `sts2.policy-runtime/agent-manifest-1` selects the additive
`native-logical-v1` Agent branch through the same `--manifest` CLI and HTTP service.
It uses the actual Connector SDK, atomic Attach/source Events, complete catalog
assembly or declared scoped queries, strict consume/acknowledgement watermarks,
and the shared controller/epoch/budget/Stop owner. It does not reuse text-menu
candidate indices. See the [Agent session contract](docs/AGENT_SESSION_PROTOCOL.md)
for current implementation limits, exact pending-request reconciliation and
source/test evidence boundaries.

A sampled-current Agent may explicitly select the closed `execution_policy`
in the Agent session contract. This enables `current_owned` reader lifetimes and
bounded new decisions after the exact `not_started / stale_snapshot_or_binding`
result. An unchanged NativeUnit only waits; a new decision requires a changed
unit, an advanced ConsumeACK, and a completed Next in the same accepted prefix.
The original request/action/basis is never replayed. Unknown/partial delivery,
source loss, Human/Stop, controller or budget loss still ends the path.

Native sampled Agents may explicitly select the additive known-stale
`agent-execution-policy-3`: a fixed 10,000 ms per-episode monotonic window under
the original global wallet, with unchanged full Current/C, fresh changed-unit
selection and unknown-delivery fences. The existing lifecycle owner expires an
idle/blocked episode itself; no count-based ceiling, new sleep, legality or replay
is introduced by that selection. Policies1/2, generic/text behavior and historical
artifacts retain their contracts. See the [closed policy3 contract](docs/AGENT_SESSION_PROTOCOL.md#explicit-native-known-stale-recovery-policy3).

The native port is exported from the package main entry. Pass the validated
manifest policy explicitly; omission keeps the legacy five-field Next grammar.

```ts
import { NdjsonAgentSessionPort } from "@rsgcsg/sts2-policy-runtime";

const port = new NdjsonAgentSessionPort(
  child, manifest.adapter, manifest.limits, undefined, manifest.execution_policy
);
// Or: NdjsonAgentSessionPort.spawn(command, args, manifest.adapter,
//   manifest.limits, { executionPolicy: manifest.execution_policy });
```

The trusted Agent caller may repeat `--adapter-env-key NAME` to forward selected
environment variables through the CLI, matching the process API's explicit
`env` option. Only names appear in arguments; values stay in the process
environment and are absent from startup/evidence. The default limited inherited
environment is unchanged. Invalid or unavailable names fail before opening the
manifest. Manifests cannot choose executable code or environment credentials.

The Connector supplies one complete ordered `BoundAction` catalog. A policy
adapter receives that exact Snapshot and Read bundle, echoes the catalog digest,
returns one score per candidate and an optional selected index, and never returns
an action object. The Runtime resolves the index against the same current catalog
and submits only that Connector-owned `bound_action_id`.

An explicit Manifest with `representation.input_schema` set to
`sts2.player-environment/text-menu-snapshot-2` selects Connector
`input_profile=text-menu-v2`, zero Reads, the complete ordered menu action IDs,
and the existing `sha256-json-menu-action-id-order` digest. It requires the
stateful `decision-only-ndjson-2` port and exact completion watermark.
`system_navigation` and `system_selection` change text menu state; only a
`native_input` leaf can report native delivery. The v2 Agent Run requires a
matching Evidence verifier before its record can be qualified.

In Auto, an exact v2 `not_applied` result with `reason_code=stale_snapshot`,
`retry=reobserve` and `native_delivery=null` may continue only with an Agent
Evidence writer, after that result is durably written and the controller is
released. Without a writer it hands back to Human. The next tick obtains a
fresh observation and complete menu (an atomic context for the required
stateful v2 port), asks the policy again, and uses new decision and request
IDs. It never reuses the old bound action, request or scores; a fresh policy
decision may choose the same semantic action.
For generic Snapshot1 and the retryable text-menu-v2 path, the first correlated,
explicitly retryable known stale result anchors a per-episode absolute monotonic
10,000 ms recovery window before Evidence or controller-release latency. Each
retry releases control, waits a positive adaptive cooldown of 250, 500, then
1,000 ms (capped at 1,000 ms and clamped to remaining local and global time),
and obtains a fresh complete observation, fresh scores and new decision/request
IDs. A tick before cooldown eligibility returns `not_admitted` with reason
`stale_submission_cooldown` and performs no environment or policy work.

New observations, changing fair-player facts, catalogs, scores and tokens never
renew the window. Only an accepted delivered/applied result in the original live
recovery epoch and authorization, before expiry wins, resets the episode; an
applied text menu navigation counts as progress at this layer. A genuinely new
Auto authorization starts a new allowance. Redundant Auto commands and cycling
between autonomous modes within one active authorization do not refill it.
The existing total time, policy-call and submission wallets remain unchanged,
and every fresh attempt is charged normally. Generic Snapshot1 retains its
optional Evidence writer compatibility; v2 still requires a writer for retry.

The shared Runtime lifecycle owner schedules the earlier global or local
absolute deadline. Local expiry itself fences and cancels pending policy work,
serializes Human/controller release and records `handoff_to_human` with exactly
`{reason: "stale_recovery_exhausted"}`, even while idle without another tick or
status read. Global expiry wins equality. Admission checks enforce expiry before
environment work, after observation/policy awaits and before acquisition/submit.
An already submitted request is still owed its actual correlated result and
Evidence classification before final release; a late delivered result cannot
revive Auto or reset an expired window. Failed handoff Evidence writes taint.
Unknown/partial delivery, correlation failure, nonretryable rejection, failed
Evidence/release, Stop, Human recovery and identity/epoch drift retain their
strict handoff/taint behavior. Delivery-successor wait exhaustion never retries.

This finite allowance is an operational cost/scope choice, not proof of native
readiness, effect settlement, eventual delivery or eventual game summary. It
replaces the generic/v2 three-stale cutoff; the native-logical Agent's separately
declared 8/3 policy and global timer remain unchanged. It needs a newly qualified
Runtime package before live use; historical runs and artifacts remain immutable.

Before observation, each Connector Manifest must exactly pin the Connector environment:
host kind, Connector version/source revision/artifact SHA-256/module version ID,
Modset status/fingerprint, and the complete ordered list of loaded Mod IDs. Any
field drift fails closed before Snapshot observation or policy scoring.

A separate, explicit `managed_text_v2` Manifest branch supports the same v2
snapshot, complete menu, port-3 confirmed-interaction, configured authorization, and
Agent-run evidence without claiming Connector MVID, Modset, or HTTP lease
facts. Its model requirements declare text protocol/profile and game support;
they do not contain a Host package, service, game continuity ID, or seed. A
separately sealed `managed-environment-binding-1` selects one trusted local
Host package and candidate build. The operator application verifies its private
profile and installed package before writing this public binding. Runtime
checks the binding against the attached Host service's declared identity and
the explicit selected service/runtime/game-continuity target, then records the
binding and actual admission in the Agent run. Neither Runtime nor Evidence
independently re-attests Host archive bytes from the service's JSON identity.
An installed Host truthfully reports `source_revision` and
`component_tree_revision` as null; the binding retains the application-verified
release provenance. The Host's source digest uses a different file inventory
from the application package-content digest and is recorded separately.
The binding's `profile_sha256` is an opaque pointer to the private profile,
not proof that Evidence saw those profile bytes.

The Host owns the one Managed game process and the only native stdin consumer.
Runtime attaches as a client, observes the Host's complete v2 menu, and uses
the Host's claim, text-submit, and release confirmations. A stale selected
episode cannot become a new run target. Stop or Human handoff releases Runtime's
claim but does not close the Host or end its game. A lost or damaged claim or
release confirmation leaves controller ownership unknown and the run tainted;
only the Host manager can recover it. Unknown native delivery is never retried.

For guarded Managed observation, Runtime briefly claims control before reading
the Host context and releases that claim before asking the policy. An existing
Auto claim stays held across observation. Cancellation fences observation after
a new claim; uncertain claim or release confirmation taints the run without
automatic retry. Connector-native observation retains its existing lifecycle.

For shared Host text-menu-v2, Runtime verifies the Host's advertised
`sts2.host-runtime/text-menu-v2-owner-1` contract before claiming control. It
then sends `policy-runtime:<run_id>` on each claim and checks the Host's exact
echo in the claim result. The run ID is generated by the Runtime control plane;
the policy adapter cannot set it. This value separates menu presentation state
among trusted logical sessions under the same Host service. It is not an
authorization credential: the attached bearer and the Host-issued lease token
and epoch remain the control checks. An older Host that does not advertise and
echo this contract is rejected before Runtime takes control.

For a text-menu Auto page that fails whole-decision admission after exact
environment and profile checks, Runtime records one
`text_observation_not_admitted` event with the admission reason and full current
snapshot before handing control to Human. Settling observations do not generate
this event. The record is an observation only: it has no decision, delivery or
causal successor association, and it does not expand the policy's supported
interactions. A failed event write triggers the existing fail-closed handoff.

## Stateful observation port (opt-in)

The separate `sts2.policy-runtime/decision-only-ndjson-4` profile consumes only
generic `sts2.player-environment/snapshot-1` with zero Reads and an explicit
`adapter_config.public_stateful_profile` of
`sts2.policy-runtime/public-observation-stateful-v1`. It uses the same Runtime,
NDJSON child transport, controller, finite authorization, Stop and submission
owners. It does not convert a public Snapshot into text-menu or native-logical
input, and the complete ordered Connector catalog remains unchanged.

Port4 sends the ordinary five-field decision input under `decision`, with a
separate `control`: `continuity_token`, `episode_scope`, `episode_id`,
`segment_id`, `observation_ordinal` and nullable `previous_action`. Snapshot1
has no atomic game continuity identity, so this implementation explicitly uses
`bounded_policy_segment`. An accepted new Snapshot watermark advances the
ordinal once; an unchanged full body/catalog at the same watermark retains it,
excluding only `observed_at`. Changed same-watermark content or a regressing
sequence fails closed. Human, a mode change, Stop, budget handoff and an offered
request without trustworthy completion end that segment. New segments start at
ordinal1 with fresh identities and empty history; these identities do not prove
one native game episode.

The output still contains only the original candidate digest, full-length scores
and optional selected index. Completion echoes the token, episode/segment,
ordinal, Snapshot ID/sequence and nullable `previous_action_request_id`. The
existing transport checks exact request/schema/completion and cancellation
before accepting scores. An optional previous action is control only: it joins
one durably written delivered Receipt and the Runtime's qualified observed
successor. It is consumed once at offer and never becomes an inferred Model
action/feedback channel or causal Human evidence.

Port4 requires the existing Evidence writer. It records `public_decision_input`
with the exact Snapshot, active mode, control and validated completion immediately
before the ordinary decision. The matching typed Evidence reader verifies full
catalog/order, segment/ordinal/cache consistency and known history joins; old
v1–v3 and Agent-session readers retain their semantics. Source support does not
update an installed package or qualify a model, native journey or strategy.

A Manifest may select `sts2.policy-runtime/decision-only-ndjson-2` with an
explicit `text-menu-snapshot-1` or `text-menu-snapshot-2` representation and
zero Reads. In that mode Runtime obtains one atomic Connector
`text-menu/observation-context` envelope for the selected input profile and
its opaque, nullable game continuity ID. A null ID
or unsupported endpoint fails closed; Runtime never falls back to a separate
snapshot GET. The game ID is control metadata and never enters the model page.

Runtime mints a separate opaque `continuity_token` and adds it to the v2
request's existing decision input. The v2 response keeps the existing
three-field `output` and adds a sibling `completion` with that token, exact
`snapshot_id`, and `sequence`. The port checks request ID, digest, count and
completion before accepting scores. A known completed observation remains in
the same continuity segment through ordinary Human handoff, One-Step,
abstention and known non-delivery. A new game ID or a request offered to the
child without a trustworthy completion causes a new token before the next
offer. Command `recovery_epoch` is separate: it rejects stale commands but
does not itself erase a known model observation.

The child processes requests serially; an old cancelled response cannot satisfy
a new request. Runtime marks a port request offered when it invokes the child
stdin write, and treats timeout, abort, invalid response or port failure after
that point as uncertain model state. Human handoff does not wait for the old
child computation. After too many outstanding cancelled request IDs the port
closes and kills that child rather than forgetting old IDs. This contract does
not claim native action execution, actual-action feedback, or model quality.

## Confirmed interaction port (opt-in)

`sts2.policy-runtime/decision-only-ndjson-3` uses `policy-port-3` with an
existing text-menu representation and a configured Agent Evidence writer.
Its decision input adds required nullable `previous_interaction`. A non-null
value binds the prior recorded decision, scored snapshot, complete candidate
digest, submitted action and Connector request to either `menu_applied` or
`native_input_delivered`. It carries no reward, inferred effect or native
Commit claim. The response completion adds required nullable
`previous_interaction_request_id`, which must echo the offered value.
After that completion is validated, the Agent Run's `text_decision_input`
records the actual opaque `continuity_token` and nullable echoed request ID
in `observation_context` beside the scored snapshot. Earlier ports retain
their two-field input event.

Runtime offers a recorded interaction at most once, on a newer observation in
the same continuity segment. A failed Evidence append, unknown or unapplied
result, Shadow decision, or abandoned decision creates no interaction. An
offered request that fails or is cancelled rotates the v3 continuity; Human,
mode changes, One-Step completion, budget handoff, Stop, taint and game identity
changes synchronously clear the pending interaction. The v1 and v2 port
contracts keep their original wire shapes and lifecycle rules.

`text_menu_dispatch_attempt` and its counters record an intended dispatch,
not Connector delivery. If Human, Stop or the budget deadline cancels while
that event is being written, Runtime records `text_menu_dispatch_cancelled`
with `recovery_before_submit` and never calls Connector submit. An already
submitted native input still waits for its Connector result. A failed
cancellation-event write taints the Agent Run because its evidence is incomplete.

## Standalone consumer package

Version `0.1.0-rc.24` provides a source candidate for external consumers. Build
from a committed component checkout with the checked-in lockfile:

```bash
npm ci
npm --prefix components/policy-runtime run check
npm --prefix components/policy-runtime run package -- --output /absolute/package-output
```

The last command creates `rsgcsg-sts2-policy-runtime-0.1.0-rc.24.tgz`,
`policy-runtime-package.json` and `checksums.sha256`. It requires committed
component source and does not publish anything. The package contains compiled
JavaScript/declarations, CLI entries, license, a component identity record and
an npm production shrinkwrap. It has no dependency on a sibling source tree.
This candidate bundles the compiled Connector SDK built from the same committed
workspace source and records its source identity and packaged bytes. The SDK's
Zod dependency is fetched against its exact locked registry URL and SHA-512,
then bundled as verified bytes alongside the SDK. The package identity records
both bundled tree hashes. No published Connector SDK release is implied.

Consumers verify the tarball SHA-256 and install that exact candidate tarball
with their own lockfile. Invoke the installed `sts2-policy-runtime` executable,
or import the public `@rsgcsg/sts2-policy-runtime` package and `./child-port`
export. Pin the component source revision, source and public-contract digests,
package SHA-256/integrity and protocol from `policy-runtime-package.json`.
Never substitute a floating branch or raw source import for that pin.
`check:package` packs twice, compares bytes, installs outside the workspace,
checks bundled SDK/Zod identity and installed bytes, exercises synthetic modes and starts/stops the
installed CLI in Human mode. It also checks the installed native Agent Runtime/SDK
APIs and validates a fixture Agent Manifest against the bundled fixed publication
profile. This is CPU package evidence with no game contact; the native API check
does not register or run a real Model.
It does not establish real-model, game, Full-Run or causal-successor evidence.

## Process boundary

`sts2-policy-runtime` starts a loopback service and a decision-only NDJSON child.
The child command is consumer-owned; the example below uses STPD:

```bash
npm --prefix components/policy-runtime run build
node components/policy-runtime/dist/cli.js \
  --manifest ../STPD/policy-manifests/s1-policy-adapter-v1.json \
  --adapter-command ../STPD/.venv/bin/python \
  --adapter-cwd ../STPD \
  --adapter-arg tools/policy_adapter.py \
  --adapter-arg=--manifest \
  --adapter-arg policy-manifests/s1-policy-adapter-v1.json
```

Arguments are passed literally. If a child argument begins with `--`, use the
`--adapter-arg=value` form. The service defaults to Connector
`http://127.0.0.1:15526` and Policy Runtime `http://127.0.0.1:15527`.
For a Managed Manifest, the caller must supply `--managed-binding`,
`--managed-attachment`, `--managed-expected-service-instance-id`,
`--managed-expected-runtime-instance-id`, and
`--managed-expected-game-continuity-id`. These select one existing Host-owned
service and episode; the Managed CLI rejects `--connector-endpoint`. Its
`startup-2` includes the verified binding digest and selected service/runtime/
continuity IDs without a token, private path, or seed.

The command fails before startup when the policy artifact is absent, its
SHA-256 differs from the Policy Manifest, or the adapter's bounded code digest
drifts. The child must first attest that exact adapter identity; the loopback
service is not published until the parent verifies it. At runtime, any pinned
environment field drift fails before observation,
scoring or controller acquisition. Adapter decisions time out after 30 seconds
and return to Human before controller acquisition. Each Shadow/Auto/One-Step
authorization selects one Runtime-owned authorization mode. The default `bounded` mode permits 16
submission attempts, 32 policy calls and 60 seconds of monotonic time. The CLI
accepts `--max-auto-submissions`, `--max-policy-calls` and
`--auto-deadline-ms`; omitted values use those finite defaults and never mean
unlimited operation. `--autonomy-mode continuous` explicitly removes these three aggregate caps and cannot be combined with any cap flag. Library callers select `autoBudget: { mode: "continuous" }`; omitted or partial numeric inputs remain bounded (including optional undefined values), while null or invalid caps fail before execution. Continuous configuration accepts only its mode key.

The authorization is shared by background Auto and HTTP ticks,
is consumed before each real submission and before each policy call, and is not
renewed by polling, reconnects or new snapshots. At a limit the Runtime
cancels pending policy work, records `autonomy_budget_exhausted`, hands back to
Human and stops the background worker. The total-deadline handoff is armed for
the whole authorization, not only for a policy call, so a successful tick
followed by an Auto idle/successor gap still releases the controller without a
follow-up tick or status request; an in-flight native submit is still classified
by its Receipt before release. A new explicit Auto/Shadow/One-Step mode from
Human starts a new authorization. Returning to Human on an unsupported surface,
abstention, known non-delivery, fail-closed condition or taint ends the active
budget without a later deadline exhaustion. A submit already in flight is still
classified by its Receipt, including `unknown`, and a release failure remains
held. Release requires an exact Host acknowledgement; an unconfirmed release
taints the run and cannot be made confirmed by a second local close or lease
expiry. It remains reported as held, blocks new non-Human authorization and
prevents Stop from sealing success. Recover against the exact Host instance or
replace the Runtime run.
The CLI publishes its exact
startup identity before enabling Shadow/Auto drive. `unknown` delivery taints the
run and is never retried. `POST /v2/stop` or process termination releases the
controller and seals an Agent evidence directory bound to Runtime code, Manifest,
checkpoint and exact environment identity. After stop succeeds and the response finishes or disconnects,
the CLI closes its HTTP service and adapter child and exits; an embedding
library owner retains lifecycle control unless it supplies `onStopped`. The directory includes the canonical
Policy Manifest bytes and the exact child startup adapter attestation; Evidence
verification rejects any digest, identity or event-association drift.

## HTTP commands

- `GET /status`
- `GET /v2/environment`
- `POST /v2/mode` with `{"mode":"human|shadow|one_step|auto"}`
- `POST /v2/tick` with `{"max_ticks":1}`
- `POST /v2/stop` with `{}`

`GET /status` and every command response include `autonomy_budget` with the
configured limits, consumed submission/policy-call counts, monotonic elapsed and
remaining time, and the exhaustion/end reason. Runtime rc40 uniformly emits `status-2`/`startup-2` (native/GLOBAL: `agent-session-status-2`/`agent-session-startup-2`) and event envelopes `agent-run-event-2`/`agent-session-event-2`. Budgets carry `mode`; continuous caps and `remaining_ms` are null, never zero or defaults. Used counters and elapsed time remain real and can exceed the bounded defaults. Counters describe one explicit authorization segment, not the cumulative run. Human/Pause freezes that segment; explicit Continue from Human starts a new segment using the same fixed mode, with no automatic reauthorization. Continuous mode has no aggregate deadline timer. Per-call timeouts, known-stale recovery windows, GLOBAL no-progress, controller leases, evidence/memory limits and unknown-delivery fences still apply.

New typed readers explicitly accept archived v1 numeric bounded events and v2 unions; they reject mixed event versions or authorization modes within a run. The original status-1 schema remains archived beside `status-v2.schema.json`. Run manifests and immutable evidence manifests remain version 1 because their shapes did not change. No archived bytes, checksums or exact-code identity are rewritten. Budget exhaustion is a safety
handoff, not a completed task or a gameplay-success claim.

HTTP envelopes use `sts2.policy-runtime/http-2` (ticks append `/tick-1`). Every
mutation requires exactly one nonempty `X-STS2-Policy-Run-ID` header containing
the intended Runtime's `startup.run_id` or previously observed `status.run_id`.
This is the immutable Policy Runtime / Agent evidence session identity, not a
native game run ID or an authentication credential. Missing, empty or duplicate
headers return 428 `runtime_run_precondition_required`; a different run returns
409 `runtime_run_mismatch`. These rejections invoke no Runtime mutation. The
server owns this check because a separate pre-command status request cannot
prevent another process from reusing the port before POST arrives.

The mutation path is also versioned: an older HTTP-1 server does not recognize
`/v2/*`, and this server rejects the retired unversioned mutation paths. An old
server that ignores the new header therefore cannot accidentally receive a
new client's command. There is no fallback to old paths. Keep expected identity
through all steps of one command; status polling must not silently retarget it.

### Game binding and cross-interface Human recovery

The `rc.4` read-only `GET /v2/environment` returns exactly
`schema`, `run_id`, `runtime_instance_id`, and `recovery_epoch`, under
`sts2.policy-runtime/environment-1`. It reads capabilities from this Runtime's
actual configured Connector endpoint; it does not observe a decision, score,
acquire control, change `/status`, or write Agent evidence. A previously admitted
instance must still match. The read requires the same loopback Host/Origin
validation as commands. Unavailable capabilities return sanitized 503
`runtime_environment_unavailable`.

New unified UI clients read this binding **before** asynchronous Recorder
preparation. They compare its game instance with the actual game task bridge and
send both `X-STS2-Game-Instance-ID` and `X-STS2-Recovery-Epoch` on non-Human mode and
tick requests, together with the existing Runtime run header. The owner reads
fresh capabilities and checks identity before dispatch; game drift returns 409
`runtime_game_mismatch`. Missing/empty/duplicate malformed supplied headers return
428 `runtime_game_precondition_required` or `runtime_recovery_precondition_required`.
An omitted new header retains legacy semantics; new UI clients must always send
both, never substitute a refreshed identity into an old intent, and treat an
older server's environment 404 as requiring an update before starting a model.

Every explicit Human or Stop advances the process-wide recovery epoch immediately
on owner entry, before waiting for ongoing work. This also applies to legacy
callers without new headers. A prepared command with a previous epoch is rejected
409 `runtime_recovery_epoch_mismatch`; it cannot take back control after the other
interface returned to Human. The owner rechecks after async capability reads and
queue waits. A read started before recovery does not return a new epoch to that
old intent. Normal mode/tick leave the epoch unchanged, so One-Step mode and its
following tick retain the same binding. The counter is a non-negative safe integer;
exhaustion blocks new guarded control until a new Runtime run, but never blocks
Human/Stop. Human/Stop ignore new game/epoch preconditions and remain available if
the Connector is offline. Existing run-ID and local-request protections still apply.

A precondition error before any tick in a bounded HTTP request uses the ordinary
error envelope. If earlier ticks already completed, a later precondition failure
returns the normal HTTP 200 tick envelope with those results and a final
`not_admitted` result carrying the error code. It cannot label the whole request
unapplied or retry already executed actions. Existing unknown-delivery handling
is unchanged. This fence coordinates control intent; it is not authentication,
new game legality, or proof of scientific model quality.

Runtime rc41 corrects Human/Pause during a mandatory GLOBAL post-delivery
capture: the same lifecycle owner identifies its revoked operation by a private
cancellation token. The known Receipt remains `interrupted_known`; the original
capture rejection/error stays in the journal, control is released, and an
intentional Human revocation alone does not taint the run. A wrapped transport
abort is classified only at that revoked read boundary, after preserving its
original error. Identity drift, unknown delivery, storage or release failure
still fence/taint normally. Explicit Continue starts a fresh authorization and
never reuses the rejected capture. Rc40 is retained as an older exact package;
rc41 keeps the existing startup/status/event v2 contracts.

While a policy decision is pending, Human and Stop signal that decision's
recovery scope and invalidate its epoch before entering the serialized control
operation. The Runtime returns the old tick as `not_admitted` and never lets a
late policy result acquire a controller, submit an action, or overwrite the
new Human/Stopped state. The NDJSON child port removes an aborted request from
its pending table and ignores its late response, so it cannot be consumed by a
later request. A native `submit` already in flight is not cancelled: recovery
waits for its bounded Receipt path and preserves `delivered`, `not_delivered`,
or `unknown` evidence. Controller release is confirmed only after the
Connector acknowledges it; a release error leaves the controller conservatively
held and records the failure.

The service is loopback-only. Every POST requires `Content-Type: application/json`
(optional UTF-8 charset), a literal supported loopback Host with the bound port,
and either no Origin (local service clients) or the exact same HTTP origin.
Cross-origin browser requests, plain-text POSTs and rebound Host headers are
rejected before Runtime dispatch. UI clients call these commands; they never
submit gameplay actions directly.

Command callers must distinguish their HTTP wait from Runtime execution. A
request timeout or malformed response after POST does not establish that the
command was unapplied. Query status, permit an explicit Human handoff or stop,
and never automatically resubmit a tick. A stopped Runtime cannot be restarted
through HTTP; launch a fresh process/run for a new exact model/Manifest. A
validated 409/428 precondition rejection is known to be unapplied; it still does
not authorize automatic command retry or identity substitution.

The rc.4 standalone package and its updated Workbench client do not install a
game Mod. The matching Live UI source is part of the unified game DLL and needs
its own exact build/install/load qualification before use. Existing qualified
Recorder DLLs, collection-tool packages and historical Human evidence keep their
original identities; source cleanup does not upgrade them.

The `successor` event is a distinct same-environment non-settling observation
obtained by polling. It is not a native causal `S'` certificate. Agent events
bind decision metadata/scores, Receipt and successor but do not archive every
pre-decision Snapshot/Read input. Research projections and evaluation protocols
remain external consumers' responsibility.

Generic Policy Runtime successor waiting supports private, opt-in failure
diagnostics through `RuntimeOptions.successorWaitDiagnosticSink`. The CLI enables
this sink only when `STS2_POLICY_RUNTIME_SUCCESSOR_DIAGNOSTICS=1`, writing one
JSON line to its existing stderr stream (captured by the local Model service's
private `runtime.log`). Accepted waits produce no line and consume no diagnostic
record budget. These lines are operational diagnostics, outside sealed Agent-run
Evidence and the public status/HTTP contracts.

Each failed, cancelled or throwing wait reports the existing request/decision and
basis Snapshot IDs, poll count, monotonic elapsed time and at most 64 retained
poll rows. Rows contain only observed Snapshot ID/sequence/status, interaction
kind/completeness, same-basis/settling flags and a closed outcome category.
Missing whole bundles are labelled `no_whole_bundle`; arbitrary error messages,
Snapshot content, action operands, referents and Reads are excluded. Oversized
strings are omitted with an explicit flag. Old rows are dropped with explicit
counts; each JSON record is at most 32 KiB. At most 16 failure records and one
explicit cap marker are sent per Runtime instance. Sink errors disable only
diagnostics. The opt-in CLI writer handles both write callbacks and later stderr
`error` events, including EPIPE. It owns one stderr error listener for this
single-Runtime CLI process lifetime, so pending error events remain contained
after Stop; the default-off path attaches no listener. No global exception
handler is added. The default remains off. The private record declares whether
`max_attempts` limits all polls (legacy profiles/One-Step) or cumulative unusable
polls (generic public Auto), and reports actual unusable and qualified-settling
counts beside total `poll_count`. Qualified settling may therefore produce more
than 41 actual Auto polls; scalar row, byte and failure-record caps remain fixed.
Diagnostics do not establish native causal settlement.

## Continuous-operation repair candidate

The source candidate keeps Auto active after a correlated `not_delivered` receipt
only when `reason_code=stale_snapshot` and the Connector explicitly allows a fresh
snapshot retry. The controller is released and the next tick reacquires a complete
bundle, rescores it and creates new decision/request IDs within the finite recovery window described above. Its expiry returns to Human. Other non-delivery, unsupported decisions and all
unknown delivery retain existing handoff/taint behavior; no old action is replayed.

After a correlated delivered generic public Snapshot1 action in Auto, Runtime
keeps that same in-flight decision/request and controller while observing under
the original absolute authorization deadline. Complete same-profile/environment
settling frames distinct from the original scored basis with a greater sequence,
with a complete and consistent `bound_actions` catalog, may continue waiting.
Repeated occurrences of one such newer settling watermark also qualify. These
polls never become Model inputs, policy calls, another submission or a successor
event, and do not renew time/call/submission budgets. This covers both generic
M0 and PublicM2 Port4 without a Host/model-specific split.

The configured `successorPoll.maxAttempts` (default 41) is a cumulative allowance
for unusable polls in this Auto scope: same-original-basis, missing whole bundle
and incomplete/truncated/untrusted settling consume it. Qualified settling never
refunds spent unusable polls. Profile/environment/sequence errors and transport
exceptions retain immediate strict failure behavior. Polls use the configured
interval (default 250 ms), with a minimum positive 1 ms yield for this Auto path,
and clamp the next delay to the original deadline's remaining time. At that
deadline, the existing owner fences the wait and serializes Human/release/evidence
without a second tick or status read. Stop/Human fence late observations and
prevent any further acquisition or action; original delivered knowledge remains
in its Receipt. Exhausting unusable observations still taints conservatively and
never retries the delivered request.

A distinct newer same-environment non-settling observation preserves the existing
successor event and validation; it is an observation, not causal settlement or
a guarantee of future input admission. Interactive child selectors use the
ordinary successor and complete-input path without a native scene/timing gate.
PublicM2 remembers a delivered interaction only after that successor is written,
and offers it once on a subsequent admitted input. Settling polls do not advance
its input ordinal/history. Stop/deadline/taint retain existing continuity cleanup.

One-Step retains the original total poll cap (default 41 samples and 40×250 ms
scheduled waits, plus bounded acquisition). Text-menu-v1/v2, Managed text-v2 and
native-logical Agent contracts remain unchanged. Extending One-Step would affect
its existing synchronous caller timing and requires a separate contract packet.
The continuation guarantee applies to background Auto: a manual synchronous Auto
HTTP tick can outlast a caller timeout. An unknown command outcome remains
unknown and must not be replayed; this change promises no universal command
latency or new reconciliation manager. A One-Step mode change queued behind an
in-flight extended Auto wait also inherits that wait's latency; its own subsequent
tick still retains the One-Step poll cap.

Initial Auto settling already waits under its original global authorization.
This correction aligns post-delivery trustworthy waiting with that finite owner;
it does not establish native readiness duration, effect settlement, eventual
successor/game summary or strategy quality. It requires a newly qualified package
before live use. Historical artifacts and failed attempts remain immutable.

### Sealed public observation acquisition

Native text-menu-v2 callers may opt into
`--observation-acquisition sealed-text-menu-v2` (or the matching
`ConnectorPolicyClient` option). The Connector SDK assembles and verifies one
immutable public Snapshot capsule before Runtime admission. Acquisition failure
propagates without a direct-read fallback. Other profiles and Managed sessions
reject this option; existing direct acquisition remains the default.

This transport does not change input timing: the current stateful port offers
only admitted, interactive, nonempty action menus. Internal waits and successor
observations are not Model inputs. A sampled-offer dataset must retain that
distinction and must not reconstruct Model history from every transport capture.

The required owned-Current lifetime tests compose the real game-free C#
Store/Projector, REST SDK and an OS Ndjson fixture child. They require the existing
.NET 9 source-test toolchain; the test helper builds its zero-package portable
bridge once, or uses `STS2_OWNED_CURRENT_BRIDGE_DLL` when explicitly supplied.
Controller/capability/Await fixture responses are synthetic; no game or model
inference is executed.

The Runtime test command serializes test files. The suite includes independent
fsync-heavy evidence admission tests and a portable bridge build; simultaneous
files can exhaust the unchanged per-test deadline through filesystem contention.
Assertions, timeouts, admission limits and optional-case conditions are preserved.


### GLOBAL public program profile

`p1-global-r1` is handled by the same `PolicyRuntime.forAgent`, Agent NDJSON
port, lifecycle owner, wall/submission/call budget, controller cleanup and HTTP
server as the native Agent profile. `createGlobalProgramManifest` binds the
shared SDK reducer and `modelView` to a fixed model-less program descriptor.
`ReferenceGlobalTransport` uses the SDK retained public capture and control
session; `ManagedServicePolicyClient.attachGlobal` uses the existing Managed
service with its real opaque control token/epoch. A complete decision requires
an actual nonnull episode binding. A later changed or null binding stops the
operation; a classified delivered prefix remains `interrupted_known`.

The first semantic occurrence is offered through `consume`; only the existing
ledger's accepted report and the original `consume_ack` frame permit `next` and
native submission. Every known delivery gets a sealed post-delivery capture,
including choices without a fold. Stop or capture failure retains that known
prefix. Unknown delivery fences further writes and is never retried.

`replayGlobalAgentRun` verifies original source/response bytes, the shared
projection, original framed Agent offers/completions/ACKs, shared lowering and
original Receipt/result joins. The Python Evidence verifier additionally
requires the application-owned `global_runtime_directory` pin; archives cannot
supply executable paths. This verifies archived public facts and program
plumbing, not native coverage, Human origin, Task success or training admission.
`sts2-p1 run` is a compatibility frontend to the existing Runtime CLI and accepts
its `--manifest`, `--adapter-command` and environment options. Historical P1
records retain their existing converter and evidence scope.

GLOBAL capture storage revision `sts2.policy-runtime/global-capture-response-1`
keeps the original response SHA/byte count, exact JSON prefix/suffix and every
chunk envelope field, but references the same content-addressed `source` record
for the payload slice. `restoreGlobalCaptureResponse` reconstructs byte-identical
wire responses before the unchanged SDK chunk decoder/assembler and source joins.
Old inline responses remain readable; failed/partial captures keep inline original
responses because no complete source owns them. Received wire bytes are first
fsynced by the same evidence owner as diagnostic `capture-staging-<request>-<ordinal>-<sha>.bin`
files. Successful source/reference bytes and events must be durable before those
temporary duplicates are removed; failure/cancellation first flushes originals
inline. A hard-killed producer retains its already staged response prefix;
`readGlobalCaptureStaging(directory)` verifies filename/hash/size and returns
read-only request/ordinal references. These are interrupted diagnostics, never
sealed evidence, continuation permission or a second artifact type. Outstanding
staging blocks sealing. Staging and final artifacts share the original physical
512 MiB artifact budget, and their memory uses the shared Agent byte budget.
The immutable inventory, events,
checksums, roles and typed Evidence type remain unchanged. New archives require
the matching trusted Runtime package; never edit a sealed old archive to upgrade
it. The 512 MiB unique-artifact cap remains; journal and inventory bytes are
additional, so capacity reports must include them and actual allocated disk bytes.
The existing `spireagent.storage.archives` deterministic gzip/tar path is the
cold storage/transfer encoding and does not change content identity.

The opt-in technical repetition budget uses `globalNoProgress` in
`PolicyRuntime.forAgent`, or CLI `--no-progress-window 32 --no-progress-repeats 8`.
Both parameters are explicit (defaults apply only once either flag opts in), with
window 2–256 decisions and repeat limit 2–window. Status exposes
`global_no_progress` with settings and last check; config and an exhausted check
are retained in the original event stream. The shared full-public graph comparator
removes only the existing binding envelope and applies the already declared
entity/choice bijection and inventory multiset rules. It preserves public words,
card/owner/choice relationships and meaningful order; `incomparable` counts as a
new state. A new public state clears repeat counts while retaining the bounded
state window, so an alternating A/B loop can exhaust. Retained representatives
are charged to the existing shared Agent byte budget. The repeated choice is not
submitted: original Stop releases control, closes the Agent and seals the run.
The tick reason and status error are `no_progress`; replay recomputes the window
and returns `technical_stop: "no_progress"`. This is a censored technical stop,
never natural win/loss, strategy qualification or permission to restart. It does
not substitute for fixing an incorrectly offered action.

Workbench exposes `POST /api/local-model-registrations/register-global-program`
with `{}` or `{"environment_kind":"native"|"managed"}`. The reviewed Python
program and adapter identity are selected by application code. Registration has
no model dependency; the ordinary installed package, environment, readiness,
Agent request and explicit load/takeover checks still apply. Its current-choice
Task declares no success predicate. Terminal observations and operation closure
therefore remain separate from Task success and strategy quality.

### GLOBAL originals at full-run scale

GLOBAL original references retain their exact decoded SHA-256 and byte length.
Large blobs (at least64KiB, when compression saves bytes) use lossless gzip storage
v1 with explicit `global-<original-sha256>.bin.gz` paths; small/incompressible blobs
retain `.bin`. The immutable directory inventory/checksums bind stored bytes,
while the shared replay decodes with the original length bound and verifies the
original hash. Old `.bin` archives remain readable. Sampling, capture frequency,
P1, Model inputs, native delivery and successor semantics are unchanged.

The existing512MiB GLOBAL writer limit bounds physical blob/staging occupancy;
it is not a logical uncompressed full-run limit. No files are silently discarded.
A real Stop whose complete seal fails fsyncs `diagnostic-manifest.json` binding the
unchanged failure prefix. This is explicitly `complete=false/status=unsealed`,
not a recording seal; ordinary typed verification still rejects it. The same
Application publication operation may preserve it as an analysis diagnostic,
reporting a separate diagnostic ID and retaining failed/unknown recording status.
Reconciliation reads that ID only; it never replays an action or republishes.

The normal Application Store path compresses the sealed directory, transfers it
to the configured R2 peer and validates empty-directory restoration. R2 owns
long-term retention; local data is the bounded active working set. Cool an inactive
expanded copy only after its exact artifact, all hashes and actual consumer restore
pass. This path archives per completed run; it does not add cloud latency to native
capture or introduce a second Runtime/cloud storage controller.

### Offline original Result comparison

Trajectory extraction reads original lossless GLOBAL Agent frames and Runtime
Results. It binds consume/directive/Result to the original acquisition and input
occurrence; it never manufactures a Result from a native Human recording.
`tools/p1-native-replay.mjs` compares the complete Result together with its original
observation and choice under one existing public entity mapping. Every literal
step field, status and extension remains part of comparison. The wire constructor
is shared with Runtime producers; execution and commitment decisions stay with
each owner. Original unknown or invalid/unbound Results fail before Host start.
Failed replay execution retains its actual known/unknown step prefix before
comparison and stops without retry. Diagnostic location preserves all mismatches
and cannot establish whole-run P1 equivalence.

### Private initial-progress original

A GLOBAL Reference Runtime may receive `--initial-progress-original PATH` plus
`--initial-progress-original-sha256 SHA256` from its existing owned Environment.
The descriptor binds the actual native `progress.save` copy to the runtime and
is validated before driving. Raw bytes are retained as a private GLOBAL artifact
with `global_initial_progress_original`, sealed and transferred with the run.
The shared verifier checks metadata, runtime binding, byte length/hash and event
order. The snapshot never enters Agent input, P1 observations or action bindings.
An absent/unknown snapshot remains unknown; old originals are never backfilled.
Only rc49 advertises this CLI addition. Older pinned Runtime registrations keep
their behavior and require normal re-registration against this version to carry
the copy into their sealed original; the Environment still retains its own copy.
