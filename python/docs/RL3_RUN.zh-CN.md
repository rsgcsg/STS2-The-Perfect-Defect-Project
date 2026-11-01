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
两边状态及原因；双边同seed只算一个。按用户11-01傍晚新定，unknown数量不使整轮
作废，其余合格自然终局按seed配对；零有效配对则不给效果估计。pending、censored、未开或缺轨迹等其他缺口仍为未完成，不能冒充
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
控制读取/解析/CPU投影并发，默认1。`reader_backend=thread`为原默认路径；`process`用
spawn独立进程，子进程从同一固定包核SHA并加载CPU reference，每子进程torch线程为1。
它们只读正常Exchange/typed shard并准备输入，不持策略/优化器/CUDA状态；主进程仍按发现
FIFO准入、复查当前新鲜度并记账。提交时版本/热身状态只是预筛快照，不能绕owner复核。
正常完整CP恢复也用同一reader重建原顺序/uses/原始开局时间，恢复不延长600秒期限。
进程返回单bytes载荷（含传输成本），避免为每个小tensor打开共享内存文件描述符；这是
本机同版本子进程私有IPC，不接收外部pickle。分类错误及安全诊断原样传播，只读Store
重试上限不变，unknown游戏从不重投。正常退出和恢复失败均排空并回收reader子进程。

CPU资源按主进程torch线程与reader并发安排，不能把reader进程计成游戏或自动申请更多核。
进程启动/冷恢复另列，生产用相同窗口的首次用数、未用过期与实战验证收益；独立CPU诊断
不替live吞吐。后台耗时与owner阶段重叠，fractions不能相加。实际actor产能看正常observer
的原始uploaded_at与每局决策数；learner.generated/uploaded仅在读入后推进，其导数受reader
限制，不等于actor实际生成速度。保留生成、上传、准入、首次使用、复用和过期各自分母。

#### Friday共享scratch热Store与后台归档（显式选用）

Friday同节点角色可使用同一个物理共享目录，沿原Exchange/Store/typed reader读写：
peer为`{"kind":"local","directory":"/data/scratch/USER/RUN/hot-store",
"archive":{...原冻结的s3 peer...}}`，execution的`store`须为同一directory。
actor、learner、watch都冻结同一个新peer；不是只改learner后继续让actor向旧R2入口发数据。
默认local/s3配置不变。archive只接受一个明确现有peer，不跟随环境改变目标，不自动串接归档。

热Store沿原原子不可覆盖写入、payload/typed读回；后台复用`copy_artifact`复制新root及尚缺
依赖，完整读回新root payload；已发布的immutable父manifest按原reuse选项复用，不冒称重新
逐字节审计整个历史前缀。相关Exchange record先确保所指artifact完整可读才归档。原artifact ID、
producer、unknown、用途、训练资格和成功use计数不重写。本地成功只说明热数据可消费，
不是R2保全完成；`.archive/pending`是持久待办，`.archive/completed`保存完整读回收据，
归档失败分类保留在`.archive/errors`。不因归档失败重开游戏或丢弃本地原件。

原run迁移前，必须先正常排空全部旧R2 producers，核完整最后记录后，再用正常loader运行
`python -m spireagent.storage.async_archive --peer PEER.json
--operation seed --run EXACT_RUN`，把原Exchange记录逐字节搬到新热Store；artifact冷缺失继续走
原Store的显式archive读通道。这个准备动作不启动actor/learner、不改原记录、不延长开局年龄；
实际切换仍走正常完整CP与源身份迁移。seed只是一次快照，晚到的旧R2记录不会自动出现在
热listing；不允许留下双入口并写。完成切换后控制也归新peer，不能把旧R2读者当新热控制owner。

actor/learner退出时会等待本进程已发布内容的归档读回；不完整会明确失败并保留shared目录，
不把transport失败改记为游戏unknown。负责人最终还须在停止写入后运行
`python -m spireagent.storage.async_archive --peer PEER.json --run EXACT_RUN --timeout 600`，
重扫已发布manifest/本run记录，恢复publication→outbox之间的进程崩溃缺口并核完整读回。
drain总限在任务之间检查；已开始的一次Store I/O仍受原S3超时约束，timeout不是硬抢占。
本地quota/OSError也记录安全stage/类型/intent到scheduler stderr，不输出原异常message。
这只处理Store对象和Exchange记录，不递归打包目录、凭据或邻近文件；原游戏scratch的正常
snapshot保全义务照旧。`--operation status`可读待办/完成/分类错误，R2失败不静默回退。

