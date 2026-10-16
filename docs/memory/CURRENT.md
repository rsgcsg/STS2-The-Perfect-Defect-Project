# Current — P1 r1.1 audit and native-readiness repair (2026-10-16)

模型方案冻结：开。New Model/Input/Target/data/training changes require user approval.
Resolve live GitHub refs; current source and original evidence override this file.

Read [working rules](../WORKING_RULES.zh-CN.md), [P1 contract](../protocols/P1_GLOBAL_R1.zh-CN.md)
and [active continuation](../plans/P1_R1_CONTINUATION_2026-10-16.zh-CN.md).

- Audit base: PR189 ff6761fd; reviewed incremental update9089fb04 (lint/active entry)
  is normally merged into this topic; develop41e62cd0. PR187 merged;
  PR189 remains draft/unmerged. Current repair topic: `codex/p1-r11-native-readiness`.
- Claude's product path is useful, but all audited terminal runs ended in first-act
  loss. census40:32 terminal/6 blocked/2 disk interruptions, across two cores.
  2428 decisions belong to the first38;2519 includes the interrupted39th. Old ref5
  remains4/5 plus a separate repaired-seed rerun. These are exploratory results.
- T7 source repair1ad27100 removes arbitrary-partial grace. Existing native
  settling is handled before capture completeness; polling only schedules reads.
  Original budget/Stop releases control; unknown never retries.
- One fresh Reference seed exercised23 settling captures and90 decisions,0 unknown,
  replay verified; stopped on unmapped deck_upgrade_selection.activate. This is
  T7 regression evidence, not complete-run or full-P1 acceptance. Existing dev Mod
  8c8c599d/artifact3c3f01e9 was reused; no new deployment.
- Next: repair terminal equality in replay tools before large paired suites;
  T1/T2 and the new upgrade mapping gap; T6 narrow callback/patch safety repair.
  Do not delete T6 assertions to get green. Human Source4 complete conversion
  remains a separate required user action after preparation.
- ff6761fd Linux failed lint58; update9089fb04 clears lint but fails5 Python
  typing checks; pytest has not started. Full selected project
  gate and final same-candidate suites remain required before merge/freeze.

## Remaining Platform non-claims

GLOBAL P1 is not frozen, Managed-equivalent, Human-validated or training-qualified.
Source/test, build, installed, runtime and research quality remain separate.
Merge/close require per-PR approval. No new spending, research configuration,
production release or destructive cleanup. Reuse already authorized operations;
retain exact raw bytes and old identities. This round stops at review + T7 repair
and bounded real regression + minimal rule corrections, not another platform rebuild.
