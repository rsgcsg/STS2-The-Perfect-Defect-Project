# P1 全局 r1：可执行合同（profile `p1-global-r1`）

状态：**r1.1，P1 的唯一权威文档**（2026-10-16 起）。其他 P1 文档（[P1](P1.zh-CN.md)、[Host/wire](P1_HOST_AND_WIRE.zh-CN.md)、[计划](../plans/P1_FORWARD_2026-10-15.zh-CN.md)、[ADR-0017](../adr/0017-p1-forward-decision-profile.md)）是背景和历史；与本文冲突时以本文和代码为准。
推进规则见 [WORKING_RULES](../WORKING_RULES.zh-CN.md)；当前进度与下一步见第 16–18 节。

代码：共享核心 `components/connector/sdk/typescript/src/p1Global/`（包出口 `@rsgcsg/sts2-connector-client/p1-global`）。
产品路径：`PolicyRuntime`（agent-session-1）→ Managed 用 `ManagedServicePolicyClient.attachGlobal`，Reference 用 `ReferenceGlobalTransport`；简单外部 Agent 经 `components/policy-runtime/examples/p1/session-bridge.mjs` 接入（`sts2.p1/agent-port-1`）。
套件入口：`node tools/p1-suite.mjs`；离线检查 `sts2-p1 replay|trajectory`。第 1–13 节是协议语义（r1.0 起不变，除非第 17 节登记）；第 14–15 节是 r1.0 的历史实测。

## 1. P1 是什么、为什么要

P1 是 Agent 与《杀戮尖塔 2》之间唯一的决策协议：每个决策点给 Agent 一份完整的公开观察和一份**只含前进动作**的语义动作清单，Agent 选一个，协议把它落实成游戏里的原生操作。

要它的原因：
- 原生界面充满纯导航（打开奖励、看卡、返回、确认），让模型学这些既浪费又制造循环（历史上真实出现过"打开卡牌奖励 → 跳过 → 回到同一页"）；
- 玩家此刻能免费看到的信息分散在多个页面，模型要靠浏览和记忆才拿得全；
- 同一个 Agent 要能在真实游戏（Reference）和无界面模拟器（Managed）上不改一行代码地运行，并且两边的数据能对照、能训练。

P1 不新增游戏规则、不判断合法性：每个 P1 动作都落到游戏端当前完整动作目录里的原生动作，由游戏端在执行时复验。

## 2. 设计原则

1. **单向**：Agent 的每个动作都必须真实推动游戏前进。纯浏览、返回、取消、反选不进清单（见第 6 节排除表）；被排除的原生动作逐条写进私有记录并注明规则，其他原生动作一个都不丢。
2. **信息给全、尽量 Markov**：每份观察都附上此刻合法可看且查看无效果的全部信息——牌组、战斗中三个牌堆、遗物药水、当前页面内容；本来要打开才看得到的（例如每个卡牌奖励组的候选卡）直接放进来。P1 自己做出的不可撤回承诺（已放弃的奖励组）也公开在观察里。不宣称严格 Markov，Agent 可以保留记忆。
3. **不暴露真实的未来**：抽牌真实顺序、未揭示的奖励、随机结果、未来敌人行动永不进入观察；也不能从别名、排序或摘要里间接泄漏（第 5 节）。付款、开箱、出牌等效果先真实发生，之后才给新内容。
4. **不加提示**：没有推荐、访问计数、"你又来了"之类程序推导的信息。
5. **完整才发布**：任何必需部分取不到，这份观察就不交给 Agent（执行器先重新取，仍不行则如实删失）。

## 3. 分层与现有组件

```text
Reference：真实游戏 + STS2_PLATFORM Mod(Connector)  ┐                       ┌→ Agent（任意语言，sts2.p1/agent-port-1）
Managed：sts2.dll 无界面宿主 + 投影                 ┴→ 两端共用的 Player Environment 合同 → P1 共享核心 ┤
   （Host 负责原生事实、完整原生动作、执行复验）    snapshot-1 / Reads / bound-actions-1 / receipt-1 └→ 执行器：预算、Stop、unknown、记录
```

- 两个 Host 已用同一套 NDJSON 驱动命令（reset / observe / read / step / close）：`components/host-runtime/tools/{managed,reference}-pe-driver.mjs`。
- P1 语义（场景家族、动作表、折叠、排除、强制步骤、承诺、别名）**只实现一次**在共享核心里；执行器和离线转换器调用同一份代码。
- Host 侧为 P1 补的只读事实：奖励组的稳定公开 `group_id` 和已生成卡牌奖励的 `card_options`（两端各自从原生对象只读取得，不打开页面、不触发 Populate/Reroll/标记已见）。

