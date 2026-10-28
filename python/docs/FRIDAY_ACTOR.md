# Friday CPU 打局

Friday 是新打局批次和 Managed 固定种子评估的默认 CPU 目标。Mac 只做必要的 Mac 对照；
连接或队列失败不自动退回 Mac。这里复用 Managed 的账本、Runtime、保存池与
[轨迹格式](MANAGED_PRESERVATION.md)，不新增游戏执行器。A 段提供固定模型的
有限批次；RL 的最新模型指针和学习者收片接缝，待 RL1 的接口合并后接入，不能
把这份工程批次称为 RL 训练或数据资格。

## 固定环境和输入

生产部署使用 `/data/<user>/friday-actor/releases/<完整提交SHA>/` 的不可变组合，
其中 `source/`、`env/`、`packages/`、`candidate/`、`models/`、`game/` 绑定同一份
已验证部署清单。新版本建新目录；保留旧环境、包和候选供在途任务及回滚使用，
不在旧 release 上 git pull、重建包或清空环境。目录700，游戏文件600、目录700；
每次传输后实查权限。节点 `/tmp` 只写证据和运行日志，不复制游戏。

环境重建入口为 `tools/friday/bootstrap-environment.sh`，在单核 Slurm 作业中设置
`F2_ROOT=/data/<user>/friday-actor/releases/<新提交SHA>` 后调用；它会清空该目标
下的 `env/`，因此只用于尚未投产的新目录，不对在用或保留的 release 重跑。
固定 Node22.23.4、Python3.11.5、.NET SDK9.0.117、CPU torch2.13.0+cpu、tokenizers0.22.2。
Node 使用该 release 的 `node22.23.4/bin/node`；launcher 必须先确认它可执行，再将其
放到 PATH 首位，不能回退学校 module 的20.9。旧 prototype 作业1393924 在开局前
被 E2 guard 的 `import.meta.dirname` 阻断，原报告 actor_started=0/world_offers=0；
账本仍记 unknown，原样保留且不重投，不把这次前置环境失败当成有效性能样本。
当前组件为 Host `1.1.0-rc.58`、Runtime `0.1.0-rc.59`；执行源码、组件包身份、
候选 SHA/MVID 和模型原 Store 身份/包 SHA 仍分别精确绑定，不凭版本号推断可混用。

运行者使用 `spireagent/managed-evaluation-execution-v1` 原配置：准确 `producer`、
`host_pin`、`runtime_pin`、`agent_adapter`、`initial_progress`、`candidate_directory`、
`models`、`peer`、`python_executable`。工程吞吐批次模型只填一份；固定种子评估可列多份，见下节。Friday 另外固定
`game_assembly_sha256=71eec3ba5e0c640fc0cf0758517faf5f2ff039bea21966b22b23f2c427c7a09f`，
仅约束原 Host 审计响应，不能绕过 Host 的精确元组准入；省略仍用原 Mac 身份。另声明：

```json
{"retention":{"tier":"trajectory","purpose":"training","audit_every":50},
 "pipeline":{"save_workers":1,"pending_limit":1},"minimum_free_bytes":16106127360}
```

这里 `purpose=training` 只选无评估计分的轨迹保存；不授训练资格。无 R2 令牌时，
`peer` 可用独立本地 Store（须有原模型和父链），不把本地读回冒充 R2。
本地 peer 的批次由 `batch.py` 把运行 Store 改到节点私有 `/tmp`；R2 批次显式
设 `store="s3"`，沿原执行器直接用已冻结 peer，避免每个工人复制模型的全部训练
祖先。Runtime 原件和账本仍在节点私有 `/tmp`，所有模型根 payload 和新产物仍经
原校验器核验；其他身份和语义不改。

由用户在 Cloudflare 创建只限 `spireagent-artifacts` 桶的对象读写令牌，用户本人
放入 `/data/<user>/.config/spireagent/friday-r2.env`，文件600，父目录700。
文件设置 `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`；非秘密桶、endpoint、prefix
从固定 `peer` 读取。`worker.sh` 在执行时加载用户文件，Agent 不查看或复制该文件。
登录只让用户输密码和手机确认；共享 SSH 连接有效期间不重复登录。

