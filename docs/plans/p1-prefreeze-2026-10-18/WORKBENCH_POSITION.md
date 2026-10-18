# K6 附件：工作台定位（只读核实，2026-10-18）

历史查过：第19轮入口/全景、冻结底稿0.5和卡K；K0格式/历史、K2地图/功能账本；9/27实际Workbench收据、10/8 E6及ADR-0016历史；第16轮C工作台/R20源码与独审，再核当前 `eafa9a7c` 的实际调用链。未读凭据或Human正文，未改源码/运行配置、启动Host/Game、构建、测试或云。本文是root唯一总报告的附件，不另派工作。

**定位：Workbench是现有Python应用用例层及其可选人类操作面板。** 它为本机操作者和工程/研究脚本把“选环境/完整Agent→准备→运行→停止→查原件”收到同一入口；第一轮训练需要它的可复用API/库和状态记录，不需要新建GUI或团队产品。`python/spireagent/workbench`是正式Application；`apps/workbench`已退役为无HTML诊断API，不能当另一个工作台。CLI/库/API是主操作面，浏览器/Mod原生页是同一用例的映射。

| 上层链的位置 | 原owner；工作台做什么 |
|---|---|
| 真实世界→公开环境/P1 | STS2/Native Foundation/Connector/共享SDK决定规则、公开信息和合法输入；工作台只选择受信环境/配置，不判断合法性 |
| 启动/隔离/续玩 | Host Runtime与LocalEnvironmentService拥有进程/实例/模板；工作台调用prepare/attach/finish和原存档owner |
| 完整Agent→在线决策 | AgentComposition/AgentRunRequest描述程序、输入/历史、0..N模型语义；现实现受信组合；LocalModelService加载；Runtime拥有Consume/ACK、预算、Stop/unknown，Agent负责策略 |
| 环境采集/AgentRun→校验记录 | Annotator/RecordingService或Runtime分别产原件，Evidence验证；工作台显式Source4/来源声明、Close/import/查看，不制造Human来源或补事实 |
| 数据/训练→模型→重上场 | Store/Curation/Training/registration既有服务；工作台只提交既有用例、显示结果。Q3的P1适配器、用途/划分、词表/配方及Q1配置等冻结后另定 |
| R2/Modal与团队 | 最简存取路径由K5定；现Hub/账号/云页面保留原状态，不是本次本机启动/Stop/查记录的前提 |

**GUI有没有独有逻辑？要区分。** 游戏/采集/数据准入/训练/预算的业务权威都在原服务，未发现本次P1需只能靠GUI才能调用的业务算法；但GUI并非“零代码”：浏览器有表单/分页/轮询、pending按钮与跨刷新unknown确认marker，原生API有配对授权及有界展示投影。这些是已有交互/信任边界适配，保留；不把它们移成第二份业务状态机，也不为口号大重构。Source/model互斥、原实例/epoch、unknown、Close和Stop仍由服务复核。两种GUI暴露的按钮也不完全相同：GLOBAL登记现有浏览器API/库可用，CLI没有登记子命令，原生页也不独立提供该登记；“主界面CLI/API”不要求每个API现在都配同名CLI。

**可立即复用的最短路线（本轮未执行）：**
1. 对已有精确安装/配置，用 `uv run --project python --locked --extra cloud python -m spireagent.workbench project open --config CONFIG --no-browser` 启动同一Application；已有依赖时不必走根`npm run workbench`包装器（它还执行uv sync/npm ci）。无浏览器也能调用库/API。prepare/attached Host要先核最终tuple；GLOBAL attached历史限制不作为此次最小路线前提。
2. 已有selection可直接用 `project model --action catalog/readiness/start --selection ID`；新GLOBAL通过现 `POST /api/local-model-registrations/register-global-program {environment_kind:native|managed}` 或Application `register_builtin_teacher(global_program=True,...)`登记。原`/api/local-models/prepare`在Human加载、`/request→/run-agent`绑定完整请求；`project native-episode --action prepare-owned/.../run-agent`已有相同Application入口，不能虚构`model --action register-global`。认证用现有客户端/私有配置，不打印token。
3. `project model --action one_step`→原Runtime一次tick；`project model --action stop`→原RuntimeStop/验证记录。这里的**model stop停止Agent**，`project stop`是停止Application服务，二者不混用。看 `project model --action status/catalog`的原报告与run_id；底层`agent-runs/run-*`原件用 `node components/policy-runtime/bin/sts2-p1.mjs replay --run-directory RUN`/trajectory核验，界面“实战记录”也是该报告投影。
4. R20用已封存仓库外first-choice包与既备私有材料 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-sprint-C-20261018/R20-EXECUTION-MATERIALS.md`)：root填最终HEAD/Runtime code/bridge SHA、Managed candidate和Reference loaded/template tuple；复用Host+Runtime owners，两Host各1fresh短run、1底层submission/8calls/60s→实际Consume/ACK/Next/Receipt/Result/独立sealed后继→显式Stop/正常close→官方replay，前后核同一外包SHA。旧包1两Host脚本在仓库内部，不能改名当R20。现suite硬码4000/8000，不能冒称一输入。**这条是外部Agent接入验收；不要求新增GUI。**

**冻结前保留/不做与成本：** 保留现有Application、CLI/API/库、简洁选择器、现有浏览器/Mod薄页与独立Human/Stop；C的GLOBAL按钮与闭合P1 context源码已存在当前候选，独审已完成，不能再列为“完全未实现”。本K6新增源码需求为0；余下是最终tuple的API/CLI实际启动→决策→Stop→查记录及外部包双Host验收。依赖就绪后预估准备参数10–20分钟、两Host短验5–15分钟，取决于实际启动/Reference可见性；Mod统一构建/安装与B预检成本由关键路径另计，不复制构建、不挡用户录制。小包2684B、短证据一般MB级；空间以真实测量为准，不新增大缓存/历史副本。

**需统一底稿的两点：** ①全景F7“K6没开始/R20材料准备中”已过时：定位本附件已交、源码接缝和材料已存在，真实最终tuple验收仍待；②旧`UI_INTERACTION_SPEC`的“全部训练/团队/云页均需完整嵌入游戏”是历史目标，本次用户0.5明确不新增复杂GUI/团队系统，不能继续作为本次冻结门。建议地图标“历史完整GUI目标，冻结后按真实需求再定”，不是删除现有功能或替用户承诺永久废弃。主线CLI/API足以过产品路径第6条；是否以后补统一GLOBAL登记CLI或扩GUI属于未来取舍，当前无需追加实现。

可定位依据：[docs/BASELINE_V1_SPEC.zh-CN.md:429](../../BASELINE_V1_SPEC.zh-CN.md#L429)、[python/spireagent/workbench/local_model_cli.py:139](../../../python/spireagent/workbench/local_model_cli.py#L139)、[python/spireagent/workbench/developer_server.py:1134](../../../python/spireagent/workbench/developer_server.py#L1134)、[python/spireagent/workbench/native_workbench_api.py:832](../../../python/spireagent/workbench/native_workbench_api.py#L832)、[python/spireagent/console/project.js:4058](../../../python/spireagent/console/project.js#L4058)；来源/历史SHA和精确范围：`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-K6-20261018/K6-workbench-position.json`。当前源码/历史证据不替代最终tuple实测。
