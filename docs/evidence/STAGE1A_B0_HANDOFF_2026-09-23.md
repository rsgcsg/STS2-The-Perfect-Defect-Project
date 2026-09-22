# Stage1a B0：本地主管接手基线

日期：2026-09-23。本文记录源码交接，不代表生产安装、原生/Human、完整1a或科学验收。
用户已把日常调度、实施与独立审查协调转交本地主管；网页不再是必经中转。
本文件使用已存在的 source anchor，最终文档合并身份 H_B0/T_B0 由合并后的封存评论记录，
不在正文写自引用 SHA。

## 源码与接受范围

- 仓库：`rsgcsg/STS2-The-Perfect-Defect-Project`。
- B0 source anchor：`5b148f22f02d9dbebfb1c50dd1e9501d9aef7bd9`，
  tree `be30e2f267b2ee1b0427f6edaacd142c120137ff`。
- 该 anchor 的正常 merge parents：`182a4a4bdb3b7da626e7259b87a3ab022d54bf92`
  （#35归并后的Stage1a）和 `0051aaf5498ee13f5ce61430b653eb96c0589c27`
  （已接受#27/#37的develop）。tree与预先计算、独立核对的确定性并集相同。
- [PR #31](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/31)
  对上述develop为68提交、91文件、+8011/-168；包含旧Stage1a累计工作，不能只称预算修复。
- #31 已正常合入 develop：`3e983857a24fabece0173d133665db652b047f73`；
  parents 为 `0051aaf5498ee13f5ce61430b653eb96c0589c27` 和上述 source anchor，tree保持相同。
  [最终组合受限接受](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/31#issuecomment-5783844864)
  仅覆盖源码/工程范围。
- 新01的01.0设计来源：`d478e70255f9e68b56df3741a781fc03a3ceba14`、
  [ADR-0015](../adr/0015-native-logical-interaction.md)。它是限定范围内接受的上层方向，
  不是已实现的新wire、新数据或全场景规范。

| 输入 | 精确接受与集成 | 仍不包含 |
|---|---|---|
| 原Stage1a + #29/#32/#34 | `101a9cc0f89f9fdcd9d8320279a48b2595e2fccc`；[独立审查终态](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/31#issuecomment-5781320990)、[接受范围](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/31#issuecomment-5780765508)、full run35752111380 | 新模式、全场景运行、四模型质量 |
| #36 新01 | head `d478e70255f9e68b56df3741a781fc03a3ceba14`；[受限接受/归并](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/36#issuecomment-5782872644)；merge `d3d53b543f4b4cb87b2cd76b28603d17251c1fd5` | 所有原生假设、实现与新样本资格 |
| #35 有限预算与恢复 | head `a32151be294e457aeb2981228ced538d27ae289d`；[修复/独立审查与包回执](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/35#issuecomment-5783268583)；merge `182a4a4bdb3b7da626e7259b87a3ab022d54bf92`，tree与head相同 | 当前已安装Runtime升级、原生恢复计时 |
| #27/#37 协作与排程 | #37 head `f1050f848c146c7e0b47b0f597a8f37df4dc58d0`；[精确归并](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/37#issuecomment-5783313019)；develop `0051aaf5498ee13f5ce61430b653eb96c0589c27` | 新训练/生产/预算权限 |

#35修复中的关键区别：旧预算交接不能终结后来明确启动的Auto；进入Human先撤销自主授权；
SDK close吞掉传输错误不等于Host确认释放。确认失败保留held/taint，不能靠二次本地close或TTL
写成功。unknown交付不重试；首个taint原因与后续诊断均保留。16提交/32调用/60秒仍只是
有限试验默认值，可以通过已有明确配置调整，绝非完整游戏时限。

## 检查身份与状态

| 检查 | 真实结果/边界 |
|---|---|
| #35当前head | [35777828163](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/35777828163)，attempt1，full：plan/Linux/Windows/portable success，docs按路由skipped |
| #37当前head | [35775225089](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/35775225089)，attempt1，full success |
| develop #37合并 | [35778177416](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/35778177416)，success；router核验同tree执行回执并做当前identity，不能写成新full |
| B0最终组合 | [35780464198](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/35780464198)，attempt1，head `5b148f22...`；full：plan/Linux/Windows/portable success，docs按路由skipped |
| develop #31合并 | [35782151978](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/35782151978)，head `3e983857...`，success；router实际选reuse，核验原full run35780464198并执行当前检查，不是第二次full |
| 本机组合短检查 | 干净anchor上 `check:identity`、`check:bom`、`git diff --check 0051aaf..HEAD`、`project:closeout`、`check:plan -- --base 0051aaf...` 均exit0；planner full；未重复本机长full |
| #35组件与临时包 | 干净a32151be上115/115测试、typecheck/build、确定性包及隔离CPU installed smoke；无游戏接触。旧失败保留 |

上一组合或单个topic绿色只证明原对象。本次最终组合自己的full与受限接受已完成，且合并tree保持一致，
B0代码起点已归并；本交接文档仍需自己的审查与门禁后封存。普通任务从接手时明确的develop开短topic，
不保留Stage1a为第二develop。

## 组件源码、包与运行环境分开

| 对象 | 本基线中的身份与非声明 |
|---|---|
| Policy Runtime源码候选 | `0.1.0-rc.8`；source `99c75d66f78aab065a644a091fab5781157257e0`；component tree `1e6b57326db2e073e38fab34bad0f13e35eadde6`；source SHA256 `3331204128356c482b31d45857a4826bf9a317c59882b235122b18ac60bd5365` |
| Runtime临时包 | SHA256 `ca32ee51475789e34104dfc40710c188ed1ff13ab8e551bcb43bc30ce95cec0c`；临时开发安装smoke，不是npm/Release已发布或生产安装 |
| 生产消费 | 既有配置仍rc.6；不把分支合并当作客户端下载或加载。当前实例、artifact/manifest/adapter实时attestation未重采，unknown |
| 旧四模型 | B-S v2、B-PF v2、D-Simple-S v1、D-Simple-PF v1；旧token-v1/N/M0工程产物保留原输入、数据、权重和回执身份，不改成新01模型 |
| 旧数据/证据 | 保留原始包、失败记录、用途/Gold与历史S/H/canonical含义；新解释形成明确派生版本，不覆盖旧字节 |
| 新01能力 | 本基线只有接受的上层设计；奖励父事实、逻辑页/能力/菜单、交互记录、研究输入和记忆实施均不得从ADR的Accepted推断完成 |

## 原生依据与下一包边界

主管只读核验目标STS2 DLL SHA256
`9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`，
MVID `57785517-0b16-42b9-8b36-bad6fb28384b`。这是静态版本证据，不是加载/真人运行。
未将DLL、反编译文本、原始轨迹、权重或私有路径写入仓库。

- 普通CardReward的内层暂不领取与外层结束奖励流程是不同原生路径；星系仪能产生多个奖励组。
  已核验版本提供重入的工程依据，不推广到所有Skip或未来版本。
- 单页完整已知选项的逐张多选，与跨组查看后再返回领取是不同问题。旧S-a-S也能表示回路；
  新01的重要变化是当前逻辑页的信息边界、显式信息取得和历史，而不是发明循环记法。
- 大牌堆完整数据与虚拟化holder对应的可执行目标仍有差距；不能扩大观察后就称全部可执行，
  也不把窗口子集冒充新模式完整菜单。
- 因而首个工程切片改为普通卡牌奖励组的父归属/返回结果，再到当前页观察、菜单、交付与记录。
  大牌堆完整列表另作能力包；0–3弃牌多选的具体游戏触发仍需核验。没有改变两类例子的产品语义。

[纠偏后的源码场景矩阵](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/31#issuecomment-5783319535)
区分四模型token端口与旧S1：不能用旧combat-only配置或8192常量概括全部四格；
具体manifest、长度预算、当前训练覆盖/质量仍分别核验。

## 接下来交付什么

1. Native Foundation最小共享事实包，独立源码/测试/精确构建审查后才做获准原生窗口；
   Connector和Annotator共用原生归属，彼此不借权。
2. 首个奖励组完整纵向切片：当前逻辑页S、范围内完整A、一个原生交互、明确Receipt/UI结果和证据；
   访问历史在下游，未访问组不泄漏，能力关闭不冒充原生非法。
3. 固定小B-S工程对照：先M0输入/反馈闭环，再真实事件历史与记忆候选；保留独立训练的reset控制。
   D-Simple与共享主体D2可按问题和预算加入，不以B失败为前提；Z、O及交互效应逐项验证，
   不奖励所有查看或惩罚所有返回。真实数据训练、用途和预算另行具体授权。
4. 游戏内工作台默认入口与后台独立服务：先跑通发现、登录/离线、同一模型注册表、加载、明确接管、
   Human/Stop与状态；采集/数据集/训练/分析/归档按同对象导航逐步接入。
5. 完整一局从安全条件具备时就持续作为评价主线；按首次失败owner定位。失败终局不证明所有场景，
   安全暂停不等于合理策略。配置分发与回退完成后才谈完整1a；约一万条数据和云GPU扩量留1b。

## 工作树、任务与权限交接

本地主管独占组合写入；已冻结的旧A/B/C工作树、运行中的采集/模型工作树及其环境原样保留。
工作树绝对路径和进程信息留在私有本地回执，不进入Git。只读CI观察绑定真实run、head和总时限，
终态退出；会话结束不等于后台任务被杀，不虚构持续监测。

本轮开发副作用包括隔离工作树、锁定开发依赖、合成测试/临时包、普通提交/PR/受控归并；
没有生产Workbench/Mod/Runtime安装或重启，没有游戏/Steam动作、真实训练、付费、发布，
没有改main或四条Workshop栈。GitHub依赖提示需要独立核对实际锁和暴露面，未自动升级依赖。

B0 source anchor以外的B1奖励事实候选与离线UI原型是后续工作，不混入本次接受：前者仍需自身PR门禁
及真实加载验证；后者只做合成模型状态测试，未取得真实浏览器/Mod界面验收。
当前开放事项和排程见[主任务表](../plans/STAGE1A_TASKS.zh-CN.md)，具体权限/等待规则只由
[AI_COLLABORATION](../AI_COLLABORATION.md)与[TESTING](../TESTING.md)拥有。
