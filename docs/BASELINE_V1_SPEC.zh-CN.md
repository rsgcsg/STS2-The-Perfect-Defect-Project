# 当前基线实施规范 v1：一份合同、一个真实小闭环

状态：G1-v1已接受用于限定S0实施。日期：2026-10-08。[独立审查与负责人接受记录](evidence/G1_V1_ACCEPTANCE_2026-10-08.md)固定范围及非声明。本文是本轮唯一实施规范；扩大profile/运行/研究资格仍需后续证据。用户已委托项目负责人自主收敛设计并继续实现、验证、集成与必要运行，替代此前“仅设计/等待用户逐项阅读批准”的推进限制。模型/训练相关付费累计上限20美元；优先本机CPU、预计付费0。

**给新工程师：**先读本文件，再读[当前执行包](plans/BASELINE_V1_EXECUTION_2026-10-08.md)、CURRENT和你的组件指南。旧`docs/design/BASELINE_*`是历史讨论/例子/原证据，不再以“所有不冲突部分”拼接成规范。需要改变本规范就记录原因、受影响合同/数据/测试与新版本，不能静默另写一份优先级更高的补充。

## 1. 需求与阶段目标

总目标是公平、可改、可扩展、可测的STS2 Agent系统：全基础角色A0–A10是设计覆盖范围；Defect A0先验证。当前显式输入I/F关闭；结构M2进入第一批实现。教程由隔离实例准备关闭，不作为默认学习重点。不得读取隐藏规则状态作策略输入、替代原生合法性、重试未知投递或伪造Human来源。

第一条真实闭环选择**S0兼容实例**：使用现存`text-menu-v2`公开结构/当前cursor动作关系，添加不可变读取胶囊（capsule）和共享取得库；实现小型结构M2，采真实原生Agent示范→限定N训练→导出→加载到原生游戏→真实动作与确认停止。S0清楚保留text-v2的虚拟选牌/目标分步语义，**不称它已实现最终native-flat L、完整瞬态捕获或所有角色资格**。这个实例用于及早证伪上层抽象；目标协议与全场景演进仍在同一能力体系中。

最小闭环不要求先改造通用云调度、所有历史PR、完整GUI或训练所有模型。每个新增共享抽象需由S0和另一个真实消费者/忠实反例约束；不为想象中的未来先建平台。G2分清source-kind：Agent来源可完成学习工程闭环，Human准入/连续示范是独立尚待满足项，不能因机器操作游戏而声称新Human证据。用户进一步授权AI代执行先前列为真人/用户的操作；这些可形成原生UI/产品功能证据，并注明AI操作者，不因步骤名字要求Human就阻塞自动验证。只有真正的人类来源主张仍需真人事实。

## 2. 抽象层、实现位置与唯一事实owner

| 层 | 它定义什么 | 初始实现/连接 | 不负责什么 |
| --- | --- | --- | --- |
| 世界 | 规则、RNG、原生输入与效果 | 精确STS2构建 | 数据目标、研究奖励 |
| Host能力合同H | 公平可捕获事实、精确输入、时钟/生命周期/质量 | Native Foundation、Connector、Host Runtime；沿现有typed seam | 自创游戏合法性或模型策略 |
| 协议核心P | 公开scope、版本、完整性、读取/动作/结果与失败含义 | Connector拥有capture/capsule及原生authority；控制保留既有Runtime | 默认替Agent选策略或重开 |
| Agent面对的接口A | ReadCurrent/ReadSealed/GetFull、C与提交/控制合同 | 共享SDK＋现有版本化text-v2 submit | 不等同模型输入张量 |
| 完整Agent | 输入消费、W、时机/选择、程序依赖与运行状态 | SDK＋Policy Runtime＋可信模型adapter形成一个版本化组合 | 不能按名称绕过任务权限 |
| Model | 明确计算图`(P/E,W,C)->(W_next,scores)` | 首先Structured S-M2-0 | 网络/存档/权限、直接native操作 |
| 数据/实验/产品 | source/use/split、训练与评价、作业/资源/分发/界面 | 复用`python/spireagent`基础设施与`python/stpd`研究实现 | 不签发native事实或Human来源 |

H/P/A是逻辑合同，不强制三个进程。BND-2现作为实施默认：共享库负责机械正确性，AgentSpec决定消费/时机/选择；任务Runner可持有显式实例管理权限，普通局内模型没有。当前目录名称是复用映射，重构必须有因果收益与迁移/回退，不为改名返工。原始schema/producer/evidence身份不重写。

## 3. 选定的信息与操作合同