## 起、停、调核数

先以 `prepare.py` 冻结非评估种子池。它只接受现有的工程诊断组，每组最多10种子；
逐种子调用现有 `require_non_evaluation_seed`。同一池中种子不得重复。
配对比较的 `c6220/fc430/mac` 是显式独立比较单元；普通32路数组统一用 `trial`，
在同一共享租约根中互斥取组。租约写入后不自动过期、不回收、不重投未知。

```bash
# 在已验证的准确 source checkout 中取得提交，选择其已部署的不可变目录。
release_sha=$(git rev-parse HEAD)
export FRIDAY_ACTOR_ROOT="/data/$(id -un)/friday-actor/releases/$release_sha"
cd "$FRIDAY_ACTOR_ROOT/source"
export PYTHONPATH="$FRIDAY_ACTOR_ROOT/source/python"
python tools/friday/prepare.py --execution /ABS/execution.json --output /ABS/plan \
  --namespace F2-paired-20261028 --groups 1 --per-group 8 \
  --cell c6220 --cell fc430 --cell mac --persistent /ABS/paired-results
# 一个节点/一个核；--constraint 按实际 sinfo features 选。
python tools/friday/submit.py /ABS/plan/batch.json --cell c6220 --cores 1 --constraint c6220
# 新池准备 groups>=32、cell=trial 后，先 dry-run 核实，再提交有限32路。
python tools/friday/submit.py /ABS/trial-plan/batch.json --cell trial --cores 32 --dry-run
python tools/friday/submit.py /ABS/trial-plan/batch.json --cell trial --cores 32
```

数组每作业1核/4G，时限65分钟，不 requeue，运行预算默认60分钟，预留退出时间；
可在调用前导出 `FRIDAY_WALL_SECONDS`、`FRIDAY_EPISODE_SECONDS`、`FRIDAY_MAX_GROUPS`。
默认单局上限900秒，剩余时间不足单局上限＋180秒时停止开新局，最后一局排空。
`--cores` 是有限数组的总作业数；并发取请求值、当前 smp 空闲核1/4、200的最小值，
排队超过300个时再限32。每个用户用固定
`/data/<用户名>/.config/spireagent/friday-submit.lock`（父目录700、锁文件600）
串行化“查队列→查空闲→sbatch”。本人还有任意 `spireagent-f2` 排队、运行或
收尾中的作业时拒绝再次提交，包括换配置或换结果目录，避免多个数组叠加突破
上限；先等上一数组排空。此限额是提交当时的快照，后续集群空闲变化不会自动
调整已提交数组，不承诺持续维持动态比例。提交记录实际空闲和排队数，
不把总硬件核数当空闲量。
没有常驻64核默认，也没有自动补队服务；常驻规模要按学习者实测吞吐另定。

```bash
touch /ABS/paired-results/STOP   # 全批停止新准入，当前局按原 Runtime 排空
squeue -u "$USER"               # 看本人的数组
sacct -j JOB_ID -o JobID,NodeList,State,Elapsed,ExitCode
```

有副作用的已开局不通过重新提交恢复。只恢复保存时，在同一原节点、原路径和
准确环境下由原 `EvaluationLedger.run_managed` 恢复既有 `save_pending/save_failed`；
不删除 STOP、租约或 worker 所有权来重开。确认全部自己的局已排空后才取消残余
排队作业；硬取消已开局会留下 unknown，不能算自然终局。

## 产物、失败和接手

`persistent_root/workers/<配置摘要-比较单元-作业号-数组序号>/` 有 `owner.json`、
`result.json`、`snapshot.json` 和 `evidence/`。每局保存排空后 rsync 原字节到该处，
随后完整逐文件 SHA 读回；每次复制字节量与耗时在 `snapshot-costs.json`，
`snapshot.json` 只证明本地暂存到 `/data` 的复制。
轨迹上传及完整冷读回仍看原报告的 `trajectory_preservation` / `preservation`。
终局、unknown、保存失败、关闭是否确认分别保留；首次非完成结果写全批 STOP。

