# RL3：一份配置，顺序启动原学习者、打局端和看守

先填完整配置并定时长，冻结后只需执行生成的启动脚本。
模板没有获批，也没有默认训练时长；准备命令不提交任何作业。
这只是已有入口的接线，不新增训练/游戏循环，不授权长跑或方法实验。

## 条件与原入口

- 运行方先在共享 GAME-SLOT 登记；用户录制优先。实际actor仍走原
  `rl.py → allocation.py` Managed资源位，不由本工具绕开登记。
- 在已经部署、干净且提交过的 Friday source 上执行。独立CUDA环境是已有物理目录，
  不能是符号链接，也不能指向CPU actor环境。工具不安装依赖，不修改任何环境。
- 凭据由用户置备。生成脚本复用 `evaluate.launcher()` 的原exec前前缀，沿
  [Friday部署指南](FRIDAY_ACTOR.md)和
  [长跑运行规则](RL_LONG_RUN.zh-CN.md)的正常私有环境加载；准备过程不读凭据，
  不给凭据新增配置项。GPU learner用明确的独立CUDA解释器，`device=cuda`失败就失败，
  不降到CPU。
- 模型、完整CP、交换、actor和评估仍归原 `Exchange`、`rl.prepare/submit`、
  learner CLI、watch CLI。评估两档与报告规则见[观测指南](RL_OBSERVATION.zh-CN.md)。

## 配置模板

下面的`CHOOSE_*`须换成已核的非秘密输入；R2 peer字段照已有正常配置，
anchor只能引用原TRAIN输入。参考配置沿RL2：batch64/anchor32、lr1e-5、KL0.02、
anchor系数0.5、每8更新发版、过期8版本、最多复用3次、12actor、温度1/均匀混合3%。
优化默认关闭。起点与KL参照分别填写；新run不续旧完整CP，重置optimizer/buffer/计数。

```json
{
  "schema": "spireagent/friday-rl-recipe-1",
  "approved": false,
  "wall_seconds": null,
  "runtime_source": "/ABS/frozen-release/source",
  "cuda_environment": "/ABS/independent-cuda-environment",
  "gpu": "a30",
  "actors": 12,
  "execution": "/ABS/training-execution.json",
  "seed_namespace": "CHOOSE_NEW_TRAINING_NAMESPACE",
  "actor_budget": {
    "episode_seconds": 900,
    "games_per_ledger": 2,
    "max_games": 10000
  },
  "behavior": {
    "mode": "temperature_uniform",
    "temperature": 1.0,
    "uniform_mix": 0.03
  },
  "learner": {
    "schema": "stpd/rl-learner-config-1",
    "run": "CHOOSE_NEW_RUN",
    "peer": {
      "kind": "s3", "bucket": "CHOOSE_BUCKET",
      "endpoint": "https://CHOOSE_ENDPOINT", "prefix": "CHOOSE_PREFIX", "region": "auto"
    },
    "start_artifact_id": "CHOOSE_START_MODEL_ARTIFACT",
    "start_model_sha256": "CHOOSE_START_WEIGHTS_SHA256",
    "reference_artifact_id": "CHOOSE_KL_REFERENCE_MODEL_ARTIFACT",
    "reference_model_sha256": "CHOOSE_KL_REFERENCE_WEIGHTS_SHA256",
    "resume_checkpoint": null,
    "device": "cuda", "threads": 4,
    "lr": 0.00001, "weight_decay": 0.0,
    "batch_size": 64, "anchor_batch": 32,
    "updates_per_version": 8, "max_staleness": 8, "max_reuse": 3,
    "baseline_window": 64, "min_episodes_before_training": 4,
    "poll_seconds": 5, "max_versions": 10000, "seed": 1,
    "tensorboard_steps": 10,
    "performance": null, "background_io": false, "exchange_poll_seconds": 0,
    "anchor": {
      "peer": {
        "kind": "s3", "bucket": "CHOOSE_BUCKET",
        "endpoint": "https://CHOOSE_ENDPOINT", "prefix": "CHOOSE_PREFIX", "region": "auto"
      },
      "snapshot_id": "CHOOSE_VERIFIED_TRAIN_SNAPSHOT", "points": 512,
      "cache_directory": "/ABS/anchor-cache"
    },
    "loss": {
      "clip": 0.2, "kl_coef": 0.02, "anchor_coef": 0.5,
      "max_grad_norm": 1.0, "rho_max": 1.0
    }
  },
  "evaluation_execution": "/ABS/evaluation-execution.json",
  "evaluation_manifest": "/ABS/registered-RL3-evaluation100.json",
  "evaluation_tiers": [
    {"name": "quick", "seconds": 14400, "seed_prefix": 20, "wall_seconds": 7200},
    {"name": "milestone", "seconds": 28800, "seed_prefix": 100, "wall_seconds": 10800}
  ]
}
```

