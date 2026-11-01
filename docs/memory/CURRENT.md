# 当前：CARD-RL-C / RL3 在线PPO（用户v7）

2026-11-01：用户已派RL3，替代暂停的10-30卡C；root负责。[本轮设计](../plans/RL_CRITIC_20261101.md)归v7，旧扫描/A-B方案已撤销且未运行。目标是每小时墙钟实战提升；长训s8136折1B温度，直接π采样、共享价值头/GAE/熵，anchor0、KL0监控，退化回好CP开KL .02。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前任务与唯一下一步

截至11-01 19:13 AEST，PR357/358已普通合入11eafd4f/7e8f8180且准确CI通过。learner实际source3b5cadf6/GPU1400318，16CPU/64GiB、torch4/8个spawn reader；原52a actor12路1399798继续，19:04新12路array1400350已运行，旧扩容1400175已完整封存。新watch1400364覆盖全部新旧array，observer1399828继续。原GPU1400147正常11464秒，之前39727秒GPU保守总账再加当前job；一次一张免费卡累计≤30h、游戏≤36，原训练11-02 03:27:51与全卡11-03 05:40末2h保全不重置。RL2不恢复，不碰Mac/秘密输入/凭据，eval100永不训练。

**唯一下一步：按真实首次使用/生成与拒绝原因量24路20–30min，接19:27 quick；收正式当前策略价值工具候选，再实现有界热参数控制。** reader完整恢复到1830更新、旧固定23组/EMA/双Adam与seq1保留；恢复约6.6min，首PPO后24min187更新，train中位2.38秒/最长3.92，整链约7.6秒/更新，未出现原27–55秒停顿。只是工程收益，不能当实力提高。当前仍策略4e-5/head1e-4、无权EMA残差value.001、lambda.95、batch256/2use/600秒/FP32/micro8、admission-v1缓存。用户已撤回此次lambda1/value.02预设，旧准备目录仅提案且未投入GPU；先按新尺子逐项选择，不预装MLP。reference KL .1–.17只是监控，watch停线为None；坏quick/行为按原卡回退。

新尺子固定v416/1749，同CP动作权重正常绑定、48个永久TRAIN留出seed、同训练纯π采样：46自然saved/2unknown，均13.37层/中位14/最高24/0胜。7826行完整MC EV.1708、局内EV.2301、等局EV.1743；按行偏差−.00320而按局+.01091，按局敏感区间跨0，旧“系统性低估两成”不成立。早/中/晚EV.038/.128/.270。正式工具候选[入口](../../tools/friday/value_validation.py)已9项数值反例、非作者及真实physical-read scorer通过；类型/准确CI/正式集成待收。评分改动前后及quick前后各做当前策略点，旧固定23/4627只连续性。下一点拟7games，24actor+EV7+quick4+U1预留1=36；U1此窗口单路两局旧Input-v2/Host59 normalActor/R2兼容短链，当前RL3/quick仍原Host58Runtime59。

第二quick v296/1062（15:28冻结）16:33完成20合格自然配对：14.0→18.4/+4.4层，14远4近2同、固定suite敏感95%[.75,8]、0胜；1unknown原ordinal9不重发，补新21。下一quick19:27:51/23:27:51不移；将按共同合格seed比较相邻模型，动态采样训练曲线不替实力。末同窗起点/最终各100尚未freeze/执行；两边同Runtime/Host/条件，unknown剔整对并逐项列出，数量不令整轮作废，零有效对不给估计，其他缺失保持未完成，raw audit/scored不改。旧144槽131saved/1unknown/1save_pending/1trajectory_pending/10reserved等失败保留不重发。U1首批Host59源码/包已交但完整Actor/R2短链未收，P1r1.8新输入不混本轮。状态/恢复/工具条件看[原SOP](../../python/docs/RL3_RUN.zh-CN.md)，原EAIT失误与所有失败/保全义务仍归private本卡报告。最终100、完整统计/A10建议、7个when-next-rl的证据与未完、所有新旧数组保全仍欠。

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

## U1 独立修复入口（2026-11-01 用户 v3）

PR359/Host59、PR361/Host60与PR367/Trial/Claws已普通合并。Host59两局Actor/typed/R2读回通过；训练主决定自然重启采用旧输入修复。下一轮输入只迁移一次：Managed Sphere与Trial/Claws合成显式P1r1.9、InputSpec `stpd-p1-mstd-input-v2-r19@1`、Host62/SDK19/Runtime62，不先迁移r18。原参数兼容构包、全242候选、正常训练/冻结入口已接通；最终准确包与CI/运行交接沿[U1证据](../evidence/UNKNOWN_FIX_U1_20261101.md)，不热换当前RL3或日常安装。

Sphere按用户批准的逻辑状态公开：至少一格露出的物品给完整矩形与种类，全未露面不公开；忽略透明角/同种局部图片细节。Reference本卡不要求，#366不挡Managed完成。#362建筑师等首次第三幕Boss清掉或首次进入再修；#363/#364/#365/#366留Issue，本卡不展开。129条分类和所有旧unknown/分母保留，不重发、不改成已恢复。

## Remaining Platform non-claims（运行与证据边界）

- 未来价值loss默认无权重，T2原W1历史不回写；动作W1另由下代配置决定。
- 混合整局不算模型评估；人的标签/片段不因P1可读或入Store自动准训。
- 固定100评估seed永久不训练，原确认20保留；训练监测与独立确认分开。
- unknown不重试、不当负局或零层；结果按原ledger、审计和保存读回判断。
- Managed成绩不替代真游戏实力；水晶球等支持域缺口和严格并行等价仍保留。
- 安装、发布、运行身份看原收据/实际节点，不从develop或CI推导；[STATUS](../STATUS.md)只路由证据。
