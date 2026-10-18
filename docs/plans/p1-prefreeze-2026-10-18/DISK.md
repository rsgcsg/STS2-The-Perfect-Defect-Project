# K 磁盘表（2026-10-18，只读，待批准）

历史查过：查了 10-16 盘点/清理收据，复用“先查独有成果和活动资源、后按批批准”的方法；本轮重测 181 个一级目录，未沿用旧许可。K1 继续核对 dirty、独有 refs、PR 与归档去向。

当前 `df` 可用 **3.4 GiB**（18:00 前复测；早前故障时约 1.7 GiB），不把变化归因为本轮清理。本轮没有删除或移动；缓存也未处理。只有缓存约 **0.234 GiB** 可明确再生，单靠它不足以恢复长局空间。以下大小是 `du` 观察量，**不是承诺可释放量**；同卷移动释放约 0，外盘迁移需另定目的地、核验和批准。各行有父子重叠，不求和。

| 项目/路径 | GiB | 能否重新生成 | 建议及空间价值 |
|---|---:|---|---|
| `~/.local/share/spireagent/research/s01-stage1-20260918` | 25.261 | 未证明；研究数据 | 最大迁移候选；先元数据确认唯一性/消费者，外盘归档才可能腾空间，保留原件待批准 |
| `SpireAgent/Re-SpireAgent/data` | 15.156 | 未证明；历史数据 | 第二大迁移候选；不能按构建产物删除 |
| 当前工作区 `work/runs` | 6.204 | 不能假设可重跑 | 保留执行和原始证据；先做 K1 去向与活动依赖核对 |
| `~/.local/share/spireagent/releases` | 5.953 | 仅有源码不等于完整再生 | 安装包/身份/回滚资产；活动版本保留，历史版本逐项归档 |
| Codex `2026-10-04` | 6.016 | 混合 | `task-10` 4.239，其中 maintenance-private 1.296、recovery-releases .921、c8-runtime .910、kit-environment-fix .851；先查独有证据 |
| Codex `2026-10-05` | 3.947 | 混合 | `task-3/validation` 3.701；验证产物不是缓存，保留后列迁移批 |
| `STS2-headless` | 3.343 | 源码/输出混合 | K1 核对 dirty/独有提交/运行引用后，单独列 worktree 批 |
| `SpireAgent-g1-s0-integration` | 3.125 | 源码/输出混合 | 同上；不按“旧目录”直接处理 |
| Codex `2026-10-01` | 2.296 | 混合 | task-8 1.156、task-22 .797、task-10 .339；分别登记去向 |
| 当前 `round12-managed-diagnostic` | 2.303 | 原始诊断不可冒充再生 | 保留旧 attempts/unknown/证据；可在批准后完整迁移 |
| `~/.local/share/spireagent/campaigns` | 2.286 | 历史录制不可再生 | K0 只统计格式/整局，不读真人正文；优先保留 |
| 当前 repo/fold-fixes/t6/landing191 四 checkout | 5.488 | 干净源码部分可重建，ignored 未确认 | K1 逐项保护独有成果、活动依赖及本地数据；不能把整目录认作可删 |
| 当前 A/B/C/root 四工作目录 | 1.933 | 活动候选与证据混合 | 冲刺中保留 |
| `~/.local/share/spireagent/managed-releases` | 1.368 | 待核完整重建链 | 活动候选 1482de5d 与原始旧候选保留 |
| `~/.npm/_cacache` + `~/Library/Caches/Codex` | .234 | 可再生 | 可单独批准清理；Codex 活动缓存需关闭相关写入后处理，收益有限 |
| `反编译sts2` | 4.897 | 本轮不调查 | 仅登记存在/大小；不读、不动 |

根目录参考：`~/.local/share/spireagent` 35.296 GiB；`SpireAgent` 16.667 GiB；Codex `2026-10-17` 20.953 GiB，其中本工作区 `work` 19.196 GiB。10-01～10-05 合计 12.624 GiB。

完整 181 目录明细 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-K-disk-20261018/DIRECTORY_SIZES.md`)及原始测量 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-K-disk-20261018/directory-sizes.json`)已保存。建议先批准**哪批数据可以迁移到哪个外盘**，再执行核验；当前没有已批准的删除/迁移批。K3 按卡 K 要求逐表批准，未批准前继续只读盘点。
