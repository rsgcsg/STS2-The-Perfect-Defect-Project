# 当前工作：P1 r1.4工程采集与严格短重放通过，准备同候选正式三局

更新于2026-10-19。本页为仓库导航；唯一现场状态为工作区固定文件
`work/P1-SEMANTIC-ACTIVE-20261019.md`，主报告为`work/P1-QUEUED-INPUT-REPORT-20261019.md`。
恢复前检查准确Git、脏改、agent及进程。不要从旧报告重复启动任务。
Resolve live GitHub refs; current source and original evidence override this file.

## 当前结果与下一步

用户要求保留三局、先修采集再补采；已批准公开升级预览、限定非战斗用药，
以及非战斗丢药共同边界。P1 r1.4新源码857831a6+00fbd57b完成该边界：
两端支持的非战斗基础页提供独立discard；Reference在同次同步采集中重采
纯popup遮住的实际底层页面，原popup完整PB另存、基础事实/全部Reads/其它动作
保留。raw partial传播outer；共享SDK核范围/同次归属和药水别名；关闭popup
后未完成输入仍为unknown，不重发。其它popup保留原页，不扩战斗时点。
109 SDK、76 C#、50 Managed与限定非作者源码复核通过，clean构建/部署/回滚/
冷加载已核；Managed原生executor本已具备，能力接入在JS projection，不另造规则。

同实际seed和Reference prepared progress的内部两端预检：真实打开Energy
popup前后完整P1相等，6次输入含discard/Play/EndTurn与最后后继共7点全等。
随后工程19由agent_native_ui加用户协助完成：6 accepted/6finished/6derived，
0producerissue/unknown/pending，18份原PB及260份卡牌Read完整，原popup完整保留。
实际Runtime Managed重放6次完成、完整7点全部严格相等，末后继相等、
0unknown/taint/budget，有限前缀结束后handoff。原件Close/pack/exit0/
shutdown200/forcedfalse，Managed正常关闭。未提交拿牌取消没有增加成功label，
不冒称另一个cancelled GameAction。只授此限定短样例，不授全部9场景实跑、
整局Host等价或训练资格。现场收据为工作区
`work/p1-semantic-20261019/noncombat-discard-qualified-short01.json`。

下一步直接开始同候选正式英文整局，由用户游玩；每局开头优先原件前缀
转换核对后继续，不再增加单独真人短局。准确游戏/端口/PTY只看唯一现场状态。
三局期间保持源码、Mod和Managed候选冻结，不同tuple不混算；最终冻结仍未通过。

原工程15/17失败与原件保留。15暴露Guilty原生不能升级误判及尾部merchant
取消未用本parent后继，09841067已修；65/79定向检查及Guilty两端实测通过。
17为20accepted＝7派生＋2已批例外＋11gap，22producerissue、0unknown。
013a9316修新卡副本在任何原生深复制前清除11回调；66检查及内部战斗短路径通过。
旧17的popup缺完整底层选择，不能回填成r1.4。工程18未作局内选择，因旧私有
固定seed没有目标样例而正常封包；编排路线复查后工程19显式复用已验证seed。

原三局同旧候选：第一局三幕win、1067accepted已解释、981点；第二局第三幕
48层loss、716accepted已解释、667点；第三局第二幕33层loss、369accepted中
2次非战斗用药已修解释，但1次丢药原H/PB缺失和2次merchant父unknown仍阻断。
三局文件闭包3/3、来源无未解释producerissue2/3、结构完整派生2/3。
旧原件不回填，结构完整不等于信息够用、整局严格或训练资格。

## 批次和其它边界

B按用户决定停止于index118，独立战斗用药时点差异不由本轮非战斗修复解释；
无07、无D、不从H补S、不删合法动作。旧eca6ce72 Reference20自然loss20/
1471选择、0unknown/taint/budget，原件及normalclose PASS；Managed40为24自然
loss/16技术停止、5unknown/7taint，FAIL。两侧同包/seed/progress的R20短链各自PASS，
不授Host等价。新候选未重跑40+20，旧通过不转移；W冻结eca6ce72不改。
升级预览5→8、Guilty not_applicable与两端完整起点定向实测通过，只授样例。
最小云首局3158文件R2回读/唯一Modal CPU同SHA通过、资源已停，正式云覆盖1/3。
本轮无新上传、GPU、训练、push或新PR。完整历史与收据从主报告进入。

## Remaining Platform non-claims（上层链路与后续边界）

从[唯一链路和连接表](../plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md)及
[P1协议](../protocols/P1_GLOBAL_R1.zh-CN.md)进入owner。C1同P1下Host一致、
C2声明原生操作一致、C3投影正确、C4信息功能够用分别判断。P1是可替换profile。
GUI/自动落盘/轻量恢复、A独立原生重放和C下一版freeze_card_group仅登记
[冻结后首批待做卡](../plans/p1-prefreeze-2026-10-18/POSTFREEZE_RECORDING_WORK_CARD.zh-CN.md)，
冻结前不实现。整体冻结与最终PR仍须批准，10-20 16:30硬截止不变。
