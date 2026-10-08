# E6 native Workbench source candidate

2026-10-08. Workstream E6; branch `codex/e6-v1-native-workbench`.
Original base `c8940b26079044dbc00ebd1c14e89214160eff9f`; source draft
`64577056`; normal dependency integration `26bd63f00378a71ff14dd2a4808957b2b11cd248`
consumes independently reviewed `f357275e809c81b3caf040118f1e7159cdadce9d`
(and its previously authorized `4b988900` ancestry). The design was reviewed
before implementation: [Native Workbench v1](../design/NATIVE_WORKBENCH_V1.md).

This is a source candidate requiring independent review. It changes G2 local
contracts/composition and G3 native presentation, and touches G4 model lifecycle
and selected-launcher setup. It does not complete their install/runtime gates.
No cross-repository pin, model math, source/use/Gold owner, gameplay legality,
Connector action catalog or native Commit authority changes in this packet.

## Resulting behavior and ownership

The existing Godot panel has native `play`, `data`, `training`, `models` and
`settings` pages. A fixed bounded native API presents the same Python Application
and existing dataset/import/training/evaluation/export/registration/model/account/
collection services. Each owner exposes its own observation time, capabilities,
progress, errors, identifiers and outcome; no second worker or job ledger exists.
Unsupported new-source preparation and remote execution remain explicitly
disabled pending their owning adapters and compatible owner capabilities.

Native access requires explicit selected verified launcher setup and loaded Mod
artifact binding. The private bootstrap is distinct from browser/admin/Hub
credentials. Python and C# share one synthetic HMAC vector; pair proofs bind the
current game, Workbench instance, saved/runtime configuration, exact loopback
origin, pair and bounded expiry. Current signed game confirmation precedes normal
dispatch. Public health/status and links do not contain native credentials.

Each mutation is submitted once. Unknown replies retain the original owner and
context fence across pair renewal; observation never retries an operation.
Model Human/Stop have an independent lane from reads, training and model loading.
Only an exact previously admitted native model request can use its original
expired proof for Human/Stop during at most 600 seconds of grace. Its intent,
Workbench, config and original game binding must remain unchanged; a replay or
newer intent is rejected. Existing direct Policy Runtime recovery remains present.

The existing model service owns one prepare/load/takeover intent: it loads in
Human mode, rechecks exact Runtime/game/recovery epoch and Recorder handoff, then
sends Auto once. UI polling does not chain those actions. Human/Stop invalidates
the intent. Native authorization and original game are rechecked before creating
the Human child and again before Auto; bootstrap removal/disable prevents pending
takeover. Ordinary browser/CLI Human loading remains unchanged.

The downloaded model catalog consumes the reviewed closed schema helper for
explicit v1/v2/v3 structured models. Recognition only advertises required export
and registration; it never creates a loadable policy or starts a Runtime. Unknown
versions remain unsupported. Domain package and registration validation stays with
the existing reviewed owners.

## Source checks

On the final source content, before this documentation-only receipt:

- Nine Python suites covering native access/API/takeover, legacy registration,
  model lifecycle/catalog, scoped Application, launcher, console and Workbench:
  **307 passed in 63.63 seconds**, no skipped tests. Synthetic local inputs only.
- Native contract plus existing console JavaScript tests: **229 passed**, no
  failures/skips, 0.661 seconds.
- `npm --prefix apps/ingame-ui run check`: **26 passed**, no failures/skips,
  2.052 seconds; includes the actual embedded portable C# fixture invocation.
- Direct exact-game unified Mod compilation against the local STS2 SDK:
  **0 warnings, 0 errors**, 3.57 seconds. This was compilation only, with no
  release identity/provenance or package acceptance claim.
- Changed Python Ruff checks passed; Mypy passed six changed production files
  from the configured Python project directory.
- Root project-system, governance and dependency-boundary gates passed;
  `git diff --check` and `npm run project:closeout` passed.

An earlier broad run exposed four existing lifecycle-test fakes that did not
accept the new `access` keyword. The fake was updated to assert the exact
Application instance/config/path; all ten lifecycle variants then passed. The
final 307-test receipt above includes that correction. An initial Mypy invocation
from the root omitted the Python project configuration; the corrected project
invocation passed. Neither event was treated as runtime evidence.

## Remaining gates, rollback and non-claims

Independent review of `d6320465` found one P2: ordinary `LocalModelService.start`
retained a failed native request's context and intent generation. Its old expiry
grace proof could therefore recover a newly admitted browser/CLI start. The
bounded repair advances the new ordinary intent and clears its native context
and authorizer in the existing model-owner admission callback, after rejection
checks under the owner lock. Rejected starts preserve the original pending native
intent. Paired native prepare/load and ordinary Human-mode loading retain their
existing behavior. The source checks above precede this repair; its exact head
requires fresh Python validation and an independent paired-Application probe.

The intent repair `c5e655d0` passed 187 writer Python tests; independent review
reproduced the original paired-Application path and confirmed that the expired
old request is now rejected after ordinary start. Its 45 native access/API/
takeover/registration tests passed without additional findings.

Lead review then identified a presentation defect: a confirmed recording refresh
or account poll can leave owner IDs and capabilities unchanged, so the disabled
submit button was not rebuilt. The narrow follow-up invalidates the cached form
key after any completed write. Only the next successful authenticated owner view
rebuilds forms using their existing capability and command guards; drafts/focus,
uncertain-command fences and stale-pair behavior remain unchanged. No button is
unconditionally enabled and no command is automatically submitted. Source guards
and actual portable command-transport checks cover explicit repeat refresh/poll
after confirmed replies, in-flight exclusion and unknown recording-refresh fences.

The lead must independently review the exact committed candidate and compose it
with the accepted native input/provenance/AgentSession/application packets. Root
STATUS/CURRENT, contract versions, component identity and BOM updates remain with
the integration owner. Full root checks and exact packaged artifact verification
remain integration gates, not implied by this bounded source receipt.

Rollback is the existing single topic-branch revert before promotion; production
would disable/remove the selected private native bootstrap and retain existing
direct Human/Stop. Closing the panel cancels observation, not admitted jobs.

No bootstrap was provisioned on this machine. No installation, game launch,
controller acquisition, actual rendered native-page inspection, real-data import,
training, provider call, upload, package publication or external Git action was
performed. Synthetic tests do not establish Human origin, native completeness,
causal outcome, model quality, a full product journey or G2/V1 acceptance.
