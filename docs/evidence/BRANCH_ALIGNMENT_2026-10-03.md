# Branch alignment and source closeout, 2026-10-03

This is a source-integration snapshot, not a release or runtime qualification.
Resolve live refs and the consolidation PR's checks before merging.

## Integration candidate

The active repository is `rsgcsg/STS2-The-Perfect-Defect-Project`.
The archived Platform repository is not a parallel development line.
Base: `develop@9556d21188b2deea027192567827a339a0ce50f7`.
Topic: `integrate/branch-closeout-20261003`.

The candidate retains this ordered stack through normal merges:

| PR | Exact source head | Scope |
|---|---|---|
| #140 | `bffb01210e5d0800887a893e04159127925cc02f` | public M0 product, declarations, native-window Host and M2 input/kernel foundation |
| #141 | `885e85ec1cf0e8ab66f56e6105e7bfc9e54d3486` | exact Modal recovery, portable filesystem and HTTP fixes |
| #144 | `1d00b60fad2829ec58a0d79e95cc38cd99a64ecc` | remote-result reuse, registration budgets and checkpoint diagnostic |
| #145 | `a253194873d0fcfdba3bd19eb431f82122d0c7c9` | bounded M0 train-to-game evidence report |

Independent source reviews cover the unchanged native slice, cloud recovery,
registration/result handling and bounded evidence wording. Predecessor hosted
results do not satisfy the new combined-head gate. The consolidation PR must pass
its own selected checks before ordinary merge to develop. Until then the four
original PRs and their branches remain open; close them as integrated/superseded
only after checking that develop contains each exact head.

The additional member-admission repair is
`aa1276681262414fcc8704f3ff20f14e1fe6e226`. Catalog and execution now share the
bounded no-follow download-completion receipt check; preflight, worker execution
and replay use it. Immutable member-source metadata keeps historical exposure
unknown for Gold admission, including previously indexed imports and fresh owners.
The receipt does not prove cryptographic Hub provenance or research admission.
The repair changes no database schema or original evidence.

## Retained independent work

| Work | Exact refs / disposition |
|---|---|
| Workshop release | #24 → #25 → #26 → #30 remain an independent release stack; #26/#30 are drafts. No publication is in this task. |
| Workshop consumer | #45 remains deferred. Its advertised PR base is stale `1820a365` and its reported merge state is dirty/conflicted; native/BOM compatibility and mergeability must be reconciled against live develop `9556d211`. |
| Native-v1 M2 engine | `feat/light-action-m2-text-engine-20261001@62c1303b` → `feat/light-m2-text-epochs-20261001@6e96ed31` → `feat/light-m2-worker-export-20261001@f03e62dc`. Preserve pending combined validation. |
| Native-v1 M2 consumer | `feat/light-m2-native-consumer-20261001@5f6e1592` contains the engine but not the epoch/export chain. Reconcile final engine/worker contracts before integration. |
| Data display | `feat/data-public-declaration-display-20261001@cfb68cbe` and `fix/data-facts-canonical-use-display-20261001@9e86fcac` contain unique changes; retain for owner review against the combined source. |
| Historical candidates | offline-evaluation summary `5c70a653`, closed PR44 page input `478b172e`, and accepted-data review combination `0f2eb4b0` remain traceable; none is silently merged or discarded. |
| Equivalent patches | environment admission `daafcd34` and preflight `5845c4a9` have no unique patches against develop by `git cherry`; retain non-ancestor refs as provenance. |

The four M2 branches are implementation candidates, not evidence that a usable
native-v1 model is integrated. The canonical M2 core and input foundation already
in the selected stack do not substitute for this missing product integration.

## Local cleanup and running producers

The initial inventory covered 228 local branches, 13 remote branches and the
remote HEAD pointer, plus 328 worktree registrations. Of the local branches,
200 were ancestors of develop and 216 were ancestors of the selected PR145 head.
Each deletion rechecked the exact current SHA and develop ancestry.

- Deleted 96 already-merged local topic refs without a valid checked-out worktree.
- Pruned 130 invalid worktree registrations; no worktree directory was deleted.
- Preserved three dirty worktrees and all four process-referenced worktrees.
- Preserved checked-out branches, unique unmerged work, main, remote release
  branches, private data, models and recordings.

The desktop checkout remains on local `develop@415e8e5a`, because a collector
process uses it. Source alignment uses the separate integration worktree; changing
the running checkout would change its producer without the owned update boundary.
The other observed services also retain their existing code. No service, game,
Hub, worker, installed package or cloud deployment was restarted or upgraded.

The local audit directory `/private/tmp/spireagent-branch-closeout-20261003`
retains exact pre-cleanup refs, per-ref dispositions, worktree status, process
references, deletion receipts and independent reviews. It contains operational
audit metadata, not a tracked raw-data or model bundle.

## Evidence and next boundary

The member repair reproduced 14 failures before the fix; its writer ran 94 focused
and adjacent tests plus scoped Ruff/MyPy. The lead independently ran 72 relevant
tests on the combined code (all passed), the complete repository guards (identity,
BOM, boundaries, history, CI contract and governance), and the final docs checks.
`project:closeout` and diff review were completed. These local checks do not replace
the combined candidate's full hosted Linux/Windows gate; that remains pending at
submission.

The latest checked-in [M0 report](../../python/docs/research/evidence/STAGE1A_LIGHT_ACTION_M0_CLOSURE_2026-10-03.md)
records 3,000 train / 16 diagnostic dev decisions and a completed 9,000-update run,
plus separate one-action and nine-action native canaries. This closeout reviewed
that report's bounded wording; it did not re-run training or reopen the private
runtime receipts. These observations establish neither independent-game
generalization nor full-game model strength.

Remaining product work includes full-scene native connectivity, environment and
scenario coverage, independent evaluation, M2 engine/consumer integration and
distribution. The public-M0 potion-popup projection gap and CLI-only remote-M0
operation path remain scoped follow-ups, not completed Workbench features.
No Stage1a completion, new Human/Gold admission, native/Windows parity, release
promotion or scientific qualification follows from this source closeout.

Rollback of an accepted integration uses a new revert PR with the required source
identity/BOM checks. Runtime rollback stays with each existing pinned producer;
do not rewrite original data, delete unique refs or move historical release tags.
