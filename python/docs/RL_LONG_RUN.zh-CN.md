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

## RL5 快链启动器（`tools/friday/fastchain`）

RL5 整链（env → 推理 → 组装 → 数据面 → 备料 → 训练 → 发布）从登录节点用一个命令起：
`launch.py plan|preflight|submit <runtag> --site <站点文件> --cards N --card-type T [--layout L]`。
- 布局是数据：`shared`（1卡共用）、`1i+1t`、`1i+2t` 或逐卡写 `inference,train,train`；
  不写时用运行配置里该卡数的默认布局。名字统一为 `N卡 · 布局 · 卡型`，目标跟卡数走
  （1卡 ≥1,400万；3卡 >3,000万，≥3,500万 = ready；2卡无目标，读损失表）。
- 运行配置 `rl5-fastchain.toml`：每个旋钮一行（值、原默认、为什么、哪次实测）；一次性改用
  `--set name=value`（记入 `overridden_from`）。bench 与整链读同一文件，生效值写入 run 目录。
- 站点文件：集群相关的路径、分区、节点、凭据文件路径都在这里，真实值不进仓库；
  照 `site.example.toml` 在仓库外填一份。凭据只放 0600 文件，工具只检查存在和权限。
  文件顶层只有 `schema` 和 `[site]`，其余顶层键或表一律拒绝；评估基线写在 `[site]` 里
  （`eval_baselines.a0 = {...}`）或单独一张 `[site.eval_baselines]`，顶层 `[eval_baselines]` 会被拒绝。
  卡型 = 分区 + gres + `constraint`（只有该卡型节点才有的 feature，`sinfo -o "%N %f"` 查，
  `""` = 分区和 gres 已能区分）；`nodes` 只用于打印 sinfo 快照和 `--fallback` 选卡数，从不
  钉节点；只有站点显式写了可选的 `nodelist` 才加 `--nodelist`（CLUSTER-QUEUE-DIAG 11-14：
  按快照钉节点、把“有空卡”当“能开始”都错过）。旧站点文件缺 `constraint` 会被拒绝，补一行即可。
- 预检任何一项不过就拒绝启动并逐项说明（只打印、从不拒绝的例外：sinfo 快照与
  `sbatch --test-only` 的 GPU 开始估计——后者不看排在前面的作业和分区锁，11-14 说“立即”
  时实际起不来；本用户 fairshare 与“现在交一个作业，GPU 分区和备料分区里各有多少个更高
  优先级的排队作业”（优先级按 `sprio -w` 的分区权重 + fairshare 权重 × 因子估计，backfill
  每轮只看前 bf_max_job_test 个，11-14 时是 500；备料分区要看，因为备料作业 GPU 开始后才排队，
  须在 `remote_prepare_wait_seconds` 内起来）；以及旋钮所需源码特性——产品自己会对缺失特性响亮
  拒绝）：卡位、单 rank 设备、备料机型与 scratch（每台开工时再查空闲 ≥ 2×在途）、分区时限、python_env_sha256
  与 lz4（每个节点开工时查）、提速开关生效值、慢机型白名单、env 包 SOURCE_REVISION 与源码
  同一 int 且 memo 开、candidate dll 前缀（≥12 位十六进制）、env 节点数够把聚合器铺满全部
  服务（0 个直接拒绝）、分片均衡、旋钮所需源码特性（#523/#525/#526/#528/#529/#530 的
  标记；缺了只 WARN）、只计实际运行部分的内存预算、跨节点身份、热路径不在共享盘
  （作业侧文件系统必须在本地白名单内，未知类型拒绝）、核数上限。
- 生产者身份：默认 `producer_identity = "checkout"`，站点 `src_dir` 须是干净 git checkout
  （不是 checkout 或有改动就拒绝），CP 带真实源码身份。`placeholder` 时 CP 带占位身份，一律
  作废，只作探测；预检只在 `--smoke` 或显式 `--probe-only` 下放行，`plan.json` 记
  `checkpoints_void`。
- 长跑：`--long-run` 用 `long_wall_seconds`（20 h = 72000 s，一个 GPU 作业、无后继）；
  与 `--smoke` 互斥。分区时限表 `partition_max_minutes` 必须列出用到的每个分区（0 = 不限，
  漏写即拒绝）；每个作业须 ≥ 运行时长 + 余量，env 节点可加入的窗口（时长 − 排空 − 最短加入）
  须 > 0。长跑的备料放在不限时分区（不放 60 分钟的 debug）。
- 冻结参数核对（A9）：`learner_config.learner_base`（`gate_recipe` 补键 + `build`）是 roles.py
  与预检共用的同一函数；预检用闸门 `learner-base.json` 算出生效学习者配置，与源码里的
  `frozen_learner_config`（`rl5.json`）逐键比较，除 `ALLOWED_DRIFT`（设备、提速开关、诊断频率、
  预热件、内存预算）外有差异即拒绝；闸门 `loss` 里有、`rl5.json` 没有的键（如拼错的
  `hp_advantage_coeff`）也拒绝，否则要到 GPU 排完队学习者 `LossConfig` 才崩；
  `max_versions` 由运行配置给（900000，同冻结值）。
- 编译预热：`compile_warmup = true` 时 `sgd_compile.warmup` 取站点 `compile_warmup_dumps`
  （1–8 个 dump，由 `python -m stpd.rl.offline_step warmup-cover` 选，文件须存在），另有
  `sgd_gc_freeze`、`sgd_step_probe` 与 #525 暂存键；读数在 `dynamo_unique_graphs` 高于预热后
  首值时报警，探针开时报 gc 秒数。`sgd_gc_refreeze_objects`（代码默认 200000，配置 40000）：
  边界 collect 后未冻结对象达到它就再冻结一次；每次在线重编译永久留下 2.8万–9.6万个对象
  （V5 冒烟按低点到低点 +9.59万/+5.88万/+2.78万），默认值下它们越积越多，边界 collect 从
  4.4 ms 涨到 65 ms。两次重编译之间的残留：c2q 最后一次冻结后 1,790 个边界，中位 13,049、
  p99 15,804、最大 19,086；V5 在 0 与约 1.1万之间。40000 约为观测最大值的 2 倍，稳态多扫的
  几 ms（0.33–0.6 µs/对象，推断，未量）换来少冻结。
- 起点模型：站点 `start_package`（含 `model.json` 的模型包）。新起时学习者配置的
  `start_model_sha256` 取该包的 `package_sha256`，学习者加载时核对；包缺失时预检拒绝。续跑不读
  起点包（学习者恢复 CP），起点字段取被恢复 CP 的 config，包删了或原地换了照样续、记的仍是真实
  起点。每个 CP 的 config 都带这两个字段。run 目录 `facts.json` 记提交时站点包的哈希和
  `training_identity`（CP 导出的绑定或随机初始化的种子）；运行记录 `identity.start_model` 的哈希取
  学习者配置（`learner/learner.json`），与提交时的包一致才带 `training_identity`，不一致时写出来。
  从零开始：`python -m stpd.rl.random_start --template <当前起点包> --seed N --out DIR`
  按模板的词表、执行配置和尺寸（q3-m-std-1）用 MStd 自身的初始化生成新权重，站点写
  `start_package = DIR/package`、`warmup_file = ""`（拟合好的 RL4 头绑定原包，学习者会拒绝；
  空值时两个头用构造初值：outcome 在合法类上均匀、HP 为 0）。合法类随楼层变，所以初始价值是
  楼层的函数（A0 约 floor 0 时 0.28、floor 48 时 0.85，审查探针），早期优势近似以楼层为势的
  整形：同层约 0、每上一层约 +0.006、死亡处约 −0.28。随机包配了预热件时预检拒绝。
  受训起点配 `warmup_file = ""` 时预检拒绝，除非站点写 `trained_start_without_warmup = true`；
  新起点的预热件按其 `model_package_sha256` 与起点包比对，不一致在提交前拒绝（续跑从 CP 取头，不比）。
- `submit` 每次用新 run 目录，冻结一份 harness（带 sha256）+ `effective-profile.json` +
  `plan.json`；先交备料：每个备料组一个作业数组、每台主机一个单节点元素（不用异构作业：Slurm
  只让 backfill 启动异构作业，11-14 一直起不来；也不用一个多节点作业：5 台整机要同时空出来，
  11-19 上机总验在 general 和 debug 都排不上）；再交 GPU 作业，带
  `--dependency=after:<每个备料元素>`（备料全部开工后 GPU 才开始排队，GPU 不再白等备料）；
  最后交 env 数组，带 `--dependency=after:<GPU作业>`（GPU 开始前只是 Dependency 排队，不占 CPU
  节点、不扣 fairshare）。`plan.json` 里的命令不含依赖项，作业号见 `jobs.json`。先等全部备料开工，
  最多 `prep_pending_max_seconds`（7200 s）；再等 gpu.sh 确认开工（写 `gpu-started`），PENDING
  最多 `gpu_pending_max_seconds`（4 h；`--smoke` 用 `smoke_gpu_pending_max_seconds` 15 min），
  且不超过第一台备料开工后 `prep_lead_seconds`（7200 s）。
  这一项，作业号见 `jobs.json`）。然后等 gpu.sh 确认开工（写 `gpu-started`），PENDING 最多
  `gpu_pending_max_seconds`（4 h；`--smoke` 用 `smoke_gpu_pending_max_seconds` 15 min）：到时打印
  任一上限到时打印 Slurm Reason、优先级和同分区排在前面的排队作业数，取消本 run 全部作业，非零
  退出，不自动重交，也从不少几台备料就开跑；备料上限到时 GPU 一秒没花。备料元素在 GPU 开工前就结束、
  或有主机写了 `prep-refused/*`，同样当场全部取消。
  GPU 作业没确认就结束（FAILED、TIMEOUT、NODE_FAIL、BOOT_FAIL、OUT_OF_MEMORY、CANCELLED、已清除）
  一样算失败；RUNNING 满 300 s 仍没有 `gpu-started`（gpu.sh 卡在检查之前，或共享盘看不到该
  文件）也算失败；squeue 读不到不算回答，下一拍再读。中途任一提交失败、Ctrl-C、SIGTERM、SIGHUP
  （ssh 断开）都全部取消再报错（`submit-failed.txt`），不自动重试。取消顺序：env、GPU、备料
  （`after:` 在被依赖的作业开始或被取消时都成立，先取消被依赖者会放出依赖它的作业）；scancel
  失败时写明 `SCANCEL FAILED ... jobs may still be alive: [作业号]`，要手动 `scancel`。
  launcher 是 GPU 作业开工前的存活负责人：从交第一个作业前到 gpu.sh 确认，每 10 s 摸一次
  `launcher-alive`；gpu.sh 开工时它比 `launcher_stale_seconds`（120 s）旧（或不存在），先等 30 s
  打开文件再读一次（NFS 属性缓存可让它看着旧约 60 s，误拒会白等整段排队），仍旧才拒绝（launcher 被
  SIGKILL、崩溃或登录节点没了），写 STOP 并以 3 退出，不会无人看管地跑 20 h。supervise 在自己
  进程里调 launcher，每一代（含自动续跑）都是同一个检查。
