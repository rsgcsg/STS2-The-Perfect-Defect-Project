# Current project context, 2026-09-27

Use `rsgcsg/STS2-The-Perfect-Defect-Project` for new work. Current develop
includes PR #51 normal merge `9f895e8b533a25abe971d169000ae81760b8e76d` (parents
`ea25a735891e310be69cea436e962ec13f3c6392` and `f508d7a1a05c5c93aaf52b98929367fe2494c839`,
tree `6794fe8feda674e099329176859e40ea37634190`). Resolve live GitHub refs, CI,
source and deployed identity; current source/runtime authorities override this file.

## Current evidence

- **Installed candidate:** Game Mod `0.2.0-rc.16`, component source anchor
  `ed40f0ebbfd59587eca0d5688f9c271062d85753`, was installed/cold-loaded with exact
  identity verification passing. The game is running for a bounded mouse
  target/cancel canary; its new recording awaits owner actions and attestation.
  This is not a passed Human gate. The [bounded receipt](../evidence/RC14_HUMAN_AND_LOCAL_WORKBENCH_RECEIPT_2026-09-27.md)
  retains separate RC14, RC15 and RC16 identities.
- **Local Workbench:** automated CUA opened the PR #50 browser from the game and
  browsed metadata after game exit. A separate isolated PR #52 candidate showed
  a no-login local home and 本机资料 1–25 of 326. The backend runs independently
  of the game. These observations are not Human evidence or a completed offline
  dataset/training workflow; exact instances are separated in the receipt.
- **RC14 Human session:** the owner attested mouse input for a separately closed
  session. It has 15 canonical transitions and 18 text-input begin labels;
  neither count implies the missing target/cancel labels. See the receipt for
  exact producer identity and separate audit counts.
- **Evidence CLI:** PR #51 fixes serialization of immutable Human input reports;
  all 145 Evidence tests and the exact private-bundle CLI check passed. Full run
  [36301622023](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36301622023)
  succeeded; the PR is merged at the develop identity above.
- **Local home candidate:** PR #52 adds the no-login local home behavior. Its
  authored source passed 95 Node and 17 Python tests. Head `2ed18832bd3bb58c6a751dd73c8663428b35e32b`
  normally incorporates PR #51; its Python-scope
  [run 36302839895](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36302839895)
  is pending. Superseded run 36302306868 was cancelled, not passed.
- **Mouse continuation candidate:** reviewed source `11938165425833ad4f7364edeba2d77adf995f6a`,
  component identity anchor `ed40f0e`, with Game Mod rc.16. Local exact-game checks
  and 297 Annotator Core tests passed; loaded mouse-target/cancel behavior still
  needs a separate canary. Untargeted mouse and ally-target stages remain unsupported.

## Current implementation direction

The [approved in-run text plan](../plans/TEXT_STS2_IN_RUN.md) limits system
navigation to eight menu kinds: Information and seven lists. Installation, full
scene coverage, new Human evidence, learned memory and policy quality each need
separate proof. PR #49 is merged with explicit small-B shared-observation
training selection; old model defaults and checkpoints remain unchanged. The
candidate scoring path is linear in menu count at fixed state/action lengths,
not linear in total sequence tokens. Learned memory and an independently trained
reset control remain unfinished; see the [progress receipt](../evidence/TEXT_MODEL_WORKBENCH_PROGRESS_2026-09-27.md).

## Next work

- Complete PR #52 independent review and hosted CI, then record its actual
  integration state and retest the distributed Workbench source.
- Build the remaining local dataset/assignment/use, training-job, analysis/report
  and archive workflows on the existing local services.
- Capture explicit mouse target-selection and cancellation labels before making
  claims about those interactions.

## Remaining Platform non-claims

### Historical evidence and authority

The [local supervisor and Agent collaboration contract](../AI_COLLABORATION.md),
[Stage1a task table](../plans/STAGE1A_TASKS.zh-CN.md), and [B0 handoff/archive
receipt](../evidence/STAGE1A_B0_HANDOFF_2026-09-23.md) continue to route work and
retain their historical evidence. Existing datasets, failed rows, source
provenance, purposes and Gold protections keep their identities; no old data is
relabelled or replaced. No new training run or paid compute is authorized here.
The local Workbench metadata view does not expose payload contents or perform
training, and does not make every feature work offline. No Full-Run Human
qualification, broad research admission, model-quality or scientific result is
claimed. Older accepted baselines remain in the [unified task-flow receipt](../evidence/UNIFIED_TASK_FLOW_2026-09-16.md),
[operating-flow receipt](../evidence/OPERATING_FLOW_2026-09-16.md), and
[monorepo Human gate](../evidence/MONOREPO_HUMAN_GATE_2026-09-15.md).
