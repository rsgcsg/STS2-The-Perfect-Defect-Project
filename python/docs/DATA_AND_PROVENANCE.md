# Data and Provenance

## Scope and connected routes

The common relationships are original producer/Host/actor provenance → verified
source and membership/use → declared Projection/InputSpec/TargetSpec → training
input/recipe/run → Model → executable Agent and a new run. The original Host is
an offline provenance reference, not a required live dependency. Model deployment
compatibility is a separate computation/input/state/Agent check. See the
[baseline relationships and five source axes](../../docs/BASELINE_V1_SPEC.zh-CN.md#6-记录来源转换与目标)
and [common Agent execution](AGENT_EXECUTION_CONTRACT.md).

The full-transition/B0/Qwen flow below is the strict combat-v0/Scheme1 lane,
not a universal normalization requirement. Existing routes retain their own proof:

| Route | Original-to-input relationship and owning contract |
| --- | --- |
| Strict ResearchTransition | Exact S/A/S′, eligibility, whole-root split and B0; the requirements below remain unchanged |
| Public Human-observation BC | Exact original H/proof/public-action binding → allocation → sample/disposition → encoded input; [Public H-BC](research/STAGE1A_WORKFLOW.md#exact-human-observation-bc-view), N without successor supervision |
| Native Source3 | Ordered captured bases/publications/inputs → declared publication-memory, recorded-capture or sampled view; [Source3 training](research/SOURCE3_ORDERED_NATIVE_TRAINING.md) |
| Direct native Agent samples | Runtime's stored offered bytes and protocol joins → reviewed producer/student relation, known context and sparse N; [sampled admission](NATIVE_AGENT_SAMPLED_ADMISSION.md) |

Integrity verification, source-specific research eligibility and permission for
this training use are distinct. These routes share Curation/ArtifactStore and
existing training owners; they do not all require a causal successor, a Qwen
cache, or a new Source registry. [Platform record ownership](../../docs/FULL_RUN_DATA_CHAIN.md)
separates Human causal records, Source3 and Agent evidence. Raw originals and
their schema/producer identities are never relabelled to enter another lane.

## Data flow

```text
external/local source
-> immutable raw zone
-> validated Player Environment records
-> ResearchState/Action/Transition normalization
-> eligibility and policy provenance
-> episode/run/seed-root split manifests
-> optional frozen-Qwen features
-> training/evaluation datasets
```

Only manifests, schemas, checksums, reviewed summaries, and small synthetic fixtures belong
in Git. Raw traces, saves, model caches, and external datasets stay local or in an approved
artifact store.

The implemented `stpd.data` pipeline reads explicit raw JSONL without correction/defaults,
validates the frozen transition schemas and B0 invariants, assigns whole seed roots to a
deterministic split, then writes canonical-JSON Arrow columns to compressed Parquet. Nested
JSON is the contract; Arrow is storage, not a second semantic authority.

The physical format and training-host transfer rules are measured and versioned separately
in [End-to-End Data Lifecycle](DATA_LIFECYCLE.md). Canonical data never becomes a Qwen cache,
and a cache or staging receipt never becomes research or training authority.

## Data zones

- `data/raw/`: immutable source capture; ignored.
- `data/external/`: licensed/public external material; ignored.
- `data/normalized/`: versioned ResearchTransition records; ignored.
- `data/features/`: Qwen or other derived features; ignored and cacheable.
- `data/manifests/`: tracked dataset/split/provenance manifests.
- `data/gold/`: only reviewed manifests and redistributable annotations; raw private notes
  remain external.

## Required provenance

Every strict ResearchTransition in the lane above records:

- policy source/version and optional confidence;
- rank/transition/return eligibility;
- episode ID, seed, game version;
- exact game and Host artifact identities plus the concrete Player Environment
  implementation revision/digest; a Managed Host does not falsely claim a loaded
  Connector DLL;
- input profile and schema versions;
- legal actions, chosen action, stable successor, terminal/outcome status.

Multi-source data remains queryable by source. Do not collapse strong teacher, heuristic,
random exploration, historical agent, current model, or human data into an anonymous pool.

## Splits and leakage

Split by episode/run/seed root, never by adjacent turn. Requirements:

- normalized state hashes do not cross train/validation/test;
- Gold-dev and Gold-test are separate manifests;
- Gold-test remains sealed until architecture/input/hyperparameters are frozen;
- future outcome, teacher identity, runtime IDs, hidden state, and test labels are absent from
  model inputs;
- candidate action order is randomized or recorded;
- duplicate and near-duplicate states are measured, not ignored.

B0 failure invalidates the experiment.

The current executable B0 checks schema validity, unique transition/action identity, chosen
action membership, fair-player model-input leakage, complete listwise catalogs, stable
successor semantics, inadmissible lifecycle reason codes, manifest row counts, and semantic
duplicates crossing splits. Large-corpus approximate-neighbor analysis remains pending and
must be added before large external data admission.

## Eligibility

- `rank_eligible`: action choice is suitable behavior supervision.
- `transition_eligible`: state/action/stable-successor semantics are reliable.
- `return_eligible`: terminal outcome and trajectory linkage are reliable.

A weak or random action can be valuable transition data while remaining rank-ineligible.
Observed returns estimate behavior-policy value, not optimal Q.

## External data risks

External processing must address:

- game/Connector/Host version drift;
- stale or incomplete legal action catalogs;
- unstable or transient successor capture;
- localization and text normalization;
- policy-source bias and source imbalance;
- licensing, redistribution, privacy, and service terms;
- hidden information and future-label leakage;
- duplicate episodes and train/test contamination;
- missing negative/counterfactual actions;
- changes in serializer, tokenizer, model revision, or cache format.

Unknown rights or unverifiable original source provenance means exclude the source
from training. A verified original with an absent game/Mod/ascension field is a
different case: its declared target/use and owning lane determine which claims
and transformations remain eligible. Preserve unknowns; never borrow today's Host
metadata or weaken a strict validator requiring exact identity. Historical-transfer
N supervision does not establish current-game/A0, causal Z or independent-test claims.

## Native Human Recorder Source

`stpd.data.human_annotator` consumes exported V1
`sts2.human-annotator/decision-record-1` JSONL and verified portable V2
`sts2.human-annotator/decision-record-2` bundles. Admission requires exact
game/Connector/Annotator identities, the exact observer Modset canary, a
complete interactive pre-catalog, one chosen action present exactly once, a
native witness mapped by reference equality to the frozen Host binding, and a
different stable successor in the same runtime/environment. It applies the one
existing `ResearchProjectorV0`; it does not derive legality from names,
coordinates, post-state, or action order.

V2 is admitted only after the pinned Platform Evidence package verifies the
bundle checksum inventory, CaptureProfile, RunJournal, content identity and
state-bound Read blobs. Required `run_deck` and `combat_piles` payloads then
enter the same research projector for both the decision state and successor.
The public raw-JSONL importer remains V1-only, so a caller cannot supply an
unverified blob root. Platform verification establishes evidence integrity;
STPD still owns research eligibility and never gains action authority.

Accepted human choices are full-listwise rank and transition eligible. Return
eligibility remains false until a separate terminal/outcome linkage exists.
The recording session plus run ID is the whole-run split root; when the normal
player-visible contract does not expose the game seed, the importer records an
explicit `human-root:` key rather than inventing a seed. Raw recordings remain
private local data and are never committed.

The currently frozen multi-worker corpus uses immutable V1
`sts2.human-annotator/session-bundle-1` directories. Each bundle binds raw
evidence, independent audit, deterministic export, exact collection profile,
pseudonymous worker/campaign fields, explicit human-origin attestation and a
complete checksum inventory. `stpd.data.human_corpus` verifies the whole bundle,
then invokes this same strict importer for every session.

V1 bundle verification now runs through Platform Evidence with the former STPD
implementation retained as a parity oracle. This changes ownership, not the
accepted V1 corpus semantics. The original V1 parity-migration statement
“No V2 Human runtime evidence is claimed yet” is historical, not a current
project-wide verdict. Current scoped runtime/Human claims are indexed by
[root STATUS](../../docs/STATUS.md) and their exact owner receipts.

Corpus construction rejects duplicate session/bundle/export identities and
global record/transition collisions. It groups complete runs and any
cross-session semantic duplicates into the same deterministic split component,
writes multi-source provenance and canonical Parquet, runs corpus-level B0, and
optionally profiles Standard serialized state/action pairs with an exact local
tokenizer. Snapshots and smoke handoffs are content-addressed and immutable.
See [Human Corpus Lane](HUMAN_CORPUS.md).

## Current external-source admission

The exact `AlayaLab/AgenticSTS-trajectories` revision
`20f5170c420584935ec20e004498b4d4a3621f8b` has been audited. Its CC-BY-4.0 trajectory
scope is immutable and usable for inspection, but none of its 139,211 combat decisions has
the complete legal-action catalog, whole-run seed/root, and exact environment identity
required for ranking admission. Rank-eligible acceptance is therefore `0 / 139,211`.

Detailed historical states and sequential result/next-state pairs are not permission to
invent the missing evidence. The first Scheme 1 smoke uses the frozen 1,500-record
unified Human campaign gate and requires current-teacher decisions with authoritative
catalogs, exact action mapping, stable
successors, exact game/Host/Player Environment identity, whole-run roots, and declared
behavior-policy provenance. See the
[AgenticSTS data-admission audit](evidence/AGENTICSTS_DATA_ADMISSION_AUDIT_2026-08-22.md).

## Sampling

Rank batches draw primarily from rank-eligible sources and should be source-balanced.
Dynamics batches may draw from all transition-eligible sources. Candidate actions for one
state stay grouped for ranking loss; do not turn them into unrelated BCE examples.