`background_io=true`时，listing与process-reader结果解码/收取在后台；owner仅消费就绪FIFO，
当前policy的整局V/acting-KL/target准入以及uses/RNG仍在更新边界。新增`admission`阶段单列这些
owner计算，`poll`不再重复包含它；与旧poll合并口径比较时须先还原定义。事件沿原telemetry
线程发布到本地热peer，R2归档有独立后台队列，不再靠owner等待R2完成。独立CPU/R2检查仅
证明传输与读回；真实训练占比、秒/更新、首次用/生成、等数据比例仍须新部署前后实测。
archive状态的owner读使用后台更新的计数快照并带verified_at，可能滞后一个后台周期；原本地
日志、持久发布与owner准入仍有真实I/O/计算成本，不能称事件成本或整段poll已全部消失。

正常恢复默认仍要求同producer。升级源码时必须同时指定`resume_checkpoint`和
`resume_source`（原producer完整repository/source_revision/uv_lock_sha256）；原run、
起点/参考包、checkpoint格式/版本及载荷绑定仍严格核对。`resume_critic_changes`逐项列
确切`{old,new}`，必须与实际非热身配置差异完全一致；未声明差异拒绝恢复。
显式旧单组→独立head双组时按原参数注册顺序重映射原Adam moments，不清空主干；
以后双组恢复用新配置的head_lr，不能被旧optimizer fixed_lr覆盖。迁移原producer、CP与
实际配置差异记入原resumed事件，新CP由新producer签发，不改旧记录。永久STOP仍不可复活。

## 当前策略价值尺子：固定模型、同策略采样与完整MC目标

默认在任何评分相关改动前后、每轮quick前后各量一次，旧23seed/4627行仅作连续性参考。
正常入口是 `tools/friday/value_validation.py`，复用完整CP/action绑定、正常
EvaluationLedger、PipelinedManagedExecutor、typed shard和allocation/GAME-SLOT。
没有新游戏循环，也不把训练批的lambda目标或动态训练层数当当前模型实力。

在正常Friday loader与ML环境中，先用 `prepare --root <新目录> --learner-config <当前配置>
--execution-template <已验证执行配置> --runtime-source <冻结执行源码> --count 48 --workers 7`
准备。当前支持原canonical Defect/A0场景。首次按固定纯hash选48个canonical单槽组，
全部通过永久TRAIN held_out检查且不属于任何保留评估池；后续加 `--seed-plan <首次fixed-seeds.json>`
复用同组，不看结果换种子。CP、动作权重、代码/Host/Runtime、温度1/均匀混合0、完整
输入和初始进度在offer前冻结。正常构造/close预检0World；未评分采样依法使用
trajectory/training retention，但这些局永不Exchange announce、不进策略或价值优化器。

用 `submit --plan <root/plan.json>` 单次提交，或通过生成的正常allocation launcher运行。
必须把actor、quick、U1与本次workers统一算入游戏并发36；CPU评分不占游戏路。
各slot最多1次offer；清洁unknown/censored单列且不补打。STOP/USR1停止新准入，已有
正常调用按原预算收尾；进入一局前预留准备150秒、整局、清理120秒和保存180秒。
失败/已有intent先核原收据，不能自动重投；未知不能改成负局或零层。保留saved的
unscored事实，不能为做价值诊断强改正式audit/scored资格。

正常采集完成后 `score --plan <root/plan.json> --output <新结果目录>` 物理读回typed轨迹，
再核原attempt/intent、模型、InputSpec、执行条件与完整CP。用同一CP的raw价值头
CPU FP32/eval/no_grad计算；标签是reward v2的完整终局MC折扣回报，不用lambda或价值
自举。JSON与逐步CSV报告标准按行EV、每局等权偏差、局内先对两列各减本局均值的EV、
前中后三分之一、固定预测分档校准及该冻结概率采样模型的层数分布；同时列所有失败分母。
等局权重分档使用inverse-full-game-length权重后在bin内归一，区别于每个入bin游戏等权。
零目标方差不给EV，零自然局不给效果估计。固定组bootstrap仅为敏感性，不是总体资格。
这些是Managed结果，不能替真游戏实力；raw价值也是回报估计，不是已校准的通关概率。

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
attention_required，不自动开新轮。原STOP与保存阶段信号逐层传到本轮停止新准入。

主comparison/均值/图只使用双边合格交集，原批次audit/scored与所有尝试分母留在result.batches
及正常原件，不能用20对分母替换它们；现有rl_observation写JSON/CSV/SVG并明确投影范围。
这只是quick报警，不证明总体强度或正式发布资格；末100按用户新定逐对剔除unknown。
已封存v134轮不补不改。最终同窗起点/末版各100仍走上一节原规则。