- 启动握手（备料先开始，11-19 起；以前 GPU 先开始、备料后排队，上机总验两次都是 GPU 开工后
  备料排不齐：general 900 s 0/8、debug 300 s 3/8，GPU 时间白等，链停）：GPU 开工时备料主机都已在跑；
  学习者从开始起最多等 `remote_prepare_wait_seconds`（300 s，只剩主机启动和自检，不含排队）等全部
  备料主机 `prep-ready/ready-*`，从不少几台就开跑，任一台写 `prep-refused/*` 立即失败；服务等学习者
  `learner.ready`，env 节点等服务。所以这段等待加进 GPU 作业时长（运行时长 + 240 s + 备料等待）和
  env 等待（`env_wait_seconds` + 备料等待）；备料作业时长 = GPU 作业时长 + `prep_lead_seconds`
  （最早开工的主机最多等 GPU 这么久，之后还要撑完整个 GPU 作业）。代价：GPU 排队时备料主机空等并
  计 fairshare，最多 `prep_lead_seconds`；排队时的作业不计费。繁忙分区上长跑的起法：备料排队（最多
  2 h，不花 GPU），全部开工后 GPU 排队（最多 2 h，备料空等），GPU 开工后 env 排队、按弹性加入；
  任一上限到时链以 `generation N submit failed (... still PENDING ...)` 停（`FINAL.json`），不续跑、不
  重交。两段上限都吃链的墙钟（`long_wall_seconds` 比训练目标多出的约 4 h）。续跑的每一代走同一顺序。
  预检 `time_limits` 按站点 `partition_max_minutes` 查三类作业时长：20 h 长跑第 0 代 GPU 1220、备料
  1340、env 1255 分钟，要 24 h 分区（60 分钟的 debug 会被拒）；hwcheck 档案三者都 ≤ 60 分钟（测试钉住）。
- GPU 作业是否还活着（prep.sh、env.sh 共用 `gpu_job.sh`）：只看 `squeue` 的 stdout（rc 0 时 stderr
  的警告不算回答）；空回答、已结束状态或 `Invalid job id` 算结束，PENDING/REQUEUED/SUSPENDED 等
  其余状态算活，squeue 失败算不知道、从不算结束。开工前已没有 GPU 作业的备料或 env 作业（被取消的
  run、结果未知的提交）直接退出：备料开主机前查一次，env 在等服务时每 60 s 查一次。备料服务期间
  squeue 答不上来时改看 GPU 作业自己的心跳（gpu.sh 每 5 s 重写 `load.txt`）：两者都说死（squeue
  失败且心跳旧于 600 s）才停主机、写 `prep-stopped-<组>` 并以 4 退出，slurmctld 长时间故障不会
  停掉健康的长跑。
- 失败不被覆盖：STOP 只写第一次原因；任一服务或学习者非零退出、学习者无报告，GPU 作业
  rc ≠ 0；MPS 起不来直接拒绝（不允许无 MPS 共卡）。
- 读数：`readout.py <run>`（老 run 用 `--plan`）给每个角色的行/s、忙碌%、队列、等待、丢弃，
  对照该卡数目标。训练行数按含 aux 计（每个 progress 事件 `rows + terminal_rows +
  hp_rows`），目标只用这个口径判；td-only 并列显示；缺 aux 字段时显示未知、不判。
  分卡布局附损失表骨架（完美装箱理想值、实测、按原因一行）；1卡共用时 MPS 重叠，完美装箱
  不是上界，不算。`--json` 给运行记录（`spireagent/friday-fastchain-run-record-1`，与表同数）：
  身份（源码版本、配置摘要、`N卡 · 布局 · 卡型`、配方旋钮含 c_hp/scope）、含/不含 aux 的训练
  行/h、首用比与入队/丢弃、学习者价值/HP 诊断（EV 对 λ 回报与终局、MSE、HP MAE，按幕与 Boss 层
  分桶；优势归一前均值/标准差）与 PPO 量（最近窗口与全程）、每臂探索剂量（RL5-METHODS A3：
  训练行中所选动作在模型自己排序里的名次 头号/二选/三选及以后、模型头号概率 > 0.99 的比例、
  非头号选择归一前优势 > 0 的比例；按决策类型与幕；学习端从已记录的 q、applied 与解码优势算，
  只进 metadata，不改训练数值；存档开局的行记在「臂名+save」下，不混进该臂从头开的局）、每臂
  PPO 更新的 clip 比例与近似 KL（`policy_by_arm`；学习端用每步已有的逐行 ratio/越界事实，行的臂
  由各来源首行的探索表推出，不加新字段、不改训练数值；没有探索表的来源记为 `unattributed`，
  读数单列一行警告与行数；近似 KL 按行合并，全程标量是每次尝试均值的均值，两者不必相等）、对照臂
  整局训练局滚动结果与 `arms` 下每臂同样的结果（只算从头开的局；每难度
  平均层数、胜率、各幕 Boss 通过率、到第三幕比例；滚动窗口按配置 `readout_rolling_games`，默认
  `[100]` 与以前相同，第二个起的窗口并列在 `rolling_windows`，如 `[100, 1000]`；自动续跑后
  新一代的滚动窗口先装入本链前几代的局，最多最大窗口那么多，记 `rolling_seed_runs`，`run`
  仍只算本代；每臂 HP 表仍只算本代）、SGD 边界分段、
  停止/拒绝/未知按原因计数、CP id。每类战斗（普通/精英/首领）的死亡率与净血量变化用 A2 的
  `hp_score`（只在格式 4，全部臂合计），`hp_score_arms` 为每臂同表（「臂名+save」另列；格式 3
  下为空，首领战每场死亡率另可看 1 − `clear_rate_given_reached`）。
  只解析完整行（写到一半的末行等下次读，坏的完整行报错并给出字节位置）；对照臂完整局没有自然
  结局（无 victory/defeat 或无整数层/难度）计入 `unscored_closes`，不计分、不报错。
- 磁盘水位（A2）：GPU 作业每 `disk_sample_seconds` 采样 run 目录与节点本地 tmp（`disk.txt`）；
  空闲 < `disk_warn_free_pct`（20%）写一行 WARN，< `disk_stop_free_pct`（10%）写 `DRAIN`
  （env 节点停止接新局），再过 `env_drain_seconds` 写 STOP「disk stop」让学习者停下（不空等
  数据到 deadline），均进 `disk-watermark.txt` 并在读数里报警。两个百分比的先后在载入配置时
  核对。
