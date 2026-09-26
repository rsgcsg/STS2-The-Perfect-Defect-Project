# ADR-0014: One observation, all B/C action readouts

Status: Accepted for implementation; real-weight cost and game qualification are separate

Date: 2026-09-18

## Decision and correction

The owner clarified that B means one observation input and all candidates processed
together by one Transformer, reading the final hidden state at each action's marker.
One shared set of weights called separately for each full state/action branch does
not fulfill that default execution requirement. ADR-0012's branch-local execution is
retained only as historical/reference behavior; its data and artifact contracts remain.

B logical input is `[O; a1; q; a2; q; ...]`, producing one vector per q and one shared
Linear score head. The same learned q initializes each readout, not separate action-ID
parameters. O only reads its causal O prefix. Each action/readout sees O and its own
causal branch. This mask holds in every layer. Branch position IDs restart at length(O),
not at cumulative candidate offsets. Ordinary triangular masking is insufficient.

C1 has the same single-readout graph; only an explicit successor objective and validation
justify a predictive interpretation. C1-N with matching inputs/parameters is B-N.
C2 puts K ordered readouts after each action in the same shared-observation sequence;
within a branch later readouts may see earlier ones. Default scoring uses the last
readout. K is capacity within one outcome representation, not random worlds. Neither
C introduces an E+J stack or separate FutureDecoder by default. C remains design-only
in this Stage 1a implementation; its supervision/teacher protocol is future work.

## Implementation and identity

Register `stage1a.b.s.v2` and `stage1a.b.pf.v2`, graph `b.shared-observation.v2`.
D-Simple's two v1 recipes are unchanged. Keep the B v1 factories and old artifacts
readable; the operator-paused v1 run is not resumed, migrated or called completed.

The initial scratch implementation executes the entire packed sequence in one
Transformer call. Frozen Qwen
may exploit the exact graph decomposition: first all fixed O/action tokens once
without autograd, then all readouts together with gradients using that shared KV.
That is two calls to the same core for disjoint token sets, not a per-candidate loop,
not two networks, and not a claim that the optimized physical path is literally one
forward invocation. A complete packed forward remains the verification reference.
Use additive masks with explicit positions and disable ordinary causal-mask inference.
KV lives for this decision only. Trainable prefixes cannot use frozen decomposition.

Implementation update (2026-09-26): scratch B v2 evaluation now computes the
observation once per layer and reuses that layer's projected state keys/values
for each isolated action branch. Zero-dropout scratch training uses the same
decomposition with autograd and activation checkpointing. The weights, positions,
causal graph and recipe ID are unchanged. Training with positive dropout keeps
the original single packed call because changing dropout draw order would alter
continuation from an existing checkpoint. That training path still allocates the
dense packed mask and has not been qualified for very large menus.

PF still trains only the shared query and Linear head (2049 parameters for width1024).
Its fixed backbone still performs computation. Packed dense attention has quadratic
physical-length cost even for blocked edges: correct sharing is not a speed guarantee.
The position budget applies to each O/action/readout path; physical packed length and
memory must also be measured, not silently truncated. No new 8192 data restriction.

Eval-mode outputs and parameter/input-query gradients must match the reference within
declared tolerances. Scratch training reuses the same prefix dropout realization across
branches; it need not reproduce independent-branch v1 updates. The new recipe/source
identity records that distinction. Same-version checkpoint resume remains deterministic.

## Validation and rollout

Test candidate permutations, replacing another candidate, exact single shared O length,
constant core-call count, full-packed versus optimized PF gradients, frozen scope,
and v2 scratch checkpoint resume. Real pinned-weight forward/backward timing and memory
come next as a durable no-update profile. Do not resume training merely to measure it.
Workbench/game admission, full gates and publication remain separate unfinished work.

Rollback selects explicit v1 only for historical diagnosis; it must not present repeated
state encoding as satisfying the new default. Original data, weights and pause receipts
remain immutable. No Platform gameplay or evidence authority changes.
