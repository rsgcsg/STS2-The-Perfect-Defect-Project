# Interfaces

Machine-readable companion schemas live under `schemas/`. Payload schemas are intentionally
versioned before model code depends on them.

## 1. Player Environment port

STPD consumes a strategy-free interface:

```python
reset(seed) -> stable Snapshot
observe() -> current Snapshot
read(read_id, snapshot_id) -> Read result
step(bound_action_id, snapshot_id, mutation_request_id) -> Receipt + successor
close() -> None
```

Requirements:

- the Snapshot contains a complete finite action catalog before STPD acts;
- `bound_action_id` is opaque, state-bound execution authority;
- unknown delivery is never retried;
- stale refusal causes a fresh observation and new selection, not replay;
- the successor is the next stable interactive state or terminal;
- Host/session/native identifiers never become model inputs.
- localized labels may be evidence/model text but cannot be the sole action
  identity or deterministic policy-ordering key.

An episode and every derived transition are environment-invalid after any
`unknown` delivery, incomplete catalog, settling timeout, missing successor,
replayed stale authority, mid-episode runtime/environment identity change,
request/action/Receipt mismatch, or unexpected environment exception. Invalid
data is quarantined; it is never silently retained for training.

Failure ownership starts with the same exact scenario: both Reference and
Managed failing points to Connector/game contract; Reference passing and
Managed failing points to Headless; both environments passing points to STPD.
Any identity change is a requalification event.

## 2. ResearchState v0

A deterministic player-visible object projected from one coherent Snapshot plus declared
Reads. It contains semantic facts needed for research and excludes:

- hidden draw order, private RNG, unrevealed content;
- runtime/process/session IDs;
- opaque action IDs except in local execution linkage;
- teacher/model identity or future outcome.

Every ResearchState records `schema`, `information_policy`, `game_version`, and a normalized
state hash. Its structural v0 contract is frozen in the tested Python type and JSON Schema;
fair-player semantic facts remain extensible only inside `facts` and declared `reads`.

## 3. ResearchAction and ModelAction v0

ResearchAction is a deterministic player-visible description of one legal candidate. It has
a dataset-local `action_key` derived from visible semantic content. The runtime
`bound_action_id` stays in an execution envelope and is never serialized into ModelAction.

ModelAction is the deterministic model-facing serialization of ResearchAction. Candidate
order is randomized or explicitly recorded for evaluation; localized display text cannot be
the sole identity or ordering key.

`ExecutionEnvelope(snapshot_id, bound_action_id, mutation_request_id)` is separate ephemeral
authority. It is forbidden in model input and must map bijectively to the semantic catalog.

## 4. ModelState profiles

- `stpd-combat-v0-lite`: minimum combat facts required by the experiment.
- `stpd-combat-v0-standard`: default v0 profile and all core architecture runs.
- `stpd-combat-v0-full`: broader player-visible context for winner-only ablation.

Profiles change information volume and token cost, not legality or environment authority.
Serializers are deterministic and versioned.

## 5. ResearchTransition v0

Canonical shape (the machine-readable schema and tested Python type are authoritative):

```json
{
  "schema": "stpd/research-transition-v0",
  "transition_id": "...",
  "episode_id": "...",
  "step_index": 17,
  "seed": "...",
  "environment": {
    "game_version": "...",
    "game_commit": "...",
    "game_artifact_sha256": "...",
    "game_artifact_mvid": "...",
    "host_kind": "managed_exact",
    "host_source_revision": "...",
    "host_source_digest_sha256": "...",
    "host_artifact_sha256": "...",
    "host_artifact_mvid": "...",
    "player_environment_implementation": "sts2_headless_managed_adapter",
    "player_environment_revision": "...",
    "player_environment_digest_sha256": "...",
    "player_environment_protocol": "1.0.0",
    "information_policy_id": "player_visible_v1"
  },
  "policy": {
    "source": "strong_teacher",
    "version": "...",
    "config_hash": "...",
    "teacher_confidence": 0.8
  },
  "decision_mode": "combat",
  "surface": "combat_turn",
  "input_profile": "stpd-combat-v0-standard",
  "eligibility": {
    "rank": true,
    "rank_mode": "full_listwise",
    "transition": true,
    "return": false,
    "legal_action_completeness": "complete",
    "reason_codes": []
  },
  "state": {},
  "legal_actions": [],
  "chosen_action": {},
  "successor": {},
  "terminal": false,
  "scope_exit": false,
  "outcome": null,
  "raw_ref": "raw/...#17"
}
```

