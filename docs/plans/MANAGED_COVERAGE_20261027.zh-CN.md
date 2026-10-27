# M3：个人原件重建与诊断覆盖

本轮唯一负责人是执行 M3 的 Codex 主对话。目标是让新真人封存给 Managed 提供可核启动条件，复用原严格重演器、在原 Q 空闲点运行诊断，并准备 Reference headless 覆盖工具。用户当前委托/`replies/MANAGED-COVERAGE-CARD-20261026.md` 是任务依据；用户录制优先，不开真游戏、不安装，直至用户给准确窗口；自动批只覆盖原含义内的实现/工程修复及不改结果的提速。5 小时开发预算从 2026-10-27 15:39 AEST 起计，20:39 硬限，保留 30 分钟收尾；CI/窗口等待不计，不因派工或上下文更换重置。

基线 `origin/develop=4a3af053d7f44eb81674efcf832fe3ee35233502`；独立分支 `codex/managed-coverage-m3-20261027`。写入范围是 Host manager 初始化、独立根诊断工具、Python 录后诊断模块及维护器一个空闲 hook、对应回归/SOP。M1 的 driver-session/service、Runtime、评估执行/保存/早停未改；W1/T1 的 cohort、输入、训练、小结及 HTTP 服务未改。合并前普通合并最新 develop，版本/BOM 等共享文件等 M1 集成后再写。

X2 的存种子/原进度已完成，本轮只核其原件与隐私链，未重做 Annotator。原来 Host 只接受全解锁档，外层驱动固定 A0；本轮新增 `captured-native-progress-v1`，保留标准档限制，使用游戏原生内存读档、SHA/reload/UnlockState 证明，并传 A10。属于原 Host 能力的实现补齐，未改变 P1、公平玩家、比较器或用户个人存档。

首个真实输入是 X2 真人 session `session-20261026T134755Z-e66c755bee1d41119b88549093e3a544`。封存来自既有完整 R2 读回 archive，本轮只读解出自己的工作集。131 点完整轨迹已由原读取器验证；准确个人进度/A10 启动证明通过，12 点/12 选择一致，13 个点已比，第 13 点锁镰 counter 真人 3、Managed 0，严格停在该差异，0 unknown，Host 和实际进程 exit=0。已到 event_option/reward_claim/map_navigation/combat_turn；剩余 118 点未到达，不能声称整局成功。原 DLL 显示该 counter 在 IsActivating 时为 3、平时为攻击数模 3，激活展示有 Cmd.Wait(1f)；与既有 M038 同属展示时机问题的推断，当前没有同版本 UI 见证，未伪造显示值或接受例外。

建议调整及理由：复用已有 `replayNativeTrajectory`，不再维护 X2 私有短脚本；另加不可当正式原件的隐私诊断日志 schema，原 P1 response 格式不放宽。开工未发现 X3 活动分支/PR，因此 Q 接线放在独立模块、独立模块已完成，但随后核到 X3 活动候选，已撤回原维护器 hook；等 X3 合入后先普通合并 develop 再接线，不改 W1 正写的页面。页面以独立静态诊断页交付，避免同时写小结和服务；仍由原 maintenance ledger 提供唯一状态。

实际验证与完整报告、准确候选/CI/合并收据在用户指定 `replies/M3-REPORT-20261026.md`，详细日志保留任务工作目录。组件源码必须普通合并，未通过规定门禁、非作者复核和准确 CI 前不能称已集成；安装、Reference 实跑、自动自然录制验收均另报，不借测试代替。未知不再投，任何真实待决/游戏/安装点停下问用户并等其回复，不把问句当收口。

操作指南：[`MANAGED_RECORDING_REPLAY.zh-CN.md`](../../python/docs/MANAGED_RECORDING_REPLAY.zh-CN.md)。后续只完成本卡，无新任务、训练、RL、付费、main 发布或原件清理授权。
