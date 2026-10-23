# Cloud image recipes in the unified project

Run the commands below from `python/` of the exact clean project checkout.
`Dockerfile` accepts `STPD_IMAGE_PROFILE=hub` (cloud/data only) or `worker`
(all locked training extras, the default). Both record the profile and source in OCI
labels. Hub does not need Torch on its small VPS. A Hub image is not a training worker.
An existing predecessor image is a rollback artifact; `refresh.Dockerfile` only accepts
an already-qualified monorepo image and retains its profile. Build the first monorepo
candidate with `Dockerfile`, not the refresh shortcut.

# Exact-source disposable compute

The Hub owns queue, attempt, lease/fence, budget and final selection. This adapter only runs
existing STPD code and returns a candidate. It does not provision accounts or qualify a GPU.
The portable tests never deploy an App, fetch weights or use paid compute.

## Input and output

A CPU controller publishes `FeatureJobSpec` + `publish_feature_job` against an already
validated ModelView. Supply the exact target Qwen identity, hidden size, batch size, fixed
TrainingConfig and optional protocol. The Qwen identity comes from the qualified target
image/backend, not the CPU controller's installed torch version. Preparing the job never
loads Qwen. Real pretrained and admitted random identities both retain the existing pin.

Call `dispatch(store, ComputeRequest('features', ...), runtime, backend=...)`. It uses the
existing feature compiler, rejects Qwen/dimension/source drift before encoding, and returns
a `ComputeReceipt`. `validate_receipt` checks binding and immutable payload/lineage on CPU.
Then `prepare_feature_run` freezes the existing TrainingInput/Experiment/Run contracts.
Dispatch `ComputeRequest('training', run_id, ..., resume=exact_checkpoint_id)` with no model
backend: training uses only the frozen FeatureSet. An explicit stop budget yields a paused
receipt; otherwise the existing Worker publishes model, dev evaluations and RunResult.

Receipts are **candidate_prepared**, not Hub completion or scientific admission. The default
CandidateReporter writes immutable events/candidates, including compute attempt/request
references in event details, and never writes `run-completions/`. Hub must persist requests,
validate receipt identity and payloads, then select results in its current fenced transaction.
CPU receipt checks also decode bounded checkpoints, recompute the shared training plan and
compare loaded model tensors with the checkpoint head. They are not a GPU replay or
model-quality verdict.
Interrupted feature compilation can be explicitly rerun; there is no fabricated partial
FeatureSet or implicit "latest" checkpoint. Training resumes only an explicit same-Run ID.

## Account and storage setup without compute

Use the repository Python 3.11 environment, never a bare `modal` executable from Conda or
another Python installation. The locked `cloud` extra includes `python-dotenv`, which Modal's
`--from-dotenv` command imports only when used. Developer bootstrap remains
`uv sync --locked --all-extras`; operators needing only the CLI can use the explicit extra:

```bash
uv sync --locked --extra cloud
uv run --locked --extra cloud modal environment list --json
uv run --locked --extra cloud modal secret list --env spireagent-b --json
uv run --locked --extra cloud modal secret create stpd-worker-storage \
  --env spireagent-b --from-dotenv "$HOME/.config/spireagent/cloud/r2-modal.env"
```

