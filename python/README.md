# STS2: The Perfect Defect

> **Status: pre-alpha research project.** The code currently in this repository is a
> real Headless/Connector integration and learning smoke, not the final STPD v0 model.
> It is intentionally retained as a reusable qualification baseline rather than treated
> as disposable prototype code.

STPD studies decision models for *Slay the Spire 2*. The project owns research-state
projection, datasets, model representations, learning, evaluation, and experiment
provenance. It does **not** own game rules, legality, RNG, effects, or execution.

Within the wider project system, STS2 AI Platform is the upper-level foundation
and STPD is the research component in this repository, consuming versioned public
contracts. Sharing a repository does not transfer environment authority to it.

```text
shipped STS2 / qualified Platform Host Runtime
                  |
       Platform Connector component
                  |
       Player Environment contract
                  |
                 STPD
   data -> representation -> model -> training -> evaluation
```

## Start here: collect and view project data

The default project workflow is one qualified Platform game Mod, one STPD workbench and the
same invited account in the local workbench and [cloud portal](https://hub.2-fire-2.com/).
Open **录制与上传** for the project's daily recording setup; topic activities are optional.
After setup, record in the game, press Recorder **Close**, then inspect packaging, upload and
remote acceptance in **采集记录**. The local **这台电脑** scope also shows the unuploaded queue.

Follow [download, install, account/device setup, collection and maintenance](docs/B_PIPELINE_HANDOFF.md).
It is the canonical everyday procedure, including the exact release combination to obtain,
one-time recording consent/configuration and recovery when a step fails. Choose a reviewed
release or explicitly non-stable candidate; do not infer a supported build from a branch name.

From python/ in the approved project checkout, the collector entry is:

```bash
python tools/open_workbench.py --config /ABS/project.json --hub-url https://hub.2-fire-2.com
```

The launcher installs the locked lightweight `cloud` dependencies and opens the workbench.
Use the same private `--config` path on every launch. First-time collectors still need the
qualified Mod/tool, invited login, device approval and explicit Human/upload/project-sharing
consent. In **录制与上传**, prepare local files, bind the recording directory with the game
closed, then start the game and refresh its connection check before enabling uploads. Setup
status survives a page refresh or restart; an uploaded receipt remains a separate result.
Opening the page alone does not start recording or authorize uploads. Models are separate downloads.
See [the console guide](docs/PROJECT_CONSOLE.md) for screen meanings and
[scoped release evidence](docs/evidence/B_UNIFIED_WORKFLOW_RELEASE_2026-09-13.md) for what has
actually been tested. Uploaded evidence, an admitted Dataset and trained model quality are
three different results.

## Develop and research the data pipeline

The Local-First Full-Run lane is separate from historical combat-v0. Platform owns semantic
state, complete `A_sem(S)`, Human choice/Commit and causal successor; STPD consumes that
contract without reconstructing native authority. The pinned Platform bundle3 adapter preserves first-class occurrences, exact parent/root lineage,
execution catalogs and Read/Commit/successor evidence. Synthetic input remains explicitly separate.

The implemented flow is Dataset → provisional ModelView → frozen FeatureSet → TrainingInput
→ disposable Worker → Checkpoint/Model → OfflineEvaluation. Local/S3-compatible ArtifactStore
owns immutable bytes/manifests. The Registry SQLite is rebuildable; the Hub operations SQLite
is durable and requires backup. DuckDB plus a local dashboard
provide projections. One shared Linear/MLP candidate scorer spans all surfaces; Qwen never
outputs native commands. Gold tooling and E0–E7 configuration do not imply Human labels or a
scientific campaign.

```bash
uv sync --locked --all-extras
npm ci
uv run --locked python tools/project.py check
```

The common gate includes a clean-source CPU E2E with replacement-process resume, store
transfer, Registry deletion/rebuild and dashboard/analysis. Hosted Linux and Windows run
that same gate; latest exact-source evidence determines readiness.
See [operations](docs/PREFULLRUN_OPERATIONS.md), [research](docs/FULLRUN_RESEARCH.md),
[training](docs/FULLRUN_TRAINING.md), [Gold](docs/FULLRUN_GOLD.md), and
[workbench](docs/LOCAL_WORKBENCH.md). The B lane adds close-to-outbox delivery, private evidence ingress, a CPU Hub and disposable
Modal execution using existing research workers. Start with [release, terminal handoff and next gates](docs/B_PIPELINE_HANDOFF.md),
then [B pipeline operations](docs/CLOUD_PIPELINE_B.md).
The [shared project console](docs/PROJECT_CONSOLE.md) connects local delivery status with
scoped cloud records, immutable Dataset/job/model lineage and operational evidence.
One invited account can view the same authorized project data in both shells and explicitly
bind its computers. Hub owns the current `member`/`admin` roster; login shows the profile before
device setup. Members see actual coverage and select explicitly shareable immutable downloads.
Administrators manage invitations/quotas in the cloud browser; personal sessions remain separate
from background device uploads. Recording consent and native setup remain deliberate actions. The
lightweight collector launcher above uses the same workbench implementation.
The daily-default workflow and new member/export/local-model capabilities require their own
exact release/service and Human gates; use the capability scope in published release notes.
Daily consent v2 is independent of software versions, while every tool, queue and native load
retains its exact identity. Registering a new tool does not upgrade an existing outbox;
[maintenance](docs/B_PIPELINE_HANDOFF.md#daily-work-upgrades-and-incidents) describes this limit.
Model download, optional Runtime installation and readiness are separate. The retained S1
combat adapter is narrow; Full-Run online model/input parity remains blocked.
Real corpus sufficiency, actual storage/GPU qualification, Human Gold and STS2 live evaluation
remain separately gated; source/test success is not service or scientific qualification.

## Retained historical combat-v0 and integration evidence

The current package contains the v0 engineering lane, an admitted real frozen-Qwen L2
backend, and the retained H1 integration regression tools:

- `linear_q.py`: a deliberately small linear-Q baseline;
- `training_smoke.py`: real single-environment learning smoke;
- `contention_smoke.py`: multiple isolated actors with one shared learner;
- `multi_seed_learning.py`: repeated learner-seed qualification;
- `reference_transfer.py`: frozen-policy execution on Managed and shipped Reference Hosts;
- `game_seed.py`: deterministic game-valid experiment seeds.

These modules prove that an independent learner can consume the Player Environment and
are expected to remain useful as regression and qualification tools. They do not claim
that linear Q-learning is the final STPD architecture or that the current reward is the
project objective.

The v0 pre-Qwen system is implemented: strict `ResearchState`,
`ResearchAction`, `ResearchTransition`, execution-envelope separation, eligibility,
Lite/Standard/Full deterministic serializers, semantic hashes, schemas, leakage/B0,
canonical Parquet/manifests/splits, a fail-closed AgenticSTS importer, a real Managed
collector, Scheme 1/S2-Simple/S2-SDT, optimizer/checkpoint/evaluation mechanics, B1-B7
report tooling, a pinned metadata/tokenizer Qwen L1 gate, and an exact full-weight
CUDA/BF16 Qwen L2 adapter with a same-architecture frozen random control. FakeQwen remains
the cheap CI backend. Real-Qwen forward/profile/backward smokes are engineering evidence.

The bounded owner-run L2 tiny-overfit sequence is complete. Attempts 001 and 002 remain
retained failures at 64 and 256 steps. Attempt 003 passed the unchanged engineering
criteria at 512 steps: final mean listwise NLL `0.0850893874`, relative reduction
`95.243%`, memorized Top-1 `1.0`, finite values, and zero Qwen gradients. This admits the
optimizer/memorization plumbing only; it is not Gate 1 or model-quality evidence.

The exact AgenticSTS trajectory release was also audited as a potential bootstrap source.
It contains 198,600 decision events and 139,211 combat decisions, but no explicit complete
legal-action catalogs, game seeds, or exact environment identities. Zero records are
rank-eligible under the existing fail-closed contract, so it remains excluded from S1.

A separate fail-closed Human Annotator importer accepts only exact native-UI
records with a complete frozen BoundAction catalog, exact-unique process-local
mapping, stable successor, exact game/Connector/Annotator/Modset identity, and
whole-run roots. It reuses the existing `ResearchProjectorV0`, canonical split,
Parquet, and B0 path. The reusable multi-worker lane adds exact collection
profiles, immutable checksummed session bundles, a pseudonymous filesystem
registry, strict multi-session admission, deterministic whole-run corpus
snapshots, corpus B0/token profiling, and a frozen smoke handoff. Exact native
human sessions now form a 1,962-record cross-platform unified population. The
versioned v2 combination globally reruns collision/deduplication, root-safe split,
B0 and Standard profiling. Serializer v1 removes only redundant combat referent
payload from Standard state text; it does not truncate or alter records. The
result passes B0 and the unchanged Standard P95/hard gates. A formal owner-gated
`S1-1K-2K-SMOKE` runner consumes only its immutable Parquet handoff; Human Gold
and scientific Core remain unavailable.

The importer also has a V2 path for portable, read-rich Human evidence. V2 is
accepted only through the version-pinned Platform Evidence verifier; direct
JSONL import cannot opt into external Read blobs. Verified `run_deck` and
`combat_piles` evidence is projected through the same `ResearchProjectorV0`,
while V1 bundles remain supported unchanged. One immutable native-human V2
bundle has been verified and imported 30/30; it is not part of the frozen V1
training corpus and does not authorize training.

The owner-run S1 smoke subsequently completed all 1,659 optimizer steps and its
behavior engineering criteria. Its exact final Scheme1 linear checkpoint now has
a versioned decision-only Policy Adapter and Manifest. Platform Policy Runtime
owns generic modes/controller/stale/Receipt/successor/evidence lifecycle; STPD
owns only checkpoint/Qwen/projection/scoring support. The original live runner
is retained only as a non-runnable golden regression until exact-artifact parity is proven. The
checkpoint is absent on the current Mac, so the new path is not exercised. See
[Experimental Live S1 Operations](docs/LIVE_S1_OPERATIONS.md).

## What v0 will build

STPD v0 is a combat-focused representation and architecture study. It freezes
`Qwen/Qwen3-0.6B-Base` for the core experiments and compares:

1. **Scheme 1 — Direct Joint Scoring**: `(state, action) -> score`;
2. **S2-Simple**: single-vector latent transition and value;
3. **S2-SDT**: learned world tokens plus an action-conditioned State Dynamics Transformer.

The first architecture phase contains 10 configurations and 3 training seeds per
configuration. Evaluation is organized into B0-B7: contract/leakage, behavior holdout,
human gold, state-action coupling, successor dynamics, current-patch transfer, fixed-seed
live combat, and compute/scaling. Gates 0-5 are the actual sequential research decisions;
B0-B7 are supporting evidence families.

See [v0 execution plan](docs/V0_EXECUTION_PLAN.md).

## Project boundaries

- **STS2 / Host** owns the real game transition and stable successor.
- **STS2-AI-PLATFORM / Connector** owns fair-player
  Snapshot/Read/BoundAction/Receipt semantics.
- **STPD** owns research projections, labels, rewards, models, training, and evaluation.
- Host-specific IDs and native operands never become model features.
- Qwen is accessed through a pinned, typed backend interface; model modules do not call
  Hugging Face implementation details directly.

## Engineering smoke: retained integration lane

```bash
uv sync --locked --all-extras
npm ci
uv run pytest
uv run mypy stpd tools
uv run ruff check stpd tests
uv run python tools/doctor.py
```

A real environment run additionally requires one externally prepared exact Managed
candidate. Host Runtime code and both strategy-free clients come from the pinned Platform
package installed by `npm ci`; the candidate remains a separately audited local artifact:

```bash
export STS2_MANAGED_CANDIDATE=/absolute/path/to/<exact-candidate>

PYTHONPATH=node_modules/@rsgcsg/sts2-host-runtime/consumers/python \
  uv run python -m sts2_headless.smoke \
  --candidate "$STS2_MANAGED_CANDIDATE" \
  --max-actions 64 \
  --evidence-file .local/evidence/environment-smoke/report.json

uv run python -m stpd.training_smoke \
  --candidate "$STS2_MANAGED_CANDIDATE"
```

Raw evidence is local and must not be committed.

An opt-in exported M2/Reset engineering smoke uses the exact installed Host Runtime
Python consumer's atomic `text-menu-v1` observation and bound submit methods. It
requires a reviewed Host package pin that actually provides those methods; an older
installed package fails before a Managed child starts. From `python/`, after supplying
private exact candidate, pin and exported package paths:

```bash
uv run --locked python tools/managed_memory_smoke.py \
  --host-runtime /ABS/installed-host-runtime \
  --host-runtime-pin /ABS/exact-host-pin.json \
  --candidate /ABS/exact-managed-candidate \
  --model-export /ABS/verified-memory-export \
  --seed M2SMOKE01 --character Defect --ascension 0 \
  --max-policy-calls 4 --max-submissions 2 \
  --max-observations 8 --max-seconds 30
```

The command verifies the detached model package and Host package identity, never
uses a Live registration manifest as Managed admission, and prints only bounded
counts/status. A complete terminal page has no score or submit. Unknown delivery
stops without retry; the dedicated child closes on all outcomes. The wall limit is
checked around each synchronous client call, whose own response timeout is bounded;
it is not a hard process-kill deadline. This is an engineering experiment, not a
Policy Runtime HTTP, Human, full-game or model-quality result.
The default is Defect at ascension 0. The Host driver accepts an explicit character
but does not expose an ascension option, so this smoke rejects other ascensions before
starting a child. The report distinguishes the requested character/ascension from
the first text snapshot's observed run/player fields for each episode and stops
before scoring if they disagree. Use an unambiguous uppercase seed (no I/O); the existing experiment seed owner
rejects aliases before starting a child, rather than comparing an unnormalized
request with a normalized native identity. The exported model package has no character or
ascension admission claim; selecting Defect here is an experiment setting.

The original operational environment patch baseline remains predecessor Headless `v1.0.1`, Managed
Host `8dc622b0.../7228541c...`, Connector `v1.1.0-rc.1`
`e065102.../c1877f1a.../64765ea1...`, and Player Environment protocol/SDK
`1.0.0/1.0.0`. Windows x64 has separate candidate identities and evidence; it does not
inherit the macOS operational freeze. A changed identity is a requalification event.
Current tooling installs Connector SDK `1.1.0-rc.1` and Host Runtime
`1.1.0-rc.7` from immutable `STS2-AI-PLATFORM` GitHub Releases. Package identity,
candidate artifact identity, and exact loaded Host identity remain independent evidence.

## Repository navigation

- [Default download, collection, cloud and maintenance workflow](docs/B_PIPELINE_HANDOFF.md)
- [Local/cloud console, accounts and computers](docs/PROJECT_CONSOLE.md)
- [Hub deployment, backup and incident runbook](deploy/hub/RUNBOOK.md)
- [Document map](docs/DOCUMENT_MAP.md)
- [Current status](docs/STATUS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Interfaces](docs/INTERFACES.md)
- [Data and provenance](docs/DATA_AND_PROVENANCE.md)
- [End-to-end data lifecycle and training-host handoff](docs/DATA_LIFECYCLE.md)
- [Human corpus lane](docs/HUMAN_CORPUS.md)
- [Qwen integration](docs/QWEN_INTEGRATION.md)
- [Qwen L2 operations and owner handoff](docs/QWEN_L2_OPERATIONS.md)
- [Experimental Live S1 operations](docs/LIVE_S1_OPERATIONS.md)
- [Scientific experiment protocol](docs/SCIENTIFIC_EXPERIMENT_PROTOCOL.md)
- [Project system](docs/PROJECT_SYSTEM.md)
- [Roadmap](docs/ROADMAP.md)
- [Pre-Qwen operations and historical L1 handoff](docs/PRE_QWEN_OPERATIONS.md)
- [Agent and contributor rules](AGENTS.md)
- [Development workflow](docs/DEVELOPMENT_WORKFLOW.md)

The project is evidence-first: a test, implementation, benchmark, and runtime claim are
separate facts. Every result must identify source, data, model, Host, Connector, seeds,
and non-claims.
