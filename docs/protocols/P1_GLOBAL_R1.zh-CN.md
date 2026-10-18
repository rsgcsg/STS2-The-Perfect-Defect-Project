# P1 全局 r1：可执行合同（profile `p1-global-r1`）

状态：**r1.3，P1 的唯一权威文档**（2026-10-18 用户决定；实现和实跑资格见 CURRENT）。其他 P1 文档（[P1](P1.zh-CN.md)、[Host/wire](P1_HOST_AND_WIRE.zh-CN.md)、[计划](../plans/P1_FORWARD_2026-10-15.zh-CN.md)、[ADR-0017](../adr/0017-p1-forward-decision-profile.md)）是背景和历史；本文定义P1语义；用户最新要求及组件权责仍优先，代码是待检验实现，不以当前代码覆盖需求。
推进规则见 [WORKING_RULES](../WORKING_RULES.zh-CN.md)；当前进度与下一步见第 16–18 节。

代码：共享核心 `components/connector/sdk/typescript/src/p1Global/`（包出口 `@rsgcsg/sts2-connector-client/p1-global`）。
产品路径：`PolicyRuntime`（agent-session-1）→ Managed 用 `ManagedServicePolicyClient.attachGlobal`，Reference 用 `ReferenceGlobalTransport`；简单外部 Agent 经 `components/policy-runtime/examples/p1/session-bridge.mjs` 接入（`sts2.p1/agent-port-1`）。
套件入口：`node tools/p1-suite.mjs`；离线检查 `sts2-p1 replay|trajectory`。第 1–13 节定义协议语义与当前实现边界；本轮审计纠正其中与共享核心不符的旧描述，语义例外仍须第17节登记；第 14–15 节是 r1.0 的历史实测。

## 1. P1 是什么、为什么要

P1 是本轮为 Agent 与《杀戮尖塔 2》选定的决策协议：每个决策点给 Agent 一份完整的公开观察和一份**只含前进动作**的语义动作清单，Agent 选一个，协议把它落实成游戏里的原生操作。

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
5. **完整才发布**：任何必需部分取不到，这份观察就不交给 Agent。原生明确报告 `settling` 时等待；离开该状态后仍缺必需信息即如实停止，不对任意 `partial` 施加重读宽限。读取轮询间隔仅为现有pull接口调度，不能证明就绪；原预算/Stop限制等待。

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
| `map` | 当前幕地图 | 当前 profile 每个非终局决策点都必需；取不到就不发布完整观察 |
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
| reward | `reward.claim(组)`（金币/药水/遗物）、`reward.take_card(组, 卡)`、`reward.decline_group(组)`、`reward.leave`、`reward.proceed` | take_card = 打开该组 → 核对打开页确实是预览的那张卡 → 选择；decline_group = 打开该组 → 合格原生 Skip → P1 关闭该组；不能把它误述为纯本地承诺 |
| card_reward（游戏端直接弹出时） | `card_reward.take`、`card_reward.decline` | 一对一 |
| upgrade / enchant | `upgrade.choose(卡)`、`enchant.choose(卡)`，`upgrade.finish` / `enchant.finish` | 逐张不可撤回追加；已选意图的合格预览自动确认；结束绑定当前原生可提交输入 |
| selector（各类选牌器） | `selector.choose(卡)`、`selector.finish` | 逐张追加或结束；不返回、不撤选。空选结束是整页拒绝；选满只留结束，原生自动完成或合格确认可折叠 |
| shop | `shop.open`、`shop.buy`、`shop.service`、`shop.leave` | 两 Host 一致的单向进入库存、购买/服务；leave = 关闭库存 → 同一商店原生 proceed，不能回到可 reopen 的 Agent 决策；open 是正常前进步骤，不是例外 |
| event / rest / treasure / relic_choice | `event.choose`、`rest.choose`、`rest.proceed`、`treasure.*`、`relic.choose/skip` | 一对一 |
| 其他 | 游戏端可交互但未在表内的动作 | 以 `native.<页面>.<动词>` 照样提供（mapped=false），不丢弃 |

