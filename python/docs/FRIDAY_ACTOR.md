# Friday 打局与评估运行手册

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
