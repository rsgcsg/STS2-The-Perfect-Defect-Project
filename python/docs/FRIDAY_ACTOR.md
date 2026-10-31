# Friday 打局与评估运行手册

本机容量检查同时看文件系统和个人配额：Friday登录节点的`usage`显示`/home`与`/data`
实际used/quota；`df -h`的共享磁盘空闲不能替代它。已发生过`/data`个人500GB用满而
文件系统仍空闲8TB的情况。开跑前按预计保留量核余量，并在本卡目录做写入/回读/fsync。
需要本轮使用共享scratch时，用实际canonical路径`/data/scratch/$USER/<本卡目录>`，
确认本人权限和现场写入，将计划/日志/结果与`TMPDIR`放到已核目录，提交时显式
`SBATCH_EXPORT=ALL`使原launcher继承；原source/env可继续只读复用。故障旧现场、未决局
和检查点保留，新worker用新名字/种子，不重投旧unknown；收尾按原Store/保全流程归档。

新 Managed 打局、RL actor 和固定种子评估默认放 Friday；Mac 只做必要的 Mac 对照。
连接或排队失败不会自动退回 Mac。这里复用原账本、Runtime、保存池和
[轨迹格式](MANAGED_PRESERVATION.md)，不新增游戏循环。

## 部署一次，以后在 Friday 内开作业

部署到 `/data/<user>/friday-actor/releases/<Git SHA>/`：干净源码在 `source/`，
准确包在 `packages/host`、`packages/runtime`，专用环境在 `env/`。旧组合保留；
不在已投产 release 上重建环境。游戏、SDK和原模型可复用既有私有原件，执行配置
中的 candidate、Host包和模型目录必须用物理绝对路径，candidate 不可为符号链接。

```bash
R=/data/$(id -un)/friday-actor/releases/EXACT_GIT_SHA
F2_ROOT="$R" sbatch --partition=smp --cpus-per-task=1 --mem=4G --time=00:20:00 \
  --no-requeue "$R/source/tools/friday/bootstrap-environment.sh"
```

这一条命令固定 Python3.11.5、CPU Torch2.13.0+cpu、tokenizers0.22.2、
.NET SDK9.0.117和 Node22.23.4；下载校验及实际环境记录在 `evidence/`。
依赖来自原 `python/uv.lock` 的 CPU 导出。launcher 必须找到该 release 的
`node22.23.4/bin/node`，不回退到学校旧 Node20.9。

游戏只留自己的 `/data` 私有布局，目录700、文件600，每次传输后实查。
R2令牌由用户本人放 `/data/<user>/.config/spireagent/friday-r2.env`，文件600、
父目录700；正常 launcher 加载，Agent 不查看、打印或复制。登录密码和手机确认
由用户完成；共享SSH有效期间无需重复登录，已提交作业不依赖 Mac 在线。

执行JSON仍用 `spireagent/managed-evaluation-execution-v1`，准确冻结 producer、
Host/Runtime pin、adapter、initial_progress、候选、模型与 peer。Friday 指定
`game_assembly_sha256=71eec3ba5e0c640fc0cf0758517faf5f2ff039bea21966b22b23f2c427c7a09f`，
并声明 `adapter_environment_keys=["LD_LIBRARY_PATH","FRIDAY_INFERENCE_THREADS"]`；
只转变量名，不能转AWS/R2凭据。实际身份仍由原Host完整审计。

## RL：本轮只准备配置，不提交

本轮只完成PR283普通合并后的准确 develop 部署、RL配置准备和本手册收口。
不启动学习者、不运行真实RL闭环、不提交RL打局作业。以RL1交接段落“转给F2的
一段话”和本轮用户明确参数为准：run=`rl1-real-1`，每小账本2局、单局900秒、
actor墙钟3300秒、最多1000局，behavior=`temperature_uniform`、T=1.0、uniform_mix=0.03。
需要已合入本 release 的原 `rl_actor` / `rl_exchange` 模块，以及同一R2 peer 的
`store="s3"`、`trajectory/training/audit_every=50` 执行配置。

