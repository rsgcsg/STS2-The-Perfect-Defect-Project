# 旧资产去向：K后增量与原账本入口（审批快照）

开工比较：K1 `60dc0a89ad6ae19b26e0e1bbf71517471f409069` → `f71507217d895fa484cb0cfe589a38e154579cb7`，快照2026-10-27约01:05 AEST。此页只索引已核去向，不是删除脚本、授权或当前任务队列。另加本轮分支/PR后应单独保护，不计为旧资产。

复用[K原资产账本](p1-prefreeze-2026-10-18/ASSETS.md)、[原远端表](p1-prefreeze-2026-10-18/ASSET_REMOTE_REPORT.md)、[本地表](p1-prefreeze-2026-10-18/ASSET_LOCAL_REPORT.md)及[K3执行收据](p1-prefreeze-2026-10-18/K3_EXECUTION.json)。K3已有24项精确tag并关闭，24/24当前仍关闭且head相同，抽核#26/#155远端tag准确；不能把原表“未执行”再当今日待批。

主仓53个K后merge均是当前develop祖先。旧7个open=主仓6＋Workspace#9；9仓70个远端branch=主仓42＋其他28。主仓本地develop落后3409提交，不能按目录名当最新。Ritsu上游dev/main已变，未更新本地pin。

本机目录完整逐项审批表及5份CSV在既有交接replies的 `CONVERGENCE-DISPOSITION-TABLE-20261024.md` 和本轮outputs/disposition；不把其他课程/无关任务的私人目录清单提交产品仓库。本页保产品refs的可恢复入口。本轮没有关PR、删ref/目录/数据或移动历史报告。

## 待用户一次批准的处置

- 建议在本轮文档合入后，精确归档并关闭主仓6个旧PR；其6分支也在逐行待批范围。PR233适用SOP/收尾已定点接回，旧CURRENT和新评估SOP不被覆盖。Workspace#9保留。
- 主仓另24个K3已归档分支＋4个已合入新分支，可批准后核head/tag再删。合计34个远端分支是建议删除范围；main/develop、其他保留ref和其他仓不在删除范围。
- 全部本机旧目录、注册工作树、安装/回滚release均保留原位置；没有整目录删除提案。dirty和ignored独有内容未按源码祖先关系宣布已保存。7天缓存许可只按原工作规则，当前收敛批前不清理。
- 每次执行前重新核同一仓/ref/head、无人写入、需要保留的原件/R2；先建且远端读回精确archive tag，再关PR/删获批分支。若head漂移，原行暂不执行，不静默改批准对象。

## 旧PR逐项（独有能力没有假称已接通）