Slurm 日志在 `persistent_root/slurm/`。`result.json` 记录源码配置、原 scratch
路径、作业号、节点、各槽原结果和耗时；错误只记类型/边界错误码，不输出环境变量。
原报告包含执行/关闭/保存分解，详细测量口径沿原保存指南。字节复制和哈希也计入
整批墙钟，不能把局内循环速度当每核实际吞吐。

成功复制也不自动删 `/tmp` 原件。原件中的绝对引用指向当次节点路径；复制用于
保全，不能直接在新节点重写引用继续开局。节点丢失时，持久租约仍证明该种子已
保留给原作业，记 unknown 并核原 Slurm/保存回执，不推断它从未开局；节点丢失前
未同步的临时证据可能无法恢复。清理由本卡按原 R2 读回与未知保留规则单独收口。

资源适配层的便宜回归：在准确开发环境的 `python/` 下运行 `python -m pytest tests/test_friday_actor.py -q`；
该文件由正常 pytest/CI 收集，无第二份工具目录测试。
它检查真实文件复制读回、并发租约、种子池排他和 Slurm 参数，不启动游戏。

测速配置可将 `python_executable` 指向该 release 的 `tools/friday/profile-python`。
它仍调用原 `agent.serve`、原投影和原模型；每次成功完成投影或前向后，原子替换
Runtime 原件目录之外的小型 `agent-performance.json`，不做 fsync。版本、CPU
亲和性和脚本 SHA 每个进程只算一次。原 Runtime 的 SIGKILL 无法执行退出钩子，
但上一次完成调用的原子检查点仍可读；被杀时尚未完成的调用不计入。首次调用前
被杀、节点丢失或首次指标写入失败时仍可能没有指标。SIGTERM/SIGINT 保留原信号
退出状态；可选 IO 失败只报一次有界类型/错误码并关闭后续指标写入，不改变结果。
`observer_prior_checkpoint_writes/seconds` 只统计此前指标检查点的写入次数/耗时，
不能把当前写入或尚未完成的调用补算进去。

Friday 的工程和评估 launcher 默认 `FRIDAY_INFERENCE_THREADS=1`：本轮16份真实
输入的1/2线程对照 argmax 一致，2线程没有实测收益。显式覆盖仍须是正整数。
通用 `profile-python` 的默认值保持2，与原 Agent CLI 相同；launcher 显式传1，
不改变其他平台的默认设置。`measure_host_performance=true` 在本卡 Host rc.58
读取已有 Host 计数器；原始记录与计数器观测分开，嵌套时间不相加成互斥总耗时。

依赖门禁修正在仓库原锁里完成，再导出同锁 CPU 环境：certifi 更新为2026.10.23
（修复原2026.7.22的 GHSA-6q48-6c7w-9gw2），aiohttp 更新为3.14.5、yarl 为1.25.1。
torch、tokenizers 版本不变，不改旧训练/模型 producer、锁或在用环境；这些修正随
新 release 的独立环境交付，旧 `env/` 和 `packages/` 保留。

Friday 的执行配置须显式列出
`adapter_environment_keys=["LD_LIBRARY_PATH","FRIDAY_INFERENCE_THREADS"]`，
让原 Runtime 转交学校 Python 模块的动态库路径和指定线程数；只记录变量名。
Mac 测速只需 `FRIDAY_INFERENCE_THREADS`。缺变量会在开局前拒绝，
AWS/R2 凭据不能通过此项传给 Agent。默认空列表保持原 Mac 路径。

## 固定种子评估也默认 Friday

Managed 打局和固定种子评估都默认 Friday；Mac 只做必要的 Mac 对照。
`tools/friday/evaluate.py` 把评估放在**一个 Slurm 节点、一个原账本**里，默认申请
4核并调用原 `EvaluationLedger.run_managed(workers=4)` 和原保存池；不把多个作业
的可变账本拼在一起。原32路数组仍只用于独立工程吞吐试跑。

