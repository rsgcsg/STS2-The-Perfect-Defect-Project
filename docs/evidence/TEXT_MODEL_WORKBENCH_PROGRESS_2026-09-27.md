# Text model and local Workbench progress, 2026-09-27

This is a bounded execution receipt and work map, not full-game/Human/scientific acceptance.
No raw observations, private paths, credentials or weights are included.

## Integrated source and observed runtime

- PR #47 source `a1d6a28b2ecee0010b93d1dba080c63d82481816`, normal develop merge
  `1ecaef8aa20b4d56ca11879d4d1d610bab2ea812`, tree
  `7e04fab7c2e407c516af63d3b3d191394c553f61`.
- [Full36240525460](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36240525460)
  succeeded; develop36296132771 reused its verified identical tree, with fresh guards.
- Exact rc.14 Mod installed and cold-loaded at 2026-09-27T05:04:18Z. DLL SHA256
  `bfb14ab382e7db1688340376e82ab5ed9bc0accd049bd1d6ce943109b2f374fe`, MVID
  `1bb73b36-6faf-4347-9f6d-665604d0f513`; game v0.111.0/41cef1ea,
  native runtime `8e58eb96fdd44dd98db540cf145a2427`.
- Mod path-source `a76f3bf5cc4ed5f04d6dbb6ec013081eeac529c5`, built workspace
  `244446d7884b51c6dcae497b4ee03a799ae4df86` has the same integrated tree.
- Workbench remained on source `949011122778ce3a5c3a8355a3203cdeace74c18`;
  the private text-menu Runtime profile remained rc.9. This did not update the
  shipped rc.6 consumer pin or re-label an older runtime.
- [Published compact live receipt](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/47#issuecomment-5852959768).

## Actual bounded Agent behavior

All runs used the existing two-step synthetic B-S-v2 engineering model, not a
policy trained on real gameplay. Limits were 16 submissions/32 calls/60 seconds.

| Run | Observed path | Final evidence |
|---|---|---|
| `run-e64519d5-738c-4884-b797-656f9cfd4551` | One native event action, then 15 information-menu navigation actions | Stopped, sealed, verifier pass, no taint |
| `run-e5d4f9de-5d7f-4079-997a-0be6ea11a0ce` | Rest site/potion popup repeatedly opened and closed, 16 native deliveries | Budget returned Human/released; stopped, sealed, verifier pass, no taint |
| `run-a8f30613-c9a2-4068-9287-2fdadeac4fed` | Two explicit One-Steps: upgrade card selection entered preview, then opened potion popup | Each returned Human/released; stopped, sealed, verifier pass, no taint |

The upgrade selection observation had 22 candidates, 12,189 state tokens and a
maximum 412 action tokens. One CPU scorer call took 0.194 seconds with finite,
exact-order scores. This single measurement is not a throughput claim. The model
did not confirm the upgrade or finish a run. Setup/navigation by the agent is not
Human evidence. Repeated loops remain failures; no candidates were hidden to
make the policy appear competent.

## Model and workbench increments

PR #49 head `9c1641c86c8c53928ceb55ef688e90c9a85db2dc` passed its own
[full36296753263](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36296753263),
actual tested merge `cd22323cc71d623ab292c837f60d3edce7bfe939`; normal develop
merge `51df77741c9beed4c805b7354bcc136adb963450`. The explicit small-B config
uses zero dropout to enable the shared-state candidate path in training and
inference. Old defaults/checkpoints remain unchanged. This is linear in candidate
count at fixed state/action lengths, not in total token length.

The local Workbench candidate combines existing-store metadata browsing without
cloud login and a Mod button opening the same browser Workbench after an exact
instance health check. Browser cookie protection and cloud permissions remain.
The native bridge carries no secret, command or arbitrary URL. Registration is
process-local, owner-bound and retried when the game starts later; clean Workbench
shutdown unregisters its own link. A crash or failed unregister can require a
game restart before a replacement Workbench can bind. This limitation is explicit.

A real Chrome check of a temporary, no-cloud, synthetic-metadata application
verified list and detail navigation. It exposed missing account controls on
local-only boot: a faithful regression failed (23 pass/1 fail) before the fix;
combined identity/project VM suites then passed 90 tests. Focused Python tests
passed 106 with no deselection after adding the locked research extra to the
private development environment. The earlier missing-boto3 failure was an
incomplete local test environment, not a Windows or product failure. This smoke
used Application/create_server directly; it is not a packaged application install.

## Next evidence and product work

- Exact current Human card-stage and cross-scene recording, with owner actions;
  existing Agent canaries cannot fill this evidence gap.
- Current-page sequence views and verified feedback, then M0/learned-memory and an
  independently trained reset control; memory is not assumed to solve loops.
- Local dataset/assignment/use management, training tasks, reports and archive
  operations through the same local services; optional team login adds sharing.
- In-game core operations and reconnect/provisioning beyond the browser-entry slice.
- Complete-run and unvisited-scene evaluation, with intervention/latency/quality
  reported separately. No paid cloud task, real-data training, Release/Workshop
  publication, or full-game acceptance occurred in this packet.
