# Native Workbench v1 implementation contract

2026-10-08. E6 candidate design. Base `c8940b26079044dbc00ebd1c14e89214160eff9f`.
The lead reviewed this design before the delegated source implementation. It implements
baseline specification sections 7-8 and product B/G within advertised owner
capabilities; it does not claim installed rendering, Human origin, remote
execution, full journeys or G2/V1 acceptance.

## One owner and five native pages

The Godot Workbench is a presentation in the existing Platform panel. Pages:
`play`, `data`, `training`, `models`, `settings`. Existing Recorder/Policy views
remain available. The Python Application owns application composition. Existing
import/dataset/training/export/registration/model/account/member/environment
services own work, state, journals, use and recovery. No new task journal,
worker queue, numerical loop, gameplay executor or generic RPC proxy is added.

The view displays selected existing source/artifact/operation/attempt/model/run
IDs, owner progress, errors, qualification/use, result links and supported
controls. Missing source-aware preparation and remote execution remain disabled
with owner reasons. Selection, pagination and geometry are presentation state;
credentials never enter layout files.

## Authenticated pair and selected configuration

Existing native browser registration is non-authorizing and stays compatible.
A new protected bootstrap credential is provisioned only by the existing verified
selected-launcher installation owner through explicit `native-access` opt-in.
Implementation and synthetic fixtures do not perform actual setup or request a
new Human approval. Old kits without bootstrap remain discovery-only.

Fixed macOS path: `~/Library/Application Support/spireagent/workbench/native-access.json`.
Exact bootstrap fields: `schema=spireagent/native-workbench-bootstrap-v1`,
`enabled=true`, `config_path`, `launcher_sha256`, `game_mod_sha256`, `secret`.
The secret is 32 random bytes represented as 64 lowercase hex. Private regular
file <=4096 bytes, owner-only permissions, no symlink in any selected path.
`config_path` equals the existing selected `launcher.json` profile; its raw hash
matches `launcher_sha256` (exact selected launcher.json file bytes, not config
bytes); actual `configuration_id` independently binds the active runtime config.
Existing release/workbench/lock identity checks remain
in the installer owner. `game_mod_sha256` comes from that verified kit inventory,
not a native HTTP argument. The loaded Mod must match that artifact. Disable or
remove the bootstrap to revoke access; never expose its contents in status/logs.
The trust boundary is the existing trusted local OS account, not hostile code
already running as that account.

Selected Workbench submits `POST /v1/workbench/native-register` independently of
legacy browser registration. The latter remains non-authorizing presentation:
its registration, URL and unregister cannot select or revoke a signed peer.
A foreign legacy Workbench can coexist unchanged. Before signing, Workbench
revalidates selected clean source/lock, saved ProjectConfig, runtime record,
instance and own server port. It reads the current game runtime from the bounded
existing bridge status and never signs an arbitrary callback origin. It submits
to the fixed game bridge. Exact request fields are `schema=sts2.platform/native-workbench-pair-1`,
`runtime_instance_id`, `workbench_instance_id`, `configuration_id`, `workbench_url`,
`pair_id`, `expires_at`, `signature`. IDs/hashes use bounded lowercase hex,
All signing/bootstrap string fields reject control characters before hashing;
Runtime ID is bounded 1..128 UTF-8 chars without controls, origin is exactly
`http://127.0.0.1:<port>/`, expiry integer is now+1..600 seconds. No Origin header,
redirect, proxy, arbitrary port scan or broad Workbench control token is used.
The bridge requires matching loaded artifact and selected bootstrap opt-in before
accepting the pair. A different live native context conflicts independently of
legacy presentation. Invalid selection, signature, clock or expiry fails closed.