**排除（单向规则）**：选牌里的返回、取消预览、撤选不进入清单；已有选择不能用“取消整页”清空。原生合法的空集提交或整页拒绝输入在空选时统一成为 `finish`，不丢掉拒绝机会。观察的 `surface.selection_progress` 给出已选公开牌、剩余名额、还需满足的最低数量及当前是否能结束；本 profile 不制造原生未提供的提交。选满后排除追加，保留完成输入；用户允许唯一结束自动执行，本实现仍可把该唯一输入交给 Agent。纯浏览仅在完整公开信息已汇总后排除，不能通过删信息换取单向。

**变换**：选择原牌就是决定，不提供返回。滚动展示不进 P1 观察，不能把某个动画帧说成承诺结果；确认能按同一 owner/request 和既选牌资格折叠即折叠，否则只留结束/确认单一选项。确定替换或候选集合必须有真实游戏 producer、公开时点和代码出处后再分型；未提供实例的分支按原生提交后揭示随机结果处理，不预演 delegate/RNG。此要求已由用户决定，当前实现及覆盖仍须验收。

**奖励组**：普通独立完整组继续 `take_card` / `decline_group` 聚合。关联/强制组先列真实 producer 及 Defect/A0/第一幕可达性；范围内未遇不登记例外，不以没有实跑就声称不存在。确实可达的不同语义先报用户决定。本次未批准把 open/return 整体登记为例外。

终局页全部原生输入只去结算页，继续按 `terminal:post_result_navigation` 排除。

**强制步骤的当前边界**：共享核心目前返回 `forced_choice_id=null`，并未启用上述chooser旁路。一个按钮或一个动作不足以证明任意机制强制；用户已允许选满后的唯一结束自动执行，本实现可保留成唯一选择。其他机制若启用，必须逐机制证明没有其他有意义机会、保留context与origin，并按版本记录。已选意图内部的合格机械fold是另一回事。

**两端呈现差异的统一**：奖励页全部领完后，Managed 给单独的"前往地图"页，Reference 停在空奖励页给"继续"——两者都映射为 `reward.proceed`；当前仍由chooser选择，不因单例自行执行。`reward.leave` 之后若出现只有一个"前往地图"输入的完成页，把它作为同一意图的折叠步骤执行。继续类动作（`reward.proceed/leave`、`rest.proceed`）不带主语（原生房间引用不含决策信息）。

**奖励组承诺**：组身份来自 Host 的 `group_id`，从不用标签、位置或观察 ID 代替；Host 不提供时该页标为不完整。放弃后同一组在本奖励页内不再提供，并公开在 `commitments`；P1提交关闭承诺前已执行合格原生Skip并核后件；原生组的最终处置仍由原owner退出处理，不用本地ledger伪造OnSkipped。

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

终局同样先发送完整 `observation{requires_choice:false}`，再发送 `episode_end`；不能仅凭结束通知或空choice判定跨Host终局相同。对照须已匹配前缀、Reference最后一点为明确终局、无删失，并比较双方完整公共终局（包含win/loss）。桥接器在返回Runtime close前让子Agent处理完终局并退出，异常退出不伪称完整交付；原Runtime预算仍约束等待。

合格 selector producer 汇总原牌及完整升级/附魔显示后，纯查看和切换预览从 P1 清单排除，原 BoundAction 仍留在环境字节和排除记录。没有相应事实的页面不能据此宣称完成信息聚合。

当前 `session-bridge.mjs` 给子进程转发 hello / observation / episode_end，不转发 result；真正 Receipt/结果保存在 Runtime 原始记录，不能靠子 Agent 从未收到的 result 宣称验收。

Agent 不需要了解 Godot、原生操作数或 Host 差异。示例：`components/policy-runtime/examples/p1/`（随机、首项、人工选择器）。