当前已进入逻辑页的公开结构、已揭示显示、公开对象/关系以及完整**当前profile动作关系**可取得。未进入页面不默认预取、隐藏抽牌顺序不公开。I/F-off不删除当前HP、focus、selected、当前正文/summary等真实观察。opaque action/ref/session/run/request ID仅用于程序绑定，不作可学习身份特征。

S0的动作完整性是`text-menu-v2`当前cursor全部choices；不同cursor不是一个扁平目录。card/target selection是协议虚拟选择，native输入只在对应确认派发。初始内核继续现有Controller、request fingerprint、stale与native execute-time复验；capsule ID不产生动作权威。目标native-flat profile日后独立版本实现，不能把S0名字换掉就继承资格。

完整范围按既有L01–64机制族、SX01–21和SC01–16固定登记：信息/预览、战斗/目标/药水、selector/父child、奖励/linked/reroll、地图、事件、商店、营火/宝箱、跨幕、终局、管理/数据/产品。教程L61为管理/专门测试条件项。适用规则是当前构建/角色/阶层可达的机制；新发现未映射机制新增明确unsupported格并扩展，不删分母、不声称穷举所有卡牌组合。source/test/build/runtime/Human/学习各列分开填写。

## 4. 三种身份与S0消费规则

必须区分source通知、成功capture、实际模型consume。

- **source notice**只声明某处可能有新资料/变化；不承诺已封存历史大payload。
- **capture ID**标识一次成功冻结的公开Snapshot字节及actual capture时点/profile/source snapshot；失效/失败不发布成功句柄。
- **consume ordinal**属于Agent run/segment；只在一个完整且合格的新观察成功交给模型时前进。传输重传、重复读取同capture、receipt/control事件不前进。

S0采用**请求取得观察**，而非完整原生事件流：ReadCurrent取得当前公开页面；库收齐同capture的状态/C后才交模型。以白名单投影后的公开Model-state digest比较最近已消费观察；排除C/opaque身份/receipt/control。公共state未变时不重复W写入，即使capture/snapshot/control身份变化；C变了仍可用同W重新评分，动作绑定按最新实际basis。页A→B→A已实际取得时state序列确实变化，仍是新消费位置。source capture ID不同但公共观察未变，不自动制造新学习step。数据保存consume/advance布尔值，训练必须复现同规则。相同digest只复用W，每次新的capture仍重建/验证当前E、ref→row和candidate gather，不复用旧raw绑定；advance=false仍可有新的N选择/loss，不能删行。TBPTT四步计数仅计真实advance，score-only行在同chunk累计loss后统一backward。

S0实际输入规格固定为`s0-admitted-policy-offers-v1`：当前Runtime端口2只把已准入、非空C的决策观察交给Model。内部等待、后继探测和终局采集全部保留为raw capture，但未offer的观察明确标为模型未消费，不进入训练W序列；离线数据只取实际offer边界。模型对空C观察的单元能力不等于当前在线端口支持。以后若引入独立无动作observe消息，必须版本化InputSpec并同时修改采集、训练与在线端口。

若notice A在取得前被B超越：记录A未形成模型输入，ReadCurrent返回实际B或expected_source失败，绝不回填A。S0序列资格表示**采样取得历史连续**，不表示人类实际看见的全部瞬态历史。S0仅对已取得的当前card target/selector/info视图承诺保留。完整瞬态CaptureProfile以后必须明确逐seam的eager字段，并另获Human/真实曝光资格；不能通过轮询或编号连续补证明。

## 5. ReadCurrent / ReadSealed：第一版可实施边界

S0选择**完整public text-v2 Snapshot字节胶囊**，先不做任意字段懒拼接。ReadCurrent在game main thread调用现有ObserveTextMenuV2Context一次，并在同一同步调用里序列化其公开Snapshot；只读store存字节，不存TextMenuFrame/native对象/callback。game continuity留在调度envelope，不进Model。SubmissionGate只保证其已有串行范围，不宣称跨整个renderer/世界全局原子。

建议schema名`sts2.player-environment/sealed-observation-1`，read profile `text-menu-v2-sealed-1`：

| 操作 | 输入 | 成功输出 | 失败 |
| --- | --- | --- | --- |
| ReadCurrent | input_profile=text-menu-v2、可选expected_snapshot_id | capsule ID、profile、actual snapshot ID、runtime/environment/generation/continuity、capture time、bytes/SHA256、expiry和第一页cursor | unsupported/stale/capture_failed/too_large/capacity；不改写原source |
| ReadSealed | capsule ID、opaque cursor、max_bytes | 同capsule/digest、byte offset、base64 bytes、next cursor/end、total length | expired/not_found/cursor_mismatch/invalid_limit；不触发native capture |
| ReleaseSealed | capsule ID | 幂等释放只读保留 | 不影响Controller或已投递动作 |

