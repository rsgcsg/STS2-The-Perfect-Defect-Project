# P1-TU — 终局比较与升级选牌的有限推进

用户明确授权本轮P1实施并检查新规范落实；这替代MGMT-1完成后的无派工状态。

- 目的/产物：终局对照不再把不同胜负或无终局证据称相同；Reference原90步升级选牌首卡点得到真实推进，若出现下一卡点诚实停止。不声称全P1冻结、等价套件完成或策略质量。
- 基线/负责人：PR189 `29c81ab2426c089c3e570c6b929d407eeeb51de6`，含develop管理规范；topic `codex/p1-terminal-upgrade`。Root唯一tracked作者/集成负责人，复用本工作树；通过正常merge复用T7的dcd571e5/1ad27100来源。Parent PR189尚未接受，历史CI和本轮证据分开。发布前已核并正常合入其11d7e039类型修正，Connector/Runtime子树完全未变。
- 任务分配：一个只读子任务核终局比较最小范围，Root处理升级映射与实现；稳定候选一次定点非作者复核。禁止多位作者改同分支，无实施worker链。
- 验收：先原记录/小反例复现，再改原owner；终局匹配/不匹配/缺证据正反例及桥接真实消费者检查；升级旧捕获回归与同seed最多一次新Reference回归（必要纠错后最多再一次，全部保留）。同一候选按实际影响跑相关组件/所选门；不靠改声明或删有效断言过关。
- 成本/停止：先20分钟核事实与拆分，再约60–90分钟完成实现/短验证检查点；原生单槽，不默认40/20局套件、不训练/付费推理。两个缺口交付并完成有界验证即收口；下一缺口只记录。CI检查选择明显失配要判断，确有关联的长检查保留；不无解释重跑。

非目标：T1/T2/T6、Source4 Human录制、协议重设计、模型/训练配置、全量能力资格。必要构建/既有隔离Reference路径短验证在当前工程目标内，新的生产安装/隐藏规则/重大语义取舍须另明确；不修改Claude分支、不合并/关闭其他PR。

规范落实的实际核对随本任务记录：当前任务可定位；权限/写者唯一；首先真实失败；相关而非仪式测试；分母/身份准确；完成即停。仅更新本任务/CURRENT和受影响合同，不再开管理体系审计。


## 本轮结果与停止点

- 终局比较：新真实Reference轨迹89点（88动作+loss终局）原样匹配；仅将终局变成win，88动作前缀仍匹配，但终局明确拒绝为terminal_outcome_mismatch。真实桥接子进程回归也通过；不声称跨Host整局等价。
- 升级映射：旧捕获从required:action_mapping:deck_upgrade_selection.activate变成complete。补充真实回归在第91次提交执行原native activate，receipt delivered/result completed；原捕获114/115显示showing_upgrade_previews false→true，13个合法选牌动作仍保留。
- Native尝试共2次，均用现有安装且无部署。第一次随机Agent88决策、loss终局、0unknown、replay verified，但没有升级页；第二次公开意图引导90步前缀后执行toggle，91提交、0unknown、replay verified。后者诊断Agent错误过滤掉显示为升级预览的卡，随后abstain/child_closed，Runtime fail-closed并释放控制；不冒称完整选牌→确认流程通过。停止，不追加第三次。
- SDK完整check442通过。Runtime首轮589通过/1环境失败/3跳过：系统Python3.9不能导入Evidence类型；用既有解释器选项改为3.11，只补该原失败通过，再完成build/package。源未因此变化，旧失败保留；不把首轮命令改写成全绿。
- 定点非作者审查接受本轮代码；仓库/identity/BOM检查通过。完整CI按当前跨组件源码范围一次正式派发，实际状态以本topic PR为准，不预填通过。未获得本PR或PR189的merge授权，不自行合并。

规范落实：当前任务替代旧管理任务，唯一作者和两个有界只读角色；修正继承的CURRENT统计混用与Reference绝对真相措辞；复用T7来源、不重做已完成工作；先原反例，按错误环境只补失败；两次Native分母分开；到停止条件就停。没有实施T1/T2/T6、训练、协议重构或新的管理工程。

## 后续有限验证与 CI 修复（2026-10-17）

Reference 后续验证绑定旧候选 `333094d4d5bd4dd78e1ca56ec41d045caab5c709`：
历史两次尝试不变；另授权最多两次，实际只执行一次，54秒/93次提交。
原始 source114–117 与 Receipt 证明 preview false→true、13个合法选择保留、
选择眼部攻击、原生要求确认后执行 confirm、进入 event_option；独立 run_deck
Read 证明同一卡从未升级变为眼部攻击+。0unknown、0fail-closed、replay verified。
显式 Runtime Stop200/controller released/pending_request=null；取消待返回
Agent Next 后 opaque agent_state=uncertain 原样保留。Host close200/exit0，
无强制退出或相关进程残留。达标后未执行第二次。本轮 CI 修复不追加游戏。

用户随后指派同一 Root 写入负责人修复 PR191 两类 CI 失败：保留来源正常合入
父提交 c0014dbe 的 Managed startup adapter 修正，并用现有 npm lock 入口把
Host Runtime 锁内 Connector SDK rc.9 更新到当前 rc.10，不放宽包断言。
起始 head333094d4、工作树干净；旧候选本地忠实复现同九个 test_local_models
失败（另两个 unavailable 用例通过），合入后这11例通过。父修复审查还发现
startup.adapter 是真实身份对象，须读取其 id 而非与 adapter 名字符串比较；
补一行同路径修正与一个实际 adapter/startup producer 形状的命令路由回归。
该回归在原父修复上失败，修正后与相关组件检查一起验证。HTTP控制 seam 为
既有测试 fixture，不冒称真实 Agent握手或 Host 等价。

集成 merge 为 c4d8127a，源码/锁修复为 b52c38f3。相关 Python 两文件合计166pass；
Host 原 package 检查通过独立离线安装、SDK/Zod哈希、导入和确定性字节验证。
Ruff通过；必要仓库门（含identity/BOM）通过，Host当前BOM绑定 b52c38f3 的
source/tree/digest。一次定点非作者审查接受本修复。额外本地 mypy 未通过：当前
环境缺 torch/huggingface_hub 等可选依赖，跨入研究导入链产生113条/53文件诊断，
local_models.py无报错；保留原日志，不放宽检查或扩到模型依赖设置。完整Python/
跨平台门由一次正式CI执行，不能把本地定点通过概括为全套绿。

源码/锁/BOM 与最终 CI 结果以 PR191 最新 head 和实际检查为准；旧失败及旧实跑
不改标新候选。Python 改动只进 Workbench Managed 命令分支；Host 锁改动影响
独立打包，Reference driver/SDK/Runtime执行源码及已加载Mod均未因此改变。
这说明路径影响边界，不构成最终候选的新实跑证明。范围到定点回归、必要仓库门、
一次稳定候选推送和 PR交接为止；不合并/关闭 PR，不开其他P1工作。