评估执行配置可有多个原模型，必须显式 `store="s3"` 和
`retention={"tier":"trajectory","purpose":"evaluation","audit_every":50}`。
默认使用原 `ref-20` 的完整20颗固定种子；3模型得到原账本的60槽，按原规则
事先选2槽完整审计，其余留轨迹。也可传已被原 `load_manifest` 支持的完整固定
T2或检查点清单；不裁剪或更改种子原件，不把工程诊断种子改称评估种子。

```bash
python tools/friday/evaluate.py prepare --execution /ABS/evaluation-execution.json \
  --output /ABS/evaluation-plan --workers 4 --wall-seconds 7200 \
  --selection early=MODEL_SELECTION_1 --selection middle=MODEL_SELECTION_2 \
  --selection late=MODEL_SELECTION_3
python tools/friday/evaluate.py submit --plan /ABS/evaluation-plan/plan.json --dry-run
python tools/friday/evaluate.py submit --plan /ABS/evaluation-plan/plan.json
# run 由该作业的 launch.sh 在 Friday 内调用；不需再次 SSH 登录。
touch /ABS/evaluation-plan/STOP
```

所有模型、源码 producer、Host/Runtime pin、清单、保存用途均在 plan 输入中冻结；
实际分配后由原账本生成准确槽和审计身份，开第一局前把全计划和本次选择写到
`admission.json`。`prepare --limit N` 只冻结本次最多开N槽，先开原规则选定的审计槽；
完整20种子×模型分母不变，未开的槽仍明确保留，不能把冒烟写成整批通过。

评估使用同一个 `spireagent-f2` 作业名和用户提交锁，所以有工程数组或评估未排空时
不能叠加提交。核数必须满足提交当时空闲1/4等门槛，不能满足就拒绝；不偷偷降核或
改回 Mac。默认单局上限900秒、总预算2小时，预留单局与保存退出时间，作业不 requeue。
未知或保存失败时只停止新准入，让已经开始的局由原 Runtime 排空，未知不再开。

进度及原模型、轨迹上传与冷读回仍由原执行链负责；最终原账本报告、所有失败分母、
保存回执在 `result.json` 和 `evidence/`，完整审计没满足时仍是 `score_admitted=false`。
同一个 plan 一旦取得 `owner.json` 不可重新开局；节点或作业失败先核原 scratch、
Slurm 与 R2 回执，只由原保存恢复入口处理，不能删 owner 重投。
此入口的便宜回归在 `python/` 下运行
`python -m pytest tests/test_friday_evaluation.py -q`，不启动游戏。

## 本轮直接接 RL1，不另跑32核一小时

本轮独立32核一小时试跑已取消，吞吐与传输验收并入 RL1 冒烟：Friday 先用
fc430 的6–8核为学校 L40S 学习者供轨迹；D1三模型×20种子评估仍用上节入口。
`submit.py` 的32路工程工具保留，但不作为本轮生产默认。
D1 使用 `T2-MANAGED-A0-SEEDS` 完整40种子清单的前20，**不是默认 ref-20**。
其 PR287 的原账本 `seed_prefix=20` 尚未合入时不得启用；合入后传
`--manifest /ABS/T2-MANAGED-A0-SEEDS.json --seed-prefix 20`，保留完整清单原哈希，
另冻本轮3模型×20种子60槽。模型 ID 尚未产出，因此这里没有宣称比较已运行。

`tools/friday/rl.py` 只启动原 `python -m spireagent.workbench.rl_actor`，不实现游戏循环。
它沿原 Exchange 的同一 R2 `run/peer` 交换模型版本和轨迹记录；不会复制或导入 RL1
未合并的私有源码。`prepare` 可在模块尚未合入时冻结计划；`submit/run` 要求原
`rl_actor.py`、`rl_exchange.py` 已存在于本 release 的真实模块路径，否则开局前拒绝。

```bash
python tools/friday/rl.py prepare --execution /ABS/Friday-execution.json \
  --learner-config /ABS/RL1-run.json --output /ABS/Friday-RL-plan --actors 8
python tools/friday/rl.py submit --plan /ABS/Friday-RL-plan/plan.json --dry-run
python tools/friday/rl.py submit --plan /ABS/Friday-RL-plan/plan.json
```

