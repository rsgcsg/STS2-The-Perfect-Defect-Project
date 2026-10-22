# 录制热点第二轮：2026-10-22

用户本轮原话：“录制卡顿先再修一轮，然后空间帮我释放30gb左右，我批准你帮我释放，其他先再说”。本任务仅修录制性能与交付对应日常候选；容量由 root 独立负责，不开展训练、Managed runtime、Human 或 NCardGrid 资格。原五小时派工已经结束，不重置其截止。当前 topic `codex/recording-stutter-round2-20261022`，fresh `origin/develop` 基线 `1e5d83100a8222f211a0e633120ea8123dd06427`；root 非作者验收，recording_stutter_round2 唯一源码/构建/游戏写入者。初始检查点为 Brisbane 10:55 起60–90分钟。

## 问题与实现

真实 current/upgrade card 同64次渲染中，旧方法枚举/ToLookup 共4608次、约50.828ms/63MB分配，Harmony动态补丁资格检查98688次仅约3.094ms。Native Foundation 现在以 loaded Type 弱引用缓存不可变 MethodInfo 表；每次请求仍实时核 Harmony patch/unpatch，未知/被补丁方法仍拒绝。无快照、动作或游戏语义变化，无新业务链。

同64次无临时诊断渲染，66.2851→14.5797ms、54,526,664→5,584,184B分配，仅为定向源码诊断。临时基准和内部热点诊断均未进入最终源码。Annotator 增加最多256条真实≥30ms慢区间，Measure记录实际 Stopwatch 起止和父phase；调用方实际帧起止由同一store记录，aggregate-only数据不伪造区间。Managed canonical patch仅同步同一Native Foundation文件，未运行Managed游戏。

## 准确候选与验证

最终组件为 Native Foundation rc8、Connector rc32、Annotator rc25、Game Mod rc58、Host rc37。Native68/68、Profiler4/4通过；Host最终423 Node通过/5条件skip、32 Python通过，offline smoke通过。原exact-game01因Connector release-manifest漏版本失败；窄修后02通过。Host前两次失败及BOM首次失败均保原日志，不冲掉分母。

rc58构建 SHA256 `55ce85994d3e06bc886c2300100f7eea06e8a7e439479fab045b3e6cf4abcc12`、MVID `a343cf70-1ba9-4278-a9a1-14a80d52f0a5`；准确完整身份以原 build-provenance 和实载owner-ready为准，工作区HEAD不替代组件身份。Managed新候选仅build/audit，不授runtime。

## 有界真实对照

旧rc57与新rc58分别冷载，各从同一隔离profile源复制，逐文件内容相同；不碰个人存档。实际原生流程是继续事件→Start→休息→进入战斗→Pause/Resume时同位置各5轮拿牌/取消尝试→相同目标/拖牌尝试→原生结束回合→正常Close/Host stop。卡牌拖动未提交，不把发出的输入算accepted；两版本均为2事件选择+1结束回合，共3accepted/3terminal/0pending/0issue，来源均为 `agent_native_ui`。

| 指标 | rc57 | rc58 |
|---|---:|---:|
| rich capture次数 | 9 | 9 |
| rich capture mean / max | 57.395 / 125.324ms | 32.219 / 93.910ms |
| rich required reads mean / max | 25.299 / 60.484ms | 9.965 / 31.783ms |
| Recording帧数 / 总秒数 | 12100 / 202.594 | 6001 / 100.546 |
| Recording frame mean / p99 / max | 16.743 / 20.794 / 167.467ms | 16.754 / 22.291 / 121.437ms |
| Paused帧数 / 总秒数 | 2255 / 37.600 | 1483 / 24.717 |
| Paused frame mean / p99 / max | 16.673 / 22.674 / 35.327ms | 16.667 / 22.448 / 33.552ms |

