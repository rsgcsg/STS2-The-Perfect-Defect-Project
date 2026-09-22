# Packed B-PF v2 engineering training pilot

Producer `ef5d4c0cd3491cdb2aad51c427e2df0b767b136f`; training implementation is unchanged
from `96fc4fbd418ecb39066cef22e064617fff5ab3d4` (only the preceding profile evidence
was added). Recipe `stage1a.b.pf.v2`, seed1701, MPS FP32, ten updates, complete
public-compact inputs, no test/Gold labels and no game actions.

| Artifact | Identity |
|---|---|
| Input | `e5eb679cd2fb4873010af2be1995539cafacff47572d8d9b64b5aa62028b303e` |
| Model view | `2553accb6bf393809f0f813f94f73a1ccd266cdb6824a38e1591b1a1ebd478fd` |
| Run | `5155688c4c883b306fb4b143fe72aa86de1ec7ce5ba7761d6f4b603256800018` |
| Checkpoint | `c60cf91e0dbfff845e6593e6bfb6d652c948418d666b53afd59990c172d8ea61` |
| Result | `a64cbf58f1ddfeafd28fccc52713fbadef07ba8b9f3b743116962c6126a7fe00` |
| Model | `5773134919e3bf6d35b9b14c95a4379cc87ea495e0d937250aa4a00aa796a998` |
| Evaluation | `0778343a30e2a770c5964ab57f53182e11a5eb80253cdda86f1c53bfae4c712b` |

Training uses the same 49 train / 16 dev view as the two D-Simple pilots, with all
49 entries available to the fixed plan but only ten optimizer updates performed.
The old B-PF v1 remains explicitly paused at step3; no checkpoint was migrated.

## Measured execution

The wrapper took 191.888 seconds; the CLI took 189.064 seconds including input
verification. The worker attempt took 166.611 seconds including model construction,
updates, checkpoint I/O and dev evaluation. Actual step2/step3 timers are 10.241 and
4.726 seconds; the old v1 training recorded 154.882 and 457.986 seconds on the same
input plan. This confirms useful practical improvement, not a controlled benchmark
or an isolated diagnosis of historical memory stalls. Stderr contains ordinary weight
loading progress. The run completed without a reported OOM or training exception.

## Same-view comparison

`compare-tokens` verified the result/parent/payload closure and exact dev row identities
for all three completed configurations. These are descriptive engineering results:

| Configuration | Top-1 | Multi-candidate Top-1 | NLL | MRR | Worker attempt |
|---|---:|---:|---:|---:|---:|
| D-Simple-S | 8/16 | 6/14 | 1.720264 | 0.642783 | 9.769 s |
| D-Simple-PF | 4/16 | 2/14 | 1.721246 | 0.381424 | 76.375 s |
| B-PF v2 | 6/16 | 4/14 | 1.714594 | 0.523458 | 166.611 s |

One independent dev run and ten updates do not establish strategy quality, pretraining
advantage or a winning architecture. Total research cost includes preparation, checks,
the paused historical attempt and profiling; the last column is not that total.

Private evidence directory: `stage1a-packed-b-pf-20260918-211759` under the research
root. Logs, exact job/status/result and `comparison-cli.json` stay private. B-S v2
real training, full gates, Workbench registration and actual game execution remain
unfinished parts of 1a. Source/test and standalone export checks do not qualify gameplay.

## Initial export verification and follow-up

The first private bounded verifier restored the checkpoint, checked published weights
and frozen-backbone parameters, then matched the exported scorer exactly on the
original 18-candidate dev snapshot. Its subsequent **bitwise equality** assertion
on reversed candidates failed. The script did not persist the numeric differences
or a success receipt. This is an unresolved validation result, not evidence of either
candidate interference or harmless roundoff. A separate diagnostic will save both
ID-keyed score maps and differences before deciding pass/fail, using the existing
packed-reference regression tolerance (`atol=2e-5`, `rtol=2e-5`) declared in advance.
The original failure log is retained. No model parameters or attention masks were
changed in response to this assertion.

### Numeric diagnostic completed

The independent exported scorer diagnostic completed in 37.918 seconds, on source
`b49a3434f321aaed94d86bd070c398914142e816`. It loaded only the export and pinned Qwen,
then scored the same 18-candidate snapshot in original and reversed order. Both
ID-keyed score maps were persisted before comparison. Maximum absolute difference
was `8.58306884765625e-06`; every candidate passed the predeclared
`abs(actual-reference) <= 2e-5 + 2e-5 * abs(reference)` criterion. Top-1 and the full
ranking were unchanged. This resolves the earlier bitwise assertion for this one
snapshot; it is not an all-input invariance or gameplay qualification claim.

Private diagnostic: `stage1a-packed-b-export-diagnostic-20260918-212920`.
Report SHA-256: `a71e5bfc6e08a897020a613a531a33f161be203fe991ed6b16eb964fca40f20d`.

File integrity and action-ID binding remain exact checks. Floating-point score
comparisons use a declared tolerance; the original failed exact assertion is kept
as history. No retraining or parameter/mask change was needed for this diagnostic.
