# E4 explicit scoped application recipe, source candidate

Task E4, G2 application/contract composition. Owning worktree is
`SpireAgent-v1-scoped-application`, branch `codex/e4-v1-scoped-application`,
explicit dependent base `4b98890035365d3556966704fa86d74a7db29657`. It includes
reviewed scoped domain identity and checkpoint/export provenance fixes. This
packet is a source candidate; complete G2/V1 still requires owner approval.

The first incorrect fact was that ordinary application training still selected
legacy run-v2 identity after scoped run-v3 existed, and downstream application
and fixed-evaluation admission recognized only the old model-v1 artifact.
The existing owners now provide explicit opt-in rather than altering old APIs:

- Static recipe `structured-m2-cpu-v3` selects `TRAINING_SCOPE`; v2 and the
  ordinary default remain unchanged. Capability discovery stays tensor-free.
- The existing training journal caches the actual current source producer per
  owned scoped attempt. The child checks it independently, prepares the scoped
  run and executes with original `run.producer` plus current `attempt_producer`.
  Resume/reconcile derives expected schemas from the trusted recipe and frozen
  run. Parent callbacks require exact current event/checkpoint publishers;
  selecting old checkpoints verifies terminal historical writers and events.
- Closed model/package mapping admits v1/v2 to package-v1 and v3 to package-v2.
  Scoped own-package admission binds model metadata, actual exporter and exact
  run/input/checkpoint IDs without reading unavailable private ancestry. The
  fixed evaluation domain edge changes admission/binding only; scoring,
  replay, optimizers and numerical algorithms are unchanged.
- Export/register/fixed-evaluation/summary services use that closed mapping.
  Registration delegates structured identity to the versioned domain validator
  instead of comparing every package to a whole-Python digest. The existing
  curation owner retains original-source/use/Gold/split/overlap admission and
  no clean held-out claim. No competing journal or automatic retry is added.

The actual synthetic private-child journey uses a clean copied-source Git
fixture, with the current production owners and two public S0 offers. The v2
service produces model-v2/package-v1 and follows real export, binding and fixed
model dev evaluation. The scoped service pauses at publication checkpoint A,
commits an unrelated Workbench change B, resumes/export as B, then commits C and
reconciles the existing result without new immutable artifacts or producer
rewrites. Registration remains valid after another unrelated application edit,
and fixed evaluation succeeds. A detached scoped cache installs after its
private training-input manifest is removed. Runtime capability/SDK validation
is explicitly synthetic in the registration test; no game is contacted.

Negative tests reject a v2 checkpoint in a scoped operation before a new attempt,
wrong model/package versions in both directions, unknown/non-string model
schemas, and exporter relabeling. An actual child emitting a coherently forged
foreign event producer is stopped by the parent, with forced exit and unknown
outcome preserved; repeated/new start cannot automatically retry it. Existing
scoped-domain, fixed-evaluation and application/registration regressions remain
part of the focused check set.

Current focused receipt: 97 tests passed in 24.36 seconds across scoped
application, scoped domain identity, fixed evaluation, structured application
and registration tests. Ruff passed the changed Python files. Targeted mypy
passed eleven source files. These are portable synthetic/source checks; the
selected root/hosted aggregate and independent review remain separate gates.

No numerical algorithm, native implementation, `local_models.py`, developer
server, console or native UI was edited by this writer. The local model catalog
owner consumes the same closed schema helper in its separate packet. No game,
real-data training, provider, paid operation, deployment, push or merge occurred.
Scope remains S0 text-v2 I/F-off; new native projection, all-scene policy quality,
Human origin, independent generalization and full G2/V1 are not claimed.

Rollback reverts this application candidate and retains immutable artifacts,
curation uses and operation records. Old code cannot automatically adopt a new
scoped operation; do not rewrite checkpoints, replenish cumulative budgets or
restore a database to manufacture compatibility. Exact final-source package,
Runtime/SDK/native install/load/control/Stop and authorized data/research gates
remain with their owners.
