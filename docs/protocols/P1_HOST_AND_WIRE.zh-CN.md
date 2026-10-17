# P1 r1：Host 对接、传输与共同记录

> 背景/历史文档。P1 的唯一权威是 [P1 全局 r1](P1_GLOBAL_R1.zh-CN.md)（r1.1）；冲突时以它和代码为准。

状态：Proposed，落实 [P1](P1.zh-CN.md) 与 [ADR-0017](../adr/0017-p1-forward-decision-profile.md)。字段名是本修订候选，未注册为可用生产 schema。

## 1. 只保留现有分层

Agent → AgentProgramPort/Runtime → 声明的 P1 映射 → Connector/Host → STS2。
Reference 与 Managed 各实现 P1-H；共享协议/比较器不代替原生资格。Runtime 沿用唯一预算/Stop/unknown/原件 owner。模型/研究不持 native operand。
P1 映射可以在现有组件内是库/模块，不要求新进程、服务器、数据库或任务管理器。

## 2. P1-A 公共结构

所有消息带 schema、revision；精确 digest/owner 归控制信封，不混进模型文本。

| 对象 | 必需结构 | 约束 |
| --- | --- | --- |
| Capabilities | profile/revision、scene capabilities、capture bounds、automatic-step policy、supported feedback/recovery | 缺能力在准入时明确；不默退旧 profile |
| Observation | episode/occurrence/capture envelope；当前 public state/entities/relations、protocol commitments、availability、完整 semantic catalog | public state 含真正影响 P1 后续机会的承诺；不含 seed/private ref/raw hash |
| Entity | occurrence内公有 ID、类型、公开属性、display context、关系；跨时同一性按声明 owner 证明 | 重复卡保独立实例和重数；opaque 重命名不是合并 |
| Action | opaque action ID、语义类型、subject/target/choice refs、公开参数/约束、binding envelope | 只来自当前完整目录；底层 native sequence/handle 不交模型构造 |
| Submit | request ID、expected occurrence/capture/catalog digest、action ID、外部预算/控制身份 | 同 ID 不同内容拒绝；不接受任意方法/坐标/native index |
| Result | operation ID、状态、已完成前缀、是否有 native 尝试、逐步 Receipt refs、commitment delta、next observation/await/terminal、failure | local commitment/native delivery/后继分别表达；unknown fence 整操作 |
| Capture chunk | capture ID、content digest、固定总bytes/coverage、cursor、bytes、end marker | 全部从冻结内容；过期/缺块/超限明确失败 |
| Recorded trace | source identity、capture、semantic request、lowering steps、native receipts、public projection、provided/consumed refs、gap/termination | 原件 immutable；投影另带版本/parents |

Availability 枚举：available、native_unavailable、not_applicable、unrevealed、unsupported、unknown、settling、terminal。required 为 unknown/unsupported/inconsistent/too_large 时不发布成功完整观察；正常不可得只按场景已声明的规则处理。empty 与 unavailable 不同。

## 3. P1-H 必需接口与责任

| 接缝 | 输入/输出语义 | Reference | Managed |
| --- | --- | --- | --- |
| describe/admit | exact profile、游戏/Host兼容、支持矩阵/限额 | 当前安装/加载和 Connector capabilities | candidate/patch/TestMode/shim范围 |
| capture | 取得当前输入资格、required公有事实、P1 commitments、语义目录并封存 | native main-thread/typed只读来源；全部详情实例 | 原生游戏模型对应公开投影；隐藏数据留private |
| resolve_intent | P1 action → 有限、已声明的执行计划/预期owner变化 | 完整native关系内精确绑定 | exact原生callback/目标/scope；不重写规则 |
| advance_intent | 复验后执行唯一下一子步，原Receipt和新公开事实返回 | 原Connector交付owner与异步子scope | 原session/decision/binding及原生effects |
| protocol_commit | 更新已授权非native承诺并记录native处置是否pending | 当前profile/session持有 | 同一语义状态机，不自行另解释 |
| stop/close | 外部预算与Stop触发、挂起也清理；返回真实状态 | Runtime/Host既有释放和episode finish | driver/session/process原owner |
| recover | 同原operation/episode/原件证明下继续或停止 | 不从UI/body/current-root猜关联 | 不因same seed重开当同episode |

Capture不授权行动；resolve不等delivery；advance不保证后继已就绪。每步实际参数仍由 owning native映射产生。P1-H可以适配现REST/NDJSON/in-process，不要求新网络协议。

## 4. 自动执行状态机

accepted → resolved → executing(substep i) → revalidated(next) → completed / decision_ready。
可在任一步到 cancelled_before_delivery、interrupted_known、unknown、expired 或 cleanup_unknown；这些状态不因后来页面相同而合并。
先记录操作意图与已执行前缀，且在原delivery/写入结果可确认时才推进。unknown 不执行下一个子步，不重发原步骤。已知拒绝是否可重新绑定由具体cause合同决定。
自动步数与总时间上限来自本次受理能力/外部预算；两者均需满足。每步都会重新检查控制/期望owner/当前完整目录；不得以后台宏绕过 Runtime 到期。
除已选意图桥步外，profile 可声明强制唯一输入；必须共同证明完整 P1 机会、原生 mandatory 约束且无取消/退出/时机取舍。auto-policy 身份、来源、观察/Receipt 单独记录，不能把数组长度 1 当通用规则。
纯导航关闭和重新打开并非自由权限；只在当前P1 intention且无剩余策略选择的声明映射中发生。若效果产生可选择的新信息，则结束自动链并发新的决策。

