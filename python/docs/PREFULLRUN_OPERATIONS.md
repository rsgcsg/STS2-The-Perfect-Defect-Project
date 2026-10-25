# Pre-Full-Run local operations

Use the one supported bootstrap (`uv sync --locked --all-extras`, `npm ci`) from an exact
clean checkout. `python -m spireagent.workbench` is the provider-neutral control surface. Commands
below use `uv run --locked python -m spireagent.workbench` (abbreviated `WB` in prose only).
No permanent service is required. Local reports and stores belong under ignored `.local/`.

## Prepare and execute an engineering Run

```bash
uv run --locked python -m spireagent.workbench prepare --fixture --config configs/fullrun/engineering-cpu.json --output .local/prepared.json
```

The returned exact `run_id` freezes Dataset, ModelView, FeatureSet, source, lock, Qwen,
serializer and complete config. Pass that ID explicitly:

```bash
uv run --locked python -m spireagent.workbench worker --run <run-id> --stop-after 1
uv run --locked python -m spireagent.workbench worker --run <run-id> --resume <checkpoint-id>
```

`--fixture` is explicitly synthetic/FakeQwen. It never produces qualified Human evidence.
For admitted future data use `prepare --dataset <id> --config <file> --qwen-snapshot <path>`;
`--control random --random-seed <seed>` uses the existing exact same-architecture frozen
random Qwen backend. Real backend admission retains its CUDA/BF16 and weight-pin checks.
Research purpose additionally requires `--protocol <id>` and qualified data. The pinned
bundle3 adapter is installed; sufficient real corpus and final Standard profiling/freeze
remain separately gated.
Changing source or lock requires preparing a new TrainingInput; a worker rejects that drift.

## Artifacts and registry

```bash
uv run --locked python -m spireagent.workbench show --artifact <id>
uv run --locked python -m spireagent.workbench lineage --artifact <id>
uv run --locked python -m spireagent.workbench registry-rebuild
uv run --locked python -m spireagent.workbench sync
uv run --locked python -m spireagent.workbench doctor
uv run --locked python -m spireagent.workbench push --artifact <id> --other-store <directory-or-s3>
uv run --locked python -m spireagent.workbench pull --artifact <id> --other-store <directory-or-s3>
```

Push/pull copy exact transitive parent closure and verified payloads, then publish manifests.
`sync` rebuilds the local manifest projection. SQLite can be deleted and rebuilt; immutable
artifact storage must be retained. Run completion slots are per-store conditional selections;
copying a RunResult transfers its evidence, not a remote worker lease or completion slot.
Use `--store <directory>` and `--registry <file>` for independent local workspaces.
P1 compute preparation/result reception has an explicit [required-payload inventory](DATA_LIFECYCLE.md#p1-compute-input-availability):
it retains exact raw-parent IDs but does not copy their payloads. This does not change full push/pull preservation.

## S3-compatible storage and disposable Linux compute

Provision a bucket/prefix and supply `STPD_S3_BUCKET`, optional `STPD_S3_ENDPOINT`,
`STPD_S3_PREFIX`, and `STPD_S3_REGION` through the environment. Credentials are read by the
SDK provider chain; environment credentials may use `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN`. Never put values in Git, manifests or
launch packets. No vendor changes training semantics.

```bash
uv run --locked python -m spireagent.workbench doctor --store s3 --smoke
uv run --locked python -m spireagent.workbench push --artifact <run-id> --other-store s3
uv run --locked python -m spireagent.workbench launch --run <run-id> --output .local/launch.json
```

`doctor --smoke` writes a unique tiny immutable probe and tests identical/conflicting
conditional publication plus readback. It leaves that isolated probe for audit; it does not
rewrite existing objects or certify every provider behavior. Read-only `doctor` verifies
all manifests and their payload hashes. Real account evidence must be recorded separately.

The launch packet supplies ordered argv arrays, exact source checkout and locked environment,
working directory and the same worker command. Provision Linux with Git, uv and the device
required by the frozen config. Upload the Run lineage first, supply scoped storage credentials,
then execute the packet. RunPod/Kaggle/Colab or another provider are optional wrappers.
Resume using the exact durable checkpoint ID; do not rename checkpoints or select "latest".

## Qualification and local projections

```bash
uv run --locked python tools/project.py closeout --base <exact-base-sha>
uv run --locked python -m spireagent.workbench e2e --output .local/cpu-e2e.json
uv run --locked python -m spireagent.workbench analysis --output .local/analysis.json
uv run --locked python -m spireagent.workbench dashboard --output .local/dashboard.html
uv run --locked python -m spireagent.workbench readiness --evidence .local/qualification.json
```

The deterministic read-only readiness command requires an exact-source reviewed qualification
receipt: `stpd/prefullrun-qualification-v1`, Producer, portable PASS, the CPU E2E report, and
hosted CI run ID/head/conclusion with successful linux-portable/windows-portable/locked-python
jobs. Missing or stale evidence returns EVIDENCE_REQUIRED. This receipt is not a cryptographic
attestation; the closeout owner verifies its linked CI and local command evidence independently.
No readiness verdict promotes engineering to scientific or real-game qualification.
