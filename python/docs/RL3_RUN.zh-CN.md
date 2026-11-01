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
  不降到CPU。GPU learner的OMP/MKL/OPENBLAS线程数显式取learner.threads（默认4），
  actor/watch保持原单线程，避免前缀把学习者CPU条件暗降到1。watch也明确使用同一
  独立CUDA环境的解释器，以复用其TensorBoard依赖；watch仍申请CPU Slurm作业，
  不申请GPU、不进行CUDA推理，actor/evaluation继续使用原CPU环境。
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
  "watch_poll_seconds": 900,
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

`evaluation_tiers`支持仅quick20（省略milestone一行），沿同一固定4h调度且不触发旧40局确认。
Friday计算节点禁止嵌套sbatch时，看守可用`evaluation_mode="direct"`并独占4 CPU；
原四路评估在这份分配内启动，少于4 CPU会明确拒绝。训练actors最多32路时加4评估路，
总游戏并发不超过36。末100另用原evaluate入口，仍绑定同一100颗manifest和argmax。

时长必须是300–72000秒的显式整数；模板null会明确拒绝准备/启动。
默认原actor预算要求 `wall_seconds > games_per_ledger × episode_seconds + 180`。
本卡短链路验证若只900秒，须在获批的smoke配置显式选例如1–2路、episode180、ledger1，
不暗改长跑模板；短链路可显式设watch_poll_seconds=60（默认900，允许60–1800），
以便及时核正常排空，smoke的`approved=true`不构成长跑授权。GPU先A30；只有已有实测及
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

- scheduler结果未知：保留该步intent和`*-outcome-unknown.json`，不补投、不把未知job
  重标失败、不猜job ID。仍对本次已认领的新run标STOP，防止可能启动的learner失去看守。
  按原job name/提交时间/plan核调度器原请求；同目录再次start会拒绝。
- 认领后的任何启动异常（含120秒未见v0）：只写本新run的learner/actor STOP和正常
  Exchange永久STOP（reason=`recipe_start_failed_new_run`）；
  不取消其他job，不删原件。remote STOP失败也保留本地STOP和未知回执。已有旧run在认领前
  就被拒绝，不进入这条清理，尤其不修改RL2。
- 由owner核原Slurm/Exchange/保存回执并按原长跑SOP收口；没有自动重启、恢复游戏或热调。
  需要换配置时，先完整存档和核停止，在新run/新目录用新的获批配置重新冻结。
- 工具测试仅替换调度器和Exchange，证明接线和未知结果保护，不证明GPU可用、真实闭环、
  数据资格、模型变强或长跑已获批准；这些由该轮运行卡的实物验收负责。


## 可选公开输入评分网络

省略`critic`/填null、`loss.mode="legacy"`保留原整局基线。显式启用下面的PPO配置，
同一个MStd加fresh progress_head，共用主干；win_head仅为既有导出结构保留、不训练，
两头都从actor包剥掉。评分只接策略同一公开Frame/TensorFrame，seed仅用于整局切分，
终局仅作目标；值是reward-v2的折扣回报，**不是通关概率**。不启用旧T2头。

```json
{
  "max_episode_age_seconds": 600,
  "publish_interval_seconds": 60,
  "max_reuse": 2,
  "anchor": null, "anchor_batch": 0,
  "critic": {
    "gamma": 0.999, "gae_lambda": 0.95,
    "warmup_lr": 0.0001, "batch_size": 64, "max_rows": 60000,
    "min_train_episodes": 32, "min_validation_episodes": 8,
    "min_updates": 128, "validation_every": 32,
    "warmup_min_seconds": 3600, "warmup_max_seconds": 7200,
    "checkpoint_seconds": 600, "min_explained_variance": 0.0
  },
  "loss": {
    "mode": "ppo", "clip": 0.2, "kl_coef": 0,
    "anchor_coef": 0, "value_coef": 0.5, "entropy_coef": 0.01,
    "max_grad_norm": 1.0
  }
}
```

PPO只接原接口`temperature_uniform/T=1/uniform_mix=0`，即直接π采样；读入时核真实
记录的选择概率。温度校准须先在新起点包中把最后action线性层weight/bias除以已冻结T，
不在actor另加探索修正。每小账本设1局，开局取后台已完整下载/核哈希的最新模型。

热身冻结全部动作参数/主干，只训fresh value head，策略更新数与策略首用计数为0；
10%训练seed稳定哈希整局留出，始终既不训策略也不训价值。其余热身局只给价值训练，
不塞入一小时后的策略buffer。至少32训练seed/8留出seed/128头更新，达到最小时长、
留出EV>0且MSE低于训练均值常数才解冻；到2h仍不过，以`warmup_not_qualified`停机保全。
可在新恢复配置中明确调整warmup时长/lr，已耗时和随机状态保留；方法调整须走下面的显式迁移。
热身期间每10分钟仍保一份新不可变版本/完整CP，动作张量可能相同，不能把版本增加称为策略学习。

