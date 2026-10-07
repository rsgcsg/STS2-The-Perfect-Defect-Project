# G1-v1 独立审查与限定实施接受

日期：2026-10-08。source审计基点`bb87e35a02b715e82ba3fbfb373d8aee094c12d0`。本轮用户已明确委托项目负责人自主完整收敛G1并继续真实实现/验证，必要操作授权不限于列举项，model/train付费合计<=20 USD；不再沿用此前仅设计、等待用户逐项阅读批准的推进限制。

## 审查与修正

冷工程/新工程师审查最初判定旧包**不能直接实施**：规范叠加、capture/consume未决、I/F覆盖矛盾、owner-ready与effect-fenced混用、S早期包不具体、通用cloud/UI前置过重、范围矩阵未冻结。lead据此收敛[唯一规范](../BASELINE_V1_SPEC.zh-CN.md)及[执行包](../plans/BASELINE_V1_EXECUTION_2026-10-08.md)，README/AGENTS/CURRENT改为单一入口，旧八份主提案明确降为历史，不再通过非冲突部分拼义务。

四个独立视角均有具体source核对：

- 新工程师/工程架构：复核唯一规范后，支持限定S0实施，无需用户回答的设计阻断；要求digest、score-only N、深冻结与真实分步关系进入实现测试。
- 原生/Host：复核text-v2 current-cursor兼容实例可行；必须在game main thread序列化公开Snapshot为bytes，native callback不可进入store；capsule不是行动权威，旧submit复验保留。
- AI/data：选择S-M2-0早期小图、I/F-off、AgentTeacher真实来源和0美元CPU路径；要求相同digest仍重建当前E/ref绑定、直接结构化中间值不反解析renderer、advance=false保留N、TBPTT只计advance。四项已写入规范后明确接受本轮implementation scope。
- 工作流/交付：当前PR165含可执行工具，full路由不能回溯跳过；独立修复纯prose过重路由需先加强repository guards并完整验证router自身。旧product/recovery不盲合全部祖先，S0不以通用cloud或旧campaign为前置。

AI/data最后实质规范复核SHA为`6f019e3ca5db06e6e63b722f46276ef817a87162d8a6380e355c04a2e65224ea`；之后仅将状态句指向本接受记录，实质合同未改。lead核读关键现有代码/测试及审查结论，接受当前文档字节所描述的范围，后续通过Git commit固定最终身份。没有拿工作者返回当作已执行native/训练结果。

## 接受结论

**ACCEPTED_G1_V1_FOR_S0_IMPLEMENTATION**：唯一选定基线是BND-2默认机械服务、请求取得的sampled public text-v2 compatibility profile、只读byte capsule、I/F-off S-M2-0、local既有存储/run/use服务、真实AgentTeacher→N→导出→native执行/停止闭环。

接受不扩大为最终native-flat/完整瞬态Human、全部L64/角色/难度、完整产品GUI/云、G2/G3、模型质量或任何安装运行资格。用户允许AI代执行以前列为Human/user的功能操作，结果标AI/nativeUI/Agent实际路径，不能用AI数据冒充真人示范。

## 当前实际检查及成本

文档/治理17 fixtures、零warnings通过；原README路由缺Skill索引被门禁发现并恢复，无弱化。root已核旧设计source、当前API和模型接缝。工作流独立审查另实际运行router/CI/receipt 21 focused tests通过；该执行不替新source测试。

本机只读确认16GiB内存、10逻辑CPU、约43GiB空闲空间；现有隔离可用Python3.11.15/Torch2.13.0，MPS可用，但首个模型采用CPU两线程。此时没有提交付费任务、运行训练、安装/启动游戏或修改数据；付费发生额0 USD。后续各操作仍需exact身份和实际回执。

## 交接和重新打开设计的条件

Source包保持独立worktree/branch与one writer；共享wire由Connector包负责，lead整合Runtime/真实链和BOM。出现无法表达真实native机制、来源/输入不一致、原生未知投递、范围被静默裁剪、资源不可控或试例反驳抽象时，修订唯一规范后再继续，不以consumer补丁绕过。其余已理解的实现/编译/test问题按包自主修复。
