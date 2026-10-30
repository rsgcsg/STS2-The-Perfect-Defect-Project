# 当前：D2 数据实验进行中，卡 C 暂停

2026-10-30维护同步：RL2已永久收口，不恢复；原模型、完整CP、轨迹、unknown、失败和STOP保留。卡A/B和卡D已交付，当前D2由原负责人独立训练和评估；MAINT-1只做获批维护，不占游戏/GPU，不改D2文件或占位。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前任务与唯一下一步

| 归属 | 状态 / 接手入口 |
|---|---|
| RL2 | 已收口；v337为2691更新，最新完整CP335为2675更新，两者不混。12路独立封存补拷归MAINT-1，原0/12失败收据不回写 |
| RL3准备卡A | 已交；[PR325](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/325)注册新100评估seed；E1未证明变强或变差 |
| RL3准备卡B | [PR328](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/328)已合；实际20更新/换模/两完整CP/正常保存短链通过，长跑资格仍未完成 |
| 数据卡D | [PR329](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/329)已合；原41局去单选简单池17751点、数据/拟合体检和统一尺子已交 |
| 数据卡D2 | 进行中：原T2检查点曲线、3744续训拟合上限、数据量与噪声、商店核查。最新设置/资源/结果由D2原任务和GAME-SLOT维护；不以旧学习者提速D2代指本卡 |
| RL3卡C | 暂停；不因工程准备或旧计划自动启动。锚点与方法实验、长跑配置待用户恢复/定稿 |
| MAINT-1 | 清理、历史登记与Issue同步已执行；十二路补拷终态待原维护报告核实，不视部分副本为完整保存。[收据及剩余项](../evidence/MAINT_1_20261030.md) |

D2下一步由原负责人按其已批预算完成并报告；卡C的启动是后续独立决定。原私有工作区的 `replies/EXPERIMENT-REGISTRY.md` 已建立，D2追加自己的行；实验原件不复制进公开仓库。

## Remaining Platform non-claims（运行与证据边界）

- 日常Workbench为 `0009eb35`；文档合并不改变安装或运行生产者。保留 `078908592` 回滚，仍被profile引用的旧发行不删。
- 新专用100评估seed与原确认20隔离；unknown不重试、不当负局或零层。成绩要有原账本、审计与保存读回。
- 后台预取未通过保批次/候选池门，默认关；固定窗FP32提速不等于端到端长跑产能。
- [RL3运行入口](../../python/docs/RL3_RUN.zh-CN.md)仍复用原learner/actor/watch；模板未批准、时长未定不授开跑。
- 源码、CI、安装、真实闭环、数据资格和模型成绩分别验收；Managed成绩不替代真游戏实力。
- 接管片段准入仍按H1隔离；未来价值loss默认不加权。T2原W1配方和历史原件不回写。
- 旧真游戏100槽已由用户关闭：35已开、0胜33负2未知，剩余不再启动。

[当前状态索引](../STATUS.md#维护同步2026-10-30)和[未完Issue归属](../evidence/MAINT_1_20261030.md)供导航；不按历史“待启动”复活任务。