仅自然完整终局授原reward v2，当前先在终局给奖励；unknown/删失原样保留且无价值标签，
不重开。每次策略更新前，用**当前**网络重算相关完整轨迹的值及全部动作分布，终局bootstrap0。
使用截断未来重要性trace：`A[t]=delta[t]+gamma*lambda*min(1,rho[t+1])*A[t+1]`，
价值目标`V[t]+min(1,rho[t])*A[t]`，rho为当前π/记录μ。这是V-trace(lambda)形式；
当前动作的rho已在PPO裁剪比内，不再乘一次。rho=1时回到标准GAE/lambda回报。
优势在实际minibatch内标准化，loss为PPO clip＋无权value MSE－entropy＋可选起点KL。
PPO训练用eval模式求导，禁dropout/id masking，与actor概率一致；legacy训练模式不变。

`max_episode_age_seconds`替代版本过期，起点600秒从原pipeline开局时间算；
`publish_interval_seconds`替代按更新次数发版。省略两字段保留旧版本/更新门。
当前Replay按整局FIFO有限保留，容量淘汰/过期/复用/热身/价值留出分别记账，不删原轨迹。
既看秒数，也看**到达时完整acting-policy KL、clip及概率比**；KL小/clip合适时不为GPU忙或
表面新鲜度改系统。确实太旧，按每小时实战收益选择分段、大批少更新、模型采用等提速，
不靠降lr或多扔数据解决；原始失败和不同参数的时间边界必须保留。

原decision ledger追加arrival_policy_drift、逐步critic_prediction、成功critic_trained及
ppo_diagnostics（含by_catalog_kind合法机会数、未被选择种类的当前/起点概率质量、熵与KL贡献），绑定原run_id:step.index、使用的critic/policy更新数、真实优势/目标/比率与KL。
原trained事件成功后才记策略首用，另含模型落后及开局到首训秒数；时钟界未知仍是null。
上报当前KL/entropy、分类指标、概率比/clip分布、原始与归一化优势、value loss及真实阶段耗时。
值头热身不算策略更新；额外整轨迹重算时间单列，不借旧固定窗称新方法仅多一成计算。

完整PPO CP为`stpd/rl-learner-checkpoint-3`：model/optimizer含共享价值头，learner JSON
是受限uint8载荷，避免长元数据突破codec的100,000节点/8MiB header；总512MiB限不变，
metadata64MiB限显式失败。legacy checkpoint-2字符串仍可读。恢复同producer/目标/起点，
原分片重建Frame和原FIFO、开局时间/复用次数/优化器/RNG/热身门/已耗时间；watcher用同解码器，
核actor动作张量时按原导出函数剥值头。拒收/退休或只训头也会触发最终新CP，不能丢末状态。

正常分配边界用原learner CLI的`resume_checkpoint`续同run；必要时显式
`republish_on_resume=true`，先把恢复的准确权重发成新不可变版本再继续。永久Exchange STOP
存在时不允许借该选项重发复活。回退/调参/换机记原CP与配置变更，不覆盖模型、CP或旧失败。
原STOP/watch保存和冷读门照旧；源码/CI通过不代表真实恢复或策略变强。

本轮授权/时限/操作调整归[RL3任务设计](../../docs/plans/RL_CRITIC_20261101.md)，
实际保存与结果归原运行账本。此配置示例不激活日常或正式配方。


## 显式新配对轮与已清理unknown

用户已在本卡明确授权末尾新的固定同窗配对轮：起点和末版各100颗原RL3种子，包含旧轮
unknown的种子；旧轮原件和失败分母保留，新轮unknown一律不重试。两份计划使用同一
冻结执行源码、Host/Runtime、原manifest、argmax和四路条件，并在同一时间窗并行启动。
起点/末版artifact及weights SHA、两份计划SHA、协议SHA与新round_id在开局前固定。

仅此类已明确批准的继续采集，可在原evaluate prepare CLI增加
`--unknown-policy continue_cleaned`（对应计划字段`unknown_policy`）；默认省略仍在首个
unknown停止。该选项只允许清理已确认的unknown继续新槽，不重新提供旧unknown；
未清理的world、save_failed或typed_original_verification_failed仍停止。原Pipeline、Ledger
审计、保存、来源与历史score_admitted字段不变，这个选项本身不赋予科学计分资格。

本卡新轮按用户固定的研究配对规则单独报告：任一侧unknown，剔除该seed整对并列种子号、
两边状态及原因；双边同seed只算一个。union unknown seed超过5整轮作废，最多5则其余
自然终局按seed配对。pending、censored、未开或缺轨迹等其他缺口仍为未完成，不能冒充
unknown剔除；未覆盖情形交用户决定。保留原审计事实，与新paired-rule资格分开，不把旧
score_admitted强改为true。末轮先确认训练演员排空，两份四路评估也受总并发36约束。

## 价值尺度、固定留出和有界后台读取