初始有界参数：单capsule8MiB、总64MiB、最多32个、TTL120s，chunk默认64KiB/最多1MiB；这些是首个资源配置，不是全范围性能资格。使用单调期限，容量超限拒绝或明确淘汰到期对象，不静默丢正在声称保留的required历史。capture失败/序列化失败/容量失败均不发布成功handle。

cursor绑定capsule/digest/offset，不能跨对象使用或依客户端任意offset跳过。共享GetFull验证identity、连续offset、无重叠/缺口、最终总长与完整SHA，再UTF-8解码和严格public schema校验；不能逐块独立解码切开的中文。相同原件各页反复读native-capture次数为0是本包应证明的关键性质；新的ReadCurrent仍会capture，**本版不宣称已实现dirty-based不扫描**。

全量input必须同一capture，required partial/inconsistent不能成功交给Model。ReadSealed成功只证明旧字节可读，不延长C合法性；Act继续用原snapshot ID及现有exact检查。静态定义复用、对象分页、可靠dirty/single-flight和factorized C是后续可测优化，不堵住第一次真实胶囊闭环。

## 6. 时机、控制与结果

目标公共事件profile选**owner-ready**，不是effect-fenced：当当前owner有完整输入关系即可选择；child-ready不等父完成，不要求所有动画/known-pending清空。效果完全结算的boundary facade独立命名，不混入默认。

S0复用既有Runtime有限auto/one-step/stop及当前text-v2结果处理；其`driver_observed_after_delivery`只是后续观察，不能冒称causal successor或全部结算。这个兼容实例可能保守等待，明确记入S0时机策略；并非已证明目标事件executor全部并发能力。

readiness映射：主战斗root由native可输入/完整menu决定；虚拟card_targets/card_confirmation由text-v2 current cursor精确关系决定；native selector/信息页/奖励等由其当前owner和complete choices决定；unknown/partial/无current input则程序等待或停止。终局game outcome与task summary结束不同；S0若source summary不支持就明确限制，V1必须补齐。

unknown delivery撤新mutation、核原request，不自动重发；delivered/pending与效果unknown分开记录。Stop取消新提交并确认原owner释放，不能撤销已接受native效果。有限预算/超时handler在AgentSpec登记，默认不偷偷替选或重开。控制消息/receipt用于程序，不驱动I/F-off的W。

## 7. 选定第一模型和输入

第一真实训练/运行候选选**S-M2-0（结构化观察-only小图）**，T-LightAction保留为后续配对参照，不作为S的前置。S0模型输入来自公开text-v2结构经固定白名单投影，不能序列化Host私有TextMenuFrame。共用现有public projection校验；新增直接结构化中间值，不将文本renderer字符串再解析成S输入。现有semantic_ref的任意列表ordinal不作为S特征/digest，只有声明的公开顺序可编码。

允许进入Model：CURRENT_PERSISTENT、CURRENT_PAGE的kind/stage/prompt/public content、CURRENT_MENU的当前cursor/selection、visible referent的公开role/kind/label/state/properties。StateDigest固定到去身份的相同结构投影/规范化版本，实体等价重排及opaque重命名不改变它，不能反向用candidate参数补writer。排除时间/sequence/snapshot IDs、runtime/environment/seed/control/request/receipt/history、action IDs和当前标签；绑定索引只用于gather，不作为身份embedding。I=None、F=None，也不改名塞到P/E。正常当前selected/focus/HP变化仍可见。

最小固定图：d=96、K=1（K8/Reset是后续独立配方）；共享UTF-8 byte局部文字encoder（258含边界标记、embedding32、Conv1d输出64/kernel3、GELU、masked mean、投影96）；numeric保留known/type与signed-log及有界signed-scale通道；typed字段/对象/容器与公开关系用一轮按关系类型变换后masked-mean的稀疏消息聚合；父→子和子→父分别标记类型，字段名由共享byte encoder表示，numeric变换固定v/(1+abs(v))和signed-log1p。无序集合不编码任意ordinal；有意义的公开顺序显式表示。current C不进入memory writer。

固定槽attention/gate从当前E/global及旧W写一次。候选verb/kind/公开label与带role的subject/argument实体gather编码到96，再只读E/W，经MLP得到全部logits。无subject和多参数有显式mask；同名实例保留公开区别；不按raw ID学习。generic对象树/field适配可以承接多场景，若有未支持必需字段则拒绝，不把任意JSON当已资格全场景。

