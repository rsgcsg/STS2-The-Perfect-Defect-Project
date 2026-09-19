# Stage 1a four-configuration engineering pilot

All four configurations completed ten updates on the same public-compact model view
`2553accb6bf393809f0f813f94f73a1ccd266cdb6824a38e1591b1a1ebd478fd`.
49 eligible train decisions feed the fixed plan; dev has 16 decisions from one
independent run. No test/Gold labels or game actions are used. This is a pipeline
and comparison check, not architecture selection or evidence of pretrained advantage.

| Configuration | Top-1 | n > 1 Top-1 | NLL | Worker attempt including dev |
|---|---:|---:|---:|---:|
| `stage1a.dsimple.s.v1` | 8/16 | 6/14 | 1.720264 | 9.769 s |
| `stage1a.dsimple.pf.v1` | 4/16 | 2/14 | 1.721246 | 76.375 s |
| `stage1a.b.pf.v2` | 6/16 | 4/14 | 1.714594 | 166.611 s |
| `stage1a.b.s.v2` | 3/16 | 1/14 | 2.362603 | 8.853 s |

The B-S wrapper took 32.818 seconds (CLI 31.584 seconds including input validation).
Its worker attempt was 8.853 seconds. Different scopes must not be conflated;
research preparation, prior failed/paused attempts and validation are additional cost.
The comparison verified exact result closures and common dev identities.

## New B-S artifacts

- source: `5a4bdaddb9c5b9bc42966dbd72de58054c308384`
- run: `b795dba9af1633b72cbf7aad4b60d9fb2112c2d826acd3aa5c54b969ef4a3fb8`
- checkpoint: `12813d4179a0af2a079202100d4111e9484228c692946cdd19b46bc75915181d`
- result: `f3176e481b3a0f6ae688a47a708bd17e256c6b0eb51eafaccb867d1bff75d064`
- model: `01b60f59b01db98021c26fc77f45094c5fa3358b4923231a6836699317abb307`
- evaluation: `9512b1848b16c6cce05019db617bf910243eb9de6aac7d06a1203b774685786c`

Private evidence directory: `stage1a-packed-b-s-20260918-213508` under the research
root. The model export is `stage1a-exports/b-s-packed-v2`.

Checkpoint/published weights were byte-identical. The independent scorer loaded in
0.0646 seconds and scored the 18-candidate snapshot in 0.3362 seconds, without loading
Qwen. Its original-order scores exactly matched the restored engine. Candidate
reversal passed `atol=2e-5, rtol=2e-5`, with maximum difference 5.3644e-7. This is one
snapshot timing without repeated latency statistics, not full-game latency.

- Comparison receipt SHA-256: `13ac5a2f7f9ad59332fe182ea2d847418482f456ce8992a9c8e720358971cbb1`.
- Export receipt SHA-256: `02055edbcbdb3a41622a3e8f9fff2d450075abd6ff476821a9e1dce227fa832f`.

## Game-entry integration work

An additive trusted `token-v1` adapter now connects public snapshot exports to the
existing decision-only NDJSON protocol. Workbench supports an operator-created local
catalog alongside shipped S1; its commands and Runtime package remain fixed by code.
No model has yet been registered for actual native use. Existing S1 support is unchanged.

Focused token-port and Workbench tests passed (101 tests, followed by 16 token-port
checks including an additional no-ML-import regression). A private new-process test
produced ready and a complete 18-score decision from the real B-S export. Public
TypeScript consumer verification is pending: the active worktree had no installed
tsc/dependencies. The dependency/build, consumer and full Python gates will run as
one durable, separately monitored job. No native game qualification follows from these
source, export or protocol checks. Local registration with current game identity,
Workbench/Mod loading and Human/Stop/model-switch controls remain outstanding.


### Integration-check environment failures and isolated-fixture repair

The exact source `c69d59c9210b7e8a7dc1eaa8f5e0591127510b0f` passed the real-export
public Runtime schema check and the Platform portable suite. The first job omitted
the interpreter bin directory from PATH; its retry fixed this but Python's isolated
import test correctly found the borrowed s0 environment still installed the old
worktree. These are retained failed attempts, not successful full-gate receipts.

A dedicated stage1a environment was created from the unchanged lock. Isolated
imports and the real B-S export/TypeScript consumer check passed. However, exporting
`UV_PROJECT_ENVIRONMENT` over the entire job redirected the offline cloud-refresh
fixture's nested `uv sync` into the caller environment. It replaced the caller's
installed dependencies, causing 110 failures and six setup errors in the remaining
suite. This was an environment-isolation failure, not 116 independently diagnosed
model failures. Training artifacts were not an installation target.

