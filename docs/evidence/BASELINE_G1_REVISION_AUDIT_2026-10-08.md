# G1-RC2 修订审计：边界、作用域、模型与输入预算

日期：2026-10-08。源核对基点`b1875e9744b858d756897c6f46071df20362ab52`。对象：[RC2说明](../design/BASELINE_G1_AGENT_BOUNDARY_AND_BUDGET.zh-CN.md)、修订G1三主文件和现行工程规范。G1未批准，D01及交互边界未定。RC1在其原commit的审查与[旧manifest](BASELINE_G1_MANIFEST_2026-10-07.json)保留历史含义；不能拿旧hash核对当前已修订内容或将旧PASS转移过来。

## 1. 本轮核对与修正

| 项 | 直接核对及修正 | 证据范围 |
| --- | --- | --- |
| D01状态 | 用户连续两次澄清任何Agent/管理边界也需比较；RC1的已选方向撤回为候选 | 当前用户方向，非G1接受 |
| 三部分协议 | Host capability、协议core、Agent-facing合同分别定义；三种BND分配和机械/策略工作可移动 | 架构方案，不是3个新服务实现 |
| 全角色A0–A10 | 原生角色/升阶及星数/球/宠物等已有通用读路径；设计扩围、分批验收 | source/native inspection，非全部运行资格 |
| 教程/解锁 | native unlock all与FTUE开关存在，Host journey有教程参数；一般Workbench解锁入口未找到 | 没有运行命令或修改存档；旧run的unlock state冻结 |
| 数据转换 | 允许有声明假设/有损/推断的任务性D监督，不一律要求先证明保真或等价 | 原件、研究假设和已知事实分开；uses仍受管 |
| 模型 | LightAction与ExperimentalDSimple图区别、状态BPE与候选byte编码、旧renderer实际为compact JSON | source核对；没有Torch推理/训练 |
| 模型预算 | 7个合成fixture，固定公开Qwen tokenizer做分词、byte与候选计数 | 精确fixture数值，不是实战分布或性能 |
| 工程标准 | PROJECT_SYSTEM已有owner；纠正其Python lint描述，补词汇/写作/执行地图；CONTRIBUTING跨仓旧指引修正 | 文档规范及实际配置读取；未批量格式化生产代码 |

独立只读调查分为native/scope/cost、model/input和governance；另一次独立架构挑战比较BND分配，反对提前固定单一Agent边界。root核读关键代码、原生证据与结果，最终修订另经独立审查。

## 2. 原生及现有源码依据

原生DLL本轮重新校验SHA256：`9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`。仅只读ILSpy，没有保存游戏/反编译源码到Git。

原生依据为`UnlockConsoleCmd.Process/UnlockAll`、`Player.UnlockState`、`NAcceptTutorialsFtue.NoTutorials`、`CardPile.MaxCardsInHand`及`CardPileCmd`正常加手牌路径、`OrbCmd.AddSlots`与`OrbQueue`、`CombatState.AddCreature`、`AscensionManager.HasLevel`。结论限制：正常手牌10、球正常加槽10；其他容器未建立统一上限，不证明任意大组合可达。解锁影响进度文件，不自动重写已有run的unlock snapshot。

以下固定源码链接支持相应组件事实：

