# RL：系统速度与学习效果分开量

系统速度是冻结工作量的完成墙钟；学习效果是相同预算下的固定评估差值。
两者分别报告。更快、吞吐更高、loss更低，都不自动等于策略变强。
测速工作量可固定，墙钟仍有机器调度、缓存和网络波动，须报告重复次数和范围。

## 唯一单位表

本表名为 `game-decision-v1`。定义随固定 P1 profile 和完整合法 catalog 解释，
并非任意协议拆分下都不变的自然常量。改 profile/分步方式须另命名，不能接旧曲线。

| 单位 | 从哪里数 | 包含与排除 |
|---|---|---|
| 游戏决策 D | 原轨迹 step 的 source observation，完整 catalog 长度至少2 | 一个 global_choice 算1；一次多选 BoundAction 算1；后续出现新选择另算。规则自动推进、无选择读数、catalog只有1的强制动作不算 |
| 模型选择行 R | 原轨迹每条 step，和当前 learner 投影行对应 | 包括catalog=1；不得把旧 `rl_samples`/steps 直接标成D |
| 生成 D | 打局端记录选择时，以 episode run_id + step.index 唯一标识 | unknown/截断前确实发生的选择也计生成，终局分母保留；它们不自动具备训练资格 |
| 上传/接收 D | 以同一ID关联原轨迹发布记录/learner准入 | 上传成功不等于准入，准入不等于训练；分片数量不等于D |
| 有效新 D | 合法窗口内准入，且首次成功完成optimizer更新的D | 每个ID只计1，首次使用落在统计窗；unknown、锚点、从未训练的准入、再次复用均不增加 |
| RL样本使用 U | 每次成功更新实际使用的R，按ID累加 | 同一行用3次计3U，但只可能增加1个新D；强制动作的U单列 |
| 锚点使用 A | 每次更新实际用的独立TRAIN锚点 | 不算RL生成D/有效新D，单列计算成本；评估种子不作锚点 |
| 更新 | 成功optimizer.step及相应状态完成一次 | 内部诊断，必须同时写RL/锚点批次，不是速度单位 |
| 版本 | 已发布的不可变model record | 不等于更新，也不固定等于8更新；发布阻塞时实际间隔会变 |
| 局 | 每个已开种子offer，结果保留win/loss/unknown/censored | 仅辅助，同时写总D和每局D分布；禁止用历史“约150”换算 |

决策/秒 = D/指定墙钟；秒/决策 = 墙钟/D，D=0时后者为null。
多核汇总同时写总墙钟和核秒，单核率用 D/核秒；延迟分位数不能取倒数叫吞吐。
有效新D/小时的墙钟包括等待、停机和收尾；局部稳态窗另标条件，不能替换整轮成本。
分桶至少为候选数2–4/5–8/9–16/17+；实体数325–512/513–768/769+可作当前模型的附加诊断。
桶界和各桶数量随工作量冻结，不从各桶的非配对速度拼一个全局倍率。

## 已有离线工具

从 `python/` 调用，输入为已保存文件；无凭证、网络、游戏或训练副作用。
输出必须是新路径，已有文件会被拒绝。读失败要保留失败，不用零填缺失。

```bash
python -m stpd.rl.measurement --output window.json window \
  --start before.json --end after.json --batch-size 64 --max-reuse 3
python -m stpd.rl.measurement --output inventory.json inventory trajectory1.json trajectory2.json
python -m stpd.rl.measurement --output comparison.json compare runs.json
```

`window` 接受原event、含event的summary、或含latest_status.learner的心跳。
只对同run、同一计时器生命周期的两个累计事件相减；更新计数会跨resume保留，
计时器却重启，所以不能拿累计updates除新计时器墙钟。epoch减elapsed仅作生命周期
相容性检查，保留原进程/续训证据；它不是进程身份认证。
`train`包含采样/排序/锚点等待；`poll`包含列键/排队/准入，不涵盖所有后台读取。
`wait_anchor`嵌在train，`version_wait`可能嵌在capture，不能机械相加。
未归类时间给范围，包含STOP查询、event/TensorBoard I/O等；不能统称网络。
非progress事件的mean_staleness描述当前buffer，不能冒充实际训练批次平均落后版本。

当前计数的守恒是 `准入R = stale移除R + 用满退休R + 当前buffer R`。
过期计数不记原uses，可能已经用过。工具只给R的条件上下界，**不输出有效新D估值**。
上下界要求batch/reuse自本run起不变、成功更新计数完整、CP状态一起恢复，且
reuse退休先于stale检查。目前上下界工具要求max_reuse至少2；无复用方案另核逐条记录。
条件不满足不运行此换算，先取逐条记录。
`inventory`用原trajectory validator校验，数原catalog；校验成功不改变原训练/计分资格。