## 完整FP32执行与可选整局准备（11-01候选）

“采用PREP-B FP32”指完整组合，而不是只把precision写成fp32：

```json
{"packed":true,"tensor_cache":true,"precompile_inputs":true,"joint_nodes":true,
 "scene_attention_area":67108864,"text_attention_area":67108864,
 "bucket":true,"reference_device_cache":true,"async_transfer":true,
 "defer_sync":true,"microbatch_size":8,"precision":"fp32","tf32":false,
 "probe_inference_microbatches":true}
```

PPO与critic均实际消费执行设置；桶排序只重排同一optimizer batch的微批，整局结果按原索引
还原。完整目录、targets、action、key和scale保持对应，loss分母仍为整个batch行数。
固定reference缓存在目标device构造；只有不可变输入和固定reference可缓存，当前策略与价值
不作静态缓存。延迟检查仍在optimizer.step前拒绝真实非有限scores/value/reference/loss/梯度。
FP32仅承诺既定容差内同目标；改变归约顺序不承诺逐位相同或所有长训轨迹相同。

`inference_microbatch_size`只用于整局重算和固定验证；训练微批仍由microbatch_size控制。
显式`probe_inference_microbatches=true`要求CUDA及已恢复的非空critic replay。恢复后、首个
optimizer更新前，从实际replay按实体数量分位取至多128帧，和父CP原执行配置同权重比较完整
分数、原单位value与argmax；候选8/32/64分别暖1、测2，保留实际微批数/显存/失败，再选通过
数值门的最快者。CUDA OOM只淘汰该候选，不改已发生的训练/游戏事实；全失败则明确停止。
选择写`inference-microbatch-probe.json`、原事件、展开配置及后续CP。冷恢复/探针耗时计原时钟，
不是重置训练预算；真实吞吐须看正常训练后的窗口，不能拿探针或历史2.88倍冒称全链收益。

默认`prepared_targets=false`，仍每次更新整局重算当前V/截断V-trace-GAE。可选true是明确的
`admission-v1`方法阶段：完整合格自然TRAIN局在准入时，同一次当前forward准备raw V/ratio/
advantage/target，最多两次成功使用；旧CP无准备数组时仅按需整局准备一次。
不改reward、原开局600秒时效或原uses；heldout与warmup用途不变。raw优势每个实际batch才
归一化，value EMA只缩放残差，不重解释缓存原单位。缓存绑定method/γ/λ/reward spec与值、
原key顺序hash、控制序列及实际policy/head更新/source/time，进入原完整CP metadata tensor。
γ/λ或控制阶段改变使缓存显式失效重算。不能把λ=1的截断trace普遍称为纯MC。

生产样本每次loss forward均从完整原acting分布重算current→acting KL和selected ratio；
准备时KL只作历史漂移。forced与多候选口径分别记录；真实旧概率0不会被padding mask吞掉。
缓存阶段要求PPO、max_reuse≤2且启用原episode age规则。

需切换时，在学习者work目录原子写入`runtime-control.json`，无需重启：

```json
{"schema":"stpd/rl-runtime-control-1","sequence":1,"prepared_targets":true}
```

命令在下一次train_once入口、optimizer更新之间生效。sequence从1递增，重复同字节命令不
重复执行，同序列改内容/跳号明确拒绝；false同样是一次新序列。原事件记操作和前后模式。
完整CP保存已接受seq/hash和effective mode；恢复以CP实际模式为准，启动JSON的默认false不
偷偷关掉已启用阶段。新写命令应使用CP下一序列。无新World loop、Store、报告服务或批准层。

11-01用户允许：切换后30–60分钟若重算仍≥20%直接开缓存；等待数据约>10%或多数数据已用满
两次即可加到20–24路，再按事实向36路内调整，不要求为单项归因另等整轮。每次记录绝对时间、
理由、原参数/新参数、实际采用和后续结果；总训练终点与全卡硬限不重置。新增actor用新正常
plan/唯一actor与seed命名空间，原unknown不重发；全部活动allocation由本卡owner统一保全。

## 同run增加actor allocation

既有数组继续运行时，先正常prepare一个新output/唯一actor和seed命名空间、原run/peer与冻结
actor source不变，再用`rl.py submit --plan NEW --additional-to EXISTING...`显式列出现有
正常plan。默认submit仍拒绝并存的F2数组；活跃打局父job须同run/peer/source，新增
actor名不得重复，当前活跃actor合计≤36。未列明的现有数组或超限明确拒绝。
沿同一用户调度锁和公平资源检查、原单核数组/rl/ledger/Pipeline入口，不重开旧unknown。