- 长跑与自动续跑（用户 11-13 C5「自动」）：`supervise.py start CHAIN --site S --cards 2
  --card-type A100-40 --layout 1i+1t [--resume-checkpoint CP --set
  shards_adopt_unsharded_checkpoint=true] [--eval-execution E --eval-manifest M
  [--eval-baseline-plan P] [--eval-a10-manifest M10] --eval-slot-registered]`，在登录节点上用
  `nohup python3 tools/friday/fastchain/supervise.py start CHAIN ... > chain-CHAIN.log 2>&1 &`
  起（ssh 掉线发的 SIGHUP 被 nohup 忽略，supervise 照常监督；不加 nohup 时掉线会让 supervise 死掉，
  作业照跑但没有续跑、读数和评估）；停链用 `supervise.py stop CHAIN --site S`（见下）；链名不能以
  `-rN` 结尾（那是别的链的代名）。一个链 = 一个运行窗口（`long_wall_seconds`，一个学习者预算 id
  `c-CHAIN` + 不可变 deadline，由学习者跨恢复保持）；第 0 代 = `launch.py submit CHAIN
  --long-run`，之后每代 `CHAIN-rN` 是新 run 目录、新训练代，从本链最新 CP（published_at）
  起步（学习者自己的 CP-only 恢复，CP 记分片数）。正常 = `gpu.sh` 退出码文件 `gpu-rc` 为 0、
  学习者报告无失败且有 `window_end`、STOP 不是失败。可续：学习者报告失败含
  `input_owner_fenced_restore_last_checkpoint`（`input_failure.operation` 说明原因，如
  `fast_handoff_lost`），或 GPU 作业结束时根本没有学习者报告、STOP 没说明原因、且 `gpu-rc` 说明是
  硬死（负责人 11-17）：没有 `gpu-rc`（节点死）、信号退出 129/130/143（Slurm 取消、抢占、超时：
  `gpu.sh` 收到 SIGTERM 记 143，以前会误记 0），或 6（学习者进程单独死了、没写报告：OOM、段错误）；
  没有报告时其他退出码（如 `gpu.sh` 自己的脚本错误 1）是终态、响亮停链。STOP 是我们自己的
  「service stop」（操作员停）且学习者没报失败时判 `stopped`（有没有报告都一样），不续。
  **停链只用 `supervise.py stop`**：直接 `scancel` GPU 作业和被杀分不出来，会被当成硬死自动续跑
  （占一次续跑次数）；不续：其他学习者
  失败（含启动失败：恢复不了的 CP 也写报告，链立即停）、只有 STOP「service failure」（GPU
  预检、GPU 数、MPS、内存守卫，会重复）、提交失败或预检拒绝、磁盘停止、代码/配置/站点/源码
  有变。最多 `resume_max_total`（3）次，用完以第一
  原因响亮停止（`FINAL.json`）。GPU 排队时间算在窗口里（deadline 在链开始时定下，学习者跨 CP
  保持）：每代在 gpu.sh 确认开工时记 `queue_seconds` 和剩余窗口并打到 stderr，`FINAL.json` 记
  `queue_seconds_total`；没做“窗口从 learner.ready 起算”（要学习者自己定 deadline、supervise
  再读回来，两处改动）。开工后任何信号都不取消作业（负责人 11-16：登录节点重启、登录服务在 ssh
  退出时清理用户进程都发 SIGTERM，nohup 挡不住，20 h 长跑不能因此死掉）：supervise 收到 SIGTERM
  只打一行醒目提示（本链已无人监督、怎么停）并退出（143），作业和评估看守照跑，但没有续跑、读数、
  运行记录和 `FINAL.json`；SIGHUP 照旧（nohup 下忽略，否则 supervise 死掉、作业照跑）；SIGKILL
  同样作业照跑。**重新接管一个在跑的链目前不支持**（`start` 拒绝已存在的链目录，#587）。若登录
  服务开了 logind `KillUserProcesses=yes`，退出登录会杀掉 nohup 起的 supervise：用
  `systemd-run --user --scope nohup …` 起，或请管理员为账户开 lingering（`loginctl
  enable-linger`）。操作员停链 = `supervise.py stop CHAIN --site S`，不管 supervise 还在不在，
  按链记下的作业号（`state.json`、各看守的 `state.json`）：写 `chain-CHAIN/STOP-REQUESTED`（还活着
  的 supervise 此后不再续跑），按记下的 pid 结束评估看守的进程组（先核对仍是 `fast_eval.py`；在起链
  的那台登录节点上跑）并取消它们还活着的 Slurm 作业，写 STOP（「service stop: operator stop …」，
  学习者和服务按它停），最多等 `stop_grace_seconds`（180 s，未量）让 GPU 作业自己结束，再取消剩下的
  （先依赖、后 GPU），结果写 `chain-CHAIN/stop-report.json` 并打印；有作业可能还活着、scancel
  失败或 squeue 读不到时退出 1。STOP 之后学习者还要训完冻结的数据集、排空、发最后一个 CP：准备主机
  （`prep.sh`）一直服务到学习者写出 `learner-report.json`（或 GPU 作业没了），不再见 STOP 就停（V6
  冒烟第二次：准备主机先停，学习者分片 EOFError、fence，没出最后的 CP）。学习者收尾超过
  `stop_grace_seconds` 时 stop 命令照样取消，最后的 CP 就丢了。GPU 作业还在、全部 env 作业却已结束，且 GPU 侧没让它们走（无 DRAIN/STOP、不在 `env-drain-at` 前的迟到窗口内）、也还没报告（无 `learner-report.json`、无 `gpu-rc`，否则按续跑规则判）持续 `env_lost_grace_seconds`（180 s，0 = 关）：同样写 STOP、等、取消，以 `env_lost` 停链，不续跑、不重交 env 数组（V6 冒烟：100 个驱动启动即崩，两张卡空等数据）。驱动自己的寿命 `driver_warm_seconds + driver_steady_seconds` 必须不短于本代墙钟（预检 time_limits 拒绝）：以前 4800 s 会让 20 h 长跑的驱动约 83 分钟后全部退出（#586 评审），现为 72000。env 节点自己也会走：GPU 作业从队列里消失（squeue 明确答结束/查无此作业；squeue 失败只算不知道、并清零计数）持续 `env_gpu_gone_grace_seconds`（300 s）而没有 DRAIN/STOP 时，`env.sh` 让驱动排空、打一行醒目提示（`gpu-gone.txt`）并以 6 退出，免得 GPU 作业硬死（不写 STOP）且无人监督时 env 核一直占到 `--time`。不覆盖：恢复失败的 CP 不会退回更旧的 CP（下一代失败，链停）。
  每 `readout_every_seconds` 追加读数到 `chain-CHAIN/readout.txt`；学习者每发布一个 CP、续跑前
  和链结束时写运行记录 `chain-CHAIN/run-record.json`（最新）并追加 `run-records.jsonl`（写失败
  记日志、不停跑）。两者共用每代一个 `readout.Readout`：只读上次之后新增的字节（学习者事件、
  学习者状态、各 driver 日志累加成运行总和；小采样文件整读），出错后丢弃、下次重建。带
  `--eval-*` 时每个套件
  一个常驻 `tools/friday/fast_eval.py` 看守（#528，不带 `--checkpoint/--once`）：每
  `eval_interval_seconds` 评本链最新 CP（`--chain`：只认 `g-fc-CHAIN`、`g-fc-CHAIN-rN` 两种训练代，
  共用 peer 的别的链的 CP 不选；V6 冒烟第二次曾跨链选），`eval_seeds` 局 `eval_suite`（空 = 固定 A0 100），
  `--eval-baseline-plan` 给配对报告，报告旁写 `eval-record.json`（阶段秒数、同时在跑局数、
  逐种子结果、配对比较；写不出时响亮记 `evaluation_record_failed`，报告照常计入配对）；
  `eval_a10_enabled`（默认关，#530 S-2 后开）再起一个
  A10 50 局看守。`--eval-execution` 是模板，链写一份 `storage_mode = "shared_local"`
  （站点 peer，不走 R2），并记 `derived_from`（模板路径、sha256、改了哪几个存储键）：模板旁的
  重冻记录钉的是模板本身，看守先核这份拷贝除存储键外与模板逐键相同、模板未改，再按模板核记录，
  所以换过 adapter 的基线也能配对（#598；记录仍必须是这份模板的记录）。看守带
  `--until-file <链目录>/FINAL.json`：链结束后不再按间隔开新轮。`FINAL.json` 的
  `final_checkpoint` 是 supervise 看到的本链最后一个 CP（本链没出 CP 时为 null）；看守只认这个
  CP，共享盘还没显示它时响亮记一次 `evaluation_final_checkpoint_not_visible` 并继续等（给了
  `--deadline-at` 时等到其余量用尽；`eval_final_grace_seconds`（默认 1800，0 = 等到 stop）
  从首次看到 `FINAL.json` 起算，到时仍不可见就响亮退出，原因
  `final_checkpoint_not_visible_after_grace`），出现后立刻评一轮；等已开的每一轮离开准备/在跑（报告、
  推迟、失败、投递未知都算，不重试），然后响亮记 `evaluation_watcher_done` 和原因（最后一轮
  投递未知时是 `final_checkpoint_round_unknown`）、自己退出；不取消任何作业。`FINAL.json` 记它们的 pid，
  `supervise.py stop` 仍可提前结束。
  重冻模板（`tools/friday/eval_template.py refreeze --previous-revision REV`）要求 REV 的文件
  算出旧模板钉的 adapter；旧模板自己的 `producer.source_revision` 不一定算得出（A40：f9fa3367
  不行，411106ad 行）。先跑 `eval_template.py find-revision --template OLD.json`
  （`--search-from` 默认 HEAD），它沿第一父提交往回找、给出最新一个算得出旧钉值的提交，再把它
  当 REV；找不到退出 2。核对本身不放宽。
  评估节点类（#612）：站点 `eval_partition` / `eval_constraint`（不写或空 = 今天的 general、
  无约束）由 supervise 传给两个看守（`--partition` / `--constraint`；手动 `fast_eval.py` 用
  `--site S` 读同一对值，旗标与站点不同则拒绝），preflight 的 `evaluation_jobs` 行和每轮报告
  开头都写明。慢节点类 300 局里 80 局到 1800 秒时限被切（另 1 局早期 TimeoutExpired、3 局未开始）。分批轮里各批账本自己决定是否计分：某批的正式审计
  局没跑完，该批整批不计分（局仍在行里），报告点名 `batches_not_admitted`，各批照常合并。
  一轮计划局中"没有跑完并计分"的比例超过 `eval_degraded_share`（默认 0.10，滚动 unknown 门槛；
  unknown、censored、未开始、save_failed 等、不计分批里跑完的局、没交报告的批的局都算，没交报告的
  批的局留在分母里）时报告首行标 DEGRADED 和各分项计数，看守照常报告并响亮记 `evaluation_round_degraded`（排除并计数，
  不作废、不停链）；这样的轮做基线时起步检查直接拒绝。
  评估每局时限（#614）：`eval_episode_seconds`（默认 1800，最多 7200 = `evaluate.MAX_EPISODE_SECONDS`，
  含 Host 准备和对局）和作业墙钟 `eval_wall_seconds`（0 = 按局数和快节点步速算）由 supervise 传给
  两个看守（`--episode-seconds` / `--wall-seconds`）。到时限还没结束的局照旧切掉、记
  unknown/censored、不重发。慢节点类每局约慢 5 倍（同种子同决策，墙钟比中位 4.9）：1800 秒切掉的
  局都还在走（约 0.39 决策/秒）。慢节点类要用 3600 秒时限和 17000 秒作业墙钟，否则最后几局排不进
  （排进新局的窗口 = 墙钟 − 时限 − 900）；`rl5-long-run-1` 就用这对值，慢节点类一轮 100 局约 3.5 小时。
  墙钟必须大于 时限 + 900（profile、fast_eval、evaluate.prepare 三处拒绝）；时限 > 1800 而墙钟为 0 时
  preflight 的 `evaluation_cap` 行给 WARN。
- 保留与 R7 审计局（#529）：`release_trained_segments = true`（学习者数据面：持久 CP 覆盖后
  删已训练原件）。`r7_games_per_hour`（10，0 = 关）按 env 节点均分，每节点的 admission
  sidecar 持有 `R7Selection`（自己的随机 key、`r7_expected_games_per_node_hour` 估计、
  `r7_max_games` 均分上限），驱动开局前问 `r7`，选中的局 `open.r7 = true`（原件全留）；
  服务侧 `mu_archive = local_mu_archive(run/mu-archive-K, mu_archive_gb 均分)`、跟随器
  `weights = node_cache_weights`。存档满时该行被响亮拒绝（`r7_max_games` 是第一道界）。
  普通局只留账本。fast env 的 `episode_open` 已带 `audit_retain_mu`（kernel.ts），R7 局的
  mu 存进服务侧 mu 存档。
- CP 保留（#555，`supervise.py` + `cp_retention.py`）：每发布一个 CP，链按
  `checkpoint_keep_newest`（默认 0 = 全留；开时 ≥ 2）留最新 N 个、评估看守在
  `eval-*/protected-checkpoints.json` 里认领的、`eval-*/state.json` 里任何阶段的评估点过的
  （含 `prepare_submit_unknown`）、同一 work root 下所有 `chain-*/state.json` 的起点 CP、
  各代起点 CP，以及每
  `checkpoint_milestone_hours` 小时窗口的第一个，其余只删 CP 自己独占的 payload 块
  （manifest、索引和 CP 记录留着，读已删 CP 响亮报 `object_not_found`）。删失败记数、
  stderr 报警、下个 CP 再试，不停训练；认领文件读不了就这一轮不删。运行记录
  `chain.checkpoint_retention` 记 kept / deleted / delete_failed / payload_bytes_kept
  （被留件 payload 大小之和，不是真实占盘：共享块重复计，只被已删件共享的块留在盘上不计）。
  按 manifest 数谁在用每个 payload：另一件也引用同一 payload（heads 未变、续训 mu 与旧版
  相同、评估导出与链发布相同）就整件跳过。已接受的竞态：写手复用了只有被删件用的块，且它
  发布时的读回刚好在删除之前完成，则该件缺块（读时响亮报错）；需要非单调共享且在毫秒窗口内。
  模型发布同样处理（`model_keep_newest`，默认 0 = 全留）：每个 mu 版本一份 model + heads，
  学习者先写节点本地 Store，归档再复制到 peer；peer 上没有活路径读旧版本（推理从学习者本地
  Store 取，续训用 CP 自带权重，评估从 CP 导出自己的模型，R7 用 mu 存档）。留最新 K 个和每个
  被留 CP 记的版本，其余删 model 与 heads 的独占块，记录与 manifest 留着；运行记录
  `chain.model_retention` 单独计数。学习者节点本地的 Store 和 `mu-network` 缓存见下一条。
