---
name: platform-runtime-qualification
description: Verify a scoped Platform build, package, install, load, probe or runtime candidate through its owning lifecycle and report each evidence level; exclude ordinary source fixes and portable-only checks.
---

# Platform Runtime Qualification

Use this Skill when the requested outcome needs exact game-bound artifact or
runtime evidence. It supports a bounded diagnostic as well as qualification;
a diagnostic does not silently become a release or a wider qualification.

## Scope and current owners

Read the applicable AGENTS, active task and owning component/runbook. Use
[Testing](../../../docs/TESTING.md), [Versioning](../../../docs/VERSIONING.md)
and [Governance](../../../docs/ENGINEERING_GOVERNANCE.md) for canonical gates,
compatibility and evidence rules. Load Status and dated evidence only for the
current claims needed by this gate; resolve current owners instead of assuming
a historical checkout path is active.

Resolve the actual checkout/base/head, component source/contract/BOM identities,
build/package bytes, target game/Modset and observed installation/runtime. Keep
workspace provenance, component identity, protocol compatibility and loaded
artifact SHA/MVID separate. Do not require every participant to share a Git SHA;
apply the actual owner's compatibility/admission rules without waiving exact
native bindings, immutable inputs or an unknown compatibility result.

Use the task's existing authorization, requested evidence level, action budget,
stop boundary and rollback. If one is genuinely unresolved, continue independent
read-only preparation and stop only the dependent operation. A runtime action
already authorized by the user does not require another approval merely because
this Skill applies. Do not invent a universal attempt count or runtime budget.

## Execute the scoped gate

1. Verify the selected source/component/root gates and required local exact-game
   prerequisites under Testing. Use permitted executed-receipt reuse only within
   its stated scope. For changed producer/consumer seams, complete the faithful
   short integration check before freezing an expensive runtime candidate.
2. Freeze the candidate and hash its actual build/package. Review changed inputs
   before reusing an earlier verdict; identical component identity or old green
   tests alone do not prove current build, installation or load.
3. Install, cold-load, probe, close and roll back only through Game Mod/Host
   owners. Preserve profile isolation, exact admitted Modset and prior bytes/config.
   Verify the observed process/load generation and common artifact identity through
   the owning loaded-evidence envelope, not a path or old status file.
4. Execute only the declared runtime exercise. Distinguish known delivery from
   execution, effects, cancellation, Commit and causal successor. Uncertain
   delivery or post-submission transport failure remains unknown: retain any
   required taint, reconcile the original request through its owner and never
   replay it automatically.
5. Enforce the task's completion/failure/unknown boundary through the owning
   stop/cleanup path. Preserve original outcomes, actual child/process exits,
   release/pending state and useful bounded diagnostics. Starting another world,
   resetting a marker or changing request IDs must not bypass that boundary.
6. Independently verify the reached evidence against original records. Record
   source, test, build/package, installed, loaded, live exercise, journey, Human
   and qualification only where reached. Apply separate collection/data/model
   admission when in scope; runtime verification grants none of those claims.

## Output and unresolved boundaries

Return exact source/component/artifact/runtime identities, actual commands,
results and failures/skips by reached level, cleanup, rollback, non-claims and
any remaining gate. A failed prerequisite leaves dependent levels unclaimed;
it does not consume a separately defined real attempt before that attempt starts.

Stop dependent work for unavailable exact game/credentials, unsafe installation,
missing scoped authority or a Human-only step. Prepare the concrete reviewable
result before requesting missing approval. No Skill rule converts source/test
success, a merge or one bounded run into release, Human or research qualification.

## Trigger evals

- Positive: “Build, install and cold-load this candidate, then run one authorized
  bounded diagnostic and stop at its declared outcome.”
- Negative: “Fix a C# compile error and run portable tests.”
- Overlap: Native-Human capture/audit uses `platform-human-evidence` for its
  Human steps; this Skill covers only the distinct runtime/package gates.