- [直接combat目录](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/b1875e9744b858d756897c6f46071df20362ab52/components/native-foundation/src/NativeCombatDecisionProvider.cs#L105)：牌/药水按当前原生目标关系展开。
- [持牌target目录](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/b1875e9744b858d756897c6f46071df20362ab52/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuFrameBuilder.cs#L180)：focus/confirm/cancel按mode，不将互斥页相加。
- [信息分类来源](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/b1875e9744b858d756897c6f46071df20362ab52/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuInformation.cs#L684)：提示/inspect列表；当前实现与拟议展平目录不同。
- [角色资源](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/b1875e9744b858d756897c6f46071df20362ab52/components/connector/host/LiveHost/LiveContextReader.cs#L259)：orbs/star/pets等公开投影；不据此声明全角色模型资格。
- [教程参数处理](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/b1875e9744b858d756897c6f46071df20362ab52/components/host-runtime/src/journey-probe.mjs#L115)：已有journey helper，不是通用Workbench准备端口。
- [LightAction核心](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/b1875e9744b858d756897c6f46071df20362ab52/python/stpd/models/light_action_m2.py#L140)、[动作CNN](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/b1875e9744b858d756897c6f46071df20362ab52/python/stpd/models/light_action_encoder.py#L27)、[动作byte codec](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/b1875e9744b858d756897c6f46071df20362ab52/python/stpd/light_action_codec.py#L12)：page/W与候选分离，feedback另编码，动作258词表。
- [当前text renderer](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/b1875e9744b858d756897c6f46071df20362ab52/python/stpd/fullrun/text_menu_inputs.py#L74)、[state词表](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/b1875e9744b858d756897c6f46071df20362ab52/python/stpd/fullrun/light_action_inputs.py#L47)：compact JSON与train-only BPE，不能冒称RC2展开renderer已实现。

规范依据另核 `.editorconfig`、`python/pyproject.toml:58`与`python/tools/project.py`、TS的strict配置、各C#项目属性、PROJECT_SYSTEM及CONTRIBUTING。没有统一ESLint或全仓ruff-format gate的证据；语言约定不等于全部机器强制。

## 3. 预算实验与可复核边界

[程序](../../tools/g1_input_budget_probe.py)使用合成字段/文字，没有真实游戏转储/记录或训练材料。运行依赖通过隔离uv缓存提供，不改项目依赖；公开tokenizer仅落临时目录，不下载模型权重。报告保留script/tokenizer SHA、固定revision、版本、fixture假设和输入文本hash。

```sh
uv run --no-project --with tokenizers==0.22.2 python tools/g1_input_budget_probe.py --tokenizer /ABS/tokenizer.json --tokenizer-revision c1899de289a04d12100db370d81485cdf75e47ca
```

使用[官方Qwen3 tokenizer固定revision](https://huggingface.co/Qwen/Qwen3-0.6B/blob/c1899de289a04d12100db370d81485cdf75e47ca/tokenizer.json)，tokenizer SHA为`aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`。无padding/truncation、无special tokens/chat模板。tokenizer是参考尺子，新scratch词表不同则计数不同；token长度不能从字符数固定换算。[Hugging Face tokenization说明](https://huggingface.co/docs/tokenizers/python/latest/pipeline.html)支持区分分词/预处理/后处理；[padding/truncation说明](https://huggingface.co/docs/transformers/main/pad_truncation)不构成本项目静默截断许可。

输出见[完整JSON](BASELINE_G1_INPUT_BUDGET_2026-10-08.json)。250项假设展平目录与当前text-v2 root约22是不同语义/粒度，报告不把两者当同一实现结果。5,745是候选byte-code总长度含首尾，3,625是固定参考BPE状态token；未测试神经时延/显存/实际大场景可达性。

结构化数据也可以不采用文本输入；[Gymnasium Dict空间](https://gymnasium.farama.org/v1.1.0/api/spaces/)是接口可组合多字段的参考，不证明本项目新的结构模型已实现或更优。外部规范仅支持设计边界，未引入Gym或Transformer依赖到生产代码。

## 4. 当前检查和最终审查

预算程序使用仓库当前Ruff版本0.16.4单文件格式/规则检查，修复导入顺序和两处长行；未改全仓门禁。7场景输出已执行；独立审查复现7组输出，与保存JSON完整相等；script SHA `c8576bad64df9466cedd85e199f8c5b36790589706920c6dd946985531afd990`，report SHA `ce78ef54f448fce23868d65d23202c1e7e2cde802da8ee065e1c5bb9816092d9`。补充非空先前动作编码成本后保留相同预算数值。文档/治理17 fixtures、0 warnings通过，最终manifest在关闭时重验。

旧RC1独立PASS与b1875e97完整hosted CI只属于原候选。本轮尚未产生游戏/Human、模型推理/训练、完整角色资格或性能结论。D01、边界分配、第一Model最终表示与G1接受仍待用户决定。

最终架构审查发现并修正三类P2新鲜度问题：错误的Oct8审计链接、总册优先级漏列RC2说明、旧路由仍称A已选。最终复核返回`READY_FOR_OWNER_DISCUSSION_OF_RC2`，无未处理P1/P2。独立核对27文件SHA/字节全部一致；旧RC1审查不转移。

最终独立核对的主说明SHA256：`cf0148ef90e33640a454e8c2fedaf4f6322089740a95c73ae09ddc56c52ab5dd`。三主文件分别为review `9060dda0abb5211eaff3a3a8386fb66db7af936c8b8e0389310d83cd75f415c3`、contracts `ec3bc5d45207235770b316626c59a8e271e11be1de57eda14554aa31c70a70bb`、roadmap `ae3050b7789b2070afebdb3807e6d41db5e02ce0f7d4d7b0becd49dac8c85a19`。本段回执填写后只行政重生manifest，不改变已审正文。