自然停完Actor但仍复制证据时，Slurm仍可能显示RUNNING。`--additional-to`会读取原
`rl.run`在Actor的`wait()`返回后、snapshot前写下的结果：全部owner/plan/config/Actor/run
绑定一致、退出0且summary为completed或stopped_signal，才把该父job记为preservation_only，
不占活跃游戏名额；结果路径与SHA写入新提交收据。仅有STOP、summary、终止信号或Slurm
状态都不够，cleanup未确认导致的stopped_ledger也不够。这样同run的新冻结peer/source可
在旧证据继续完整保存时接管，不改作业名、不停止快照、不重投旧局；旧R2生产者排空后再
seed的新peer要求仍保留。缺结果按仍活跃处理，绑定错误拒绝；归档是否已完成另看原snapshot。

新工具可编排旧冻结actor源码：运行新`tools/friday/rl.py`时PYTHONPATH指向原actor source的
python，保留require_modules真实绑定；生成的launcher仍运行plan.source_root。不升级动作
adapter或日常安装。额外提交先持久化submission-intent，响应未知后默认/额外两入口均拒绝
重发，先核原请求和调度结果；成功的原submission.json拥有实际job身份。

看守配置可列`additional_actor_plans`，每项为`{plan:{path,sha256},job_id}`，绑定原plan及其
submission。read_config核run/peer/冻结source、actor名/job不重叠与≤36核；健康计数汇总所有
allocation，Stop为每个plan写原STOP且对每个job只发一次信号，drain需全部终态。配置更新仍
按原USR2维护、pending排空、显式旧新config digest与原start/schedule状态交接；GPU不重启。
本卡owner在维护完成前也须跟踪新job原收据，并保全全部array，不能只拿主数组终态称完成。
实际扩容时还须计入同时进行的评估游戏核，保持本卡总游戏核≤36。

## Actor提交资源与实际容量核对

仍使用正常`rl.py prepare/submit`与全新seed_namespace；资源可在`submit --memory-gb 6
--exclude-node smp-9-0`显式给出。默认仍4GiB/不排节点，API与CLI映射同一submit；
冻结计划、同run/source/peer父计划、submission lock/intent/STOP和unknown禁止重投不变。
增加内存或避节点不是重发旧槽，也不能绕审计。本卡45秒audit不改；诊断保留原超时，
不从FAILED1:0或贴4GiB峰值推断OOM/NFS根因。

容量以`squeue -r`逐task及`sacct`完整窗核，不能按plan长度、array数量或RUNNING父行推算。
局部array保留全部历史，实际存活索引单列；32路连续满30min且原同32任务无退出后才量
训练计时/墙钟、秒更新、first-used/观测入账generated与等数据，窗口失败即作废。
actor终止基础设施事件与已有World槽分别列；原unknown总分母、run/seed/reason/资格不改。

## 零初始化MLP残差评分头（用户23:20决定）

本轮直接从完整CP切到零初始化attention-residual-v1；离线候选未过线的历史保留且不导入。
用户知悉结果后决定以后默认MLP，不设线性对照、回滚条件或额外运行验门，不等qualification。
原策略lr、λ、value系数、entropy与目标尺度不变。已有离线工具只作分析，不能阻塞本次切换。

新run的LearnerConfig.read和Friday recipe在启用critic而未指定kind时默认MLP，并把recipe
冻结为显式head_kind；旧完整CP缺head_kind仍按linear解释，不回写历史。低层CriticConfig的
linear默认只保留旧CP/API兼容，不是本轮对照臂。评分只接公开scene，critic特征detach；
最后层为0、残差加在原线性基底上，策略正常PPO照常。actor导出剥全部progress_residual参数。

从旧线性完整CP升级时配置critic.head_kind=attention-residual-v1，沿原正常resume_source和
resume_critic_changes声明kind变化；不需要离线candidate或qualification文件。只允许新增
残差缺键，原策略/线性权重和Adam矩、RNG、FIFO、EMA、固定留出、prepared控制seq保留；
新残差Adam矩从空开始。后续MLP完整CP严格正常恢复，不再执行首次zero初始化迁移。

只记录一个实际切点model/version、时间、updates及准确source；最终100+100把切前后阶段
分开说明，不把全程效果单归头。原定训练终点和资源上限不变，原正常评估照常，不额外加
切前后对照或1h/3h门。PR369和已准备好的Host/actor组合尽量同一自然批切，未ready不拖主切。