Signing bytes are UTF-8 lines with one final newline, in this exact order:
role, Runtime ID, Workbench instance, configuration ID, Workbench URL, pair ID,
integer expiry. Roles are `native-register-v1`, `native-register-ack-v1`,
`native-access-v1`. Signature/ACK/token are lowercase HMAC-SHA256 using bootstrap
secret bytes. Distinct roles prevent response/token substitution. ACK returns
exact `schema=sts2.platform/native-workbench-pair-ack-1`, the six binding fields
and `signature`. Workbench verifies before installing its active pair. The Mod
stores the derived scoped bearer only in an in-process connection accessible to
its native UI; public bridge status/health never expose it. An exact active pair
duplicate is idempotent; same-context renewal requires strictly later expiry.
Equal-expiry different pairs and older renewals cannot replace it. The existing
connection owner serializes acceptance, current proof and exact close with one
lock and a protocol-clock high-water; clock regression never restores access.

The one production auth owner captures the process-immutable runtime once in
TaskBridge startup, before the listener or any auth lock. The existing controller
Snapshot can expire leases, so it must never be called under the auth lock.
Register/close consume raw body/headers; the owner acquires its lock first, reads
the authoritative selected bootstrap there, and validates signature/scope before
any credential transition, install, proof or retirement. Current/IsCurrent and
native link completion use that same owner/read boundary. Caller-provided pre-read
bootstrap/runtime snapshots cannot reset generation or retirement. Uninitialized,
disabled, missing or drifted selection fails closed. Independent test owners bind
their own immutable runtime and private authoritative fixture reader; production
has one instance and no additional Connector API or epoch/lease ledger.

`POST /v1/workbench/native-unregister` is authentication cleanup only. JSON body
contains exactly the six binding fields, at most 4096 bytes, with the existing
native-access bearer and four exact headers. Current bootstrap, game and ordinary
expiry apply. Exact current full-pair close terminally retires its entire
credential/game/Workbench/config/URL context across all pair IDs. With no current
pair, authenticated close retires the possibly accepted candidate and returns
`already_closed`. A different current pair returns 409 without changing it.
Response is exactly `schema=sts2.platform/native-workbench-close-1`,
`status=closed|already_closed`, `binding=<six fields>`; no secret/bearer and no
job/controller completion claim.

Retirement remains terminal after 600 seconds for that credential/game generation.
Delayed registration, in-flight later renewal and freshly signed reuse of a closed
context are rejected. At most 32 contexts are retired; admission reserves a free
retirement entry before installing a new current context. Renewal and duplicate
close consume no additional entry. Entries are never evicted by slot replacement,
time or capacity pressure. Further new contexts fail closed at capacity until an
explicit new secret or confirmed new game runtime invalidates all old requests
before reset. A new Workbench instance is a new bounded context; reopening the
same closed context requires a new credential. This is ephemeral authentication,
not an action/job/event ledger.

Before graceful close, Workbench snapshots current/pending possibly accepted pair,
peer and lifecycle generation, fences candidate creation/sign/send/install, and
revokes local access. Late ACK cannot reinstall it. Bounded join timeout retains
revocation and unconfirmed cleanup. Once in-flight registration is terminal,
cleanup addresses at most two exact snapshotted candidates, including a lost ACK,
without creating a fresh pair or clearing operation uncertainty. A close may be
reconciled idempotently; conflict never becomes replacement closure.

Explicit installer `native-access` disabled-to-enabled creates a fresh secret,
even when launcher/Mod/config path match. Unchanged enabled-to-enabled stays
idempotent; accepted rebind creates a new grant. Same-path configuration selection
changes use explicit disable/enable. Ordinary registration and launcher publication
never rotate credentials. The existing private native-authorizer callback captures
the authenticated credential generation at admission so a fresh grant cannot
authorize old pending Load/Auto. No model DTO, admission rule or action is added.