```bash
export PYTHONPATH="$R/source/python"
"$R/env/bin/python" "$R/source/tools/friday/rl.py" prepare \
  --execution /ABS/Friday-execution.json --learner-config /ABS/learner-school.json \
  --output /ABS/Friday-RL-plan --actors 8 --seed-namespace rl1-real-1 \
  --games-per-ledger 2 --episode-seconds 900 --wall-seconds 3300 --max-games 1000
# 只准备；本轮不调用 submit，也不启动任何Actor/学习者。
```

run和peer跟原学习者配置，不另建交换链；行为显式配置保留原值，缺省为T=1/0.03。
`games_per_ledger` 允许原接口的1–10，max_games最多1000；不额外注入F2的首unknown
停止策略。unknown连续3个或至少8局后比例超过25%的默认处理、以及任何ledger stop，
均沿原Actor。owner不重投与失败结束后共享STOP的既有保全仍保留。

未来另获启动授权后，每项单核、唯一actor、fc430、默认1推理线程，调用原
`python -m spireagent.workbench.rl_actor`；由原Actor在小账本之间更新模型。
Slurm提前USR1预留 `games_per_ledger × episode_seconds + 180` 秒（本配置1980秒）。
**原owner边界缺口：** 当前 `_signal` 只标记signalled，`run` 在整个小账本结束后
才检查，所以2局配置可能在收到信号后仍完成该账本的第二局；不能承诺“仅当前局”。
适配层同时保留STOP并把USR1直接传给持有句柄的原Actor，不私改RL1账本中途停止语义。
真正TERM/INT仍走原E2取消路径，不称正常排空。

## D1：三模型 × T2原清单前20种子

执行配置列三份原模型，保存用途改为 `trajectory/evaluation/audit_every=50`。
D1传完整 `T2-MANAGED-A0-SEEDS.json`，用原账本 `seed_prefix=20` 冻结新的60槽，
原40种子清单及哈希保留；必须有提供该API的PR287。不是默认 `ref-20`。

```bash
"$R/env/bin/python" "$R/source/tools/friday/evaluate.py" prepare \
  --execution /ABS/D1-execution.json --manifest /ABS/T2-MANAGED-A0-SEEDS.json \
  --seed-prefix 20 --output /ABS/D1-plan --workers 4 \
  --selection min=MODEL_MIN --selection last=MODEL_LAST --selection position=MODEL_POSITION
"$R/env/bin/python" "$R/source/tools/friday/evaluate.py" submit \
  --plan /ABS/D1-plan/plan.json
```

三模型在一个节点的同一原账本内运行，原规则事先抽2槽完整审计并先调度，其余留轨迹。
`--limit` 只限本次开槽数，不裁计划分母。模型ID未齐就不启动；D1自己提交并收齐60槽，F2只交准确部署版本入口；完整审计不满足时
仍是 `score_admitted=false`。不传manifest时保留原完整 `ref-20` 入口。

## RL3：100 个永久排除训练的学习曲线种子

`stpd.evaluation.suites.rl3_learning_curve_manifest(game_version)` 生成版本化清单
`managed-rl3-learning-curve-100-20261030-Defect-a0`：Defect、A0、Managed，序号1–100。
传入实际游戏版本，将正常 JSON 序列化结果交上面的 `evaluate.py prepare --manifest`；
原 reader 校验完整清单及哈希，不接受改过的种子、角色、难度或 Host。
`seed_membership` 和 `require_non_evaluation_seed` 永久登记这100个种子，
actor 的 `diagnostic_reference_manifest` 碰到保留种子直接拒绝。

这套清单用于模型选择和学习曲线；原 T2 40 个种子（含预留确认20个）保持不变。
结果仍须原账本完整审计、轨迹保全/读回后才能入账；选最好模型还需独立确认。
注册不会升级已冻结的旧 actor；下一轮训练必须采用包含该登记的研究源码。

