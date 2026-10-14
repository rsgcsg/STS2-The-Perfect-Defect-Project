# Structured M2 remote numerical execution


Current J-min applicability (2026-10-15): this packet is assembled source for the
existing TrainingService. The authorized diagnostic is synthetic local work with
at most 1000 total optimizer executions and one Root-owned remote profile with a
cumulative cost cap of USD 1, including image/startup/idle/cleanup. Root alone
starts paid resources. No new model or first scientific training configuration is
selected. Historical A/B/C budget and unexecuted-gate statements below describe
those earlier source packets; they grant no additional budget or execution.
Current outcomes and exact source receipts are in the J-min handoff.

This is a source candidate for the existing application training operation,
not a qualified image, CUDA result, deployment or trained model. Application
journal/use reservation and selected result remain canonical in
`LocalTrainingService`. The provider adapter owns submission/reconciliation and
exact App stop inspection. Research owns numeric projection/checkpoint validation
and package export through the existing package owners.

## Frozen request and finite authority

Before Run publication, the source owner verifies its TRAIN partition and data
use. `freeze_remote_execution_identity` preserves graph, input, target, source,
control and existing source closure, while explicitly binding remote runtime,
FP32 device policy, the numerical TRAIN projection digest, the qualified target's
noncircular execution binding and extended remote source closure. CPU and CUDA
create different Runs. Image/runtime qualification must be obtained independently;
the presence of a target DTO does not establish qualification.

The pure request carries the existing `StructuredWorkloadRequest`, frozen identity,
trusted closed scope, finite grant expiry and original event sequence position.
The shared transport carries only `training_projection` and an optional explicitly
selected `resume_checkpoint`. The projection consists of TRAIN public model
frames, labels, boundaries and run/group binding IDs. It excludes original archive
bytes, capsule bytes, teacher payloads, store locators, journals and credentials.
Only the parent can replay source verification or reserve data use. A projection
is neither Human proof nor live parent authority.

`structured-m2-slice-v1` dispatches only
`stpd.workers.structured_remote_domain.execute_structured_remote_request`.
The handler verifies original request/projection/attempt/code/runtime bindings,
then stops starting chunks on the first expiry, wall, chunk, update or complete
workload limit. A single already started TBPTT chunk finishes as the declared
checkpoint boundary; the provider's independent hard wall remains necessary for
a hung chunk. No automatic submission retry, automatic resume or executable path
from request data is supported. Cumulative optimizer updates come from the actual
engine state; the provider's response counters describe this attempt's delta.

The result is a bounded candidate delta containing one numeric checkpoint and one
checkpoint event. Numerical training completion enters evaluation; it is not yet
canonical Run completion or model selection. The parent validates the exact saved
provider handle, actual terminal outcome and stopped App with zero tasks and zero
containers, then revalidates the complete checkpoint/event/tensor/counter binding.
It rechecks canonical source/use/current attempt before guarded adoption. Complete
training uses independent CPU fixed-weight evaluation and the existing package
exporter before `RunReporter.complete`. A failed publication retains immutable
partial artifacts for reconciliation; it does not grant another writer.

## Device policy and checkpoint semantics

The training-only adapter inherits the unchanged StructuredM2 graph. Parameter
validation requires the declared device, finite FP32 tensors and complete original
candidate catalog. Loss targets are created on the scores' device by the shared
training owner. FP32, TF32 disabled, deterministic algorithms enabled with
`warn_only=False`, and cuDNN benchmark disabled are explicit choices. CUDA requires
`CUBLAS_WORKSPACE_CONFIG=:4096:8` or `:16:8` before the numerical runtime loads.
An unavailable device or unsupported deterministic operation fails explicitly.
There is no CPU fallback.

