# 第19轮冻结底稿有界审计（私有附件）

本件是合成冻结定义的依据，不是批准版，不改变目标、范围、协议或数据用途。仅交 root 并入一份总报告；未另写共享一页报告。

历史查过：先读 K0/K1/K2 与 K4 已有账本地图，仅对缺口定向读现行架构、组件、协议、版本、研究数据 owner 和原 session 的用户消息。没有读原始 Human 内容、密钥或反编译 sts2，没有启动游戏、云或测试，没有修改原件、旧报告、历史 refs 或产品代码。

实读源码基准：`work/p1-sprint-C-20261018/repo`，HEAD `eafa9a7c6cb737548bccb2f3856336f0050ab979`，只读检查为 clean。root 后续提供 develop `ea7c14e8969006fa0e470678198ef94aca471f90`、tree 与 eafa 相同、integration CI39120207920 PASS；本附件未独立重查远端。root 提供 Mod6c 已安装、英文菜单可见，B 唯一游戏进程等待输入；这些不等于录制预检通过，最终状态由 root 当前回执负责。

## 可直接采用的结论

- 用户明确要求训练前冻结、全部幕、英文整局转换，以第一轮训练和快速复用为中心。API、库和 CLI 是主入口，GUI 映射已有服务。
- R20、Workbench 短验收和最终 Managed40/Reference20 有直接用户来源，不能按“只是 Claude 建议”删掉；应以现有最小调用链完成，不能推导出复杂 GUI 或全平台重构。
- 本轮最简 R2+Modal 已有直接指示。上传哪些对象、费用和实际消费操作仍按各自已有授权；不能因此自动上传两个大数据根或开始训练。
- 需要直接修正文档事实：过时状态、混淆组件与协议的串行图，以及把 record→dataset 阶段线说成已经冻结研究消费合同的表述。
- 需要交用户确认的范围：逐幕机制表的硬门强度、云最小消费细门、Agent 信息复验时点。保留现有要求，不通过审计自动放宽，也不借底稿暗增门槛。

## 事实错误、过时、有效与待定

| 编号 | 分类 | 审计结果及处理 |
|---|---|---|
| F01 | 过时事实，可直接更正 | 冻结提案§3仍写 888 当前、#198 Draft、环境整局转换未做；全景写 CURRENT 仍第12轮。实读 CURRENT 已第16轮；run-scope 源实现回执存在。应分别改成“源码实现已交、真实整局资格未完成”和 root 当前安装/PR状态。源码完成不是实跑完成。 |
| F02 | 架构事实，可直接更正 | 草图把 Mod、Host Runtime、P1、AgentProgramPort、采集和研究处理放成强制串行。Mod 是包装；Host 能力不是 Host Runtime；SDK 是实现库；P1 环境合同与 Agent 端口不同；执行 context 横切各角色。拆成在线闭环、并列记录路线与离线学习闭环。依据 ARCH:26–60、69–99、117–162；COMP:38–89；ADR16:7–56。 |
| F03 | 范围表述错误，事实说明可改，目标取舍待批 | “冻结线就是 P1 记录和数据集之间的那条合同，离线训练只读冻结版本的 P1 记录”过强。P1 发布冻结固定生产/解释身份，并不自动固定 Projection/InputSpec/TargetSpec、来源准入、use、划分或数据门槛。历史多源路线不能被一句话废止；本批选用 P1 要明确为本批选择。依据 DATA:3–28、HUMAN:33–43、ARCH:132–158、Q3:19–29、VERSION:20–58。 |
| F04 | 有效方向，限界可直接写清 | “冻结后不用一直翻历史”应兑现成入口能定位 owner、状态、出处、适用范围和资产去向；不再每轮从头考古。它不是穷尽历史全部知识的保证，不要求重验每条旧结论。K1 已明确135目录仅 metadata、迁入未核实等边界；这些可以诚实保留。 |
| F05 | 目标/范围取舍，需用户确定 | 全部幕不自动推出每项共同机制在每幕都必须实测。v0.3 将同14行复制三幕，是覆盖索引，不宜未经确认变成42个全部通过的硬门。建议区分共同机制、幕特有机制、跨幕及终局；未遇行写到达办法，真正必验清单交用户确认。 |
| F06 | 用户已定与新细化分开 | 最简 R2+Modal 已由 U19 直接要求；原件/P1记录上传、读回 hash、Modal 消费是 K5 的具体方案细化。它是服务训练的接缝，不是 P1 固有协议语义，也不是任意上传/费用许可。不得先搬40G历史或重建 Hub。 |
| F07 | 有效已定，保留 | R20 两端、Workbench 短验收、最终40/20在 U14 直接要求中。已有私有 R20 外包/harness，应该快速复用；SDK probe、repo 内 Agent 或材料 syntax-check 都不是独立包双 Host 实证。 |
| F08 | 版本说明过粗，可直接更正原则 | 不能机械写“普通缺陷一律 r1.x，能力或信息一律 r2”来代替 owning 兼容性判断。按公开语义、实际消费者、解释变化与数据影响决定版本/派生；旧原件及 producer 不改，新解释身份另记。r1.x 标签本身不证明事实保持。依据 VERSION:20–58、ARCH:101–108。 |
| F09 | 最新指令执行；证据层标明 | root 明确转交本轮最新直接用户指令：旧中文 Reference 缺陷只登记，新英文做严格验收，真实 Managed 缺陷继续修。照此执行。所选 session 用户消息未发现这句逐字原文，不能把 START_HERE 转述伪装成本审计已直接重验；root 应补对应原 message。 |
| F10 | 有效复用，避免虚假替代 | 成熟 Annotator 的采集、Close、pack、存储和操作经验可快速复用；trace4/canonical3/bundle3 与 Source4 信息合同不同，不能承诺旧数据无损变 P1。K0 估旧适配16–48+人时且存在不可恢复事实，当前第一轮无须先迁全部旧格式。 |

