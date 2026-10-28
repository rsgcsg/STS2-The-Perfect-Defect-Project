# Friday CPU 打局

Friday 是新打局批次的默认 CPU 目标。Mac 只在明确指定的对照或诊断中运行；
连接或队列失败不自动退回 Mac。这里复用 Managed 的账本、Runtime、保存池与
[轨迹格式](MANAGED_PRESERVATION.md)，不新增游戏执行器。A 段提供固定模型的
有限批次；RL 的最新模型指针和学习者收片接缝，待 RL1 的接口合并后接入，不能
把这份工程批次称为 RL 训练或数据资格。

## 固定环境和输入

只在自己的 `/data/s4961270/friday-actor` 部署，目录700；游戏文件600、目录700，
不放镜像或共享 scratch。节点本地 `/tmp` 只写证据和运行日志，不复制游戏。
环境重建入口为 `tools/friday/bootstrap-environment.sh`（Slurm 单核作业内一条命令，
固定 Python3.11.5、.NET SDK9.0.117、CPU torch2.13.0+cpu、tokenizers0.22.2）；执行源码固定 Git 提交，
Host/Runtime 固定包身份，候选固定 SHA/MVID，模型固定原 Store 身份和包 SHA。
每次传输后在远端实查权限，不能只信 rsync 参数。

运行者使用 `spireagent/managed-evaluation-execution-v1` 原配置：准确 `producer`、
`host_pin`、`runtime_pin`、`agent_adapter`、`initial_progress`、`candidate_directory`、
`models`、`peer`、`python_executable`。模型只填一份。另声明：

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
放入 `/data/s4961270/.config/spireagent/friday-r2.env`，文件600，父目录700。
文件设置 `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`；非秘密桶、endpoint、prefix
从固定 `peer` 读取。`worker.sh` 在执行时加载用户文件，Agent 不查看或复制该文件。
登录只让用户输密码和手机确认；共享 SSH 连接有效期间不重复登录。

## 起、停、调核数

先以 `prepare.py` 冻结非评估种子池。它只接受现有的工程诊断组，每组最多10种子；
逐种子调用现有 `require_non_evaluation_seed`。同一池中种子不得重复。
配对比较的 `c6220/fc430/mac` 是显式独立比较单元；普通32路数组统一用 `trial`，
在同一共享租约根中互斥取组。租约写入后不自动过期、不回收、不重投未知。

```bash
export PYTHONPATH=/data/s4961270/friday-actor/source/python
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
排队超过300个时再限32。提交时记录实际空闲和排队数，不把总硬件核数当空闲量。
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

资源适配层的便宜回归：在准确开发环境中运行
`PYTHONPATH=python python -m unittest discover -s tools/friday -p test_batch.py -v`。
它检查真实文件复制读回、并发租约、种子池排他和 Slurm 参数，不启动游戏。

测速配置可将 `python_executable` 指向 `tools/friday/profile-python`，它调用原
`agent.serve`、原投影和原模型，只记录投影/前向累计秒数，退出时写一次
`agent-performance.json`。`FRIDAY_INFERENCE_THREADS` 显式选择正整数，默认2与
原 Agent CLI 一样；单核1线程配置须经相同种子和结果对照后选用。
`measure_host_performance=true` 在本卡 Host57 读取已有 Host 计数器，
原始记录与计数器观测分开；不把嵌套时间加成互斥总耗时。
