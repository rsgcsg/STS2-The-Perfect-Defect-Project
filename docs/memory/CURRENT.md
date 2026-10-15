# 当前状态（2026-10-15）

模型方案冻结：开（对象：首轮训练配置和任何新模型方案；工程实现与路径冒烟不受限）

实时事实（分支、PR、CI、安装、运行）以 GitHub 和原始记录为准，本文件可能过时。

## 当前选择

- 一套环境协议 P1，目标修订 r1（[规格草案](../protocols/P1.zh-CN.md)）。Managed Exact 做实验和批量评测，Reference 真实游戏做验证。
- 当前组合、首个瓶颈和交付卡都在[活动计划](../plans/P1_ROUND1_PLAN_2026-10-14.zh-CN.md)。旧 BASELINE_V1_SPEC 和 G2/V1 执行矩阵不再派发任务。
- 第一轮训练配置草稿已撤回，等 P1 r1、数据准入报告和批量/GPU 路径后重写。

## 现在的位置

- 第一个数字（Managed、随机合法、smoke-20）：20 局里 8 局自然终局（全败），12 局技术删失（10 药水、2 通用选牌），0 unknown。L0 要求 ≥ 95% 自然终局。
- 真实游戏：d6 M0 单种子自然打到第 11 层（2026-10-11），但还没有任何套件数字。
- 首个瓶颈分两半：Managed 动作完整性和抽牌公平投影（MX）；真实游戏缺套件数字（R0）。工程阻塞：Windows 托管 CI 慢且不稳定（上限、构建超时、时序 flake）。

## 正在做

1. MX：Managed 药水/通用选牌/抽牌公平投影修复，然后同 manifest 重跑 smoke-20。
2. R0：真实游戏 ref-20（随机合法 + d6 M0）怎么跑，出写代码卡 R0-impl。
3. CI：测试桥构建超时（BT）与时序 flake（FL），再拆组件阶段、成本均衡与文档路由（CS/DR）。
4. J-min：raw score 消费者清单；之后的 v2 资格和远端闭环等有确认的训练配置再做。
5. PR178（含交接后的改动）复审；PR180 等用户批准合并。

## 预算与边界

- GPU：J-min 累计硬上限 USD 1（含失败尝试），已花约 USD 0.03。USD 20 的本轮总额只是待确认提案，不是授权。
- 不做：生产部署、main/release、删除原始数据或历史资产、未批准的花费、未确认的训练配置、自行合并。

## 去哪里找

- 规则与角色：[ENGINEERING_RULES](../ENGINEERING_RULES.zh-CN.md)；决策：[DECISIONS](../DECISIONS.md)；分层：[SYSTEM_MAP](../SYSTEM_MAP.zh-CN.md)。
- 已实现与证据：[STATUS](../STATUS.md)；旧计划与历史证据：[执行包](../plans/AGENT_RUN_COMPLETION_2026-10-11.md)。
