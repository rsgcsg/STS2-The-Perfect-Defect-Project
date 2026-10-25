# P1 training execution plan v1 — 2026-10-25

Status: implementation candidate; current Modal qualification and exact-head CI are
still pending. This record does not qualify model quality or start formal training.
Owner: training-speedup task, base `6808583ff9019e7c4168fe33bb9aaef2fbc8bf11`.
The [canonical definition](../../python/docs/FULLRUN_TRAINING.md#training-performance-belongs-to-the-existing-chain)
and [operator entry](../../python/docs/P1_T0.md#bounded-real-train-performance-calibration)
own future use; historical reports remain evidence, not another current SOP.

## Frozen question and execution

New runs select `MStdExecution.for_plan("mstd-training-v1")`: packed gathers,
original microbatch cap8 and scene/text area262144, CPU2, FP32 and deferred complete
score checks. Learning config, batch64 membership/order, sampling, loss weighting,
DEV selection/early stopping and TEST protocol are unchanged. A legacy Run without
a plan/policy remains reference. Explicit reference uses
`MStdExecution.for_plan("reference-v1").to_dict()` on a new Run. Model construction
and legacy package loading retain reference; a plan never changes a live Run.

Snapshot: `615318fe605eccd91fb4e39051cb99b18f62cebaad26af397081eadcb650f46b`.
Original Run: `39739842270bf66db440476e4a4659444746cec38a3350b4f7ab7f1b570d5064`.
Original calibration input: `488f669757f4ddf3ec80b10727481b18d8fa38cef877fd1f9d52607615877796`.
Original CP312: `05303485980068a49f97914fff9fdbcdba61e8281d58809f9dad19f87718af6c`.
Counts: TRAIN9315 / DEV446 / TEST558. Original config is seed1, epochs30,
batch64, lr0.0003, weight_decay0.01, warmup0.05, patience5, CUDA FP32, CPU2.
No TEST scoring, full quality comparison, migration or formal training is authorized.

The source edit to execution.py changes new adapter source digests because it is
inside adapter_identity's closure. Unchanged legacy inference behavior does not
mean unchanged new-source adapter identity. The evaluation App stays pinned;
training work never updates its installation or state.

## Bounded qualification

Use the original Application/Curation owner to verify its already recorded TRAIN
use and current purpose/exclusion/exposure constraints. This creates no new grant
or ledger and does no local corpus reprojection. Remote computation still verifies
the full original Run/Snapshot/parents before any model calculation. All heavy
projection, shape scans and training execute on Modal, with zero automatic retries.

Predeclare score/loss rtol3e-4/atol2e-5 and gradient rtol3e-4/atol2e-6; compare all
parameter gradients including None versus zero and stochastic state. Scan every
TRAIN point, select extreme/quantile real frames, preserve full candidate catalogs,
and include the largest scene/text/candidate tails. The bounded short run must
visit full TRAIN shapes and save complete model/Adam/RNG/sampler/DEV-best/history;
read original Store/R2 bytes into an empty directory, restore in a new OS process,
continue, and compare its next state with the same plan's uninterrupted state.
Short training is performance/recovery evidence, not a complete quality control.

Budget: aggregate USD3. Current published Modal resource rates are L40S0.000542/s,
CPU0.0000131/core/s, RAM0.00000222/GiB/s
([primary pricing](https://modal.com/pricing), checked 2026-10-25).
One CPU2/RAM8 build bounded900s costs at most0.039564 resource dollars. One
L40S/CPU2/RAM32 call, including1800+120+2 seconds, reserves1.22861928 dollars.
Two such calls plus one build reserve2.49680256; reserve the remainder for transfer,
additional build and failure overhead. These are conservative configured bounds,
not an invoice. Reconcile original App/call/container before another allocation.

## Original historical evidence and corrections

Original local task evidence is retained under
`/Users/fire/Documents/Codex/2026-10-23/` (not committed raw data):

- `nl/outputs/mstd-learning-curve-update-2190-events.json`: 2256 original events,
  11 starts, 2200 actual update records, 35 CP, 9 pauses, one completion. Final
  trajectory2190 includes ten repeated updates across attempts; retain their cost.
  Train41411.005869s, DEV490.333883s, CP1081.414369s (publish1072.015321s,
  encode9.399020s), prepare4359.748251s. Old TEST43.218636s/export17.792042s
  are historical, not newly accessed scores.
- `first-four-agent-training/budget.json`: school74322.101323s=20.6450h is an
  allocation upper bound, including7498s of old no-training allocations. It is
  not a measured end-to-end training wall or invoice. Remaining19420.588274s
  after measured phases/no-training allocations is unassigned, not proven idle.
- `training-performance-investigation/evidence/frozen-TRAIN-shapes.json`:
  652 median and4033 maximum describe only CP312+256 frames, not all9315 TRAIN.
- `training-performance-investigation/evidence/optimization-L40S-report05-packed-ABBA.json`:
  exact `8a7cffab4629027c24f375c590e550815fdb41a8`, four updates/256 samples,
  one warmup per variant; ABBA19.9493484→2.1856035s/update. Model/Adam differ;
  RNG same in that window; final four-frame raw score max difference5.00679e-6.
- `training-performance-investigation/evidence/optimization-L40S-report03-ABBA.json`:
  row about1.19x with exact model/Adam/RNG. `report04-future.json`: micro2
  1.529899s/update, changed RNG and0.1833205 raw score difference. Old row
  deferred checks10.701821→10.763834s show no independent measurable benefit.
- `first-four-agent-training/evidence/606-delay-fixed-event-read-benchmark.json`:
  647 events (not641), serial73.089370→parallel8 9.036545s, identical event-ID
  digest. Existing cloud CandidateReporter and remote progress use concurrency8.
- `training-derivation-qualification/evidence/failure-closeout.json`: lineA normal
  start HTTP400, jobs/attempts/GPU submissions0; detailed original cause unknown.
  `training-compute-assessment/five-call-summary.json`: lineB scope remains short
  throughput, not quality or full restore. PR225 final delivery records six actual
  automatic new segments; original events retain nine pauses.

Earlier exact archived sources, inspected in Git rather than inferred from PR titles:

| Source | Concrete original lesson |
| --- | --- |
| J-min `6cc2a0d7c90aca9ecd5751923261d3a7adc1d4f8` | CUDA q07 raw-weight tolerance failure; observation-only later result. Raw-score/common-bias behavior does not erase failed original assertions. Original receipts: handoff `replies/J-min/evidence/cloud-observation-08-retained-result-02.json` and `root-observation-result-review.json` |
| #141 `885e85ec1cf0e8ab66f56e6105e7bfc9e54d3486` | m0_modal.py submits once, journals handle, never retries unknown paid execution |
| #147 `71309adfcdfd808a6b99d2bc43abda9079ef1d7a` | bounded zlib wire binds logical length/hash and rejects corruption/truncation/trailing data |
| #150 `732a2d67bbd3718da62f87cd51eb3842abc23757` | real Adam108067322B+stage36015707B exceeded old128MiB; tiny fixtures missed capacity and repeated DEV cost |
| #158 `c8714976de1e01beace89fb17d59891c273833fa` | CUDA-trained artifact can use normal CPU inference/export; training device does not define inference device |
| #161/#163 `41ab5cd17ab7c28be19eb34292c22a65be6a2e51` | persisted submit intent and original unknown block redispatch; reuse current Ops rather than revive old campaign controller |

Original human messages were also checked in
`~/.codex/sessions/2026/09/10/rollout-2026-09-10T11-31-01-01a088f0-8c56-7662-985c-c7c2612d4e15.jsonl`:
2026-10-23T06:03:41.128Z (line414515) separately requests concrete speed work and
its long-term project position; T05:32:10.286Z (414068) allows looking beyond
migration; T02:33:11.857Z (411441) accepts same-Ops continuation with verified CP
and no unknown retry; T11:14:23.852Z (418101) explicitly defers old optimization
until the old batch ends. Current authorization comes from the 2026-10-25 request.
The 10-23 largest49MB session file is a subagent history, not the original human chat.
