# P1 训练前支持范围书 v0.3（执行草稿，2026-10-18）

用户第15轮冲刺指令已授权实施，**本稿不是冻结批准**。范围扩到第一至第三幕，冻结只在训练前做一次；最终冻结 PR 仍由用户批准。硬截止 **2026-10-20 16:30 Brisbane**，到点交 PR 或剩余清单，不降低门槛、不顺延。v0.2 中第一幕限定、中文语言和旧预算不再作为本轮范围；历史证据继续保留原身份。

历史查过：复用既有 Architecture/Components/ADR0016、P1 合同、K0 采集历史、K1 资产账本和 K2 地图；旧录制、旧批次和旧资格保留原范围，不转换为当前候选资格。

## 冻结内容与当前身份

最终清单需要填写 artifact SHA、MVID、source/patch/profile/converter/core 与原始批次路径。下列是当前实际基线和在途候选，不是最终组合，也不把源码、构建、已安装和实际执行资格互相替代。

| 层 | 本次绑定内容 | 当前已核身份/状态 |
|---|---|---|
| 游戏 | STS2 实际 DLL、模式、角色/难度、语言 | sts2.dll SHA256 `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`；标准单人 Defect/A0；正式新记录英文 |
| 协议 | P1 r1 观察/目录/承诺/消费、ACK、Stop/unknown、集合与顺序语义及例外 | 当前 r1.3；#202 SDK/比较器绑定见下方组件身份。typed tooltip 纳入原全局双射；仅唯一 typed shop catalog 固定前缀后的完整 card-detail 尾段无序，文本/价格/重复数/owner/未知 Read 顺序和 500000 工作上限保留。usable_potions 仍有序；最终 init core 身份须用实际最终候选记录，旧 core 不冒充新候选 |
| Reference | DLL、MVID、完整编译来源与回滚档 | 当前已安装新A1 SHA `0db06ad7bfc989eb8bdcef0e80639c27760d7876d633c660097328033c386199`，MVID `6daa8347-948c-4614-8e5a-0d57fb28fab5`，编译Connector source `91b478f283a36230c560a9c76218b8be8ddfb40c`，clean workspace `67adeccb`；新身份Source4预检待完成。旧888及回滚档 `a1-integration/apps/game-mod/.local/deployments/2026-10-18T07-19-27.577Z` 已核验，旧实跑不转贴新Mod |
| Managed | native 产物/MVID/patch 与 Host 投影 | #202 A5/C 候选 SHA `1482de5d5c8aeee9e3ead971f54f7ae0c8a3ded194c70c66c80d043ce7fa0735`，MVID `3ffecdec-7008-4df3-bda3-948164a3ba84`，patch `89ddc776e969175eb3b18c49d3d862aa078ace6d8ef2305f8ab3270e4a05e374`；组合源码 `0affa651`。S05实际存读与后续floor2战斗通过；不是整局严格资格。A3诊断及旧候选保留 |
| Connector 全组件/SDK | 当前发布元数据；最终候选重新取确切身份 | `1.3.0-rc.25`；source `e1d787667a265119f41ab717af10f1b61b2ce231`；source SHA256 `71337ef058d6b87f0f377d77c8e6eb1c580e7de072b7d7a2cb44c59a80aa912e`（#202 workspace `0affa651`；源码身份，非 installed/live 资格） |
| Host | 当前发布元数据；最终候选重新取确切身份 | `1.1.0-rc.31`；source `d6ebd47a281f15b2dbf002f013aae763f51019a6`；source SHA256 `69e1b45c9fb66e29ba733c22a31a59c243ae9c9511dffa28fa27447622b66220`（#202 workspace `0affa651`；源码身份，非 installed/live 资格） |
| Runtime / Agent 端口 | 当前发布元数据；最终候选重新取确切身份 | `0.1.0-rc.37`；source `e1d787667a265119f41ab717af10f1b61b2ce231`；source SHA256 `6c6681011f51e8bd943d1bf472e77691ba6360be50d3d08ec023fbe59f03b2d6`（#202 workspace `0affa651`；源码身份，非 installed/live 资格） |
| 采集器 | 当前发布元数据；最终候选重新取确切身份 | `0.3.0-rc.19`；source `c60c3691fe45590a12eb10ff99a130b2903c6623`；source SHA256 `d8f36b2504235413256992f5d0f4e0df0ab1d0aae973de47d530ce9222cc5041`（#202 workspace `0affa651`；源码身份，非 installed/live 资格） |
| 离线证据工具 | 当前发布元数据；最终候选重新取确切身份 | `0.1.0-rc.31`；source `c60c3691fe45590a12eb10ff99a130b2903c6623`；source SHA256 `6418df7cf4f65460b2f619506d379c21c670e8dfaa780dcece757b4c82f6ebb0`（#202 workspace `0affa651`；源码身份，非 installed/live 资格） |
| Workbench | 当前发布元数据；最终候选重新取确切身份 | `0.1.0-rc.3`；source `01debc74ba24c61c5cbfbe9023ac8d4b3a7e9bc3`；source SHA256 `2accc441c50968fc2f8ca0bafba1fb7d9e71eaa47dc24fc60d5a4c1b768f80b4`（#202 workspace `0affa651`；源码身份，非 installed/live 资格） |
| 英文模板 | Reference eng / Managed en；payload 与运行实际语言 | `defect-a0-eng-windowed`，显式eng、fullscreen=false/target_display=0；888已实际可见并采集通过，0db预检待完成，profile原件保留确切payload，不覆盖旧中文模板 |
| 人工选择器 | 显示、输入来源和记录 provenance | 简洁全部动作首页显示v4，#199 merge `fdb465bb1057180bd5b877d174adeca133d08819`；用户同时看真实游戏画面，不能声称只看P1；Agent观察不变 |
| 环境录制 | 原件格式、Close/打包、转换器及 SDK 映射 | 通用环境来源不限 Human/鼠标；只要按来源合同能转换为所需轨迹即可。本轮 B 仍需用户已定的英文 native_input Source4 整局 complete=true；新 tuple 尚无合格整局。直接 AgentRun 独立于 Annotator/Source4；Agent UI 预检不计 Human |
| 校验/比较 | 离线 verifier、完整公开图比较、实例全局双射 | 当前 r1.3 比较器，不提高比较上限、不删字段、不按两端私有ID对齐；最终工具 source/digest 与原始回执一并固定 |
| 文档 | 协议§16–18、CURRENT、本范围书、例外与覆盖表 | 冻结前更新；本稿先列真实缺口 |

