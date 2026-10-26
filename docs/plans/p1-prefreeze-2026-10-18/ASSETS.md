# K1 全项目资产账本（只读，2026-10-18）

> 2026-10-27参考定位：本目录保原任务/合同/盘点快照，当前能力和运行节点从[根地图](../../DOCUMENT_MAP.md)进入，派工只看[CURRENT](../../memory/CURRENT.md)。[本轮去向增量](../CONVERGENCE_DISPOSITION_20261027.md)补K后的变化；K3已有执行收据优先于旧“未执行”摘要。本轮未移动原目录或改写下面历史正文。

**交付**：[项目地图入口](README.md)；本仓保留一次完整 [CSV](asset-ledger.csv)，共2063行；原完整JSON和详情文件的精确路径/大小/SHA见[source index](asset-sources.json)。每行有是什么、层、独有成果或未核实、状态、建议去向和出处。目录/ref/文档/副本相互重叠，行数不是功能数、局数或可释放空间。

日常先从[操作导航](../../DOCUMENT_MAP.md#按我要做什么进入)进入现owner；遇到旧native-ui、Recorder连录、Store/R2或GPU恢复，从[已有解法](../../RESOLVED_ISSUES.zh-CN.md)直接到代码/原收据。本账是2026-10-18元数据盘点，不能用它的目录、PR、dirty或空间数字操作当前资源。功能迁入与支持入口只在[原功能表](FEATURE_STATUS.md)维护；未核语义不能靠归档标签或该账的行数完成。

**历史查过**：H1 主表/附表/JSON、10-16 branch-inventory、10-16 ban/worktree-list、7 个交接包入口及当前 `00_START_HERE`/卡 K；复用独有成果线索、恢复包和结论边界；旧计数与旧清理结论不沿用，Git/远端和目录已重新查。固定 develop=`60dc0a89`；远端快照约 17:50 Brisbane，活动 PR202 仅记当时 `0affa651`，由 Root 接管。

| 覆盖分母 | 已登记 |
|---|---:|
| 桌面顶层目录（反编译仅登记存在） | 167/167 |
| 共用 Git 库 / 注册工作树（均存在） | 9 / 173 |
| Codex 日期 / 直属工作目录 | 14 / 148 |
| 真实 GitHub 仓库 / 全部开 PR / 远端分支 | 9 / 31 / 66 |
| 主仓库开 PR / 远端分支 | 30 / 38 |
| develop 根 docs 文件 | 227/227 |
| 交接包 / 报告索引 / 限定结论 | 7 / 570 / 14 |

**优先保全**：保护清单 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-K1-20261018/protected-assets.json`)逐项列 **6 dirty 目录、46 个潜在未覆盖本地 refs**。46 是本地 tracking-ref 差异候选；实时全部 heads 补核为 43 个不可达，RitsuLib 1 个因缺对象只能给上界；均先保留，不等于从未推送或功能未合入。CI balance 已证 patch 等价。旧 WIP/bundle/失败结论保留；95 组报告副本不重计整局。

**去向批次**：①上述 dirty/refs/数据原地保留；②5 个历史交接/审查包只建立[逻辑归档索引](asset-archive-proposals.json)，物理移动前核消费者、另批；③逐 PR 建议见[远端表](ASSET_REMOTE_REPORT.md)，30 个主 PR 均非祖先包含，语义迁入多数未核实，尚无可立即批量关闭结论。

**边界**：135 个其他/未明 Codex 工作目录只登记元数据；嵌套未注册 checkout、ignored 原件、活动消费者和逐功能迁入仍可明确未核实。两大数据根 `research/s01-stage1-20260918`、`SpireAgent/Re-SpireAgent/data` 已仅按元数据登记保留 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-K1-20261018/large-data-metadata.json`)，不读 Human 正文。未删除、移动、关闭 PR、删分支、fetch/改 refs、改产品源码或启动游戏；K1 是账本交付，不代表 K3/K4 完成。

逐目录/PR/ref去向可从CSV的identity检索；[本地目录/ref说明](ASSET_LOCAL_REPORT.md)、远端表及小归档建议表一并保留。Git将CSV按LF保存，2063行解析内容与原CRLF文件逐行相等；source index分别列原始文件与仓库副本的字节数/SHA。大型原JSON不重复提交，去向仍是建议，未执行处置。
