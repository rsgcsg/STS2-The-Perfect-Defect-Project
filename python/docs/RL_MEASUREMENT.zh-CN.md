# RL：系统速度与学习效果分开量

## RL4 短段口径

新短段沿同一 `game-decision-v1`：完整catalog≥2且首次成功SGD才增有效新D；
forced、辅助回放和KL刹车中未成功的批不增。未知前已确认的合法前缀可以贡献D，未知动作本身不能。
`first_use_multi`为累计有效新D，`first_use`含强制行；原件段数、接收数、重复两遍均不是新D。
`updates.jsonl`保留每次SGD的KL/clip、首次使用年龄/楼层/μ差、辅助成功使用；CP恢复接原累计计数。
用原始逐次记录的时间窗相减，整小时含等待/发布/收尾；不要把旧max_reuse上下界公式套给新段。
M4固定真实工作测同成员/数值配置，BF16单列概率差与实际时间，不能以近似相等声称逐位等价。
共享主干梯度只统计辅助损失实际可达的参数，动作专属readout不进入策略分母；
缺少有效共享梯度时角度为null，不填0。完整分解会多做autograd；普通与诊断SGD分开计时。
实际训练进程不做大trace的key_averages/导出统计；固定诊断在无Actor的有界工作上做，
已有trace交独立CPU分析。旧local-inference的score_seconds可含投影/编译；
`stpd/rl-inference-2`的同名计时是scoring worker host区间，含排队后执行、H2D/事件等待/
前向/D2H，compiled-frame解码在handler入队前，不在这个桶里。两者均不是纯GPU kernel时间。
compiled协议的`compile_seconds`另计Actor编码；`request_seconds`从编码/JSON大小计算后开始，
包含评分请求往返，排除段边界的begin容量等待。`compiled_wire_bytes`是编译输入JSON大小，
不是实测TCP流量。服务端`server_seconds`从该μ的本批开始累计到当前microbatch完成，
后面的组可包含前组耗时；不把它当逐请求独占GPU时间。
`batch_histogram`在按μ/形状/面积拆分后记录microbatch数；producer trace的`batch_size`
按请求加权。报告需写分母，不能将小批全部归因2ms合批窗口。

summary的`timing`含`waiting_for_data_seconds`、`input_read_decode_seconds`、
`input_wait_seconds`、`input_optimizer_overlap_seconds`与`target_and_auxiliary_seconds`。
读/解码可与optimizer重叠，不能把各项相加当总墙钟或把重叠全算成收益。
SGD事件的`first_use_age_samples_seconds`保留该次首次成功使用的逐行年龄；
整窗P50/P95从全部原始样本计算，旧逐批分位数仅代表原批，不能平均成全窗分位数。
恢复旧CP后，年龄统计含此前已生成而未首用的库存；按真实来源分组或明确保留这个混合分母，
不能用任意年龄阈值伪造“新策略数据”来源。版本lag、真实SGD lag与概率差分别计量。
主线程`target_and_auxiliary_seconds`与`optimizer_attempt_seconds`是host区间；2秒一次的
NVML采样及这些区间不能拆出准确GPU训练/推理/重叠/空闲比例。若需要该结论，须用
有界真实steady work的CUDA activity证据，离线分析，不在正式Actor线程导出重profile。
缓存首次填充、相同成员热读、连续不同dataset和完整在线循环分别计时；辅助HP/终局uses
上限会改变后续成员，不能把同512引用重复热测倍率作全链收益。配置有旧字段不代表新
prepared_update实际消费，旧能力要核到本次路径/开关。

prepared 路径已有 current+next：下一批准备与当前批 SGD 重叠，不代表多批准备彼此并行。
`request_wall_seconds` 是请求返回墙钟；`sources_ready_wall_seconds`、
`auxiliary_ready_wait_seconds`、`assemble_roundtrip_seconds`、`batch_unpack_seconds`
定位收集和 IPC。`assemble_queue_seconds`、`assemble_unpack_seconds`、
`assemble_cpu_seconds` 是 roundtrip 内的 worker 区间。`source_queue_seconds`、
`source_pack_seconds`、`hot_read_wall_seconds`、`hot_json_seconds`、`hot_validate_seconds`
为各 source 累加；pack 不含进程间 pickle/传输，read 含 Hot owner/锁和完整性读回。
这些桶不能相加成墙钟。正常 Exchange 已验证原文后可复用其 hash 进入 FrameCompiler，
仍查 wire/frame/tensor/动作顺序；直接 prepare_source 和自定义 reader 继续验证原文。

系统速度是冻结工作量的完成墙钟；学习效果是相同预算下的固定评估差值。
两者分别报告。更快、吞吐更高、loss更低，都不自动等于策略变强。
测速工作量可固定，墙钟仍有机器调度、缓存和网络波动，须报告重复次数和范围。

