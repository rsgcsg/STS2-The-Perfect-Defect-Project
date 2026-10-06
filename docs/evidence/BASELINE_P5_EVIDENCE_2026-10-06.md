# P5 有界证据：原生机制、参考探针与未测范围

日期：2026-10-06。源码审查基点：c5ddc26789376a5d230ca06d096d5b80fbf8b17c；产品候选 e30247defd859dfc75708fea4612c668e93cdbd2 仅用于注明的只读比较。结果服务 [P5 交付](../design/BASELINE_ACCEPTANCE_PLAN.zh-CN.md)和[场景册](../design/BASELINE_SCENARIO_SPEC.zh-CN.md)，不授权执行后续实现或训练。

## 1. 检查范围与原生身份

本机对应 v0.111.0 / 41cef1ea 的 STS2 DLL SHA256 重新计算为 9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4。使用已有 ILSpy 9.1.0.7988 经 dotnet 只读解析类型/方法，输出仅在进程内阅读；没有运行游戏代码、构建 Mod、操作游戏或将程序集/反编译源码写入仓库。

原临时 ILSpy 目录缺 runtimeconfig，第一次工具调用失败；改用本机已有完整全局安装成功，没有安装或修补依赖。复核者应使用自己合法安装的同 SHA 程序集和列出的符号，不把本地私有路径写进公开命令。程序集不同则需重新核对。

## 2. 原生符号与发现

| 证据 ID | 在该 DLL 中读取的符号 | 核实到的行为 | 设计使用 |
| --- | --- | --- | --- |
| NAT-01 | CardModel.TryManualPlay、NControllerCardPlay、NTargetManager | 完整 native card/target、untargeted confirm；target cancel 正常返回 null，接受/排队不等于效果完成 | SX-03/04；保留 native targeting，不合并所有 null 目标 |
| NAT-02 | NPotionPopup、NPotionHolder.TargetNode/UsePotion | popup 可先移除再等待 target；discard/use/close 不同；merchant 等目标不能随意以 null 代替 | SX-05；不能用 popup 消失推断药水消耗 |
| NAT-03 | CardSelectCmd.FromHand/FromHandForUpgrade、NPlayerHand | 手牌 max 时可替换；零/隐式选择路径；hand exit 可空结果完成 | SX-06；不套用网格规则 |
| NAT-04 | NSimpleCardSelectScreen、NChooseACardSelectionScreen、NCardGridSelectionScreen | grid capped toggle；min=0 的 native confirm；生成页 opening guard；grid/generated exit 可取消未完 Task | SX-07；区分 ready/empty/cancel |
| NAT-05 | NCombatPileCardSelectScreen.UpdateConfirmButton/CheckIfSelectionComplete/UpdatePileContents | 有效候选下的 clamped min/max、动态 selected 清理、空/全选可自动完成 | SX-08；native actual capability 优先于 prefs 算术 |
| NAT-06 | NDeckCardSelectScreen、NDeckUpgradeSelectScreen、NDeckTransformSelectScreen | selecting→preview→confirm；preview cancel 与 whole close/exit 不同；preview 使用原始对象的显示派生 | SX-09/10；不将 preview clone 当 operand |
| NAT-07 | CardReward.OnSelect/Populate/Reroll、CardRewardAlternative.Generate、PaelsWing.TryModifyCardRewardAlternatives | Skip 不完成 reward、reroll 留在当前过程、特殊替代可能完成并产生其他效果；多选 Hook | SX-12/13；不能按 label 推断操作语义 |
| NAT-08 | NRewardsScreen.UpdateScreenState/OnProceedButtonPressed、NLinkedRewardSet.GetReward | 独立/linked reward、nonterminal 自动关闭、terminal Proceed 与 group resolution | SX-12/14/19 |
| NAT-09 | MerchantEntry.OnTryPurchaseWrapper、MerchantCardRemovalEntry、OneOffSynchronizer.DoMerchantCardRemoval | native stock/gold/hook；移除最终选牌返回后扣费；cancel 无该扣费 | SX-09/15 |
| NAT-10 | NRestSiteRoom.AfterSelectingOptionAsync、RestSiteOption.Generate、SmithRestSiteOption.OnSelect、DigRestSiteOption.OnSelect | 可有剩余/追加选项；Smith cancel 非成功；新 Dig 结果不可前置 | SX-17 |
| NAT-11 | NTreasureRoom.OpenChest/OnProceedButtonPressed、NTreasureRoomRelicCollection | 正常/额外奖励先于 relic collection；空/skip/award 与后续流程不同 | SX-18 |
| NAT-12 | NEventRoom.SetOptions/OptionButtonClicked、NMapScreen.OnMapPointSelectedLocally、RunManager.ProceedFromTerminalRewardsScreen | 当前 options/locked、vote/travel、父事件返回/地图/跨幕不同路径 | SX-02/16/19 |

主作者独立复核了 NAT-05 的 clamp、NAT-07 的 Skip/reroll、NAT-09 的最终扣费、NAT-10 的多 option 与 NAT-11 的奖励先后顺序。其他符号由有边界的独立只读原生调查核对并与所列源码入口对照。该来源分工不是每个 caller/Hook 都经过实机验证的保证。

## 3. 源码对照与实际缺口

固定源码入口：