同9次rich的平均耗时下降约43.9%，required reads约60.6%；这是本轮特定热点改善。两次观察时长不同，不能从max下降推断整体帧尾改善；p99未改善，仍不能称日常不卡或用户眩晕已解决。只有一次冷载/候选，没有随机重复或统计性能资格。

新候选31条慢样本中，首次94.165ms idle/readrich实际区间包含在104.007ms Recording帧内；最慢121.437ms帧没有重叠的≥30ms已测capture区间，仍可能含更短capture或其他引擎/系统工作，不能无据归因。旧rc57无慢区间字段，不能回填旧时间相关性。

两个隔离对照候选loaded-evidence均ready，录制兼容条件为 `artifact_unqualified` / execution=false；正常Close健康，Host HTTP200/code0/signalnull/unforced。临时部署后已由原Game Mod rollback恢复rc57，DLL SHA256 `b55a8e98852c0f560f5b6983412a9b321e76695c7ee78b0a3cad2c7556511e36` 复核。临时对照在此结束；后续正常日常交付已由原installer完成，见下。

原始证据在共享交接目录 `replies/evidence/recording-stutter-round2-20261022/`，两原session、完整profile、命令请求/响应、loaded身份、退出和rollback均保留；汇总 `runtime-comparison01.json` SHA256 `2ffcb685a28d940e7036d7aa37f23483fac6c8b71abd3b360a0408f70a23c43d`。本文件仅摘要，不复制原始游戏数据。源码通过正常merge保持组件provenance；PR可开，未获本轮merge授权。

## 正常日常交付已完成

原installer产出并安装不可变kit `3e9f9e614e70ef07ec368d5c9cc026411366669661ca621af194c6ef3759f28c`（12,577,266字节、736文件、打包源 `d04bbfeebd538121b0ef446e9f408fcf928340cb`）。原prepare和隔离initialize通过，永久Python环境按原锁保留cloud与local-models功能，原Evidence安装身份及Torch导入检查通过。旧Workbench70228经原owner正常stop；collection-tool注册、launcher CAS替换、原native-access重绑、deploy和固定open入口均exit0，project配置SHA256仍为 `07df30d95fac4a2dfdce649577ec5deb084247b5c9044850a77f8e4aa32df44e`。

2026-10-22 01:38:56 UTC正常冷启动游戏PID11422，新Workbench PID11378。kit自带owner `collectionSetup status` 确认实际rc58 SHA/MVID、01:38:58实载时间与进程代数，返回bound/connected且errors为空。原生Platform面板显示“已连接游戏工作台”；health HTTP200，Model idle/未加载，Training ready/idle；录制ready、无session、append/disk为not_open、无error/pending/recovery。`last_result`仍是另一历史runtime的Close，未借作本次Close证据。本次正常路径未开启新录制，也未操作游戏。

正常日常launcher的既有source-canary路径报告 `canary_exact` / execution_available=true；这与前述隔离对照的artifact_unqualified/execution=false条件不同，分别记录，不提升动作、runtime或科研资格。原Model runtime保留旧代，没有重装、训练重跑或新资格声明。正常游戏和Workbench留给用户继续使用；本次无新增性能测量。browser专用read-only接口在control凭据下的401/400响应保留为边界结果，不计功能通过。

最终日常收据为共享证据 `normal-delivery-final01.json`，SHA256 `fb9f9186fcbd33ddbcac0de30d1efaa17166ef940e21e8f73299930135dd5888`；原loaded/health/cutover收据均在同目录。旧kit `a9724a6762f49489ad0fb291e93ad9f47c7f73f585e7e45383a12514197badb4` 与私密配置、launcher/native-access备份保留在原workbench-recovery的recording-round2-20261022目录。回退须先原owner停游戏/Workbench，再用原installer将旧kit注册与launcher CAS恢复并deploy旧rc57；不直接覆盖运行中的DLL。源码以正常merge后revert回退，PR211仍未获merge授权。Hosted CI以PR实际最新head和原run状态为准，pending/skipped均不写通过。
