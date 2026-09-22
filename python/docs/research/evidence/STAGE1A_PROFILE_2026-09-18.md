# 1a 初始计算图检查 — 2026-09-18

源码 `60b04ffff3c87a2e283b251d1ae6ed4f429622f6`；锁
`1dd5f0dc645c7330b4a6e7712da5edad7f04caa6640b69c0cf1d0dfac42f84f7`。
私有任务 `com.spireagent.stage1a.profile.20260918-170715`；结果 schema
`stpd/stage1a-graph-profile-v1`，状态 completed，总耗时 180.869 秒。

本机 MPS/FP32，Torch 2.13.0、Transformers 5.15.1，Qwen3-0.6B-Base
revision `da87bfb608c14b7cf20ba1ce41287e8de496c0cd`，权重 digest
`c0d42533fa221952ba6502ae7425a424e7784a2190122bb10250807b896b85ec`。
四图各检查 128/512/1024 个随机合法词表 ID、两个 24/25-token 动作，seed1701。
使用合成选择索引检查 backward，optimizer 更新次数为零；没有人类数据或游戏执行。

| 配置 | 1024-token forward 秒 | backward 秒 | 反向后 MPS driver 字节 |
|---|---:|---:|---:|
| B-S | 0.181 | 0.114 | 1152401408 |
| D-Simple-S | 0.055 | 0.057 | 1128218624 |
| B-PF | 62.118 | 95.966 | 16582017024 |
| D-Simple-PF | 0.770 | 0.001 | 2833039360 |

全部 12 项分数和训练参数梯度有限；冻结骨干未获得参数梯度。
没有统一 warm-up 或重复采样，执行顺序和内存压力影响时间；这些不是正式延迟分布。
driver 字节是采样值，不是独占物理内存或精确峰值，不能与 CPU RSS 简单相加。

结论是计算图可运行，同时 B-PF 的整段 autograd 路径在较长输入时存在严重成本问题。
后续改用固定因果前缀 KV、末尾 query 保梯度的执行方式，必须分别验证分数／梯度等价
及实际性能，不能把本报告的结果改写为优化版已通过。真实 lite 数据曾达到约六千个
Qwen tokens，不能用本次 1024-token 两候选检查推断正式训练预算。

首次进程已完成并保留结果；旧 `launchctl submit` 启动方式随后重复唤起，均被
“输出已存在”拒绝，没有覆盖结果或重新训练。任务已移除。后续使用显式
`RunAtLoad=true, KeepAlive=false` 的一次性 job，并分别核对进程退出与报告终态。

## 优化后复验与真实输入准备

源码 `d88c39307bc7aad23e43a75c05d3569aadc20d0f`，同一锁、Qwen pin 与 MPS/FP32。
任务 `com.spireagent.stage1a.inputs.20260918-172254` 成功退出，总计 38.680 秒；
输入准备 22.554 秒，四图检查 14.948 秒。没有 optimizer 更新或 Modal 计算。

复用 S01 的固定 lite ModelView
`90f252d9cd0f2eb78b4e0f04df2c9a7ba671b995c05a617793e5963db0e9ae3a`，
50 train、16 dev，各侧一个 run 的限制保留。生成后重新加载、重投影，完整候选未删减。

| token 输入 | artifact ID | 实际词表 | 联合长度 p50/p95/max |
|---|---|---:|---|
| S | `87bec5319ecd976649ec8216d20fd4388efa9a891cba07d26a2670f61bf6421a` | 2848 | 2890 / 5120 / 5466 |
| PF | `7dc23e8ce2d4a73722f2c7c2b6c91c9f686aec211d1e91b77475e6c097fecf7a` | 151665 | 3215 / 5451 / 5989 |

S 分词器仅由 train 拟合，digest
`bbab9dcbdbcf15ddd9c6eec05b9056603b5043e671e84dd5cd0b30e0fd5b8184`；
PF tokenizer JSON digest
`c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539`。
PF 全部输入的 IDs 与固定 Hugging Face fast tokenizer 一致。

固定权重的 128-token 全分支与因果前缀分解对照：隐藏表示最大绝对差
0.0001449585，query 梯度最大绝对差 0.0005626678；通过预设的
hidden atol1e-4、gradient atol1e-3、共同 rtol1e-4 联合容差。不是逐字节相等。

1024-token、两个候选的 B-PF forward/backward 分别 1.945 / 1.629 秒，
反向后 MPS driver 采样 5014142976 字节；D-PF 为 0.746 / 0.001 秒。
全部 12 个长度／图检查通过，骨干保持冻结。仍是合成长度、单次、无统一预热的工程观察，
不是实际 5989-token 多候选训练/推理性能。原慢结果和新结果分别保留，不改写旧 receipt。
