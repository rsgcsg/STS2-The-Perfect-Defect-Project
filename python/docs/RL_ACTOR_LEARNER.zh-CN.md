# RL1 打局者/学习者（Managed，A0 Defect）

**范围：** 第一轮 RL 代码与小冒烟。只用 Managed；不开真游戏；训练局不进评估，训练种子来自
`diagnostic_reference_manifest`（非评估命名空间）。正式长跑另发卡。

## 链上位置

```
学习者(stpd.rl.learner，入口 spireagent.workbench.rl_learner) ──模型版本──▶ peer ◀──轨迹分片记录── 打局者(spireagent.workbench.rl_actor)
        ▲   读分片/人类锚点                     原 run_managed + PipelinedManagedExecutor + trajectory 档
```

- 打局者只用 CPU，复用原账本、执行器、轨迹档；不另写游戏循环。每个小账本开局前取已后台
  下载并核过哈希的最新版本，从不等学习者。
- 学习者有数据就训练；每样本最多复用 `max_reuse` 次；比当前版本旧 `max_staleness`
  版以上的局丢弃并计数；读分片、发布版本、检查点（D1 `CheckpointPublisher`）都在后台。
- unknown/截断局照样发记录、计数，不重试、不删证据；打局者连续 3 个或超过 25% unknown 自停。

## 两个交换物（`spireagent/rl_exchange.py`，都在同一 peer，路径 `exchange/rl/<run>/`）

| 交换物 | 键 | 内容 | 校验 |
|---|---|---|---|
| 模型版本 `spireagent/rl-model-version-1` | `models/<版本6位>.json` | version、parent_version、model_artifact_id、model_sha256、origin、training（更新数、用到的分片记录、样本数） | 记录自带 `record_sha256`；版本绑定原 `model` artifact 的 weights 哈希；打局者下载后再核 weights 与 package 哈希 |
| 轨迹分片记录 `spireagent/rl-trajectory-shard-record-1` | `shards/<actor>/<序号8位>.json` | shard_artifact_id（无分片则 null）、run_ids、model_version、model_artifact_id、每局 seed/状态/原因/耗时/行为策略、finished_at | `record_sha256`；run_ids 必须等于分片 manifest；分片本体仍走原 trajectory 读回校验 |

两者都不可变、只追加；没有可改的 latest 指针，最新版本 = 最大键。本地目录和 R2 只差 peer 绑定。

## 行为策略与奖励

- 只在 `trajectory/training`（不计分）档允许抽样：`q = (1-ε)·softmax(logits)^(1/T)/Σ + ε/N`，
  T∈[1,1.3]；每局种子 = 该局 intent_id；每个决定的抽签 = sha256(种子, consumption_id)，
  重放/重读同一决定结果相同。轨迹按同一函数从记录的 logits 重建 q。
- 奖励：胜 1；负 = 0.5·min(层/终层,1) + 0.1·已证 Boss 数（无见证为 null 记 0），上限 0.99。
- 优势：蒙特卡洛整局回报减滑动均值/标准差基线；不用 T2 值头。
- 损失：PPO clip（ratio = π/μ，μ 为记录的行为概率）+ KL 到起点模型 + 人类锚点交叉熵。

## 一条命令（单机）

```bash
npm run slot -- run --resources managed --card <卡> --until <ISO> --lanes <N> -- \
  <python> -m spireagent.workbench.rl_run --config run.json --work <工作目录>
```

`run.json`（`stpd/rl-run-1`）：`run`、`peer`、`seed_namespace`、`wall_seconds`、
`learner`（`stpd/rl-learner-config-1` 字段）、`actor_defaults`（执行文件、behavior、
`games_per_ledger`、`episode_seconds`、`max_games`）、`actors`（每个打局者一个名字），
可选 `cloud_environment`（私有凭据文件，只传子进程）。结束写 `time-ledger.json`。

执行文件必须由与运行源码完全一致的干净 checkout 生成（producer 校验）；运行中不要改这份
checkout，建议从单独冻结的 worktree 跑。

## 加一台打局机器 / 把学习者挪走

- **加打局者：** 在那台机器上用同一 `run`、同一 peer（R2）写一份 actor 配置
  （`spireagent/rl-actor-config-1`，唯一 `actor` 名、本机执行文件），运行
  `python -m spireagent.workbench.rl_actor --config actor.json --work <dir>`。
  不需要改学习者。Friday 打局机器的打包/租约/环境归 F2。
- **挪学习者：** 只改学习者配置的 `device`（`cpu`/`mps`/`cuda`）和机器；peer 改成 R2。
  学习者先发布版本 0。

## 续训（换机器 / 学校 5 小时分配用完）

- 每发布一个版本，后台同时发一个**完整检查点**（`checkpoint` artifact，
  `stpd/rl-learner-checkpoint-2`：权重、优化器、torch/抽样随机状态、更新数、计数、基线回报、
  已读分片键、缓冲区每个样本的分片/复用次数/优势），并在 peer 上写只追加记录
  `exchange/rl/<run>/checkpoints/<版本6位>.json`。
- 学习者到 `wall_seconds` 或 STOP 正常结束时，把最后几步也发成一个版本 + 检查点，不丢更新。
- 接着训：同一 `run`、同一 peer，在学习者配置里**显式**写
  `"resume_checkpoint": "<检查点 artifact id>"`（从最新检查点记录读出），不自动挑。
  续训校验 run、起点模型哈希、producer（必须同一份源码）都一致；缓冲区从原分片读回重建，
  缺样本就拒绝。同一 run 已有版本但没写 `resume_checkpoint` 会拒绝。
- 打局者不用停：它们一直取最大版本号；续训后的第一个新版本 parent 指向检查点版本。
