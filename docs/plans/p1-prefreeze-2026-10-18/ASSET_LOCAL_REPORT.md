# K1 本地资产增量（只读）

历史查过：H1-2026-10-17.md / H1-details / H1-inventory.json 复用目录及旧结论分类；10-16 branch-inventory/DISPOSITION.md 复用不可按年龄关闭、可达性不等语义独有的判定；2026-10-16/ban/outputs/worktree-list.md 仅复用调查入口，旧计数和可清理结论不采用，全部 Git 状态已重查。

基线 `60dc0a89ad6ae19b26e0e1bbf71517471f409069`。桌面顶层 167/167 目录；9 共用 Git 库；173/173 注册 worktree 路径存在；顶层加注册 worktree 补充共 185 目录行；410 本地分支。6 dirty；25 目录 HEAD 和 46 本地分支有相对本地远端跟踪 refs 不可达提交。它们不等于从未推送或语义未合入。

实时 heads 补核：43 个本地分支、24 个目录 HEAD 不被所属远端当前所有 heads 覆盖。主仓库读取 sprint-C 独立仓库已有 objects 作为进程级 alternate，只读、未 fetch 或改 refs。RitsuLib 5 个实时 heads 中仅 2 个对象在本机，其 1 个本地分支仅能给上界，明确未核实。参见 local-live-summary.json。

独立提交对象索引 `local-nonremote-commits.json` 含 389 行（每共用 Git 库去重）；包括本地 branches/tags/登记 HEAD 相对本地远端 refs 差异。跨库不得把重复迁移提交或目录当独立功能或运行。

## 必须先保留的未提交工作

| 目录 | HEAD | 未提交路径数 | 建议 |
|---|---|---:|---|
| /Users/fire/Desktop/SpireAgentProject/STPD-analysis | ebabd308d1b8 | 1 | 保留；完整路径清单见 JSON |
| /Users/fire/Desktop/SpireAgentProject/STPD-fullrun-gold | 2a45185c0ee1 | 1 | 保留；完整路径清单见 JSON |
| /Users/fire/Desktop/SpireAgentProject/STPD-integrity-review | ad4bc1c5a509 | 3 | 保留；完整路径清单见 JSON |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-legacy-structured-m2-read-arm | 5a037165a54b | 16 | 保留；完整路径清单见 JSON |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-p1-c1-validation-20261016 | 222fad8947d9 | 67 | 保留；完整路径清单见 JSON |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-p1-consumer-wire-20261016 | 222fad8947d9 | 41 | 保留；完整路径清单见 JSON |

## 含本地 refs 差异的目录

| 目录 | HEAD | 本地 remote refs 未覆盖提交 | 现况 |
|---|---|---:|---|
| /Users/fire/Desktop/SpireAgentProject/STPD-analysis | ebabd308d1b8 | 2 | dirty |
| /Users/fire/Desktop/SpireAgentProject/STPD-fullrun-gold | 2a45185c0ee1 | 1 | dirty |
| /Users/fire/Desktop/SpireAgentProject/STPD-integrity-review | ad4bc1c5a509 | 1 | dirty |
| /Users/fire/Desktop/SpireAgentProject/STS2-AI-PLATFORM-pre-full-run-hardening | 037437549240 | 3 | clean |
| /Users/fire/Desktop/SpireAgentProject/STS2-AI-PLATFORM-recording-hotpath-performance | 5185d47bc961 | 45 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-ci-balance-20261015 | f40c13b55584 | 3 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-legacy-structured-m2-read-arm | 5a037165a54b | 11 | dirty |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-monitor-fixed4-facts | f7d2125ed61b | 2 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-p1-c1-validation-20261016 | 222fad8947d9 | 65 | dirty |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-p1-consumer-wire-20261016 | 222fad8947d9 | 65 | dirty |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-p1-shared-profile-20261016 | 0fe70b952e8a | 58 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-p1-viewing-probe-20261016 | c4dc3922a363 | 59 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-public-structured-decision4-port | 45b23bce1ebc | 9 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-remote-monitor-source-gate | f9015b9c5630 | 14 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-round-evidence-public-read-profile | 0e7ad48ded83 | 2 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-round-native-episode-application | 89bb4c4b057b | 4 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-round-real-store-startup-diagnostics | 8d593a340948 | 1 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-source3-recordability | 6c9f40184f2a | 2 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-structured-remote-domain-c | 0e31b8c07b9d | 3 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-v1-bounded-corpus | 358fcda5cef4 | 5 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-v1-combat-candidate | 2d9c0ffe31ea | 3 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-v1-combat-owner-bridge | a4a954a176f2 | 5 | clean |
| /Users/fire/Desktop/SpireAgentProject/SpireAgent-v1-upgrade-materialization | 1032f1413dd9 | 2 | clean |
| /private/tmp/spireagent-j-min-cloud-1aa567db | 1aa567db8eda | 1 | clean |
| /Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/landing189 | 9c1259f9ae60 | 8 | clean |

CI balance 反例已按 60dc 重核：`git cherry 60dc f40c13b5` 返回 `- f40c13b5`，可见提交身份差异不能当功能遗漏；见 `ci-balance-cherry.txt`。

## 可审批去向批次（尚未执行）

1. 保全批：6 dirty 目录、46 含本地 ref 差异分支及 389 提交索引先保留；原 WIP 恢复包不动。此项只是保留建议，不建议批量合入。
2. 逻辑归档批：旧审查包、global handoff/delivery、Codex closeout 纳入新归档索引，原路径与失败、unknown 结论保留。移动、删除仍须另批；当前只交新索引。
3. 延后去向批：其他 clean 工作树仍可能有 ignored 原件和运行消费者，未核实；不建议仅凭 clean 删除。磁盘候选由 root 独立表负责。

完整逐目录、逐本地分支及 dirty 路径：`local-assets.json`；无任何目录、PR、branch、数据的去向已执行。无 native、Human、训练新验证。