## 4. 时序：Agent 什么时候看到什么

```text
游戏端报告"可交互且原生目录完整"的观察（决策边界）
  → 按该观察 ID 读取它声明的全部 Reads（同一时点；过期则重新观察再取）
  → 共享核心生成 P1 观察 + 语义清单；环境原始字节先写盘并同步
  → 声明的强制步骤直接执行（Agent 收到"仅供参考"的观察）；否则 Agent 选一个动作
  → 执行该意图：第一步原生操作 → 回执 →（如有）声明的折叠后续步骤，每步都从当前目录重新绑定
  → 等游戏推进到下一个决策边界或终局（期间的中间观察照样记录）
```

- 游戏端在 Agent 思考期间变化：提交时带期望的观察 ID，过期则为"未交付"，重新观察后 Agent 重选；绝不把旧选择套到新状态。
- 出牌后出现子选择（生成卡、从弃牌堆选牌等）：游戏端给出新的决策边界，立即交给 Agent，父动作可以仍在进行中。
- 结束回合：Agent 选择后由游戏自动推进，不反复调用 Agent，直到下一个决策边界。
- 终局：游戏端确定胜负即为终局，Agent 收到终局观察；返回菜单和清理属于任务管理。

## 5. 观察（`sts2.p1/observation-1`）

| 部分 | 内容 | 可得性 |
| --- | --- | --- |
| `envelope` | episode、occurrence、host_kind、来源观察 ID 与序号 | 仅用于绑定，不进模型（`modelView` 去掉） |
| `status` / `scene` | decision / terminal / unsupported；场景家族、原生页面类型、阶段、页面内容 | 页面内容来自游戏端当前界面 |
| `run` / `player` | 幕、层、进阶、Boss、HP、金币、遗物、药水、槽位 | 每份观察 |
| `deck` | 牌组（规范化多重集） | 游戏端声明了牌组 Read 时为 available |
| `combat` / `piles` | 战斗上下文（手牌、能量、敌人意图等）；抽牌堆为规范化多重集，弃牌/消耗堆保留原生可见顺序 | 战斗中 available（含战斗中的选牌页：Managed 把战斗 HUD 嵌在 `context.combat`，核心照样取出），否则 not_applicable |
| `map` | 当前幕地图 | 地图页 available；其他页面 r1 为 unsupported（游戏可看，Host 未取） |
| `commitments` | 本奖励页已放弃的组（只用公开别名）、是否已离开奖励页 | 每份观察 |
| `catalog` | 语义动作清单（第 6 节），按 (kind, subject, target) 规范排序 | decision 时非空 |
| `hidden_by_policy` | 游戏端声明隐藏的信息类别 | 每份观察 |

可得性取值：available、not_applicable、native_unavailable、unsupported、unknown、terminal，含义互不混用。

**别名与公平**：所有原生 ID（含奖励组 `group_id`）换成本局稳定的公开别名（c=卡、m=敌人、r=遗物、p=药水、u=界面项……），按固定遍历顺序首次出现时分配。无序多重集按公开内容排序，打平时按已分配别名排序，从不按游戏端列表位置；所以打乱抽牌堆内部顺序、互换两张同名牌，输出逐字节不变（单元测试覆盖）。

## 6. 动作清单

| 场景家族 | P1 动作 | 原生落实 |
| --- | --- | --- |
| combat | `combat.play_card(牌, 目标)`、`potion.use(药水, 目标)`、`potion.discard`、`combat.end_turn` | 一对一 |
| map | `map.travel(节点)` | 一对一 |
| reward | `reward.claim(组)`（金币/药水/遗物）、`reward.take_card(组, 卡)`、`reward.decline_group(组)`、`reward.leave`、`reward.proceed` | take_card = 打开该组 → 核对打开页确实是预览的那张卡 → 选择；decline_group 是纯 P1 承诺，不发原生输入 |
| card_reward（游戏端直接弹出时） | `card_reward.take`、`card_reward.decline` | 一对一 |
| upgrade / enchant | `upgrade.choose(卡)`、`enchant.choose(卡)` | 选卡 → 出现预览时自动确认 |
| selector（各类选牌器） | `selector.choose`、`selector.skip`、`selector.confirm` | 单选出现预览时自动确认；多选逐张追加、由 Agent 决定何时确认 |
| shop | `shop.buy`、`shop.service`、`shop.leave` | 一对一 |
| event / rest / treasure / relic_choice | `event.choose`、`rest.choose`、`rest.proceed`、`treasure.*`、`relic.choose/skip` | 一对一 |
| 其他 | 游戏端可交互但未在表内的动作 | 以 `native.<页面>.<动词>` 照样提供（mapped=false），不丢弃 |