完整当前 source/digest 原件：组件身份 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-sprint-root-20261018/pr200-current-identity.json`)、整合编译闭包 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-sprint-root-20261018/pr200-integrated-code-closure.json`)。上述链接均指向本机保留原件。

## 计数和验收

- 正式计数：同一冻结候选、英文、至少3整局 Human，环境录制与选择器分别计数；每局0 unknown、离线通过、Managed严格重放到底。旧中文 Human01/02、Agent预检、诊断 locator、not_started跳过、不同候选及恢复片段不能拼成3局。
- 环境录制必须有整局 `complete=true`，短段通过不替代整局；三幕机制可以由合格批次合起来覆盖，第三幕必须实际打到。胜利终局是必验缺口，若无法取得交用户定，不能用 loss/Stop替代。
- 冻结候选上 Managed40 + Reference20、Workbench启动→决策→Stop→记录、独立Agent包 R20两端、project:closeout 均需实际完成。历史40/20只有历史资格。
- 按同种子与公开选择前缀重放；每个真正决策点比较完整公开观察、目录、承诺与选择；只有协议已声明的集合可无序。`different`、`incomparable`、unknown、技术停止与自然终局分别记录。
- 原始记录不改。旧 Human01 occurrence41 的同一卡公开描述仅有4，新生产修复后应为6；不能凭另一张Strike或源码猜值改成6。旧局正式严格到终局目标如何处理，已交用户决定；其余根因修复继续。