## 停、查、恢复

```bash
scancel --signal=USR1 --batch JOB_OR_ARRAY_ID  # 原Python停新准入；RL排空当前小账本（默认2局）
squeue -u "$USER"
sacct -j JOB_ID -o JobID,NodeList,State,Elapsed,ExitCode
```

每个Slurm分配经 `allocation.py` 正常进入原E2 `resource-slot.mjs run`，在私有
`/tmp` 资源域登记真实路数；原E2生成、传递和释放token，Mac全局占位不变。
提前USR1由私有PID路由给原Python，Node仍持槽；真正TERM/INT走原E2取消，约1秒后
可能强杀子树，不能称正常排空。原无token日志和 `*-allocation.json` 在 `slurm/`。

运行证据在节点私有 `/tmp`。原执行器负责R2上传和完整冷读回；RL退出后把原摘要、
日志及工作集完整复制到 `plan/actors/<actor>/evidence` 并逐文件读回，评估在
`plan/evidence`。看 `result.json`、原账本报告、preservation回执和Slurm日志；
学习者是否实际使用分片看学校学习者收据，不能从上传成功推断训练完成。

owner/租约取得后不可重投；unknown、save_failed及异常保留原状态和分母，不重打。
RL的未知阈值与任意账本停止沿原Actor；结束摘要发现失败会先写共享STOP挡未启动项；
原接口没有跨actor即时STOP，已运行的其他actor不会因此立刻停下一局，计划明确记录
`cross_actor_immediate_unknown_stop=false`。全批排空用上面的USR1命令。
节点丢失前尚未同步的临时前缀可能无法恢复；核原Slurm、租约、scratch与R2回执，
不能据无记录推断未开局。保存恢复沿原账本入口，不删STOP或owner重开。

独立工程批次仍可用 `prepare.py` / `submit.py`，只收原非评估工程种子，不能称RL训练。
测速 `profile-python` 仍调用原Agent，记录已完成的投影/前向和自身写入成本；指标原子
保存但不fsync，SIGKILL时在途调用不补算。原始证据和比较规则不改。

## RL2：冻结运行包上方的长任务调度

RL2 授权后可用更新的 `tools/friday/rl.py prepare --runtime-source <冻结release/source>`
准备 12–15 路、最长 79200 秒（22 小时）的作业；`--wall-seconds 75600 --max-games 10000` 给约20小时
训练及排空留余量；10k仍是有限局数上限，避免快局在20小时墙钟前撞到旧1000局上限。`submit` 仍调用原 Slurm、单核、fc430、容量、永久 owner 与 USR1
路径。调度工具与实际 producer 分开：`source_root` 必须指向原干净冻结源码，执行配置
producer、Host/Runtime、游戏包不变。调用更新工具的 actor prepare/submit 时
`PYTHONPATH` 指向冻结源码的 `python`，原 `require_modules` 仍拒绝模块来源漂移。

评估 `submit` 的互斥改为准确计划路径对应的 `spireagent-eval-<hash>` 作业名，加上
原 owner/STOP 检查；因此同一训练的演员和不同评估计划可以并行，相同计划不能重投。
`prepare --runtime-source` 同样把评估真正执行的源码固定在原 release；不把调度工具
提交号冒充已部署 Managed producer。

### 一个1核看守作业

先实际核 `scontrol show partition smp` 和容量；在计算节点已有获准分配中用
`sbatch --test-only --partition=smp --cpus-per-task=1 --time=00:01:00 --wrap=true`
核是否接受节点内提交（仅调度验证、不启动游戏）。若站点禁止节点内提交，配置
`evaluation_mode="direct"`：评估作为同一1核分配内子进程运行，主看守仍观察并止损；
不另开服务，不依赖 Mac。允许时用 `submit`，评估另占默认4核。

配置（所有路径为真实绝对路径；peer 是非秘密 R2 绑定，不含凭据）：

