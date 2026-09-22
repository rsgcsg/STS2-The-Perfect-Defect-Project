# Policy Runtime

The Policy Runtime is a model-neutral Connector consumer. It owns controller
lifecycle, Human/Shadow/One-Step/Auto modes, stale refresh, delivery safety,
stable-successor polling, and Agent-run evidence. It does not own game legality,
model inference, or native operands.

The Connector supplies one complete ordered `BoundAction` catalog. A policy
adapter receives that exact Snapshot and Read bundle, echoes the catalog digest,
returns one score per candidate and an optional selected index, and never returns
an action object. The Runtime resolves the index against the same current catalog
and submits only that Connector-owned `bound_action_id`.

Before observation, each Manifest must exactly pin the Connector environment:
host kind, Connector version/source revision/artifact SHA-256/module version ID,
Modset status/fingerprint, and the complete ordered list of loaded Mod IDs. Any
field drift fails closed before Snapshot observation or policy scoring.

## Standalone consumer package

Version `0.1.0-rc.8` provides a candidate package for external consumers. Build
from a committed component checkout with the checked-in lockfile:

```bash
npm ci
npm --prefix components/policy-runtime run check
npm --prefix components/policy-runtime run package -- --output /absolute/package-output
```

The last command creates `rsgcsg-sts2-policy-runtime-0.1.0-rc.8.tgz`,
`policy-runtime-package.json` and `checksums.sha256`. It requires committed
component source and does not publish anything. The package contains compiled
JavaScript/declarations, CLI entries, license, a component identity record and
an npm production shrinkwrap. It has no dependency on a sibling source tree.
The Connector SDK uses the exact `consumer-sdk/v1.1.0-rc.1` release URL and
SHA-512 integrity from this component's lockfile; its transitive dependencies
are also frozen in the packaged shrinkwrap.

Consumers verify the tarball SHA-256 and install that exact tarball/release URL
with their own lockfile. Invoke the installed `sts2-policy-runtime` executable,
or import the public `@rsgcsg/sts2-policy-runtime` package and `./child-port`
export. Pin the component source revision, source and public-contract digests,
package SHA-256/integrity and protocol from `policy-runtime-package.json`.
Never substitute a floating branch or raw source import for that pin.
`check:package` packs twice, compares bytes, installs outside the workspace,
checks resolved SDK integrity, exercises synthetic modes and starts/stops the
installed CLI in Human mode. This is CPU package evidence with no game contact.
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

The command fails before startup when the policy artifact is absent, its
SHA-256 differs from the Policy Manifest, or the adapter's bounded code digest
drifts. The child must first attest that exact adapter identity; the loopback
service is not published until the parent verifies it. At runtime, any pinned
environment field drift fails before observation,
scoring or controller acquisition. Adapter decisions time out after 30 seconds
and return to Human before controller acquisition. Each Shadow/Auto/One-Step
authorization also receives one finite Runtime-owned budget: by default 16
submission attempts, 32 policy calls and 60 seconds of monotonic time. The CLI
accepts `--max-auto-submissions`, `--max-policy-calls` and
`--auto-deadline-ms`; omitted values use those finite defaults and never mean
unlimited operation. The wallet is shared by background Auto and HTTP ticks,
is consumed before each real submission and before each policy call, and is not
renewed by polling, reconnects or new snapshots. At a limit the Runtime
cancels pending policy work, records `autonomy_budget_exhausted`, hands back to
Human and stops the background worker; a new explicit Auto/Shadow/One-Step mode
from Human starts a new authorization. A submit already in flight is still
classified by its Receipt, including `unknown`, and a release failure remains
held. The CLI publishes its exact
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
remaining time, and the exhaustion/end reason. Budget exhaustion is a safety
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

## Continuous-operation repair candidate

The source candidate keeps Auto active after a correlated `not_delivered` receipt
only when `reason_code=stale_snapshot` and the Connector explicitly allows a fresh
snapshot retry. The controller is released and the next tick reacquires a complete
bundle, rescores it and creates new decision/request IDs. Three consecutive stale
submissions return to Human. Other non-delivery, unsupported decisions and all
unknown delivery retain existing handoff/taint behavior; no old action is replayed.

After delivered input, the default bounded observation wait is 41 samples at a
250 ms fixed interval (10 seconds of scheduled waiting, plus bounded HTTP time),
with one observation attempt per sample. This accommodates enemy animations; it
is not a causal settlement proof. Human/Stop interrupts further polling and never
submits another action. Exhaustion or identity drift still fails closed. Initial
settling frames do not terminate Auto; unsupported stable surfaces still hand off.
These changes require a newly pinned package before live use; rc.4 artifacts remain
immutable and do not acquire this behavior from a source edit.