## 三幕机制覆盖表

状态“Reference已见/两端执行”只描述下列**旧候选**事实；“最终待验”均没有新冻结批次资格。“两端严格通过”必须补逐步完整相等的原件，现不以C11/C09诊断走到终局代替。

旧证据：H1=中文Human01，317输入、Act1 Boss后Act2 floor22 loss；H2=中文Human02，434输入、Act1 Boss后Act2 floor28 loss。M1=C11、M2=C09均为诊断新执行，分别67/293、47/432公开图相等，终局图也不同。原件索引与逐occ归因 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/outputs/P1-round12-Managed-diagnostic-2026-10-18-details.md`)。本表按幕核对了原轨迹的选择位置，完整索引见 历史动作索引 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-sprint-root-20261018/historical-act-mechanism-index.json`)。索引说明原始选择/catalog与场景；只有对应Receipt/Result和后继才能证明效果完成，旧not_started不计成功。不从“打到某幕”推断所有该幕机制都遇到。

| 幕 | 必验机制 | 已有记录及真实状态 | 新冻结验收状态 | 到达办法 |
|---|---|---|---|---|
| 第1幕 | 普通/精英战：出牌、目标、结束回合、公开状态与牌堆 | H1/H2 Act1普通战实录；精英种类仍需逐场核，不从combat_turn推断 | 最终待验 | 正常推进并选精英路线；每场核完整观察/选择 |
| 第1幕 | 战斗药水使用、丢弃及槽/目标顺序 | H1 occ119/120是可用药水列表顺序反例；实际potion.use选项见78/131/158，需分场核Result | 最终待验 | 保留药水至有合法目标的战斗；验证后继与库存 |
| 第1幕 | 地图、跨房间、非地图公开map | H1/H2通过Act1并在Act2终局；H2 occ321地图→新事件 | 最终待验 | 同种子公开前缀；记录房间变更和owner |
| 第1幕 | 金币/遗物/药水奖励、满槽与离开 | 历史普通奖励已见；满槽/linked/mandatory分支未逐项证明 | 最终待验 | 保留满槽；按实际奖励分支记录，未出现不填通过 |
| 第1幕 | 卡奖领取、跳过/替代、生成卡详情 | H1 Act1 occ16/29等领取卡，occ159 REBOOT生成选择；拒绝/替代逐项待核 | 最终待验 | 战斗后自然奖励；保留完整详情与后继牌组 |
| 第1幕 | 营火休息、离开 | H1 Act1 occ100–102旧rest修复链；H2 occ136/137等rest.choose；休息具体意图核原件 | 最终待验 | 路线选营火，分别记录休息和可继续页 |
| 第1幕 | 升级选择、预览、确认 | H1 Act1 occ101/116/142/195、H2 occ166选择upgrade.choose；预览/后继待最终验收 | 最终待验 | 进入升级营火并选合法卡，核Preview/Result/后继 |
| 第1幕 | 附魔选择、预览与结果 | H1 Act1 occ75、H2 occ54选择enchant.choose；旧公开summary差异在修 | 最终待验 | 遇附魔机会保留原始前缀；不能按名字推支持 |
| 第1幕 | 商店购买、移除等服务、关闭再开及离开 | H1 Act1 occ104–108、145–147商店链，含原RNG/投影反例；服务效果待逐项核 | 最终待验 | 有金币的商店路线；核售罄/价格/服务后继 |
| 第1幕 | 该幕事件：选项、继续、owner与嵌套 | H1 Act1 occ49/50、74/76；H2 occ77事件及78/79八选二；不是Act2证据 | 最终待验 | 各幕真人有意走事件；每类出现的选择都校验 |
| 第1幕 | 宝箱打开、取遗物、跳过 | H1 Act1 occ110–113；H2 occ132–134宝箱动作，原H1跳过not_started另记 | 最终待验 | 路线选宝箱；不在开箱前揭示未来遗物 |
| 第1幕 | 单选、多选、生成卡、选堆/包选择 | H2 Act1 occ77事件八选二、89弃牌选择；H1 occ159生成选择，其他类型待核 | 最终待验 | 复用已发生公开前缀，其他类型仅在真实出现后登记 |
| 第1幕 | 移除/变换及后继牌组 | 旧事件/商店有入口线索；未逐幕证明全部效果 | 最终待验 | 使用自然服务/事件；核实体消失或新实体及后继 |
| 第1幕 | Boss战斗、掉落与Boss遗物选择 | H1/H2确实经过Act1 Boss；Act2/3 Boss未有合格证明 | 最终待验 | 真人目标第三幕，保留Boss战及奖励完整链 |
| 第2幕 | 普通/精英战：出牌、目标、结束回合、公开状态与牌堆 | H1 Act2 occ243起/H2 occ255起普通战实录；精英种类待核 | 最终待验 | 正常推进并选精英路线；每场核完整观察/选择 |
| 第2幕 | 战斗药水使用、丢弃及槽/目标顺序 | H1 Act2 occ310选择potion.use；场景与Result须原件验收；H2 occ323事件喝药不代替战斗用药 | 最终待验 | 保留药水至有合法目标的战斗；验证后继与库存 |
| 第2幕 | 地图、跨房间、非地图公开map | H1/H2通过Act1并在Act2终局；H2 occ321地图→新事件 | 最终待验 | 同种子公开前缀；记录房间变更和owner |
| 第2幕 | 金币/遗物/药水奖励、满槽与离开 | 历史普通奖励已见；满槽/linked/mandatory分支未逐项证明 | 最终待验 | 保留满槽；按实际奖励分支记录，未出现不填通过 |
| 第2幕 | 卡奖领取、跳过/替代、生成卡详情 | H1 Act2 occ265、H2 occ279特殊奖励；H2 occ281/315/390领取卡、357拒绝组 | 最终待验 | 战斗后自然奖励；保留完整详情与后继牌组 |
| 第2幕 | 营火休息、离开 | 尚无确认的Act2营火操作链；不从Act1迁移 | 最终待验 | 路线选营火，分别记录休息和可继续页 |
| 第2幕 | 升级选择、预览、确认 | H1 Act2 occ237–240选择upgrade.choose；不迁移Act1证据 | 最终待验 | 进入升级营火并选合法卡，核Preview/Result/后继 |
| 第2幕 | 附魔选择、预览与结果 | H2 Act2 occ252选择enchant.choose；最终完整比较待验 | 最终待验 | 遇附魔机会保留原始前缀；不能按名字推支持 |
| 第2幕 | 商店购买、移除等服务、关闭再开及离开 | H2 Act2 occ287仅shop.leave，不能当购买/移除完整链 | 最终待验 | 有金币的商店路线；核售罄/价格/服务后继 |
| 第2幕 | 该幕事件：选项、继续、owner与嵌套 | H2 Act2 occ320–323旧event→map→新event反例，原322 not_started；888定点短测越过 | 最终待验 | 各幕真人有意走事件；每类出现的选择都校验 |
| 第2幕 | 宝箱打开、取遗物、跳过 | H2 Act2 occ360–362 treasure.open/activate，需核各自Result与奖品后继 | 最终待验 | 路线选宝箱；不在开箱前揭示未来遗物 |
| 第2幕 | 单选、多选、生成卡、选堆/包选择 | H1 Act2 occ248/289、H2 occ301/346/400 selector.choose；具体类型按原scene逐项核 | 最终待验 | 复用已发生公开前缀，其他类型仅在真实出现后登记 |
| 第2幕 | 移除/变换及后继牌组 | 旧事件/商店有入口线索；未逐幕证明全部效果 | 最终待验 | 使用自然服务/事件；核实体消失或新实体及后继 |
| 第2幕 | Boss战斗、掉落与Boss遗物选择 | 未有Act2 Boss完成记录；两局均在floor22/28自然loss | 最终待验 | 真人目标第三幕，保留Boss战及奖励完整链 |
| 第3幕 | 普通/精英战：出牌、目标、结束回合、公开状态与牌堆 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 正常推进并选精英路线；每场核完整观察/选择 |
| 第3幕 | 战斗药水使用、丢弃及槽/目标顺序 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 保留药水至有合法目标的战斗；验证后继与库存 |
| 第3幕 | 地图、跨房间、非地图公开map | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 同种子公开前缀；记录房间变更和owner |
| 第3幕 | 金币/遗物/药水奖励、满槽与离开 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 保留满槽；按实际奖励分支记录，未出现不填通过 |
| 第3幕 | 卡奖领取、跳过/替代、生成卡详情 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 战斗后自然奖励；保留完整详情与后继牌组 |
| 第3幕 | 营火休息、离开 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 路线选营火，分别记录休息和可继续页 |
| 第3幕 | 升级选择、预览、确认 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 进入升级营火并选合法卡，核Preview/Result/后继 |
| 第3幕 | 附魔选择、预览与结果 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 遇附魔机会保留原始前缀；不能按名字推支持 |
| 第3幕 | 商店购买、移除等服务、关闭再开及离开 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 有金币的商店路线；核售罄/价格/服务后继 |
| 第3幕 | 该幕事件：选项、继续、owner与嵌套 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 各幕真人有意走事件；每类出现的选择都校验 |
| 第3幕 | 宝箱打开、取遗物、跳过 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 路线选宝箱；不在开箱前揭示未来遗物 |
| 第3幕 | 单选、多选、生成卡、选堆/包选择 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 复用已发生公开前缀，其他类型仅在真实出现后登记 |
| 第3幕 | 移除/变换及后继牌组 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 使用自然服务/事件；核实体消失或新实体及后继 |
| 第3幕 | Boss战斗、掉落与Boss遗物选择 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 真人目标第三幕，保留Boss战及奖励完整链 |
| 1→2 | 跨幕选择、地图与状态连续性 | H1/H2实际进入Act2，旧中文Reference已见；Managed仅诊断执行 | 最终待验 | 完成Act1 Boss奖励后跨幕，核进入新幕前后 |
| 2→3 | 跨幕与Act3初始化 | 未有实际记录 | 最终待验 | 真人完成Act2 Boss并进入Act3 |
| 终局 | 自然loss、完整终局公开图与释放 | H1 Act2/22、H2 Act2/28均真实loss；M1/M2到loss但终局图不同 | 最终待验 | 新英文整局自然终局，核胜负/HP/Receipt/Stop/释放 |
| 终局 | 胜利 | 未有合格记录 | 必验缺口 | 真人尽量获胜；取得困难时由用户决定，不替换标准 |

