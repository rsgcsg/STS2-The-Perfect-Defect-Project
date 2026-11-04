# RL Actor—准备—Learner（Managed，A0 Defect）

RL4.1 沿原 Actor、Exchange、Runtime、Evidence、Store 和归档入口接线。
actor 默认在 Friday CPU 本地评分；CPU 进程准备完整批，learner 只负责数值更新、发布 μ 和保存 CP。
本页拥有接口和操作方式；实际派工、预算、候选身份和验收由 [CURRENT](../../docs/memory/CURRENT.md)
指向的当轮任务负责。源码/CPU fixture 通过不代表真实 World、部署、GPU 性能或模型实力通过。

## E1–E8 的合同、实现状态和选择

只用四种实现状态：默认（唯一）、临时（写退出条件及收口时点）、备选（写当前使用方/条件和选择规则）、只读（只读旧数据）。不用的实现移除，必要时从 Git 取回。
组件承载角色，部署位置不另生业务链。E1/E7/E8 的含义变化由 RL4.1 卡授权；
E3 是准备角色从训练中细分，其他边在原合同内替换实现。

| 边 | requires → provides；失败含义 | 实现状态 | 开关 / 唯一入口 |
|---|---|---|---|
| E1 打局→段 | Runtime 确认的 completed directive、真实 successor、同 μ 全分布及尾值、原件 fsync 承诺 → ≤64 行单 μ 热段；未知动作不入段 | **默认**：Exchange 本地 `.rl-hot/<run>/<generation>`，准入不等待 archive；自然终局尾段仍等待 typed 终局 | `LearnerConfig.segments`；`SegmentManagedExecutor` → `put_hot_segment` → commit |
| E1 历史编码 | 原完整 artifact/input pack → 原历史内容 | **只读**：segment v1/v2 reader；逐段上传/压缩/复制后准入已移除 | `managed_segment_store.read_segment`；旧 writer 拒绝 live 发布 |
| E2 推理 | 公平公开输入、完整 catalog、冻结 μ → scores 与51类分布；不判合法性 | **默认**：actor CPU μ、1线程、μ 内有界文字缓存 | `segments.central_scoring=false`；`LocalMuScorer`，actor loopback 统一服务 Agent 和 producer |
| E2 中央实现 | 同上 | **临时**：中央不可变 μ 集合、合批，CUDA 固定独立高优先 stream；退出条件：RL5 收卡时仍无两卡计划就退役。当前仅工程测试显式使用，单卡选本地，两卡计划获准后才考虑中央；没有自动 fallback | `segments.central_scoring=true` → `create_segment_inference_pool`；attachment `role=learner_central` 使 actor 选择 `RemoteMuScorer`；CPU 可作该实现的小测 |
| E3 段→ready | 准入热段、已验 aux、固定成员/随机顺序/4个切点 → 完整参数无关 PreparedBatch | **临时**：CPU spawn 进程、整批 advantage 和 targets、完整 forward/loss 元数据；退出条件：I1 合入 develop 时转默认，旧主线程准备移除 | `BatchPreparer.submit`；`preparation_workers=4`、`preparation_threads=1`；`prepare_ahead=true`；`prepared_bytes=1GiB` 约束当前+下一批 |
| E3 历史输入 | 原 artifact/compiled input pack → 不可变 wire | **只读**：有界 SegmentInputs LRU；同 key 只有一个冷填充 owner，读验/等待均在共享锁外 | `SegmentInputs`；新热段不依赖 pack/cache 恢复 |
| E4 更新 | `stage_batch` 一次后的完整批 → θ/Adam/EMA 更新及成功 uses | **默认**：整批 loss，4 minibatch × 1轮；按行 `segment_update` 仅拒绝旧调用 | `prepared_update`；每个物理 encoder 组用 non-reentrant activation checkpoint，整 minibatch 一次 loss/backward/Adam；KL 刹车未成功不记 uses，不补第二轮；benchmark 与 application 走同接口 |
| E4 加速 | 同上 | **临时**：BF16、loss compile；退出条件：G1 选定时，选中的转默认，其余退役；本轮 CPU 短测均关闭 | `performance.precision=bf16`、`performance.compile_loss=true`；默认 FP32 / compile关闭；不设数值等价门 |
| E5 发布/采用 | 更新后完整 θ 快照 → 完整 model+heads μ；段边界采用，旧尾值仍用旧 μ | **默认**：CPU 后台导出/哈希/Store 发布，跳过未发送中间版；默认 actor 按 grant 按需装载 | `_schedule_publication`；`SegmentGrant.mu`；当前/旧尾值显式 retain/release；临时中央实现在后台复制冻结模板和装载设备 |
| E6 标签 | Evidence 验证的真实终局或稳定 reward 的公开 HP → typed 标签及 exact RowRef | **默认**：Evidence-owned live verifier+绑定；全量二次 replay 仅抽样审计/评估 | `LiveGlobalReplay.verify(expected)` → 原 typed 入口；不是 observer 副本授权；审计失败追加 finding+Stop |
| E7 CP | 构造、θ/heads/text、完整 Adam/参数组次序、EMA、计数、Python/Torch/CUDA RNG → 自包含 CP | **默认**：`checkpoint_state.SCHEMA`；无恢复 parent，不读旧 queue/aux/输入日志；mid-epoch重放恢复已移除 | 配置 `resume_checkpoint`；新 `training_generation`；只恢复学习状态，旧热代追加丢弃处置 |
| E7 旧 CP | 真实 CP16 + 原构造包/参数次序 → 同学习状态转换 CP；转换零 SGD/World | **只读**：旧 schema `stpd/rl-segment-checkpoint-1`；显式 `convert_cp16`，没有隐式 fallback | application `--convert-cp16-package`；转换序号16、正常下一次保存17、首次新 μ58；转换和恢复 generation 各自具名 |
| E8 保留/归档 | 开 World 前冻结 seed/selection/manifest hash → 普通自然 win/loss 抽约5%原件，其余留账；技术、unknown、异常、censored、评估全留 | **默认**：`audit_every=20`，原有 full_peer 保存/后台审计按规则调用；所有训练段全归档已移除 | `managed_retention.training_audit_selected`；原 native+behavior 原件全部规定读回后才释放本轮新副本，旧资产不扫描清理 |

