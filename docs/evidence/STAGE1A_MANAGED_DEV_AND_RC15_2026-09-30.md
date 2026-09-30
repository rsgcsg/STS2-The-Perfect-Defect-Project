# Stage1a: installed rc.15 and the Managed scene-to-dev journey

Date: 2026-09-30. Execution receipt, not Stage1a acceptance or policy quality.
Private observations, game files, model weights and credentials remain outside Git.

## Accepted source and checks

- Runtime PR125 head `69fdeae526079d174d6c3c6329ea12b3021e6454` passed
  [full 36685764302/1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36685764302).
  Normal merge `df7c66078a5c56f650e2567f159d7c204b643b79` passed
  [integration 36693501225/1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36693501225)
  with verified same-tree execution reuse and fresh repository guards.
- Managed PR124 head `1d3ab294747ab967e06e34d0a9b829837f786aff` passed
  [full 36693791434/1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36693791434):
  plan, Linux, Windows and portable success; docs unselected.
  Normal merge `14738f7fc764b9ef077f805022dc52839fe0c02a` has parents
  `df7c66078a5c56f650e2567f159d7c204b643b79` and `1d3ab294747ab967e06e34d0a9b829837f786aff`.
  Its tree `747ea5932fc66a29a9dbda078a11d874b9cacf9a` equals the accepted candidate.
  [Integration 36698120360/1](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36698120360)
  succeeded with `scope=reuse`, `reason=verified_execution_same_tree`, proof run
  36693791434. Fresh repository/docs and portable guards passed; OS jobs were
  unselected, not newly executed full tests.

## Runtime package, installation and bounded native use

The owning installation procedure exercised rc.14 → rc.15 → exact retained rc.14
rollback → rc.15 activation. Existing model manifests, registry and Host configuration
were preserved. The installed Mod remained `de23ce167fddc5bfe149cd29f2773299b261845d`;
Runtime installation did not restart or replace it.

| Identity | Value |
| --- | --- |
| Runtime version | `0.1.0-rc.15` |
| Path-scoped source | `f8288a5940fd54dafce30b1e4c3124ceae28a7d1` |
| Component tree | `4f33904d79478a882b69c49b6f7da76ad8b83bd1` |
| Source digest | `4188ef735061fa566ddd1d1b0db789b7ab26763cef13056e36664cbadac3139f` |
| Contract digest | `72d52f1e9b801021be3e99bf25e17deee4dff9647a05cf521bd2346318e0a3b5` |
| Archive SHA-256 | `adebbe9862bb9ec97f0af05df44112073312c32483d3a1582a4ac26bbdea1dd2` |
| Installed content SHA-256 | `1bdc769f52136f01ae6cc6da56b3e2d0969033e3c2654f991130f433a6a63d90` |
| Observed code identity | `17489f2fb999919e0e454df46bed537691cdca71515c27b4b3e1632752f79116` |

Run `run-fbfa620e-98ce-41d4-8bb2-bb7b5171d957` made 16 policy calls and 16
submissions, reached its original 16-submission limit, handed off, then completed
one explicit Stop. Final state: stopped, unloaded, Human, released, untainted,
no Runtime errors. Typed Evidence verification passed with zero findings over
86 events; content digest `94e510542312cba547899b407c0302332a0fc783a2c746722e3892c098566267`.

Three stale rejections were explicitly `not_applied/reobserve`, with no native
delivery. Each was followed by a fresh observation, policy decision and request,
not replay of the rejected action. Three text-menu changes applied and ten native
operations were delivered; the latter included two plays and four pairs of opening
and returning from the discard pile. Those repeated choices are retained model
behavior, not filtered away to manufacture competence. Twelve nonempty previous
interaction references each identify an earlier, distinct, confirmed applied input;
rejected attempts do not enter history.

