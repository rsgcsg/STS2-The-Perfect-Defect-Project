# Human input local Workbench browser sample

Synthetic application evidence, not native-Human qualification or model quality.

## Exact source and boundary

- Source: `406ab8ae6172aca1aaf349746964c27cb1570959`, clean source during the trial.
- Safari application UI, actual local HTTP Application and disposable worker.
- UTC: 2026-09-27 17:51:24 through 17:53:18 (2026-09-28 Australia/Brisbane).
- Private isolated store and profile; two synthetic verified recording sources were
  selected. All fixture setup patches were removed before constructing Application.
- No user's library, game, team service, Qwen weights or paid provider was used.
- The trial process was explicitly stopped with exit 0; the original Workbench
  remained running and the browser was returned to its home page.

## Observed UI journey

1. Select two recording checkboxes: the page displays two selected sources.
2. Explicitly preview: pending disables duplicate preview; status refresh reports
   two accepted input labels and says training split has not yet been checked.
3. Explicitly save: the page reports a completed operation and links to the saved
   dataset. No training starts as a side effect.
4. Open that dataset: its exact training-purpose binding exposes the small-B command.
5. Explicitly start: the real CPU worker completes three steps; the page links the
   result, model and dev report.
6. Open dev report: one multi-candidate dev row, one session-scoped recording group,
   native-run independence unknown. Model and action-only Top-1 both equal 1 on this
   tiny fixture; this is not learning-quality evidence. Uniform Top-1 is 0.5.
7. Open model: B v2, scratch, CPU, three steps, with checkpoint/view/run links and an
   engineering-only qualification notice. It is not registered or loaded in Runtime.

The completed operation journal and object identities agreed with the UI:

| Object | Synthetic artifact ID |
|---|---|
| Dataset | `6ab363f1bfcccf21111adc1592b8152e733188342575e83a0ad923c582380b8b` |
| Run | `e575ab2e5ad7c69541b4177c8a7bff1c481cd286dcd450feb2fdaf9a21a3ef8e` |
| Model | `9bf8c81810424736d26c3e39e2d10ac8c8fecf3dfbb9911eb098912fc4507a88` |
| Dev report | `550f58248c1ecc5b220eafee625f7c9b00f9659b4cc4480912e2f2f9ec31f099` |

These IDs describe synthetic objects, not attached weights or private recordings.
The trial's two-minute wall time includes manual browser inspection, not training
benchmark timing. Cross-page selection and negative/security cases are Node/HTTP
regression coverage; this three-source browser trial did not exercise pagination.

## Related checks and later change

- At `7e8906d3c357c4f46a31fedc112a14509418b6f6`, the existing real three-step Human
  worker test additionally exported its exact result and loaded TokenDecisionScorer:
  at least two complete ordered action keys and finite scores; 1 passed in 5.26s.
  This proves the standalone scorer seam, not installed inference or game behavior.
- After the browser trial, `2c3d428d6ec91e91a54be4f9cfcffbce4e4b94f2` corrected the
  local clear-selection button remaining enabled after a clear. It adds no server
  command. Its full Node file has 117 passed, 0 failed, 0 skipped. That later button
  correction is not claimed as part of the earlier browser source identity.
- The final topic still requires its own selected hosted gate. PR #69's older
  green checks are not evidence for this changed source.