## 10. 记录与转换（`sts2.p1/episode-record-1`）

- 每局一个只追加文件，每行写入后立即 fsync，然后执行器才继续。环境行保存游戏端原样字节及 sha256，**先于** Agent 收到对应观察写入；没有 Agent 也能记录环境。
- 行类型：record_open（含 P1 核心版本与代码摘要）、env_request / env_response / env_error、p1_observation（含来源引用、被排除的原生动作）、p1_choice（含来源：agent_choice / forced）、p1_step、p1_result、agent_message_in / out、episode_end、host_close、record_close。
- 离线转换（`sts2-p1 convert`）只读记录里的环境字节，用同一共享核心重建每份观察，并与当时实际发给 Agent 的逐字节比对；同时报告记录时和当前的核心版本是否一致。
- 人工 P1 选择器的记录来源为 `human-p1-chooser`；游戏内原生物理操作由 Annotator 另行记录，二者不混同。

## 11. 两端等价怎样检查

本轮按用户批准的 r1 支持范围书 v0.2：固定 test-1000 前20种子、角色/难度/语言和候选 tuple，保留 Reference 真实公开选择，再在 Managed 按完整公开身份/关系重放。不能用两个 Host 各自随机跑到死亡代替配对重放。

- 正式比较完整 P1 公开观察、目录、实际选择和不可撤回承诺。一个全局注入双射约束全部卡、重复实例、owner、目录及被选动作；只有合同声明的多重集可换序。
- 文字逐字段比较，不全局删名称或描述；同候选下编码/运输差异先按原始反例确定适配 owner。当前卡牌详情 Read 库存无时间顺序，可作为多重集比较，但每张牌、显示上下文、owner 关联及重复次数必须保留。
- 所有真正选择点及完整公开终局（包括 win/loss）均须一致，Reference 必须明确自然终局且无删失。unknown、预算停止、基础设施中断、partial 卡点单独计数。
- 比较器资源上限返回 incomparable，不能算 equal；改变 loss→win、丢失重复实例、错绑关系或文本不相等必须拒绝 equal。
- 批准的差异也不算一致，只缩小声明；未实现/未验证机制继续保留全局义务。旧 r1.0 的压缩 decision-signature 统计是历史结果，不代替本标准。r1.2 新语义/观察绑定不能重贴到旧 r1.1 原件上。

### r1.3 修订登记（2026-10-18，用户批准）

只在 `combat_turn` 的 `sts2.player-environment/surface/combat_turn-1` 中，
`scene.content.surface.playable_cards[].target_entity_ids` 与
`scene.content.surface.usable_potions[].target_entity_ids` 是**完整、无重复的无序合法目标集合**。
顺序没有决策意义，Agent 不得依赖其顺序。比较时保留全部成员、所属卡牌/药水与目标实例的关联，
并纳入同一个全局实体双射；缺失、增加、重复或错绑成员均不等价。

此修订源自 Human01 第19个输入：Reference 按每进程随机实体ID排序合法目标，Managed按公开敌人顺序投影。
批准仅覆盖上述两个路径。敌人/手牌顺序、其他ID列表、目录关联、文字、extra_reads、
未知扩展及其他所有字段沿用既有严格语义。以后如发现其他字段随机ID排序，须逐项报告，不沿用本批准。
Human选择器可将同一卡牌/药水的目标选项按公开敌人从左到右显示，保持原choice_id和编号绑定。

r1.2 原始运行、帧和既有结论保留。用 r1.3 回放旧轨迹时必须显式生成独立衍生轨迹并记录原件hash、
来源帧hash和逐点前后hash；唯一允许修改的是观察的 `revision`。数组和事实不重排、不删除。
该衍生件只用于在新语义下重放，不表示真人原来消费了r1.3，也不转移原生/Human/训练资格。
直接混比不同revision仍返回incomparable。

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