- GPU 节点本地保留（#558，`segment_learner.py` + `local_retention.py`）：数据面模式下学习者
  Store 在节点本地（`learner-live/store`，归档到 peer），每个被服务的 mu 另有一份整文件拷贝
  （`learner-live/mu-network/<snapshot>`），两者原本只增不减。`learner_local_keep_models`
  （默认 0 = 全留）开时，每次发布后学习者只做集合运算并把一轮删除排进发布进程（单 worker，
  排在进行中的发布之后，所以删时没有发布在写），旧 mu 的本地块和 mu-network 目录在以下全满足
  时才删：不在最新 K 个里；本轮和上一轮都不在 `pool.snapshots`（get_mu 能服务的全部：最新、
  各节点报告缓存的、各租约绑定的；不在其中的 key 本来就被拒且不会回来）；不是最新本地 CP
  记的版本；model 与 heads manifest 都有归档完成收据（peer 已全量读回），否则留到下轮。
  `learner_local_keep_checkpoints`（默认 0）同理留最新 K 个本地 CP（续训从 peer 读）。
  manifest、索引留着，之后读会回落到 peer。删失败记数、stderr 报警、不停训练；每轮一条
  `learner_local_retention` 事件记删除数、未归档保留数、失败、释放字节；本地 store/mu-network
  占盘只在每个新 CP 后的第一轮走一次目录。两个开关都为 0（默认）时学习者不建这个对象：没有
  删除轮、没有事件、不走目录，和改动前完全一样。
- 难度（#530 S-1）：`ascension_mix`（级别 → 权重，和为 1，默认 `{0 = 1.0}`）按最大余数
  分给每个节点的驱动，`env_driver.mjs --ascension` 必填（0–10，无默认）。段格式是独立旋钮
  `segment_format`（只许 `stpd/rl-fast-segment-3`，默认，或 `-4`；两者都带难度级别）。是混合
  比例、不是损失系数（I5）；无自动课程。
- 单战斗血点分（W2，卡 §6.5）：系数 c_hp = 学习者配置 `loss.hp_advantage_coef`，作用范围
  `loss.hp_advantage_scope`；两者和其它配方键一样来自闸门 `learner-base.json`，经
  `learner_config.build` 原样进 learner.json，由 A9 与 `rl5.json` 逐键比较（不在
  `ALLOWED_DRIFT`，无默认值），续训锁定。默认 c_hp 0 = 关，训练与以前数值相同。
  - 单位：A_hp（段里的 `hp_adv`）是 HP 点（几十），A_outcome 是 0..1 奖励单位，c_hp 预期在
    0.001–0.01。
  - 范围（owner：血点分用于单战斗练习）：`"combat_ticket"`（默认）只给 `ticket.end ==
    "combat"` 的段加，整局里的战斗行不加；`"all_combats"` 是实验臂，整局所有战斗都加。
    env 还不发单战斗 ticket（W3），所以在那之前 `combat_ticket` 下 c_hp > 0 实际不加任何行，
    预检 `hp_score` 因此拒绝 c_hp > 0 配 `combat_ticket`（#568）。
  - c_hp > 0 要求 `segment_format = "stpd/rl-fast-segment-4"`，否则预检 `hp_score` 拒绝（学习者
    对旧格式段会 `fast_segment_without_hp_score` 拒收）。做实验 = 改源码 `rl5.json` 的这两个值，
    再 `--set segment_format=stpd/rl-fast-segment-4`；闸门不动。
  - 按战斗类型权重（RL5-METHODS A2）：配置旋钮 `hp_combat_weights`（`normal`/`elite`/`boss`，
    默认 1/1/1 = 不加权）只乘 A_hp 的净血量变化部分及其基线，药水 −5 与死亡 −30 不乘。战斗类型
    = 该战斗首行的公开 `combat.content.encounter_type`：聚合器随行发 `encounter`，格式 4 的战斗行
    缺它时组装拒收（`hp_encounter_required`）；`unknown`（如事件战）按 normal。非 1/1/1 要求格式 4
    （载入配置时核对），预检要求源码特性 `hp_combat_weights`。实验起点
    `--set 'hp_combat_weights={ normal = 1.0, elite = 1.0, boss = 0.3 }'`。
    主管定的读法（2026-11-15，#548 审查）：死亡时降到 0 血（−H_t）和收回的最大血量都属于
    被乘的血量变化部分，只有 −30 与药水项不乘。
  - 药水交给价值头（票 H）：配置旋钮 `hp_potion_mode`，默认 `"points"`（现行为：从该行起每用
    一瓶扣 5）；`"value_head"` 时所有战斗都不扣药水分，且本行所选动作是 `potion.use` 的行去掉血量
    变化部分、只留本场死亡 −30（与同场其他行一样，#572 审查），用不用药水由价值头的结局优势判断（否则战内血量分会鼓励每场都喝药）。死亡 −30、
    战斗类型权重和 HP 头基线都不变。要求格式 4（载入配置时核对）且 c_hp > 0（预检 `hp_score`），
    预检要求源码特性 `hp_potion_mode`。这种模式下免血量变化的药水行数
    就是 `hp_score.potions`；血量项占优势方差的份额照旧看 `advantage_raw.hp_share`。
    `"value_head_neutral"`（#608）同 `"value_head"`，只是药水行的血量变化部分取本场非药水行
    血量变化部分（已乘战斗类型权重）的平均值，本场没有非药水行时为 0；死亡 −30 不变。按场中性
    （不是按数据集），血量分不偏向也不排斥药水。预检另要求源码特性 `hp_potion_neutral`。
  - KL 刹车范围和学习率预热（票 K）：旋钮 `kl_stop_scope`，默认 `"all"`（现行为：小批里所有
    多选行的 KL 和 `target_kl` 比）；`"control"` 只算名为 `control` 的臂的行，含它的存档开局
    `control+save`（存档只改起点、不改采样；探索臂故意偏离，不该触发刹车），这个小批没有对照行时退回全部行，并在该次记 `kl_stop_basis:
    "all_rows_no_control"`、读数计数。`"control"` 下每次都记全部行 KL 和对照行 KL
    （`approx_kl_control`、`kl_control_rows`）。要求阶梯里有份额 > 0 且 T 1 / mix 0 的 `control` 臂
    （载入配置时核对）。旋钮 `lr_warmup_steps`，默认 0（无预热）；N > 0 时前 N 步学习率从 lr/N 线性升到 lr，
    head_lr 同比例；步数取恢复的更新数，续跑接着升，升完后续跑不重来。两个都进学习者配方
    （续跑锁死，默认值不进配方摘要），预检要求同名源码特性。
  - 运行记录（A2）：`learning.*.advantage_raw.hp_share` = c_hp·A_hp 在归一前优势方差里的份额
    （多选行上 Cov(c_hp A_hp, A) / Var(A)，各数据集均值），`hp_std` 是它的标准差；c_hp 0 时两者
    为 null。`hp_score` 按战斗类型给格式 4 组装器累计的战斗数、死亡数与死亡率、每场平均净血量
    变化（H_T − H_0 + M_T − M_0，死亡记 −H_0，不加权）。毛伤害不进段（段里只有决策时刻的 HP），
    不记。
  - 闸门不用补（RL5-METHODS F）：共享闸门的 `learner-base.json` 生成于 #538 之前，没有这两个键；
    预检和学习者启动都经同一个 `learner_config.learner_base`，从运行源码的 `rl5.json` 补上闸门缺的
    `loss` 键（预检 `frozen_recipe` 的 PASS 行列出补了哪些，学习者事件记 `harness_gate_filled`）。
    闸门已有的键仍是闸门的值，A9 照样逐键比较，所以补过键的私有闸门副本照用，值须等于
    `rl5.json`。以后新增的 `loss` 键同样从 `rl5.json` 补，不用重做闸门。
  - **不要往共享闸门里加键**：旧代码（#538 之前）的学习者 `LossConfig(**loss)` 遇到未知键直接
    TypeError，起不来；旧预检只比较 frozen 里有的键，拦不住，要到 GPU 作业提交后学习者才崩。
    supervise 每代都重读闸门。
