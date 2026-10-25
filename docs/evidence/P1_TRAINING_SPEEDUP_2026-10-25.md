# P1 training execution plan v1 — 2026-10-25

Status: bounded real-TRAIN qualification passed on exact worker source4ab791ea;
that source's full Linux CI passed. Final documentation/parameter metadata candidate
still needs its own checks and reviewed integration. No full quality control or
formal training was started.
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
The three recorded builds plus one actual GPU call reserve1.34731128 resource
dollars; a separate0.10 allowance covers bounded transfer/storage/operations. These are conservative configured bounds,
not an invoice. Reconcile original App/call/container before another allocation.


## Chosen plan v1 and current real evidence

The selected combination is packed gathers plus deferred complete score checks,
with original FP32, optimizer batch64, microbatch cap8, scene/text area262144,
CPU2, exact sample membership/order and dropout draws. No AMP, compilation,
CUDA Graph, micro2 or checkpoint migration is selected. Reference remains rollback
and legacy decoding; row-fused stays an explicit supported diagnostic/old-policy
reader; existing migration decoding stays for its own historical consumers.
Unselected branches are not another controller or default training path.

Exact executing source: `4ab791ea3e39e4474238773db9201eff138417cc`;
lock `23fed3426d340c85b32769ef2b7347a6cb9bf33b151cb48aad80a41d6fcc9e68`;
image `im-cdowSBS0clQ5WnwLuxDDRJ`; App `ap-DbCJeVK0P5QluCFIVjc6GA`;
call `fc-01M5RMBFRM7KKNMKYHSFPQ11ER`, one L40S/CPU2/RAM32768MiB,
timeout1800s, no retries. Full qualification1471.284s. CPU model is hidden as
`unknown` by the container, so cross-container CPU comparability is not claimed.

R2 performance `cb292d64544a859f075c97a76ffd0f99cb44bcce2b5f69f8aa331ffb686fdac6`
is the durable index to the exact input, throughput report, complete checkpoint and
exported package. Checkpoint analysis
`624ccd38a8916c8aa11a0447f87c86226b1ac6960b48dab71e5127c78f337e38` contains
70188861 bytes, SHA256
`8a72ecbe676aef117cc4e2b63931a4a45c4be7aae41dea1393883c3a14f9a57d`.
The model package is17697155 bytes, SHA256
`e6c9280184715e01f9ffdd6f25a4ec791d7aaa3083f8c26b73e7894fc67dcf31`.
These are calibration/reader artifacts, not a formal selected Model installation.

| Question | Observed result and scope |
| --- | --- |
| Complete shape coverage | All9315 TRAIN scanned: entities287/634median/918p95/1508p99/4181max; text tokens34..261; candidates1..32. Canonical point-ID grouping has9313 singleton groups and one pair; it is not the shuffled sampler's distribution. The original cap/area is preserved for any order. |
| Equal-weight numerical parity | 21 actual extreme/quantile TRAIN frames, training dropout, all parameter gradients including None/zero and all RNG states. Score/loss max difference0; gradient max6.8917871e-8, within predeclared score/loss3e-4/2e-5 and gradient3e-4/2e-6. No tolerance changed after results. |
| Packed alone | Same-allocation ABBA,4 complete batch64 updates per variant,1 warmup+3 measured: reference10.496188s/update → packed1.318558s,7.9604x short-window speedup. |
| Deferred checking alone | Separate C/B/B/C interleaved control: packed1.358566 → deferred1.306637s/update,1.03974x. Full model/Adam/RNG hashes identical for all packed/deferred clones. Do not multiply these factors into a full-run claim. |
| Longer stability | Fresh seed1/original full config,200 normal trainer updates; full TRAIN epoch plus3456 examples. Compute265.331s, original DEV cadence one check, no TEST. Peak CUDA1085879808 bytes (~1.01GiB), no OOM/nonfinite. |
| Complete recovery | Saved200-step state to R2; new OS PID33 versus original23 reads all70188861 bytes into an empty directory, strict full restore succeeds, next201 state SHA256 `55b15fb6af211e73b3ffd2880f56057f458ffb31ed31a273778e21d22ebdec33` equals uninterrupted execution. |
| Cold path / export | Recovery phase134.317s includes126.9s cached population reconstruction; fresh Store full read/restore/next update itself7.439s. Normal export/readback is equal. Root independently read the entire CP and package from R2 into another empty directory and loaded the normal model reader. No live registration or installation. |
| Existing product chain | Focused normal Application start/pause/resume/result/export/registration and legacy inference seams plus final CI cover the product contracts. The CUDA experiment is the bounded calibration path, not a claim that a new full Ops training Run or installed Agent was exercised. |