Native requests carry `Authorization: Bearer <native-access-v1 HMAC>` and
`X-STS2-Game-Instance-ID`, `X-SpireAgent-Workbench-Instance-ID`,
`X-SpireAgent-Configuration-ID`, `X-SpireAgent-Pair-ID`. Server validates constant-time
token, active pair, expiry, current saved/runtime configuration and exact current
game pair before every dispatch. Restart/config change/expiry or signed close
revokes ordinary access. No CORS, cookies, CSRF values, Hub credentials or admin token enter the
native channel. Hub and curation/use/Gold authorities still revalidate actions.
Local signed-out access is supported; local pairing does not grant team access.

Before dispatch Workbench queries the fixed authenticated game bridge
`GET /v1/workbench/native-status` with the same pair bearer/binding headers.
The bridge checks current in-process Runtime and pair; its exact response is
`schema=sts2.platform/native-workbench-current-1`, six binding fields and a
HMAC signature under role `native-current-v1`. This confirms a current pair,
not game outcome, causal settlement or job completion. No timeout/error is
converted into pair success; no native command is submitted before it passes.

## Bounded application API and view model

Routes under `/api/native-workbench/v1`:

* `GET /view?page=<enum>&limit=1..50&offset=0..10000[&id=<artifact/selection>]`.
* `POST /actions/<fixed-action>` with exact `schema=spireagent/native-workbench-command-v1`,
  `request_id` (32 hex diagnostic correlation), `payload` (original exact owning
  DTO). request_id does not invent owner idempotency or admission.

Authenticated reads/mutations share Application owner methods and validators
with browser/CLI. Native responses remove browser protection/credential/private
path fields. Reads <=262144 bytes; command body <=32768 bytes; duplicate fields,
unknown route/page/action/schema/fields and redirects fail closed.

Exact view top-level fields: `schema=spireagent/native-workbench-view-v1`,
`binding` (Runtime, Workbench, configuration, pair, expiry; no token), `page`,
`observed_at`, `availability`, `reason`, `capabilities`, `cards`, `items`,
`pagination`, `context`. Cards contain separate owner name/schema/observed_at/data
and do not claim atomic cross-owner capture. Items are bounded object metadata,
never model weights, raw recording bytes or arbitrary manifest programs.
Capabilities are static owner-advertised actions/config/source/placement controls;
unknown or absent capabilities disable writes. Context external links are typed
view+ID targets resolved only against the exact paired local origin, plus the
LocalIdentity-produced login approval URL on its configured trusted Hub origin.
No bearer or cookies appear in links.

Fixed actions and owning DTOs:

| Action | Owner/body |
| --- | --- |
| workspace.create / curation.prepare | existing Application methods, empty body |
| recordings.refresh / recordings.import | existing catalog refresh; import candidate_id + true Human attestation only where supported |
| datasets.preview / datasets.human-preview / datasets.publish | existing LocalDatasetService exact preview/publish fields |
| training.start / pause / cancel / reconcile / resume | reviewed TrainingRequest and exact operation/attempt/checkpoint/new intent/original limits |
| evaluation.start | existing model_id + source_id (+ supported bound) |
| models.export / models.register | existing model_id (+ supported environment_kind) |
| models.download / models.load / models.takeover | existing artifact_id / selection_id + run_profile |
| models.human / models.stop | owner command literal human/stop, bound current native game; recovery remains independently available |
| identity.login / identity.poll / identity.logout | existing device_name/empty/empty, credentials stay with LocalIdentity |
| collection.consent / prepare / upload | existing CollectionFlow DTO and owner consent/use semantics |
| downloads.start | exact authorized export_id through MemberClient |

No endpoints for raw shell, arbitrary Hub routes, install/deploy, process kill or
new gameplay operations. New source/remote commands require their owning packets
and are declared unsupported until then.

## One application takeover intent