## 11条验收：依据、第一轮训练价值、成本与权限

成本为既有估算或建议的成本边界，不是本轮测量或承诺。此表不是最终 live 进度，root 用最新实际回执填进总报告。

| # | 验收 | 用户已定 / Claude细化 / 待确定 | 第一轮训练价值 | 成本、复用与本次建议 |
|---|---|---|---|---|
| 1 | 全幕 Reference 正确性 | U14直接定全幕、英文、Act3目标、尽量赢及胜利机制行；U15采纳提案§4/§6计数口径，现行≥3同候选英文整局。不是 UBoss 中文“最多3局”预算。 | 原始公开事实、选择与结局可靠；三局是环境验收样本，不是训练数据数量门槛。 | 主要真人时间；全景6–8h只是Claude估算。自然 loss 可算整局，Act3/win另列；不暗改成三局都赢或三局都到Act3。当前没有本审计核验的新英文整局凭证。 |
| 2 | Managed 一致、最终40/20 | U14直接要求严格到底、候选 Managed40/Reference20；旧中文处理依最新指令。 | 避免训练/评测的公开事实、动作与真实游戏不同。 | 复用原比较器、按新英文局配对；稳定候选再跑一套40/20，不为每个小改反复全批。旧诊断终局相同不等于逐步严格；unknown/技术中断/incomparable各记。 |
| 3 | 机制覆盖表 | U14直接要求1–3幕和胜利必验行及未覆盖到达办法；全机制×全幕笛卡尔积没有直接依据。 | 显示首轮环境能力范围、漏项和真实失败位置。 | 复用轨迹索引/owner，按必要位置核；穷尽所有事件无界。建议共同/幕特有/跨幕/终局分层。真正硬门范围交用户确认，不能自行降低。 |
| 4 | 英文 | U14直接要求 eng 新Mod+记录器预检、英文整局。 | 与 Q3 文字输入一致，避免中英文混样。 | 复用 eng 模板；Reference eng、Managed en 绑定同语义，记录实际语言/loaded身份。与B整局共用，不再另造一局要求。旧888成功不授新Mod资格。 |
| 5 | 采集/转换路径 | U14直接要求 Source4→P1 整局转换冻结前完成；选择器已有R21要求。环境采集不限Human/鼠标。 | 得到可验证原生示范和独立 Agent/选择器记录，为训练供数。 | K0两接缝6–16h是旧估，已有修复后不从零重估；run-scope源码已交，真实完整局仍待证。分别列 bounded complete、起止/连续性、自然terminal、Human来源、训练准入。直接AgentRun不依赖Source4。 |
| 6 | CLI/API、Workbench短验收、R20 | U14直接要求 Workbench/R20；U19主API/库/CLI与GUI薄映射不取消现有短验收。 | 保证训练得到的完整Agent能运行、Stop、查记录，独立包不依赖repo内部策略。 | 复用2684B外包与已有harness；计划每Host1submission/8calls/60s是有界材料，未执行不算通过。GUI只映射原应用服务，不新增manager或复杂页。 |
| 7 | 文档/closeout | U14直接要求协议16–18、CURRENT、project:closeout；单份冻结定义仍待用户批。 | 下位消费者能找到确切身份、操作、差异与回滚。 | 更新原owner/路由，复用现有BOM与组合回执，不新建版本系统。CURRENT已Round16，但其60dc/202未合快照本身也要刷新；文档校验不等于全游戏验收。 |
| 8 | K收敛 | UK直接要求K0/K1/K2并行、冻结等K；U19要求历史审计。K条目具体完成强度仍需明确。 | 找到可快复用功能、保留独有成果，新agent不反复重做调查。 | 复用2063行账本、100行功能图、401文档索引；K4既有9/10，B操作单与K3批准/执行是不同问题。入口→owner/出处/去向可达，不要求全史重验。历史资产处理按直接批准。 |
| 9 | 最简云 | U19直接要求R2+Modal最简；具体上传/读回/Modal消费为K5细化，纳入合成定义确认。 | 远程训练能读到与本地一致的获准数据。 | 复用原store/provider，对一个获批最小样本核接缝；不先搬两大数据根。不读密钥/签名URL，上传对象、费用、消费操作按各自授权。Hub/团队/云页只标状态。 |
| 10 | Agent公开信息复验 | U14直接说Agent观察不变、“之后再复验信息全不全”；提案将其固定成冻结前逐类验收，是时点/强度细化。 | 防止人看GUI能选但Agent缺公开信息，直接影响Input和决策。 | 用已录观察与真实画面对照，针对支持机制复验；不建永久全游戏审查系统。缺口记owner及本批影响；改协议/范围交用户定。不能声称人只看P1。 |
| 11 | 每组件/协议/格式链路位置 | U19直接要求为何存在、如何服务第一轮训练。 | 防止重复实现与权威混淆，训练和运行接线清楚。 | 在K2与既有组件表补用途/消费者/状态，不重构整个架构。改成在线/记录/研究三路，执行context与存储横切。 |

