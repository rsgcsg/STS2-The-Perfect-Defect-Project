# Packed B-PF v2: real-weight parity and bounded cost check

Producer: `96fc4fbd418ecb39066cef22e064617fff5ab3d4`, MPS FP32, two CPU threads.
Input: `e5eb679cd2fb4873010af2be1995539cafacff47572d8d9b64b5aa62028b303e`.
Qwen snapshot: `da87bfb608c14b7cf20ba1ce41287e8de496c0cd`.
This profile performs **zero optimizer updates**, exports no trained model, and
neither resumes nor replaces the operator-paused B-PF v1 run.

## Correctness scope

On real pinned weights, a short input (64 observation tokens, two actions of 8/11
tokens) compares a full packed forward against the optimized fixed-text/readout
decomposition. Maximum hidden difference: 0.0000991821; maximum query-gradient
difference: 0.0000715256. All elements pass the predeclared hidden atol=1e-4,
gradient atol=1e-3, rtol=1e-4 checks. This is bounded real-weight parity, not
whole-corpus numerical equivalence. The source tests separately cover candidate
isolation, permutation, scratch gradients and v2 resume/export (40 relevant tests).

## Original slow inputs, new execution

The same seed1701 plan's first three train inputs are measured sequentially with
freshly initialized v2 readout/head and no optimizer. Every decision makes one
fixed-text core call and one all-readouts core call, independent of candidate count.

| Original step | Observation tokens | Candidates | Packed tokens | Forward | Backward | Sum |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 293 | 2 | 466 | 0.537 s | 0.404 s | 0.940 s |
| 2 | 1801 | 9 | 4376 | 7.631 s | 3.828 s | 11.459 s |
| 3 | 1500 | 3 | 2535 | 3.459 s | 2.248 s | 5.706 s |

Step 2 fixed/readout core calls take 5.734/1.374 seconds. The readout boundary
reports 6.692 GB allocated and 8.628 GB driver memory; the largest sampled driver
value is 8.630 GB (decimal bytes). These are phase-boundary observations, **not
continuous peak measurements**. Retained driver memory differs from live tensors.
After backward live allocated memory returns to approximately 2.384 GB.

Historical v1 training logs report 1.613/154.882/457.986 seconds for these inputs.
Those timers cover an actual update including optimizer/gradient checks; the new
profile has no optimizer and a different warm-up/parameter/cache history. Thus the
observed improvement is useful engineering evidence, not a controlled speedup ratio
or a finding about which subsystem caused each historical stall.

## Next bounded run and remaining work

The planned first ten training inputs have packed lengths 466–5521; fixed dev inputs
reach 8156. The profile covers only the first three training inputs, so remaining
lengths and evaluation remain to be exercised by the ten-update pilot. Preserve
complete inputs, explicit failures and checkpoints; do not truncate or claim the
whole request space has passed because this profile did.

Use a new `stage1a.b.pf.v2` run identity and seed1701 initialization, not the paused
v1 checkpoint. The latter remains historical/paused at 3/10. Same-version recovery
is unchanged. B-S v2 real training, exported model checks, Workbench/Mod integration,
full repository gates and scientific quality are still separate unfinished work.

Private receipt: `stage1a-packed-b-profile-20260918-211112/status.json` under the
research root, plus `profile.log`, `profile.err` and pinned launch specification.
Stderr contains ordinary Qwen loading progress; the receipt status is completed.
Receipt SHA-256: `7fd02af11f730469646944e0167fc65f4abb11cfd9fb7a2530b8313c055da572`.