The job-wide override is removed. The synthetic refresh fixture now strips that
override and `VIRTUAL_ENV` from its own subprocess environment. A new offline
regression injects a foreign target, performs both dependency versions in the
fixture clone, and verifies that the caller target remains untouched. The cloud
refresh, isolated import and packed-model regressions passed together (28 tests),
then isolated imports of the current worktree, Torch 2.13.0 and Transformers 5.15.1
still passed. The original test assertions remain intact. A complete Python gate
on the repaired source is still required; prior Platform evidence retains its own
source identity, and native registration/game qualification remain outstanding.

Private failed attempts: `stage1a-integration-check-20260918-214945`,
`stage1a-integration-retry-20260918-215115`, and
`stage1a-python-env-check-20260918-220513`. The last directory retains the successful
new-environment real-export contract receipt as well as the failed overall status.

### Repaired full Python gate and local registration preparation

Source `e5f925b101256ec584ebb90e35a0c86b2cbe36c4` passed the complete Python
portable gate from its own locked environment: 1,122 tests passed, three skipped,
21 subtests passed; lint, typecheck, engineering checks and wheel/sdist packaging
also completed. Total job time was 133.421 seconds. The private receipt is
`stage1a-python-gate-20260918-221415/status.json`, SHA-256
`4cd716f1b771dcfd6ebc3eb72478c5d5744cb10a5a5101e12ca12bd0cc33ac7e`.
The earlier Platform pass remains bound to `c69d59c`; this is not hosted CI or
native-game qualification.

Four private token registrations now point to the verified exports and current
observed native environment. The loaded Mod SHA/MVID match the retained curation
acceptance artifact (`52bcd795…ae0e`, `9db537ec-840b-4e2e-aa85-bea9fab6f4f1`).
Registration admits only whole `combat_turn` decisions containing `play`, `use`
and `end_turn`; no candidates are removed to fit this scope. Broader game surfaces
remain unsupported by these registrations. No full-run support is claimed.

The current game process reports `artifact_unqualified` and
`execution_available=false`: passive observation is available, model actions are
not. Its recorder is Ready with no open session. A cold launch through the owning
Game Mod lifecycle is needed for explicit exact-source execution admission. Do
not weaken compatibility checks or rebuild an unchanged Mod to hide this state.
The running released Workbench has not yet been switched to Stage 1a source;
pinned Runtime installation/readiness, Workbench load and game controls are next.

### Cold load and B-S Workbench load

After the owner closed STS2, the retained exact Mod's lifecycle launcher cold-started
the game without replacing its installed bytes. `verify-loaded` passed; Connector
reported `canary_exact` and `execution_available=true`, runtime instance
`343a26af3a704d028e520edff2dd78ef`. This is process-local exact-source canary
admission, not new general artifact qualification.

Workbench was gracefully restarted from source
`07d82eb9b5c49096d196e9c32028d5c63e203edf`, using the same project configuration,
account state and delivery queue. The pinned Runtime rc.4 installation passed all
four registration readiness checks. An initial B-S load in Human mode was stopped
before any decision; the cold process's changed observed Modset fingerprint was
bound in new manifest files, preserving the prior files. The second B-S load passed
the actual Workbench prepare/load and adapter attestation path:

- Runtime run: `run-4595d3c0-3298-431a-b412-221007898cbb`.
- Manifest: `stage1a-b-s-live-pilot-20260918-36df5154`.
- Runtime code: `0a436e79dab4833223a6fb9b3ccd9ba600111f44cd5b9d119b32ebe46aa071ec`.
- State: loaded, Human mode, controller released, untainted, no Runtime errors.
- `/v2/environment` confirmed the same game instance and recovery epoch 0.

Private evidence: `stage1a-live-load-20260918-222647`; loaded-status SHA-256
`3b52b8617c937b271dda2c7e8bbee92c845679ff345565dce38892162550a8d1`,
environment receipt SHA-256
`7cd59d07b40bbd188e28333a0fe4413add8c23ea7f26ab169dea14ba974e522e`.
The browser was opened to the model page; automated visual inspection was unavailable.
No native model decision has yet been executed. Game UI visibility, one-step delivery,
pause/recovery and model-switch operation remain pending user-assisted acceptance.

### Owner-triggered Auto attempt: bounded delivery, unresolved integration defects

The owner subsequently reported completion and poor interaction flow. The same
run's raw log has 22 events: four decisions and four receipts, three `delivered`
(two card plays and one end-turn) and one `not_delivered` because the exact snapshot
changed. These are directly inspected producer records, not a passed evidence
verification or settled causal-transition claim. The log records two Auto entries,
an `action_not_delivered` handoff, a later `successor_not_stable` taint, Human mode
and Stop. Do not call this successful continuous control or complete Stage 1a.

