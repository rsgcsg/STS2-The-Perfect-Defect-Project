# M2b 分档、保存流水线与轨迹边界（2026-10-27）
本轮 G2 应用保存/表示变更；运行节点的同一账本接 Runtime 封存、原 Host 关闭、有限保存池和存储节点读回。
用户已批准三档及工程自动合 develop；不改 Host 打局/P1/Connector/Reference/比较规则/模型输入，不训练、不实现 RL 采样、不安装日常组合。

| 交付/准确条件 | 实物与限制 |
|---|---|
| 入口与唯一 owner | [Managed 保存契约/SOP](../../python/docs/MANAGED_PRESERVATION.md)；原 evaluation_ledger，默认 formal；新计划冻结档位/池/Store，旧账本和原件不改。 |
| 分档与分数 | formal 保持完整 Runtime/模型父、原件/哈希/本次 R2 typed；diagnostic 正常胜局留本机、负/unknown 全档，trajectory 分片/约1%全档审计/unknown全档。后两档不出分。 |
| 有界流水线 | 默认执行最多4（命令控制）、保存2、最多8个含执行与封片等待的未完原件。原15GiB余量、每未完原件2GiB峰值门未放宽；已关 Host 才交保存。失败只恢复保存。 |
| 轨迹格式 | stpd/p1-trajectory-1，公开观察/完整目录/动作/原交付/后继/战斗边界/身份/原结果；分片最多8局/256MiB/gzip1。真实概率与模型 softmax、价值各自标明。438仍argmax、实际概率1、价值null、rl_ready=false。 |
| 最短真实保存恢复 | 2个新种子、Host52/Runtime52/438；一次保存 copy 故障注入后同 job/同 run/同原件恢复，新增 offer 为0；邻局正常排空，最终2份完整R2。生产者 e23f09af。 |
| 正式档完整新批 | 82669c38 producer；8自然负/0unknown，4路配置及观测峰值4，243.8807秒含准备/尾巴，118.0905正常局/小时。 |
| 轨迹档完整新批 | 同 producer/Host52/Runtime52/原438，独立8种子：7自然负/1unknown；204.6527秒含尾巴，123.1354正常局/小时，分数0。unknown完整R2另有轨迹，未重投。 |
| 保存量/资源 | 正式/轨迹每局远端payload中位11,059,472.5/751,255.125字节（轨迹按分片均摊，父模型/Runtime复用另列）；RSS求和峰值3.35/3.49GiB、最低余量22.83/24.04GiB。不同种子/结局不证明档位的纯因果倍率。 |
| 中断分母 | 先前正式计划8槽：发现游戏进程即停止准入并原Runtime Stop，实际5开=1自然负/2censored/2unknown、3未开；全部已开局关闭/R2。单列保留，不当完整吞吐，不续原未知。 |
| 200局估算 | 按轨迹样本，200次开局尝试约85.3分钟；200个正常局约97.5分钟。只含打局/保存，热节点工作集，小样本外推；不是RL unknown丢弃/替换或训练授权。 |

全部实际新开23槽（2恢复验证+5中断批+8正式+8轨迹），共18自然负/3unknown/2censored，26计划槽中另3未开。
旧8份 M1R2 正式原件到新格式的1283步、旧原件保存A/B均为单独离线边界检查，不并入新局分母。
新/旧保存方法对同一原件得到相同 artifact ID，archive payload 与旧 R2 一致；该A/B远端payload已存在，不称首传倍数。
旧8局分片又由新进程、空scratch、仅R2 reader取回；不是学校节点或训练资格。

准确基线795c5d24；本轮普通合入最新develop73378b15一次，保留M2a共享测试的有效版本断言，执行器游戏文件未改。
Host52/Runtime52/候选包为本卡私有APFS副本；Host52 pin source8667465e、component tree52215d21、asset464eb9aa，
Runtime52 asset c85cd244；旧默认r1.5/438保持。测试运行版本不改标为最终源码或在用安装。
M2a40槽结束/40份R2已先核；本机M2a完整测试仅与中断批重叠，后两完整性能批未采到其它project check。
首次节点Store为对已静止M1R2工作集的只读源APFS clone（0.5004秒），不是R2冷恢复；每批constructor准备已计入墙钟。
本机Runtime临时原件按原保留规则保护；长期远端轨迹档只发训练所需分片和规定的全档，不新增清理系统。

验收：相关回归、非作者审查、真实闭环与本次R2读取分别保留。最终完整源码门在冻结候选CI执行，本机只跑相关增量及真实Managed/R2。首候选前置mypy曾从仓库根运行，未加载Python组件严格配置，漏报5处本卡类型注解缺陷；改用组件入口、补齐注解后按新候选重跑规定CI，原尝试保留。此为本卡代码/自检入口错误，不归环境或测试修复卡。合并头核新身份和可复用的完整执行收据。
准确PR/CI结果及原日志、逐槽SHA、资源采样、所有分片/完整artifact ID归用户指定
claude-handoff-2026-10-13/replies/M2B-REPORT-20261027.md及本任务outputs/evidence、outputs/measurement。
未知原因为原记录 global_post_delivery_capture:global_post_delivery_capture_incomplete；本卡只保全，不越权修Host或接受其为等价例外。