- env 侧配方（NEW-METHODS-PLAN 11-14 I4–I6、I8），短跑用 `--set` 改：
  - 探索阶梯 `exploration_ladder`（臂名 → `share` 与 `combat`/`noncombat` 的
    `temperature`[`sampling_temperature_min`,3]、`uniform_mix`[0,0.1]，share 和为 1；默认单臂
    `control` = T 1 / mix 0，即原行为）。温度下限 `sampling_temperature_min`（[0.25, 1]，默认 1
    = 原 [1,3] 校验）：低于 1 的臂（如战斗 T 0.7）要先把它调低；档案、fast env 配置
    `inference.temperature_min`（=1 时不写）、推理服务三处用同一个值。一个驱动一个臂（fast env 一进程一个 `inference.sampling`，`open` 不带每局
    采样），按全 run 驱动号 `节点号 × P + p` 平滑加权轮转，任意前缀各臂份额误差 ≤ 1 个驱动；
    `env_driver.mjs --exploration-ladder --arm` 必填。fast env、服务、组装逐行核 applied 与
    该臂一致，behavior_q 即实际采样分布（I4）。
  - 同起点 K 局 `replay_k`（1–16，≤ `env_slots`，默认 1）：同一驱动的 K 个槽开同一 ticket，
    组号 `TAG-kN`、成员号记在 driver.jsonl；中途被拒或早停的成员不补（不重试）。种子消耗变 1/K。
    `env_driver.mjs --replay-k` 必填。
  - 存档开局 `save_starts`（RL5-METHODS B，`tickets.mjs`；默认 `share = 0` = 不存不读，开局命令与
    原来逐字相同）：`share` = 从存档开的局占开局数的比例（按开局数，不按决策数）；`share > 0` 时每个
    新局在每个首领战前（全局 16/32/47 层）的地图和 `random_saves` 个随机层存档，进本节点 `/tmp` 的
    池子（每作业一个、作业结束删、不上 R2）；按 `act_weights` 抽幕（目标 = 各幕「打过的层数」占比，
    补最缺的幕），再在该幕均匀抽层、该层均匀抽一份；`died_later_weight` = 只从「同一幕里死前最后
    一份」里抽的比例（用户未定，默认 0）；每份最多用 `uses_per_save` 次；池超 `pool_gb` 删最旧的（有
    未结占用的不删），用完的也删。只收已录取且证据完好的局的存档；存档开局带原种子，录取照查留出
    种子；读档被拒或结果不明：作废、停整族、不重试。只有同难度、同候选、同初始进度（sha256）的驱动
    能用。`env.sh` 传 `--save-starts`、`--save-pool`。终局标签：第 a 幕的存档开局算已过前 a−1 个
    首领（与同终点的新局同类）；读档后直接进首领战（首领前存档）时，这场战斗从第一行自己的公开
    HP 起算（聚合器在战斗行带 `public_hp`），与新局同一场战斗同分。
  - 读数「recipe」表：按臂、难度、起点种类、K 给局数（完整 = 终局、已录取、证据完好）、平均层数、
    胜场、行数（已录取且证据完好局的模型行）；K > 1 另给组数、满组数、组内层数极差均值。按臂、
    难度、K 三张只算从头开的局，存档开局只进「起点种类」和下面的「save starts」表。
    「save starts」表：每幕入池存档数、问题存档数；完整的存档开局按起始幕、按第几次用给局数、平均
    终层、比存档多走的层数、胜场、原局死了的局里走过原死亡层的比例。滚动成绩只算对照臂从头开的局。
    运行中也看得到（每个 CP 的运行记录）：按起点种类的已开局数与实际份额（存档开局 / 全部已开局，
    对照配置的 `share`），和各驱动最近一次的存档计数之和（无票退回新局、读档失败、停族、抢占冲突、
    淘汰；驱动每次 status 与退出时写进 `driver.jsonl`）。
  - 链内换配方只在代际边界：`chain-CHAIN/env-recipe.txt`，每行一个 `name=value`（同
    `--set`），只许 `exploration_ladder`、`ascension_mix`、`replay_k`、`sampling_temperature_min`、
    `save_starts`，下一代提交时读、记入该代；别的键或值不合法 → 链停。代际边界只在
    可续失败时出现，所以计划内改配方 = 停链、从最新 CP 起新链。
  - 新链换学习配方（RL5-METHODS C，#573）：从 CP 起新链时可以声明改学习者配方，模型和
    优化器状态照留。步骤：
    1. 停旧链（STOP），在读数或 `run-record.json` 的 `checkpoints` 里取要接的 CP id。
    2. 写声明：`--set 'declared_changes={ checkpoint = "CP", changes = { "lr" = { old = 0.000113,
       new = 0.00005 } } }'`。每个和 CP 不同的配方键都要写，`old` = CP 里的值，`new` = 新值。
       只许 `lr`、`segments.head_lr`、`loss.entropy_coef`、`loss.hp_coef`、
       `loss.hp_advantage_coef`（c_hp，血量优势系数；不是 HP 头损失权重 `loss.hp_coef`）、`loss.hp_advantage_scope`、`segments.target_kl`、
       `segments.kl_stop_scope`、`segments.value_target`、`segments.gae_lambda`、
       `loss.explore_ratio_floor`；每个键为什么
       可以改，写在 `python/stpd/rl/recipe_changes.py`。改 `segments.kl_stop_scope` 时，旋钮
       `kl_stop_scope` 也要设成同一个新值。模型结构、奖励表、片段格式、批几何和
       `lr_warmup_steps` 都不许改。
    3. 起新链：`supervise.py start NEWCHAIN ... --resume-checkpoint CP --set 'declared_changes=...'`。
       预检（`declared_changes` 项）会从站点 peer 读 CP 的配置头（不用 torch），列出每个
       `键 旧 -> 新`。预检只比许可单里的 10 个键；别的配方键不同，要等学习者启动时才拒绝，
       那时 GPU 作业已经开始（#582）。以下情况在提交前拒绝：许可单里漏声明的改动、`old` 和 CP 不符、`new` 和本次
       配方不符、新旧相同、不在许可单里的键、没给 `--resume-checkpoint`、声明的 CP 和本次起点
       不是同一个。学习者启动时会用同样的规则再核一遍完整配方，错误码以 `declared_recipe_`
       或 `undeclared_recipe_change` 开头。
    4. 读数：`restored CP:` 行写实际加载的 CP（代、序号、mu 版本、更新数）；不是计划里的
       resume CP 时会写 `NOT the plan's resume CP`。`declared recipe changes:` 行写每个改动，
       以及优化器学习率：CP 里的（restored）、本次配置的（base）、下一步实际用的（next step，
       预热没走完时 = base × 预热比例）。实际加载的 CP 和计划的不是同一个时，记为事故
       （`incidents.restored_checkpoint_mismatch`，读数里 `INCIDENT:` 一行）。学习者按计划给的 CP id
       加载，存储会按内容 id 核对字节，所以这只会是传参出错；它查不出计划本身选了旧 CP 的情况。运行记录里对应 `restored_checkpoint` 和
       `identity.declared_changes` 两项。
    - 语义：Adam 的一阶、二阶矩和步数照留（它们和学习率无关）；只把两组的学习率设成新值。
      `lr_warmup_steps` 按恢复的更新数接着算，不会重新预热。新代会丢掉旧代的队列、在途批和
      value-target 暂存，所以改 `value_target` 或 `gae_lambda` 时不会有按旧值算好的行。
    - 锁定：只有恢复的正好是声明里那个 CP 时，声明才生效。链内自动续跑恢复的是新链自己的
      CP，按原来的精确核对来，所以锁在新配方上。不写声明时行为和以前完全一样：配方不同就
      拒绝（`self_contained_resume_source_config_or_generation_changed`）。

### 第一次 RL5 长跑的配置（`rl5-long-run-1`，主管 11-18 定值）

长跑和它之前的短硬件体检都从仓库里的文件起，不再从议题里拼 `--set`：
- 运行配置 `tools/friday/fastchain/rl5-long-run-1.toml`：叠加在 `rl5-fastchain.toml` 上
  （`base = "rl5-fastchain.toml"`），只列和它不同的旋钮，其余取基础配置。
- 短硬件体检 `rl5-long-run-1-hwcheck.toml`：叠加在长跑配置上，只改窗口、评估大小、评估作业墙钟
  `eval_wall_seconds`（6300）、`min_generation_seconds`（600）和 `checkpoint_keep_newest`（3），配方和其余值同长跑（见下文“短体检”）。
- 冻结配方 `python/configs/rl5-long-run-1.json`（旋钮 `frozen_learner_config` 指向它）：
  `rl5.json` + 下表的配方值，预检 A9 逐键核闸门。
- 叠加规则（`run_profile.py`）：叠加文件每个旋钮仍写满 value/default/why/measured，default 必须
  等于基础文件的值（不等即拒绝），和基础值相同的旋钮拒绝（叠加文件就是差异清单）；`base` 成环拒绝；
  基础文件缺失或不是合法 TOML 时一行原因拒绝。supervise
  的“配置没变”身份对整条链（叠加文件 + 各层基础文件）取摘要，改了基础文件也不会被当成同一配置续跑。

和 `rl5-fastchain.toml` 不同的值（每项一行原因）：

| 旋钮 / 配方键 | 默认 | 长跑 | 原因 |
|---|---|---|---|
| `frozen_learner_config` | `rl5.json` | `rl5-long-run-1.json` | 长跑配方单独冻结，`rl5.json` 不动 |
| `loss.hp_advantage_coef` / `hp_advantage_scope`（配方） | 0 / combat_ticket | 0.003 / all_combats | 血量分开（A26 份额算账），env 不发单战斗票，只能 all_combats |
| `loss.explore_ratio_floor`（配方） | 0 | 0.3 | 离线在价格跑数据上选（RL5-A48）：0.3 基本只托底两条非战斗臂的少见非战斗选择，几乎不碰战斗臂；下一档 0.5（代码上限）中途用声明改动 |
| `segments.value_target` / `capacity_victim`（配方） | segment / oldest | game / hash | 整局真结局传到每行；game 下用 hash 驱逐，不偏删长局后段 |
| `segments.kl_stop_scope`（配方，旋钮 `kl_stop_scope`） | all | control | 探索臂故意偏离，KL 刹车只看对照臂 |
| `loss.kl_coef` / `anchor_coef` / `rho_max` / `gradient_diagnostics`（配方） | 不写 | 0.02 / 0.5 / 1.0 / false | 都等于 LossConfig 默认，行为不变；V5 时代的闸门带这 4 个键，冻结里没有时 A9 拒绝（V6 冒烟踩过） |
| `segment_format` | fast-segment-3 | fast-segment-4 | c_hp > 0 需要逐战斗血量分列 |
| `hp_combat_weights` | 1 / 1 / 1 | 1 / 1 / 0.3 | 首领战掉血权重 0.3（计划 v3 §3.2 起点值，未量） |
| `hp_potion_mode` | points | value_head_neutral | 喝药由价值头判断，血量分按场对药水中性（#608） |
| `exploration_ladder` | control 1.0 | control 0.5 / nc15 0.17 / nc2 0.17 / c12 0.16，mix 全 0 | 一半驱动不变（刹车和滚动曲线的基准），其余非战斗 T1.5、T2，战斗 T1.2 |
| `env_core_budget["2"]` | 290 | 256 | 2 卡 = 16 台 × 16 核；写明（原 290 本来也会被核上限和偶数台裁到 256） |
| `train_target_seconds` | 0 | 72000 | 20 小时学习者训练时间，跨续跑累计，到了正常结束 |
| `long_wall_seconds` | 72000 | 86400 | 有目标时它只是链的墙钟上限；第 0 代剩余墙钟须 ≥ 目标，72000 起链一刻就不够，留约 4 小时给启动、续跑和排队 |
| `eval_batch_seeds` | 0 | 100 | 100 局一轮仍是一个作业；终点要 300 局时只 `--set eval_seeds=300` 换 A0 300 清单即成三个并行作业 |
| `readout_rolling_games` | [100] | [100, 1000] | 1000 局窗口给停滞判断和 A0 30% 滚动胜率 |
| `checkpoint_keep_newest` | 0（全留） | 24 | 最新 24 个（2 小时）+ 评估认领/点过的、各代起点（cp_retention 自动留） |
| `model_keep_newest` | 0 | 256 | peer 上没有活路径读旧版本；约 2 × 推理滞后 p95（131 版） |
| `learner_local_keep_models` | 0 | 256 | GPU 节点本地；get_mu 还能服务的和最新本地 CP 的版本另外自动留；约 2 × p95 |
| `learner_local_keep_checkpoints` | 0 | 4 | GPU 节点本地 CP 副本，续跑读 peer |

