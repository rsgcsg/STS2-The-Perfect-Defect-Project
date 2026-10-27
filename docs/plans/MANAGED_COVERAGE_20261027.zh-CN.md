# M3：个人原件重建与诊断覆盖

本轮唯一负责人是执行 M3 的 Codex 主对话。目标是个人原件同条件 Managed 重演、Q 录后自动诊断、Reference/Managed 多种子严格对照及可见覆盖页。用户委托与 `replies/MANAGED-COVERAGE-CARD-20261026.md` 是依据；录制优先。用户后来明确批准 headless 诊断，核无人占用后可直接使用；用户随后明确回复“已退出游戏，允许安装并验收”，生产安装与现有原件自动链路已完成。5 小时开发预算从 2026-10-27 15:39 AEST 起，20:39 为不扣等待的保守硬限，留30分钟收尾；CI/窗口等待不计，不因派工重置。

开工基线 `4a3af053`，分支 `codex/managed-coverage-m3-20261027`；已普通合入 M1/X3 后的 develop `df6311da`。本轮只写 Host 私有初始化、根诊断工具、Python 录后诊断 owner、原 Q 完成/空闲后的一个 hook、对应回归与 SOP。M1 评估执行/保存/早停和 W1/T1 页面、cohort、输入、训练、HTTP 服务未改。Host 升 rc.47，原 Mod/Annotator/Tool 未安装替换。没有新任务、训练、RL、费用或 main 发布授权。

X2 存种子/原样进度已完成，本轮复查并复用。新 `captured-native-progress-v1` 保留标准全解锁校验，以原 UTF-8/SHA、游戏原生内存读档/reload/UnlockState 证明个人档启动；外层原生入口传 A0–A10。种子/进度不进 P1、模型或玩家快照；诊断 transport 遮蔽启动私有值。

X2 真人 session `session-20261026T134755Z-e66c755bee1d41119b88549093e3a544` 的131点完整原轨迹由原件读取器验证。个人 A10/zh 启动证明通过；13点已比、12点/12选择一致，首差异index12锁镰counter3/0，118点未到达，unknown0，实际进程正常关闭。显式normal原生速度的隔离Reference对同原件73/73点、72选择一致，重现counter3后到150秒预算停止，unknown0/正常关闭。种子+进度+选择尚不足以宣称整局重建：原件未记录原速度/完整展示时机，Managed仍即时等待。正常时钟POC非作者复核发现异步/原生线程风险，在任何世界投递前全部撤回，未进最终实现；M055留原例外表，不接受展示豁免。

多种子工具沿正常Source Runtime先记录实际策略动作/完整Result，再严格Managed重演。3seeds×uniform/冻结438softmax共6份自然Source、515计划点；391已比/388一致，2条全程观察及Result一致；4条在M052/M023关联差异及原生pile-selection partial处停止，全部unknown0。首轮工具写错en而profile为zh的6份Managed停止记录保留；修正语言后只复用准确Source，没有另开游戏。逐界面表及全部尝试见指定报告。

Q接线在X3合入后完成，复用原maintenance ledger、importer guard、ManifestArtifactStore与peer。无显式配置不启动；空闲每次最多1局，先持久化attempt。用户恢复游戏、observer失败或owner关闭只清理自己的进程组；清理未确认保持interrupted_unknown并阻塞下一局，确认退出后只归档、不重演。失败完整前缀升analysis并全包/逐文件peer读回；录制中延后发布和保存，随后只续存储。成功诊断留运行节点，不删原件。页面从同一账本派生，展示missing、未确认、失败及未到达分母；不并行改W1/X3页面。

建议调整理由：复用原严格重演器及正常Runtime，不另造动作/比较链；独立静态页避免与活跃页面作者双写；真游戏只串行，优先走最短多种子诊断；normal时钟大修按卡允许留后续，不以忽略counter换通过。最终报告 `replies/M3-REPORT-20261026.md` 记录准确CI/合并/安装状态；源码、测试、安装、运行、Human/训练资格分别报告。操作入口见[原SOP](../../python/docs/MANAGED_RECORDING_REPLAY.zh-CN.md)。实现PR262已普通合并为1ee40011；准确候选e9452e11的full CI40467694761与合并头CI40470018747均通过，非作者复核无剩余阻塞。本地首轮full的11阶段通过，Python缺锁定npm包及两条测试长行的失败保留；修正环境/换行后完整Python门5423passed/17skipped/93subtests及CPU e2e/build通过。

用户批准后已安装固定release c75ecd42（源码e9452e11），原Mod c6fee8dc/Tool5afc7d76字节不变。初次仅cloud环境缺tokenizers导致Q启用失败，尚未开始重演；同一uv.lock沿现有环境入口 `uv sync --project python --locked --extra cloud --extra local-models` 补齐（不下载权重），核正常Q imports后正常重启恢复Q原策略/up_to_date。首份既有真人source5501214…自动按个人A10/zh启动，830计划点/73已比/72一致，event_option的options[0].is_enabled处严格停止（M056），unknown0，Host和实际process close0。完整失败analysis16829058…已由原peer及主作者独立全R2 archive-member/hash读回。已生成独立自动页；没有为验收重录一局。

个人1703/default2/日常录制28012文件集合、大小与SHA在安装后仍完全不变，原project配置和Q策略保持；旧完整release/launcher回滚保留。安装游戏位已于18:08 AEST释放，日常Workbench/Q/M3作为功能继续运行，后续实际attempt以原maintenance ledger和页面为准，未知不重演。原131点真人整局、normal时钟、M055/M056与其它未到达路径仍未闭合；本卡按能走多远报多远交付，不接受差异、不借局部测试授资格、不自动接后续任务。
