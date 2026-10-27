# T2 auxiliary outcome training preparation

T2b owns preparation, one user-confirmed cohort freeze and training. T2a owns daily
installation; T2c owns later offline shortlist and gameplay evaluation. The initial
base is `73378b15` on `codex/t2b-value-training-20261028`.

The input-v2 encoder and action graph are unchanged for the standard candidate.
Optional `MStdConfig.value_heads=true` adds independent 192→1 win and progress
heads (386 parameters), after the existing action initialization. Architecture,
TargetSpec and checkpoint schema advance together. Historical configurations omit
new default fields and keep the original N-only path. Runtime export strips the
auxiliary parameters; full-catalog action scores are unchanged for the same action
weights. These are demonstrator-outcome predictors, not an RL value or reward.

Only an unconflicted native natural terminal supervises either head. Stopped,
unknown and technical-only outcomes are masked; valid action imitation examples
remain. Progress uses fixed A0=48/A10=49 finish floors (the existing W1 observed
win/final-boss-map evidence), not a maximum fitted to the chosen subset. The mapping
and loss definition are frozen in `value_targets.py` and checkpoint target identity.
The objective adds 0.01 BCE(win) and 0.01 MSE(sigmoid(progress)) to the original
weighted action NLL; masking contributes zero without renormalizing by a varying
known-outcome count. DEV action NLL alone still selects and stops training.

Optional `model_size=large` doubles text/scene depth (4/6 versus 2/3), retaining all
widths and the same data, batch, precision, optimizer and target protocol. It is a
conditional comparison only when the actual school allocation has spare capacity;
implementing support neither allocates a second GPU nor authorizes extra budget.

The fixed Managed suite preserves the original 10 A0 and 2 A10 prefix and extends
to 40/10. `seed_membership` reserves every new seed, and direct M-std preparation
rejects a matching known source seed. It is never an RL collection suite. Unknown
old seed ancestry stays unknown. No game or Managed batch is run for this change.

The preserved original 100-slot scoreboard has 35 started episodes across several
checkpoints, not 35 episodes from checkpoint 438. Only five A0 episodes have 438
natural results. The user explicitly left the ten-seed pairing gap to T2c on
2026-10-28; T2b does not invent additional old 438 scores or change comparison rules.

Preparation uses focused source/type/consumer checks; required complete portable
checks run in CI, without repeating them locally during user recording. Exact
Run, input, source/lock, allocation, checkpoint and actual verification receipts
belong in the task report. This document does not claim formal training or model
quality, installation, loading, Human qualification or gameplay.