和默认相同、按定值确认过的：lr 1.13e-4、head_lr 1e-4、entropy 0.01、clip 0.2、target_kl 0.03、
gae_lambda 0.99、dataset_rows 4096 / 4 个小批 / 1 epoch、`lr_warmup_steps` 0、A0（`ascension_mix`
`{0 = 1}`）、`save_starts.share` 0、`replay_k` 1、`service_cores` 16 + `rank_cores` 16（32 核 GPU
节点）、8 台备料主机、`resume_max_total` 3、A0 100 局每 7200 s + A10 50 局看守、`eval_degraded_share`
0.1、`checkpoint_milestone_hours` 0。

保留算账（20 小时；CP 每 300 s 一个、每个 84.9 MB，实测 V5；发布率约 2,100 版/小时，由 V6 冒烟
7.8 分钟到 mu 276 推出；GPU 节点全留约 45 GB/h，推出）：
- CP（peer）：全留 240 个 × 84.9 MB = 20.4 GB。现在最多 24 最新 + 评估点过的 ≤ 20（A0、A10 各每
  2 小时一轮，各 10 轮）+ 各代起点 ≤ 4 = 48 个 ≈ 4.1 GB。
- 模型发布（peer）：每版约 10.7 MB（V5 冒烟 peer 38.5 分钟约 14 GB 模型发布 ≈ 22 GB/h ÷ 2,100，推出），
  全留约 440 GB；现在 256 × 10.7 MB ≈ 2.7 GB + 被留 CP 的版本 ≤ 48 × 10.7 MB ≈ 0.5 GB。
- GPU 节点本地：每版约 21 MB（Store 块 + mu-network 整拷贝，45 GB/h ÷ 2,100，推出），全留约 900 GB；
  现在 256 × 21 MB ≈ 5.4 GB + 本地 CP 4 × 84.9 MB ≈ 0.34 GB。
- 合计：peer 约 7.3 GB、GPU 节点约 5.8 GB（全留时约 460 GB、920 GB）。不在这里的：WAL（≤ 20 GiB 上限）、
  训完即释放的段和 value-target 暂存、R7 mu 存档（`mu_archive_gb` 60 GB 上限，分给各服务）、评估导出。
  每版字节是推出值，短体检读两个文件系统的剩余空间斜率核对。
- 中途换链（声明改动或新链接旧链）只从评估过的 CP 起；否则先停掉旧链再起新链——旧链的保留只护
  自己评估认领/点过的 CP，没评估过的较旧 CP 可能在新链读到之前被删掉。

短体检（`rl5-long-run-1-hwcheck.toml`）：
- 规则：每个作业都能进 60 分钟分区（测试钉住）：第 0 代 GPU 45、备料 60（45 + `prep_lead_seconds`
  900 s）、env 55 分钟（训练目标 1500 s，`env_wait_seconds` 和 `remote_prepare_wait_seconds` 各 300 s）。
  链墙钟上限 7200 s 不是作业时长（有训练目标时每代作业只要剩余训练 + 预留），留给备料排队（≤ 1800 s）、
  GPU 排队（≤ 900 s）、两次启动和被杀那代丢的尾巴；`min_generation_seconds` 600，续跑后剩余训练
  ≥ 600 s 时才再起一代。GPU 排队超过 900 s（11-19 第二次是 941 s）会按上限停、不花 GPU。
- CP 每 300 s 一个（冻结配方锁定，A9 不许改），1500 s 训练约 5 个 CP，多于留 3 个，保留和删除都会真跑到；
  评估每 900 s 一轮（A0 100 局，是决策轮、按种子和登记基线配对，`DECISION_GAMES` 100；A10 16 局），
  训练期间至少一轮链内评估，评估认领保护也会被跑到。
- 操作步骤：第 2 个 CP 落盘后，`scancel` GPU 作业一次（不是 `supervise.py stop`），看三件事：
  链自动续跑且从最新 CP 接上；peer 上超出最新 3 个的旧 CP 被删；评估认领过的 CP 没被删。
- 预计时间（两个数分开）：GPU 时间约 35–45 分钟（25 分钟训练 + 预热 + 一次续跑）；总墙钟（排队 +
  备料 + 链 + 收尾那轮评估：A0 100 局作业墙钟上限 17000 s，16 核上约 1.4 小时（RL5-eval-baselines 实测 5,133 s））约 2–3 小时。
- 要读的：两个文件系统剩余空间斜率（核对上面每版字节推出值）、续跑耗时、评估作业时长、新配方键
  （`explore_ratio_floor` 0.3、三个 `segments` 键）第一次上硬件是否正常。

站点侧（不进仓库）：
- 起点：`start_package` = a46afd12 的包、`warmup_file` = 3cf87760（同 V6 冒烟），是从包新起一条链，
  不是 `--resume-checkpoint`。
- 闸门 `learner-base.json` 必须带 `segments.value_target = "game"` 和 `segments.capacity_victim =
  "hash"`：闸门缺的键只补 `loss` 块（`gate_recipe`），`segments` 不补，所以用一份私有闸门副本（V6
  冒烟同样做法），否则 A9 拒绝（`effective '<missing>'`）。闸门里没有的 loss 键（如
  `explore_ratio_floor`）从冻结配方补；闸门里有的必须等于冻结值。闸门副本若取自冻结学习率的跑（如
  价格跑的 lr 1e-12），`lr` / `segments.head_lr` 要改回冻结配方的值（1.13e-4 / 1e-4），否则预检
  `frozen_recipe` 拒绝。
- 评估节点类：站点 `eval_constraint` 填快节点类的 feature（预检 `evaluation_jobs` 不填只警告）。
- GPU 卡型的 `node_cores` 照实填（32 核节点放得下 16 + 16）。
- 短体检要 SGD 离线 dump 时：`sgd_offline_dump_every` 不是运行配置旋钮，由闸门 `dataplane.json`
  原样带进学习者；体检用的闸门副本加这一键（可选 `sgd_offline_dump_limit`，默认 20 个文件），长跑的
  闸门副本不带。

命令（链名自定；其余参数同上文 supervise 一节）：
`supervise.py start CHAIN --profile tools/friday/fastchain/rl5-long-run-1.toml --site S --cards 2
--card-type T --layout 1i+1t --eval-...`；体检把 `--profile` 换成 `rl5-long-run-1-hwcheck.toml`。

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
`watch.py mode=segments` 的全局unknown门（卡U4 C，替换原来按attempt累计的5%门）：按结束时间取
最近30分钟全部已结束和已证失联的局，分母至少100局；≥10%发typed `attention`请负责人查，训练继续；
≥20%自动停（`training_unknown_fraction_above_limit`，`stop_detail.rule=rolling_unknown_window`），
原件全留。可用`unknown_window={window_seconds,attention_fraction,stop_fraction,min_denominator}`显式改；
`unknown_stop_fraction`在segments模式只再管未决准入比例。c11回放：10%比原5%停早约27分钟。
每个unknown/save_failed在收局时按已记事实分为`environment_gap`/`infrastructure`/`save_ledger`/
`wall_limit`（单局墙钟到点，截断局），带层数、步、动作、节点、原生错误；对不上或两类都有记
`unclassified`并写原因，不猜。#386自动排除节点上的未分类/环境类按`node_auto_excluded`计基础设施；
基础设施类不进模型统计（`unknown_overview.model_unknown_fraction`），按节点隔离照旧。
已被#386自动排除的节点，在排除时刻及以后结束的局不进全局门的分子和分母（用户11-07"不算，只报警"），只单计
`unknown_window.excluded_node_unknown`并每节点每窗口发一次`attention reason=excluded_node_unknown`；排除前的局照计。短段worker正常退出时不因单局unknown/save_failed写全数组STOP；
进程异常仍阻止待启动worker。单局ledger停止和保存义务不变，未知局不重投。短段Actor未显式配置 `unknown_stop` 时不按局部计数停机，
直接 `rl.py launch/learn` 不自带该全局比例门。heartbeat不刷新SGD进展。
可选 `node_unknown_exclusion={}`（默认 window_attempts=30、min_attempts=20、node_fraction=0.25、
fleet_median_max=0.10、max_nodes=4）按sacct NodeList把原close归到节点：某节点最近窗口达阈值、
其它节点中位数仍低时，记typed事件，向该节点在跑actor发原合作USR1（打完当前局不再offer），
之后补位与新attempt的actor、CPU步和评估job的`--exclude` = 配置exclude_nodes + 自动排除节点。排除前的原unknown照计入全局门，
不重投；整体都高时只记录不排除，超过上限记录后交回全局滚动unknown门。阈值来自c11回放。
窗口内自动重新准入（RL5 OPS-2，`canary_lanes`>0时，默认开）：`max_nodes`是同时隔离的节点数，不是累计数。
被排除节点按第几次（strike）冷却`cooldown_seconds`（默认2/4/8 h，超出列表就本窗口不再试），到期且离deadline
超过30 min时，watcher在该节点上钉`canary_lanes`（默认2）个额外actor（`rl.submit --nodelist`，计入`cpu_budget`，
不够就等并发一次attention），不补位。canary累计`canary_games`（默认10）局无节点状态失败就放回
（`node_readmitted`，证据kind=canary，排除期那段原局仍隔离），并用`scontrol update ExcNodeList`让本attempt
还在排队的actor数组也能用它；有一局失败、或canary全部提前结束，就算失败：USR1让canary打完当前局，
进下一个strike的冷却；canary提交后`canary_start_seconds`（默认3600）内一个lane都没起（全PENDING），也算失败：只scancel这个canary数组，它的lane不再计入`cpu_budget`。放回节点时更新ExcNodeList跳过所有canary数组（它们钉在被测节点上）。哪些局算节点失败：unknown/save_failed里除`environment_gap`、`wall_limit`外都算
（c13f：坏节点的unknown 823/826是`runtime_execution_or_release_unknown`→unclassified，只数infrastructure就漏检）。
慢节点单独判：存档局用时中位数 > 其它节点（≥3个，各≥`slow_min_games`局）中位数的`slow_ratio`倍（默认2），
记`node_slow`，只不再派新actor，不发信号，不占`max_nodes`。`canary_lanes=0`保持旧行为（本watch内永久排除）。
同窗口续跑（`previous_watch_state`）沿用原state；`release_swap.py`在canary开时把前任自动排除写成
`inherited_node_exclusions`（带strike和冷却到期时刻；OPS-2前的记录按排除时刻+该strike冷却算），
新watch首次建state时并进自己的排除，不再写进`exclude_nodes`整窗不用；canary关时照旧并进`exclude_nodes`。
人工重新准入仍可在自然重启点由负责人按证据做（`release_swap.py --readmit`，见“换 release 续训”）：
配置`readmit_nodes`里的节点在同窗口续跑首次启动时移出继承的自动排除，之后照常计数，坏了会再被排除。
请求里不是自动排除的节点不改，记一条`node_readmission_skipped`事件。
每个actor结果带`node_nfs_events`：分配开始和结束时节点内核日志里NFS lost locks、state manager失败、
server not responding的计数差（读不到内核日志记`kernel_log_unavailable`），供节点诊断用，不影响门和排除。
learner发布failed/finished；故障Stop绑定generation，全run人工Stop仍有效。
learner的`failed`按`watch.learner_failure_class`的闭表分类（OPS设计§2 (a)），由事件自己的终止错误决定：
只有终止错误就是SGD fence/abort结果（`error_code=input_owner_fenced_restore_last_checkpoint`或
`error_type=SgdProcessFailed`，且没有`finalization_failure`）时，才看`sgd_failure`。可恢复的只有两类：
SGD子进程级死亡（`sgd_process_stalled`，或带信号的`sgd_process_exited`；无信号的exitcode 1算不明错误，
不含协议违例），以及CUDA OOM（`OutOfMemoryError`）。
这两类不停run：本attempt以`learner_crash_recoverable`停下，写一条`recoveries`记录（错误类型/代码、
起点CP，开新attempt时补上恢复的CP id、CP年龄和丢掉的updates），发一次`attention`；
learner和actor都到终态后，照常从最新完整CP开新attempt。最近2条恢复规则相同且恢复的是同一个CP
（中间没写出新CP）时，停在`learner_recovery_limit_requires_diagnosis`；`max_recoveries`仍是总上限。
本窗口还没写出CP时，从窗口起点CP重进（#388）。表外的一律按不可恢复处理，仍停在`learner_failed_requires_diagnosis`。

