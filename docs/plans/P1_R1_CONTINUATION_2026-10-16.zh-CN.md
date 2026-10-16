# P1 r1.1 续作计划（2026-10-16，交给 Codex）

权威：[P1 全局 r1](../protocols/P1_GLOBAL_R1.zh-CN.md)。规则：[WORKING_RULES](../WORKING_RULES.zh-CN.md)。
基线：分支 `claude/p1-complete`（草稿 PR，目标 develop）。从它的最新提交切自己的短分支。

## 这一轮的目标（不变）

一条清楚、稳定、可替换的链：
Reference / Managed → 完整、公平的 P1 观察和单向动作 → 任意外部 Agent 选择 → Runtime 执行和记录。
还要求独立的环境录制（Source4）能转换成可校验的 P1 轨迹。
上层链（Agent 端口、记录、对照）要能在 P2 原样复用。

## 候选收敛指标（通过不自动冻结 P1 r1）

1. Managed：40 局套件（Defect，zh）≥ 95% 自然终局，0 unknown，卡点原因全部列出。
2. Reference：20 局套件 ≥ 90% 自然终局，0 unknown。
3. 等价：至少 5 局 Reference 真实轨迹在 Managed 上重放到底。分叉先定位原生事实/两端投影/共享核心；真实协议差异需用户确认，不能默认一端永远正确。
4. 录制：用户在真实游戏里录一段短的原生操作（Source4），离线转换成 `complete=true` 的 P1 轨迹。
5. 文档：P1 权威文档第 16–18 节更新到最终数字，CURRENT 一屏。

以上是运行可用性采样指标，不是完整机制/信息正确性或策略能力证明。冻结还须已声明场景的完整信息/动作/失败语义覆盖、同一候选的跨Host终局比较、必要Human记录与所选门通过；不把一幕死亡终局率替代覆盖。达到有用竖切即可提交草稿PR，合并和最终冻结分别审查。

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

逐个修卡点（以原生事实核对，修实际错误owner，不放宽公平性）。本轮已经修掉的 Reference 卡点：
- 地图 `current_position`；
- 投递后的过渡态等待；
- 游戏结束页的地图读取；
- 商店关闭的动词 `cancel`；
- 套件每局重启 driver。

### T4 Reference→Managed 等价套件

把 T3 的每局轨迹都在 Managed 上重放，汇总每局第一处分叉，按原因计数。先按原件定位分叉owner；T1是当前第一处分叉，不是默认Managed有错的通则。

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

### T6 原生显示覆盖与外来执行入口（未解决）

`NativeRewardCardRendererTests.SameActualNativeRewardCardRetainsTheFullReferenceDtoAfterExactParentScreenBinding`仍失败。
独审区分了两类问题：普通native卡类/战斗hook的固定名单过严；外来formatter、variable delegate和有效派生getter的patch检查仍有必要，preview transaction不覆盖其副作用。
推荐保留native展示覆盖、恢复窄来源/有效方法检查，再拆分巨型测试为正常native正例和外来执行反例；不恢复全部卡类名单，也不删除仍有效断言。
这是待实施/验证方向，不是已批准的新协议例外；若确需改变玩家信息或读取副作用保证，再给用户具体方案与影响。当前**不要自行删除断言**。

### T7 投递后等待：消费原生状态（本轮修复）

首个错误事实已由Reference ref5原件复核：投递后snapshot已经是`settling/no_action`，但广告的map Read暂不可用使basis为`partial`。因此先修Runtime消费顺序，不新增一套queue/动画推导。

- 只有原生snapshot.status=`settling`时等待；每次保留原捕获，不重发动作，不推进Agent消费。
- 现有HTTP捕获是pull接口，50ms仅限制读取频率，不是稳定条件。原操作预算和Stop中断等待、释放控制；超时不生成ready。
- 一旦离开settling，捕获仍partial立即保留delivered前缀并停止；新交互/子选择交共享语义处理，不能等待父Task或全局队列全空。
- 已删除任意partial的15秒重读宽限。单例/合成传输测试通过后，按同一原失败种子作短真实复验；完整20局不是每次改动的前置。
- 若真实复验发现新的未标记过渡，再根据该原生owner修producer，不能以泛化计时或queue-empty猜就绪。

验收分别报告源码回归、真实同场景复验和仍未覆盖机制；旧5局+修复后单跑不合并成新5/5。

## 交接格式

每个任务结束写 ≤ 1 屏：
- 改了什么、为什么；
- 跑了什么命令，结果数字（完整分母）；
- 下一个真实卡点和复现命令；
- 需要用户决定的事。

## Codex 本轮限定收口（2026-10-16，最新）

先完成Claude交付独审、T7一条修复/复验链、最小工作规则纠偏；不重开全平台重构。
调查约30分钟，实现与短复验先用60–90分钟窗口；只有具体新证据才扩测试或提预算。
T7首错已由ref5原件定位：snapshot已settling/no_action、map Read暂不可用令basis partial，Runtime投递后判断顺序错误。
修复复用原生status和现有pull接口；50ms只调度读取，预算到期为中断，不产生ready。不要再按通用queue-empty推导ready，子选择可先于父Task结束。
本轮T6保留原断言：原生常规类型名单可减，foreign formatter/delegate/effective patched getter仍有实质检查目标；先做窄修，不接受简单删断言方案。
另发现轨迹工具未比较终局完整观察/胜负；先修这一验证缺口，再开展T1/T4大规模对照。96点旧ref轨迹本身无终局。
ff6761fd CI的Linux失败是Python lint58；增量9089fb04已修lint和Managed active-entry引用，但最新Linux仍在mypy的5处失败，pytest未执行。后续不得沿用“只剩BOM”的旧诊断；并行Auto-fix更新须按实际head分开检查。
