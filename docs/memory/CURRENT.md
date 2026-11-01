# 当前：CARD-RL-C / RL3 在线PPO（用户v7）

2026-11-01：用户已派RL3，替代暂停的10-30卡C；root负责。[本轮设计](../plans/RL_CRITIC_20261101.md)归v7，旧扫描/A-B方案已撤销且未运行。目标是每小时墙钟实战提升；长训s8136折1B温度，直接π采样、共享价值头/GAE/熵，anchor0、KL0监控，退化回好CP开KL .02。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前任务与唯一下一步

截至11-01 23:10 AEST，实际GPU1400318/source3b5cadf6继续；原1399798全12＋1400350_0–7八路＋全新1400576十二路合32，后者全在smp-9-17/每路6GiB/exclude smp-9-0，正常watch1400589已接管。1400480全8和1400350_8–11旧4在smp-9-0 FAILED，旧21:32–22:03不是稳定32窗口，其扩容/learner瓶颈结论撤回。12次下一executor审计超时不是12个新World/unknown；其226原槽209saved/17unknown原样保留、infra类别另列。实际数由squeue逐task核，不从计划推算。用户游戏cap45；原单免费GPU/累计30h、训练11-02 03:27:51、全卡11-03 05:40末2h不变，不自行延长；不碰Mac/秘密输入/凭据，RL2不恢复。

**唯一下一步：23:14:58后核同32任务完整30min稳定窗并重测，再决定36；接23:27:51正常quick，随后按完整CP自然边界部署已授权管道/Host60。** 当前仍lr4e-5/head1e-4/λ.95/value.001/entropy.01/无权TRAIN尺度、FP32批256微8、2use/600秒/prepared seq1。PR369准确4d5f521a六Linux+portable全绿，已普通合737950c5，共享scratch/后台R2归档和process结果后台解码源码已交、尚未部署，不冒称生产提速。切换须先排空全部旧R2 producers再精确seed本run记录，新热peer不能漏旧晚记录；原全前缀保全义务不变。实际仍Host58Runtime59/Input-v2；Host60仅随自然重启，U1 r18/r19新InputSpec不混。热参数控制归#319/#293，不在本卡。

MLP本次离线候选未过线，不部署、不默认启用，也不根据考题重调。冻结v518主干/原线性，只用256自然TRAIN（排永久held_out/48/eval100/unknown），内部DEV选epoch1；同47自然考题9796行，行EV.1493→.2466，但等局MSE.003557→.003827、均局bias+.002792→+.021665、相关中位.8350→.7480、抖动1754→4316，多个预先门失败。47局全策略logits/argmax/模型权重CP重载exact通过，不是闭环玩法或真实GPU迁移。相关工具/可选完整CP导入仍在[PR368](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/368)，线性默认不变；其CI空间前提阻塞由[PR371](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/371)保留产品guard修测试。正式价值工具PR360已合489d9b79并完成真实48点全链，旧23组只连续性。

第三quick v471/2274对起点20合格pair为13.65→21.10/+7.45；与v296 CURRENT共同18直接+3.2778、固定组95[-1.3889,7.9444]跨0，是改善信号非确定增长或平台。current24offers=20合格自然+3unknown+1自然37层原批审计不准入，均保留。最终同窗100+100尚未执行，unknown剔整对逐项列而不按数量VOID；最终曲线、七Issue、#338末CP（仅提前核v518574/100000节点）和所有新旧array/GPU/Store/失败prefix完整保全仍欠。SOP为[原运行指南](../../python/docs/RL3_RUN.zh-CN.md)，private卡报告/本chat输出绑定实际对象，不借代码/CI作运行证明。

CARD-1A / [#340](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/340)仍待用户试1–2局和原件/完整入库；本卡不碰Mac。原source ab816a93 / Workbench cc39dc7f / UI24 / Mod72 / Runtime60的已装与0动作保存范围见[STATUS](../STATUS.md)，不拿它作模型实战资格。

| 对象 | 已知结果 / 剩余 |
|---|---|
| 目标 | L1为A0约5%通关；本批模型评估未见通关，未到L1。[完整阶梯](../DECISIONS.md)不因诊断指标改定义 |
| D2 / D2b | 新段最高完整CP为段内8856/累计12600；保存8928失败、末72更新未保存，未达到经验拟合平台。五版各50槽共246自然负/4unknown；长训有局部改善信号，家族区间仍跨0，未证稳定越训越强 |
| 数据量 / 概率 | 20/41局共同94seed层差+0.81，95%区间[-0.24,+1.95]，分不出来；n10未准入。DEV/TEST NLL翻倍而一选近似不变，先拆分排名/概率诊断，不自动废除留出 |
| RL2 | v337为2691更新；最新完整CP335为2675更新。12路独立保存12/12、1967518文件核同，旧0/12失败历史保留；不再派补拷 |
| RL3准备 | PR325/328已交，短链20更新、真实换模和2完整CP通过；不替200步/首小时/长跑学习效果。配置模板未批准，预取默认关 |
| 录制 | Workbench cc39dc7f / Mod72加载、窗口/退出Stop及新持久本机库0动作索引保存回读已核。用户0局；完整对局数据链与Human资格未通过 |
| 新用法 | 排序对照真实游戏、显示全部P1动作、人排几个并执行第一、可标问题，不需后台模型；交替各段标人或准确模型版本。复用原决策端口/控制/原件，不另起执行链 |

## U1 独立修复入口（2026-11-01）

PR359/Host59首批、PR361/Host60晚到子输入与settling刷新已交；训练主决定自然重启采用。Host59两局Actor/typed/R2读回通过，no_value_head不当RL就绪。新P1r1.8/新InputSpec另版：Trial/Claws原生组件→正常消费、v416参数保留已核，真实Reference场景仍缺[#366](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/366)。未热换RL3/日常。沿[U1证据](../evidence/UNKNOWN_FIX_U1_20261101.md)恢复129条分类、已达与未达；Sphere/Architect/宝箱/Stop ACK/typed诊断归#350/#362/#363/#364/#365，旧记录和分母保留。

## Remaining Platform non-claims（运行与证据边界）

- 未来价值loss默认无权重，T2原W1历史不回写；动作W1另由下代配置决定。
- 混合整局不算模型评估；人的标签/片段不因P1可读或入Store自动准训。
- 固定100评估seed永久不训练，原确认20保留；训练监测与独立确认分开。
- unknown不重试、不当负局或零层；结果按原ledger、审计和保存读回判断。
- Managed成绩不替代真游戏实力；水晶球等支持域缺口和严格并行等价仍保留。
- 安装、发布、运行身份看原收据/实际节点，不从develop或CI推导；[STATUS](../STATUS.md)只路由证据。