审计失败先持久化integrity finding，control不可达也由原owner停住。修后继续用
`acknowledged_integrity_events={原event key:原record_sha256}`具名确认已经诊断的finding；
原finding、失败和unknown保留。连续两次配对区间全负时停止并写具名rollback CP，先诊断再恢复。

archive健康消费磁盘可用量、站点usage配额、inode、积压及观察新鲜度；不足暂停新lease/新局。
未报告某挂载配额不等于无限配额；陈旧/错误观察不当通过。storage_inventory分物理路径种类和
model/heads/CP逻辑载荷，两个时点量增长。热段最后引用释放即删；成功μ发布仅释放本次临时导出
副本，规范Store不变。完整CP全入R2，本地仅冷却已远端逐字读回的旧payload，保最近3份及评估/
回滚引用；共享chunk、活reader、未保全数据不删。结束走原archive drain，不靠人工cron。
窗口结束（watcher的`run_budget_complete`/`absolute_deadline` stop、deadline或版本预算）drain后仍在飞的
world/lease是正常收尾：learner在final CP之后把它们记进summary和finished事件的`dropped_at_window_end`
（计数、episode键，不重投，原件保留），再照常检查actor allocation和SGD退出；watcher只报
`worlds_dropped_at_window_end`。watcher把stop理由和budget_id原子写进`learner/STOP`（它比socket stop先到learner；drain后learner再读一次stop事实），空STOP或别的理由不算窗口结束。其他停法（人工STOP、故障stop、fence、读不到stop）仍报
`unresolved_worlds_after_drain`。每两小时评估的job wall默认10800 s（审计第21行：evaluation-0007在
snapshot阶段到2:05墙）；`release_swap`把更小的显式值抬到这个默认。snapshot仍逐字节哈希两侧，只是16线程并行。
共享outbox（`exchange/.archive`）只有一个消费者（RL5 OPS-2）：watcher启动时起archiver线程，持
`publisher.lock`、在窗口内复制所有pending intent，窗口结束再做最终drain（`archive-final.json`）。
actor进程只写durable pending intent（`put_if_absent`，不加锁），不起复制线程、不在NFS锁里传R2、
退出时不drain（summary里`archive.publisher=shared`）；原件一直在共享Store。learner在dataplane下用自己
节点本地的outbox，不做共享outbox的消费者。只有计划里写了`archive_publisher: shared`的actor才这样做（SegmentWatcher的learner启动`learn --shared-archiver`和补位计划会写）；别的启动方式actor照旧自己复制，不会静默丢R2副本。watcher每`archive_check_seconds`（默认300）把archiver的pending数、最老pending时长、bytes/h、last_error写进watch-state的`archiver`，最老pending超过`archive_backlog_alarm_seconds`（默认1800）或错误连续两次不变就报一次attention。最终drain由archiver线程自己做，等待时间按backlog和实测速率算（120–1800 s）；没清完就写进`archive-final.json`并报`archive_final_drain_incomplete`，原件保留，用`async_archive --operation drain`续，不算watcher失败。

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
每局还有墙钟上限 `evaluation.episode_seconds`：watch 默认 1800（#394）；`evaluate.py prepare` 默认 900，最多 1800。撞上限的局照旧记
unknown、不重投，理由写 `episode_wall_limit_reached`（以前混在 `runtime_execution_or_release_unknown`
里）。上限不进协议身份，调高不断开可比性，对局内每一步也没有影响。但它同时决定开新局的窗口（剩余wall需大于上限+180 s）和watch排评估要的截止余量（上限+780 s）。这两条现在对默认配置就生效：默认1800配wall 7200时，评估要离截止至少约43分钟才排，开新局只剩约5220 s，慢节点上50局可能开不完，要同时调大wall或增加路数。11-07 窗口2首个评估所在节点同时跑着
actor，每步比窗口1慢约1.5倍（原因未单独证），打到第44–47层的5局都撞了当时的900 s。
默认报告（`rl_observation.write_report` 的 report.json/README）另报每种难度的正式 reward-v2
平均奖励分、有效对数、配对差和 bootstrap 区间，以及只作诊断的下界 [L, U]（排除局按出错前
公开最高层/已见 Boss 计输局分为 L、计通关为 U）；两模型平均差区间为 `[L新−U旧, U新−L旧]`，
下界之差不是提升的下界。`--excluded-facts` 可补排除局的公开进度。
每次评估并排报两个主分（卡U4 B，watch记录`scores`）：正式分=只算正常打完局的平均奖励分；
保守分=全部计划局，unknown按打到的层计输局分（即该局L值），只去掉基础设施类unknown和没开的局。
unknown再多也不作废；只有原件缺失（没开、待保存、清理未确认、job报错）或抽查不一致才标
`incomplete`，分数照报，但不作比较参照和选best。选best和回归判断默认用保守分
（`evaluation.selection_score`可选`conservative`/`finished_mean`/`paired_max_floor`，记录里写
`selection_score`/`regression_score`；旧记录按`paired_max_floor`，换分口径时不跨口径连成两次回归）。
单局默认墙钟1800 s（`evaluation.episode_seconds`）；`evaluation.avoid_actor_nodes=true`时评估job排除
本watch在跑actor的节点。`evaluation.exclude_nodes`（节点名列表）设了时，评估job和它的1核prepare步
用它代替actor的`exclude_nodes`（自动排除的节点照加）；没设时行为不变。设了`evaluation.pool_nodes`（评估池）时，
评估**只**在池里跑：release_swap把`evaluation.exclude_nodes`写成评估分区里池以外的全部节点，actor排除池；
两者对不上的配置被拒（`evaluation_pool_must_be_actor_excluded_and_the_only_eval_nodes`）。背景是OPS 11-07：
prepare曾因允许的节点全被自己的actor占满等了52和57 min。prepare步或评估job排队超过
`evaluation.wait_alarm_seconds`（默认1200 s）时，每个阶段发一次`attention`
（`evaluation_job_queued_over_limit_no_fallback`），不换到别的节点。兼容修复登记在`tools/friday/compatible-fixes.json`（host_pin X→Y + 固定种子
证明sha256）：只差已登记host_pin的两批照常比较并记`compatible_fixes`注记，未登记的差异照旧跳过。已登记：Host rc.63→rc.64（U4-1固定种子复跑，原卡住局前536步相同后自然获胜），所以窗口1（rc.63）与窗口2（rc.64）的评估从下一个release起可以带注记比较。每局墙钟上限900→1800 s不进观测协议，不需要登记。一局保存失败只排除该局，
批末重试一次保存，仍失败按排除一对报告并告警，不停评估、不停训（RL5 卡 §3E）；正式审计槽
的保存仍失败则整批不计分（`evaluation_audit_save_failed_cohort_not_scored`）。只覆盖
`preserve_report` 失败，轨迹分片上传失败照旧停批。待重试的保存继续占用pending额度与磁盘余量，
新局不会挤占它唯一一次重试所需的空间。

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
新窗口在发布自己budget的首个CP之前，链接一直有效：首个attempt在首CP前死亡时，下一attempt
仍从同一父CP经同一校验重新进入，`consumed_seconds`带上已记账的本窗口秒数（不清零、不继承父窗口）。
评估准备在deadline−900仍未完成时，只发协作信号并记`absolute_deadline_no_World_offer`
（不改标更早的typed stop）；准备job终态后原pending记为skipped评估并清空，不阻塞本窗口链接。

### GPU 后继 holder（自动，有界）

用户 11-07 批准自动续卡，条件是有界。watch 配置写了 `gpu_successor`（同时要有 `held_job_id`）后，
watcher 每 5 min 调一次 `tools/friday/gpu_successor.py`。当前 holder 离 Slurm 结束不到
`lead_seconds`（默认 4 h）时，提交**一个**后继 holder，`--begin` 定在当前 holder 结束前
`overlap_seconds`（默认 30 min）。