时长必须是300–72000秒的显式整数；模板null会明确拒绝准备/启动。
默认原actor预算要求 `wall_seconds > games_per_ledger × episode_seconds + 180`。
本卡短链路验证若只900秒，须在获批的smoke配置显式选例如1–2路、episode180、ledger1，
不暗改长跑模板；smoke的`approved=true`不构成长跑授权。GPU先A30；只有已有实测及
本轮决定支持时才选A100。工具每次只请求1张，并在共享提交锁内检查本用户整个
`smp_gpu`分区没有现存作业；不停止任何已有GPU作业来抢位。

## 准备、审阅和一次启动

```bash
# 在已部署Friday源码中，用原CPU环境解释器执行；不访问R2，不提交Slurm。
python tools/friday/recipe.py prepare --configuration /ABS/rl3.json --output /ABS/NEW-RUN

# 核冻结配置/输入/源码身份。仅完整配置已获本轮批准、approved=true才能启动。
bash /ABS/NEW-RUN/start.sh
```

`prepare`生成冻结configuration/learner、原actor plan、evaluation输入副本及三份脚本，
`plan.json`保存配置/输入/脚本SHA、准确源码commit和关键源码文件SHA。支持输入位置不变，
不复制凭据、原模型或旧run。可先用`approved=false`准备审阅；批准后在原单配置中设置
true，另建新输出目录重新冻结，保留旧审阅物，不编辑已冻结的plan/config绕过哈希。

`start`先拒绝已有intent/receipt，再只读确认目标run无旧STOP/model/checkpoint/shard。
只有确认是新的空run后才认领本次启动；随后：

1. 共享提交锁内确认GPU位空，请求一张指定GPU，Slurm最长=learner时长+600秒，
   结束前300秒USR1；明确CUDA解释器运行原learner CLI。
2. 最多等120秒观察原v0记录，再调用原`rl.submit`启动actor，预算/保存边界不变。
3. 用v0原`published_at`作为准确started_at，把actor实际job ID写watch config；
   提交原watch CLI，带原两档评估，原预留确认20不动。

每步先写intent，再记调度回执。`start-receipt.json`绑定实际三方job、起点时刻和plan哈希。
learner原STOP/signal保存负责最终CP，看守负责原健康规则和排空；本工具不接管正常长跑。

## 失败与恢复

- scheduler结果未知：保留该步intent和`*-outcome-unknown.json`，不补投、不声称失败、
  不猜job ID。按原job name/提交时间/plan核调度器原请求；同目录再次start会拒绝。
- 已知失败（含120秒未见v0）：只写本新run的learner/actor STOP和正常Exchange永久STOP；
  不取消其他job，不删原件。remote STOP失败也保留本地STOP和未知回执。已有旧run在认领前
  就被拒绝，不进入这条清理，尤其不修改RL2。
- 由owner核原Slurm/Exchange/保存回执并按原长跑SOP收口；没有自动重启、恢复游戏或热调。
  需要换配置时，先完整存档和核停止，在新run/新目录用新的获批配置重新冻结。
- 工具测试仅替换调度器和Exchange，证明接线和未知结果保护，不证明GPU可用、真实闭环、
  数据资格、模型变强或长跑已获批准；这些由该轮运行卡的实物验收负责。
