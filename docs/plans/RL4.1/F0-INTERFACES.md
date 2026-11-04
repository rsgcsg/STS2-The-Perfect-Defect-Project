# RL4.1 F0：三线文件边界

基线 `origin/develop=e7770d3c113debf37ce1a899dccc300802733e17`；F0 只冻结内部接口和 stub。
工作区根：`/Users/fire/Documents/Codex/2026-11-04/rl41/`；`freeze` 是共享对象的独立 clone，三条 worktree 归它管理，主 clone 保持原状。
链路位置：既有 Actor → Exchange → 准备 → learner → μ/CP；变更类为表示/传输。
卡 v5 §12 优先：文字继续训练；actor 按 text digest 缓存；不设逐位、容限等价或同成员梯度门。

| 线 / 工作区 / 分支 | 唯一可写文件（仓库相对路径） | 不许碰 |
|---|---|---|
| W1 / `../w1` / `codex/rl41-w1-learner-20261104` | `python/stpd/rl/{segment_learner,segment_targets,loss,segments,segment_inputs,performance,learner,prepared_batch,checkpoint_state}.py`；`python/stpd/p1/{model,text_cache}.py`；新增 `python/stpd/rl/batch_preparation.py`；测试 `python/tests/test_rl41_w1*.py`、既有 `test_rl_{compiled_segments,segment_learner,segment_loss,segments,segment_input_cache,precompile_inputs,process_reader,performance,joint_nodes,text_reuse,input_recovery}.py` | W2/W3 文件；不得直接改控制/本地 scorer、Exchange、producer。`segments.py` 的数量上限归 W1，W2 只提需求。 |
| W2 / `../w2` / `codex/rl41-w2-actor-20261104` | `python/stpd/p1/{local_inference,agent,agent_driver}.py`；新增 `python/stpd/p1/local_mu.py`；`python/spireagent/workbench/rl_actor.py`；`tools/friday/rl.py`；测试 `python/tests/test_rl41_w2*.py`、既有 `test_{p1_local_inference,rl_inference,rl_compiled_inference,friday_rl,friday_actor}.py` | W1/W3 文件；不改 `segments.py`、model/text_cache、producer。5% 的 launcher/actor 参数接线归 W2，抽样算法归 W3。 |
| W3 / `../w3` / `codex/rl41-w3-data-20261104` | `python/spireagent/workbench/{rl_segment_producer,managed_segment_store,managed_retention,managed_pipeline,managed_evaluation,evaluation_ledger}.py`；`python/spireagent/rl_exchange.py`；`python/spireagent/storage/async_archive.py`；必要的 `components/policy-runtime/src/{global-live-replay,global-replay}.ts` 和 `components/evidence/sts2_platform_evidence/global_agent_session_run_evidence.py` 及其直接测试；测试 `python/tests/test_rl41_w3*.py`、既有 `test_{rl_segment_store,rl_shared_archive,managed_retention,managed_pipeline,managed_evaluation_dispatch}.py` | W1/W2 文件；不写 learner 输入缓存或 local scorer。沿原 Evidence 产生 typed 标签；不把 observer 报告当验证结果。 |

表外文件和共享测试由 I1 单 writer 负责；确需新增文件先由 Claude 分配唯一 owner；表中工作区的 `..` 相对 `freeze`。
三线均不改 `python/spireagent/rl_contracts.py`、`python/tests/test_rl41_contracts.py` 和本表；字段变更交 I1 统一修改后重绑。
版本登记、BOM、依赖锁、共享 SOP/决定/规则文件由 I1 归并；F0 不分配或发布新的公共 schema 号，不改旧 reader。
不改主 clone/他人工作区，不 push，不开 PR，不跑 GPU/训练，不删已有数据；原 unknown 与原账不改。

| 接口位置 | 归属和连接 |
|---|---|
| `python/spireagent/rl_contracts.py` | W3 生产 HotSegment/Commit/EpisodeLabels，W1 消费；W2 通过 LocalMuScorer 提供同 μ 的每行 51 类与非终局 bootstrap。共用 `control_request_key`；ACK 保留完整原 response/lease/μ，ready/used/restore 丢弃是后续追加处置。 |
| 同上：MuIdentity/MuPublication、SegmentGrant、TextCacheNamespace、LearnerRendezvous | W1 后台发布完成的全量 μ；W2 在新段 begin 采用，旧 μ retain 到 forced/尾值完成；W3 负责 Exchange 传输。text digest 包含文字权重，namespace 同时绑定 tokenizer/词表/语义/数值模式。重连先查原 receipt，绝不重投 unknown 动作。 |
| `python/stpd/rl/prepared_batch.py` | W1 的 PrepareRequest 固定整批成员、aux 与 4 个 minibatch 切点；CPU 准备进程返回 PreparedBatch：全部参数无关张量、整批多选归一 advantage、targets、mask、joint metadata、aux 预留；成功 SGD 才计 uses。默认 4×1，aux≤128+128、复用≤2/1。 |
| `python/stpd/rl/checkpoint_state.py` | W1 的 SelfContainedCheckpoint 嵌入构造、θ/heads/text、完整 Adam/参数组次序、权重 EMA、计数及 Python/Torch/CUDA RNG；身份 `(run,generation,sequence)`。`convert_cp16(CP16ConversionInput)` 为显式待实现入口；保原学习状态，清未消费热数据，续 CP17/μ58。 |

CPU 环境：各工作区 `python/.venv` 指向已有 rl-critic 环境，不重复安装。
运行：`cd python && OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python -m pytest -q tests/test_rl41_contracts.py`。
F0 测试只证实 import、字段与现有 codec 可传递；实际目标/恢复/吞吐/学习质量留给 W1–W3/I1/G1/H1。

CORRECTION: T0 §3.8D 把 `self.ema` 称为尺度状态有误；当前 `segment_learner.py:952–958` 对整份 model state 更新权重 EMA，本接口保留该权重 EMA。
