# Current project context, 2026-09-27

Use `rsgcsg/STS2-The-Perfect-Defect-Project` for new work. Current develop is
`ea25a735891e310be69cea436e962ec13f3c6392`, the PR #50 merge. Resolve live GitHub refs,
CI, source and deployed identity; current source/runtime authorities override this file.

## Current evidence

- **Installed package:** Game Mod `0.2.0-rc.15` was installed and its loaded
  identity check passed with no errors. The root Agent's CUA workflow later
  quit the game; the process is not currently running. The exact artifact and component identities
  and post-quit check are in the [latest bounded receipt](../evidence/RC14_HUMAN_AND_LOCAL_WORKBENCH_RECEIPT_2026-09-27.md).
- **Local Workbench:** root Agent CUA first opened the PR #50 integrated local
  browser from the game and browsed metadata after game exit. In a separate,
  isolated candidate profile, CUA then saw a local home with no login prompt and
  loaded 本机资料 1–25 of 326; that service and the game are currently separate,
  and the game remains closed. These are automated UI observations, not Human
  evidence. The [receipt](../evidence/RC14_HUMAN_AND_LOCAL_WORKBENCH_RECEIPT_2026-09-27.md)
  separates the prior server from current candidate identity and limits.
- **RC14 Human session:** the owner attested mouse input for a separately closed
  session. It has 15 canonical transitions and 18 text-input begin labels;
  neither count implies the missing target/cancel labels. See the receipt for
  exact producer identity and separate audit counts.
- **Evidence CLI:** PR #51 fixes serialization of immutable Human input reports;
  all 145 Evidence tests and the exact private-bundle CLI check passed. Its
  Linux portability job passed and Windows was in progress at the latest check.
  PR #51 is not yet integrated.
- **Local home candidate:** PR #52 adds the no-login local home behavior. Its
  95 Node and 17 Python tests passed; Linux and Windows jobs were in progress at
  the latest check. It is not yet integrated.

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

- Complete PR #51 review and hosted CI, then record its actual integration result.
- Complete PR #51 and PR #52 independent review and hosted CI, then record their
  actual integration states and retest the distributed Workbench source.
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
