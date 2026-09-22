# Testing and Evidence

The repository-wide [testing guide](../../docs/TESTING.md) owns CI and promotion.
Commands below run from `python/` and describe the Python component suite.

## Supported portable contract

Bootstrap once with `uv sync --locked --all-extras` and `npm ci`, then:

```bash
uv run --locked python tools/project.py check
uv run --locked python tools/project.py closeout --base <exact-base-sha>
```

Each worktree uses its own `python/.venv`, installed from that worktree's locked
project. For background jobs, put that environment's `bin` directory on `PATH` and
verify isolated imports (`python -I`) resolve `stpd` and `spireagent` to the intended
checkout. `PYTHONPATH` does not repair a foreign editable installation. Do not export
`UV_PROJECT_ENVIRONMENT` across the whole test job: nested temporary-project syncs
must not target the caller's environment. The image-refresh fixture strips both
that override and `VIRTUAL_ENV`, with a real offline installation regression.

Python >=3.11,<3.12 is required. Dependencies may need network on a cold cache. Tests do not
require STS2, proprietary binaries, real Human evidence, Qwen weights, GPU or production
credentials. Installed versioned Platform consumer packages are checked; they are not a
real-game installation. The lock may include GPU-capable framework wheels, but CI never
requires GPU hardware or downloads model weights.

The Python component gate validates repository/CI contracts, runs tools/doctor.py, Ruff on the whole tree,
Mypy on stpd/tools, the existing Connector SDK test, the complete Pytest suite, clean-source cross-process CPU E2E, compileall,
`uv build`, working-tree and HEAD patch hygiene, and optional exact-base diff hygiene.
Focused tests accelerate development but never replace this gate.

Root `linux-portability` and `windows-portability` run the same complete monorepo check,
including this Python suite. Required `portable` succeeds only when both lanes succeed.
The old `locked-python` aggregate belongs to archived STPD CI, not a second active workflow.
Exact head checkout, read-only permissions, immutable
Action pins and stale-run cancellation are checked structurally. Do not weaken a check to
repair CI. A green result on an older SHA is not current-head evidence.

## Test shapes

Contracts: valid, malformed, future schema, missing provenance, tampered bytes, collisions,
duplicates, stale inputs and identity drift. Data: candidate completeness, whole-run and
semantic-component isolation, deduplication and leakage. Artifacts: immutable/idempotent
publication, integrity, missing objects, publication interruption, deterministic registry
rebuild and lineage. Workers: input verification, durable checkpoint, crash/resume, duplicate
completion, reporting failure and provider neutrality. Models: one score per candidate,
permutation behavior, finite loss, frozen backbone, Linear/MLP and checkpoint round trips.

Use FakeQwen, CPU and deterministic synthetic fixtures in portable tests. Provider-specific
integration is opt-in. Local/fake conformance is not real S3 or cloud qualification.

## Evidence boundaries

Source/test/package, data admission, model artifacts, training, offline/Gold/live evaluation,
scientific inference and service deployment are separate dimensions. A fixture E2E is only
engineering evidence. A completed GPU run does not prove quality; a green build does not prove
real-game qualification. Record source, command, environment, input/artifact identities,
results and non-claims. Never transfer evidence across changed identities without proof.

The gate writes its local E2E receipt to ignored `.local/cpu-e2e.json`. It fails if the checkout
changes while checks run. For a reviewed exact-source readiness receipt after hosted CI:

```bash
uv run --locked python tools/qualify_prefullrun.py --ci-run <exact-ci-run-id>
uv run --locked python -m spireagent.workbench readiness --evidence .local/qualification.json
```

The capture tool reads current GitHub CI head/jobs, reruns the full local closeout gate and
binds both to the same Producer. No manually asserted ancestor pass is sufficient.

The B lane also runs `python -m stpd.cloud_jobs.smoke` in the common gate: independent worker
processes compile fake features, pause/resume and compare learned weights/dev metrics. True
Platform-client/Hub HTTP tests cover pending/restart/receipt and Dataset reprojection. This
is portable engineering evidence; it does not substitute for real R2/GPU/Human qualification.

Decision-dataset regressions live in `tests/test_decision_dataset.py` and
`tests/test_decision_store.py`. They must preserve partial versus complete claims, source-byte
reprojection, parent context, duplicate accounting, immutable preview binding, revoked access,
and read-only profile views. Use only synthetic bundles in portable CI. A real-source dry run
is data-path evidence, not a new Human recording or model training result.
