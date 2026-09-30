# Confirmed interaction memory: source and package receipt

Date: 2026-09-30 (Australia/Brisbane). This is a candidate receipt, not native,
Human, policy-quality or complete Stage1a acceptance. Final PR head and hosted
results belong to the PR and its Checks, not a self-referential commit here.

## Scope and exact source

Base: accepted `develop@a887e4f1c75eb48066d00c49b279abee51abd30c`.
Its tree `5ff4b641ac5c6f072203c9b782c8877c2adae86d` equals accepted PR117
head `de23ce167fddc5bfe149cd29f2773299b261845d`. PR118's evaluation-summary
metadata is included through normal ancestry. Their full receipt is
[run 36641484349/1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36641484349);
it does not qualify the new candidate. Integration run
[36644990717/1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36644990717)
succeeded at `a887e4f1` via `verified_execution_same_tree` reuse, with fresh
repository guards/portable and both OS jobs unselected. It is not a new full.

| Owner | Candidate source | Tree / source SHA-256 |
|---|---|---|
| Policy Runtime rc.14 | `e1944a40e92427ecb568b9308cd473b715d6df07` | `9972f91bb8e5e988c5f50b0e44431b04ef692441` / `8b2e11f195a84e23e26fa8d4d3fe87ec9b8f1abc8ec37626fd44bf7e0e63f8e4` |
| Evidence rc.23 | `492b9de7a402ad6072c78ee09c9bf0a53b5fbf08` | `64be9ccd75aff1d0ee28e5524655aba7bc561f6d` / `d52faa68f67a67cf2bad801316dbaf74bcf28e4fa8fabdfe19e1a077294bb059` |
| STPD / Workbench consumer | `a1ca442ccc78f99989e9f3c7796422fbc63e64b3` | Normal-merged into the candidate; six existing model-engine files unchanged |

G4 cross-owner consumer/protocol change: opt-in decision-only NDJSON port 3,
versioned history projections, strict recorded continuity and completion echo,
and confirmed prior-input tokens into existing D-Simple memory. Platform never
encodes model memory, infers game effects or supplies hidden facts. Connector
still owns the full legal catalog and execute-time checks. The current page is
unchanged; binding IDs do not enter model text. See the
[design and boundaries](../plans/M2_CONFIRMED_INTERACTION.zh-CN.md).

## Reproduced defect and repair

At Runtime parent `88ec380e`, faithful tests paused durable dispatch-attempt
append, requested Human or Stop, then resumed append. Both regressions failed:
Connector submit was called once after recovery. The repair checks the recovery
epoch immediately before submit, and writes a distinct
`text_menu_dispatch_cancelled` record if recovery intervened. It fabricates no
Receipt, never marks an unknown submission as not-delivered, and keeps the
already-in-flight submission classification unchanged.

The old Evidence verifier rejected the new event with `unsupported_event_kind`:
the pre-change focused run had 57 tests with 12 subtest failures. The final
verifier requires exactly one prior attempt, no result or causal successor,
and the exact cancellation reason. Evidence-write failure still taints.

Agent replay now uses the exact context actually echoed by the adapter and
persisted by Runtime, rather than guessing the latest outcome or treating every
mode record as a memory reset. The confirmed request is consumed once within
the same continuity. Late outcome, wrong binding, cancellation and recovery
cannot inject history into a new request.

## Executed checks

These are local source/synthetic checks, with no game contact. Commands below
ran in isolated source trees; stdout/stderr retained locally. The independent
reviewer read the exact final sources, original logs and installed-package
identity. Review is not a replacement for hosted CI.

| Check | Source / result |
|---|---|
| `npm --prefix components/policy-runtime run check` | Runtime `e1944a40`: typecheck, **163 tests**, build, deterministic package and installed smoke; exit 0 |
| `PYTHON=<private Python 3.11> npm --prefix components/evidence run check` | Evidence `492b9de7`: **183 tests**, exit 0 |
| STPD focused 11-file pytest run | `a1ca442c`: **175 passed in 17.83s**, exit 0 |
| Ruff / mypy | All 17 changed Python files / 15 non-test source files; exit 0 |
| Final consumer combination, 13-file pytest run | Clean executable source at integration `b3d944219d9da9d579465180053bdcaad208769b`, with documentation/BOM-only edits during testing: **160 passed in 47.39s**, exit 0 |
| Locked environment | Private Python 3.11.15, pytest 9.1.1, torch 2.13.0, Evidence rc.23 from `492b9de7`; project imports from isolated candidate; `uv lock --check --offline` exit 0 |

The final combination command was:

```sh
python/.venv/bin/python -m pytest -q -ra \
  python/tests/test_confirmed_interaction_memory.py \
  python/tests/test_memory_sequence_bridge.py \
  python/tests/test_memory_scorer.py \
  python/tests/test_memory_policy_port.py \
  python/tests/test_memory_v2_policy_port.py \
  python/tests/test_memory_v2_profile_foundation.py \
  python/tests/test_memory_v2_run_export.py \
  python/tests/test_workbench_memory_recipe.py \
  python/tests/test_local_memory_model_export.py \
  python/tests/test_local_memory_registration.py \
  python/tests/test_local_model_registration.py \
  python/tests/test_local_memory_evaluation.py \
  python/tests/test_workbench_memory_v2.py
```

No skips were reported by that run. It is not the full Python or dual-OS gate.
An earlier worker run had two legacy v1 registration failures because the new
default keyword reached old callers; the final implementation omits that
keyword for the legacy path. Those failures are retained, not reclassified.

The first root BOM check reported six failures from the same stale
`current_component_*` Runtime relation (rc.13). Updating only those current
relation fields to the computed rc.14 identity fixed all 15 BOM tests. Historical
source/build/runtime/Human fields were preserved; no check was weakened.
`npm run check:repository` then passed with exit 0, including identity/BOM,
CI/router contracts, boundaries and import history. It reported one advisory
`current-context-growth` warning for CURRENT; no failing gate or skipped gate
was converted into success. `git diff --check` and `project:closeout` also
completed with exit 0; closeout is an impact report, not a full gate.

The retained installed Runtime package SHA-256 is
`a314ae6571d70457ab54fd2de9684bfc1014a34a7c7fbaeffdb93a491b604e5a`.
Two real installed-package synthetic bundles passed the final public Evidence
verifier with no findings: normal 13 events, content
`aebb3af7e5bb9d1f35652d7528d9297a2d32e9d6ab89636dd06483b711ac4ad8`;
cancelled 9 events, content
`5065f4465327ca9676af5afe481cffb91f5298ac6f28f055f800e00f20de430f`.
These use synthetic policy fixtures and do not establish native model behavior.

## Cross-process combined probe

A separate bounded probe used the actual installed rc.14 package, real
`NdjsonPolicyPort`, real Python `stpd.policy.memory_port` child and
`OnlineM2Scorer`, through the actual Connector SDK to an ephemeral synthetic
HTTP Host. An existing tiny CPU synthetic training/export fixture supplied the
model, not private data. Two ticks navigated, with two submissions and two atomic
observation-context reads (zero legacy snapshot reads). The second recorded
input echoed the first confirmed request on the same continuity token.

The final immutable bundle has 12 events, `status=stopped`, `tainted=false`,
content ID `14a440e0e24551a84055dcabea721aee28db95ed2d1dd759fdde9c1d66e4ab1b`;
the rc.23 public verifier returned `pass`, `findings=[]`. The exact STPD child
code digest was `3070354a8f42f4ad4314ae90303cbd958caa4535a8a6576d5f8128c033459d22`.
The harness deliberately labels runtime identity with a fixture digest; actual
installed source/package identity is checked separately. This is not an
attested production run or native delivery evidence. Initial harness capability
and result-shape failures were corrected in the fake Host only, with no product
source changes. No fixed sleeps or substituted Runtime/scorer were used to
make the assertion pass.

## Accepted local installation, separate from this candidate

The local Workbench was explicitly moved to clean accepted `de23ce167...`,
with its private locked Python environment, original selected research workspace
and records/models retained. Old-process shutdown first left its OS lock held;
setup correctly refused without mutation. Setup resumed only after the process
exited and the lock could actually be acquired. No lock file was blindly deleted.

A local Mod build from the same accepted tree was deployed, launched and verified
loaded: game `v0.111.0/41cef1ea`, Mod DLL SHA-256
`188d080e15712ddadf461c5aa883534464b3b393eb37dfb6f3ea1e7dbcbdd9df`,
MVID `23880303-49fd-4f11-be2d-1ad7c5ff471f`, compiled source
`afe61173cc47a6f871801e9be00709b3c9255fe911faeadfcfab83f5eb28a3ad`.
The matching local CollectionTool release is
`a4ae13461633f51cfa4cebc85ddec12b630db61c6dae4f459a05140ce4d89af9`.
The Platform button opened the no-login local Workbench home; its original
library remained visible. These are local load/UI observations, not Human
capture or fresh-member distribution qualification. The previous deployment
and config backup remain available for rollback.

The new port-3 candidate is not installed in that running application. No public
package, release asset, model weight or private recording was published.

## Human-history dev evaluation

Independently reviewed consumer commit
`33f1d1d632f2cfbbe8ab8cb19330457f92ef6164` adds the missing dev path for
the Human confirmed-interaction profile. It replays the exact stored training
projection, tokenizer, source map and episode bytes before evaluating a separate
admitted dev source. Omitted settling limits derive from that pinned profile;
an explicit incompatible limit fails before the Workbench operation starts.
The old observation-only profile keeps its previous default and report format.

