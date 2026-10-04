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

The M0 request transport accepts legacy canonical request JSON and a bounded
version-1 zlib frame (`STPD-M0-REQUEST`, NUL, version byte 1, big-endian uint64
logical length, 32-byte SHA256, then one zlib stream). The wire limit remains
256 MiB; declared and actual decompressed bytes are bounded at 1 GiB. Malformed,
truncated, trailing, digest-mismatched and noncanonical logical requests fail
before training. The local M0 controller frames only requests exceeding the
wire limit, preserving legacy wire bytes for smaller requests. Owner storage,
attempts, provider environment pins, results and recovery bind the original
canonical logical request hash; a frame is not a new training identity. A new
exact-source worker image is required before using this transport in compute.
Framed requests are stored as the same bounded frame in 8 MiB chunks, with a
request-only versioned index recording both logical and wire lengths/digests.
The existing request ref remains its logical hash. Recovery verifies chunks,
wire bytes, bounded decompression and logical identity. Existing legacy indices
remain readable and immutable; generic result storage retains its v1 contract.

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

## Submit, reconcile and restore

Persist `submitting` plus the request/target/attempt before calling `ModalProvider.submit`.
Persist the returned ModalCall before reporting submitted. SDK errors during submit mean
`submission_unknown`: the remote invocation might exist. There is no automatic retry, and
provider inspection/terminal reconciliation is required before a new attempt. `poll` is
non-blocking and receipt-bound; timeout only means no result yet, not proof of worker health.
`cancel` requests cancellation but does not manufacture a terminal result. Keep the call ID
for operator reconciliation if the provider reports an ambiguous cancellation or result.

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