**排除（单向规则）**：`upgrade.back`、`enchant.back`、`selector.cancel`、`selector.deselect`、`upgrade.deselect`、`enchant.deselect`、`shop.open`；终局页上的全部原生输入（只是去结算页的导航，规则 `terminal:post_result_navigation`）。有预览时不再提供 `reward.open_card_group`；只有游戏端给不出预览时才保留它。

**强制步骤**（不调用 Agent，记录 origin=forced）：地图只有一条路、事件只有一个选项、`reward.proceed`、`rest.proceed`，以及所有奖励组都已领取或放弃后的 `reward.leave`。只剩一个金币、只剩"结束回合"不算强制。

**两端呈现差异的统一**：奖励页全部领完后，Managed 给单独的"前往地图"页，Reference 停在空奖励页给"继续"——两者都读作一个强制 `reward.proceed`。`reward.leave` 之后若出现只有一个"前往地图"输入的完成页，把它作为同一意图的折叠步骤执行。继续类动作（`reward.proceed/leave`、`rest.proceed`）不带主语（原生房间引用不含决策信息）。

**奖励组承诺**：组身份来自 Host 的 `group_id`，从不用标签、位置或观察 ID 代替；Host 不提供时该页标为不完整。放弃后同一组在本奖励页内不再提供，并公开在 `commitments`；原生奖励仍在原处，离开奖励页时由游戏原生处理（P1 不伪造原生 Skip）。

## 7. 结果与失败（`sts2.p1/result-1`）

每个意图返回：completed / interrupted_known / not_started / unknown，以及每一步的原生动词、delivered / not_delivered / unknown、原因码。
- 第一步未交付：not_started，重新观察后 Agent 重选。
- 折叠后续条件不成立（例如打开的卡牌奖励不是预览那张）：interrupted_known，把新情况交回 Agent。
- 任何一步回执为 unknown 或无响应：本局立即以 unknown 删失结束，**不重发、不继续后续步骤**。
- 已完成的前缀永不改写为"未执行"。

## 8. 发生次数（occurrence）

新观察 ID 或 P1 承诺变化 → occurrence 加一；同一观察重新取回 → 不加。模型只按 occurrence 推进记忆。

## 9. Agent 端口（`sts2.p1/agent-port-1`，标准输入输出逐行 JSON）

```text
执行器 → Agent：hello{protocol, profile, revision, episode_id, host_kind}
Agent → 执行器：ready{protocol, agent_id, agent_version}
执行器 → Agent：observation{observation, requires_choice, forced_choice_id}
Agent → 执行器：choose{occurrence, choice_id} | abstain{reason}
执行器 → Agent：result{result} …… episode_end{outcome, censor_reason}
```

Agent 不需要了解 Godot、原生操作数或 Host 差异。示例：`components/policy-runtime/examples/p1/`（随机、首项、人工选择器）。

## 10. 记录与转换（`sts2.p1/episode-record-1`）

- 每局一个只追加文件，每行写入后立即 fsync，然后执行器才继续。环境行保存游戏端原样字节及 sha256，**先于** Agent 收到对应观察写入；没有 Agent 也能记录环境。
- 行类型：record_open（含 P1 核心版本与代码摘要）、env_request / env_response / env_error、p1_observation（含来源引用、被排除的原生动作）、p1_choice（含来源：agent_choice / forced）、p1_step、p1_result、agent_message_in / out、episode_end、host_close、record_close。
- 离线转换（`sts2-p1 convert`）只读记录里的环境字节，用同一共享核心重建每份观察，并与当时实际发给 Agent 的逐字节比对；同时报告记录时和当前的核心版本是否一致。
- 人工 P1 选择器的记录来源为 `human-p1-chooser`；游戏内原生物理操作由 Annotator 另行记录，二者不混同。

## 11. 两端等价怎样检查

同一种子、同一确定性 Agent，两端各跑一次，再用 `sts2-p1 compare` 比对，报告第一处差异的路径；不一致就保留双方原件，不归一掉。