输入必须是物理绝对路径，candidate 不可为符号链接；执行配置必须 `store="s3"`、
`trajectory/training/audit_every=50`，peer 必须与学习者完全相同。默认从原 RL1 run
配置取得 behavior 和 seed_namespace；若给单独的 learner 配置，须另传
`--behavior /ABS/behavior.json --seed-namespace 原命名空间`。不改行为策略参数。

每个数组项单核、唯一 actor 名、fc430，默认8路；学习者提速后可显式调到25–40路，
仍受空闲核1/4、绝对200核和拥堵时32核的原门槛约束。复用原用户提交锁，不能叠加数组。
每个 actor 一局一个原账本，并通过原 `unknown_stop` 配置在首个 unknown 后自停。
工作目录在节点私有 `/tmp`；原 Actor 上传与读回轨迹后，退出时将原摘要、日志及工作
目录完整复制到计划目录的 `actors/<actor>/evidence/` 并逐字节读回，owner 租约不重投。
这证明资源适配和原交换物，不自动证明 learner 已实际训练，后者看学校学习者收据。

**现有接口缺口：** RL1 没有跨 actor 的即时 unknown 停新准入接口。本适配不补游戏
控制或新监控；只在某 actor 退出后发现 unknown 时写共享 STOP，阻止尚未启动的
数组项。其他已运行 actor 不会因此立刻停止下一局。计划和报告明确保留
`cross_actor_immediate_unknown_stop=false`；需要更强保证须由 RL1 原 owner 提供接口。
Slurm 停止信号仅通过原 actor 工作目录 STOP 让本 actor 在一局账本后排空，不重试未知。

便宜回归在 `python/` 下运行 `python -m pytest tests/test_friday_rl.py -q`；
替代的只有 Slurm/Actor 进程边界，不用私有 RL1 源码、不启动游戏或学习者。

## Slurm 分配进入原 E2 占位入口

每个单核 Slurm 作业是独立资源域；单节点多核评估在同一域中登记 `workers` 路。
三个 launcher 都经过 `tools/friday/allocation.py`，在 `/tmp` 建700的私有占位目录，
设置 `SPIREAGENT_SLOT_ROOT`，再正常调用原
`node tools/resource-slot.mjs run --resources managed --card F2-Friday --lanes N
--games M --until ISO -- 原Python命令`。批次/RL 数组每项登记1路，评估登记实际
workers 路；局数为冻结计划或本作业预算上界，截止取现有墙钟预算加300秒收尾。
这只映射资源分配，不替代原 Managed admission，也不改 Mac 的全局占位目录。
原 E2 生成、传递和释放 token，封套不读、不造、不写 token。

结束后仅把原 `GAME-SLOT.md` 复制到 `/data` 的该批 `slurm/` 日志目录，逐字节读回；
同目录的 `*-allocation.json` 记录资源路数、局数上界、截止、原占位目录、信号和
E2 命令真实退出码。含 token 的原占位文件不复制；若原 E2 收尾不明，它仍在该
私有目录保留原状态，不能伪称释放或手工清锁。

Slurm 提前 USR1 由封套按私有 PID 元数据传给原 Python 命令，继续用已有停止准入/
当前局排空逻辑；不把 USR1 发给 Node，避免误开 inspector。原 E2 Node 在这段
排空期间仍持槽，待原 Python 正常退出后由原 E2 收齐进程树并释放。
真正 TERM/INT 仍转交原 E2，它约等1秒后可能强杀未退出子树；这是取消路径，
不能称为正常排空，未知与异常原件仍按原规则保留，不自动重试。
RL 某 actor 最终摘要出现 unknown、save_failed 或失败无分片时，在慢速快照前
写共享 STOP 挡未启动项；即时跨 actor 停止的接口缺口仍按上节如实保留。

`python -m pytest tests/test_friday_allocation.py -q` 使用原 E2 CLI 和原 Python
`require_managed_slot` 进行无游戏检查，核 USR1 到原 Python、排空期间 Node 存活、
最终真实退出码及原占位释放。它不证明游戏结果或 RL 学习效果。