- [combat provider](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/native-foundation/src/NativeCombatDecisionProvider.cs)、[combat delivery](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/connector/host/LiveHost/CombatTurnSurfaceReader.cs#L232)、[target flow](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuCombat.cs#L94)。
- [hand selector](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/connector/host/LiveHost/CombatHandCardSelectionSurfaceReader.cs#L137)、[pile selector](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/connector/host/NativeUi/NativeCombatPileSelection.cs#L119)、[simple grid](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/connector/host/NativeUi/NativeSimpleCardSelection.cs#L108)。
- [deck upgrade stages](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/connector/host/NativeUi/NativeDeckUpgradeSelection.cs#L193)、[enchant](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/connector/host/LiveHost/DeckEnchantSurfaceReader.cs)、[bundle selection](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/connector/host/LiveHost/CardBundleSelectionSurfaceReader.cs)。
- [card reward qualification](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/connector/host/LiveHost/CardRewardSurfaceReader.cs#L276)、[native reward pages](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuRewardPages.cs)、[exact nested binding](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/c5ddc26789376a5d230ca06d096d5b80fbf8b17c/components/annotator/src/STS2HumanAnnotator.Mod/NativeNestedSelectorPatches.cs)。

发现的具体对照差异：原生 combat-pile confirm 用 min(MinSelect, displayedCount)，当前 adapter 使用 raw MinSelect；simple-grid 原生 min=0 初始 confirm 与 adapter 的 RequireManualConfirmation 限制不同。它们是本轮 source/native 差异，E1 需核具体 caller、加忠实回归并修复；没有在本轮修改源码或宣称已实机复现。

其他边界：visible-holder 遍历不自动证明完整逻辑列表；exact parent/child patch 的存在不证明每个 factory/嵌套情形已绑定；未归类 custom/FTUE/multiplayer 保留 gap；既有 narrow reward profile 只在 exact alternatives 满足条件时标 return，不把已有代码误报为所有替代都统一 Skip。

## 4. 合成探针与成本回执

工具：[baseline-design-probe.mjs](../../tools/baseline-design-probe.mjs)。命令：node tools/baseline-design-probe.mjs。

回执：[BASELINE_DESIGN_PROBE_2026-10-06.json](BASELINE_DESIGN_PROBE_2026-10-06.json)。首次提交版本 script SHA256 为 fda63641804c9d918f72c8d710664d66325809355dece4c0e72b882b622943ea；回执自身 SHA256 为 177b17667f5190e80836576c253bd93f7f0d1a3b743ae5001363b48589cfcb3c。重跑的计时可不同，不能要求回执字节恒等；须核 source digest 和 fixtureVersion，再比较不依赖计时的字段。

38 项分别为 15 项 reference_transition、4 项 binding_guard、16 项静态 design_counterexample、3 项 cost_fixture，通过只具有各自的验证范围；另有 24 条固定场景/信息配置成本行，Node v20.20.2 / darwin / arm64。计时是 reference JSON projection；字节、模型调用假设、原生导航假设、Read 数分别报告。fixture 无真实玩家数据、权重或 proprietary card 内容。

referenceAgent 是手写区分历史的见证，不是学出来的 D-M2。状态边界的简单字段拒绝不构成真实代码/OS 沙箱的安全证明。一些 native/O case 是设计反例断言，不是 production 单元测试；它们不能声称已修复当前 adapter 差异。

### 本轮对旧成本解读的修正

原始 script/JSON 不改写。C-H 只是 query 的自动附带信息配置，旧表让它与 query 有不同基础信息，并假定 C-L 原生导航各产生额外 policy 调用。Agent 完全可以用固定程序浏览而不逐步调用神经模型。因此该表只能说明这些 fixture/执行假设的差异，不能证明独立协议优劣或支持 C-H 默认推荐；此前推荐撤回。新比较方法见[详细候选第 11 节](../design/BASELINE_PROTOCOL_OPTIONS.zh-CN.md)。

本轮另核对 535b39f4 的 VisibleEntityFacts、BuildCard、NativeTextMenuInformation、ReadService 和 SnapshotBuilder：原生 inspect 文案与 HUD 动态详情来源不同；BuildCard 尚不提供同样的完整 tooltip 关系；Read catalog 只有四 kind，surface_card 不是全场景详情接口；Snapshot signature 未覆盖 materialized Read payload。新通用 Inspect 和一致资料包均为后续设计要求，不能从现有 Snapshot 绑定推导已经实现。

## 5. 未成功执行的可选内核检查

尝试从已有 Python 3.11.15 本地环境运行 test_light_action_m2.py 中 6 个选定 CPU case：候选排列/只读、完整目录预检、writer 输入、reset、在线与前缀重放。预检 import torch 即 ModuleNotFoundError，所以 **0 个此项测试实际执行**。没有安装 ML 依赖，没有下载权重，没有回退到其他源码来制造通过。补齐合格测试环境属于 E3 前置，历史 CI 只保留其原范围。

本轮没有 D-M2 forward/backward 延迟、BPE tokens、真实 GPU/云费用、网络延迟、实际记录开销或 native transition runtime 测量。合成报告不能代替这些值。旧优先推荐已撤回；控制流/字节/源码依据保留各自范围；生产预算和运行承诺仍需后续实测。

## 6. 交付边界

P0–P5 文档与有界设计探针可进入 G1 审查。接受设计不等于将现有 Host 或模型标 qualified。E/V 所需 exact build、运行、Human、数据资格和云生命周期均保留。所有旧记录、模型、用途账与候选身份未改写。