资源初值：每字段最大4096 UTF-8 bytes、每frame总文本1MiB、实体/结构节点65536、边262144、候选16384、单请求32MiB；超限明确拒绝，不截断。实现须声明实际node/edge定义，不把预算数字当原生上限。首先CPU/2线程、FP32、AdamW，学习率1e-3、seed0，TBPTT每4个合格观察detach；无N事件advance，无N chunk不更新optimizer。预算和数据不足时减少明确训练updates/episode数，不悄悄换信息/模型图。

## 8. 数据、训练和真实闭环

S0先用获授权真实原生游戏中的**显式teacher-Agent**产生选择数据；teacher只消费同样公开观察及完整C，策略程序和参数有身份，不能读隐藏状态。可编程策略可用于采样覆盖，但不得伪称Human或强策略。

原始run records保存：producer/profile/runtime/game身份、实际capsule/hash与消费顺序、chosen C成员、请求/结果、终点/中断/控制、source-kind=agent、teacher identity。公开观察与动作标签形成observation-only序列，receipt等仅用于资格/诊断不进Model。保存已取得的无标签观察、重复/缺口/reset，不能从相邻canonical行补完整前缀。

数据以独立新run/起点来源group先划分train/dev/test；目标先至少3独立集合run，若实际数量不足则只能报告学习smoke，不给held-out泛化结论。示范→N是模仿teacher行为，不证明最优；训练loss下降不是完整策略资格。不得触碰受限Gold或把历史dev改成新test。

复用ArtifactStore/Manifest、用途记录、RunReporter及validator；local worker与CLI先连，不建立通用资源调度平台。实际训练输出配置/data hash/checkpoint/optimizer或可复验结果、参数预算及loss；导出固定Agent包并做离线/导出score一致与runtime状态一致检查。资金首选0美元本地；所有新增model/training/API/GPU费用用一个累计ledger，预留后提交，不超过20美元，不明费用不盲跑。

G2-S0通过需：exact-game构建、isolated安装/冷加载、真实capture及多次sealed read无重复原生采集、真实teacher数据、实际小训练、校验导出、learned Model经原生完整C执行至少一条native动作、明确stale/unknown/Stop回归和实际停止、输入到结果全来源可追。使用新Human capture不是此S0必需条件；Human链保持独立待做。S0不证明全部L64、全角色、胜率、全流程流畅、云/GUI产品完成。

## 9. 实施与验收关系

1. 本文件经独立native/AI/冷工程师/工作流审查和负责人核对后，记录G1-v1接受范围。授权来自最新用户委托，不冒称之前已读懂旧包。
2. 合入设计规范，Connector sealed read＋SDK、S模型/数据/导出、检查经济性可以按独立owner并行；Runtime/实际闭环由lead整合，共享wire一位writer。
3. 先portable机制测试再exact-game；先isolated读/停止canary，再teacher采集/训练/learned运行。改源码/产物必须重验受影响身份与gate，不移植旧安装资格。
4. S0反例若否定抽象，先改本规范和消费者合同，再修其owner；不能靠额外UI脚本/私有字段/盲retry补洞。
5. G2后扩原生flattened信息路径/事件与Human CaptureProfile，覆盖固定机制族；V1验自主主要流程、记录、产品、恢复和成本；G3封存完整支持范围，R1发行，R2稳定规模实验。200/500牌罕见常规和10000压力由后续可扩展层分别验证，不把S0的少量实际牌声明为通过。

旧四模型全场景要求替换为首个默认Agent全范围＋历史模型compat矩阵；约10k合格交互与K/Reset、backbone、N/Z/O研究义务保留单独阶段，不阻塞S0也不无声取消。主线转研究规模应依据真正达到的工程/数据条件，允许有界研究pilot与V1工程并行。

## 10. 工程速度与长期维护

当前框架不引入另一套registry、权限、原生操作队列或版本权威。旧代码按已核对能力复用；无复用价值时重构owner并保留兼容读/迁移，不为名称调整大量文件。规范、架构与当前状态各一处：本文管基线合同；ARCHITECTURE/COMPONENTS管已接受组件关系；TESTING管可执行gate；PROJECT_SYSTEM管风格/命名/文档；CURRENT只管正在做什么。

开发时用最低忠实单元/合同/跨组件测试。纯Markdown路由过重可在独立受验CI包修复，保留identity/BOM/boundary/history和链接检查；工具/运行/schema/CI自身改动仍按实际风险跑full。不能把本PR含可执行probe的累计diff改名docs以跳过已选门禁，也不重复跑相同长本地full与hosted full。

合并走topic PR到develop、组件源码normal merge，release/main与安装分别记录；不直接push保护分支、不admin bypass。有限观察外部任务，等待期间推进独立工作，不伪造后台monitor。独立review针对真实diff/证据、不能作者自签，也不因每个小改动重扫全部历史。
