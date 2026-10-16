# P1 r1.1 续作计划（2026-10-16，交给 Codex）

权威：[P1 全局 r1](../protocols/P1_GLOBAL_R1.zh-CN.md)。规则：[WORKING_RULES](../WORKING_RULES.zh-CN.md)。
基线：分支 `claude/p1-complete`（草稿 PR，目标 develop）。从它的最新提交切自己的短分支。

## 这一轮的目标（不变）

一条清楚、稳定、可替换的链：
Reference / Managed → 完整、公平的 P1 观察和单向动作 → 任意外部 Agent 选择 → Runtime 执行和记录。
还要求独立的环境录制（Source4）能转换成可校验的 P1 轨迹。
上层链（Agent 端口、记录、对照）要能在 P2 原样复用。

## 停止线（做到就收口，冻结 P1 r1）

1. Managed：40 局套件（Defect，zh）≥ 95% 自然终局，0 unknown，卡点原因全部列出。
2. Reference：20 局套件 ≥ 90% 自然终局，0 unknown。
3. 等价：至少 5 局 Reference 真实轨迹在 Managed 上重放到底。分叉先修 Managed；修不了的登记成 Managed 已知差异，要用户确认。
4. 录制：用户在真实游戏里录一段短的原生操作（Source4），离线转换成 `complete=true` 的 P1 轨迹。
5. 文档：P1 权威文档第 16–18 节更新到最终数字，CURRENT 一屏。

达到后开 PR 收敛到 develop。合并要用户批准。之后 P1 r1 冻结，新需求进 r2 或 P2。

## 待办（按顺序 T1、T2、T7、T3、T4；每项都是真实失败，含复现）

复现时使用的变量：
- `W`：仓库根；
- `C`：Managed 候选构建，用 `repin` 流程生成，见 host-runtime 文档；
- `REF_ROOT`：Reference 状态目录（本机 `SpireAgent-claude-p1-global/.local/ref-state`）；
- `AGENT`：`python3 $W/components/policy-runtime/examples/p1/random_agent.py`。

### T1 Managed 缺牌组卡详情（等价第一分叉）

现象：Reference 种子 `STPD91686A026CCEF3EA17DE24BD4890` 的 96 步轨迹，在 Managed 上第 0 步就分叉，位置是 `$.observation.deck.content.cards[0]`。
- Reference 给每张牌组卡发一条 `surface_card` 详情读取（`extra_reads`，含 keywords、card_previews），Managed 一条都没有。
- Managed 牌组卡多出 `can_play: null`、`target_type: null` 两个空字段；Reference 不输出空字段。
- 因为有详情读取，Reference 的同名卡是具名别名 `e66`，Managed 是匿名 `a_deck_1`。

做法：Managed 投影（`components/host-runtime/src/managed-player-environment.mjs`，必要时 candidate C#）按 Reference 的同一形状给出牌组卡详情。空字段不输出。
验收：同一轨迹在 Managed 上重放越过第 0 步；记录下一个分叉。

复现：

```sh
node $W/components/policy-runtime/bin/sts2-p1.mjs trajectory --run-directory <ref-run> --out t.json
node $W/tools/p1-suite.mjs --host managed --candidate $C --character Defect --language zh \
  --seeds STPD91686A026CCEF3EA17DE24BD4890 --out out \
  --agent "node $W/components/policy-runtime/examples/p1/replay_agent.mjs $PWD/t.json $PWD/r.jsonl"
```

### T2 Managed 选卡页元数据不完整（6/38 局卡点）

现象：40 局套件里有 6 局停在 `card_reward_selection`，缺 `exact_card_reward_parent_and_card_identity`。
例如种子 `STPD8E9E3AD38E6DC8B98394AC01485D` 停在第 17 个决策。

判定在 `managed-player-environment.mjs` 的 `metadataComplete`。需要先查清是哪一项缺失：
- `card_entity_ref`
- `parent_reward_ref`
- `parent_status` 是否为 `bound`
- `reward_set_ref`
- `selection_completes_reward`
- 渲染字段

原始状态没有进证据。复现时在候选里打印原始 state，或用 `managed-probe`。修复要以 Reference 同类页面为准。

### T3 Reference 20 局套件

命令：

```sh
node $W/tools/p1-suite.mjs --host reference --local-root $REF_ROOT \
  --suite $W/python/stpd/evaluation/suites/test-1000.json --limit 20 --out ref20 --agent "$AGENT"
```