固定TRAIN计算诊断可复用原工具：`python tools/benchmark_rl.py --package /ABS/policy \
--heads-checkpoint /ABS/normal-warmup-cp --input /ABS/train-states.jsonl \
--plan /ABS/cases.json --output /ABS/new-profile --source-revision FROZEN_SHA`。
该历史模式已随按行 `segment_update` 退役，不再用于当前prepared训练资格；真实TRAIN状态/标签之外的概率和TD原为诊断构造；不发布模型，
不进first-use或训练资格。各配置同起点，记录完整工作哈希、成功SGD数、概率差和实际墙钟。

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
reuse退休先于stale检查。旧日志的上下界工具要求max_reuse至少2；无复用方案另核逐条记录。
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

## 旧运行的缺口与新账本边界

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
新运行的具体实现见下一节；旧运行缺失的字段不能事后补造。账本按批写入原观测链，成本实测；后台预取的采用仍须保住准入/批次顺序，或另获学习变化授权。


## 新运行的逐条账本

启用 `decision_ledger` 时沿原 `run_id:step.index` 记录，无第二套游戏ID。
新轨迹的 `generated_at` 来自原typed journal的global_choice.recorded_at；
上传站关联原分片记录的finished_at（分片发布前的时刻，不冒称网络ACK）。
learner只对已读回episode计生成/上传/准入或拒收，日志名为 `decision-ledger.jsonl`，
按原content/shard关联。只有optimizer.step成功后增加uses、首次训练和有效新D；
失败尝试不增加，退休按stale/reuse及原uses分组。unknown保留生成/上传与拒收，不准入。
未发现、在途、未封存actor的选择不在learner cohort中，不能把局部守恒改称全链全局守恒。
收口要另核actor原轨迹/发布记录、未开/unknown和在途分母；live未训项是右删失。

```bash
python -m stpd.rl.measurement --output decisions.json ledger decision-ledger.jsonl \
  --start-at RUN_WINDOW_START_EPOCH --end-at RUN_WINDOW_END_EPOCH
```

可按顺序传多份完整JSONL段，exact重复内容去重；缺失准入/成功使用连续性会失败。
输出有效新D/小时、cohort首次使用率、uses/退休分布、生成到首训延迟分位数与时钟未知数。
默认窗为所给记录首末事件，正式整轮吞吐须显式给含启动/等待/收尾的run窗口。
跨机延迟附 `clock_offset_bound_seconds`；未知写null，不把原始epoch差称校准延迟。
有已测偏差界时，每次first use同时记录延迟区间，不把负原始差裁成0。
完整CP存live账本状态和RNG；按内容SHA冻结JSONL段进原Exchange `decisions/`，
正常收口等待所有段写完再冷读核hash。原件不重写、段上传不重新授予训练资格。

训练事件新增采样/锚点选择、reference/forward/backward/optimizer的host elapsed；
GPU工作异步排队，遇到的设备等待计入当时host阶段，不冒称互斥的GPU kernel时间。
reader/projection/CPU准备另列background耗时，可与owner训练重叠，不与wall机械相加。
STOP查询、数据等待、capture、version等待、reader积压、账本自身写耗时分别保留。
TensorBoard将训练态模型熵及按候选数归一的熵，与原实际抽样分布熵分开；
写advantage、原回报、buffer uses/版本落后直方图和退休计数；无critic的value loss/state value标不适用。

首次概率门用相同acting权重、官方投影/完整动作目录、eval模式核记录概率/logits。
训练态有dropout与identity mask，ratio不机械要求1、KL不机械要求0；
起点与KL参考不同的eval KL本来也可非零。诊断门不静默关闭训练随机化。

## RL4.1 预算表与新增观测

整链报告首屏沿[接口SOP的E1–E8预算表](RL_ACTOR_LEARNER.zh-CN.md#进程线程和质量预算)，每条边都填
吞吐、延迟、host峰RSS/显存和**长期磁盘增长MB/h**；没有实测写未测，不用“热集有界”代替CAS/账本/μ/CP增长。
G0只代表局部固定工作，G1走正式profile、真实段、正常CLI，并验证CP保存和只换learner后的SGD。

正常progress的`memory`是该learner进程的高水位RSS及CUDA allocator allocated/reserved峰值；
summary保留实际learning_configuration；model_published另报后台发布进程RSS和发布耗时。
checkpoint_captured/published分别记录CPU快照、编码/Store发布耗时、字节、该进程RSS高水位。
CP节点数从原tensor-tree header的树数出，和codec `MAX_NODES=100000`对比，不能用CP字节数代替节点数。
OS外部采样给各spawn worker/actor/Host的RSS、保存阶段的瞬时峰和nvidia-smi忙闲；累计高水位不是某阶段独占开销。

LocalMuScorer的评分分位数仅含forward+结果取回，排除compiler.decode和段边界容量等待。
≤4096次时保留全部评分耗时；超过时用独立RNG的4096项均匀reservoir，并报告population/sampled，
不影响policy/训练RNG。多actor的全窗分位数要合并原样本且说明抽样权重；不能平均各actor的P95。
文字缓存命中按实际lookup的hit/miss聚合。估路数用完整R/h、多选比例、实测首用率，评分P50的倒数不能直接当actor产能。
