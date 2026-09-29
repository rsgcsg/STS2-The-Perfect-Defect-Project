# 已确认交互 → D-Simple 内部记忆

日期：2026-09-30。源码起点 `2dbccda18b1c0d4f025b1b05a664eb1d96cae1c2`。
本文固定本包实现和回归目标；完成状态以精确候选回执为准。Human row2顺序基础来自已合并PR116。
目标是补齐用户已要求的D-Simple循环：当前页＋旧内部记忆＋此前实际发生的交互 → 新记忆；不是把候选选择当效果，不把人工历史文字拼进S。

## Runtime owner 的最小新合同

保留现有port1/2逐字语义；新增显式 `decision-only-ndjson-3` / `policy-port-3`，只允许已有text-menu profile与stateful model。现有manifest结构不新增必填项，adapter.protocol选择该合同。不能给旧模型/配置自动升级。

请求复用 StatefulPolicyDecisionInput 全部字段，增加必填nullable `previous_interaction`。非null结构严格为：
- decision_id：前一已核验Decision
- snapshot_id：前一被评分页面
- candidate_digest：前一完整候选顺序摘要
- action_id：同份原目录里被实际提交的唯一动作
- request_id：Connector提交请求
- effect_domain：text_menu或native_input
- result_kind：menu_applied或native_input_delivered，与effect_domain一一对应

不含奖励、效果、成功后局面推断、原生隐藏参数、完整旧页面或上一动作的模型自选索引。上述IDs只是绑定验证，不进入模型文本。完成响应复用原continuity/snapshot/sequence字段，并新增 `previous_interaction_request_id` nullable echo，防止adapter忽略新历史却返回旧完成合同。

port3在构造/启动时要求实际配置Evidence writer；没有writer直接不准入，不能把appendEvidence无writer时的true当持久化成功。port1/2原语义不变。Runtime只在Connector结果绑定校验通过、相应menu_navigation/text_native_delivery由真实writer append成功、结果捕获epoch仍等于当前epoch且无handoff/taint时产生槽。Human/Stop/budget等恢复入口先同步清槽并旋转port3 continuity，再等待release/append；已失效epoch的晚返回或晚写入成功不得复活槽。native交付不等于Commit/效果/因果S′。

一次结果最多供给一次新的观察写入。请求构造/重绘不消费；真正offer后视为可能已消费，失败/timeout/cancel即旋转整个新profile continuity，清槽，不重发旧history。即便模型调用返回成功但用户取消后尚未submit，也不产生新history。Shadow/弃权/not_applied/unknown/关联错误均不产生新history。

port3在以下已有owner边界同步旋转记忆和history：Human/模式切换、One-Step完成、budget handoff、Stop、failClosed/taint、游戏/实例身份改变、丢失观察连续性。port2保持原语义。晚响应、排队旧epoch和在途native submit分类保持当前规则。不以更快响应覆盖unknown或重复动作。

## STPD owner 与迁移

新增明确history projection/profile身份贯通训练输入、模型manifest/portable package/adapter/Workbench recipe。现有六个engine实现hash文件不变；底层 previous_actual_action tensor 已有轻量动作编码入口，feedback仍None。原有observation-only包仍用port2，新history包才用port3。

共享纯函数从原snapshot完整菜单按action_id取得动作语义，冻结token IDs而非旧embedding；按结果basis/domain做明确轻量表示。STPD adapter/scorer缓存最多一份已评分菜单及token IDs，Runtime只持有模型中立结果绑定元数据。绑定不匹配拒绝，不能按当前菜单位置/同名动作猜测。decision_id在Runtime收到模型响应后才生成，adapter不能声称与先前模型请求中不存在的decision_id独立核对；它作为Runtime来源的关联ID，仅校验格式并随结果记录。adapter独立核对snapshot_id、candidate_digest、action_id、effect_domain及一次性消费。缓存和模型memory在同一成功边界提交；同页携带非空history明确拒绝，无history的同页重读保持缓存幂等。

离线只消费当前观察前已可用、未消费的交互：Agent必须使用当前input保存的observation_context精确引用此前持久化outcome，按真实continuity_token分段；缺少该元数据的旧档案不支持新history profile。Managed严格已验证顺序；Human row2使用capture C、completed-append W、source physical S，basis=last_known_human_input_witness；row1为unknown/null。Human不是全局完整输入流。C/W只解决录制可用顺序，不把每次独立Witness菜单sequence=1改成在线持续会话序号。

Human单槽的明确规则：同段同运行身份内，选择C_i < C_current且S_i <= W_current的首次可用accepted witness中最大已完成S，称最后完成的已知输入见证；本次水位覆盖的其余旧候选同时退休，不能在后续观察逐条冒充新交互。无法定位、跨段或身份不符保持null并保守断开连续段。此定义不声称全局立即上一动作。单个已知见证只能第一次可用时进入记忆一次；后续被动新观察没有新交互则None。当前label永不进入当前步记忆。旧记录原字节不变、旧训练结果不回写、旧权重不重新贴标签。