## 固定工作量基准

优先复用 `tools/benchmark_rl.py` 和原Managed ledger/pipeline，禁止另写游戏循环。
每份基准清单绑定以下内容，并对规范JSON本身取SHA256；不能只冻结一个“64步”的名字。

| 子链 | 冻结什么 | 计时边界和正确性门 |
|---|---|---|
| learner消费 | 原轨迹/锚点内容哈希、投影版本、完整批成员顺序、action/behavior/advantage、初始模型/optimizer/RNG、reference-cache命中计划 | 从相同起始状态做完同一批次表，到CUDA同步和末状态；CPU预编译/冷启动另外计费，不藏在计时窗外 |
| 原件到更新 | 同上，加固定分片及到达/准入/抽样顺序 | 从首字节读取到末更新及末CP读回；等待/投影/网络/发布都计入，后台阶段可重叠，不相加当总墙钟 |
| actor | 模型包/权重、完整训练专用seed manifest、Managed/Runtime/adapter pin、难度/角色/取动作方式/随机种子 | 原正常入口，从启动到同一批终局+记录/上传/读回；重复unknown不允许，记录失败为未完成工作量 |
| 引擎 | 同一初始状态和公开合法动作序列/后继校验 | 区分引擎本体与SDK/IPC/推理/保存；旧随机固定prefix只能称对应工作量，不能当完整actor |

对A/B按ABBA或交替顺序至少各3个完整独立重复，预热和冷热缓存分开报。
报告原始每次总时间、median/min/max/相对范围、环境、峰值内存和CPU/GPU条件。
工作量相同不表示测时“绝无噪声”。固定训练批次应核最终参数/optimizer及中间输出；
固定actor应核逐决策输出、终局、unknown和保存结果，不只核argmax几个样本。

`compare`输入为JSON数组，每项包含 `variant`、`workload_sha256`、
`conditions_sha256`、`wall_seconds`、`complete`、`outputs_equivalent`。
后两者必须由独立原结果核验后填写；工具不是等价验证器。
两个hash分别绑定工作量及相同机器/计时条件，候选源码另记，不混进共同工作量hash。
不足3重复会标稳定重复门未过。它不产生学习效果结论。

FP32重排浮点运算通常只达误差容限一致；同权重argmax全同不证明长训练参数或
闭环路径逐位相同。后台预取若改变到达/准入顺序，也可能改变抽样和优势baseline。
只有输出/顺序契约已保住的实现替换，才可仅测系统速度；其余另做固定预算学习比较。

## 学习效果只复用OBS

评估入口由OBS的 `stpd.evaluation.rl_observation` 负责，读取同一原Friday result/ledger，
按seed配对，保留unknown/unpaired，输出差值、区间、分布、Boss证据和样本量。
本工具不复制统计实现，不运行新评估，不把最大层数推断成Boss通关。
在同一难度、固定取动作模式及冻结版本比较；外部强度推断须另核协议和seed抽样前提。
预先选择相同墙钟预算或相同新D预算，不能看完结果再挑更好看的分母。
区间整体大于0为变强、小于0为变差、含0为分不出来；零差异不是等价性证明。
固定suite的bootstrap仅是该suite的敏感性，不自动是总体置信保证。
多次看评估、按评估挑版本须记录选择偏差；固定独立确认suite保留到最后。

## 下一代才接的逐条账本（未接运行中的learner/actor）

沿已有trajectory的 `episode.run_id:step.index` 建派生账本，不改原轨迹或Exchange语义。
每条保留run/actor/model version/原content hash/catalog大小/候选及实体桶，
并关联生成、发布、收下、首次成功训练、每次使用、退休原因/原uses。
生产者本机用monotonic计时；跨机器epoch延迟要记录时钟偏移上限。
不能把现有record.finished_at（保存后、publish_shard调用前）当决策生成时刻。
当前trajectory_to_training实际在准入时计算，是“发布记录准备到准入”的尾段延迟，
不是决策生成到第一次训练，不能沿用旧名解释。

生成 = 待上传 + 已上传；已上传 = 在途/待准入 + 拒收 + 已准入；
已准入 = 从未用过 + 至少用过。退休按stale/reuse及原uses分层。
optimizer失败不能把预增uses当成功训练；只有更新成功后才能确认首次使用。
跨窗统计要保留起终队列，结束仍在途/未训的是右删失，不能立即标丢弃。
账本按批写入原观测链，成本实测；具体埋点/后台预取上线仍需换代授权。