后继就是 `rl.launch(held_job_id=...)` 认的那种卡（`rl.held_allocation_detail`）：`smp_gpu` 分区、
watch 的 `gpu` 型号一张（`gres/gpu:<gpu>:1`）、作业名 `rl4-learner`，这三项都不可配置。
`allocation.sh` 由运营模板生成，模板里必须有 `@HOLDER_ROOT@` 和 `@BUDGET_FILE@` 两个占位符。
读配置时（以及 `--check-only`）就检查模板和记录根目录：规范路径、不经过符号链接、自己拥有、权限 700。

硬上限：
- 我们所有未终止的 GPU 作业的卡数（后继自己也算）不超过 `max_gpus`（不超过 2）；读不出卡数的作业按超限算；
- 已有任何一个排队中的 GPU 作业，就不再提交；
- 每个 holder 只有一个后继；
- `sbatch` 之前先写 `records/<holder>.json`。结果不确定（有意图、没有回执，`sbatch` 失败或超时）时
  记 `outcome_unknown`；记录读不出也一样。之后每次检查都报警，永不重试。

只报警、不提交的情况：
- 后继过了 begin 10 min 还在排队：`gpu_successor_pending_past_begin`；
- holder 在提交后继之前就没了：`gpu_holder_gone_without_successor`。

提交和每种拒绝都发一次带类型的 `attention`。交接本身不在这里做：新窗口仍然用
`release_swap.py prepare --held-job <后继>` 换上。在 supervisor 上线前，**开这个功能就必须有人在后继
开始后 2 h 内接手**（续跑换上），否则 `scancel` 后继，不让它按 48 h 预算空占一张卡。
`gpu_successor.py --config watch.json --check-only` 只做检查、打印判断，不提交。

`outcome_unknown` 的人工处置：
1. 用 `squeue -u $USER -o '%i|%T|%j|%S'` 和 `sacct -S <intent_at> -n -o JobID,JobName,State,Submit`，
   按作业名 `rl4-learner` 和记录里的 `begin` 查这次提交有没有成功。
2. 查到了：把记录改成 `outcome: submitted`，并填上 `job_id`。
   确定没有：改成 `outcome: not_submitted`，附上证据（查询时间和命令）。
   原记录先复制一份留档。
3. `not_submitted` 不会自动重交这个 holder 的后继，要续卡由人提交。

### 换 release 续训

换release续训（同窗口恢复、新窗口或只关窗）一律用一条命令准备，它只准备，不启动：

```bash
# Mac：把含目标SHA的develop打成bundle，拷到Friday
git -C <repo> fetch origin && git -C <repo> bundle create /tmp/swap.bundle origin/develop ^<上一release的HEAD>
scp /tmp/swap.bundle friday:<scratch>/
# Friday登录节点（先 module load python/3.11.5-gcccore-13.2.0 nodejs/20.9.0-gcccore-13.2.0）
python <任一含本工具的checkout>/tools/friday/release_swap.py prepare \
  --sha <40位develop SHA> --bundle <scratch>/swap.bundle \
  --predecessor <long-run>/watch-c13 --name c13b \
  [--total-training-seconds 129600] [--exclude-node smp-9-12 --exclude-reason '坏节点 11-07 06:33'] \
  [--readmit smp-9-0 --readmit smp-9-7 --readmit-evidence <节点体检报告>] \
  [--eval-node smp-c03 --eval-node smp-c04] [--max-excluded-nodes 8] [--expect-mode same_window]
```

节点计划（OPS 11-07，都写进watch配置，下个续跑照抄）：
- `exclude_node_provenance`：每个排除节点的来源，`auto_node_unknown_exclusion`（#386，带时刻和窗口数）、
  `operator`（`--exclude-node`，带`--exclude-reason`；或`--node-reason NODE=理由`给已排除节点补理由）、
  `evaluation_pool`、`hardware_class`（Slurm features不含actor节点类型，硬件约束，不是健康排除）、
  `slurm_unavailable`（DOWN/DRAIN/FAIL）或`static_unrecorded`（Slurm没报、来源没记的旧条目）。
  清单按来源、按节点类型计数，并点名actor类型（如fc430）里被排除的节点。
- `--readmit NODE`（可多次，必须是当前排除的节点，必须带`--readmit-evidence`）：从`exclude_nodes`去掉，
  写`readmit_nodes`（watch在同窗口续跑时移出继承的自动排除）和累积的`node_readmissions`记录
  （节点、原排除来源、证据、哪次swap）。OPS F2的`kthread_run: -4`探查已删（NODE-HEALTH-REVIEW 11-08 §2：
  -4是NFS恢复期间我们自己的actor被杀留下的EINTR，跟着大批actor退出出现，把恢复中的节点关了19 h，
  又漏掉了正在变坏的节点）；节点证据改由watcher的冷却+真游戏canary给。同一节点放回两次后又被自动排除，再放回会被拒（`readmission_limit_reached`），先诊断。
- `--eval-node NODE`：评估池，加进actor的`exclude_nodes`，写`evaluation.pool_nodes`，`evaluation.exclude_nodes` =
  评估分区（`sinfo`读取，读不到就拒绝）里池以外的节点 + actor排除节点；准备时的真游戏也按这个列表提交。
  当前被自动排除的节点只能和`--readmit NODE --readmit-evidence`一起进池（否则`eval_node_auto_excluded_needs_readmission`）。
- `close_window`时`--readmit`/`--eval-node`/`--max-excluded-nodes`/`--exclude-reason`都会被拒（不静默忽略）。
- `--max-excluded-nodes N`：写`node_unknown_exclusion.max_nodes`（0–16）。

`tools/friday/release_swap.py prepare`按顺序做下面6步，任一步失败就停在该步，打印
`release_swap FAILED at <步>: <typed code> (<说明>)`：

1. **建release**（照DEPLOY-795622dc/R14）：从上一release的source克隆，取bundle，detached干净HEAD，
   复用env/env-gpu/game/node/dotnet链接，写`ENV-<short>.sh`（只换release路径，内容不打印）。
   `components/contracts/apps`或锁文件有变化就拒绝（`host_stage_rebuild_required`）：本工具只复用
   Host/Runtime stage。release已存在时核HEAD、干净、链接一致后复用。
2. **重冻结身份**：在actor解释器里（执行文件的`python_executable`，`env -i`，同Runtime起子进程）
   重算执行文件`agent_adapter`、producer，learner `input_compiler_identity`；打印新旧对照表和每项变化原因。
   input_spec或词表变了就拒绝（`model_input_changed`，那是新学习run）。
3. **执行文件和learner文件**：续训CP用前任watcher自己的`select_checkpoint`选（最新完整CP，含archive）。
4. **watch配置**：按前任的stop、预算和截止自动选：
   - 前任以`run_budget_complete`/`absolute_deadline`结束 → 新窗口（`previous_window_state`）。
     预算id默认递增，截止默认holder结束−30 min，训练秒 = min(72000, `--total-training-seconds` − 各窗口已用)。
   - 其它stop、预算和截止都还有余 → 同窗口（`previous_watch_state`，保留`previous_window_state`）。
     须在`deadline_at`−600 s−`--launch-margin`（默认1800 s）前启动，清单写明时刻。
   - 其它stop，且预算已用完或已过`deadline_at`−600 s → 只关窗：同窗口配置、前任自己的release和ENV，
     第一个tick就以`run_budget_complete`/`absolute_deadline`停、在`start_attempt`之前，所以不开learner和actor（到点的评估可能先跑完再退出）；
     然后对它再跑一次本命令开新窗口（c12c→c12d→c13）。这时第1步建的release用不上，留给新窗口复用。
   - 离`deadline_at`−600 s不到`--launch-margin`：两种都不安全（启动的watch还会开attempt），拒绝并给出可重跑的时刻。
   同窗口和新窗口都把前任state里自动排除的节点并进`exclude_nodes`；前任配置的`evaluation.episode_seconds`
   低于watch当前默认（1800）时改写为默认，并在清单和`decision.json`里写明（c13d曾抄来900）。其余照抄。
   前任仍在跑、learner job未终态、死attempt缺`control-wal-final.json`（c10）、有未确认integrity finding
   （`--acknowledge KEY=SHA`）时拒绝。
5. **验证**：`read_segment_config`；同窗口`require_watch_continuation`，新窗口`require_completed_previous_window`；
   `checkpoint_link`（即`start_attempt`的CP规则）；`compatible-fixes.json`能读；agent子进程用新身份启动成功、
   错编译器身份被拒（`RL_producer_compiler_identity_differs`，负对照）；新release+CP经`evaluate.py
   prepare-checkpoint/prepare/submit`打一局真游戏，必须自然结束、0 unknown。
6. **就位**：在`.swap-<name>.staging-*`里写完并验完才改名为`swap-<name>/`，再核一次（含启动截止：
   真游戏可能比`--launch-margin`还长，过了就拒绝），最后写`READY.json`。
   失败的staging改名为`.swap-<name>.FAILED-*`，不像有效输出。验证原件在`swap-<name>.evidence/`。

规则全用release里watcher自己的函数，不另写一套。同输入重跑得到同样的文件和hash（`READY.json`对比），
不重打真游戏；输入或已准备文件变了报`prepared_output_differs`。`--dry-run --release-dir <已有release>`跳过建release
和真游戏，可在scratch里对前任state的拷贝演练；dry run写`DRY-RUN.json`（不写READY），启动命令被注释掉。
agent子进程的环境照Runtime的`agentProcessEnvironment`（PATH、TMPDIR、PYTHONPATH）加执行文件的
`adapter_environment_keys`，另加`PYTHONDONTWRITEBYTECODE`以免在用的release多出`__pycache__`。

`swap-<name>/CHECKLIST.txt`列出模式和原因、身份变化、CP、各项验证结果、启动截止和人工核对项，
最后是启动命令（负责人执行）：

```bash
cd <release>/source && source ENV-<short>.sh && source "${FRIDAY_R2_ENV:-…/friday-r2.env}" && \
  nohup python tools/friday/watch.py --config <long-run>/swap-<name>/watch.json \
  --work <long-run>/watch-<name> > <long-run>/watch-driver-<name>.out 2>&1 < /dev/null &
```

为什么要这样：11-06/07三次换release失败都出在手工重冻结：c12漏了`agent_adapter`，c12b漏了learner编译器身份
（构造器检查和pe-probe都没发现，Host driver不经过Python adapter检查），c12d在截止后启动，窗口接不上。
11-07 dry run：对c13 state拷贝准备出的c13b配置，和手工版`watch-c13b.json`/`learner-c13b.json`只差路径和更新的CP，
执行文件逐键相同。

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