The installed Evidence verifier rejects the run with `schema_keys`: "receipt
successor read contains unknown or missing fields". Preserve its raw bytes; inspect
the current public Read contract and reader version before any repair. Workbench
also records a subsequent failed load with `runtime_port_already_in_use`; current
inspection finds no listener. Inspect stop/restart port lifecycle rather than asking
the owner to repeatedly reload. No automatic retry or untaint was performed.

Required follow-up is owned by Runtime/Connector lifecycle, Evidence contract
compatibility and Workbench restart handling respectively. The in-game interaction
requirements are recorded in the root UI specification. No new training, release,
UI deployment or full-game support is claimed by this follow-up.

### Source repair candidate, not yet deployed

The Evidence failure was reproduced on the unchanged native log. Public Connector
SDK `readSchema` permits omitted/null `target_referent_id`; the Python Agent-run
reader incorrectly required it. The corrected reader preserves strict required and
unknown-field checks and target referent validation. Its nine focused tests pass,
including omitted/null/invalid targets and extra keys. Reading the unchanged native
run with the source candidate now passes evidence integrity verification. This
does not remove its recorded rejected action or taint, nor prove causal settlement.

Runtime now re-observes and re-scores only after a correlated explicit stale,
retry-allowed non-delivery, with a three-consecutive-rejection bound. Unknowns and
other failures still stop. Successor observation uses a fixed 250 ms interval,
41 samples by default (10 seconds of scheduled waiting plus HTTP), rather than
the old 375 ms scheduled wait. Polling is interruptible by Human/Stop and does not
nest stale retry budgets. Focused regressions cover a six-second animation,
exhaustion, recovery during waiting and fresh request/action identity; 69 Runtime
tests passed. No polling result is promoted into a causal Human transition.

Workbench's port check now matches reusable closed TCP connection semantics on
POSIX while rejecting a live listener. The real-socket regression passes. An
additional test-isolation defect was found: a catalog test inherited the operator's
private registrations. It now reads only the shipped registry; both focused tests
pass. The initial broader run retained one failure and 86 passes before that fix.

Full gates, versioned Runtime/Evidence package pins, live restart and repeat native
acceptance remain pending. Existing installed packages and prior failed reports
are not overwritten by source-only verification. In-game one-click load/takeover
is the subsequent implementation step, not a delivered feature in this candidate.

### Repair gate: portable typing correction

The `stage1a-live-repair-gate-20260918-225354` job at source
`9171e830742a645fed51bb510f8589a8dee7af81` passed the Platform portable gate
in 31.94 seconds, then stopped at Python mypy: POSIX socket stubs do not expose
Windows-only `SO_EXCLUSIVEADDRUSE` under an `os.name` guard. No Python test result
is claimed from that failed job. Use the type-checker-recognized `sys.platform ==
"win32"` guard while preserving the same exclusive Windows bind and reusable
POSIX bind behavior; do not suppress the check or weaken live-listener rejection.

After this correction, mypy passed all 218 source files, Ruff passed the changed
module, and all 87 local-model tests passed in 22.31 seconds, including the real
listener/closed-connection regression. These are local macOS checks, not a Windows
runtime qualification. Retry the Python component gate separately; Platform source
has not changed since its passing gate. Package delivery and native acceptance
remain pending as described above.

The Python-only retry `stage1a-live-repair-python-20260918-230957` at
`67fb8bc472cfed1893f0ec06f13eda236f04ddad` completed in 124.56 seconds:
1123 tests passed, 3 skipped, 21 subtests passed, plus the component's lint/type,
Connector SDK, CPU E2E, worker smoke, build and patch checks. The prior Platform
pass remains associated with its original source; this Python-only correction
did not alter Platform component source. Runtime rc.5 and Evidence rc.13 are the
next immutable repair candidates; version declarations alone do not mean they
are built, installed or live-qualified. Installed rc.4/rc.12 remain unchanged.

### Versioned repair dependency delivery

The `stage1a-repair-packages-20260918-231916` job at
`07d0e73da89d9767ebebcc4d886b723e48e9d44c` passed the Platform candidate gate
in 32.09 seconds and built both packages (34.08 seconds total). Candidate assets
are published under `candidate/stage1a-live-repair-20260918`, explicitly a
prerelease, not the default release or a native qualification. Downloaded assets
match the built bytes:

- Runtime rc.5 archive SHA-256:
  `028bfd53295142799d342bdb0ad31baf24f2ec4eb32e6242c1f3ee2d54976fed`.
- Evidence rc.13 wheel SHA-256:
  `a85504a45c6ad19a9b2811e15330bb1c5efa8fd63fbfb810fad7dd064dff9dad`.

