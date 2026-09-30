# V2 Workbench preparation, 2026-09-30

This receipt separates the running application, installed packages, model
artifacts and native execution. It is not Stage1a acceptance or policy quality.
Private paths, weights and raw game pages are intentionally not published.

## Accepted application and existing native boundary

PR122 normally merged as `8441be3adbaab12340029025638bc4f43c1b926e`, tree
`600e56eab59350be20233a0cab5d6adeba1ca304`. Its head
`aa341bd738c5131388a83d20831fca4aeb128ac8` passed
[full 36666716000/1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36666716000).
[Integration 36669199106/1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36669199106)
succeeded using verified same-tree execution reuse, not another full dual-OS run.

The [installed-generation receipt](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/122#issuecomment-5904198787)
records the deliberate Workbench update and v1 M2 Runtime rc.12 to rc.14 switch,
exact original-profile rollback, then verified reactivation. The original rc.12
bytes remain. This does not change the installed Mod at
`de23ce167fddc5bfe149cd29f2773299b261845d` or make v1 models into v2 models.

The [bounded native canary](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/122#issuecomment-5904687687)
used v1 confirmed-interaction K1 in run
`run-e8b5ec86-1e94-4d17-b4c0-7cdd68812d87`: 15 decisions and 15 delivered
native inputs, followed by a terminal observation, Human/release and explicit
Stop/sealing. Typed verification found no findings in 82 events. However, all
nine combat decisions had no card-begin actions despite public playable cards.
Do not attribute that loss to policy weakness. The exact native UI predicate
responsible for the omission remains unresolved.

The later [read-only comparison](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/122#issuecomment-5904774184)
observed seven v1/v2 pairs at one new combat state: both menus offered the same
five card subjects and four other entries. V1 used `begin_card_play`; v2 used
`select_card`. This did not reproduce the earlier omission or prove its cause.
V2 already uses text-owned card/target intentions followed by an exact native
leaf, rather than requiring a mouse/controller card-begin UI state. Native
legality and execute-time revalidation remain owned by Connector/native code.

## Actual current-Workbench Managed sequence

On clean application `8441be3a`, the existing stopped-Workbench
`configure_managed_host` owner verified the previously qualified rc.20 package
and candidate before selecting `text-menu-v2`. The real game was not operated.
The Workbench was reopened with the same state and research workspace.

Package identity retained, not relabeled as current source:

- Host source: `35dcf11ac29495bfac76d34f7af50380f7a25e2a`.
- Component tree: `c883ad6fab2143cc5dab09b98de3e9c6a77c0c2c`.
- Archive SHA256: `f6ef479f1267c5afcfc4741d8e1ad52df8ddd713eb81123c827af46b2874a7d3`.
- Installed content SHA256: `f56978fd016834e5eef2b5afd5c81b8ad94d5c005e0dc36d6b036396116512bd`.
- Managed executable SHA256: `dd726fba38f4fc097a57e9dd4a5fe7d220bd94ea3527e132be31a31c95963d93`.
- Exact game assembly SHA256: `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`.

A browser-cookie/Origin/CSRF-authenticated driver used the real Workbench APIs
and public Host client. In 5.984 seconds it submitted:

| Action | Effect | Result |
| --- | --- | --- |
| Map activate | native input | applied, delivered |
| Select card | text menu | applied, no native delivery |
| Cancel selection | text menu | applied, no native delivery |
| Select same card | text menu | applied, no native delivery |
| Select target | text menu | applied, no native delivery |
| Play | native input | applied, delivered |

Each immutable event was read back against its exact before-context, selected
action, request and result. Stop returned `stopped`; the archived report contains
all six events. A subsequent GET did not mutate the stopped session. Driver exit
was 0. This is a fixed-seed engineering path, not six additional independent
games or Human demonstrations.

- Report: `927a29594d505ef53bc0ba6e6ed9a24489902a2d84b8f6b2c8023dc3ccd2c34f`.
- Reduced private receipt SHA256: `18a0068d041e6dcc956f9a3e385d8645a79a74f8caf3f5bc5737fcbc25e2b154`.

## Actual confirmed-interaction K1 training and export

The public report-import API explicitly admitted that current-instance report
for training. Old six-step sources in other temporary workspaces were neither
copied into this store nor relabeled. The resulting source remains
`managed_control_input_stream`, `engineering_control`, actor `unverified`.

The public training API ran recipe
`stage1a.dsimple.m2.k1.confirmed-interaction.v2`, then the export API completed.
The model uses width 48, one layer, two heads, one persistent slot and two CPU
threads, with the existing bounded recipe; no Qwen download or GPU job ran.
Independent read-only verification checked the training-purpose claim, exact
source-use record, indexed source and immutable input/run/checkpoint/result/model
parent chain. The worker completion marker records exit 0. Stored inputs contain
one episode, six labeled steps and one reset: the first step has no previous
action; the next five carry only the preceding confirmed interaction. No public
feedback field was added. Text selections and native deliveries remain distinct.

| Identity | Value |
| --- | --- |
| Source | `3fc977a3906c622d7e27fbda19d1f66ade0fd5cbe6cdba350de9c226f192ced3` |
| Training operation | `1b30b642e5b3407d909b0658d3791b10` |
| Training input | `472e6532500e7f0657c9ea1303986e8ecbe7e53bccbd1411148e0e1d41364fbf` |
| Run | `21fda1da5c1b43e8a653a76ae4a0c14463290bf0547cdf50736465d8b37bb5a1` |
| Model | `ba8f766871f93f4bb4536376dbfc8590490e25b73ca46b7f3a781f4ed28f6588` |
| Export operation | `01838589d4374ddd888ebd099b592544` |
| Export model.json SHA256 | `65ee06f6874db24b3c5786233efa0191542b30ebd9b273ceceac173e55879d18` |

The package keeps schema `stpd/experimental-m2-portable-policy-v1`; its renderer
is explicitly `stpd/m2-confirmed-interaction-v2`, input schema
`sts2.player-environment/text-menu-snapshot-2`. The package schema name does not
imply a v1 observation profile. Evaluation remains `not_run`.
Independent package validation against the exact v2 confirmed-interaction
profile and recorded digest passed. Port 3 is the expected binding for this
profile; export verification alone is not an actual port-3 load or run.

The first start request wrongly included an `after_completed_operation_id`
from a different dataset. The owner rejected it with HTTP409
`new_experiment_precondition_failed` before starting training. Source inspection
confirmed that this precondition applies to a new experiment on the **same**
dataset. An explicit corrected new-dataset request then created the operation
above; no uncertain or failed training job was retried.

## Remaining owner gaps

The registration status correctly reports `text_runtime_profile_required` for
`text-menu-m2-v2`; no v2 model has been loaded through this current Workbench.
An initial private Runtime profile is missing. Existing upgrade requires a prior
active profile, while the automatic first-install path requires a selected,
verified developer kit containing the exact v2 pair. The proposed explicit
developer initialization belongs to the existing Runtime-generation owner;
it must not bypass package/SDK validation or manufacture kit provenance.

Independent Managed v2 dev evaluation is also not yet implemented: current
evaluation and purpose admission accept Human history. A same-seed repeated
engineering sequence is not an independent dev set, and training/export success
does not establish memory benefit, native full-game connectivity or policy skill.
