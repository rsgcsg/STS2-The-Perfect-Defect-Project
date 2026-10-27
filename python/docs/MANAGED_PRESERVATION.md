# Managed 分档保存、流水线与公开轨迹

本页拥有运行节点→存储节点→训练读取的保存契约与操作；实现为
`spireagent.workbench.managed_pipeline`、`managed_retention`、`managed_trajectory_store`，
研究轨迹格式归 `stpd.p1.trajectory`。不改变 Host 打局、P1、Connector、模型或输入。
入口仍是 [同一评估账本](LOCAL_MODEL_TASK_FLOW.md)，没有第二套游戏调度器。

## 开始前声明用途

在 `managed-evaluation-execution-v1` 配置内声明以下字段；省略时沿用正式档。
创建账本会校验并把解析结果写入不可变 plan 和 slot 身份，开始后不能换档。

```json
{
  "retention": {"tier": "trajectory", "audit_every": 100},
  "pipeline": {"save_workers": 2, "pending_limit": 8},
  "store": "/ABS/node-managed-store",
  "read_concurrency": 8
}
```

| 档位 | 保存与失败含义 | 成绩 |
|---|---|---|
| `formal` 正式 | 原完整 Runtime/模型父与原件，复制到冻结 peer，每次完整 typed 读回 | 只有自然终局、关闭与读回全部确认后进分数板 |
| `diagnostic` 诊断 | 正常胜局留本机完整 Store 并 typed 读回；自然负、censored、unknown、异常升完整 R2 | 永不进分数板；升档只改变保存量 |
| `trajectory` 轨迹 | 正常局只把公开轨迹分片送 R2；约 1% 完整审计；unknown/异常完整 R2，typed 可读的 unknown 同时留轨迹 | 永不进分数板 |

诊断档拒绝 `store=s3`，避免把“本机保存”实际写成远端。轨迹分片父引用已有模型，
不复制一份新模型。完整审计按 `sha256(seed, selection, manifest_sha256) % audit_every == 0`
选择，规则和分母在开局前冻结，不按输赢挑局。默认 100 为约 1%，小批不保证恰有一局。
原完整证据不减字段，不放宽 producer、整包、父对象或 payload 哈希检查。

