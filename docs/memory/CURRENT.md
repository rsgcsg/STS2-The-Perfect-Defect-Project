# 当前：CARD-RL-C / RL3 在线PPO（用户v7及追加）

用户已派RL3，root负责。[本轮设计](../plans/RL_CRITIC_20261101.md)及[原运行指南](../../python/docs/RL3_RUN.zh-CN.md)拥有配置；旧扫描卡撤销。最新用户决定：不回滚、不另开训练批，接当前完整状态；零输出MLP残差、未来新RL默认MLP，不用失败离线候选，不设线性对照/回退/额外价值验门。lr4e-5/head1e-4/λ.95/value.001/entropy.01、FP32批256微8、2use/prepared seq1保持；用户追加授权下一完整CP续接把开局新鲜度600秒改900秒。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前任务与唯一下一步

11-02联合切换：旧32Actor全部自然结束，旧GPU1400318正常0保存v732/5476；全部12938 exchange记录唯一seed读回。新GPU1400871/source c87f2c8c（PR368正常合并，含共享管道）已于00:56:08发布v733/5476及MLP完整CP。动作tensor/词表/InputSpec/执行逐项相同，包SHA仅因血统/CP元数据改变。Host63/Runtime62已正常冻结验证，本轮仍Input-v2/r1.5，不混r19。当前新GPU等数据，原12路只保存退出快照；正常提交器须按Actor已退出收据区分保全阶段，保持原名额/身份/unknown和全原件保全要求，再开新32与新watch。具体job/intent/最新采用以root本轮收据为准，不从计划推存活数。

**唯一下一步：完成正常32路/看守接管并继续到原终点，再做最终三组同100与保全收口。** 用户允许延至下个四小时检查点，训练11-02 07:27:51、全卡11-03 05:40末2h不延长；单免费GPU/累计30h、游戏cap45，不碰Mac游戏/秘密输入，RL2不恢复。旧失败12任务及所有unknown原样保留，旧21:32–22:03不是稳定32的比较作废；真正稳定32约97374首次用决策/h、wait0，只作观察证据，不据旧数字加38–40路。

最新quick v681/4836对起点20合格pair为14.25→15.7/+1.45；与v471共同18对22→15.722，−6.278，固定组95敏感[-9.611,-2.944]，近期退步用户知情仍不回滚。v681原22槽=20自然/2unknown。最终07:27后起点/v471/最终各原100同种子argmax、相同Host58Runtime59/原冻结eval源码；v471准确完整动作包已读回无fallback。三个pair分别剔unknown整对、逐项列，不补局、不按数量VOID；其他缺失incomplete。报告均差/区间、逐层死亡分布/max/Boss1/完整分母，训练按实际切点前后分段。仅建议final−v471区间全负选v471策略，否则final，评分头按零MLP，最后用户定，不自动下一轮。最终300局、原因及GPU/CPU全链性能分析、七Issue/#338实际末CP余量与全部旧新array/GPU/Store/observer/失败前缀保全仍欠。

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