可选`critic.normalize_targets=true`用TRAIN目标的无权EMA均值/二阶矩计算标准差，
`target_ema=.01`、`target_std_floor=.01`；损失为`((Vraw-targetraw)/std)^2`。
均值在归一化预测与标签的差中抵消，raw输出/GAE/终局奖励/EV不改单位；这不是PopArt，
不重参数化head或Adam。loss.value_coef因此换了单位，必须重新选并看共享主干梯度比。
`critic.head_lr`为raw价值头独立学习率，其余参数仍用策略lr；省略保留原单组优化器。
每个validation周期的首个PPO步在既有ppo_diagnostics记录实际策略与加权价值的共享主干
梯度范数和比值，动作专用readout不进分母；该额外反向成本计入update_seconds。

`critic.fixed_validation=true`在收够min_validation_episodes个留出seed时一次冻结当前
全部留出整局；既有ready CP迁移在恢复后冻结它实际持有的组，不按预测好坏挑选。
固定run/shard引用、归一化统计、完整Adam/RNG都进原CP；固定组免FIFO淘汰，普通回放
仍受max_rows软限并保留最后一份完整整局。尚未凑够固定组的早期warmup可正常恢复继续收数。
新heldout seed仍不进任何优化器/归一化统计，但不替换固定组。validation_kind/run_ids绑定
固定组；默认旧rolling行为不变。价值验证用相同场景encoder的value-only路径，省去动作readout。

`background_io=true`启用已有异步发现/遥测，`read_ahead`限制预取；`reader_workers=1..8`
控制读取/解析/CPU投影线程，默认1。后台只准备不可变CPU输入，owner仍按发现FIFO准入、
复查新鲜度并记账，worker出错正常传播，不静默跳过。线程耗时与owner阶段可能重叠，
不能将fractions相加；冷恢复另列，长期比较用相同窗口首次用数和未用过期分母。

正常恢复默认仍要求同producer。升级源码时必须同时指定`resume_checkpoint`和
`resume_source`（原producer完整repository/source_revision/uv_lock_sha256）；原run、
起点/参考包、checkpoint格式/版本及载荷绑定仍严格核对。`resume_critic_changes`逐项列
确切`{old,new}`，必须与实际非热身配置差异完全一致；未声明差异拒绝恢复。
显式旧单组→独立head双组时按原参数注册顺序重映射原Adam moments，不清空主干；
以后双组恢复用新配置的head_lr，不能被旧optimizer fixed_lr覆盖。迁移原producer、CP与
实际配置差异记入原resumed事件，新CP由新producer签发，不改旧记录。永久STOP仍不可复活。

## 后续quick：原池内补足20个合格自然配对

显式`natural_quick`只用于direct四核、单一每4h quick计划，默认旧行为不变。
watch配置保留`runtime_source`与原actor计划的严格绑定；可另给`evaluation_runtime_source`
指定独立冻结评估源码，不升级actor/learner。`natural_quick.baseline_model`含准确的
`model_artifact_id/model_sha256`；`baseline_cache`是原正常批次的plan/result/snapshot三个
`{path,sha256}`引用；`baseline_unknown_seeds`列已知不能再打的同起点unknown（不改旧事实）。
后续正常baseline补充批的原引用由watch state持久保留，供下一轮复用，未知或未资格的
baseline不会再发。动作adapter必须与缓存原执行一致；不能仅改expected digest来制造一致。
若控制源码更新而动作实现冻结，明确记录这个实现组合及两份真实producer。

每轮冻结current与baseline模型、原100 manifest及四路/argmax/原保存审计条件。
先打原前20；缺额按原1-based ordinal递增选后续seed，既不切片/重写manifest，也不重编号。
`EvaluationLedger.create(seed_ordinals=[...])`和Friday prepare的`--seed-ordinals`提供这个
新ledger子集，不能与seed_prefix同时用；原prefix行为保留。每个子批仍完整经过原
Evaluate/Pipeline、原审计与全字节保存读回，再计数。当前局合格而baseline cache缺失时，
正常补相同seed的起点局；任一边unknown则不计这对，原件/原flag保留，游标前进不重试。
原score_admitted=false批次的自然局也不计入合格20对，不用补充审计回写救活旧批。

该协调器是`tools/friday/quick.py`，通过原allocation.py资源入口串行调用正常evaluate，
不是第二个游戏循环；始终四路，不把current与baseline并开成八路，200为原100池双边上界。
20对、原100池耗尽、原墙钟上限三者先到即停；后两者不达20就是incomplete，不扩池。
缺自然终局指标、保存/清理未确认、完整性失败或不闭合cache要求owner读原现场，watch保留
attention_pending，不自动开新轮。原STOP与保存阶段信号逐层传到本轮停止新准入。

主comparison/均值/图只使用双边合格交集，原批次audit/scored与所有尝试分母留在result.batches
及正常原件，不能用20对分母替换它们；现有rl_observation写JSON/CSV/SVG并明确投影范围。
这只是quick报警，不套末100的unknown>5作废规则，不证明总体强度或正式发布资格。
已封存v134轮不补不改。最终同窗起点/末版各100仍走上一节原规则。