Eligibility meanings:

- `rank`: the behavior source is eligible to supervise action ranking;
- `transition`: `(s, a, stable s')` is semantically reliable;
- `return`: the episode has a reliable terminal outcome.

Random/exploratory actions may be transition-eligible while rank-ineligible. A non-terminal,
in-scope transition without a stable successor is rejected.

## 6. Qwen backend port

The v0 backend is pinned to `Qwen/Qwen3-0.6B-Base` and exposes:

```python
encode_joint(state_texts, action_texts) -> pooled hidden states
encode_state(state_texts, return_sequence=True) -> hidden sequence + mask
embed_action_tokens(action_texts) -> frozen token embeddings + mask
identity -> model/tokenizer revision, dtype, device, frozen flag
```

Generation/chat APIs are not part of the core v0 interface.

## 7. ActionScorer

```python
score(model_state, model_actions) -> one scalar per legal action
```

The scorer must preserve candidate count and order. Selection happens outside the model by
`argmax` over the current complete action set.

## 8. Experiment and artifact manifests

`experiment-manifest-v0` binds source, plan, data manifests, Host/Connector/Qwen identities,
architecture/config, seeds, benchmark set, and output locations.

`model-artifact-manifest-v0` binds a frozen checkpoint to the experiment, files/checksums,
data manifests, metrics, compatibility scope, and non-claims. `stpd.artifacts` builds and
verifies this manifest without loading model code; unsafe paths and changed bytes fail closed.

## 9. Derived feature and training-input manifests

`frozen-joint-feature-manifest-v1` identifies a rebuildable Scheme 1 pooled-feature artifact
by exact canonical corpus, serializer/input profile, Qwen identity, compiler operation,
shape and file checksums. It contains no legality, reward or training authority.

`training-input-manifest-v1` is the training-host integrity envelope. It separates canonical
research objects from model views and feature caches, binds the exact STPD commit, `uv.lock`
and consumer entry point, and supports missing-object-only staging. Verification returns
`integrity_ready_authorization_required`; it never authorizes optimizer creation.


## Current Full-Run source interface

The independent `stpd/fullrun/contracts.py` codec retains ResearchTransitionV1 and adds
`stpd/research-transition-v2` for verified Platform bundle3 decisions. V2 records exact
occurrence/parent/root/native-origin lineage and the actual catalog authority; opaque
source identity is provenance, not a feature. `SourceProjection.accounting` retains the full
accepted/disposition/invalidation/journal population, while training rows contain canonical
committed decisions. See [Full-Run Research](FULLRUN_RESEARCH.md).

`PlatformBundle3SourceAdapter.project(bytes)` accepts only a bounded tar.gz of exact bundle
root-relative regular files and calls the version-pinned Platform verifier. Source archives
are not new Platform schemas. `archive_bundle(Path)` is a deterministic developer transport
helper; it neither modifies source bytes nor attests Human origin. `publish_source`,
`admit`, `publish_dataset` and `load_dataset` preserve and reverify the source projection.
Verified evidence is not automatic research admission; independent whole-run components,
no real lost decisions and source-bound records remain required.


`publish_received_source(store, received_evidence_id, producer)` consumes a Hub
`stpd/received-bundle-v1` artifact's exact tar.gz `archive` payload. It reruns the pinned
verifier independently, publishes a source projection with a `received` parent, and returns
the source manifest/projection for `admit` and `publish_dataset`. The transport parent retains
receipt lineage; it does not become research admission authority.

## Fixed decision datasets (candidate)

