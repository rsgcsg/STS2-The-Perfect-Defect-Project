# K8 and independently trained Reset-K8 through the local Workbench

This receipt records a bounded product/research path, not a memory-benefit,
independent-game, native-play or complete Stage1a qualification. Times below
are UTC; the workstation timezone is Australia/Brisbane (UTC+10).

## Source and application upgrade

Both new operations used clean accepted source
`37c96d0b21cf353dfc29b4a869ab2dd031adf2ba`, Python 3.11.15 and locked
project digest `65cee52304ed1f958f95174c75236ede0d3b9beb9315881c12879adb982af4cd`.
Workbench executable digest:
`5ad1c8df6686b7e081ecf97b1b423db4e3f77ce6894e3d227883bd3725498020`.

PR115 repaired the first owning configuration defect: explicit setup replacement
now retains a previously selected external research workspace. The new regression
failed against the old implementation; the complete affected Python file passed
80 tests after repair. Its own hosted Python gate
[36616778204](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36616778204)
succeeded. Normal merge `37c96d0b` preserved candidate `224b6a15`'s tree
`1e60016b6a888f6f7fd5c393bcffeff24cb896df`. Integration
[36620542458](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36620542458)
used verified reuse and fresh guards/portable, not a second full execution.

Before upgrade the prior training and evaluation were completed, and no model
was loaded. The old local service was stopped through its existing owner;
private configuration was backed up and setup replacement kept the exact
research workspace. A new detached runtime tree was started; no source was
hot-edited in the running tree. Its private SDK dependencies were installed
from the existing lock; the compatible heavy Python environment was reused
read-only with project imports from the new tree. No environment was synchronized
into another writer's checkout. The new service instance is
`a91024b0241f265c6ca5a50e7972f673` (PID 21436 at observation).

The actual Chrome page was reopened and showed the retained local library.
Training and evaluation were started by explicit requests to the same local
Workbench application API, using its browser-session/CSRF protection. These
were API operations, not claims that a person clicked each training button.
The K8 report was also visibly inspected in Chrome: 31 decisions and Top-1
0.4839. Three metadata labels were still unknown in that view; a separate
owning presentation correction is pending, not hidden by this receipt.

## Fixed experiment identities

Both new models are observation-only D-Simple with lightweight action encoding.
They do not receive executed-action history or invented native feedback.
Candidate scoring reads shared memory without updating it per candidate.
Eight fixed slots keep candidate-dependent scoring linear in menu size;
this does not claim page-Transformer attention is linear in page length.

Training source/dataset:
`63c0198a37d18039e47bce29a3abe6e4cd81b09902a9452bf52fb49fb363d3ef`.
Shared training input:
`29e206e696a1f2655388fedc1639bfa05554fd4df08ea539cdc758561c746e2d`.
The source manifest establishes one recording session with 537 labels, not
537 games. Its separately audited native journey is described in the
[full-run receipt](FULL_RUN_POTION_EXECUTION_2026-09-29.md); the source manifest
alone does not establish native-run completeness.
No source, old model, old result or original recording was overwritten.

| Identity | Persistent M2-K8 | Independently trained Reset-K8 |
|---|---|---|
| Operation | `308bf727c8b94a2e8b597ee56f3efff3` | `7012864def034a72b7385e2c590f14fe` |
| Run | `f9f1455b9a8d1ea361c653a35996eda18b08eae0b03141a2e430fe167502d213` | `e5e58d5d2a584029615e618cddedb5ffe8831e3192be8489590b6224294ffca4` |
| Model | `8d45b9de339f33d1ae029ff3b9d69d121527408209c9ccd20e6f9982886f6fd4` | `dfe7ebf00b3baa96f634481f90dd66e04e9321e357038d822338d619e7b3fc72` |
| Checkpoint | `4958440adb4114c298ef359fcce6830879c2b40e57fc75527b5143fcec4dd481` | `dad79cbd2698a9e8e1550ae995f87abb342904bce271375b5ad1443260156279` |
| Result | `e8eb6f7a1a98eb445f37d5e199d706172f9d0bbc4642103e59d5db99c560b8a2` | `d2cbf5ab17d63410138eca3a5c2c0f9bb5e5b499ba5bc85f08e9d3cbe4082354` |

