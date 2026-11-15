# Configuration workspace

Future experiment configurations live here after their interfaces are implemented.

Recommended layout:

```text
configs/v0/data/
configs/v0/models/
configs/v0/training/
configs/v0/benchmarks/
configs/v0/experiments/
```

A config contains no secret or machine-specific absolute path. It references versioned data,
model, serializer, Host, Connector, and benchmark identities. Each executed config is copied
or hashed into its experiment manifest.

`configs/v0/experiments/s1-human-combat-smoke-v1.json` freezes the bounded
owner-gated behavior S1 smoke. Machine-specific corpus/Qwen/runtime paths and
their exact hashes live only in the generated `.local/training-ready` handoff.

`configs/v0/experiments/s1-human-combat-live-v1.json` freezes the first
experimental S1 checkpoint-to-Connector lane. It admits only the complete
Defect A0 `play_card`/`end_turn` catalog and binds the exact local checkpoint,
Qwen, game, Connector and Modset identities without changing training data.

`rl41.json` and `rl5.json` are learner profiles (`"profile": "rl41"` / `"rl5"` in a
run's learner JSON; explicit sections override them). `rl5.json` is the RL5 fast-chain
long-run starting point: the RL4.2/S3 method values plus the measured training levers
(rows-v2, `prepared_compile`, `prepared_kernels`, one row bucket per group). It needs
`dataplane.sgd_process: true`; with `prepared_compile` the SGD process defaults to
`recompile_limit` 16 and a 300 s stall floor unless the dataplane sets them. The run
still supplies its paths, the inductor `cache_dir` (keep it across jobs) and up to 8
warm-up dumps chosen with `python -m stpd.rl.offline_step warmup-cover`.
`loss.hp_advantage_coef` (c_hp, default 0 = off) weights the per-combat HP score
(segment format 4): A = A_outcome + c_hp * A_hp before the one normalisation. A_hp is
in HP points (tens), A_outcome in 0..1 reward units, so c_hp is expected around
0.001-0.01. `loss.hp_advantage_scope` is `"combat_ticket"` (default: only single-combat
ticket segments) or `"all_combats"` (an experiment arm: every combat, full games too).
No source sends single-combat tickets yet (the fast env's `episode_open` names none), so
the fast-chain preflight refuses c_hp > 0 with `"combat_ticket"` (#568).
Both are frozen for a run. A fast-chain gate `learner-base.json` that has them must match;
a gate made before them takes them from here (`learner_config.gate_recipe`).
The fast-chain knob `hp_potion_mode` (default `"points"`: -5 per potion used from the
row on) set to `"value_head"` (format 4, c_hp > 0) drops the potion points and gives a
row whose own choice is `potion.use` A_hp 0, so using a potion is judged by the outcome
advantage alone; death points, combat weights and the HP-head baseline are unchanged.
