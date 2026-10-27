# 录后 Managed 重演与覆盖诊断

这条链路只消费 Q 已入库的封存原件，不决定 Human 来源、训练用途或正式游戏成绩。开局种子与原样进度只在原件；P1、模型输入、玩家快照和诊断日志不复制这些私有值。X2 已负责采集，本工具不补造旧录制的启动事实。

## 输入、入口和成功含义

运行节点需要同一准确候选的构建产物：根目录 `npm run precheck:s0`，以及 Host 原生候选的 exact SHA/MVID 审计。使用固定、clean 的执行 checkout；配置同时绑定源码 revision、入口文件与实际 SDK/Runtime dist 文件哈希。原件不能选择执行程序。

先从一局有 `run_started_native`、自然终局、captured 种子和进度、没有原始 unknown 的完整封存开始：

```sh
node tools/p1-managed-recording-replay.mjs \
  --bundle /ABS/verified-sealed-bundle --candidate /ABS/exact-managed-candidate \
  --language zh --budget-ms 180000 --out /ABS/new-diagnostic-directory
```

原读取器先验证完整 inventory、hash、session/timeline/run 绑定，再按首份 P1 事实读取角色与 A0–A10。Host 以 `captured-native-progress-v1` 把原 UTF-8 字节交给游戏原生 `SaveManager`，仅写隔离的内存 store；原 `normal-sp-all-unlocked-v1` 校验仍严格。运行开始的 SHA、种子与 UnlockState 证明先于动作。语言由工程配置显式提供，不翻译/归一化原观察。

每个动作仍来自原 P1 lowering 和当前原生有限目录，逐点使用原严格比较器；首差异、unknown、预算或缺失证据就停。已有输出目录拒绝再次执行。`completed` 只表示走完该封存轨迹；Human 原件没有原 Runtime Result，因此即使全程一致，也不能称完整 Runtime Result 等价、正式评估或游戏强度证明。

输出 `report.json` 与 `replay.jsonl`。后者明确是 `spireagent/managed-diagnostic-log-1`；私有启动 transport 只记 hash/原件引用，使用 `private_launch_transport`，不伪造标准 P1 raw response。其余公开 response 保留原字节及 hash。

## 录完后自动跑一次

部署新版 Workbench 前仍须服从用户录制与安装窗口。没有以下显式配置时，Q 不启动任何重演；打开 UI 也不会启动游戏。

```sh
python -m spireagent.workbench.recorded_managed_replay \
  --config /ABS/project.json --source-root /ABS/fixed-execution-checkout \
  --candidate /ABS/exact-managed-candidate --language zh \
  --closed-after 2026-10-26T13:08:00+00:00
```

这是配置本机运行节点，不是修改原件、cohort 或模型。`--closed-after` 应设为已安装 X2 的准确范围；无种子/进度的原件显示 missing/跳过，不能回填。可用 `--no-enabled` 停用诊断，原 Q 策略不变。

既有 `P1RecordingMaintenance` 在入库、派生、保存、小结完成且空闲后调用同一诊断 owner；每次最多一局、一个 Managed 进程。原 maintenance ledger 保存每份 source 的 attempt 状态，先落盘再启动。诊断时临时占用原 importer 的 admission guard，避免另一个导入/Store 写入者并行；真人录制入口不受此 guard 限制。

启动前和运行中复用 Host 的游戏进程观察器。游戏再次启动、观察器失败或服务关闭时，只清理本 worker 的进程组，保留不确定结果，不再重演该 source。重启先独立确认旧进程组已退出，才封存其失败前缀；无法确认的继续显示待核，不声称已清理。POSIX worker 生命周期是本版支持范围；不借 macOS 检查宣称 Windows 支持。

可直接打开配置 state_dir 内的 `managed-replay-summary.html`。它由同一 maintenance ledger 的派生快照生成，包含 missing、失败、unknown 与未走到的界面；不需要再扫描原件或触发工作。该独立页面用于避免与其它卡正在修改的小结/HTTP 服务产生双写，后续若要导航入口，仍由原页面 owner 接入。

## 保存、失败与恢复

通过的诊断全套先留产生它的运行节点，按项目 7 天规则保留；本工具不自动删除文件。失败/unknown 的完整诊断前缀沿既有 ManifestArtifactStore 发布为带原件 parent 的 `analysis`，不是伪造正式游戏原件。原 Q peer 可用且游戏退出后送存储节点，完整读回所有 archive member/hash 才写 `full_readback=true`。没有 peer/录制重新开始时显示待保存，后续只续存储，不重演。

发布/传输不确定时保留准确 artifact intent 与状态；仅按该 ID 独立读回核对，不重新投递动作或重造历史。空间低于 15 GiB 时不开始新重演。诊断失败与 Q 入库/小结失败分别显示，不能用可选诊断故障停掉原件接收。真正的原件、未知、失败分母和已冻结的实验用途都保持原样。

## Reference headless 自动覆盖工具

尚未获用户对 Reference headless 诊断用途及 GAME-SLOT 窗口的批准时，只生成计划和跑夹具：

```sh
node tools/p1-cross-host-coverage.mjs --config /ABS/coverage-config.json
```

配置包含 `seeds`、`strategies:[{id,command:[...argv]}]`、`candidate`、`localRoot`、`out`，可选 `budgetMs`、`template`、准确 `expectedInstalledTuple` 与 `gameDir`。种子复用 Host 规范化并拒绝规范化后的重复；策略可用既有 uniform agent，或 `tools/p1-policy-sampling-agent.py` 消费已验证的 438 installation，按 softmax 采样，权重和 InputSpec 不改。

获批且无用户录制时，登记 GAME-SLOT 后才加 `--execute`。当前短链范围是 Defect/A0、显式配置 en/zh 的工程 profile，每次只跑一个 Reference。它先让正常 Runtime 记录策略实际动作和完整结果，再把准确 Reference prepared progress、种子和原动作交给 Managed 严格重演。原 source unknown 不重投；已录点仍进计划分母。Reference 关闭错误原样记录，仅作诊断；关闭未确认时不启动下一份 Reference。

`coverage.json`/`index.html` 包含计划 job 总数、已尝试/未执行、每界面计划/已比/一致/未到达及差异编号。没有见到的界面不能算通过；这不是分数板，也不把两边成绩合并。


`--sources /ABS/prior-output` 可在修正语言等诊断工具错误后复用已验证的准确 Reference Source，不再开游戏；先核 jobs 与 source identity 一致。旧尝试必须保留，不能把来源 unknown 换成新一局。配置 `language` 必须和工程 profile 一致。

当前实测边界（2026-10-27）：真人 A10 原件 131 点，只比到 13 点、12 一致，锁镰展示计数器 3/0 严格停下。显式原生正常速度的隔离 Reference 对同原件 73/73 一致后预算停；Managed 仍即时等待。原件未记录原游戏速度/完整展示时机，当前工具明确报告 `startup_scope`，不能把启动验证称为整局重建。另一个六局 Reference 工程批有两条全程观察和 Runtime Result 一致，四条保留严格停止；完整表归 M3 原报告。本版不安装试验性的正常时钟补丁。