历史 `segments.inference_stream` 字段只读；当前实现选择由 `central_scoring` 决定，
中央 CUDA 固定独立 stream。已移除共用训练锁/默认 stream 的中央评分，以及 forced null 分布再由 learner 补算的分支。

## 内存与物理批形状

`microbatch_size` 只限制物理 encoder 组；scene/text attention area 仍可能把实际组缩到1行。
例如512节点的 padded scene，默认 `scene_attention_area=262144` 只能容纳1行；
4行需至少 `1048576`。这不改1024 PPO行、128 terminal+128 HP、四份minibatch的成员、
整批归一化、各头分母和一次Adam决定。各组 encoder 在 backward 重算，
只保留全批小输出，不把全部 encoder 激活叠到一次 backward；代价是额外 forward。
逐组梯度累积也必须沿用整 minibatch 分母，全部组结束后才裁剪、判断KL和step，当前不另开一条实现。

学习状态在 `_learning_state` 已冻结为owned CPU bytes；`encode_state` 只展开dataclass元数据，
不再对共享slab上的每个Tensor做deepcopy。原CP schema/codec与读回校验不变；
模型、Adam、EMA、RNG和恢复计数都保留。量内存时分进程记录RSS/PSS、CP前后与forward/backward峰值，
不要把整批行数误当microbatch，或把Slurm所有进程的高水位相加当物理常驻量。

## 冻结身份和 μ

μ 仍是完整 model 包及独立 `stpd/rl-behavior-heads-1` checkpoint；snapshot hash 绑定
weights、heads、projection、adapter 和数值配置。`export.adapter_identity()` 的代码闭包包含
`local_mu.py`。文字可训练；缓存 namespace 随实际 μ、文字权重和输入身份变化，不把文字冻结作加速。
clock offset 沿 Agent 实测的 `{lower, upper}` bounds 或 null 传递，不把区间伪装成单个 offset。