Authenticate with `uv run --locked --extra cloud modal token new` only if current authentication
is absent/expired; that step requires the operator's browser. Select an existing authorized
workspace and environment. Secret creation is a separate metadata operation from deployment
or function execution. Do not add `--force` to recover an uncertain create: first inspect the
named Secret in that exact environment. Never print dotenv contents or put values in arguments.
Keep the external file mode 0600 with only `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`STPD_S3_ENDPOINT`, `STPD_S3_REGION`, `STPD_S3_BUCKET`, and `STPD_S3_PREFIX`. Worker credentials
are restricted to the private artifacts bucket; ingress and backups use separate roles.

Use the same explicit `--env spireagent-b` for eventual deployment and `MODAL_ENVIRONMENT`
for the Hub's provider process. Local CLI authentication/defaults are not copied into the
future Hub container. Keep Hub compute budget zero until the separately authorized worker
qualification. Secret existence/key validation proves configuration only, not injected values
inside a worker, an image build, GPU availability, billing limits or an execution result.

## Image and deployment

1. Push/review the exact source candidate and resolve its Producer and lock hash.
2. Build `Dockerfile` with `BASE_IMAGE` set to a Python 3.11 Debian registry reference **by
   digest**, and `STPD_SOURCE_REVISION` set to the full reviewed commit. No floating base
   image belongs in a deployment receipt. The image retains that clean checkout and runs
   `uv sync --locked --all-extras`; no local source or credentials are copied into it.
   A measured small source/dependency update can instead use the qualified-image refresh
   procedure in [the Hub runbook](../hub/RUNBOOK.md#refresh-from-a-qualified-image). Its
   default path requires an unchanged lock; an explicitly reviewed new lock requires the
   exact parent source/lock and a full locked synchronization of the resulting environment.
3. Push the built image and resolve its immutable registry digest. Capture build source,
   base image, uv version, final OCI digest and registry/build receipts separately.
   The current adapter uses an anonymously pullable image: both the Hub host and Modal
   must be able to pull that exact digest. This public-code image contains no game files,
   recordings, weights or credentials. Private-registry authentication is not configured
   by the adapter and must not be assumed from a local registry login.
4. Create a `ModalTarget` JSON using the Producer, final image `@sha256:...`, explicit GPU
   (or `none` for CPU), timeout, named storage Secret and optional pre-existing Qwen volume.
   Current `stpd/modal-target-v2` also freezes `cpu_cores` and `memory_mib`
   (defaults 2 and 8192) as explicit request/limit pairs. Old v1 targets and their
   calls remain historical evidence for their original source; do not rewrite
   an active handle or grant it the new source/image.
   The Secret contains scoped `STPD_S3_*` / `AWS_*` environment settings, and when required
   `STPD_QWEN_SNAPSHOT` points at a pre-verified snapshot under `/qwen`. Never put values in
   JSON. Worker credentials must not grant Hub database, deployment or final-selection access.
5. Set `STPD_MODAL_TARGET` to that file and deploy using the locked `cloud` extra:
   `uv run --locked --extra cloud modal deploy deploy/cloud-worker/modal_app.py`.
6. Keep the exact target JSON and returned deployment/image identity as service evidence.
   Account/region/device qualification and cold GPU source/lock/backend identity are separate
   real-runtime gates; a successfully built definition is not qualification.

One app name is derived from the complete target hash, including source, OCI digest and
resources. Modal's free/default lookup routes to a named App's latest version; do not redeploy
changed code/resources under an old target name. Each invocation verifies target ID and the
worker verifies actual clean executing-checkout Producer. It invokes the image's locked
`/opt/stpd/python/.venv/bin/python -m stpd.cloud_jobs`, not Modal's injected SDK Python for training.
The local serialized deployment wrapper must also execute from the clean exact target
Producer. No source-identity check is disabled to support packaging. The wrapper is serialized with
only standard-library dependencies and primitive target identity, not the local STPD package.
Freeze wrapper values such as the timeout as primitive globals before defining
`compute`; referencing the typed `ModalTarget` inside that serialized function
would require importing STPD in Modal's injected interpreter before the project
venv starts. The deployment regression decodes the actual locked-SDK pickle in a
fresh interpreter with both project packages unavailable.

### Native Modal image refresh

The same `ModalTarget` and deployment wrapper also accept an exact Modal-native
immutable `im-...` image ID. This is a provider image identity, not an OCI digest;
mutable names/tags remain rejected. The complete target still binds image,
Producer, resources and timeout, and the image's worker independently checks its
actual clean source and lock before dispatch.

When no OCI builder is available, `build_modal_image.py` refreshes an explicitly
identified worker parent with an exact Git bundle. It checks the parent source
and lock, checks out the new source, runs the ordinary full locked dependency
synchronization and `uv pip check`, and verifies actual `source_identity`. It
does not carry secrets or invoke a training function. Run it from `python/`:

```bash
python deploy/cloud-worker/build_modal_image.py --parent-image EXACT_IM_ID \
  --parent-source EXACT_PARENT_SHA --parent-lock EXACT_PARENT_LOCK_SHA256 \
  --source EXACT_CANDIDATE_SHA --bundle /absolute/candidate.bundle \
  --environment spireagent-b --output /absolute/new-build-receipt.json