- **decision 模式（默认，跨 Host 的正式口径）**：只比 Agent 真正做选择的点和终局（强制步骤是 Host 呈现，不算决策）。每个点化成与语言无关的签名：场景家族、动作清单多重集（按 动作种类 + 主语/目标的公开身份：卡/敌人/药水定义、地图坐标、奖励种类、选项序号）、所选动作、决策相关公开状态（HP、金币、遗物药水、牌组、战斗回合/能量/格挡/手牌/牌堆数/敌人血量）、承诺。
- semantic / exact 模式比整份观察（semantic 忽略显示文字）。两 Host 的引用结构本来就不同（例如 Managed 地图给"选项+节点"、Reference 只给"节点"；Reference 存档中文），所以这两种模式**不是**跨 Host 的等价口径，只用于同一 Host 的复现。
- 配对用 `examples/p1/signature_agent.mjs`：按 (种子, 自己的第几次决策) 的哈希在签名分组里选，不依赖别名、文字和强制页数量，所以两端状态一致时做同一个语义选择。

## 12. 限额

单份观察 8 MiB、清单 4096 项、一个意图最多 8 个原生步骤、连续强制步骤 64 次。超出时明确失败，不截断。

## 13. 验收标准（什么样算好）

| 层次 | 标准 |
| --- | --- |
| 协议 | 共享核心的单元测试全过：确定性、抽牌顺序与同名牌置换不变、奖励聚合与承诺、单向排除不丢其他动作、读失败不发布、两端终局格式 |
| 执行 | 执行器反例全过：unknown 与无响应都停局且不重发、未交付后重选、目录外选择被拒、Stop 先于变更、环境先于 Agent 落盘 |
| 实跑 | 两端用同一外部 Agent 跑固定套件，完整分母报告，0 unknown；每局记录都能逐字节重建 |
| 覆盖 | 每个场景家族在两端都有实际记录；没遇到的家族列为"未遇"，不算通过 |
| 等价 | 同种子同 Agent 的两端对照：报告首处差异及原因 |
| 产品 | 人工选择器能在真实游戏里经同一链路选择、执行、记录 |

## 14. 实测证据（2026-10-16，本机，r1 最终核心）

| 运行 | 宿主 | Agent | 结果 |
| --- | --- | --- | --- |
| smoke-20 r3 | Managed（候选构建 artifact `75be6efa…`，MVID `c91e08aa…`） | 外部 Python 均匀随机 | 20/20 自然终局，0 删失，0 unknown；1467 决策点（Agent 1362，强制 105，折叠 48）；20/20 记录离线重建逐字节一致、核心版本一致 |
| ref-20 r1 | Reference（真实游戏 + 开发 Mod，`--experimental-connector`） | 同上 | 20/20 自然终局，0 删失，0 unknown；1386 决策点。r1 早于最终核心，作为实跑证据，不作为最终核心的转换证据 |
| pair4 ×6 种子 | 两端 | `signature_agent` | 6/6 记录两端都逐字节重建一致；decision 模式 5/6 局整局等价（57/57、46/46、31/31、47/47、50/50 个决策点），1 局在第 19 个决策点分叉，原因见第 15 节"药水丢弃" |

记录在 `.local/p1-runs/`（本地，不入库）；交付报告列出路径和 sha256。

**支持矩阵**（"实录"= 在上述真实运行里出现并执行成功）

| 场景家族 | Managed | Reference |
| --- | --- | --- |
| combat（出牌、药水、结束回合） | 实录 | 实录 |
| map | 实录 | 实录 |
| reward（领取、选卡、放弃组、离开/继续） | 实录 | 实录 |
| card_reward（游戏端直接弹出的卡牌奖励） | 未遇 | 未遇 |
| upgrade | 实录 | 实录 |
| enchant | 实录（本轮新增 Host 支持） | 实录 |
| selector | 实录 | 实录 |
| shop | 实录 | 实录 |
| event | 实录 | 实录 |
| rest | 实录 | 实录 |
| treasure / relic_choice | 未遇 | 未遇 |
| 第二幕及以后 | 未遇（随机 Agent 最高到第 9 层） | 未遇 |
| terminal（失败） | 实录 | 实录 |
| terminal（胜利） | 未遇 | 未遇 |

## 15. 已知缺口与不声明的事

