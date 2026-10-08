# Architecture

## Product Boundary

STS2 Connector turns the real game's current fair-player state and native UI
inputs into a constrained Player Environment. STS2 remains authoritative for
rules, RNG, objects, effects and Commit paths.

```text
STS2
-> LiveHost observation
-> NativeUi binding/execution
-> PlayerEnvironment contract
-> loopback REST
-> optional SDK or MCP transport
-> consumer
```

The Connector is not a strategy engine, simulator, Headless process manager,
UI automation system, or source-code interpreter. The same embedded Host can
report `live_ui` or `headless` according to the actual Godot display driver;
launching that process, save/profile policy and lifecycle belong to a Host
launcher such as STS2-headless.

## Ownership

| Layer | Owns | Must not own |
|---|---|---|
| `LiveHost` | one current input owner; stable fair-player facts | public authority; consumer strategy |
| `NativeUi` | exact native objects, controls, operands, rediscovery and native input | wire identities; guessed business rules |
| `PlayerEnvironment` | Snapshot, Interaction, Referent, Read, BoundAction, Receipt, stale semantics | hidden state; second legality/effect model |
| `Authority` | artifact/runtime/environment identity, one controller, request idempotency | UI legality or outcomes |
| REST/MCP | bytes and endpoint translation | semantics or authority |
| optional Host control | process-local, runtime-bound shutdown and episode seed/provenance requested by a supervisor | Player Environment actions, gameplay authority or remote lifecycle control |
| TypeScript SDK | strict decoding, HTTP and controller session mechanics | strategy, normalization, legality or retries of unknown delivery |
| consumer | strategy, prompts, search, memory and progress interpretation | native operands or mutation authority |

An optional process-local witness seam may freeze one public Snapshot/catalog
and correlate an already accepted native UI action against the same Host-local
object bindings. It returns only zero, ambiguous, or exact-unique correlation;
The scoped native-input variant also correlates expected and accepted operand
references when public delivery is settling, returning `exact_native_input`
without a BoundAction. It preserves observation identity and makes no legality
claim at Human time. Neither variant can execute an action; both are absent
from REST, MCP, and the SDK. This is
conformance/recording infrastructure, not a second authority path.

## Canonical Contract

`contracts/player-environment-contract.json` is the machine-readable protocol
inventory. `contracts/host-compatibility.json` is the separate exact game and
sealed-artifact authority inventory. C# records implement the wire and enforce
the Host inventory; `npm run check:compatibility` rejects source/manifest drift.
TypeScript schemas independently
reject malformed responses. `npm run check:contract` keeps protocol version,
routes, schemas, verbs and hard-shell invariants aligned. Neither the SDK nor a
consumer may extend authority by accepting more than the Host publishes.

The default-disabled `/api/host-control/shutdown` and
`/api/host-control/provenance` routes are outside this canonical contract. A
Host supervisor may enable them for one process with a 256-bit environment
token. Every request is bound to the loaded runtime instance. Shutdown invokes
STS2's native `NGame.Quit()` path. An optional process seed is applied only to a
headless standard-run Embark after the same exact owner/control revalidation,
through STS2's own `NGame.DebugSeedOverride` seam; provenance then compares it
with the game-owned run RNG seed. The token and seed metadata are not published
through capabilities, SDK, MCP, Snapshot, Read or BoundAction.

Game and artifact admission precede UI actionability. A sealed tuple may enter
normal authority; a known candidate needs exact game ID and source-revision
process opt-ins. Empty, partial, mismatched and unknown identities fail closed.
Build, install and a 40-character Git revision are identity facts, not
qualification. Native owner, operand and control legality are still
rediscovered for every delivery after admission.

## Observe, Read, Interact

**Observe** returns stable current facts, the current Interaction, Referents,
advertised Reads, completeness, and a finite BoundAction projection.

**Read** retrieves stable normal-player information that need not be repeated
in every hot Snapshot. A Read is advertised, Snapshot-bound, runtime-bound,
read-only and non-authorizing.

**Interact** submits one opaque BoundAction already present in the current
complete projection. The public action names Referents; exact native operands
remain Host-local. The Host re-observes and revalidates before native delivery.

Only a non-empty complete BoundAction projection is `interactive`. Truncated,
unknown-owner, stale, unsupported, identity-incomplete or controller-conflicted
states have no mutation authority.

## Delivery Lifecycle

`request_id` is idempotent within the runtime. The first attempt produces one
action-local Receipt; a duplicate returns that Receipt and cannot deliver the
input twice.

- `delivered`: the game-owned input path accepted delivery.
- `not_delivered`: no mutation was delivered; the reason determines whether a
  newly observed decision may proceed.
- `unknown`: delivery may have happened; automatic retry is forbidden.

Receipt delivery is deliberately narrower than business completion. Consumers
use the immediate successor and later observation for progress without
recreating game rules.

The Connector composition root admits at most 256 pending main-thread callbacks
and starts at most ten per process-frame drain. Full admission fails immediately
with `MainThreadQueueFullException`; the existing REST error boundary returns
HTTP 503 with `main_thread_queue_full`, before that callback has run. There is
no implicit retry. Pending work retains FIFO order across frame budgets.

Internal `RunOnMainThread` callers may supply a cancellation token. Cancellation
and start share one gate: cancellation that wins removes the pending callback,
releases capacity and cancels its task without executing native input. After
start, cancellation does not erase the actual result or exception, including
an uncertain native outcome. This dispatch boundary creates neither gameplay
legality nor controller authority. Legacy REST callers currently supply no token;
this seam alone does not detect disconnected HTTP clients or revoke a controller.

## Information Boundary

The information policy is fair-player only. Stable visible facts and
player-openable details are projected; hidden RNG, true draw order, unrevealed
future content and private game state are excluded. Unknown tooltip/subtype or
owner drift fails the affected surface closed. See
[Information Closure](INFORMATION_CLOSURE.md).

The default-off `native_pages.v1` profile may open/read/return fixed native
pages as operator evidence. It reserves input while active and creates no
BoundAction or controller authority.

## Identity And Compatibility

Capabilities expose exact Host artifact SHA-256/MVID/source revision, runtime
instance, game identity, main assembly identity and Modset. Observation and
mutation compatibility are explicit. A new game binary, Modset or Host artifact
does not inherit old Live evidence automatically.

The normal mutation envelope remains Connector-only. An exact process may
identify one complete Modset fingerprint containing this Connector plus only
Mods declared `affects_gameplay=false`. That observer canary requires the full
fingerprint, remains observation-only, and never becomes mutation authority or
durable support by declaration.

The installed Mod implementation ID and DLL remain `STS2_MCP` for upgrade and
rollback compatibility. Current source naming is `STS2Connector`; MCP is only
an optional transport.

Native object aliases retain a public kind prefix followed by 192 cryptographically
random bits. They are opaque and carry no allocation counter, session sequence,
object hash or hidden enumeration order. One exact native object retains its alias
inside one registry/runtime lifetime; another runtime receives independent aliases.
Host-private exact object references still bind observation and delivery. SDK and
recording consumers compare IDs as opaque strings; historical recorded IDs and
their original producers are preserved without rewriting or rebinding.

## Extension Rules

A new supported interaction needs visible extraction, exact private binding,
finite projection, execute-time revalidation, tests and exact-runtime evidence.
A data-only content instance composed entirely from an existing native UI shape
should require no consumer strategy or protocol fork. An unknown UI shape or
unknown semantics may still be observed, but it receives no guessed action.

No extension may introduce index/coordinate mutation, arbitrary method calls,
consumer operands, silent fallback, a second controller, hidden information, or
automatic retry after `unknown`.
