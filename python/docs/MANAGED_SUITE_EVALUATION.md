# Managed suite evaluation

The research evaluator measures the random Agent's natural terminals, quality
metrics and runtime costs over a declared full seed plan. It uses Host Runtime's
`ManagedPlayerEnvironment` reset/observe/step/close and `FiniteActionView`; it does
not introduce a Host controller, legality engine or operational evidence ledger.
The owning read-only `spireagent.workbench.managed_task_outcome` reader produces
common `AgentTaskOutcome` only from its verified Managed raw grammar. The run
retains an exact evaluation source snapshot, file index and external index SHA;
the reader rechecks bytes, config TaskSpec, game/build/load tuple, reset/seed/runtime
joins, ordered mutation offers/receipts, public terminal body, final episode end
and independent close facts. Changed, missing or mismatched evidence remains
unknown. Existing sealed `agent_task_outcome.project`, native/public M0 TaskSpecs
and archival readers retain their semantics. Raw Managed verification is a
separate scope and never sealed Policy Runtime evidence, native ready-summary
completion or qualification.

Versioned manifests in `stpd/evaluation/suites/` bind game v0.111.0, character,
A0, purpose and `derive_game_seed(suite_id, ordinal_1_based)` seeds. Smoke/dev/test/
ref and five per-character generalization sets are disjoint. `seed_membership`
and `require_non_evaluation_seed` expose the reserved seed exclusion query for
training/collection owners. This module does not retrofit their existing jobs.
`ref-20` is consumed by the Reference owner or future paired evaluator; the
Managed CLI refuses to execute it. Pairing must reuse the same manifest.

Run one private directory per attempt:

```sh
python tools/evaluate_managed_suite.py --suite smoke-20 \
  --candidate /ABS/prepared-candidate --run-dir /ABS/private-runs/eval-id
```

The default uses the existing exact released Host package loader. The explicit
`--source-host-runtime /ABS/components/host-runtime` mode consumes local source
and retains the Host's original ready/build/game tuple; it makes no released
package or installation claim. Each episode creates and closes a dedicated
Managed child. No GUI or Human control is involved. Configuration/meta/report
and per-episode original ready, seed provenance, Snapshots, Receipts, errors and
close receipts remain private. Existing directories are never overwritten.

Every offered reset is counted as started, even if no reply returns. Natural
win/loss requires the complete public Managed game-over surface, including its
boolean victory fact and empty complete action catalog. Cleanup remains an
independent fact. An unknown/malformed/missing mutation receipt ends the episode
and fences new suite launches. There is no unknown retry. An explicit stale
not-delivered refusal permits a new observation and fresh uniform catalog choice.
The random Agent receives only the complete action identifier tuple. It receives
no Snapshot or Read content; the runner uses public Snapshot fields for identity
and outcome measurement. No Read calls occur. This does not verify all Host
information as fair or qualify a P1/teacher data package. The fixed RNG is
derived per planned seed from Agent id and `--random-seed`;
worker scheduling does not change that seed's random choice sequence.

Finite per-episode wall/step and suite wall budgets are recorded. The wall timer
calls the existing Host client's force-close owner without another gameplay RPC.
A deadline after an offered mutation without a receipt remains unknown. Original
close/fallback/error facts determine whether cleanup is confirmed. Unconfirmed
cleanup stops the suite. Workers can be configured, default 1; already running
workers retain their own records after another worker fails, while queued seeds
stay unstarted. No process-exit-only cleanup inference is added.

Reports keep every planned seed in the denominator, including canary-unselected,
unstarted, budget, unsupported and unknown rows. Win rate and Wilson 95% CI use
the planned denominator; natural terminal rate, floor/act distributions (including
missing), observed surface/catalog counts and end-to-end decisions/second are
reported independently. These do not establish Reference equivalence, complete
interaction support, P1 r1 qualification, Human origin or strategy quality.
A canary `--max-episodes 1` keeps the full suite denominator and deliberately
cannot pass the complete suite.

`test-1000` is held out and requires both `--allow-test` and `--test-purpose`.
Its explicit opening purpose and result are retained in the run; it is not for
tuning. Seed membership queries do not execute or open test episodes.

B6 report migration: new `stpd/b6-fixed-seed-report-v1` requires explicit
`planned_seeds`; no received-row inference is allowed. Empty plans/policies,
missing seed/policy observations, incomplete outcomes, unknowns and pipeline
failures fail. Duplicate/out-of-plan rows reject. The sole pre-existing Python
caller was the B6 regression test and now supplies its plan. Historical v0
reports are unchanged; a v0 report's missing plan cannot be reconstructed from
its rows.

Source/Test class: T1 for Stop/unknown and source-bound result verification;
metrics and suite manifests are T2. Rollback removes the new CLI/reader/manifests
and restores B6 v0 caller/API together; existing historical reports remain
unchanged. The source owner fixes the received-only B6 denominator, rather than
changing native legality or weakening unsupported projection gates.