- **Reference 战斗中不提供"丢弃药水"**：真实游戏可以在战斗中丢弃药水，Managed 提供、Reference 没有。是 Reference Connector 的缺口，造成 pair4 唯一一局分叉。
- **Managed 战斗中选牌页（如全息影像从弃牌堆选牌）**：HUD 不含手牌，也不提供牌堆 Read；Reference 都有。
- **显示文字两端不同**：Managed 卡牌/事件描述是模板加数值（如 `Gain {MaxHp} Max HP.` + variables），Reference 是解析后的中文；Managed 事件选项不带卡牌预览提示；Managed 附魔描述带一个未解析的图标路径。decision 口径不受影响，但模型看到的文字不同。
- **Managed 多选牌组选择会发出 `preview` 动词**，而 snapshot-1 合同没有这个动词（严格解码会拒绝）。P1 运行里没遇到；是 Managed 投影已有的潜在缺陷。
- **地图**：只有地图页给完整地图，其他页面为 unsupported。
- **记录器**：现在是协议来源记录（P1 选择器/外部 Agent 的意图与环境字节）。游戏内原生物理操作的见证（Annotator V4）不在本轮范围。
- **人工选择器**未在真实游戏里由真人跑过；执行链路与外部 Agent 相同。
- **执行路径**：`sts2-p1 run` 直接用 Host 驱动协议，不经过 Policy Runtime 的完整产品路径（workbench/队列）；Codex 的 `p1-ordinary-combat-r1` 是另一个 profile，二者关系待整合。
- 不声明：胜率或策略强度、第二幕以后的覆盖、胜利终局、Reference 发布级资格、Managed 规则等价资格、训练数据可用性。

## 16. r1.1 产品路径实测（2026-10-16，本机，source + runtime，非 hosted CI）

全部经 Policy Runtime 产品路径。外部 Agent 是 `examples/p1/random_agent.py`，经 `session-bridge.mjs` 接入；入口 `tools/p1-suite.mjs`。

| 运行 | 宿主 | 结果 |
| --- | --- | --- |
| suite-b，10 种子 | Managed（Ironclad，en） | 9/10 自然终局，0 unknown，10/10 回放校验通过 |
| census40，40 种子（统计 38 局） | Managed（Defect，zh） | 32/38 自然终局，0 unknown，2519 个决策；6 局都卡在选卡页元数据（续作 T2）；另 2 局因本机磁盘满中断，不计入 |
| ref5，test-1000 前 5 种子 | Reference（真实游戏 + 开发 Mod，Defect，zh） | 4/5 自然终局，343 个决策，5/5 回放校验通过；1 局在第 44 步出现 unknown（见下），已修复，修复后待重跑确认 |
| Managed→Managed 重放 | Managed | 70/70 匹配，终局相同 |
| Reference→Managed 重放，96 步轨迹 | Reference 原件在 Managed 上重放 | 第 0 步分叉：Managed 缺牌组卡详情（续作 T1） |

本轮为让真实宿主跑通而修的问题（每个都来自真实运行）：
- Managed 战斗 HUD 白名单过严，原来 1 步就停；
- Managed 重复申请控制权；
- 选卡页缺 `selectable_card_entity_ids`；
- 富文本图标被拒；
- 牌组卡详情读取目标不是当前实体；
- 地图 `current_position` 为 null 时被省略；
- 投递后过渡态没有等待；
- 游戏结束页仍声明地图读取；
- Reference 商店关闭动作的动词是 `cancel`；
- 请求层"未交付"回执不带 subject，被误判为 unknown；
- 套件在一个 driver 上连续 reset。

## 17. 登记的协议例外（必须经用户审核才能新增）

| 编号 | 例外 | 原因 | 批准 |
| --- | --- | --- | --- |
| E1 | P1 不提供原生"打开卡牌奖励后跳过，从而冻结候选、不受之后遗物升级影响"的输入。奖励页按普通方式发布。 | 这是导航技巧，不是新决策；提供它会把浏览重新带回单向协议。 | 用户，2026-10-16 |

另有两条用户确认的规则：
- Reference 是真相，Managed 跟随；等价性通过在 Managed 上重放 Reference 原件来检验，分叉默认是 Managed 缺陷。
- Managed 只需在 P1 语境里绝大多数情况与原版一致，功能齐全、逻辑相同；不要求逐字节相同。

## 18. 当前已知缺口（r1.1）

按续作计划 T1–T6 处理：
- T1：Managed 缺牌组卡详情和空字段形状，是等价的第一分叉。
- T2：Managed 选卡页元数据不完整。
- T3：Reference 20 局套件还没跑。
- T4：Reference→Managed 等价套件还没跑。
- T5：真人 Source4 短录制还没做。
- T6：卡牌渲染白名单测试，待用户决定。
- 第 15 节的 r1.0 缺口仍然有效，除非已在第 16 节列为已修。
- 不声明：胜率或策略强度、第二幕以后的覆盖、胜利终局、发布级资格、训练数据可用性。
