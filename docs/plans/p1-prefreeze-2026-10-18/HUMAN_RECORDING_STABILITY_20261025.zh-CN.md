# 日常 P1 录制稳定与封存（卡 B 第一段）

root 唯一源码/安装/现场负责人。用户先要求32局使用现状、两批间安装，随后明确最近不录制、开录会明说；因此当前可用空档检查及安装。日常主要入口是 mac 启动台，Steam 游戏库和内部正常入口同样需要可用。基线 origin/develop `21fd85ea78c8e402166bda69b84a5ec5dec900fa`，topic `codex/recording-stability-20261025`。开发起点2026-10-25 23:13 AEST，45分钟复盘23:58，硬截止10-26 03:13，02:43起收尾；不以换上下文重置。

目标是每次可开始、中途不掉、完整封存，其次是一个按钮开录、逐局自动封存及保存退出/重启后待命。GUI/CLI/API共用原RecordingApplicationService；封存后导入/派生/R2归卡Q。用户接管模型段与新来源另交用户批，不在第一段混入。

历史上已有成熟Human native staging/async owner、Store Close、逐局自动开始/封存（ADR-0010）和外部投递（ADR-0008）；旧约两万条固定数据并非P1。10-19的恢复方案已识别当时连录未携带P1配置，但当前ContinuousRecording.NextSessionCommand已经修好，所以本轮直接复用。当前新增的GUI工作台配对前置不是原录制CLI所必需，改为映射同一录制Application；不倒退当前P1公开信息/Read/原生回执。10-22之后普通安装Mod rc59，而日常工作台/本机访问仍pin rc58，是本轮读实际文件与主菜单加载身份确认的错配；不削弱原身份检查。

链路位置⑤录制/封存；变更类为现有实现与GUI调用映射修复，治理G4。上游给原生接受/执行/结束事实和公开PB，下游Q需要真实完整Close的不可变原件。本轮不改Human来源、P1合同、派生或研究准入。

验收：受影响Core/UI/原件封存回归；规定exact-game和干净构建/产物身份；适当非作者复核和准确CI；用正常安装owner保回滚、装载验证。最短现场先验证启动台主菜单开始/暂停/继续/Close与重启待命；工程操作来源只能agent_native_ui，Human整局由用户本人另验，保开始/失败/未知分母。故障与原件不重写，unknown不重试。剩余空间低于15GiB停止。

当前：B PR246与Q PR245均已按准确CI普通合并，本次从共同提交0009a2cf一次安装kit394a0200、Mod65/Tool5171及同一日常Workbench；启动台/Steam与内部入口均已冷载并实证native工作台连接。3次显式起录及1次Continue自动起录形成4个Agent工程片段，全部双closed；实际3次游戏操作、pending/issue均0；重启待命和取消通过，隔离个人profile全树不变。Q已从now/Human/no-cool范围启用并亲核原25组/10319N首盘，保原enable ACK超时与后台操作。源码、检查、原生退出警告、回滚及证据边界见[准确安装实机回执](../../evidence/DAILY_P1_RECORDING_INSTALL_2026-10-26.md)。工程交付已完成，剩余唯一验收为用户下一局及32局实际Human连续录制；第二段接管未开始。日常App保持服务、Agent待命已取消、工程游戏已正常关闭，不停其他服务。