诊断与运行节点暂存沿用 [原七天保留及容量规则](DATA_LIFECYCLE.md#workstation-capacity-before-long-tasks)。
满七天并不自动删除唯一数据；本实现没有清理服务，也不动已有 Store、录制或 Q 数据。
轨迹局的完整 Runtime 原件仍在运行节点保留到允许清理时，远端仅保声明的数据。

## 执行、关闭、保存与恢复

同一账本在 offer 前持久化 reservation。原执行器完成 Runtime 封存及 outcome，再由原
Host owner 关闭。扩展只在原 `_preserve` 接缝写不可变 save-job；关闭确认后释放执行工人，
由同一账本交给有限保存池：

`reserved → executing → sealed → close confirmed → save_pending → readback → finished/saved/unknown`

`pending_limit` 是全部未完成保存原件（包括正在执行、等待/正在保存、等待封片）的总上限，
默认 8；`save_workers` 默认 2。执行工人上限仍由原 `--workers` 决定。
空间为每个未完原件保守计原 2 GiB 峰值，并留原容量余量/本轮停止线；达到队列或空间上限
就停新准入、排空已开局。关闭未确认停新准入；保存失败记录 `save_failed` 并排空已开局。
整批墙钟包含冷准备、所有失败/unknown 和最后封片/读回尾巴。

在同一冻结源码、配置、Runtime 与模型环境下，再运行原 `run --host managed --ledger /ABS/ledger`
会先读取原尝试目录，只恢复 `save_pending/save_failed/trajectory_pending` 的保存。
只要有待恢复保存，本次调用不 offer 新局；保存确认后另一次调用才继续原 not_started 槽。
恢复验证 plan、save-job、关闭收据、原件封印和包身份；运行中的 report 是 owner 的可变
checkpoint，最终 report 才固定哈希。保存途中终端退出或重启不把槽位退回 not_started；
unknown 不重投。若原 Host 关闭不明，必须由原 owner 核清，不能拿进程名消失推断关闭。

Runtime 在封存前异常时，全部已有 Runtime 字节按既有 archive/transfer/hash owner 保全到 R2，
标为 `managed-failed-original-1`、`typed_recording_qualified=false`。没有 Runtime 原件时保存
准确失败事实；不补造运行记录，不把这类诊断称为 typed 完整成功或训练资格。
保存失败只恢复同一任务；原执行结果/unknown 与保存状态分开保留。

`phase_seconds` 保留执行/关闭/保存等互斥阶段，并增加 save_wait、save_verify、trajectory_extract、
shard_wait 与 shard_preserve_shared；末项为该分片共同耗时，不能跨局相加当 CPU。
`detail_seconds` 为嵌套观察，不与 phase 相加。每局 elapsed 包括自身排队/封片等待；批时间
取命令外墙钟，含 preparation，不用单局中位数乘局数冒充吞吐。

## 轨迹格式 v1

`stpd/p1-trajectory-1` 每局一个 JSON 对象；分片为 `p1-trajectory-shard-1` 的 gzip NDJSON，
每片最多 8 局、解压后 256 MiB（单局最多 256 MiB），最后不足 8 局照样封片。
采用 gzip level 1、固定 mtime=0，复用 Store 分块、manifest-last 发布及复制，不另建传输。
先用八局上限平衡封片尾巴、内存和失败恢复；暂不为填满分片等待下一批。

| 字段 | 含义 |
|---|---|
| `identity` | 实际 publisher producer、完整 Host/Runtime pin、model artifact/weights SHA、实际 InputSpec、seed、角色、难度、开局 progress SHA、冻结档位和本局 behavior_policy（T/ε 固定） |
| `original_content_id` | 原 typed Runtime 原件的物理内容身份；不是另一次打局 |
| `observations[]` | 按原事件顺序的公开 P1 observation，完整 catalog 内嵌；原 acquisition、source ordinal、occurrence、consumption unit、InputSpec 一并保留 |
| `steps[]` | 原真正 offer 的 action id/index、公开观察 index、global_result 交付结果、next_step/next_observation index |
| `behavior` | 真实行为分布、所选动作概率、温度、uniform_mix、模型原概率与来源；记录的 logits 与动作目录同序 |
| `state_value/action_values` | 价值头输出；A′ 存完整动作目录同序向量，没该头则 null 并写 value_status，绝不把 logit 当价值 |
| `combat_spans[]` | 公开观察中 combat 从 available 到离开的索引；不见结束则 end 为 null，不补推隐藏战斗结局 |
| `outcome` | 原终局结果、最高公开层数、原 finished/censored/unknown、unknown 原因 |
| `rl_ready` | 当前 argmax/无价值头为 false；不因保存成功授训练资格 |

实际采样模式的格式要求：若原模型概率为 p，温度 T、uniform_mix=ε，则
`q_i=(1-ε) p_i^(1/T)/sum_j p_j^(1/T)+ε/N`，selected_probability 必须等于 q 的所选项。
记录完整 p 与 q、T（每局固定于身份/轨迹生产者，计划范围 1.0–1.3）、ε（计划约 0.03）和价值。
本卡只实现记录校验，不实现抽样。后续生产者必须记录抽样时的真实值，不能事后猜测。

当前 438 仍按原 argmax：行为分布为所选动作 1、其余 0；temperature=null、uniform_mix=0。
模型只发 logits，因此 model_probabilities 是公开声明的 `softmax_of_recorded_logits`，
不是声称模型发过概率。当前无价值头，明确 null、`rl_ready=false`。
未封存失败前缀可由完整失败 artifact 找回；没有可靠 typed 输入时不伪造轨迹步。
本格式不定义 unknown 的 RL 丢弃/奖励规则，不授 RL 开训权限。

保留所有顺序公开观察和战斗边界供以后派生辅助标签；现在不算标签、不重打局。
同一步的 next_observation 是下一次真实 Agent 公开输入，不承诺包括所有原生中间帧，
不把下一输入误称为有因果保证的单帧 successor。

## 训练节点最小读取样例

沿原私有 R2 配置初始化环境（不把凭据写进脚本/输出）；输入精确 shard artifact ID 与
冻结 peer binding，用已有 Store reader 完整物理读回、校验模型父 weights SHA、逐局/逐步读取：

```python
from pathlib import Path
from spireagent.storage.config import open_peer
from spireagent.workbench.managed_trajectory_store import read_shard

# peer 是原已冻结的非秘密 bucket/endpoint/prefix binding；凭据只在环境中。
store = open_peer(peer)
for episode in read_shard(store, shard_artifact_id, scratch=Path('/ABS/private-scratch')):
    for step in episode['steps']:
        before = episode['observations'][step['observation_index']]['observation']
        after_index = step['next_observation_index']
        after = None if after_index is None else episode['observations'][after_index]['observation']
        # before['catalog'] 是完整目录；未知和 rl_ready 必须交给已批准的训练入口处理。
        consume(episode['identity'], before, step, after, episode['outcome'])
```

读完整个 iterator 才确认完整分片成功；校验失败必须失败，不换缓存冒充本次 R2 读取。
恢复模型仍走原模型 reader 的完整验证，轨迹 reader 不替它授资格。