Each model was newly trained, rather than loading the persistent checkpoint
and changing reset behavior only during evaluation. Both configs use seed 1701,
vocabulary 7,348, width 48, one Transformer layer, two attention heads,
feedforward width 96, eight memory slots, no gate, dropout zero and two CPU
threads. Normalized configurations differ only by `reset_each_step` false/true.
Each has 1,106,065 trainable parameters. The lightweight candidate encoder and
all other model parameters are present in both controls.

The shared input contains one episode with 548 ordered observations and 537
labels from 584 source events. There are 3,133,167 token IDs, split into 275
bounded chunks (at most two observations and 23,403 input tokens observed per
chunk), resulting in 275 optimizer updates. One episode does not mean one
gradient update. The configured chunk token ceiling remains 24,576, total input
ceiling 4,194,304, observation ceiling 768 and menu ceiling 256.

Recorded attempt durations were 63.5459 seconds (K8) and 67.7525 seconds
(Reset-K8). They are individual local observations, not a controlled speed or
cross-platform claim. Log filesystem timestamps delimit log activity, not exact
gradient start/end times. All private input/model payloads were checked against
the store's recorded sizes and hashes; initial/final weights were not uploaded.
The exact common input identity above includes the same episodes, tokenizer
and source map, rather than merely matching recipe names.

## Development evaluation and limitations

Both use the same already selected dev source
`26e75e6ea837fcc6f616f8617e55677a97d74651c997f95e37795a5529ab70f1`,
with 31 labels in two recorded groups. These groups do not establish independent
native games. The protocol remains `independent-source-retrospective-v1`,
engineering-only, strict-deduplicated-benchmark false and model-selection
exposure unknown. Reported semantic/rendered train/dev overlap is a diagnostic,
not proof of game independence.

K8 evaluation operation `412d256ad3a74a69b0e027dc9c842adc` completed with
report `82d3d6ac372a9f2108a512fdd16f75eb68e3e7c53b91986eea4bb3f280317893`
and input `facd0a277cf079b5177d85e2ee6e9068696c789c676f3af5e73dab746d70c680`.
Reset-K8 evaluation operation `c09b514dc9bb4232908d5ecb03bb4e20` completed
with report `28faff8f6a91484a37bdbfe202c4625655ce620f9c4888e03861797c731bf4d9`
and input `5ee70e610a0c312c2cab0ea68a56c75f28a56ea28c615b63079edc51be9ffc06`.
Its completion was observed at 2026-09-29T19:51:04Z.

| Recorded measure | Persistent M2-K8 | Reset-K8 |
|---|---:|---:|
| Labels | 31 | 31 |
| Top-1 matches | 15/31 | 15/31 |
| MRR | 0.6629032258064517 | 0.6655913978494623 |
| NLL | 1.331823895031865 | 1.346694367151187 |
| Rendered train/dev overlap | 0 | 0 |

Equal Top-1 does not establish memory benefit or equivalence. The small ranking
metric differences are descriptive only; this tiny retrospective set does not
support a choice of architecture or a policy-quality conclusion.

The older [K1 control](STAGE1A_RESET_K1_CONTROL_2026-09-29.md) remains tied
to its original producer/configuration. A larger slot count, training completion
or a small retrospective score difference cannot establish useful memory,
better gameplay or win rate. No additional tuning follows this comparison.

No public package, model weight or raw recording was uploaded. Neither new model
was installed in the game or used to submit gameplay. The installed Mod was not
changed by this Workbench update. Private research artifacts/use reservations
and the two explicit bounded training/evaluation operations are actual side
effects. Scene checkpoint qualification, roughly-10k work, Qwen/Z/O, actual
interaction memory, native product and cross-machine delivery remain separate.
