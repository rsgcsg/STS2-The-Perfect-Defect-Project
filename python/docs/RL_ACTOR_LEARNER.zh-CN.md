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
- 奖励 v2（用户 10-29 批）：胜 1；负 = 0.5·min(层/终层,1)² + 0.1·已证 Boss 数
  （无见证为 null 记 0），无 0.99 上限。A0 固定 48 层 / 3 Boss，A10 固定 49 层 / 4 Boss。
  最终 Boss 击败对应自然终局 win，直接得 1；不根据层数或 Boss 数重标原始终局结果。
  v1 的线性进度与 0.99 上限只属于旧 producer/模型训练身份，原记录保留不改写。
  切换 v2 必须另用新源码和新 run；不能用 v1 的完整检查点跨源码续训。
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

## 长跑曲线、远端状态与停止

学习者配置 `tensorboard_steps` 默认 10，每 10 次优化器更新记 policy/anchor loss、KL、
clip 比例、ratio、梯度范数、缓冲区大小和该批样本的平均/最大版本陈旧度；0 显式关闭。
训练环境安装 `ml` extra（含 PyTorch 官方 `SummaryWriter` 所用的 TensorBoard）。
每次检查点完成及退出时关闭当前 event 文件，用原 BlobStore 的 `put_if_absent` 写入
`exchange/rl/<run>/tb/events.out.tfevents.<内容哈希>`，下一段另开新文件，只增不覆盖。
训练和评估文件位于同一目录，横轴是累计更新数；分配续训保留原累计更新数。
本地从 R2 下载该 `tb/` 前缀后运行 `tensorboard --logdir <下载目录>` 查看；只下载新键即可。

Friday 看守完成一次原评估账本后，调用：

```bash
python -m spireagent.rl_tensorboard --peer-config peer.json --run <run> \
  --evaluation evaluation-metrics.json --work <节点暂存目录>
```

`peer.json` 是原 peer 配置（只引用环境变量，不含凭证）；`evaluation-metrics.json` 字段为
`version`、该版本的累计 `updates`、`wins`、`mean_max_floor`（无有效局时为 null）、
`unknown`，可选实际完成时间 `evaluated_at`（Unix 秒）。评估计数来自原账本，不重试 unknown。
可选 `evaluation_kind` 默认 `fixed20`，另可用 `confirmation` 将胜局确认批写入独立
`confirmation/*` 曲线，避免与固定 20 局的 `evaluation/*` 混用；`planned` 记录该批原计划分母。
此入口只做曲线，不提交评估、不改训练用途、不替代原评估证据。

学习者每次 progress、版本/检查点发布、结束时把原字段和 `counts`、`seen_shards`、
`version`、`updates`、`buffer_size`、平均/最大陈旧度、计时写入只追加
`learner-events/<时间纳秒>-<唯一值>.json`；等数据时约每 30 秒写 `waiting`。
`Exchange.latest_event()` 只读最新条，`event_records()` 读全部历史。正常退出 `finished`；
异常最后一条 `failed` 保留 `error_type/error_code`，不把异常伪装成完整检查点。

看守通过 `Exchange.request_stop(reason)` 在同一 peer 创建唯一的 `control/stop.json`
（`spireagent/rl-stop-request-1`，绑定 run、原因、时间及记录哈希）；第一个请求生效，不能撤销。
学习者每轮 poll 前检查它，沿原 STOP 正常退出路径发末版本和完整检查点。仍需从实际
`finished` 和检查点记录确认收尾完成；请求写成功不是已经停止。读写或校验失败明确报错。
数值损失目前检查非有限数值；`kl_coef` 是损失系数，源码没有独立 KL 停止阈值。

## D2 可选学习者执行（默认关闭）

省略学习者配置的 `performance` 或写 `null` 时，仍走原更新实现。实验必须新建 run，
冻结准确源码与配置，不在运行中的学习者或旧检查点上替换。它不改变 actor 包里的
执行计划；训练时暂选的实现会在更新结束或异常时恢复，导出沿原包执行身份。

```json
"performance": {
  "packed": true,
  "tensor_cache": true,
  "microbatch_size": 8,
  "scene_attention_area": 262144,
  "text_attention_area": 262144,
  "bucket": false,
  "defer_sync": true,
  "precision": "fp32",
  "tf32": false
}
```

`packed` 复用已有完整 packed 计算；`tensor_cache` 只缓存不含权重、padding 或随机数的
Input-v2 编译结果，退役样本随缓冲区移除。微批仍限 1–8；面积限制决定何时拆微批，
不能截断超大样本。`bucket` 只按同一次优化器更新已选中的样本实体数排序，不换成员、
不增加复用；它会改变 padding 和 dropout 抽样次序，必须另做质量对照。

可选执行路径将损失计算保持 FP32，并集中回读指标；`defer_sync` 还把逐微批分数检查
延后到优化器更新前，非有限分数、损失或梯度仍拒绝更新。参考模型始终保持 eval，
缺失参考分布整微批一次回读，后续复用原缓存。

`precision` 只允许 `fp32` / `bf16`；bf16 必须支持它的 CUDA，明确失败而不回退，
并须核损失、梯度、KL/clip 和正式评估后再决定采用。TF32 只在 `tf32=true` 时临时打开
矩阵乘法开关，并在退出时恢复；该 Torch 开关是进程全局，因此只能用于独立学习者
进程，不能与其他需要不同精度的推理/训练共进程。增大 RL 更新批量仍用原
`batch_size` / `anchor_batch`，属于另一个待验证的算法配置，不是微批提速开关。