| PR | 准确head | 去向 / 条件 |
|---|---|---|
| [rsgcsg/STS2-The-Perfect-Defect-Project#233](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/233) | `dab9541c3a2aee0b822206cf6b61d25078423cea` | 3个独有文档patch：GUI模型实战SOP与10-23收尾导航本轮定点接回；export模块CLI原已在develop，仅补锁环境/避免best / current误复制。旧CURRENT不搬。；归档后关闭（先定点吸收）；本轮PR合入后再tag→关PR→删获批分支 |
| [rsgcsg/STS2-The-Perfect-Defect-Project#181](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/181) | `6cc2a0d7c90aca9ecd5751923261d3a7adc1d4f8` | 44路径J-min Structured lanes/CUDA/finite TrainingService候选、原数值和transport unknown证据；K后head未变，非当前P1训练实现，不因P1提速已合入就宣称它已迁入。；归档后关闭（不声称迁入）；本轮PR合入后再tag→关PR→删获批分支 |
| [rsgcsg/STS2-The-Perfect-Defect-Project#178](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/178) | `41e20810e432a091ade25973175331d75b36ae39` | 19路径旧工程规则/系统图/P1计划候选；K后head未变，25个旧提交未祖先合入；有历史用户决定及被新证据替代的阶段计划，不能整支合入现行规则。；归档后关闭（保留原决策）；本轮PR合入后再tag→关PR→删获批分支 |
| [rsgcsg/STS2-The-Perfect-Defect-Project#172](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/172) | `c358d2b44260d13992a8078b90d2889d326167dc` | 仅platform-runtime-qualification/SKILL.md；仍有1个非等价patch。当前Skill已有授权持续/按任务范围等文字，但原候选完整改写未被证明逐条吸收。；归档后关闭（保留Skill增量）；本轮PR合入后再tag→关PR→删获批分支 |
| [rsgcsg/STS2-The-Perfect-Defect-Project#25](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/25) | `6b1d006f8e9b1abfcb9c3c113c988ac8cfe9da63` | Workshop L2 deterministic staging，基于PR24；2个提交/6路径，未纳入develop，保留原受控字节/格式边界和测试。；归档后关闭（L2）；本轮PR合入后再tag→关PR→删获批分支 |
| [rsgcsg/STS2-The-Perfect-Defect-Project#24](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/24) | `0ffb3cf7870e9b07fa5bce15363dc40b7623ca81` | Workshop L1 release boundary，1个提交/7路径；未纳入develop，发布元数据/边界资产仍是独有历史候选。；归档后关闭（L1）；本轮PR合入后再tag→关PR→删获批分支 |
| [rsgcsg/SpireAgent-Workspace#9](https://github.com/rsgcsg/SpireAgent-Workspace/pull/9) | `601b47175d99ca05e71cf65f1ea2a8f5857ffb22` | 独立external resource lifecycle/registry候选8路径；head与K一致，7个提交未纳入其main，当前消费者/适用规则未复核。；保留open，原Workspace owner |

## 远端分支逐项（70个开工快照）

| 仓 / ref | head | 去向和归档ref |
|---|---|---|
| `BAKAOLC/STS2-RitsuLib` / `refs/heads/dev` | `1fc8501ccb18fc92148e5632043b61a71751568f` | 保留外部上游，禁止本轮关闭/删除 |
| `BAKAOLC/STS2-RitsuLib` / `refs/heads/feat/raw-progress-bridge` | `0d20cf063ce47df80dec5f02648ad35a801bea33` | 保留外部上游，禁止本轮关闭/删除 |
| `BAKAOLC/STS2-RitsuLib` / `refs/heads/feat/support-for-baselib/card-outline` | `9c44afad3787274886665b7abaf365c705d2ac97` | 保留外部上游，禁止本轮关闭/删除 |
| `BAKAOLC/STS2-RitsuLib` / `refs/heads/main` | `772f280964f2efc7c7e2f29fc59844e68616dd57` | 保留外部上游，禁止本轮关闭/删除 |
| `BAKAOLC/STS2-RitsuLib` / `refs/heads/refactor/modular-runtime` | `d94e97328cf21cd92a21774707e038a0ea556280` | 保留外部上游，禁止本轮关闭/删除 |
| `rsgcsg/STS2-AI-PLATFORM` / `refs/heads/develop` | `487455cc4025ac29aae27ad8084cac6aa8dba66d` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/STS2-AI-PLATFORM` / `refs/heads/main` | `3dc2331afd62f7e09ca6cf9c7c772ca13e954521` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/STS2-Connector` / `refs/heads/main` | `2a14504fffe2cf6fc21298dcb5e4084b9ac27ef1` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/claude/two-tier-engineering-rules` | `41e20810e432a091ade25973175331d75b36ae39` | 批准且本轮文档合入后，准确head归档tag读回→关闭PR→删除此远端分支；独有源码由tag保留；`refs/tags/archive/convergence-20261027/pr-178-41e20810e432` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/claude/write-probe` | `1f8851dc3e1c3f3cdd6d5d09d30d55ff70d6270d` | 保留历史ref；归档 |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/codex/j-min-batch-cuda` | `6cc2a0d7c90aca9ecd5751923261d3a7adc1d4f8` | 批准且本轮文档合入后，准确head归档tag读回→关闭PR→删除此远端分支；独有源码由tag保留；`refs/tags/archive/convergence-20261027/pr-181-6cc2a0d7c90a` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/codex/m0-full-data-ready-20261003` | `c8d654b7aa87ad71da7c5f12e0a20815f2c16889` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-148-c8d654b7aa87` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/codex/m2-linux-pilot-20261004-21f7a04` | `21f7a0431e8503eb27bf2dd5ad8d6a6d1aae86d3` | 保留历史ref；归档 |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/codex/native-model-closeout-20261023` | `dab9541c3a2aee0b822206cf6b61d25078423cea` | 批准且本轮文档合入后，准确head归档tag读回→关闭PR→删除此远端分支；独有源码由tag保留；`refs/tags/archive/convergence-20261027/pr-233-dab9541c3a2a` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/codex/p1-application-store-20261020` | `12df5cc80eba4286b639007a640bfb2c371aa043` | 已合入；用户批准后先打准确head归档tag，再删远端分支；`refs/tags/archive/convergence-20261027/codex-p1-application-store-20261020-12df5cc80eba` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/codex/p1-normal-baseline-startup-integration-20261018` | `15c3436e64c0861c065e9731d0005458c396302e` | 已合入；用户批准后先打准确head归档tag，再删远端分支；`refs/tags/archive/convergence-20261027/codex-p1-normal-baseline-startup-integration-20261018-15c3436e64c0` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/codex/pr141-stage1a-m0-integration-20261002` | `1d00b60fad2829ec58a0d79e95cc38cd99a64ecc` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-144-1d00b60fad28` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/codex/reference-evaluation-workflow-20261023` | `ec105137f692864de21dc8fe61ddf027928150aa` | 已合入；用户批准后先打准确head归档tag，再删远端分支；`refs/tags/archive/convergence-20261027/codex-reference-evaluation-workflow-20261023-ec105137f692` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/codex/runtime-qualification-skill` | `c358d2b44260d13992a8078b90d2889d326167dc` | 批准且本轮文档合入后，准确head归档tag读回→关闭PR→删除此远端分支；独有源码由tag保留；`refs/tags/archive/convergence-20261027/pr-172-c358d2b44260` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/codex/t0-p1-training-20261020` | `a18ff9fbe04246be7ee6dc348d157072ef1016d5` | 已合入；用户批准后先打准确head归档tag，再删远端分支；`refs/tags/archive/convergence-20261027/codex-t0-p1-training-20261020-a18ff9fbe042` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/develop` | `f71507217d895fa484cb0cfe589a38e154579cb7` | 保留默认/集成线，不提出删除或改main发布 |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/docs/stage1a-light-action-m0-closure-20261003` | `a253194873d0fcfdba3bd19eb431f82122d0c7c9` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-145-a253194873d0` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/configurable-m2-campaign` | `41ab5cd17ab7c28be19eb34292c22a65be6a2e51` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-161-41ab5cd17ab7` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/m2-campaign-recovery-v2` | `762907a641f603e1db32155058a70efbf3b11ee5` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-163-762907a641f6` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/new01-faithful-page-input` | `478b172ef072cb63d955a89edd281647ee7f8c36` | 保留历史ref；保留 |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/public-m2-chain-20261004` | `732a2d67bbd3718da62f87cd51eb3842abc23757` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-150-732a2d67bbd3` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/public-m2-memory-three-20261004` | `96c86a2e03bcb6b0c9f7c64650b0f20d32c89731` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-157-96c86a2e03bc` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/public-m2-product-consumer` | `829496cfaf012defd0f169341fef13ee248dc218` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-162-829496cfaf01` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/workshop-stage1-release-boundary` | `0ffb3cf7870e9b07fa5bce15363dc40b7623ca81` | 批准且本轮文档合入后，准确head归档tag读回→关闭PR→删除此远端分支；独有源码由tag保留；`refs/tags/archive/convergence-20261027/pr-24-0ffb3cf7870e` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/workshop-stage2-staging` | `6b1d006f8e9b1abfcb9c3c113c988ac8cfe9da63` | 批准且本轮文档合入后，准确head归档tag读回→关闭PR→删除此远端分支；独有源码由tag保留；`refs/tags/archive/convergence-20261027/pr-25-6b1d006f8e9b` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/workshop-stage3-prepare` | `4f26d8dfbbc58e3a4a0e546e8bc67f72a9f4c60f` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-26-4f26d8dfbbc5` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/workshop-stage4-publication` | `b8e7f02db2c868604bbfd2fe682782acb976c568` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-30-b8e7f02db2c8` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/feat/workshop-stage5b-consumer` | `fbfab632c78dc1640be6e1345dae1bbb2b44d1df` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-45-fbfab632c78d` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/canonical-launcher-target-bytes` | `80c865c6e3fb9ffbba37c91a0b02797142ed0871` | 保留历史ref；归档 |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/deferred-live-ui-candidate` | `abb30dda5ab746aad70da2cf26412812fca31fe2` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-149-abb30dda5ab7` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/game-workbench-recovery` | `9f1177dae8a0bd3e701e947cb860c752b758b390` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-152-9f1177dae8a0` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/kit-m0-environment` | `81ad4d1cb14a1d2661ed8f5be8770b03f3edfa1f` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-153-81ad4d1cb14a` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/m0-bounded-compressed-request-20261003` | `71309adfcdfd808a6b99d2bc43abda9079ef1d7a` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-147-71309adfcdfd` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/pr131-managed-text-selection-ownership` | `4c5ae5684f8865f2536aa1d55b24bf82c66cb3d6` | 保留历史ref；归档 |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/product-workbench-integration` | `1fe27bae96ed8219e96f2c75a4b73e8662cd288f` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-154-1fe27bae96ed` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/production-runtime-seal` | `31cd6bde5534f3edef4e5516a0d6b03c459f9e8f` | 保留历史ref；归档 |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/public-m0-training-device-ui` | `c8714976de1e01beace89fb17d59891c273833fa` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-158-c8714976de1e` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/runtime-rc22-published-product-pin` | `e30247defd859dfc75708fea4612c668e93cdbd2` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-164-e30247defd85` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/fix/workbench-service-resource-boundary` | `fdc3c23de1675196879c0273f50659753f35ed9b` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-160-fdc3c23de167` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/integrate/branch-closeout-20261003` | `697880c06e52ef1e7e3d588627379abd50c28a5b` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-146-697880c06e52` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/integrate/m0-fixed-dev-stpd-b9e8df57-20261002` | `885e85ec1cf0e8ab66f56e6105e7bfc9e54d3486` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-141-885e85ec1cf0` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/integrate/stage1a-public-m0-product-20261001` | `bffb01210e5d0800887a893e04159127925cc02f` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-140-bffb01210e5d` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/integration/port4-semantic-cycle-guard-20261004` | `d8b5c9ff277cba39921c533a06a22763818c0141` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-151-d8b5c9ff277c` |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/main` | `83e25b1d97b9552211a748a70897a986090dfb86` | 保留默认/集成线，不提出删除或改main发布 |
| `rsgcsg/STS2-The-Perfect-Defect-Project` / `refs/heads/perf/verified-idempotent-local-blobs` | `32ac14a08cedc405f237a81c029a7862b107619c` | 已有K3精确归档tag；建议用户批准后复核head/tag不漂移，再删除远端分支；独有源码继续由tag保留；`refs/tags/archive/p1-prefreeze-20261018/pr-155-32ac14a08ced` |
| `rsgcsg/STS2-The-Perfect-Defect` / `refs/heads/develop` | `4dd9232350cf37c15cad1284f430216c6bb87944` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/STS2-The-Perfect-Defect` / `refs/heads/main` | `7a1df8372bcee3c6b8227cc5ebcb48a2374ac8a4` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/STS2-headless` / `refs/heads/main` | `1deb5b7b3125e2308df7e8e44cb3b213e46e7f64` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/STS2-human-Annotator` / `refs/heads/main` | `82c5e8e60fdb08d876fd3726597608eb43a69a52` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/chore/workspace/bootstrap-governance` | `17399174605fb94d94d2b45314928b1905ae87fe` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/chore/workspace/product-scan-governance-20260828` | `bbceefa8a41e7c43ab6fe41746250f5278fc2c31` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/chore/workspace/skill-governance-reconcile-20260828` | `206ee65f56711f2a889d3b6435eb5245ae9800ca` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/chore/workspace/skill-governance-reconciliation` | `bb427fdd87c400cfed8f4fd46054e30b0b1803fb` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/chore/workspace/skill-remote-first-policy` | `e438ac0dce5d7fe2d8a467160f5f7060e384b344` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/chore/workspace/skill-update-20260828-canonical-130` | `97bd215c5489460304dc338c75af2dd4648bab6b` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/chore/workspace/skill-update-20260828-product-feasibility` | `97bd215c5489460304dc338c75af2dd4648bab6b` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/develop` | `9ac572dc30057e90daf731f711b4b9d8ba4f28c5` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/docs/workspace/external-resource-registry` | `601b47175d99ca05e71cf65f1ea2a8f5857ffb22` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/main` | `e0a136bc29ec0186c98a9f37980ef86d72e9b0a2` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent-Workspace` / `refs/heads/relay/20260828-skill-delivery-workspace-skill-maintainer-180` | `25ae5e183576358f89b02afcfccd7d9b9bf967ae` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent` / `refs/heads/agent/re-prompt-v2-baseline` | `ecc793e99f8288833d2e6ef051199a149d8d79ad` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent` / `refs/heads/connectorV3` | `01390b93f8455cb1fd944359a6ce40eeeabfec36` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent` / `refs/heads/develop` | `19ec93c66c02612a78556b9b7e513d8853c79e48` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent` / `refs/heads/human_equivalent_connector` | `159167d2b51ea6e0ca009bbeb7ab5f2748c2719b` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |
| `rsgcsg/SpireAgent` / `refs/heads/main` | `546705b8e3d1d619979b16c8fc44bebc367db682` | 保留独立仓库/来源ref；复用K原去向，不按产品develop替代独立权威 |

## 已合并增量（53个主仓＋1个外部）

| PR | merge / 去向 |
|---|---|
| [BAKAOLC/STS2-RitsuLib#122](https://github.com/BAKAOLC/STS2-RitsuLib/pull/122) | `8fc274bbc1d2de60564a3e63d59128e7c6c9e44b`；外部Ritsu dev；不改本地pin |
| [rsgcsg/STS2-The-Perfect-Defect-Project#202](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/202) | `beca367cf72516f982031423d3266c9536925dd2`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#203](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/203) | `ecdc198ca427f4004670a835c4ebdf3e3363369e`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#204](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/204) | `ea7c14e8969006fa0e470678198ef94aca471f90`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#205](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/205) | `733782b119967828160f893893aaf5bff3b540e4`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#206](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/206) | `8b2c2334e3269cd9899debd20ed392c4d6de6ed6`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#207](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/207) | `319e684d56a3de955a8b0f2dcb5f36bf68ea8113`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#208](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/208) | `9561d262b27a619174cc735116930d2bbb067306`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#209](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/209) | `482317648af85d43bc9b67a344165ab912065249`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#210](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/210) | `1e5d83100a8222f211a0e633120ea8123dd06427`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#211](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/211) | `71f378fb2b7a1275ab7e73e3dbce4180aa5534e8`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#212](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/212) | `3a50fbbbcaf35a0db878d75d5567ef3fd013f892`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#213](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/213) | `03f53af49067e06d4748934ef342e0572fba52bf`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#214](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/214) | `8910656134b3a4cc4872a3fd387f926cff68dccf`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#215](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/215) | `07f49de37d14335ec4f7032de110f899073bc963`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#216](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/216) | `0519ec9f3223cb1f599187f45f964a71032c6ede`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#217](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/217) | `5e29475ee26f3ea108e5e33cd4b823a8971ae435`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#218](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/218) | `385dd8b20850369a6a6de2471ee4ad7b4fce847b`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#219](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/219) | `6cfbd9bed2446d3c171d06165d4bba7f7f5b76e9`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#220](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/220) | `b6e7d6455f40305f28f28f5973dec375e17416e9`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#221](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/221) | `16bbda48d9ff677de152ea9a7af61dc567ebb7ae`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#222](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/222) | `4f561277b4b011580fbe0c438c78b8fc0074c85b`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#223](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/223) | `f62388c8fb0c28c1f56de54b96a84a4811bfb4be`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#224](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/224) | `33de58751631a19d9e799d98ac3f81487cc5f255`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#225](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/225) | `58d445f534c608260fbf825bdc5ae204772f8011`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#226](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/226) | `1f88c1c149445c0801ce524c4eda64c9c0b3db29`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#227](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/227) | `4b0583c39b3dcf7b06166ff247c3e11cb847b674`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#228](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/228) | `01ca934b7fd90c7ebedcd063fbc54da6090eb723`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#229](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/229) | `93e90df1299fa657ceb9a5a13389e64915914859`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#230](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/230) | `55ca53d82c57bc6ac1afaa8cd9f22196aaf62471`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#231](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/231) | `2dbb96c63f92c7e76f0299e61e2ea49cc3bd7466`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#232](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/232) | `7f3c711cddd214dceea3fcc947cded315dce5416`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#234](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/234) | `9abab77a66eefc0761df4d9b9044bd76731f48b4`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#235](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/235) | `74da29f06bff24f89d99f5637db517d2a7ce5511`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#236](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/236) | `6808583ff9019e7c4168fe33bb9aaef2fbc8bf11`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#237](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/237) | `b62310da65fbccf203353dfeb30926982a35315f`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#238](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/238) | `8d628cf2f4f0f92e0f61a8eeb14c480840c903d5`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#239](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/239) | `9ae5aa852332df9a97985ae2686d73f8b9f2ea71`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#240](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/240) | `011830b9e6785a91929df5fe07db508fb232d8d5`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#241](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/241) | `6339addc0f27ee158c64ff296b0ed4f999a6a024`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#242](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/242) | `d84d32c57ac5d75df46df1da2d78137207158b9d`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#243](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/243) | `a1802d59595ff7fa305976e178c868ddf8a2a2a2`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#244](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/244) | `21fd85ea78c8e402166bda69b84a5ec5dec900fa`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#245](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/245) | `0009a2cf7e21b2f432ee0b657d45c7efc22516e0`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#246](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/246) | `9b87d215a80bd907075e1bad5de37579b4db834c`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#247](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/247) | `280fc3e9dc194dbd32308b209e20faca80722175`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#248](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/248) | `14c6c1c019db826ec8daa33b59d28c5c3a438433`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#249](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/249) | `6bc8358e21a7374469709632ace5ee1f39b6023f`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#250](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/250) | `7d448fc2963955a09c6e2839f0010e4c986fd5fb`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#251](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/251) | `8846a418052e1402dbfef486f3a6652e11136a8d`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#252](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/252) | `31ab9a971b7538fb2e601ce592aa73156bfc72f4`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#253](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/253) | `49aa20383cd418f6bafe43d7e2bc1c865c505d4d`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#254](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/254) | `119d6e22d73e0393f17ed3bcf07449eff65c1ddf`；已纳入当前develop |
| [rsgcsg/STS2-The-Perfect-Defect-Project#255](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/255) | `f71507217d895fa484cb0cfe589a38e154579cb7`；已纳入当前develop |

## 本机目录去向与边界

桌面现存49个顶层目录没有K未登记的新目录；仍沿K原表按owner保留。主Git库86个注册工作树、10-18后54个新工作树：49个HEAD祖先包含，4个仅patch等价，PR233的3个文档patch定点吸收；这些关系只谈tracked源码。旧c1-validation/consumer-wire仍有67/41项dirty，新Q2有2个未跟踪依赖目录，全部保护。异常/prunable注册项也保留，不能据不存在可用git状态猜可删。

另只查10-18起82个Codex直属目录metadata，非本项目/owner不明同样原位保留；不递归计算体积，不猜独有数据已上传。各目录的准确路径、ref/状态/未核项与建议逐行在本轮私有审批表和CSV。安装中的release、回滚、模型/检查点、个人存档/原件均不进入删除清单；主仓之外旧工作树复用K边界，未声称全盘重新核验。

`p1-prefreeze-2026-10-18/` 保原处作专题参考并加导航头，未移动以免打断现有入口；历史报告/replies同样保原处，本轮不批量移动。这样保留原证据位置，日常从短地图进入，不要求每份旧报告都有摘要。
