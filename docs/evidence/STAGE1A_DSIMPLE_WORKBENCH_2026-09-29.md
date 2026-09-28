# Stage1a D-Simple and Workbench state, 2026-09-29

This note records the bounded source, activation and Human observations known
at this date. It is not whole-Stage1a acceptance, a model-quality result or a
new training receipt.

## Source and validation anchor

`develop@34f4d99491ad1ee4a2c5520769208b624daea576` integrates PR #83's
standalone D-Simple M2 computation. The hosted full run
[36442293932](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36442293932)
and develop integration run
[36445816685](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36445816685)
completed successfully under their recorded routing. The
[PR #83 activation comment](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/83#issuecomment-5873889101)
records the integration and local activation follow-up. These checks establish
source/test and bounded application evidence only.

## Local application observation

The owner-retained activation packet `stage1a-dsimple-workbench-activation-20260929`
contains `receipt.md`, `before.json`, `after.json`, `after-details.json` and
`doctor.json`. It records activation of clean application source
`c8b0871a6884e4cbf89bacbd7e4917268fcbe62b`; its source tree is the same as
develop at the anchor above. Doctor passed. The old and new Workbench processes
were stopped/opened through their owning lifecycle. No source-tree or app-data
migration was performed.

Read-only before/after checks matched the project-config digest, all three
completed operation-file digests, and all four training/export/dataset/model
status responses. Training, export and dataset operations stayed completed;
the model stayed idle and unloaded. The game bridge registered the new
Workbench instance while the game runtime identity stayed the same and the
recording remained closed. No new training, export, data import, model load,
gameplay request, team upload or cloud action occurred.

Safari's current training page showed the 31-label Human dataset and offered a
new D-Simple-S v1 CPU two-thread, three-step task. The old B training result,
model and report remained linked. Only status was refreshed. The UI task-entry
gap for the new recipe remains unresolved; the offer does not prove that this
recipe can be launched or completed from Workbench.

M2 computation is present in source after PR #83, but it is not the default
short-training recipe and was not used by the active model path. It has no
real-data training result. PR #87's sequence-training candidate is a separate
increment. As observed on 2026-09-29, PR #87 head
`40331aab28e4ebf050113434c975c31de826b57a` had plan/Linux checks pass and
Windows pending. PR #86 head
`a45a1d92dc75d246949ea06c6da4cac1ffd80607` likewise had plan/Linux pass and
Windows pending. Their source and check identities must be revisited before
later status claims.

## Human inputs and limits

The [two-fragment report](TWO_LOCAL_HUMAN_RECORDINGS_2026-09-28.md) retains 31
accepted Human input labels and 23 separate canonical decisions; two final
actions have unknown successors. The [PR #80 native canary receipt](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/80#issuecomment-5864511251)
records Game Mod rc.18, Annotator rc.14 and Evidence rc.19 with 9 input labels
and 7 canonical decisions, but no native run start/end. Do not add the two
views together or call these full games. Human native run independence remains
unknown. Human Gold is unavailable and no Gold label is inferred from a
recorded choice.

The 31-label set used by the earlier fixed three-step CPU B v2 run had
train 26/dev 5 under its then-current Human v2 grouping rule. That run is
historical and is not rewritten as M2 or D-Simple-S v1. Its dev Top-1 was 0/5.
The local activation did not run either recipe. The approximate 10,000-row
Stage1a target is not ready; its data and split qualifications remain open.

## Real and Managed Host text interface

Connector's current public text profile uses `text-menu-v1`,
`sts2.player-environment/text-menu-snapshot-1`, `menu` and `menu_actions`.
The existing Managed Host source
`components/host-runtime/src/managed-player-environment.mjs` still projects
`sts2.player-environment/snapshot-1`, `bound_actions` and separate Reads. The
previous Managed Player Environment probes therefore do not prove it implements
the current Connector text contract, nor that both Hosts have equivalent
native leaf behavior. Current text-menu compatibility, fair-player parity and
scene coverage remain to be explicitly tested.

Environment and scene management are an independent Stage1a line. A Managed
scenario repetition result is bounded to its recorded scenario, seed and
actions; it does not supply real-game equivalence, text-menu qualification,
full-run coverage or learned-policy quality. The whole 1a product remains
incomplete.