HotSegment 没有 Artifact schema。commit 的 `payload` 是 `HotPayloadRef(slot,sha256,size)`；
`segment_artifact_id="hot:"+slot` 仅是消费者位置，禁止交给 Store 或 CP parents。
Delayed outcome/HP 都使用 `source={run_id,segment_id,training_generation,row,key}`。
`HpBattle.post_hp` 是原公开绝对 HP，行 `hp=post_hp/100`；reward 变体带实际
boundary acquisition、step、input hash 和战斗范围，native terminal 变体由 typed 终局绑定。
不能用 max_hp、下一场战斗状态或未确认的非战斗输入代替标签。

## 容量、失败和引用

ready、在途 lease 共用 `capacity_rows=4608`；每批至少1024行，保留整段溢出行，
单 episode 每数据集最多128行。PPO、延迟标签、aux、prepare、reader 各自持有 hot owner；
commit/episode_end ACK 前取得下游所需引用，用尽或淘汰释放，最后引用才删除本轮临时热 bytes。
每个数据集抽样的 aux 终局≤128行、最多2次且跨2个数据集；HP≤128行、1次/1个数据集。
常驻池是另一口径：terminal最多256局、每局均匀抽最多64行；HP最多128场战斗，
每场行数随原件变化，128行/批不是整个HP池的行数上限。

输入 ACK 是原输入及完整 response 的不可变事实。ready、used、discarded_on_restore 都是后续处置，
不改旧 accepted/unknown。断线先查原 receipt；原结果未知时不盲重投。
明确新代且原请求不存在，旧 commit/episode_end 只追加丢弃；同一 World 通过新 begin 领新代 lease，
不重新 offer seed。尾段写入撞上已撤销的旧热代也追加丢弃，不改成 unknown 或 accepted。

## 启动、CPU 短测和只替换 learner

正常应用：`python -m spireagent.workbench.rl_learner --config learner.json --work <fresh-work>`。
Friday 入口仍是 `tools/friday/rl.py learn`，只在 Slurm 分配和原 resource-slot 内运行；
`device=cpu` 可做已授权短测，`device=cuda` 沿原 GPU 分配。actor 数与 inference_batch 解耦，1–200路；
CPU 单路短测同时设小 dataset/episode_rows，不能用默认1024行的最少8路条件冒称单路通过。
使用 `rl.py prepare/submit/run/save` 原 actor 入口、完整原件保存和收据；unknown 不重试。

配置固定 `segments.rendezvous_path` 为原启动的共享 inference.json。launcher 把此路径交 actor，
learner 把完整新 attachment 原子写回同一路径；环境 capability 保持在原环境中，不写文件或日志。
恢复前停止并确认旧 learner/prepare/SGD 已结束，保留 actor/World 和原件；新 work 目录、同一 run、
新 generation、同一 peer/rendezvous/capability，从指定自包含 CP 启动。
恢复撤销该 CP 所属旧热代，追加 discarded_on_restore；旧 ACK 仍可查。不要同时启动两个 learner。

旧 CP16 转换先准备一个正常 learner JSON：同原 run、原 CP16 ID、目标新 generation，
明确本轮 peer/资源和学习参数，再运行：

```sh
python -m spireagent.workbench.rl_learner --config learner.json --work conversion \
  --convert-cp16-package /ABS/original-construction-package \
  --source-generation g-legacy --conversion-generation g-converted
# 零 SGD/World；输出 conversion-receipt.json 和 converted-learner.json。
python -m spireagent.workbench.rl_learner --config conversion/converted-learner.json --work fresh-learner
```

原构造包只在显式转换时需要；自包含 CP 正常恢复不需要它。转换保留原非空 Adam、EMA、RNG、
362 updates / 48951 first-use / 42787 multi / μ57；新参数差异写在转换记录中。
转换成功不自动证明真实打局恢复；须新段→成功更新→新 μ采用→新 CP→只换 learner→继续更新。

## 验证和停止点

语义反例使用 CPU 测：TD/λ/终局/HP、非终局 bootstrap、同 μ forced/尾值、完整 catalog、
代际隔离、CP-only 与 unknown 不重投。重要跨线候选作非作者复核。
加速可改变批次/精度/实现，不设逐位或容限等价门；G1看整循环速度、KL/熵/clip/各头 loss 和常数基线，
GPU throughput、真实 World、健康小时及实力结论各用其原实测。CPU fixture 不替这些结论。