## 原始用户证据与计数口径

原 session：`/Users/fire/.codex/sessions/2026/10/17/rollout-2026-10-17T10-30-35-01a14744-8623-7f83-a92d-a57418dd5597.jsonl`。只筛 `response_item role=user` 的目标关键词；未扫描 Human 录制内容。精确所选用户 record 及 hash 在 `audit.json`。

| 代号 | session行 / message | 直接说明 |
|---|---|---|
| U14 | 16719 / `msg_01a14da5-5a2b-7d62-aab7-171f01ba8792`，06:14Z | 全幕、Source4整局、英文预检/短段/整局、Act3目标、尽量赢、逐幕/胜利行、严格重放、最终40/20、Workbench与R20。 |
| U15 | 16737 / `msg_01a14db1-a1c1-7421-a7ec-a219570f20e4`，06:27Z | 采纳第14轮审计及提案4/6计数和时刻，不降标准。数字的证据是“采纳口径+当时底稿”，U15原句没有逐字写≥3；本次未重建当时文件bytes。 |
| UK | 19735 / `msg_01a14dfb-75de-72b1-b491-f07575eba0e3`，07:48Z | K0深查/K1K2并行/冻结等K，删除移动历史资产须批。 |
| U19 | 22971 / `msg_01a14e95-b56a-72b2-a295-b69e19aa3480`，10:36Z | 底稿需审计并合成定义；首轮目标/API库CLI/GUI薄映射/最简R2+Modal/K6；11条进度表。 |
| UBoss | 10084 / `msg_01a14c70-6a5a-71c0-8c35-32559a58c0be`，00:36Z | 中文R21 Boss窗口最多3局、每局150分钟，是旧窗口预算，不能转作最终英文至少3局的同一依据。 |

“第三幕”与“胜利”有直接目标来源；用户原话是“目标打到第三幕，尽量打赢”和“胜利终局必验机制逐行列出”。这支持保留真实覆盖行和缺口交用户，不支持擅自设“用户必须赢三局”。

## 建议冻结定义结构（不是批准版）