关联/强制奖励等只有特殊模式证据的机制，先列实际标准局可达实例；不凭fixture宣称标准模式覆盖，也不因暂未遇到而宣称不存在。多人、未知身份与隐藏未来仍为当前不支持范围。

## 执行节奏、缺口处理与存档

B 先完成“新Mod＋采集器＋英文”原生预检，再给一页说明供用户录约15分钟：至少结束回合、选卡、事件或商店。每个根因先用30分钟定位并分“转换问题 / 采集问题”，再用60分钟修复和重验；时限到仍未闭合则报告具体证据与剩余工作，不静默兜底或降低complete。普通修复继续自主处理，真实协议取舍/例外/用户游戏操作才询问用户。unknown请求永不重发；修复后新候选从原种子头开始、独立新身份。

地图房间存档续玩作为C并行最小功能：Managed 的 S05 已实际验证：HP62/gold111/deck11/floor1 保存、正常关闭父进程、全新进程 full-P1 恢复相等、随后实际进入floor2战斗；17 delivered、0 unknown、0 retry，两个进程正常退出。仅此受支持完成地图边界，不是战斗任意checkpoint。S03 incomplete/S04 fail原件保留。Reference现有原生继续入口不能单凭按钮名证明当前地图状态已存。恢复记父局/存档SHA/候选/新continuity，片段与原局同数据分组，不算独立新局。不以此阻挡A/B。