Overlap comparison for history inputs includes the actual prior-action tokens
with the page and full candidate menu, excluding the current label. Managed and
Agent sources remain outside this Human evaluator. Evaluation does not create
an optimizer or alter model weights. It does not turn a distinct recording ID
into proof of independent-game data.

The final isolated six-file candidate passed 114 focused tests in 3.40 seconds,
Ruff on six files and mypy on four source files (all exit 0). Before the repair,
the two new focused cases failed with `train_projection_identity_mismatch` on
the old evaluator. These are synthetic source/test results, not new real-data
dev scores. The consumer was normal-merged into this packet; its combined
hosted gate remains required.

## First hosted failure and owning correction

[Full run 36647445252/1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36647445252)
at `ea06ab2c0dc3deedfccc524173dc713c13a40484` completed with failure.
Linux succeeded; Windows had 2 failed, 1962 passed and 40 skipped tests;
portable failed. Tested merge `2b376dca4c723735bcf967d7b9e5f102b41091d6`
had the candidate tree `f8399006d3b525cca0e137e34ad4d6a046ad305d`.
This failed result is retained, not replaced with the local short checks.

The first failure exposed a real Workbench Managed-session startup race:
after durable activation, a submit could advance the session to `unknown`
before startup cleanup checked the mutable `status == active`. Cleanup then
closed an already handed-off client and prematurely published a failed report.
A barrier after the active save reproduced `unknown -> failed` on the parent.
The correction binds the completed handoff to the exact session/client rather
than the later status. Explicit Stop still owns finalization. Failed original
client cleanup remains owned and unresolved, including Stop during construction;
a replacement client's successful close cannot prove the original child exited.

The second failure was the existing HTTP training test's second explicit run:
`interrupted_unknown` / `training_storage_or_process_error`. The hosted log and
JUnit did not retain the underlying parent exception, so its cause is **still
unknown**. A bounded private parent traceback and sanitized exception type,
errno/winerror, stage and source callpoint now make a future failure diagnosable.
Only sanitized fields enter the test failure output; raw messages, private
paths and child logs do not. Public status, recovery, training and retry behavior
are unchanged. No timeout or assertion was weakened, and no old run was rerun.

The isolated correction/diagnostic candidate
`53586647b2db6e7c1c4f0e96e75d8b67ea109d6c` passed both affected Python files:
64 tests, with scoped Ruff and mypy passing. Its initial baseline failure and
two new diagnostic-test assertion failures (Python specializes `OSError(13)`
to `PermissionError`) are retained. Fixing that expected exception type does
not explain or resolve the second Windows failure. The new combined hosted
candidate must run its own selected full gate.

After normal-merging the evaluator and correction, the six affected consumer
test files passed together at executable source
`6a3bc72f9d6ea31c6999997a801c8ef62107c98f`: **121 passed in 38.68 seconds**,
exit 0, no skips. They were `test_local_environment.py`, `test_local_training.py`,
`test_memory_evaluation.py`, `test_local_memory_evaluation.py`,
`test_confirmed_interaction_memory.py` and `test_workbench_memory_recipe.py`.
The tree was clean at test start; only CURRENT documentation was shortened
during execution. The first repository check correctly rejected CURRENT at
8300 bytes against its 8 KiB limit; the summary was condensed while the dated
receipts retained their details. A follow-up check also caught two required
safety phrases removed during that edit; both were restored. No governance
limit or required token was changed.

## Remaining boundaries

- New Agent history requires recorded context. Old Agent archives without it,
  and new offline episodes containing repeated snapshots, are explicitly
  unsupported; online no-history same-page reads remain cached and do not
  advance memory. This limitation is not hidden by dropping labels.
- New Human history still means the last available completed input witness,
  not a claim that all user inputs were captured or that it caused a successor.
- Human-history dev evaluation is now implemented; its combined hosted gate
  and a real-data result are still separate. Existing K1/K8/Reset dev scores
  keep their old observation-only identities and cannot qualify the new input.
- The [accepted row-2 mouse capture](STAGE1A_ROW2_MOUSE_CANARY_2026-09-30.md)
  now has a passing producer audit and typed bundle verification. It retains
  one unresolved final successor and does not qualify this candidate's model.
- No independent-game benchmark, roughly-10k training run, memory benefit,
  Qwen/Z/O result, general checkpoint/MCTS or complete Stage1a acceptance follows.
- Existing consumer bundles/pins are not replaced with a nonexistent public
  Runtime rc.14 asset. Opt-in registration retains actual Runtime admission.