Runtime evidence includes Torch, NumPy, Python, OS/architecture, threads,
precision/determinism flags and, for CUDA, CUDA/cuDNN versions and actual device
name/capability. The observed runtime must match the Run's qualified declaration
before training and at subsequent numerical boundaries. The remote closure extends
the owning training source/lock identity with the new device, projection, handler
and shared transport files; it does not relabel CUDA source as an unchanged CPU
closure.

The tensor-tree codec normalizes model, optimizer and memory tensors to CPU bytes.
The adapter adds explicit CUDA RNG bytes and retains CPU/Python/NumPy RNG state.
Resume validates identity, parameter/optimizer inventories, cursor/counters, memory
and RNG before restoring, maps memory and optimizer moments to the declared device,
and restores CUDA RNG on that same device. CPU export loads normalized weights into
an independently constructed CPU inference graph; it is not resume on a different
device and does not rewrite training provenance.

## Validation and later qualification

The source tests use synthetic public fixtures and the real serializers, engine,
checkpoint reader and parent validator. They compare CPU adapter updates with the
original CPU engine and exercise explicit resume, expiry, bounded work, payload
integrity, unconfirmed/nonzero provider stop and forged cumulative counters. Source
or synthetic CPU passes establish neither GPU execution nor Human/research quality.

The next GPU gate must use one Root-allocated attempt against exact assembled
source/lock/image/runtime. It must record actual CUDA forward/backward and optimizer
updates; compare the same synthetic inputs/initial weights against CPU reference;
confirm same-device explicit resume, CPU-normalized checkpoint and independent CPU
export/load; read the actual provider call and stopped0 inspection; and report
cold/image/transfer/CPU preparation/numeric/export costs separately. Start with
score/memory FP32 tolerances `atol=5e-5, rtol=2e-4`, preserving measured errors and
failure rather than widening tolerances silently. CPU/GPU bitwise equivalence and
speedup are not claimed. A CUDA smoke is not either arm's >=100 qualified N or
>=300 ACTUAL updates/whole-cohort result.

No paid provider call, image build/deploy, installation, weight load, raw data access
or runtime qualification is performed by authoring this source packet. Parent
review, assembled source tests, full selected repository gates and separately
allocated GPU qualification remain required before execution placement is enabled.


## Explicit independent batch execution

`TrainingRequest.batch_policy` is a numerical policy, separate from the existing
native `execution_policy` used by deployment history. It is supported only for
an explicitly configured remote structured placement (`modal-cpu` or
`modal-cuda`) on a scoped recipe. Omitting it preserves the CPU single-sequence
and remote single-sequence behavior. The independent-batch policy freezes 1–32
lanes, fixed independent run waves and `label_mean` reduction in the immutable
Run execution identity and saved training plan.

Each lane consumes that run's unchanged four-advance TBPTT chunks, reset and
score-only rules. Ragged or exhausted lanes consume no padded observations,
write no memory and add no labels. All actual labels in one round contribute to
one mean loss and one AdamW update. Lane memory is detached at that boundary;
completed runs and epoch changes reset their lanes. Checkpoints bind every lane
cursor and memory, optimizer participation, counters and same-device identity.
The reference is independent lanes at the same weights with accumulated
label-normalized gradients, not a different sequential optimizer update order.
Forward calls retain the original graph; vectorized GPU speedup is not claimed.

The existing recipe now owns source/use revalidation, remote Run preparation,
parent adoption and explicit checkpoint resume. The parent rejects forged initial
metrics, lane cursors and inactive memory before publication. A lost provider
response still yields unknown after an original-App stop inspection; stopping an
App cannot promote an unknown call/result to known or permit automatic replay.

`python -m stpd.workers.structured_remote_qualification --device cuda:0 --updates 2`
runs the bounded synthetic device comparison and same-device resume diagnostic
inside an independently allocated exact image. `actual_updates` counts the main
engine; `diagnostic_total_optimizer_executions` also counts the CPU reference and
resumed continuation. The CLI does not allocate a provider or establish source
admission. CUDA unavailability and numeric mismatch fail without fallback.