All seven formal numerical files in the checkpoint identity match current source
bytes. Later changes only clarify shape-report ordering and parameterize explicit
qualification resource bounds; no math, sampler, checkpoint codec or selected
execution changes after this experiment. Their normal/default resource tuple is
still the tested1800/CPU2/RAM32. A 5x dataset is not yet qualified; follow P1_T0's
short calibration and separately approved budget, rather than inheriting this pass.

## End-to-end planning, not a full training benchmark

Do not divide the school20.645h allocation bound by a short-window factor. The
actual historical compute total includes2200 records; the final selected trajectory
has2190 updates. At the new200-update mean1.326657s, the same2190-update work
projects to2905.4s (~48.4min), or~96.8min for the full4380-update epoch budget.
The new numerical trajectory may change early-stop length, so both counts matter.

| Stage | Historical observed | v1 planning basis |
| --- | --- | --- |
| Compute |41411.0s, all2200 actual records|~48.4min for2190,~96.8min for4380; extrapolation, not a new full run |
| DEV/selection |490.3s over15 epoch+1 extra checkpoint checks|one short-run check+selection3.50s;~1min at16 checks, longer budget proportionally; no DEV tuning |
| Worker verify/population |4359.7s over11 starts|one current verification320.2+projection245.8=566.0s (~9.43min); one segment is the aim, recovery still pays preparation |
| Save/export |1081.4s over35 CP:9 pause+17 periodic+8 schedule/best+1 last|no forced pause CP; estimated9–17 saves for2190 work. Current train-child non-update residual53.8s includes CP publication, one extra update and export, so using54s per save/export event is conservative (~8–15min), not a measured total. Do not add that composite twice. |
| Continuation |9 pauses, unknown aggregate idle attribution|zero forced continuation target; existing Ops insurance retained, original unknown handle is never resubmitted. Cache-based134s experiment is not complete normal Ops resume wall. |
| New-Run Application preparation |not isolated in old resource upper bound|four full Snapshot validations plus vocabulary/transfer still exist in normal prepare; allow20–40min as planning margin from measured cold costs, not a newly measured prepare endpoint |

Combined planning is roughly1.5–2.0h for2190 work, and2.3–3.0h if4380 updates
are needed. Keep the task's2–3h target as a conservative planning window until a
future authorized complete Run measures it. The old application preparation and
future DEV/early-stop curve remain explicit uncertainties. Use a target timeout
with margin (e.g.14400s), preserve periodic1800s and schedule/best checkpoints,
and retain same-Ops continuation with the original cumulative budget. No such
formal allocation is started by this task.

The next chain bottleneck is likely serial game evaluation:24 original natural
loss episodes total5099.816s, mean212.492s, median178.348s (96.605–772.010s).
At that historical mean100 games take~5.90h and75~4.43h, excluding orchestration
and unknown overhead. Source: the frozen replay EPISODES.csv matched by exact
run_id to original reports. This is a planning extrapolation of short old loss
trajectories, not the new App's throughput or future longer-game guarantee. Do not
repair game evaluation in this training task; await its own complete denominator.

## Failures, cost and evidence limits

Before GPU submission the non-author review caught the real CLI `-m __main__`
child-entry failure. It was fixed to the canonical module and regressed in the
actual top-level context. The first local preflight then rejected an old retained
profile's native combination; the existing read-only load option resolved this
without rewriting the profile or weakening Curation/remote identities. It created
no App or GPU request. An initial local assertion expected a score error for finite
scores with overflowing loss; corrected to its original loss assertion. A local
normal-Application check initially rejected the dirty candidate, then passed from
the committed source. These outcomes and the unused CPU image builds are retained.

There was exactly one paid GPU call, completed, and its original container was
independently observed absent. Three CPU-only image builds were recorded. At the
checked rates, all configured resource caps total<=USD1.347312; withUSD0.10
transfer/storage/operations allowance the conservative bound isUSD1.447312, below
USD3. Offer-to-confirmed-container-absence resource accounting plus build caps and
allowance givesUSD1.222415. Neither number is an invoice; no further compute was
started to consume the remaining authorization.

Current full quality comparison is unnecessary for this selected equivalent-execution
combination under the task's acceptance rule. No new quality/gameplay/generalization
claim is made. Micro2/AMP or changed stochastic/update semantics would need their
own approved complete quality comparison. This task never scores TEST, changes the
evaluation App/state, starts formal training, retries unknown execution or publishes
to main. Final merge requires latest exact-head selected CI and the evaluation's
**final** installed-App boundary; the earlier b623 install is not reused while
PR238 still requires another installation.

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
