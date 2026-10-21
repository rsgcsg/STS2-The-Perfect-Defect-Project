# 当前五小时工程交付：2026-10-22

本轮由用户明确授权立即执行，替代旧 Batch1/Round3H 的派工状态。目标是修复普通日常录制与派生、累计数据到真实短 GPU 训练、Store/R2与容量和正常模型消费者，并在准确门/CI、非作者复核、无活动录制、可回滚后交付日常安装。正式长训练、完整 P1 冻结、未验 Human 和策略质量不因工程交付自动通过。

本轮唯一主管 root；R 独占 native/派生实现，T 独占 STPD 数据/训练，S 独占 Store/Application 映射，I 独占集成分支、根导航与正常安装。各 writer 独立 topic/worktree，以当前 origin/develop 为共同基线；初始准确基线为 `9561d262b27a619174cc735116930d2bbb067306`。组件源码 normal merge 保 path-scoped 来源。直接相关 PR 的本轮条件批准不扩大到其他历史任务。

硬截止为 2026-10-22 07:18:15 Brisbane；稳定候选检查点 06:15、停止变更 06:40，I 最迟 07:05 保据并释放资源。重型/native/游戏只有一个本机槽，由 root 排期；当前正常服务和用户数据不得因任务收口擅停。每个领域先交实际消费者增量，最后组合门只在稳定准确候选运行。

初始现场（历史）：I 集成分支为 `codex/p1-fiveh-integration-20261022`。正常初始 Application 使用永久 941ae84d release/c47 来源，游戏初检 PID8720、录制与 Close 均 closed；这只是开始时点。旧 G 私人检查点不再派发动作，不能复用旧报告直接授予资格。下方收口状态拥有本轮最新结果。

