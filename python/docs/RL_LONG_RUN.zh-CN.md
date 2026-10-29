# RL 长跑运行图（按节点）

一页说清 RL 正式长跑在哪个节点跑什么、怎么起、怎么停、怎么续、出错看哪里。细节不在这里重写，只链接原手册：
- 接口和续训规则：[RL_ACTOR_LEARNER](RL_ACTOR_LEARNER.zh-CN.md)
- Friday 部署、打局、评估、停止：[FRIDAY_ACTOR](FRIDAY_ACTOR.md)
- 学校 GPU 登录和 SSH：[FULLRUN_TRAINING](FULLRUN_TRAINING.md)

写于 2026-10-29（G2 收口）。开长跑要用户明确说"开"；本页不是启动许可。

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
2. 申请一张 L40S，记下分配号、主机名和最长时长（约 5 小时）。
3. 在门户的 Jupyter 终端里跑 `nvidia-smi`，确认是 L40S、驱动正常。
4. 用户把分配号和主机名告诉执行的 Agent。之后 Agent 按 FULLRUN_TRAINING 的严格 SSH 入口（本机 → moss 跳板机 → 分配主机）核主机公钥，不关闭校验。

**起学习者**（Agent 做）：
1. 在学校主机上检出干净的 develop，和 Friday 冻结部署用同一个提交（10-29 是 `445b9f98`）。
   RL1 留的材料写的是 `e187b6b7`，比 F2 合并早；长跑卡开工时先核学习者和打局者的源码是否必须一致，不一致就统一到 Friday 那个提交。
2. 学习者配置用 RL1 留的 `learner-school.json`（run=`rl1-real-1`、peer=R2、device=cuda、`wall_seconds`=15600，比 5 小时少约 40 分钟，留出准备和收尾的时间）。
3. 启动脚本按 RL1 的 `school-learner.sh`：检查源码干净 → 从用户放好的私有文件加载 R2 环境变量（不打印、不复制）→ `python -m spireagent.workbench.rl_learner --config … --work …`。脚本里的 R2 文件路径要先核实。
4. 学习者先发布版本 0，打局者才有模型可取。

**续训**（每次分配用完都要做）：
- 学习者到 `wall_seconds` 会自己发最后一个版本和检查点，然后正常退出。
- 下一次分配：从 `exchange/rl/<run>/checkpoints/` 读最新记录的 artifact id，**显式**写进学习者配置的 `resume_checkpoint`，再启动。不写会被拒绝。
- 打局者不用停，它们一直取最大版本号。
- 学校空闲断开的规则还没核实，并且不许用保活脚本；断了就从最近的检查点续。

## Friday（打局和评估节点）

- 部署已经冻结在 `/data/<用户>/friday-actor/releases/445b9f98…`，导航文件是 `current-deployment.json`。8 路 RL 配置在 `config/rl1-prepared/plan.json`，**没有提交**。
- 起打局者：学习者发布版本 0 以后，按 FRIDAY_ACTOR 的 RL 一节提交。每路一个核、一个唯一 actor 名，用 fc430。
- 调核数：跟着学习者的速度走。按 10-29 实测（学习者 1.40 秒/步，fc430 每核每小时 22.4 局），大约 11–15 个 fc430 核就够学习者吃；先用 8 路，看学习者是否在等数据再加。
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

## 出错看哪里

| 现象 | 先看 | 处理 |
|---|---|---|
| 打局者 unknown | 打局者的账本报告和 `result.json` | 不重试，单独计数；连续 3 个，或至少 8 局后超过 25%，打局者自停。总体超过 5% 就开 Managed 修卡 |
| 学习者没有新版本 | 学校上学习者的工作目录事件、R2 的 `models/` | 看是没有新分片（打局者的问题）还是在训练（学习者的问题） |
| 学习者被断开 | 学校分配状态 | 新分配 + `resume_checkpoint` 续训 |
| 分片上传了，但学习者没用 | 学校学习者的收据 | 上传成功不等于已经训练 |
| Friday 作业丢了 | `sacct`、作业的 Slurm 日志、R2 回执 | 不能因为没有记录就当作没开局；按原账本入口恢复 |

## 还没在真实环境证明的（长跑第一小时就是冒烟）

1. 打局者真的会换到学习者发布的新版本。
2. 跨节点只走 R2：Friday 写、学校读。
3. 学校 L40S 上从检查点续训。
