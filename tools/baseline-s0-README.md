# Bounded S0 collection and evaluation

This CLI reuses the shipped Host Driver, Connector sealed text-menu-v2 SDK,
Policy Runtime and trusted Python NDJSON2 adapter. It has no second executor.
The lead prepares the isolated `defect-a0-s0` profile template and exact installed
artifact first. The Driver explicitly selects Defect A0 and verifies its public
run identity. Build the existing Connector SDK and Runtime before running.

```sh
node tools/baseline-s0-runner.mjs collect \
  --installation /ABS/STS2 --local-root /ABS/host-local \
  --evidence-root /ABS/private-s0 --seed 1 --template-id defect-a0-s0
node tools/baseline-s0-runner.mjs evaluate \
  --installation /ABS/STS2 --local-root /ABS/host-local \
  --evidence-root /ABS/private-s0 --seed 2 --template-id defect-a0-s0 \
  --package /ABS/agent-package --python-command /ABS/python/.venv/bin/python
node --test tools/test/baseline-s0-runner.test.mjs
```

Budgets default to 60 policy calls, 60 submissions and 240000 ms. Flags
`--max-calls`, `--max-submissions`, `--deadline-ms` may only reduce those bounds.
The raw capture budget is 64 MiB / 4096 captures. SDK capture failure or ledger
write failure stops collection; native unknown is never retried. `--browse false`
disables the finite teacher information browse/return/revisit journey; this
parameter is recorded with the teacher source hash. This is an explicit public
heuristic teacher, not Human data or an optimal strategy.

`records.jsonl` uses `sts2.baseline-s0/raw-record-1` envelopes with monotonic
`record_index`, `run_id`, `recorded_at`, `type`, `payload`. Each `capture` stores
the complete metadata and a relative `snapshot_path` to exact original UTF-8
bytes, SHA256 and length. Raw capture alone is never a training row.

Only actual adapter invocation (teacher) or child port write offer boundary
(learned adapter) emits `policy_offer`. It joins the latest exact full snapshot
to its capsule and stores `offer_id`, `capture_id`, capture ordinal, bytes path
and digest, snapshot identity, continuity token, complete candidate digest/count,
`input_spec=s0-admitted-policy-offers-v1`, `source_kind=agent`, actor identity and
`I=false,F=false`. Successful `policy_result` joins by `offer_id`, echoes validated
completion, complete scores, selected index and chosen action ID. Failed/aborted
offers have no fabricated label. A projector must validate the actual capsule,
offer/result and current catalog; it must not infer offered inputs from polls,
receipt successors or captures, and must derive advance/score-only via the
versioned structured projection. Receipts/ticks/control are separate diagnostic
records and never model features. Public choice is not Commit or causal outcome.

`run_start`, `episode_identity`, `capabilities`, `runtime_identity` preserve
teacher parameters/source hashes, run seed/template, exact game/install/runtime
identity, original Driver provenance, compiled Runtime digest and generated
manifest. The existing AgentRunEvidence writer additionally records admitted
Runtime decisions, submit results and lifecycle. No raw files enter Git.

Every exit closes the owning episode. Runtime Stop precedes a fresh control GET
that must show the exact runtime with null controller; then the child adapter
and Driver close. `summary.json` preserves the explicit termination reason,
release confirmation and errors. Unknown termination may have confirmed control
release but remains unknown; it is not a successful native action claim.

Tests exercise public choice/browse, exact capsule joins, corrupt/over-budget
capsules, capability binding, unknown no-retry, Stop release and failure cleanup.
They are portable source tests; real installed/load/game/training qualification
belongs to the lead's exact native execution packet.
