# RL 长跑运行图（按节点）

## 当前 RL4 节点与运行入口

Friday CPU节点继续跑原Managed Actor；单张本卡批准的A30/A100常驻μ推理与θ训练，通过内网常连接互通。
当前源码/预算从CURRENT进入本轮；启动与同CP继续见[原接口手册](RL_ACTOR_LEARNER.zh-CN.md#friday-的正常启动与继续)。
正常入口为登录节点 `tools/friday/rl.py launch`；GPU脚本执行 `learn --external-submit`，
原CPU prepare/submit/E2保持同一条链，避免计算节点嵌套sbatch被拒绝；
不再用下面旧 `rl_run` / 学校GPU示例启动整局RL。凭据和临时推理auth只经环境。
收尾先停新段、等现有段、CP/R2读回后释放；数组快照因时限中断时只用原 `rl.py save`，不再打局。
短段unknown只结束该局、保留已验证前缀，不默认按比例停整个Actor；
背压调用预算、已闭episode回收与同CP操作参数迁移见原接口手册，不靠修改运行中JSON生效。
部署前冻结同一源码与Actor CPU compiler身份；旧CP先在CPU按原Store生成compiled-input pack，
完整保全/读回后再申请GPU。新的CPU reader与独立推理stream操作字段及失败恢复见
[公开输入准备](RL_ACTOR_LEARNER.zh-CN.md#公开输入只编译一次与准备重叠)。不改运行中的源码/配置。
GPU关闭后可由CPU完成Archive与冷Store预取；非零CUDA RNG的最终替换恢复仍须真实CUDA进程，
CPU不能代存成无CUDA RNG的新CP。先seed原Exchange，再按需取CP及受保护μ的payload，
不为恢复递归复制全部warmup祖先。所有GPU初始化/收尾仍计本卡总预算。
第一小时外是否继续以当前用户预算为准，本SOP不授权。

## 以下为旧部署操作史

一页说清 RL 正式长跑在哪个节点跑什么、怎么起、怎么停、怎么续、出错看哪里。细节不在这里重写，只链接原手册：
- 接口和续训规则：[RL_ACTOR_LEARNER](RL_ACTOR_LEARNER.zh-CN.md)
- Friday 部署、打局、评估、停止：[FRIDAY_ACTOR](FRIDAY_ACTOR.md)
- 学校 GPU 登录和 SSH：[FULLRUN_TRAINING](FULLRUN_TRAINING.md)

起源为2026-10-29的RL1准备示例，RL2已经按本卡授权运行。实际run、精确源码/配置、作业和预算从[CURRENT](../../docs/memory/CURRENT.md)进入本轮记录；下面旧路径/时长是示例，不能用它们覆盖当前卡或重复启动。本页本身不授予新的训练权限。

## 链路和每条边

```
 学校 L40S：学习者 ──模型版本 / 检查点──▶ R2 ◀──轨迹分片记录── Friday：打局者 × N
                    ◀──────读分片─────────    ──────取最新版本──▶
 Friday：评估（固定种子，和训练种子分开）──结果──▶ R2
 Mac：只部署和用户录制，不进 RL 循环
```

| 边 | 提供方 → 使用方 | 交换物（合同） | 使用方要求 |
|---|---|---|---|
| 起点模型 | 已发布模型 → 学习者 | `model` artifact `d31b37d8…`（原 2016 步） | 学习者配置里的 `start_artifact_id` 和 `start_model_sha256` 一致 |
| 模型版本 | 学习者 → 打局者 | `spireagent/rl-model-version-1`，键 `exchange/rl/<run>/models/<版本>.json` | 打局者下载后核权重和包哈希；只增不改，最新 = 最大键 |
| 轨迹分片 | 打局者 → 学习者 | `spireagent/rl-trajectory-shard-record-1`，键 `exchange/rl/<run>/shards/<actor>/<序号>.json` | `run_ids` 等于分片 manifest；分片本体走原轨迹读回校验 |
| 检查点 | 学习者 → 下一个学习者 | `stpd/rl-learner-checkpoint-2`，记录在 `exchange/rl/<run>/checkpoints/` | 同一 run、同一起点模型、同一份源码；缓冲区缺样本就拒绝 |
| 评估 | Friday 评估 → 用户/卡 | 原评估账本，固定种子前缀（PR287） | 评估种子不进训练；unknown 单独计数 |

## 学校 GPU（训练节点）

**先核能不能用**（用户自己做，登录、MFA、验证码只由用户做）：
1. 在学校门户登录，看有没有正在占用的分配。一个账号同时只能有一个，旧分配要先释放。
2. 在已授权预算内申请L40S，记下实际分配号、主机名和平台时限；不能把旧卡5小时授权当作平台上限。
3. 在门户的 Jupyter 终端里跑 `nvidia-smi`，确认是 L40S、驱动正常。
4. 用户把分配号和主机名告诉执行的 Agent。之后 Agent 按 FULLRUN_TRAINING 的严格 SSH 入口（本机 → moss 跳板机 → 分配主机）核主机公钥，不关闭校验。

**起学习者**（Agent 做）：
1. 使用本轮已验收的干净、准确源码。学习者和actor可以采用不同提交，但交换合同、模型适配和各自producer必须匹配，记录各节点实际身份；不能为“同一SHA”替换正常运行版本。RL1早期`e187b6b7`/`445b9f98`路径仅作历史示例。
2. 学习者配置用 RL1 留的 `learner-school.json`（run=`rl1-real-1`、peer=R2、device=cuda、`wall_seconds`=15600，比 5 小时少约 40 分钟，留出准备和收尾的时间）。
3. 启动脚本按 RL1 的 `school-learner.sh`：检查源码干净 → 从用户放好的私有文件加载 R2 环境变量（不打印、不复制）→ `python -m spireagent.workbench.rl_learner --config … --work …`。脚本里的 R2 文件路径要先核实。
4. 学习者先发布版本 0，打局者才有模型可取。

**续训**（每次分配用完都要做）：
- 学习者到 `wall_seconds` 会自己发最后一个版本和检查点，然后正常退出。
- 下一次分配：从 `exchange/rl/<run>/checkpoints/` 读最新记录的 artifact id，**显式**写进学习者配置的 `resume_checkpoint`，再启动。不写会被拒绝。
- 打局者不用停，它们一直取最大版本号。
- 先核当前平台分配和闲置规则；不许用假保活绕过。SSH断线不等于训练退出，先核真实进程；分配确实结束时才从最近已核CP接段。

## Friday（打局和评估节点）

- 精确部署与plan由当前卡和不可变部署标记负责；不要将历史`445b9f98` / `config/rl1-prepared/plan.json`准备例子当成现在仍未开跑。RL2远端应用修复可复用原Host/Runtime包，来源必须明确。
- 起打局者：学习者发布版本 0 以后，按 FRIDAY_ACTOR 的 RL 一节提交。每路一个核、一个唯一 actor 名，用 fc430。
- 核数按当前卡；RL2采用12路。先区分正常等待、维修空档、准入过期和真正积压，再考虑资源调整，不能从旧测速或累计等待比例机械加核。
- 停：`scancel --signal=USR1 --batch <作业号>`，打局者会排空当前小账本（默认 2 局）再停。不能承诺"只停当前这一局"。
- 评估：用原评估入口和固定种子前缀新建账本，评估种子不进训练。
- 用户什么时候要登录：Friday 登录（含 MFA）过期时，由用户登录；Agent 不碰凭证。

## R2（存储节点）

- 全部交换都在同一个 peer 下的 `exchange/rl/<run>/`：`models/`、`shards/`、`checkpoints/`。只追加、不改、不删。
- 核对：版本记录、分片记录、检查点记录都自带 `record_sha256`；检查点和分片的本体走原 artifact 读回校验。
- 运行暂存按原 7 天保留规则；删除要用户点头。

## Mac（运行节点）

- 只做部署（构建、打包、推到 Friday）和用户的日常录制，不跑学习者也不跑打局者。
- 用户录制优先：长跑期间不在 Mac 上开重活。

## 长跑维护：先判影响，再决定继续、隔离还是停

训练分成学习者、打局者、评估和保存四个责任。一个部件出错不自动杀掉其余部件；
但数据身份、完整性、数值或既定停止线失守时，也不能以“还有进程活着”为由继续消费。
当前任务决定 run、准确部署、预算和阈值；本节给处理方式，不扩大运行授权。

| 问题 / 判断依据 | 跑还是停 | 维修和恢复 |
|---|---|---|
| 一次模型查询/下载失败，原模型已完整校验 | `s3_get_failed` / `s3_list_failed` / `s3_stream_read_failed` 连续不足3次时，actor可暂用已核模型；按原轮询间隔再读。没有首模型就等，不开局 | 每次记录阶段、异常类型、安全错误码/errno和连续次数；成功归零。底层SDK已有标准重试，外层最多3次连续失败，耗尽停该actor，不无限重试 |
| 模型记录、哈希、包身份不符，或其他未分类异常 | 立即停该actor，不换成不明模型；若是共享坏产物，暂停相应消费者 | 核原immutable记录和manifest；另版修生产者，旧原件不改。不能把完整性错误列入网络重试 |
| 一个或多路actor退出，学习者仍正常 | 隔离失败路，健康路和有可用数据的学习者继续；看守报告实际运行/失败数，不称满负载健康 | 先读`sacct`、`result.json`、原stderr、账本和保全收据。核原进程已退出、未决offer、原分片是否已公布。恢复使用新plan和新actor名字/新种子，保留旧owner/STOP，不重投旧局。全体替换前先USR1排空旧阵列 |
| unknown、save_failed、账本清理未明 | 停该路并保全现场，不重打；全局unknown超过5%按原停止规则停打局者 | unknown不是普通下载错误。原分母保留，查Host/Runtime/保存owner；不能把Slurm的COMPLETED当游戏成功 |
| 学习者等数据或没有新版本 | 先核进程、最新事件、到达分片、准入/过期数、更新数；允许正常等待 | 无积压但过期高，优先调整发版/换模/窗口配合；积压高才查读取或训练瓶颈。不能仅凭等待或过期就加核，也不造活动绕学校闲置规则 |
| 轨迹过期多 | 原件保留，按现配置拒收；运行时不偷偷改判 | 在受控切换点另存配置，记录生效版本/更新数和原因。例：每8update发版且2局才换模，3版本窗口可能小于两局耗时；适度放宽后看新增准入率、KL、ratio/clip和正式评估。窗口不是越大越好，CP保存频率独立保留 |
| loss、梯度、权重非有限数值或学习者异常退出 | 停更新，保全日志；看守按原停止规则排空打局者 | 不反复重跑找绿。先确认错误更新是否被发布；只从已完整核验的可用CP/权重恢复，记录损失的未上传区间。学习者恢复前不要无上限积压轨迹 |
| 学校分配结束、主机丢失、SSH断线 | SSH断线先核真实进程，不重复启动；分配确实结束才接段 | 从R2完整读回CP并核哈希、源码、run、起点，再显式`resume_checkpoint`。同代码续训保留optimizer/计数；改学习者源码按原换代规则用新run热启动，不冒称无损续训 |
| R2暂不可用，保存尚未确认 | 不把本地结束当已保存；停新准入、保留本地待保存/失败前缀 | 按原保存/读回入口核原请求结果，避免重复发布或重打。恢复后核原immutable对象，不盲重试有副作用操作 |
| 磁盘不足或内存压力 | 按容量SOP停新准入；不删原件求继续 | 内存先分anon/filecache、OOM事件和进程RSS，不能把cgroup缓存占满直接叫泄漏。核实际写入卷、保全双副本峰值和剩余空间；有依据再改资源申请 |
| 固定评估变差或停滞 | 历史RL3才采用连续两次平均最高层低于11.75的规则；RL4.x采用下文配对区间与具名rollback CP规则。连续三次没改善只报告 | 用原固定种子/独立账本和完整分母比较；评估unknown不算胜负，不换种子求好看。KL最近10次均值>0.1报告用户，不把0.02损失系数当自停阈值 |
| 看守退出/状态过旧 | 游戏与训练进程可能仍活着，先核；补回监督不能重复开训练 | 新版支持`SIGUSR2`仅请求看守维修退出：不再开评估，等已有评估收齐且无全局停止原因后退出，不写训练STOP。USR1/TERM/INT仍是全局停止请求，含义不同 |

### 每次维修的固定顺序

1. 留一份事故前快照：准确run/源码/配置、Slurm与PID、最新模型/CP/更新、完成/unknown分母、
   未决账本/保存、剩余绝对预算。记录事实和不知道的事，先不改现场。
2. 以最小范围隔离。健康生产者/消费者继续；新证据若触及共享数据或停止线，再扩大停止范围。
   一人写运行配置和部署，其他人只读调查/复核，避免双重启动。
3. 原部署不改源码。热修复在独立分支，相关回归、非作者复核、所选门禁/CI后，
   登记新x86部署身份；复用已核Host/Runtime包和依赖可以，但准确来源写入收据。
4. 切换前确认旧owner状态；已退出不能当作旧局从未开始。新worker用新的身份和种子，
   所有旧结果、失败、unknown和计数留原处。新看守必须绑定实际新阵列，并保留原开跑时间、
   评估种子与时钟、总预算；维修不重置20h/22GPUh限额。
5. 学习者只改配置也要受控：上一进程正常结束并保存完整CP，同源码、同run、显式CP续接，
   新工作目录和配置保留生效点；先排除父launcher会自动释放分配，禁止两个学习者并写。
   launcher没有热接段支持时优先走原分配边界，不直接编辑正在执行的脚本。
   确需同分配人工交接时，先核准确父子PID和自动释放路径：只暂停旧父launcher，正常关闭学习者、
   完整读回最终CP并确认旧学习者/timeout已结束，保证旧父不会再执行release，再起唯一新监督者。
   新监督者保留原绝对deadline，旧关闭工作集另行完整保全；不能把这次特定PID照抄到下一次。
6. 验最短真实链路：新进程确实活着→下载/校验新模型→完成新种子局并上传→
   学习者接收/更新→新CP完整R2读回；另核看守监督的是新作业。源码/单测绿不等于恢复。
   旧作业若仅剩证据复制，可与新生产并行，但先逐项核关闭result/summary、已公布分片、
   无待启动成员且actor/Host/Runtime均已退出；明确标记保全阶段并保留原jobID、证据和预算。
   新生产仍走正常提交、owner不重投与实时公平容量检查，不能只凭rsync在跑就绕过排空。
7. 比较切换后的增量指标，不拿累计过期比例掩盖改善；质量仍看固定评估。
   报告已恢复、降级运行或未恢复及剩余问题，并把可复用解法归回本指南。

### 监督频率和权责

沿用一个Friday看守：15分钟汇总R2与调度器事实、约4小时固定评估、原机器停止线；
Mac每小时只读检查，异常及时报告，普通结果4–6小时汇报。`actors.running`是Slurm分配仍在运行的数量，
`actors.active_actors`才是尚未产生退出结果的actor数量；`actor_results`保留退出状态及保全是否完成。
原launcher在复制前已封存scratch/result，看守直接读这个已有事实，不能等NFS复制结束才报actor退出。
这只改监督，不代表退出结果已通过完整保全，也不授权自动重投。调度器观察失败要写“未知”，
不能写0路或健康。看守只监测和执行已定停止规则，不自动改训练参数、修代码、重开unknown、
换种子或增加费用。actor只读模型故障的有限重取是唯一这里新增的自动恢复。

### 2026-10-29 预取失败的已知边界

六路`model_prefetch_failed`的原底层异常被旧实现吞掉，不能补造网络或OOM根因。
原版Exchange对最新及两个相邻模型冷读/哈希成功；磁盘充足；活动作业4GiB大头为文件缓存，
所查OOM计数为0。修复因此针对已经确认的缺口：安全诊断、有限只读重取、永久错仍停，
及看守实际actor容量可见性；不把“以后不会再失败”当验收承诺。

计速单位、固定工作量、离线读数与缺失值见[RL计量](RL_MEASUREMENT.zh-CN.md)。学习效果评估复用 OBS 的同一报告入口，不从训练吞吐推断变强。

## RL2已实际验证与仍待验证

已在真实运行证明：actor采用学习者新模型、Friday→R2→学校更新、完整CP冷读，以及学校同源码CP续训。PR315维修后，新actor的18局saved轨迹已被学习者读到，后续CPv125/update995完整读回；这只界定本次恢复证据，不代表长期无故障。

正式评估、模型变强、后续预算内持续运行和最终保全/释放仍由本轮任务验收；不能用上述恢复成功提前收口整轮训练。


## RL3 工程接入与调整边界

RL3 配置冻结、单一配置启动及停止入口见 [RL3运行](RL3_RUN.zh-CN.md)。
`start_artifact_id/start_model_sha256` 决定新模型的起点；可独立指定
`reference_artifact_id/reference_model_sha256`（或各自本地 package）决定 KL 参照。
省略 reference 时沿用起点。新 run 不读取旧完整 CP：Adam、buffer、baseline、计数和 RNG
按新配置重新初始化；热启动来源和参考哈希分别进入配置、导出身份和完整CP。
完整CP另存 CUDA RNG，恢复按CP原buffer顺序，不按分片排序改变下一批候选池。
完整CP可能比模型晚上传完成；owner每轮收取已完成的CP，即使正在等新数据，
也不等下一次模型发版才挂接存档记录。挂接仍沿原发布/验证路径，可能包含同步I/O。
跨源码、run、起点或参考身份不同的完整续训仍拒绝，不能改旧记录来绕过。

学习者只读 GET/LIST 的错误保留安全的操作、键/前缀、SDK类型、服务错误码、HTTP状态。
只对明确的连接/超时/限流/可重试服务错最多三次有界重读；鉴权、配置、完整性错直接失败。
原SDK重试仍计入总等待，原错误消息/URL/头不进入日志。诊断在本地 `io-errors.jsonl`，
即使R2不可写也保留原因；游戏、unknown、写入结果未知不在此重读范围。

`background_io` 默认关闭。有界FIFO只证明已发现分片的准入先后，**不证明每次更新前
候选池相同**：同三份已ready分片、read_ahead=2，原路径首poll收3条，新路径只收2条。
同一冻结准入cut能复现批次/RNG/末权重，但这个局部门不能代替在线保批次资格；
当前采用门未通过，默认配置保持关闭。开启该开关的短链路仅验证功能和守恒。
D2的FP32 `performance` 仍为独立默认关开关，数值是容限内近似，不能称逐位训练相同。

异步STOP查询频率由 `remote_stop_seconds` 限制（默认5秒），响应正常时的发现延迟上界
为该间隔加一次主循环/更新及请求往返；本地STOP每轮检查。读取超过
`remote_stop_read_timeout_seconds`（默认30秒）时owner在下个循环明确失败并保存最后
完整成功状态。SDK的在途读线程及网络保存可能继续至其本身的超时/重试上界；
这不是30秒完整存档承诺。GPU CLI的TERM/INT/USR1只标本工作区STOP，完整更新后保存。

actor分配的signal handler一直保留到snapshot逐字节核验和两份receipt写完。
原保存中断后先核allocation终态，再在原scratch仍可访问的节点执行：

```bash
python tools/friday/rl.py save --plan /ABS/actors/plan.json --actor ORIGINAL_ACTOR
```

此入口只复用原owner与rsync接续拷贝，重核每文件hash；不重开游戏、不改原summary，
缺owner/result、身份不符或allocation未终态均拒绝。看守持久化每job的signal intent，
同一停止最多发一次USR1；调度返回未知需人工核原请求，不盲重发。R2永久STOP保持幂等。

运行中调整没有热调接口。先由唯一owner按正常STOP排空，核同版本模型和完整CP，
追加调整前不可变引用；冻结新配置和源码后另起run热启动，记录父CP、旧/新配置hash、原因、
wall/GPU预算、版本/更新数，以及Adam/RNG/buffer/baseline重置方式，再追加调整后引用。
同源码的原完整续训入口只按既定恢复合同使用；普通配置文件修改不代表热调已生效。
每8更新发版/存档的默认频率保留：原capture约0.87%，本轮不为这一小收益引入独立调频。
提速后8版本过期窗口对应的秒数会缩短，不能把同一个窗口值当同一个实际年龄。

采用前200步核loss/KL/clip/ratio/梯度；第一小时先保持12路、按逐条账本再定供数。
下一次可比评估平均层数比上一代低超过1层时，按已批准运行卡回退到保留模型；
回退/调整发生在完整存档边界，原失败分母、旧版本及原STOP不改不删。

## RL4.1 长训运营入口（OPS 候选，集成后使用）

原 `watch.py` 的 `mode=segments` 仍消费原 `rl.py launch/learn/prepare/submit`、
`evaluate.py` 和 Store；不直接开 World。GPU launcher 保留48 h、显式异卡1运行+1排队门，
`retain_capability`仅在成功交接后把同一capability留在登录owner环境中供新actor补位；异常恢复原环境。
CPU 可先在 general/debug 验闭环。最终组合和 G2/H1 仍由 CURRENT
原任务验收；这里不授予开训、GPU 或模型质量资格。

- 冻结完整 learner JSON、原执行 JSON、peer、源码、CP、actor数和排除节点。prepare、learn、
  bind_actors支持1–500路；短段RL采用idle_divisor=1，普通打局/评估保持四分之一，
  排队超过300项仍降至32路。配置500不等于已取得500核。
  rl41 profile版本上限为900000，正常learn显式冻结每actor最多100000局。
- watcher配置使用 `schema=spireagent/friday-rl-watch-config-1`、`mode=segments`，必填
  `run/peer/budget_id/deadline_at/training_seconds/actors/learner_config/execution/
  runtime_source/learner_python`。deadline是UTC epoch；training_seconds≤72000。
  `operating_budget={id,total_seconds,consumed_seconds}`随完整CP保存，恢复继承已耗时间和截止，
  不重给20小时。具名运营窗口与CP16学习run分开，诊断不会占用/污染后续长训窗口。
- 已有获准GPU holder时可显式指定`held_job_id`：正常launcher在同一用户的唯一RUNNING、
  同型号`rl4-learner`分配内开一个srun step，拒绝已有数字step；不另申请GPU。
  watcher只核账和停止准确job.step，不释放holder。未知step提交保留原intent，不重投。
  有限故障资格化可给`learner_supervisor={path,sha256}`：固定脚本在原E2内包住原learn命令，
  只换learner时用`learner_replacement_started`事件连接原/新generation与已耗秒数；
  watcher合并两代分母并对全部owned generation Stop。该脚本不拥有游戏或新的提交权限。
- 登录owner运行 `python tools/friday/watch.py --config <frozen-watch.json> --work <private-work>`。
  capability只在内存/继承环境中，不写文件、命令参数或日志。`cpu_only=true`要求learner为cpu，
  仅用于获批CPU证明。CPU分区、constraint与exclude_nodes明确冻结；GPU只用I1正常launcher。
- learner allocation终态后，先排空并核每个原actor的Slurm raw/array ID。选最新完整CP；若它
  还在共享盘pending，先完成同ID保全，再空目录正常reader读回。新allocation/generation、
  actor名字和seed namespace继续；只丢旧热代，不重offer旧World。actor终态由learner owner
  释放lease/delayed引用，原ACK保持，不能补造terminal或训练标签。
- 未知sbatch回复保留原intent，禁止自动再投。owner重启复用原work；新目录继续需pinned
  previous_watch_state。原state带原watch-config.json pin，run/peer/预算/截止/总时长必须相同。
  旧job仍须对账；只有CP不能复活交付账本。CP-only恢复指学习状态和预算。

计时是learner就绪后的服务/更新窗口，不是CUDA kernel秒；排队、初始化和退出排空另计。
恢复用原owner保守时间与CP对账，原失败和回滚掉的更新不升级成有效训练。
真实offer/close/actor终态按唯一intent归并，迟到/乱序不增分母；未offer准入单列。
`watch.py mode=segments` 的全局5%门用已结束或已证失联尝试，在途不当成功；
恰好5%不停、超过才停。短段worker正常退出时不因单局unknown/save_failed写全数组STOP；
进程异常仍阻止待启动worker。单局ledger停止和保存义务不变，未知局不重投。短段Actor未显式配置 `unknown_stop` 时不按局部计数停机，
直接 `rl.py launch/learn` 不自带该全局比例门。heartbeat不刷新SGD进展。
可选 `node_unknown_exclusion={}`（默认 window_attempts=30、min_attempts=20、node_fraction=0.25、
fleet_median_max=0.10、max_nodes=4）按sacct NodeList把原close归到节点：某节点最近窗口达阈值、
其它节点中位数仍低时，记typed事件，向该节点在跑actor发原合作USR1（打完当前局不再offer），
之后补位与新attempt的actor `--exclude` = 配置exclude_nodes + 自动排除节点。原unknown照计入全局门，
不重投、不改评估；整体都高时只记录不排除，超过上限记录后交回全局5%门。阈值来自c11回放。
learner发布failed/finished；故障Stop绑定generation，全run人工Stop仍有效。

审计失败先持久化integrity finding，control不可达也由原owner停住。修后继续用
`acknowledged_integrity_events={原event key:原record_sha256}`具名确认已经诊断的finding；
原finding、失败和unknown保留。连续两次配对区间全负时停止并写具名rollback CP，先诊断再恢复。

archive健康消费磁盘可用量、站点usage配额、inode、积压及观察新鲜度；不足暂停新lease/新局。
未报告某挂载配额不等于无限配额；陈旧/错误观察不当通过。storage_inventory分物理路径种类和
model/heads/CP逻辑载荷，两个时点量增长。热段最后引用释放即删；成功μ发布仅释放本次临时导出
副本，规范Store不变。完整CP全入R2，本地仅冷却已远端逐字读回的旧payload，保最近3份及评估/
回滚引用；共享chunk、活reader、未保全数据不删。结束走原archive drain，不靠人工cron。

### 每两小时固定 Managed 评估

`evaluate.py export-checkpoint --learner-config ... --checkpoint ... --output ...` 直接导出raw θ；
CP16另给原构造包。prepare-checkpoint绑定CP→动作包→原evaluate/ledger，0学习/0私有开局。
正常评估保全部完整原件，也保原P1轨迹供Boss/协议报告；unknown只保原件、不评分。
submit支持general/debug及排除G1节点，仍用原容量门、E2和分区时限。

watch的evaluation配置固定seconds=7200、seeds=50..100、canonical manifest pin、
baseline_checkpoint、baseline_binding pin、baseline_batches（各批plan/result/snapshot pin）及执行JSON。
配方切换可显式设置`rollback_reference="initial_checkpoint"`，绑定已有`initial_checkpoint`的
0秒评估作固定参照；连续两个后续2h点的配对区间全负即停止。部分回升但仍显著低于切换点
也触发，不再只看相邻两点。默认仍比较上一点评估。`rollback_recipe`可记录已批准回退参数，
随具名CP写入原`rollback-checkpoint.json`，诊断后另代恢复；不会热改在用配方或自动重投。
所有点必须完全同题，核完整Host/Runtime/Input/初始进度/argmax协议，分别对CP16和上一点评估。
报每层分布、最大层、Act1 Boss正见证、胜局、配对差区间；unknown或未获资格的那一对排除并单计，
不补题。评估准备、排队后运行和在途停止都受同一绝对截止。

首批技术停止只可用新批填原not_started、无offer且无run_id的题；fixed_baseline由原每行资格
投影，保固定分母，拒绝重投任何已尝试seed，原失败status不升级。旧full_peer批没有轨迹时，
`evaluate.py project-report --result ... --plan ... --output ...` 用原by-ID typed reader及P1 extractor
只读补独立投影，绑定原result hash，不改原report/ledger。把精确observation_projection pin放进
对应baseline batch。读回失败保持未证，不能重开该局。

### 完整发布与新运营窗口

Friday worker/evaluation owner以完整fsync临时字节原子不覆盖发布，既有owner永久排他；
读者遇空、截断JSON或截断UTF-8视为尚未发布，后续观察，不据此重开旧局或停整链。
完整但身份/哈希/schema错误仍拒绝。STOP只看存在，空STOP仍生效；mutable state/result沿原fsync原子save。
step-id完整且指向另一job即拒绝，半成品在原ready窗口等待。

H1通过后用户可明确开一个新的72000秒运营窗口，并继续同一学习状态。
新watch配置用`previous_window_state={path,sha256}`绑定已结束的原watch state，run/peer相同、预算id不同；
原窗口须以`run_budget_complete`或`absolute_deadline`结束，且无active attempt、无pending评估、各attempt终态已记账；
unknown比例、watcher失败或信号等其它stop仍走各自恢复路径。deadline后启动的watch保留更早的typed stop，不改标为`absolute_deadline`。
指定`learner_config.resume_checkpoint`必须属于原窗口的budget id/total/deadline和owned generation。
原learner接收`operating_budget_parent={checkpoint_artifact_id,operating_budget,deadline_at}`明确父链接，
完整继承θ/Adam/EMA/RNG/训练计数，μ按普通恢复重新发布，旧CP和旧窗口耗时不改。
这只用于明确授权的新运营窗口；同窗口故障恢复仍继承已耗时间，禁止重置预算或deadline。

评估manifest先用`evaluate.py prepare-manifest --game-directory <已部署游戏> --output <manifest.json>`
读取原`release_info.json`的精确`version`（当前为`v0.111.0`），再交原prepare-checkpoint。
不自行去掉/添加版本前缀，也不替换固定种子。
`evaluation.initial_checkpoint`可指定本次长训起点CP作为首个2h周期的anchor：入场先安排其50槽，
后续仍在7200/14400/...秒评估当前CP；首个当前点同时对CP16和该H1 anchor逐seed比较。
这复用同一个pending/ledger/evaluation owner，不增加并行业务链；每个计划最多16workers，合计不超过已批CPU上限。


抽样整局重放由 Actor 的单线程后台审计执行，不阻塞保存/下一局；重放预算600秒、
低优先级。超时、子进程不可用或审计执行异常追加 `audit_inconclusive`，原typed结果
和数据保留，原件不释放；真实字节/身份/语义不一致才追加integrity finding并STOP。
审计结果使用独立控制连接请求STOP，避免与Actor正在进行的控制请求串读。
watch health分别报告pending、完成和inconclusive；最近50次已完成审计内，至少5次
inconclusive且占比严格超过20%时告警，只暂停当前budget的新World准入和补位，
已有World继续正常收口，不作废已有数据。每次审计的不可变request/result留在原attempt，
inconclusive不自动重放，后续诊断保留原失败身份。


正常STOP先封新准入，继续让inflight World通过原 `episode_end` 和receipt收口。
held allocation中的数字learner step使用原generation Stop、`login-launch/STOP`及
`learner/STOP`协作通知，不发step-wide USR1；绝对deadline仍按原owner取消step。
自定义learner supervisor的普通signal relay必须只 `child.send_signal(SIGUSR1)`，
不能 `killpg`：preparer/publisher及Python resource tracker需要存活到排空结束；
显式崩溃注入或硬超时的整组KILL仍是原故障路径。更换外部supervisor时更新其hash pin，
旧脚本和旧run不覆盖。CPU反例不证明旧200次失败的内层异常；原失败分母不重写。
后处理失败会保存 `processing-error.json`，并把异常类型、栈位置及跨RPC/worker的
原诊断纳入failure artifact；不保存异常message、源码行或locals，避免泄露请求/凭据。


learner收尾先排空既有episode，再完成标签/最后μ发布，最后完整CP完成后才关闭进程池。
最后μ发布或标签worker失败不会跳过非fenced学习状态的 `checkpoint(final=True)`；
失败继续抛出并记录typed `failed`，带安全诊断和finalization阶段，不产生finished假成功。
CP保留实际 `updates` 与 `published_updates` 的差异；恢复从CP内theta重新发布，不把失败μ
当成功。`input_fenced`仍只允许恢复上一完整CP，不能借此规则保存未确认输入状态。
这项保障以CP存储可写/可读为条件；CPU故障注入和本地冷恢复不构成R2或GPU运行资格。