```json
{
  "schema": "spireagent/friday-rl-watch-config-1",
  "run": "rl2-real-1",
  "peer": {"kind":"s3","bucket":"BUCKET","endpoint":"https://ENDPOINT","prefix":"stpd","region":"auto"},
  "actor_plan": "/data/USER/rl2/actors/plan.json",
  "actor_job_id": "ACTUAL_ARRAY_JOB_ID",
  "runtime_source": "/data/USER/friday-actor/releases/445b9f980364baca7fe1f77dedb09d3211557c6b/source",
  "evaluation_execution": "/data/USER/rl2/evaluation-execution.json",
  "evaluation_manifest": "/data/USER/rl2/T2-MANAGED-A0-SEEDS.json",
  "started_at": 0,
  "wall_seconds": 72000,
  "poll_seconds": 900,
  "evaluation_seconds": 18000,
  "evaluation_mode": "submit",
  "evaluation_workers": 4
}
```

`started_at` 必须填真正起跑的 Unix 秒。看守校验 run/peer、冻结源码和 actor 提交号；
相同 work 只有一个进程持锁，恢复只读原状态，不重投结果未知的评估。凭据由用户私有文件
通过原 launcher 同样的无回显方式加载。单独的看守 shell 最后执行更新源码里的
`python tools/friday/watch.py --config /ABS/watch.json --work /ABS/watch-work`，
其 `PYTHONPATH` 指向含 RL2 Exchange STOP/events 和 TensorBoard 的更新 `python`。
用 `sbatch --partition=smp --cpus-per-task=1 --mem=4G --time=22:00:00 --no-requeue`
提交该 shell，日志放本任务私有目录。看守源码无需替换 frozen runtime。

约每15分钟将只增状态写到 `exchange/rl/<run>/status/<时间>.json`：最新模型、过去一小时
公布局数、训练 unknown 分母、分片积压、最新 learner 事件和原评估结果。每5小时取最新
已发布模型，核模型包后走原 `evaluate.prepare/submit`（固定原40种子前缀20、原审计账本）。
不完整评估、未过原审计门或 unknown 都保留原状态，不把无分数的结果当退化证据。
unknown 训练总体严格超过5%、连续两次完整评估平均层严格低于11.75、learner failed/
非有限数值、或20小时期限，会写原 actor STOP 并 USR1 排空，同时通过 R2 请求 learner
正常发最后版本和完整检查点。`stop_requested` 是请求证据，实际退出和 checkpoint 还需
核原收据。原配置没有“现有KL阈值”；不把 KL 系数当阈值。若另获具体阈值可显式填
`kl_stop_threshold`。连续3次评估无提高只报告，不自停。

固定20颗出现首胜后，看守只触发一次同一获胜版本的独立完整T2原40颗确认账本；
全部40颗均训练禁用，其中前20重复原评估、后20为新增评估种子，不称40颗全部新种子。
确认使用1核direct、最长3小时，不延长20小时总时限；截止未完成保留完整分母。
确认结果另标 `confirmation`，不进入固定20的退化或无提高序列，也不重置5小时评估时钟；
不能将20颗里的单胜直接宣称达到L1。调度/评估失败立即写 `attention`，不重试 unknown
或申请结果未知的作业。看守是有限作业，退出不会自动重启。

20小时停止后，看守继续最多40分钟观察actors、learner末检查点和评估子进程退出。
R2不可达时遥测失败记stderr，原actor本地STOP/USR1仍按20小时执行；learner的同一停止
请求保留待写并在收尾内重试，超时本地drain-status.json明确未确认，不称已经完成。

## 长跑评估分布与定时提交

[只读报告与下一轮操作](RL_OBSERVATION.zh-CN.md)复用本页原评估入口：
同种子起点/上一轮比较、逐层图与缺失分母；独立多核submit、可选Slurm `--begin`，
以及新看守的评估截止唤醒。源码合并不更新运行中的RL节点，采用时记录新部署。
