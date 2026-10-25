# 日常 P1 录制：共同安装与工程实机回执（2026-10-26 AEST）

卡 B 第一段的源码、共同安装和有界工程实机链已交付；**Human 手感、用户下一局及32局连续采集尚待用户实际录制验收**。本轮不实施模型接管来源，不改 P1 合同，不处理旧录制。root 是本轮唯一录制源码、安装和现场 writer；Q 负责封存之后的维护与首盘核验。

## 准确安装对象

- B PR246 普通合并 `9b87d215a80bd907075e1bad5de37579b4db834c`；Q PR245 普通合并 `0009a2cf7e21b2f432ee0b657d45c7efc22516e0`。
- 本次永久 Workbench/source 固定在共同提交 **`0009a2cf7e21b2f432ee0b657d45c7efc22516e0`**。之后 develop 上的 PR247 不在本次已装组合中，不能借此回执宣称它已安装。
- kit SHA256 `394a0200c71c59a81d20ff7eca86772e89450ffa837187a7b7eb28a9b0aca3e2`；永久目录为 `~/.local/share/spireagent/workbench-releases/<kit SHA>/`。
- Mod `0.2.0-rc.65`，DLL SHA256 `4a4fe02c28e47b979fb9e58f6cc6bd529a522ba77d009010000f642134ca40a5`，MVID `5ee6d8c5-3b4d-4f4c-929e-4d27d6b0787c`；Annotator rc28 / Live UI rc20。
- 固定 Tool 格式 version=1，release `5171897f083a5abd77bad879ad7314c2c47fbd3633a19f07eaaa27f1489bf227`，构建工作区 `8f4de36c053fde6ec9365c6cc29e68b35d9c722f`。原 packager/sourceSetMatches 核共同源码的所有编译输入后复用该实物，没有因 Python 批准清单变化重发 Tool。
- 用户已在 Q 批准此 Tool 相对0464的 PR241 卡包 admission/reducer 差异，只用于今后录制；旧25组继续选择0464，主转换器/bundle未因 B 改动。

原 installer 的 prepare、initialize、preflight、Game Mod deploy、Tool replacement、双hash launcher CAS和native-access入口均已执行。旧 Mod、launcher可恢复目标和私有配置快照保留；没有复制native-access secret。仍用同一 `p1-development/project.json`、录制根、status路径、Store、远端计算与cloud环境文件引用；配置比较确认只有combination更新。`cloud`及`local-models`按原锁定入口准备，doctor和实际backend import通过，没有启动训练。

## 实机结果与分母

| 场景 | 实际结果 |
|---|---|
| 与mac启动台相同的Steam快捷App | 新Mod已装载，原collection setup绑定准确runtime；Workbench未启动时P1 API开始、暂停、继续、Close均成功，0操作工程片段已双closed |
| 原Game Mod内部launch入口 | 已冷载同一Mod；native Project页显示已连接同一Workbench；GUI暂停/继续改变同一原Application会话；正常Quit自动双closed |
| 再从普通快捷App重启 | 新runtime、Ready/session=null，HUD恢复录制待命；普通native Project页已连接；原CLI Ready Close明确返回recording_standby_stopped，偏好持久禁用 |
| 原Host隔离native-ui / Defect A0 | GUI显式选择AI界面来源并开始；原生新局录2次操作，Save & Quit自动双closed；Continue自动新开同来源片段，录1次操作，再Save & Quit双closed |

共 **3次显式开始/3成功，1次Native Continue自动开始/1成功；4个工程片段/4已封存；3次原始游戏操作，pending=0、issue=0；录制开始失败0、录制请求unknown0；Human片段0**。前两个片段为主菜单零操作控制验证，不能当作整局成功。后两段分别有 `run_started_native` 与 `run_resumed_native` 见证，保存退出保 `recording_segment_exit`；没有拼成完整自然局，也没有训练派生主张。

四个session后缀依次为 `ac08bb16b862437ea700cebaf53848f0`、`be1a92bcebb64d8cbdba213b489175c7`、`8d54bee8106048a3b1f7566afcdc1ef1`、`df4eabbcec2a462590025158492b99f4`。实际读回每个 `p1-native-close.json`、`session-close-receipt.json`、events和run journal，核Closed、身份及两项SHA；回执 `recording-seal-readback-01.json` SHA256 `5bdf3454195b1e7411d967f6d0156a3661fce5dfcb7ffe3cacc87686961235d9`。

隔离Host经原owner正常shutdown，forced=false、endpoint released、driver exit0；原个人profile全树前后均1889文件、98,465,058字节，SHA256均 `63a7f76b70c87b0c502515ac61f88ce3039bb774eb007b2c83bd38497da4b545`。最后已取消Agent待命；日常Workbench保持正常服务，工程游戏已关闭。

## Q连接与准确检查

先用旧正常CLI禁用旧maintenance并停旧App，再装共同组合。新原CLI enable一次遇2秒Timeout，**没有重投**；随后原API/磁盘确认范围已经应用：`closed_after=2026-10-25T15:32:47.700939+00:00`、只纳入`declared_human`、no-cool-recent、中央policy、原R2 peer保留。原cache trim自然完成，Q亲核首盘25组/A0=18/A10=7/N=10,319、配置与旧基线相同，工程来源排除；实际冷首盘131.527秒，启用到首盘491.861秒含原cache和工程游戏等待。没有把ACK超时改成exit0；后续原后台操作继续由原owner完成，没有重启或reconcile它。

B最终候选8f4de36c：非作者复核接受，exact-game Core464 / NativeLogical182 / SDK615通过，clean Mod build通过；full03全部12阶段通过且source_accepted=true；准确CI40199783919与合并同树CI40201827482成功。Q最终候选adcdbdd4：137相关回归通过；准确CI40204007363的两Linux和portable成功，full回执5319通过、16跳过，跳过独列；合并同树CI40205927794成功，不冒充再执行一次full。

保留失败：B full01环境依赖/系统Python问题；full02因真实Restore InvalidDataException修复取消，链接production partial的9项回归后才用full03；Q前一轮2个旧cache fixture漏声明game stopped，补前提而非放宽生产门。CUA曾返回noWindowsAvailable；隔离场景切回标准窗口后续玩完成，普通路径GUI连接也已实证，不据旧失败截图改产品源码。原生退出的Godot RID/resource leak警告单列保留，不声称完整teardown零警告。

普通Steam被动录制上下文仍为 `artifact_unqualified/execution_available=false`，其装载和录制连接不等于原mutation verify-loaded gate通过；内部/隔离canary有各自身份，不能转授普通执行资格。没有掉电/强杀零损失、全卡全事件覆盖、模型效果或Human整局资格主张。

## 用户入口与剩余验收

操作见[原生GUI指南](../../apps/ingame-ui/README.md)和[原安装指南](../../python/docs/DEVELOPER_KIT_INSTALL.md)。照常从启动台/Steam开游戏，新局前点“开始本人录制”；该点击是明确来源声明，GUI/CLI仍映射同一RecordingApplicationService。暂停或结束并封存会取消待命；正常保存退出后续玩另开真实片段。下次用户本人开始录制时，约10分钟观察起录/保存重进可并入原32局；长局途中不断采与自然终局原件仍由用户的真实数据验收。
