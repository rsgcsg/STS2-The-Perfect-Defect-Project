# 第一批三小时执行：当前唯一接续

用户明确授权开始第一轮，硬上限三小时，达到验收可提前结束。起点UTC10:00:59，绝对截止UTC13:00:59（Brisbane20:00:59—23:00:59）。root仅主管；各实施者独立分支，batch1_integration独占集成、BOM、CURRENT及本文件。外部主管表为`claude-handoff-2026-10-13/replies/P1-BATCH1-SUPERVISION-20261021.md`，其中最新用户原话优先。

## 当前状态与准确边界

集成从PR207准确4128b2ce继承，origin/develop基准8b2c；唯一writer分支`codex/batch1-integration-20261021`，只普通FF推既有PR207。纯规范44aac862、Installer8f807/96f6/405bbe、Storage47769154/b42020f7及GUI4ce1298d均已normal merge；UIrc16/Modrc51源95b171d4及BOM一致。正常cloud extra加入原已锁boto3，不换解析版本。准确组合七文件174测试、UI34测试和BOM15测试通过；保已有remote_compute的新回归18测试另通过。CURRENT与project:closeout已检查，准确完整门尚未启动。

PR207旧head4128仍保Linux fixture原失败，日志在集成工作树`.local/batch1/original-ci-linux1.log`。中间eca run39582497868因后续普通push自动取消，不能当新产品失败或通过；169c run39583904466已启动，本批最终CI须独立读取。旧完整门5失败与随后7项targeted通过仍保原身份。

正常日常后台61901保留，当前未安装本批候选、未开游戏。唯一正常安装者是GUI；Installer准备kit命令和依赖，I不给自己另开Mod build。历史HOME固定launcher仍指旧kit/临时配置，GUI须按原expected-hash替换、正常永久kit配对与rollback验收。Storage正在修首真实import的实例锁自锁，Training无新增生产源码；它们的实际结果不由targeted绿色替代。全门前冻结准确组合，root给重型slot才执行，未准确完整门/CI全绿不merge及不正常升级。

## 本批可验结果与硬限

| 唯一负责人 | 验收完成节点 | 硬限UTC |
|---|---|---|
| batch1_storage | 正确Tool真实Catalog原因；后台自动封存处理/R2全读回及reader；允许副本冷却→恢复→reader、活动保护和实际df，重开策略保持 | 12:35停新增，12:45交原证据 |
| batch1_installer | Linux忠实fixture和Kit空env根因修复；相关测试通过；正常后台不依赖Codex或临时终端的机制查明/必要修复 | 10:45首包；12:40最终停 |
| batch1_gui_native | 游戏内简单录制控件、R2/空间状态映同一应用；准入后准确正常安装/加载/主控件与重开实证 | 12:30停新native，12:40恢复交付 |
| batch1_training | 既批两次额度内工程baseline/同Run恢复/ModelResult/CPU导出原证据及费用Stop；七源+627候选实际去重/成员/用途账 | 12:15停实现，12:25provider终态，12:35交付 |
| batch1_disk_handoff | 大户具名清单、dirty/active/唯一/恢复证据和建议；原SOP缺口及具名接手 | 10:35首包，12:35具名接手停 |
| batch1_integration | 各包normal merge、对应测试和准确组合完整门/CI；满足用户既批条件才normal merge207，给GUI准确安装身份；closeout和逐项结果 | 11:00 freeze目标；12:30停新变更/长门，12:45收口 |
| root | 调度/统筹/审计/交付；每15分钟核真实推进；依赖或目标错误及时收敛 | 13:00:59最终交付 |

## 资源与验证安排

10:15—10:35重IO给Storage，scratch上限700MiB。11:00—11:45预留唯一本机完整门slot，其余源码轻检查可并行。日常服务和录制优先。I私有小env只复制原1.6MiB锁定依赖视图，ML依赖以只读路径复用；Evidence32真实VCS安装的原metadata/RECORD及31源文件逐字节核验，父子进程均从当前集成源码读取，20required/87selected无锁差。Node依赖APFS克隆54MiB，不共享可写目录。

全门预计30—45分钟。旧全机窗口可用空间下降约5.7GiB，仅是保守参考，不能当全门自身峰值；本轮使用独立TMPDIR并测实际体量。2GiB只是早期临时估算，不新增为通过/中止门；开始前据当时df、已测峰值、活动录制保护和其他进程判断资源，低空间时正常停止并保留准确结果，不自动重跑。准确完整门及CI未齐则S2未验；通过也只授source/test，不授installed/runtime/Human。GPU和数据/R2按原owner授权，不能借测试启动正式训练或任意清理。

## 收口规则

到时停止新增，准确报告PASS/FAIL/未验。录制线可先交，但不替整批通过。保留所有原失败、原件和运行身份；不杀正常日常服务。旧历史第一轮结果继续从[历史执行记录](ROUND1_EXECUTION_20261021.zh-CN.md)读取，不能从其旧时间或私人checkpoint派活。