检查点：10-18 22:00预检及说明；10-19 12:00短段complete与A根因表；10-19晚20:00第一局英文转换/重放；10-20早09:00稳定候选及最终批次/产品验收；10-20 16:30冻结PR或剩余清单。以上均为Brisbane时间。未来检查点已设当前chat提醒，平时只通知实质变化。

## Reference 速度与呈现补充（用户10-18新增要求）

隔离Reference默认instant，显式fast、normal仍支持；headless/native-ui是独立呈现维度。PR201全部原提交已整入已合并的PR200（develop `60dc0a89ad6ae19b26e0e1bbf71517471f409069`，与67adeccb同树）（六格实跑Host source/head仍为a7db15529d7fe266154553dc428540bb35527d91），当前留装888下六格短实跑均通过requested/effective和真实round1→2，12/12delivered、0unknown；最终冻结tuple仍须自己的实际记录。shared/attached个人游戏不覆盖偏好，低层诊断/模板启动不伪装成准备完成的Reference episode。当前与历史启动方式、证据边界 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/P1-Reference-launch-history-2026-10-18.md`)。

## 当前新增候选与实跑回执（持续更新）

A第二组源码候选 `f11ed1abb33e3b884aefd3b4bc4ffd7dd8ced3e0`；Managed native SHA `778a557902dbb9012797ff459db7212474965466963c8e20dc6decbafbefbe44`，MVID `dd3ed293-23ff-4ff3-90de-8c42735464aa`，patch `403e33061bdaf67e8751274b2d5ebaa5439288f941d3cc199b23ca4232a8925d`。Human01新诊断293个选择全部completed，158完整相等/135仅定位，23原not_started另列，真实loss、终局图不同、0unknown；这些仍不计最终英文严格整局。原件 A01 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-sprint-A-20261018/diagnostics/human01/attempt-A01.json`)。