The owning Runtime installer verified rc.5 and its dependency closure in the
local Workbench's private installation; it did not activate gameplay. The Python
dependency is pinned to Evidence source
`17bffb3b69dd8f414e6aec2306c47c63d11cfa39`, with the updated lock and Workbench
composition. The installed rc.13 verifier passes the original failed Agent-run
bundle, with all six original file hashes unchanged. This is a new verification
of old bytes; the prior failed report and recorded taint remain historical facts.
The 117 focused installer, local-model and token-port tests pass after dependency
installation. Application integration, reload and continuous-game acceptance
remain distinct subsequent checks. Existing Mod and collection-tool bytes are
unchanged; the immutable collection outbox retains its own tool association.

The installed-package integration exposed a further reload blocker: the old
session remained `runtime_exited`/recovery-required despite a sealed terminal Stop.
Its recovery demanded today's package and model manifest, making it impossible
following an upgrade. The Workbench now permits an explicit Stop to retire a
verified finalized old run with a terminal Stop and unoccupied port. It archives
the previous session and keeps the recorded taint. Seven faithful regressions
using synthetic finalized Evidence bytes pass, covering old/removed registrations,
retained taint, missing/tampered/mismatched evidence, absence of terminal Stop and
occupied port; mypy (218 source files) and changed-file Ruff pass. This does not
claim recovery from unsealed unknown delivery or native continuous-game acceptance.

### Installed version mismatch found and corrected

The explicit sealed-Stop recovery succeeded on the original 22-event native log,
retaining `tainted=true` and creating a separate passing integrity report. The
next Human-only load rejected rc.5: its package version was new, but the exported
Runtime version still said rc.4. No model action was enabled. The failed startup
and incomplete run directory remain retained. rc.5 must not be recommended for
Workbench loading and its published bytes are not replaced.

The replacement rc.6 synchronizes the exported Runtime version and Connector
product identity, with an installed-package assertion against package.json (the
previous smoke compared two copies of the same stale constant). Runtime's 69
tests, type/build checks and installed CLI start/seal/exit smoke passed. The
Workbench also clears a prior run's startup/evaluation when creating a new
Human-mode child, and does not mislabel a failed attestation as a running-session
recovery problem. Its focused failure/recovery regressions pass; application and
native integration still require the subsequent gates.

### rc.6 installed reload acceptance (2026-09-19)

The rc.6 candidate archive SHA-256 is
`16e1caec78bfaefe5ab0635a8888c6d945325dfa3acb6f8d979a376ad05abdc8`,
source `9701b7530a94422fd8d7ae9296d3aee88fbdc4b3`. The owning installer downloaded
it from `candidate/stage1a-runtime-rc6-20260919` and verified the full package and
dependency closure. Runtime code SHA-256 is
`2b78f8bc6fc75fd2bef51af1d61d095be9c711d03509df6c81ffc96dfd6c7f6b`.
Evidence remains rc.13. Workbench source for the reload is
`c1c16cf` (full SHA retained in the private source/job records).

Using the same B-S model artifact and the new explicit adapter/lock manifest:

1. Load `run-90af8071-501b-4c99-9da5-0ed9eec15ab5`: Human mode, controller released,
   rc.6 startup identity matched; no decision or Receipt.
2. Explicit Stop: lifecycle stopped, finalized evidence verified, exactly one
   stopped event. Evaluation
   `18c9e0b51c38ceeb90d2587d4a496feeb4f55a94a6654922b7ca0e6f926ce295`.
3. Load again `run-961524ff-89f3-4e6c-8eda-aadd71898784`: Human mode, released
   controller, no errors or taint. No port-in-use or old-session recovery blockage.

The unchanged installed Mod cold-load was verified through its owning lifecycle;
no Mod rebuild or gameplay action was performed in this acceptance. All 113
focused local-model/sharing/installer tests passed before the additional startup
state cleanup; its eight focused failure/recovery tests and full mypy then passed.
A final integration gate is required on the complete source and dependency pins.
Continuous native actions and in-game one-click model selection/load remain pending;
this reload evidence does not establish either.


### 2026-09-19 Integration gate test isolation repair

At source `75e264637e9a2abdbc61665a5c9fc00fcf4229f0`, Platform portable passed (31.96 s). Python pytest reported 10 failed, 1120 passed and 3 skipped: installation and fake-process startup tests probed the operator Runtime on fixed port 15527. The live Human-mode Runtime exposed a test isolation defect.

Installer tests now redirect the declared fixed-port probe to real ephemeral sockets; an occupied-listener regression still requires installation refusal before npm or package staging. The fake-process attestation test isolates its port prerequisite. Production guards, model artifacts and live Runtime were unchanged. The two affected files yielded 108 passing tests plus one new assertion failure about the lock directory; correcting that assertion to check package/staging preservation gave 16/16 focused passes. Python full validation is pending a fresh durable job; this is not continuous native gameplay evidence.