```

Retain the original build receipt with native image ID, parent tuple, bundle and
recipe SHA-256 separately. Its builder is CPU2/8GiB with a 900-second deadline.
Build costs need the same explicit authorization as compute. A failed or uncertain
build is retained for provider reconciliation, without automatic rebuild.

The optional builder flag `--p1-engineering-profile` freezes a synthetic-only
measurement entry in that image. The unchanged `execute_request` runs first; an
independent clone of the completed checkpoint then measures at most four N
forward/CE-backward passes without optimizer updates. Original weights,
checkpoints and receipt remain unchanged. A `performance` artifact records actual
worker wall time, sampled GPU use, peak memory and separate clone timings, bound
to the exact request/attempt/run/checkpoint. Measurement failures stay explicit;
the observer rejects any original whose origin is not `engineering_fixture`.
Clone microtimings are not actual training throughput or a formal-data speed claim.
The wrapper always uses a fresh container per call, startup120 seconds, two-second
scaledown and zero retries; its ordinary non-profile entry remains unchanged.

## Submit, reconcile and restore

Persist `submitting` plus the request/target/attempt before calling `ModalProvider.submit`.
Persist the returned ModalCall before reporting submitted. SDK errors during submit mean
`submission_unknown`: the remote invocation might exist. There is no automatic retry, and
provider inspection/terminal reconciliation is required before a new attempt. `poll` is
non-blocking and receipt-bound. The locked SDK's polling timeout means no result
yet, not proof of worker health. Its distinct `FunctionTimeoutError` is reported
as `execution_timeout`; transport failures remain `result_unavailable`. Neither
error manufactures a terminal receipt or permits resubmission. The Hub retains
the exact call handle for its existing terminal reconciliation.
`cancel` requests cancellation but does not manufacture a terminal result. Keep the call ID
for operator reconciliation if the provider reports an ambiguous cancellation or result.
When no worker events appear, inspect the exact deployed App's startup logs;
`retries=0` does not prevent Modal from replacing containers that fail to start.
An empty container list or a cancellation ACK alone is not terminal proof. For a
failed isolated target, stop its exact App and verify its stopped state and zero
tasks, retain the original call's output/error and stop evidence, then use the
existing `Operations.reconcile_stopped` operator entry with that evidence. This
retains the job, handle and reservation and never requeues the original request.

The Application's normal `training --execution remote` client is the operator entry.
After a client interruption, inspect its original Operations job and saved provider
handle first. Reopen the same configured owner and use `status`/`reconcile` for that
job; a provider success still needs the ordinary typed receipt validation and fenced
selection. `reconcile` also copies the selected result's complete parent closure
back to the local Store. A transport wait is an observation limit, never permission
to repeat `start` or to manufacture a terminal state. Schedule that readback and
CPU materialization against the actual local memory/disk/IO budget.

Fixed-pause recovery uses the original paused job, expected attempt and exact
checkpoint through `resume`. Preserve its Run's producer, lock, input, numerical
execution identity, model budget and all previous invocation reservations. A new
authorized invocation or target timeout does not rewrite an old target, handle,
deadline or cancelled job. If the available call/time budget cannot contain a
baseline plus valid pause/resume, report the missing part or reuse an already
verified compatible pause; do not weaken the recipe, relabel cancellation as pause
or silently add a call.

For normal P1 M-std/baseline execution, Modal and the SSH supervisor pass the
frozen `target.timeout_seconds` to the same cloud CLI as `--wall-seconds`.
`execute_request`/`dispatch` keep this execution allowance outside ComputeRequest
and Model configuration. The existing ExecutionControl requests pause300 seconds
before timeout and the original worker returns its complete checkpoint/paused
receipt at an update boundary, before final TEST. P1 allowances<=300 reject as
insufficient. The provider hard timeout remains active; non-P1 workers and the
synthetic-only profile entry keep their prior hard-timeout behavior.

M-std also uses its existing codec/Store/Reporter to save after1800 monotonic
seconds without a successful save, coalescing with milestone/DEV-best saves on the
same update. These are complete recovery states, not extra evaluation points or a
new selection/checkpoint ledger. An indivisible update/DEV pass, slow I/O or startup
can exceed the margin; then the hard timeout may still interrupt the attempt. Only
actual verified immutable checkpoints and original typed receipt/termination facts
establish what survived. Measure and freeze adequate headroom before a long run;
do not relabel a hard timeout as a successful pause or retry unknown delivery.

Acceptance separates provider terminal/zero running inputs from selected
RunResult/Model closure, complete baseline-versus-resume checkpoint state and CPU
package tensor/scoring consumption. Record startup, worker, checkpoint/transfer
and reconciliation timings separately where observed. Synthetic N throughput and
clone microtimings do not predict the complete real corpus. Quote current resource
rates and cumulative request envelopes including previous attempts/builders;
unknown invoices remain unknown. After the bounded campaign, stop only its named
deployment and verify actual stopped/zero-task state, preserving images and data.

Modal worker settings are one container, zero warm containers, a fixed timeout and zero
user-code retries. Infrastructure preemption or client-level delivery behavior can still
cause duplicate execution; fencing is always required. Scoped immutable candidate writes are
safe to retain; the Hub must never interpret their mere existence as final selection.

Official API references checked for this implementation:
- https://modal.com/docs/guide/trigger-deployed-functions
- https://modal.com/docs/reference/modal.FunctionCall
- https://modal.com/docs/guide/images
- https://modal.com/docs/guide/retries

Provider adapter tests plus CPU replacement-process evidence qualify only engineering.
They do not establish account availability, real S3 semantics, CUDA/BF16 admission, cost,
scientific validity, Human origin or native gameplay correctness.

## Portable subprocess evidence

After committing the exact clean candidate, run
`uv run --locked python -m stpd.cloud_jobs.smoke --output .local/cloud-worker-cpu.json`.
It prepares explicit synthetic input on CPU, compiles features in a separate process,
pauses training, resumes in a replacement process and compares learned weights and dev
metrics to an uninterrupted process. The final result identities can differ because
checkpoint audit RNG bytes belong to their actual process; those bytes are never rewritten.

## Isolated real-TRAIN calibration on Modal

`calibrate_p1.py` is an explicit one-call engineering operation. It consumes the
verified TRAIN capsule from `training-calibration prepare/push`, reuses the same
Store and scoped worker Secret, and publishes a `performance` artifact. It does
not deploy a long-lived training service, select a Model, enqueue a formal job or
alter an existing Run. The synthetic-only `p1_gpu_profile` restriction is unchanged.

Snapshot and projection integrity validation reads all partitions before selecting
TRAIN. `DEV_consumed=false` and `TEST_consumed=false` mean no model forward,
gradient or selection use; they do not claim held-out bytes were never read. The
explicit report fields record this distinction. Source capsules created before
these explanatory fields remain immutable.

Build the exact clean candidate with the ordinary immutable image refresh first.
The plan JSON contains exactly `variants` (one to eight complete `MStdExecution`
objects) and `profile` (boolean). A first complete optimizer update is warmup;
remaining updates measure the shared production numerical path. An optional trace
covers one whole real TRAIN frame, with backward and no extra optimizer update;
it is not full-batch timing. Keep thread count, microbatch policy and numerical
trajectory differences explicit in comparisons.

```bash
python deploy/cloud-worker/calibrate_p1.py --image EXACT_IM_ID \
  --input EXACT_CALIBRATION_INPUT --plan /absolute/plan.json \
  --receipt /absolute/fresh-operation.json --environment spireagent-b \
  --timeout 1200 --cpu 2 --memory-mib 16384
```

The launcher binds actual clean source/lock/image/input/plan/resources, uses one
L40S, fixed CPU/RAM pairs, startup120 seconds, scaledown2, single-use container and
zero retries. It atomically publishes receipts (same-directory temporary file, fsync and replace),
preserving the previous complete App/call identity on a failed write. It writes an offer receipt before spawn and preserves original
App/call IDs before waiting. Budget all allocations, startup, failed attempts,
CPU builds and transfer; a cap is not a consumption target. After unknown output,
inspect that exact App/call and stop the original resource if necessary; never
relaunch as a transport retry or treat an ACK as termination. Read the published
report/trace through their exact Store IDs and verify actual stopped resources.

Source checks plus a cold locked-SDK decoder test establish wrapper preparation,
not account, CUDA, full checkpoint migration or a speed benefit. CPU calibration
is explicitly diagnostic; it never claims an exact GPU checkpoint continuation.