## 必须验证

真实Runtime+模拟Connector held情形、menu/native结果、失败append/no evidence、取消/unknown/Shadow、OneStep/Human/budget/game变化、晚响应、同页重读、一次性消费、三个版本互拒/兼容；真实子进程NDJSON/HTTP及临时安装smoke。

STPD新profile：Human嵌套C/W/S排除未来、原row1为null；菜单重排不串绑；Agent晚outcome不能泄漏；三步合成在线/离线 tokens/memory/scores一致；Reset/K1/K8相同history准入；checkpoint/export/recipe旧身份保留。真实Human stream的局部snapshot sequence不能用来冒充持续Connector session。

六个engine文件原字节仅保留旧权重engine身份，不代表adapter源码改变后旧code_sha256仍匹配；新adapter安装/绑定必须显式生成并照常严格核验。旧port2 exact-key/completion/continuity/同页缓存不改义，Human事件与在线缓存写入次数差异不静默抹平。

Runtime与STPD分别由单owner实现，共用上述合同；Evidence核验新版adapter身份、取消事件与observation_context的精确关联，不决定模型记忆或训练准入。最终以真实NDJSON输入、导出包与已验证事件进行合成端到端检查。源码、测试、临时安装、真实游戏和模型质量各自记录，本文不代替这些证据。

## 用户与模型看到什么

当前页S保持当前原生逻辑页面的公开内容，不追加历史页面或人工总结。
完整A(S)不筛除候选；模型只在自己的内部表示中使用过去。

```text
当前页面tokens + 旧memory + 上次可用交互的轻量tokens
  → 现有可训练M2更新 → 本次memory
本次memory + 各动作独立轻量编码
  → D-Simple候选更新/评分 → 一个当前候选索引
Connector实际应用/交付 + Evidence记录
  → 下一次可用交互绑定（不是预期效果）
```

上一交互的tokens来自当时完整目录中的准确动作，模型相关编码由STPD拥有。
运行ID、快照ID、请求ID、候选摘要仅参与核验，不作为页面文字或训练捷径。
没有已知反馈就不提供反馈。模型内部记忆可训练，平台不为它编写记忆内容。
每步页面只读取一次，候选独立评分的总计算随候选数线性增长；本包不更换模型图或增加候选间全互注意力。
K1/K8与独立Reset对照共享同样输入资格，后续真实实验需分别记录数据/配方/预算，不能借此变更历史结果。


## 提交前恢复的确定语义

Runtime 在写完 `text_menu_dispatch_attempt` 后、调用 Connector 前，再核对
恢复 epoch 和取消状态。Human、Stop 或 deadline 已介入时，写入
`text_menu_dispatch_cancelled`，载荷严格为
`{decision_id, reason: "recovery_before_submit"}`，本次不提交。
该记录是 Runtime 已取消准备的事实，不伪装成 Connector Receipt 或
`not_delivered`。准备次数仍计入 attempt 预算，不表示游戏执行次数。

Evidence 把取消与已有 attempt 一对一关联，不允许同时出现 Connector 结果、
重复取消或因果 successor。取消记录写入失败仍按证据不完整处理；已经发给
Connector 的请求沿用 delivered/not_delivered/unknown 和原有安全规则。
STPD 保留所选决策标签及记录来源，但取消不是实际交互，不能注入下一步记忆。
该事件要求 Evidence rc.23；新 adapter port 3 要求兼容 Runtime，已有生产安装
和旧模型不会因为候选源码存在而自动升级。


## 在线与离线的同一边界

port 3 的 `text_decision_input.observation_context` 必须保存已验证 completion
实际回显的 `continuity_token` 与可空 `previous_interaction_request_id`。
这两项属于运行记录元数据，不是模型页面文字。Evidence 按 protocol 选择严格
形状：旧 port 1/2 不增加字段；port 3 缺字段拒绝。历史引用只允许此前已确认、
同 continuity、更新且不同页面、未消费的结果；新 token 首步为空，退休 token
不能重新出现。STPD 按实际 token 分段，按引用选择历史，不靠最后一条日志猜测。

新 Agent-history 离线投影不支持重复同一 snapshot 的 episode，会明确拒绝；
在线相同页面的无历史重复请求仍幂等返回缓存、不重复更新记忆。现有训练 step
不表达这种缓存读取，不能默默丢标签或多写一次记忆假装等价。Human 独立见证帧
仍按 capture/完成水位语义，不套用连续 Connector snapshot 序号规则。
普通 dev evaluation 尚不支持新 history profile；本包没有把它标为可用。下一项在 STPD evaluation owner 内沿用同一投影与 train/dev 隔离补齐，旧 K8 指标不转移。