操作入口保持 [日常导航](../../DOCUMENT_MAP.md#按我要做什么进入)，现场安装按 [原 installer](../../../python/docs/DEVELOPER_KIT_INSTALL.md) 与 [Game Mod lifecycle](../../../apps/game-mod/README.md#lifecycle)，模型按 [原 Application](../../../python/docs/PROJECT_CONSOLE.md#p1-model正常下载注册与使用)。各实现负责人同步其原功能指南；I 只汇总实际交付与未完成义务，不另造流程。

用户最新明确日常录制卡顿会造成眩晕，本轮必须由R修同次Freeze重复构建并由I做真实录制开关/帧停顿验证；旧IO诊断不替代正常使用或用户体感。

## 05:58 收口结果

PR209 稳定候选 `6fdb3c3e2011a1190c275d979a9c1d79bcbccadf` 已 normal merge 为
`482317648af85d43bc9b67a344165ab912065249`，树与候选相同。CI39658596791及merge
CI39661134408两Linux/portable SUCCESS；router跳过门不冒通过。原Host完整门423 Node通过、
5条件skip、32 Python通过；锁与训练闭包115项、最终private Host/Ops组合24项、
closeout/repository/exact-game均保原准确候选。旧55 CI五失败、985取消、c844 Host清单失败保留。
原private Host derive漏SDK外canonical contract已按同源码生产布局窄修，无新schema或宽门；
Ops备份仅在副本清三可重建ConsoleIndex，账户/可见性/未知表拒绝不变，活DB未替换。

日常原installer/CAS已升级最终kit
`a9724a6762f49489ad0fb291e93ad9f47c7f73f585e7e45383a12514197badb4`、source6fdb、Tool2cd、
Runtime rc39与Mod rc57，永久父/-I子78 distributions及真实Evidence已核。原working target、
config/grant、旧release、rollback和原件保留。普通Steam冷载57身份准确，原Tool被动连接/
配置成功且errors[]；四次约6.5秒同binding“已连接游戏工作台”真图已非作者核。
正常artifact_unqualified/execution=false，原verify-loaded忠实失败，不授正常执行资格。

正常Steam a557与隔离headless c184 Modset不同；首R20在bootstrap前严格拒tuple，0提交且
正常Close。一次只读原Host诊断后，新不可变参数绑定实际c184，原v4第二attempt通过：
1submission/2policycalls、Consume/ACK/Next/Receipt/Result、独立后继ordinal2/reward_claim、
official replay verified、Stop known/released/pendingnull、Host200/code0/signalnull/unforced，
外包manifest/files前后一致。只验Reference，two-host package check=false，Managed延期。
原rc56/c844 Reference20有20/20真实终局/replay/normalClose、0unknown/error，保其原组合。

真实短GPU Run4220、Resultd63、Model6ef、lastCheckpointaa3仍归producer9561/lock3ebe。
三次调用分母为取消、暂停step1、同Run合法resume完成68updates；4335 TRAIN/446 DEV，best
step55。候选reader曾读含TEST的总体，不称TEST零接触；短Run未用TEST558做fit/梯度/DEV选模
或模型TEST评估。Ops/Curation原小备份已R2和staged restore验证。正式30epoch仍未执行，
无第4次GPU调用。正常日常只新增同Ops/Store的observation_only绑定，不启动第二Scheduler；
固定open重开后3jobs/handles/results一致，同Run状态/进度可查。工程serve49285已按原project
stop收掉，日常观察仍可用。网页视觉因本机浏览器自动化禁用未验，原生Training页仍仅local。

同一Model `6ef07b5530c8b42117a1bfd374346c201508cbe4ab5d622940fe3f7aaaf5c386` 经正常Application
register/readiness、原prepare-owned和原request运行一次。Store weights payload/package SHA
`dc85db55924b61737caab0ce9ca2ef700535f6844a51401a0473db744b5c08b7`（17,653,965B）保持；
独立decoded weights-only digest d52a6f不是全包SHA。首次注册缺lease保typed409，原双R2/
2GiB/retain2不变的正常cache-configure准备lease后成功。实际run-c1617b20-7724-4706-af1e-8bbafba0f815
15提交/15delivered、32calls到policy_call_limit；1条global_dispatch_not_authorized/sent=false
保预算边界分母。原Stop evaluation=pass/findings[]、378events、known/released/pendingnull、
无taint；native-finish closed，Host200/code0/signalnull/unforced、driver0、无cleanup_error。
Store/R2原operation359358dfb60b4f93b61f5cc3c29642a1的终态未捕获，后续原后台cache维护覆盖了
当前operation；不能拿新维护completed代证原发布completed。准确artifact
`2045850c3928831480d125417910b139d0e8f3d1b68469bfffdc326ace3a71a3` 随后按原trusted Runtime
在本地及first R2 peer独立完整读回通过，content9ada8b/同run/378events/archive610852B一致，
typed_verification=pass，用时12.86s、无再发布。完整copy_artifact验证训练祖先payload，
不能把本轮温Model成功称全新冷R2 Model按需组合实证。原终态持久性缺口仅报告，本轮不扩修。

录制眩晕/卡顿仍未完成：rc56工程记录2accepted/2execution/0issue，但Recording max180.487ms、
Paused max33.959ms；Tool派生/永久reader读2N/1next且raw不变，不授Human或完整行为链。
INFO仅同次中文card/upgrade/keywords/relic/enemy intent真图与PB对照；exact NCardGrid未遇未验。
一次有界系统sample缺托管热点符号，不无据削弱完整PB/采集契约求快。
S具名缓存与关闭的通过合成fixture清理批次实际净增0.613GiB，旧raw/模型/用途/环境/rollback保护。
最终06:00日常再次原Stop/固定open后仍同配置/source6fdb、原3jobs/handles、Model注册，
无重投；日常App保留，native closed/原Host进程清单空，工程serve已正常关闭。
06:05:31 fresh df为2,645,276KiB可用（约2.523GiB），不能使用先前4.338GiB或06:00的
2.864GiB快照。当前冻结配方完整父链新增规划2,708,079,710B后不足1MiB，低于原0.5GiB
floor，完整父链落地的正式长训容量为硬阻塞。有限盘点中Store总2.153GB/19:15后mtime文件
102.56MB、日常state45.59MB/新39.13MB、永久releases新10.39MB，日常scratch为0且maintenance
active=null/up_to_date。追加本机full门新建uv下载cache315.22MB逻辑、源码venv约235MB逻辑及
mypy cache18.74MB；硬链接/clone和缺少前基线使这些数字不能相加为APFS增长或完整归因。
只做一次原uv cache prune，unused0/无回收，停止清理。该full独有compiler39980经原compiler-only
build-server shutdown正常关闭；不删新venv、真实数据、失败原件或whole worktree。
2GiB是可冷却payload策略，Model/Checkpoint/Result全保热，不是总磁盘硬上限；正式长训输入、
并发峰值与长时容量未资格化，不为凑6GiB扩大清理。整体P1冻结、Human/研究资格、胜率/模型质量、
Managed、正式长训等未完成项均保留。本轮文档收口不重包、不新增业务/身份契约。

文档收口独立check:docs及closeout通过。clean文档候选的原planner仍因路径分类选择full；
一次本机尝试出现缺torch/transformers等环境mypy失败，后续仅中止该检查进程组，exit130，
不称full通过。该失败/取消保原日志，准确远端CI沿原router执行，未修planner、放宽门或在本机
重跑资源不匹配的full。历史Platform cwd一次误写CURRENT已按其原HEAD精确恢复并核clean；
正确I树保当前导航与原非声明标题，误cwd和一次缺标题closeout失败均不作最终门证据。
