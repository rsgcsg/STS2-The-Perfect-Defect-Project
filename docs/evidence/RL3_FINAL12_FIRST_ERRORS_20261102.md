# 最终十二槽：定位与失败诊断保全修复

原十二槽均保持未知，没有补局或改成绩。
九槽有既有同因修复覆盖，另三槽的原历史根因尚未证实。
本卡修好的是保存器丢失验证finding，不宣称原运行故障已经消失。

## 原件与结论

Owner：本卡root。基线develop10471c76。原parent1401485及三侧完成0:0；
原消费者1a3e4898、Host58/Runtime59、Input-v2/argmax不变。12份原attempt报告
按原SHA读回，精确failure/original Store ID、末条成功边界、失败调用、Host退出、
cleanup及保存收据逐槽保留在本卡私有CSV/JSON；不把原运行数据提交Git。

| 槽 | 首个可证的错误边界 / owner | 判断 |
|---|---|---|
| start56；v471/21,58；final21,82 | 水晶球capture partial，缺native minigame公开事实；Managed事件提供者 | 5槽同因覆盖：PR370显式r1.9组合，不能静默套给旧r1.5 |
| v471/43,79 | 两槽均带TinyMailbox，休息页再次提供输入时原Host operation仍等待 | 2槽同因覆盖：PR359 Host59原奖励子输入resolver |
| v471/9 | Orobas手动确认、min=0/max=15 selector被报mechanics unqualified | 1槽同因覆盖：PR359；非手动零下限仍不自动放行 |
| v471/20 | game_over处于end_committing，缺原native end commit | 1槽同因覆盖：PR359 Kill/OnEnded等待；缺见证仍unknown |
| v471/50,69 | 对Rocket剩1/5血出Go for the Eyes/Hyperbeam，exact native play未被见证 | 2槽尚未证：与PR359 null-audio修复条件相关，但原件没有异常栈 |
| v471/84 | typed验证失败，而调用层丢掉了具体finding | 原历史原因尚未证；丢失finding的保存链缺口已定位并修复 |

11槽执行失败时仍余约496–840秒游戏预算；第84槽最后Stop尚余168秒。
12槽cleanup接受，Host close/服务退出0；这些收据不证明执行成功。

第84槽精确失败artifact正常Store完整读回8,337文件/205,875,380字节。
在Friday单CPU上，准确Runtime59的正常reader于134.34秒返回失败，记录到
120秒子进程TimeoutExpired；但同份原件、完整package pin及原expected事实，
仅把可信安装目录搬到本机，准确未改Runtime59正常reader **40.03秒通过**。
故不能把某环境的复现超时倒推为原失败首错，也不能把本次读验变成旧槽资格。
这与既有[#365](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/365)
的125秒失败/约40秒复验教训一致。10月24日83949137修复的是历史回放内存和
准确语义结果复用，本卡不另建回放平台。未达标的SDK/Runtime性能候选已撤回，
两个大型转换夹具的准备失败亦保留；本PR不包含这些改动。

## 本次代码与正常使用

链路位置：⑤⑥ Evidence verifier结果→Managed保存→不可变failure Store→peer reader；
变更类别为表示/传输。G1：同一Python应用owner按既有API保存已有事实，原件格式、
P1/InputSpec、控制、游戏规则和资格判据均不变。非作者只读复核已接受最终小diff。

- 同步和pipeline调用在typed拒绝前，使用原Artifacts writer保留status、expected和
  真实VerificationResult.findings，包含code/detail/path；不补造空缺的历史原因。
- pipeline正常failure artifact的facts嵌入这份结果，经过原hash/transfer/peer读回。
  节点attempt目录以后不在时，按stored_failure_id正常读manifest仍可取得finding。
  诊断写盘失败时先保留原typed unknown，不能留下verification_pending被以后读验升格。
- 成功receipt不变；早期无typed结果时不造finding；unknown、score及
  typed_recording_qualified=false保持。历史artifact无此字段仍按原facts验证。

检查失败时，先按report的typed_verification引用及SHA读本次结果。冷端使用既有
`store.get_manifest(stored_failure_id).parameters.value()["facts"]`读取可选
`typed_verification`；旧记录缺字段就是缺失，不推断timeout，不重试World。
peer验证证明记录/传输完整，不证明原历史失败原因。

实际相关检查133/133，包括真实Verifier失败→正常序列化/归档/Store/传输/独立peer
读回，以及篡改finding必须拒绝。正常通过和未sealed失败路径保留。原游戏前缀与原生
执行在这些测试里是合成接缝，不授予游戏、Human或训练资格。规定候选门禁/CI另绑最终head。

本卡0新World、0GPU、0训练任务，未安装或升级游戏/Host/Runtime、未部署或合并，
原300槽及12 unknown不变。执行偏差：本地检查入口自动同步了94个开发venv依赖，
不符合本卡“不安装”要求；已停止该入口，没有擅自卸载或改动其他环境。
本机旧候选全量检查期间修改源码触发clean-checkout拒绝，共19失败/6037通过/17跳过，
不记为通过、不借给新head；最终候选由其自身正常托管CI执行所选检查。
未完：两槽出牌缺原异常栈，第84槽缺当时finding；没有新原生场景或新批次验证。
唯一下一步为审阅本PR；本卡不自行重开评估来追溯无法补造的历史原因。
回退为revert本次Python改动；原件、模型、已用组合及历史资格均保持。