逐个修卡点（Reference 是真相，修 Connector 或 SDK 映射，不放宽公平性）。本轮已经修掉的 Reference 卡点：
- 地图 `current_position`；
- 投递后的过渡态等待；
- 游戏结束页的地图读取；
- 商店关闭的动词 `cancel`；
- 套件每局重启 driver。

### T4 Reference→Managed 等价套件

把 T3 的每局轨迹都在 Managed 上重放，汇总每局第一处分叉，按原因计数。分叉默认是 Managed 缺陷（T1 是第一个）。

### T5 真人短录制（需要用户操作）

先构建并部署包含本分支 Source4 改动的 Mod（现有安装不自动具备）。

1. 真实游戏里打开 **Platform → Workbench → 采集/Data → 原生交互与观察**。如果有模型在控制，先 Human/Stop。
2. 格式选 **Source4（保留 P1 公共基底）**，来源选 **本人操作**，填 actor ID，点开始。不启动 Agent。
3. 在原生游戏界面里亲自操作一小段。建议先包含一次结束回合。
4. 点 **结束并封存**，等生命周期和 closeout 都显示 `closed`。
5. 打包并离线转换：

```sh
dotnet run --project components/annotator/src/STS2HumanAnnotator.Tool -- pack-source-v4 /ABS/SESSION operator campaign /ABS/NEW_BUNDLE PACKER_SOURCE_REVISION_40_HEX
node tools/source4-to-p1.mjs --bundle /ABS/NEW_BUNDLE --python /ABS/PYTHON_GE_3_11 --host-kind reference --output /ABS/NEW_REPORT.json
```

检查报告里的 `complete`、`actions`、`unresolved_inputs`、`unresolved_contexts`。退出码 0 不等于转换完整。

### T6 待用户决定：卡牌渲染测试

`NativeRewardCardRendererTests.SameActualNativeRewardCardRetainsTheFullReferenceDtoAfterExactParentScreenBinding` 失败。
原因：本轮按"原生逻辑优先、不做纯度证明"去掉了变量、卡池、悬停图标和格式化器白名单，被打补丁的固定闭包仍然拒绝。这个测试还在断言这些白名单会拒绝。
用户批准后，把这几段断言改成新规则；或者恢复白名单。**不要自行删除断言。**

### T7 投递后的"等待稳定"改为原生信号（用户要求，优先于 T3）

现状：本轮为跑通 Reference，在 `agent-native-runtime.ts` 加了一个临时补丁（选项 `globalPostDeliverySettleMs`，默认 15 秒）。已知投递后，如果 Host 返回 `capture_status: partial`，就每 100 ms 重读一次，直到完整或超时。它只重读、不重发，但判断"稳定"的依据是时间，这不符合要求。

要求（用户确认）：
- P1、Connector 和 Runtime 判断"可以给 Agent 下一个观察"时，**以原生事件或原生状态为准**，不靠读秒。
- 只有在原生确实没有信号、或原来就是计时实现时，才用计时，并且要在文档里写明原因。

做法：
1. 在 Connector 找出投递后不完整的真实原因：过场或动画、动作队列未清空、页面未就绪、某个 Read 暂时不可用。复现用本轮 Reference 套件（`tools/p1-suite.mjs --host reference`）。
2. 对这些情况，用原生状态标 `status: settling`（已有字段，Runtime 已经会无时限等待 settling），不要发 `partial`。可用的原生依据：ActionQueue 或命令队列是否空闲、NOverlayStack / ActiveScreenContext 是否已切到目标页面、过渡或动画是否结束。现有的就绪判断优先复用。
3. 真正缺信息时仍发 `partial`。Runtime 收到 `partial` 时立即按原逻辑停下，不再计时重读。
4. 时间上限只防卡死（例如 settling 长时间不结束）。超时要明确停局并记录原因，不得当作稳定。
5. 删除 `globalPostDeliverySettleMs` 的重读循环，或把它改成只防卡死。改测试（`global-forward-runtime.test.ts` 的部分抓取用例），再用 Reference 套件重跑。

验收：Reference 5 局结果不差于本轮（4/5 终局，修复后 5/5），事件日志里没有因计时放行的观察。

## 交接格式

每个任务结束写 ≤ 1 屏：
- 改了什么、为什么；
- 跑了什么命令，结果数字（完整分母）；
- 下一个真实卡点和复现命令；
- 需要用户决定的事。
