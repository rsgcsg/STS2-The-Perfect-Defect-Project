# Reset-K1 Workbench control, 2026-09-29

This is a bounded development comparison and product-path receipt, not a
memory-benefit, independent-game, native-play or Stage1a qualification.

## Source and checks

The real Workbench run used clean source
`b7e5dc0d6a30de6f04218b2c6f74f76cf23c2ea3`. Backend `d974083c` and UI
`e61968d5` received separate independent source reviews before integration.
The combined source passed 107 targeted Python tests, all 164 tests in
`python/tests/console_project.test.mjs`, scoped Ruff and mypy. No tests in
these executions were skipped. `git diff --check` passed. These are short
checks, not a substitute for the candidate's selected hosted gate.

Commands used the worktree's private Python 3.11.15 environment:

```text
python/.venv/bin/python -m pytest -q -ra python/tests/test_local_training.py python/tests/test_local_memory_model_export.py python/tests/test_local_memory_registration.py python/tests/test_local_model_registration.py python/tests/test_local_workspace.py python/tests/test_workbench_memory_recipe.py python/tests/test_memory_ranking.py
node --test python/tests/console_project.test.mjs
```

The normal merge of accepted develop `cd09b86d` into this candidate produced
`27150443` with the same tree `5f93a45552a17d94a77b1c06c0a776b81696c2b1`.
Later documentation changes do not replace the actual tested/run identity.
A subsequent normal base merge at `ccbc5f31` includes accepted Host PR100
(`develop@9732c44e`). The Python subtree is unchanged from `85af80c3`;
this alignment does not reclassify the earlier local measurements.

## One new operation, same recorded inputs

Safari's existing Workbench dataset page was used to choose **Reset-K1** and
explicitly start one new training operation. It completed, preserved links
to the previous M2 result, and produced a new independently trained model.
No existing checkpoint was resumed or changed to reset at inference time.

| Identity | Persistent M2-K1 | Independently trained Reset-K1 |
|---|---|---|
| Operation | `e614d9678f4a4cf7a74253bfd17c7655` | `d49ec1b6965c48a3bea504ff99d32522` |
| Run | `8b012441790907bdba3bc528945eea694bdb9840689cdc3d1a887cadb6b901d6` | `a09006cb5744cfb6b8f80e726375733016f14197c31f6b999e488d5caf156082` |
| Model | `ebe9dd8387adeb0f7ef9b903106a9ef9c80fcbd90de16a28c5dadfa6b2bd64a9` | `14a4f5df0a8ef5d6205e4bb71201b3e4f16224468838ef13be75bd7e5a8b7dec` |
| Input | `21f50895fbcb481716715a4bab0e3ce47b2d2e27335bce1d84dc40f8b1cfa6ca` | `480c91d08066ca751a46679f68419e3e5e208155e40607ac31501f535e742d20` |
| Checkpoint | `d0b3661af0ab52dd931ffc98fb096bb1476159fc81c27d89c72608e8a60f4143` | `047d592acb0f862593434e9d14df062b284ee720b44475c01e605eb40e322426` |
| Result | `8cc0e627b10108ae29d9a1b20ab37f4e26f196971548b3c8f90b2fc2ae9dff43` | `ebc46660a0a96c5ec41c3fd40761218648f1e81a58df1f6697dedc2905fe6033` |

Both consume source
`63c0198a37d18039e47bce29a3abe6e4cd81b09902a9452bf52fb49fb363d3ef`.
An independent read-only store verification found byte-identical episodes
(26,371,635 bytes), source map (327,617 bytes), and tokenizer (227,931 bytes).
The source has 584 events, including 548 model observations and 36 separately
retained settling events; 537 labels do not mean 537 independent games.
Settling events do not become invented memory updates.

The recorded seed, runtime identity, input digest, episode order and budget
match. The complete configurations differ only by `reset_each_step` false
versus true: width 48, one layer, two heads, feedforward 96, one memory slot,
ungated, dropout zero, seed 1701, CPU two threads, one training episode.
Fresh-engine regression checks equal initial weights and empty optimizers.
The real producer revisions and lock digests differ; immutable artifact IDs
therefore differ even though the verified input bytes match. Do not rewrite
either producer identity to imply they were the same source execution.

Recorded training attempts took 32.190 seconds and 33.368 seconds respectively.
These are individual local observations, not a controlled throughput result.

## Fixed development evaluation

After input verification, the Reset model was explicitly evaluated once on
the same separate source used for the existing M2 report:
`26e75e6ea837fcc6f616f8617e55677a97d74651c997f95e37795a5529ab70f1`.

| Recorded measure | M2-K1 | Reset-K1 |
|---|---:|---:|
| Labels | 31 | 31 |
| Top-1 matches | 14/31 | 14/31 |
| MRR | 0.640323 | 0.645699 |
| NLL | 1.373541 | 1.364116 |

M2 report: `e109af7fa9ff5a0649f8829f4c6c2f08657e98f9d1410379fdd7a0bb4fe537a8`.
Reset evaluation operation: `81911649075d432eba3fa831854fabda`;
report: `a3f3aacbc035a3b4a3d166d94eeff3bc1b3138431053493f8a934dd5b38321ca`;
evaluation input: `bed1c2e83fcae19086c539e26517e8ebc5dbbc2fd7d39e3652bac135df021764`.

Both reports retain `independent-source-retrospective-v1`, engineering-only
qualification, unknown model-selection exposure, no established strict
deduplicated benchmark, and unproved native-game independence. The two
recorded groups are fragments, not proof of two independent games. Rendered
train/dev overlap was zero. No scientific verdict or memory benefit follows
from equal Top-1 or the small changes in ranking metrics. No tuning followed.

## Export, registration, and a remaining setup gap

The same browser flow exported and verified the new model. Export operation
`5f7b0c3b90434140aa59ad5699f8356e` completed; package SHA-256 is
`46be06ef8e63b5a55145cd5e0182676d89c507f332b734f117b15fbbfac60e68`,
weights SHA-256 `dd9cdfc9c96b87236de69b5262989adf0f8346eea500dd2e221d654e5791023e`,
tokenizer SHA-256 `d7e64760cbfa197d0831c82a3bf8640da16e355b07f9ffa4e0af553cf086e128`.
Payload bytes total 4,659,079. These private weights were not committed or uploaded.

The first browser registration request displayed the unavailable-runtime message
and did not create a selection. This failure was observed in Safari; no raw
POST failure receipt was retained. A subsequent read-only owner lookup failed
on the absent private runtime-profile pin, while exact install validation
confirmed the existing Runtime in shared application state. The original profile bytes
(SHA-256 `3453b93f5fd5347257b267f7de2cd85c5c3c26990a227c58753489343038418b`)
were explicitly copied into the new private checkout only after validating
the existing install against that pin. No package was reinstalled, repinned,
loaded or started. A second explicit browser request then registered
`local-text-m2-34c57b7943e24f8a88b0247decae1a3e` with a Reset-K1 label and
the verified Reset recipe, not an M2 recipe inferred by the UI.

This manual configuration carryover is a remaining application lifecycle gap,
not proof of one-click environment setup. Operator pins and private model
registrations still depend on the source checkout, while installed packages
live under application state. Their owner separation needs its own reviewed
change; do not mask it by claiming installation or silently copying profiles.

No Reset model was loaded into the game. No gameplay, native full-run,
cross-platform installation, K8, Qwen, Z/O, 10k training or policy-quality
qualification was added. Old models, reports, failures and native evidence
remain unchanged.
