# STPD Schemas

The research state/action/transition schemas are the frozen structural v0 contracts. Their
fair-player semantic payloads remain extensible inside `facts` and `reads`; top-level meaning
is closed and versioned.

- `research-state-v0.schema.json`: one coherent fair-player semantic decision state;
- `research-action-v0.schema.json`: one legal candidate's visible semantic meaning;
- `research-transition-v0.schema.json`: one provenance-bound stable-successor transition;
- `data-manifest-v0.schema.json`: checksummed source, split, deduplication, and file lineage;
- `experiment-manifest-v0.schema.json`: one reproducible training or benchmark run;
- `model-artifact-manifest-v0.schema.json`: one frozen model/checkpoint artifact.
- `human-collection-profile-v1.schema.json`: one exact collection envelope;
- `human-collection-campaign-v1.schema.json`: one profile's worker and row target;
- `human-corpus-combination-v1.schema.json`: exact cross-profile compatibility
  and the final combined-corpus target.
- `frozen-joint-feature-manifest-v1.schema.json`: one immutable, rebuildable pooled-feature
  cache bound to exact corpus, serializer, input profile, and Qwen identity;
- `training-input-manifest-v1.schema.json`: one content-addressed training-host handoff that
  separates canonical research data from derived model views and caches.
- `rl-inference-3.schema.json`: the P-REWRITE `fast_chain` wire between a node
  aggregator and the GPU inference service (`stpd/rl-inference-3`; the old chain keeps
  `stpd/rl-inference-2`). Rules beyond the schema, executable in
  `tests/test_prewrite_wp0_contracts.py`:
  - the service scores and draws a catalog position only; legality stays with the game
    and Connector, and a score is never resent;
  - nothing seed-derived reaches the inference side: `game_instance_id` and
    `sampling.seed` are opaque, never derived from a game seed or world identity and
    never model input; seed pairing and holdout bookkeeping stay on the environment side;
  - before a position becomes a BoundAction the environment checks the identity triple
    (request id, game instance id, catalog sha256), the q vector, the segment mu and
    `applied` (arm id, decision class, T, eps equal to this game's `episode_open`
    sampling for that class); any mismatch or `refused` = `not_started`, the game is
    closed, nothing is retried;
  - inference timeout (no reply in time) = `not_started` and the game is closed; a reply
    for a closed game instance (timeout, restart, save load) is counted and discarded;
    a native action that was sent and not confirmed = `unknown` and the game is closed;
  - Stop releases control only after every in-flight row resolved (reply or timeout);
  - mu is fixed within a 64-row segment; the service evicts a mu only when no open
    segment references it; a segment of a game closed as `not_started`/`unknown` is
    sealed truncated, kept as original and not given to SGD;
  - a sealed segment is written to local disk before it is handed to SGD;
  - `episode_open.audit_retain_mu` (optional, default false) marks an R7 audit game chosen
    before it opens; every temporary mu its segments bind is archived with its weights
    (bound by sha256) before a row is scored with it, and the game's originals are kept
    in full; the flag carries no game information;
  - `single_choice: omit` (L1) changes training data: one-option states are not rows,
    their native steps fold into the previous row's outcome (`forced_steps`), and
    `max_decisions_per_game` counts model rows only. The user approved omit for the fast
    chain on 2026-11-09 ("这个我决定没什么问题。你自己检查隐藏的风险", WP8 card ③): fast-chain
    configs write `omit` (the fast-env field is required, so the launcher states it) and
    the old chain keeps `score`. Arms other than
    `control` still need their own approval; the default arm is `control`.

Rules:

- bump the schema identifier for incompatible changes;
- update `docs/INTERFACES.md`, tests, migration notes, and all producers/consumers together;
- never change the meaning of an existing field while retaining the same schema ID;
- validate manifests before training or evaluation;
- payload openness is not permission to add hidden state or runtime authority; executable
  leakage tests are the second line of defense.


The separately versioned Full-Run V1/V2 transition and SourceProjection boundaries are owned
by the executable strict codecs in `stpd/fullrun/contracts.py`, with round-trip and tamper
regressions. V2 preserves exact native decision lineage and execution catalog authority;
old V1 payloads/identities remain unchanged. Dataset loading also reruns the version-pinned
source adapter; structural decoding alone is not source qualification or research admission.

Developer combination/project v1 schemas bind installed public package identities and local
paths/endpoints. They contain no credentials. Compute/upload/receipt contracts are strict
executable codecs in cloud_jobs and hub; their IDs are separate from native evidence schemas.
