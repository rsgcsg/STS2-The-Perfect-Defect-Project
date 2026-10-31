# 当前：CARD-1A 游戏内对局台开发与安装验收

2026-11-01：用户已派 CARD-1A / [#340](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/340)。本卡 owner 为主对话，范围是游戏内多窗口、排序/鼠标/模型的统一用例、记录/标记、清理 Mod GUI、直接安装及部署登记；用户实测后才收口。不训练、不改 RL、不碰凭据。1B 只读、1C 在训练侧另跑，本卡不写 `python/stpd`。预计本地 05:05 AEST 前完成，08:05 硬收口（不计等用户实测）。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前任务与唯一下一步

本轮以[#340](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/340)及用户v6卡为准，长期语义归[交互规范](../UI_INTERACTION_SPEC.md#current-pre-training-scope)，入口去向归[游戏内指南](../../apps/ingame-ui/README.md)。PR344/345已普通合入develop，安装source ab816a93完整CI绿；Workbench cc39dc7f / UI24 / Mod72 / Runtime60已装并冷加载，窗口与入库关闭时退出Stop实核通过（0游戏动作）。旧Store绑定缺失后，用户明确选择新建持久本机库；原owner建立并重绑、旧配置保留，无旧数据导入/改写、无云端保存。正式保存按钮成功，原CLI按准确artifact读取0动作/11控制事件索引；Deployment7462443141新增success并保留原failure。**唯一下一步：用户试1–2局覆盖鼠标/排序/模型展示/交替/一步/标记/点错/取消，然后由主对话核对原件与完整入库读回，再决定340收口。**游戏在菜单、未录制、Runtime Human无自动走；不重复安装或执行unknown请求。诊断GLOBAL类型分歧346、舒适项343与账本300保留，297用途决策已关闭。准确身份、失败历史和局部验证边界见[STATUS](../STATUS.md)。原[统筹复核](../evidence/COORDINATION_1_20261031.md)保留其他线的历史义务。

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
