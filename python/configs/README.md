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
`loss.explore_ratio_floor` c (default 0 = off, 0..0.5): a policy row of an exploration
arm whose ratio r = p_theta(a)/q(a) is below c takes the gradient c * A * grad log
p_theta(a), both signs of A, instead of the clipped surrogate's, so a rarely sampled
action still learns from its advantage; rows with r >= c and the KL stop are unchanged.
Control-arm rows (and rows of a source without an arm) keep plain clipped PPO; a dataset
with no arm identity is refused (`explore_ratio_floor_needs_row_arms`), and the old
`rl_update` path refuses c > 0. Declarable at a
continuation (`recipe_changes.DECLARABLE`); at 0 it is left out of the recipe digest.
With c > 0 the progress events carry `explore_floor_by_arm` and the readout shows it.
`segments.value_target` (fast chain only) is `"segment"` (default: each 64-row segment
ends on its own seal) or `"game"`: the learner holds a game's segments until the game
closes, spilling their bytes to `<live work>/value-target-spill` (node-local, sha256
checked on read-back), then stitches each segment's lambda tail to the next one, so
the real outcome reaches every row. Rows get older (a whole game) and `hp_share` reads
lower. A game that never closes, fails a spill or is held at stop is dropped and counted.
The KL stop is the same under both rules. Under `"game"` set `segments.capacity_victim`
to `"hash"`: a whole game enters the queue at its close and a dataset takes at most
`episode_rows` of it, so the default `"oldest"` drops the later segments of long games
first. `"hash"` drops the lowest packet digest instead. The run record counts selected
and evicted rows by distance to the game's end (`game_end_*_rows`).
The fast-chain knob `hp_potion_mode` (default `"points"`: -5 per potion used from the
row on) set to `"value_head"` (format 4, c_hp > 0) drops the potion points and gives a
row whose own choice is `potion.use` only the death points (no HP-change part), so using
a potion is judged by the outcome advantage, not by the HP it saves; death points, combat weights and the HP-head baseline are unchanged.
Ticket K adds two `segments` keys, both absent here (= default), resume-locked and left
out of the recipe at their default; the fast chain sets them from the profile knobs of
the same name (`learner_config.build`). `kl_stop_scope`: `"all"` (default, the stop
compares the KL over every policy row of a minibatch with `target_kl`) or `"control"`
(over the rows of the arm named `control` only, its save starts `control+save` included:
a save start changes the starting state, not the sampling; the profile requires that arm
at T 1 / uniform_mix 0; a minibatch without one uses all rows
and the attempt records `kl_stop_basis: "all_rows_no_control"`). Under `"control"` every
attempt also records `approx_kl_control` and `kl_control_rows` beside the all-rows
`approx_kl_mu_theta`. `lr_warmup_steps` (default 0 = none): before optimizer step k the
lr of both groups is the configured one times min(1, k / N); k is the restored update
count, so a resume continues the ramp and a resume after it stays at the full lr. The
lr used is already in every progress event (`lr`, `head_lr`).