`LocalModelService.prepare_and_takeover(selection_id, run_profile)` shares the
existing preparation/loading implementation and intent_generation fence. It
owns one `prepare-and-takeover` operation, loads in Human first, rechecks unchanged
intent and exact bound Runtime/client, completes Recorder handoff through the
existing owner, then sends Auto once. Human/Stop invalidates that intent during
preparation/loading. Unknown preparation/control never becomes a UI status-driven
follow-up Auto. Existing `prepare_and_load` and start preserve Human-only behavior.
No new model/package schemas or registry authority are introduced.

## Presentation and uncertain mutations

HTTP and parsing run asynchronously; Godot nodes update only on the main frame.
One visible-page read at a time, bounded retention; closed panels stop reads.
Mutation and recovery transport are separate from reads. Model Human/Stop and
existing direct Policy recovery are independent of slow catalog/training/load
requests. Every mutation is submitted once. Lost response leaves a visible
unconfirmed fence scoped to exact owner/context; status polling cannot erase it
or automatically resubmit. Auth renewal/restart preserves visible unconfirmed-command fences and selected
owner/context references; a new pair never clears uncertainty or replays a command.
Existing direct Policy Human/Stop remains available when pairing is stale.
The global external-window header is explicitly the legacy compatibility browser
entry and keeps its existing URL/health/launcher behavior. A native object's
external link instead re-reads selected current connection, verifies the existing
authenticated bounded view/context, then revalidates selection before opening only
its typed paired-origin link. Stale native links never fall back to a foreign
legacy URL. An old Python client's unsigned graceful unregister no longer clears
native access; its native slot may remain until expiry, at most 600 seconds.
Reopen reads owner state; supported explicit owner
reconcile/recovery is required. Closing panel cancels observation, not an admitted
job. Game exit leaves independent Workbench jobs alone and never promotes them
into native task completion.

## Faithful fixtures and required validation

Shared synthetic protocol vector lives at `fixtures/native_workbench_pair_v1.json`.
Python/C# tests use the same HMAC bytes and tamper Runtime/Workbench/config/URL/
pair/expiry/roles. HTTP tests cross real Application/service authorization and
body boundaries, including stale pair, browser Origin, malformed/duplicate/large
payload, no secret in public status, paused/unknown/resume, exact context links
and cancelled takeover not reaching Auto. Native portable tests cover one-shot
unknown and independent Stop lanes; presentation tests cover five-page discovery,
capability-driven fields, unavailable reasons and no hidden job creation.
Exact-game compilation requires the shared heavy slot; build identity/BOM/publish,
installation, game execution and Human qualification are outside this packet.


## Reviewed bounded pre-Runtime recovery refinement

Native `models.load`/`models.takeover` admission records the client `request_id`,
original pair binding and existing model `intent_generation` in the existing model
operation/session state. This is not a new request/job ledger. The native client
retains that exact submitted context even if its response is lost.

Only `models.human` and `models.stop` may submit a recovery payload
`{native_request_id: <original request_id>}` with the original pair bearer after
ordinary expiry, for at most 600 seconds of grace. The server still requires the
selected enabled bootstrap, exact Workbench instance/current config and original
HMAC headers. It also requires the same admitted model intent to remain current.
The final context check and model-command intent increment share the model owner
lock, rejecting replay or a newer intent. No other action gains expiry grace.
The old game may be gone: recovery may cancel that old pending load or stop its
exact owned Runtime; existing Runtime run/epoch/startup checks prevent controlling
a replacement. A fresh valid pair can use ordinary Human/Stop with empty payload.

Native loading revalidates active authorization and the original Connector game
before creating its Human-mode Runtime child. Ordinary browser/CLI loading
retains the existing behavior. A changed game or revoked bootstrap cannot turn a
pending native load into a newly owned process for a replacement game.

Before the native operation sends Auto, the model owner rechecks current native
authorization and its exact admitted context. Explicit bootstrap disable/removal
or context revocation prevents pending Auto. Auth renewal can preserve an admitted
same-instance/context operation but never clears its UI uncertainty. Recovery ACK
is still intent accepted, not worker/controller termination.