环境录制 Source4 capture profile SHA `acde561770d11aeaff8e22bf082bd0869bf440015f7ea525a9521dc4d81b96a4`；当前转换器source文件SHA256 `3d176dbad60cf9f397e08f0afc403071485e5e404ccd493d3b81434dba96b737`；B05完整原件保留实际源与SDK/core身份，完整自然终局转换仍待真实记录。

## 第16轮 K 收敛门（仍未冻结）

- [磁盘表](DISK.md)：181目录；本轮不删、不移，K3按用户逐表批准。
- [K0采集调查](RECORDING_HISTORY.md)：复用成熟采集/Close/pack；Source4两接缝估6–16人时，昂贵旧数据转换暂缓。
- [K1账本](ASSETS.md)：2063行；6 dirty、46潜在未覆盖refs保护，目录/PR去向仍是建议，未物理归档或关闭。
- [K2单入口](README.md)：沿原框架串目标/链路/合同/功能/组件/格式/状态，K4新agent验收与K3批准执行仍待完成；不能以盘点表存在宣布K完成。

当前 develop 是60dc0a89；#202候选0affa651已通过source CI39108923063及独立复核，Ready但尚未合并；仓库merge自动删分支与当前禁删分支要求的权限接缝由root处理。最终freeze PR仍需用户批准并等K完成。最终候选英文≥3Human整局/实际第三幕/胜利行、Source4整局、完整终局严格比较、Managed40/Reference20、Workbench/R20和最终文档仍未齐备。旧Human01牌文本与药水顺序有不可事实保持派生的原始问题，另有终局UI接缝，不能静默放宽严格门槛。

最新组合身份原件：0affa651组件身份 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-sprint-C-20261018/map-continue-final-identities.json`)；S05独立full-public比较 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-sprint-root-20261018/S05-independent-full-public-compare.json`)。新增A/B普通修复准备不覆盖以上冻结候选或旧实际记录。