1. **目标和本批边界**：首轮训练可用的P1发布基线；全部幕；最终冻结PR由用户批。已有验收不自动减少，新增门槛不暗中加入。
2. **三路架构/owner**：在线运行闭环、独立采集与直接Agent记录DAG、离线研究到新Agent评测闭环；只映射既有职责。
3. **现有发布组合**：复用 `platform-bom/v<version>` 与组件独立版本，固定游戏、Mod、Managed、SDK/Host/Runtime、模板、转换器/比较器；workspace SHA、path-scoped source、产物hash与实际loaded分别记录。merge/tag是待确认交付形式，不新建registry。
4. **P1合同与支持范围**：观察/动作/承诺/ACK/Stop/unknown/语言/tuple/例外；共同机制、幕特有、跨幕、终局及未遇的到达办法。
5. **原件和解释**：各grammar/producer/源连续性、Source4整局scope、Agent-session目录与旧reader；派生身份、离线校验和跨Host比较规则。
6. **11条证据矩阵**：逐条当前状态、exact原件、剩余成本/依赖、已批或待定；不合并不同候选/来源/语言，也不混自然终局与技术停止。
7. **record→research接缝**：冻结可校验原件及其解释；Projection/Input/Target、数据准入/use/划分/门槛、词表、训练配置和评测方案仍由Q链owner另定。阶段线不等于这些合同已全部冻结。
8. **最简云和工作台**：R2承载对象和读回；Modal消费获批输入/作业。CLI/API/库是主接口；Workbench映射原服务。Hub/团队/复杂云页只列状态。
9. **K项目地图/资产去向**：入口能定位owner/状态/依据/去向；未知与适用范围如实保留；建议/批准/执行回执分开。
10. **发布后变化与决定清单**：按实际兼容性、公开语义与数据影响修订；不改旧原件。用户只拍真实目标、范围、协议、研究配置、发布和未授权迁移，普通修复继续。

建议图：

```text
在线：完整Agent ⇄ AgentProgramPort ⇄ Policy Runtime ⇄ SDK(P1) ⇄ Connector ⇄ Host/STS2
      执行请求/权限/预算/Stop/实例与证据关联横切；Host Runtime负责启动/隔离/恢复
      Reference=真游戏+Mod；Managed=另一Host路径；Mod是包装，不是另一套游戏规则

记录：原生变化/输入 → Annotator Source4原件 → Evidence → SDK解释为P1轨迹
      Agent/选择器 → Runtime AgentRun原件 → Evidence/原replay（独立路线）
      不可变原件保留producer、缺口、父局及候选；存储位置可替换

研究：获准来源/成员/use → Projection/InputSpec/TargetSpec → 数据集/划分/训练Run
      → checkpoint/Model → 完整Agent组合 → 在线端口实际运行 → 评测
      R2承载对象；Modal承载已批作业；二者不拥有游戏或研究准入语义
```

可在P1原件/解释与Q链研究消费之间画本次交付阶段线；不要抹掉P1在线合同、Agent端口、记录读取合同、研究投影合同的差别。Model是Agent的可选部件，不是完整Agent。

## 核对过的文件位置

所有相对路径均以 `/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling` 为根；JSON保存绝对路径、行范围和实读文件hash。

- ARCH：`work/p1-sprint-C-20261018/repo/docs/ARCHITECTURE.md:26–162`。
- COMP：同repo `docs/COMPONENTS.md:38–89`；ADR16：`docs/adr/0016-agent-composition-and-execution-request.md:7–56`。
- DATA：同repo `python/docs/DATA_AND_PROVENANCE.md:3–28`；HUMAN：`python/docs/HUMAN_CORPUS.md:33–43`；VERSION：`docs/VERSIONING.md:3–58`。
- P1：同repo `docs/protocols/P1_GLOBAL_R1.zh-CN.md:137–182`；CURRENT：`docs/memory/CURRENT.md:1–59`；SCOPE：`docs/plans/p1-prefreeze-2026-10-18/SCOPE_v0.3.zh-CN.md:31–94`。
- K0：`work/p1-K0-20261018/P1-K0-recording-formats-2026-10-18.md:7–22`；K1：交接包 `replies/P1-K1-assets-2026-10-18.md:3–21`；K2：`work/p1-K2-20261018/README.md:9–119`；K4：`work/p1-K4-20261018/Q5-Q9-followup.md:11–33`。
- RUNSCOPE：`work/p1-source4-active-run-entry-20261018/run-scope-implementation-receipt.json:1–35`。代码交付证明，不是完整游戏实测。
- R20：交接包 `replies/P1-round16-C-R20-execution-materials-2026-10-18.md:3–11`。
- Q3：交接包 `Q3_BATCH1_PLAN_2026-10-17.zh-CN.md:12–29`，明确P1前只低优先级M0、数据怎么分/门槛待定、Modal仅一次基线额度。
- 待审底稿：交接包 `00_START_HERE.md:21–73`、`P1_FREEZE_SCOPE_PROPOSAL_2026-10-18.zh-CN.md:1–155`、`P1_TO_FIRST_TRAINING_OVERVIEW_2026-10-18.zh-CN.md:1–104`、`P1_PREFREEZE_CONVERGENCE_CARD_K_2026-10-18.zh-CN.md:1–156`。它们是参考与派工入口，不能代替用户直接授权。