This does not prove that an immediately observed successor is causally attributable
to a card effect. It is neither a fresh-start Full Run nor Human-origin evidence.
The [installed/native receipt](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/125#issuecomment-5908270679)
retains the precise package, model and runtime boundaries.

## Workbench and Managed source-to-evaluation use

After PR124 integration passed, the existing application owner stopped Workbench
source `8441be3adbaab12340029025638bc4f43c1b926e` while models and tasks were idle,
then opened accepted source `1d3ab294747ab967e06e34d0a9b829837f786aff` from a separate
clean checkout. The new instance was `fd93d883b0123263e2877229784dc7e9`.
Sixteen protected configuration/registry/profile/model/session files remained
byte-identical. The existing private Python dependencies were reused read-only;
Node consumers were independently copied and checked against unchanged pins.
No dependency upgrade, model training, game restart or cloud action was needed.
This verifies an actual service/API journey, not a new browser-click or Mod-entry test.

The explicit seed `M2DEV20260930B` was saved as scene
`cd20bc3ea2f86e9fc6115233a417e7c914428a9c4c734e98c7bb767dd6e3d6cd`.
An isolated Managed Host rc.20 instance reported matching requested/actual seed,
Defect and integer Ascension 0. Its source was
`35dcf11ac29495bfac76d34f7af50380f7a25e2a`, component tree
`c883ad6fab2143cc5dab09b98de3e9c6a77c0c2c`, content SHA-256
`f56978fd016834e5eef2b5afd5c81b8ad94d5c005e0dc36d6b036396116512bd`.
The exact candidate build and game tuple remain in the immutable scene/report.

The operator agent explicitly chose six currently offered actions: enter a map
combat, select Strike, select its target, play, select Defend, play. Every request
used the then-current action/snapshot/continuity binding. All six applied: three
native deliveries and three text-only changes. One Stop closed the instance and
published the report. These are engineering labels with unverified actor, not
Human labels, model gameplay, optimal actions or a complete game.

| Object | Exact identity |
| --- | --- |
| Managed session | `47313ba27db440d6a19743a1ae68a557` |
| Sealed report | `d76464cc1e98c770db534cca13ad87cf19bc53386213c24d5f762cb1184fbeaa` |
| Imported source | `a87cc7e57bf124049db1bbb5e9cd716fd19bfae68912dcfe4bf7ba98c235b6f2` |
| Existing evaluated model | `ba8f766871f93f4bb4536376dbfc8590490e25b73ca46b7f3a781f4ed28f6588` |
| Model's earlier training source | `3fc977a3906c622d7e27fbda19d1f66ade0fd5cbe6cdba350de9c226f192ced3` |
| Dev operation | `ba8e7d1542554da59bd5fe5b6a02fa24` |
| Evaluation input | `e85c9e4d9342656bc7410b24777951e72f9848ebbf09950f803d9dd8ebe6d1b1` |
| Evaluation | `cd91273500f548ed80f4eea20205d8620107887618e027c55fc7daacdee087de` |

Report import admitted the source to the training-eligible research pool; it did
not train a model. The existing owner then reserved actual `dev` use and completed
one evaluation of the previously exported confirmed-interaction K1 model. The
new source was not retroactively added to that model's training lineage.
Host profile is `text-menu-v2`; research projection is
`text-menu-v2-confirmed-interaction`, with `max_settling_events=0`.

The Workbench result shows 6 choices, 5 with multiple candidates, top-1 agreement
5/6, MRR 0.9166666667 and NLL 1.1139184044. Train/dev rendered overlap count is 0.
The earlier six-label training source had seed `M2H0ST20260929A`. Different seeds
and no equal rendered pages do not prove independent native games, a deduplicated
scientific benchmark, memory benefit or quality: `engineering_only`,
`native_run_independence=false`, `scientific_verdict=not_claimed` remain explicit.
The one-choice map row also makes aggregate accuracy unsuitable as a quality claim.

## Remaining boundaries

- Full-scene exact native connectivity still needs representative current-version
  evidence; Managed coverage is not a substitute for a native merchant/potion seam.
- Current saved scenes restart a fixed-seed run. Combat/selector checkpoint restore,
  arbitrary state cloning and MCTS are not established by this journey.
- Approximately 10k qualified labels, Qwen/Z/O comparisons, multi-device distribution
  and the complete user journey remain separate Stage1a work. Existing Human input
  labels do not require fabricated Commit/successor pairs; transition prediction
  needs its own stronger evidence.
- This flow did not modify the existing native game, old models, raw recordings,
  failures or retained rollback packages. It publishes no package and does not
  accept Stage1a as complete.
