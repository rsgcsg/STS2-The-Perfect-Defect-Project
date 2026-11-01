# 当前：CARD-RL-C / RL3 在线PPO（用户v7）

2026-11-01：用户已派RL3，替代暂停的10-30卡C；root负责。[本轮设计](../plans/RL_CRITIC_20261101.md)归v7，旧扫描/A-B方案已撤销且未运行。目标是每小时墙钟实战提升；长训s8136折1B温度，直接π采样、共享价值头/GAE/熵，anchor0、KL0监控，退化回好CP开KL .02。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前任务与唯一下一步

截至11-01 18:08 AEST，PR355完整PREP-B FP32已合入e95ffacb，learner实际source4355889d/GPU1400147；actor冻结52a7cd83，12路1399798继续，新增12路1400175已正常退出并完整读回封存（12/12、517105文件）。PR356新watch1400213已接管所有新旧数组，observer1399828继续。GPU一次一张免费卡累计≤30h、游戏并发≤36（含并行U1最多4路）；原训练11-02 03:27:51 AEST与全卡11-03 05:40硬收口不重置，末2h保全。不碰Mac/凭据，RL2不恢复，100评估seed永不训练。

**唯一下一步：把已验证的完整reader进程隔离接入正式链路，稳定候选过检查/非作者复核/准确CI后，合并到一次正常完整CP迁移。**固定33shard/7112准备样本和128完整帧摘要前后全同；CPU对照372→362秒，仍有27秒host停顿，修复未部署，不称吞吐解决。PR357最终88893ad5的CI41285668492全绿，已普通合入11eafd4f；原磁盘夹具失败保留，产品15GiB门槛不变。进程隔离CPU诊断33/33、7112样本/128帧摘要全同，含bytes IPC耗时143秒、foreground最大.077秒；正式backend候选41项相关检查、相关mypy和非作者复核通过；准确候选CI/上线仍待验。当前仍435，策略lr4e-5、head1e-4、无权EMA标准差残差/value.001、λ.95、batch256/复用2/600秒/micro8、torch4/reader4。16:13 prepared-targets缓存实际热开且完整CP读回通过；prepare耗时降但host停顿令首次用数降至约3万/h，已收回供数过剩的12路，不能把读入generated计数当actor真实产能。normal observer上传时间独立测得12路约4.69万决策/h、24路约9.50–9.60万/h；重叠阶段非单因素因果。参数可按用户新授权自主调整，尚未把λ1/value.02方案部署。

第二quick已于16:33完成20合格自然配对（15:28冻结v296/1062）：起点14.0→18.4层、+4.4，14远/4近/2同，固定seed bootstrap敏感区间[.75,8]，两边0胜；21个current offer含1unknown，原ordinal9不重发，新ordinal21补配对。不能归因后续FP32/缓存或当最终资格。下一quick19:27:51。固定旧policy留出23seed/4627行EV约.161，当前policy新留出实战还未做。原起点quick20与旧100的unknown/audit缺口保留。用户另批末尾同窗起点/最终各100含旧56：双方同Runtime/Host/条件，unknown不重试，任一边unknown剔整对，逐项列unknown种子/side/原因，数量不使整轮作废，零有效对不给估计，其他缺失/删失保持未完成；草稿通过不替实际freeze/执行。完整状态/证据在[原SOP](../../python/docs/RL3_RUN.zh-CN.md#可选公开输入评分网络)与private本卡报告。U1专修unknown版本仅在自然重启点由root选采用，原[#350](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/350)事实缺口仍不推定已修；原失败、未知分母和新旧原件全部保留。新写共享scratch，旧data个人quota满不删原件。

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

## Remaining Platform non-claims（运行与证据边界）

- 未来价值loss默认无权重，T2原W1历史不回写；动作W1另由下代配置决定。
- 混合整局不算模型评估；人的标签/片段不因P1可读或入Store自动准训。
- 固定100评估seed永久不训练，原确认20保留；训练监测与独立确认分开。
- unknown不重试、不当负局或零层；结果按原ledger、审计和保存读回判断。
- Managed成绩不替代真游戏实力；水晶球等支持域缺口和严格并行等价仍保留。
- 安装、发布、运行身份看原收据/实际节点，不从develop或CI推导；[STATUS](../STATUS.md)只路由证据。