The curation candidate adds `stpd/curated-decision-dataset-v1`. New jobs carry
`curation: {purpose: training|test|gold, paired_training: artifact_id|null}`.
Confirmation retains the exact preview selection and quality snapshot. The immutable
artifact contains a compact selection receipt and a commitment to its internal report;
unselected observation/action context and quality comments are not downloadable receipts.
`POST datasets/materialize` queues the existing durable worker to create a derived
`stpd/dataset-materialization-v1` artifact only when complete records are requested.
Repeated requests return the same task; retry remains explicit.

Hub owns the transactional curation ledger in its Operations database. Whole-run and
transitive duplicate groups protect Gold across original archives and derivatives.
Publication reserves before writing an artifact; interruption retains the reservation.
Only Gold parents can merge into Gold. Hiding tasks/artifacts never releases protection.
Member download and training preparation recheck current reservations; wrappers and model
ancestry cannot relabel held-out datasets. Paired train/test selection and actual external
evaluation check overlap independently. Offline checks cover available immutable ancestry
and exact duplicates; they cannot discover copies outside that store.

Gold sealing is a managed-system restriction, not proof of zero historical exposure.
The manifest records historical external exposure as unknown. Existing immutable datasets
and recorded training uses are reconciled before sealing. Already downloaded bytes cannot
be recalled. After the first seal, rollback must retain the reservation-aware reader and
download/training guards, or enter maintenance mode; an older unguarded Hub is unsafe.

`GET collections/{upload_id}/decisions?limit=25&offset=0` reads the bounded operation index.
`POST quality-annotations` accepts upload_id, occurrence, action (flag/exclude/restore),
and a nonempty reason up to 500 characters. Changes append to private history. Excluding
a parent excludes dependent child selections; old raw evidence and dataset versions remain
unchanged. An annotation change between preview and publication requires a new preview.

`fullrun.decision_dataset.SelectionRules` is `stpd/decision-selection-v1`.
`fullrun.decision_store` publishes/loads `stpd/decision-dataset-v1` from independently verified
received bundles or exact `stpd/local-verified-bundle-v1` artifacts in the selected local store.
The local path rechecks transfer, raw-source hashes and the installed typed verifier before
projection, including cache hits; it preserves the local source parent and does not invent a
Hub receipt. Human input labels never substitute for canonical decisions. This source resolver
does not grant dataset curation, training/test use, Gold status or independent-run qualification.
The strict `fullrun.data` contract remains separate. See
[ADR-0007](adr/0007-fixed-decision-datasets.md) for defaults, bounds and version policy.

Authenticated member BFF routes: `GET games`, `GET datasets`, `GET datasets/{job_id}`,
`POST datasets` (name, explicit uploads, typed rules, nullable preview_id), and
`POST datasets/{job_id}/retry` (empty body; failed/cancelled tasks, same task and a new fenced attempt),
and `POST datasets/{job_id}/cancel` (empty body; before final publication). Browser requests
retain Origin/CSRF enforcement; personal tokens and device credentials are not interchangeable.
POST queues CPU work. A completed preview's logical ID must match a reproduced publication.
Game/profile reads use persisted summaries, not archive extraction. Artifact downloads reuse
existing export inventories and source sharing checks; these routes never submit GPU work.

The same POST also accepts `datasets` in place of `uploads` for selected-set union. Exactly one
input kind is allowed; a completed preview must match inputs and rules during publication.
`stpd/decision-union-v1` parents retain the original selected datasets. Its owner loader rechecks
parent selection and lineage; it does not expand excluded rows. Job reads expose durable phase,
work-unit counts, elapsed/observed times and explicit-retry status. Retrying continues the same
task ID, retaining prior failures in events. Private source indices and fixed preview selections
can reuse completed work; eviction falls back to verified reproduction. These checkpoints never
resume unknown gameplay commands. Game summaries cover all available shared profiles and report missing
coverage; they do not claim globally deduplicated independent games. See
[ADR-0008](adr/0008-selected-decision-unions.md) for cache trust, immutable contracts and limits.