## 5. 传输、缓存与成本

默认消息采用现有 UTF-8 JSON/NDJSON 边界，字段schema版本化；大包复用现有sealed store/chunk机制。二进制或紧凑编码只能作保语义运输优化，不成为不同P1。
一次capture固定membership、动态值/描述context、完整目录与commitments。静态正文可按明确游戏/locale/授权定义缓存；不能用live formatter把新动态值写进旧capture。
小包可inline，大包同版本流式/分块。读端验证完整性/覆盖和end；所有required完成才宣称full offer。capture存储期限不延长动作合法期。
至少测200/500对象常规目标和10000对象压力例；这些是工程输入规模，不声称已在原生遇到。测capture、序列化、bytes、传输、校验、模型耗时与内存；不得只优化JSON后宣称模型能读完。
限额以机器能力描述给出并固定进证据。首版具体值由测量与独审决定；不能无限增长、静默截断或长期以“以后再定”冒充运行准入。

## 6. 数据与比较使用同一捕获

只新增必要P1 capture/semantic-operation投影，复用现Store/Evidence/source lineage。环境原件可早于校验保存；失败保留。Human source、机械测试source、Agent source显式区分。
Agent offer只证明提供；consume是声明；W变化需可选数值诊断。旧 Human 数据没有捕获的详情不能补造；自动步骤不生成新的模仿标签。
比较器检查公开信息、完整目录、同一/不同实体关系、可见顺序、commitments、阶段、结果。归一化只允许保持关系的一一ID重命名及明确非语义字段；不能以相同标签合并对象。
Native微步骤数、P1语义决策数、chooser调用数、Model consume/advance数独立。压缩UI步骤后的吞吐和旧profile调用数不能直接比较为模型提速。

## 7. 首批闭合路径与失败反例

普通战斗capture → 选择一个已知card/target意图 → native交付 → 真实子selector → selector决策 → 父后继 → 记录重读 → 同动作两Host对照。
奖励另验显式多个独立组、linked互斥、decline-group、整set退出、领取遗物修改剩余候选与拒绝ledger恢复。
必须覆盖 `DisallowSkipping`/强制领取：局部可返回不等集合可放弃，不能全declined后死锁或代选完成。
每条必须包含stale between steps、partial delivered/unknown、同包重传、真实重访、重复实体、>8详情、required缺失、Stop/期限和记录写失败。合成通过后再exact native与运行验证。
R0产品suite复用Workbench/Runtime的owned episode，Reference bootstrap移交后不再直接step。策略套件与相同动作对照是两个任务模式；共享Agent/Host/结果定义，不强制共用一个物理循环。

## 当前公开事实包与协议适配

新增 `public-basis-1` 是 Connector 当前 Snapshot1 与全部广告 Read 实例的同边界封存格式，
不包含 P1 策略、承诺或 Agent 状态。`capture_status=complete` 只说明原生公开目录的取数完整；
P1 另行验证场景必需资料、完整语义目录和粒度映射，不能用这一标记替代 P1 完整性。
缺失 Read 保留原 error/partial；不按 kind 覆盖实例，不补抓另一时刻的数据。

该格式复用 SealedObservationStore 的容量、TTL、哈希、连续分块和释放，独立声明
`/api/player-environment/public-basis/{capabilities,current,read,release}`；旧 C1 及其归档原义保留。
SDK `getFullPublicBasis` 验证原始字节和 Snapshot/Read/session 绑定，清理失败显式报错。
Reference 当前捕获使用 `input_profile="public-basis-1"`，提交及结果查询须携带相同 profile。
该投影保留 Snapshot1 的内容结构，额外标记 input_profile；公开事实包 revision 为 2，SDK 仍可读 revision 1 原件。
捕获、执行前重建及 receipt successor 均使用 aggregate public Reads/详情和独立 view identity；
夹入未变化的 legacy 观察不改变该身份，实际 native 变化或当前绑定变化仍使旧 token 过期。
旧 null/C1、reward 与 text-menu 的投影、输出和身份算法保持原义；不自动升级历史 token。
调用方可以传外部 AbortSignal、发出前的异步准入与原始响应字节回调；控制、预算和操作账本仍归 Runtime。
清理使用独立有界信号，未知操作结果不因存在捕获/释放接口而允许重投。

在线映射与离线转换使用同一份已验证原件；独立原生 Recorder 应在原有 prefix/publication
冻结中复用同次构造的公开事实，不能连续调用两个 Current 再声称同边界。
本增量先提供格式/封存/SDK接点；地图、完整显示闭包、原生 Source Recorder 与 Managed 接入的
实际实现和资格仍按活动计划逐项验收，不能从此接口存在推出全局完成。