以下是旧 r1.1 的历史结果，不给 r1.2 重新贴资格标签。全部经 Policy Runtime 产品路径。外部 Agent 是 `examples/p1/random_agent.py`，经 `session-bridge.mjs` 接入；入口 `tools/p1-suite.mjs`。

| 运行 | 宿主 | 结果 |
| --- | --- | --- |
| suite-b，10 种子 | Managed（Ironclad，en） | 9/10 自然终局，0 unknown，10/10 回放校验通过 |
| census40，40计划/40尝试 | Managed（Defect，zh） | 32失败终局、6选卡卡点、2磁盘中断；有效38局2428决策，全部40尝试2519决策；跨两种core，属于探索记录，不是同候选通过率 |
| ref5，test-1000 前 5 种子 | Reference（真实游戏 + 开发 Mod，Defect，zh） | 4/5 自然终局，343 个决策，5/5 回放校验通过；第 4 局在第 44 步出现 unknown（请求层“未交付”回执不带 subject，见下）。修复后另跑该种子：失败终局，57 个决策，0 unknown；原report回放检查失败，后续独立按当前读者验证通过；不拼作原套件5/5 |
| Managed→Managed 重放 | Managed | 70/70 匹配，终局相同 |
| Reference→Managed 重放，96个决策点（无终局，旧ref-probe4） | Reference 原件在 Managed 上重放 | 第 0 步分叉：Managed 缺牌组卡详情（续作 T1） |

本轮为让真实宿主跑通而修的问题（每个都来自真实运行）：
- Managed 战斗 HUD 白名单过严，原来 1 步就停；
- Managed 重复申请控制权；
- 选卡页缺 `selectable_card_entity_ids`；
- 富文本图标被拒；
- 牌组卡详情读取目标不是当前实体；
- 地图 `current_position` 为 null 时被省略；
- 投递后过渡态：旧Runtime对任意partial设15秒重读窗口；T7源码修复先处理原生settling，再判断完整性，不重发。原Reference记录已经给出settling，不能把问题一概归为Connector未标记；当前真实复验结果另列。
- 游戏结束页仍声明地图读取；
- Reference 商店关闭动作的动词是 `cancel`；
- 请求层"未交付"回执不带 subject，被误判为 unknown；
- 套件在一个 driver 上连续 reset。

## 17. 登记的协议例外（必须经用户审核才能新增）

| 编号 | 例外 | 原因 | 批准 |
| --- | --- | --- | --- |
| E1 | P1 不提供原生"打开卡牌奖励后跳过，从而冻结候选、不受之后遗物升级影响"的输入。奖励页按普通方式发布。 | 这是导航技巧，不是新决策；提供它会把浏览重新带回单向协议。 | 用户，2026-10-16 |

对照范围与判据：
- STS2原生事实是真相，Reference是首选对照但其投影也需审查；在Managed重放Reference原件并比较决策前缀和终局，按原件定位分叉owner。
- Managed 只需在 P1 语境里绝大多数情况与原版一致，功能齐全、逻辑相同；不要求逐字节相同。

## 18. 当前已知缺口（r1.1）

按续作计划处理：
- T1：Managed 缺牌组卡详情和空字段形状，是等价的第一分叉。
- T2：Managed 选卡页元数据不完整。
- T3：Reference 20 局套件还没跑。
- T4：Reference→Managed 等价套件还没跑。
- T5：真人 Source4 短录制还没做。
- T6：卡牌渲染的native覆盖与外来执行检查需窄修；不能简单删断言或恢复全部名单。
- T7：Runtime已按原生settling等待，短真实回归已跨过原过渡；新暴露升级activate映射缺口，尚未全机制验收。
- 历史第15节只保留当时结果；当前覆盖以同候选原件和逐项检查为准，不能从旧摘要自动继承支持或缺口。
- 不声明：胜率或策略强度、第二幕以后的覆盖、胜利终局、发布级资格、训练数据可用性。
