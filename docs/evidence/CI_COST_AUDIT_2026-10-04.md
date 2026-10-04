# CI 成本与测试真实性审计，2026-10-04

本轮结论：Windows 关键路径主要在 Pytest，不在排队、安装或 Workbench E2E。
重复构造真实训练/存储输入，以及重复 immutable 发布的磁盘写入值得优先优化；
当前证据不能把 Windows 放大全部归因于 Torch。此次小候选改进本地 blob 的
幂等发布热路径，并把已提交补丁检查放到 CI 选任务之前。没有减少测试、skip、
双 OS 门禁、BOM/source identity 验算或内容校验。

## 范围、身份与证据方法

只读 GitHub 官方连接器取得 job/step 原始时间、完整 job logs 和已有 JUnit ZIP。
以下详细基线均为 attempt 1、真实 full 执行，已核对成功 portable 回执：

| Run / PR | PR 源 head | 回执中的实际 checkout | 树 |
|---|---|---|---|
| [37178536966 / 151](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37178536966) | d8b5c9ff277cba39921c533a06a22763818c0141 | 1d702e9f0bc9e360dc06be2e87deebe99397f46b | cc85c699d7d83289871a58cd3df49daad4f360d4 |
| [37180090110 / 153](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37180090110) | 81ad4d1cb14a1d2661ed8f5be8770b03f3edfa1f | 38f1e5447bec071675c46fb130143858d52c82b5 | b657c6c7c677190c0d70d02ef1e7c8ad9736e07f |

PR source head 与默认 PR merge checkout 分开记录；不能把二者混写。
两份回执的 workflow blob 分别为 c97dfdf3834a55d595b256597afbefab9b43c67b、
26be7843a750d11925eb33ff145250cbeae1394a。四份 JUnit ZIP 均逐字节核对下载 SHA256：

| Run / OS | Artifact ID | ZIP SHA256 |
|---|---|---|
| 151 / Windows | 11294744841 | 074f7098ffb77231c860489a87f24811c296f209a0d7c5c2d5c0f52ba7102218 |
| 151 / Linux | 11294991192 | e25d68d43502049e995bdd4df9ab294e0a0b8b195006c9397fa1ab9c620338f2 |
| 153 / Windows | 11294494231 | 19a69021d5bfec7b15869d75ab64e7862a076d012b6ed53904ea591fbe0c58e1 |
| 153 / Linux | 11295074746 | 8dd417bc4ee76306581ec0db115f33d6aca79c21ba516c11e8a6036b2aa0c654 |

回执 ZIP SHA256 分别为 c6cde64af3007fc7202ff6c3b551d8820977ccb62bdf028c53d36dfa3ea9b8ba、
1bf77e097a5537d4bba0feb53c1200d16eaf42bd0d9be05455aa63bdb946b44f，
与 GitHub artifact digest 一致。采集于 2026-10-04；
报告只含计时、源码身份和合成测试信息，不含真实 store、权重、游戏或录音内容。

本任务独立 clone/branch 从当时 live develop
9556d21188b2deea027192567827a339a0ce50f7 开始。该 develop 尚不含上述所有
候选实现；审计新候选源码为只读，未改 M2/Product writer 的 checkout 或脚本。
本候选不据历史 run 宣称新 head 已过 CI。

在首轮候选执行期间又核对到直接 base 的同树基线：develop9556 的
[push run36832341761](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36832341761)
仅 reuse，不能当新 full；其 plan 指向
[executed run36828240491](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/36828240491)。
原 PR source550c5b8d50eb861ad3ed3318aa8b300a9645463a、实际 checkout
01bef8d1e8b6f858a6d1076f3a34703872bd73d2、tree
d5b01593b3419c3fd92ba7bb744f424bb94880d2，与本候选 base tree 完全相同；
workflow26be7843a750d11925eb33ff145250cbeae1394a 也与 base 完全相同。
三个 artifact 均下载并核对 SHA256：Windows11147995396
4345c28fb9c11c7ec386a918a2a677b36116617e53a719ec9f636ad88d73519a；
Linux11147170173
102e2db7396e421207a89db2e6c49ffe4f38d871d2729c64428e76fe5f351e28；
receipt11147376197
f4d0c469a9701076fedde82adcd760e4f70f53138e229ace9414df6f1e954526。
两 OS JUnit 均2249条，errors/failures=0，Windows/Linux skip41/4。
这是更接近的源树 before；仍不是相同日期、同一 runner 的受控重复试验。

## 墙钟与阶段分解

单位秒。安装取 job steps；Python 细项取已有 portable_duration JSON。
“其他 gate”是 selected step 减去所有 Python 命令计时，主要为仓库、Node、
C# 和包装检查，含少量进程启动/输出开销，并非精确 CPU 时间。

| 阶段 | 151 Windows | 151 Linux | 153 Windows | 153 Linux |
|---|---:|---:|---:|---:|
| portability job 墙钟 | 3301 | 952 | 2242 | 997 |
| uv 锁定安装 | 31 | 13 | 30 | 14 |
| npm 两次安装 + SDK | 10 | 5 | 8 | 6 |
| selected gate | 3210 | 917 | 2164 | 954 |
| Pytest 子进程 | 2825.985 | 716.745 | 1885.829 | 747.793 |
| Mypy | 46.906 | 47.466 | 27.563 | 51.288 |
| Workbench CPU E2E | 75.109 | 30.784 | 48.921 | 31.347 |
| cloud-worker CPU smoke | 26.266 | 18.281 | 16.735 | 19.047 |
| Python build | 4.000 | 2.615 | 2.765 | 2.650 |
| Python doctor/Ruff/SDK/compile/hygiene 合计 | 2.140 | 1.183 | 1.452 | 1.202 |
| 其他 gate（差值） | 229.594 | 99.926 | 180.735 | 100.673 |

Windows selected gate 占 job 的 97.2% / 96.5%，Pytest 占 selected gate
88.0% / 87.1%。plan 为 10 秒，portable aggregate 为 10 / 14 秒；
run 创建到 plan 开始约 3 秒，plan 完成到叶 job 开始约 2–4 秒。
这些 run 没有长队列证据。全 run 墙钟分别约 55:30 和 37:53，不能把
Linux 与 Windows 墙钟相加解释开发等待时间。

直接 base 的 executed run36828240491 阶段秒数如下：

| 阶段 | Windows | Linux |
|---|---:|---:|
| job / selected gate | 2406 / 2308 | 743 / 698 |
| uv / SDK+npm安装 | 44 / 9 | 15 / 6 |
| Pytest子进程 / JUnit suite | 1900.000 / 1896.595 | 496.275 / 493.316 |
| Mypy / Workbench E2E | 52.907 / 71.406 | 48.518 / 31.814 |
| cloud smoke / Python build | 30.766 / 4.422 | 19.292 / 2.550 |
| 其他 gate（差值） | 246.515 | 98.469 |

base runner image 为 Windows20260922.246.2、Ubuntu20260920.314.1；
后续候选必须记录自身 image，不能只因为源树相近便忽略 runner 差异。

另外 fresh job 元数据：[PR150 run37175286478](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37175286478)
Windows/Linux gate 为 2424/1053 秒；
[PR152 run37178246849](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37178246849)
为 2589/833 秒。其安装同样仅几十秒，支持上述主因判断。

根据 npm script 日志相邻入口时间，151 的组件检查区间如下。
它包括嵌套命令和 npm 开销，不能将 C# 与 Node 单独相加两次：

| 组件 | Windows | Linux |
|---|---:|---:|
| Connector SDK/CLI | 18.424 | 11.881 |
| Host Runtime（含 C#/Python/包 smoke） | 54.568 | 21.946 |
| Annotator（C# + Node） | 34.376 | 20.814 |
| Evidence Python | 35.906 | 9.654 |
| Policy Runtime TS/typecheck/build/package | 52.793 | 25.727 |
| Workbench Node | 0.957 | 0.630 |
| Live UI Node | 6.338 | 4.190 |
| Game Mod source tests | 0.869 | 0.474 |

现有日志能定位组件级成本；未有单项 C# JUnit/CPU/profile，不能声称其内部
哪个 fixture 是瓶颈。Policy Runtime 重建 Connector TS SDK，存在重复构建；
其区间最多几十秒，优先级低于 Python。禁止省去安装包消费者或独立 package smoke。

## Pytest 热点与 OS 倍率

下表是 JUnit testcase time 按 classname 累加，含 setup/call/teardown，
不是完整 suite 墙钟。151 suite 为 2784 条 JUnit 记录、Windows/Linux skip43/4；
153 为 2822 条、skip43/4，均 errors/failures=0。子测试记录会影响计数，
不要把 JUnit 条数直接写成普通 test 函数数。

| 文件（tests/） | 151 Win/Linux 秒 | 比值 | 153 Win/Linux 秒 | 比值 |
|---|---:|---:|---:|---:|
| test_local_m0_remote | 478.544 / 94.638 | 5.06 | 310.754 / 98.841 | 3.14 |
| test_local_training | 207.591 / 71.803 | 2.89 | 149.785 / 76.582 | 1.96 |
| test_dataset_curation | 98.405 / 4.132 | 23.82 | 33.413 / 4.431 | 7.54 |
| test_hub_member_api | 97.496 / 5.623 | 17.34 | 43.346 / 6.423 | 6.75 |
| test_local_memory_model_export | 89.102 / 26.204 | 3.40 | 55.877 / 28.857 | 1.94 |
| test_token_comparison | 82.506 / 7.176 | 11.50 | 52.426 / 7.220 | 7.26 |
| test_public_m0_registration_budget | 77.347 / 22.852 | 3.38 | 58.421 / 22.209 | 2.63 |
| test_workbench_memory_v2 | 75.966 / 54.360 | 1.40 | 51.403 / 55.873 | 0.92 |
| test_token_remote_update | 74.110 / 26.487 | 2.80 | 59.570 / 27.829 | 2.14 |
| test_local_memory_registration | 67.852 / 18.170 | 3.73 | 40.087 / 19.882 | 2.02 |

前两文件在 151 Windows 累计 686.135 秒，占 testcase 累积约 24.5%。
不能只处理某个排名第一的单例，就称已解决整套 CI。跨 run Windows 漂移明显；
两个源树、测试集合、runner image/负载不同，以上不是受控 before/after。

源码定位到的重复成本：

- M0 remote 的 _case 每个负例重新执行 _ready、输入投影及 prepare_token_run；
  很多用例主要测试账户、规格、停止回执或断线恢复，输入本身不变。
- 注册 budget 的五个参数用例，每次 real prepare -> 一步 train -> export ->
  首次 register；日志中 setup 本身约 11–15 秒/Windows、约 4 秒/Linux。
  五次 setup 在 151 约 73 秒。其后还会故意撤销 ledger/tamper receipt，
  因此不能共享可写 owner/目录；可共享一次真实 immutable producer 输出，
  每个用例独立复制并重新建立 owner/验证。此项尚无安全复制实测，不报节省预测。
- Workbench v2 单例真实执行四个 recipe 的 child train/export，覆盖完整 recipe
  与 profile/error 边界；不能删掉三个 recipe 或替成过宽 fake。
- token_comparison 已采用 module 范围的真实 completed-pair producer，再按
  用例 copy_artifact 到独立 store；继续改 fixture scope 不会消除大部分磁盘成本。
- LocalBlobStore.put_if_absent 在本轮 base 中，即使已存在完全相同内容，仍
  mkstemp/write/flush/fsync/link失败/get比字节/unlink。copy_artifact 每次闭包
  发布和重复 prepare/publish 都会碰到这条幂等路径，属于可直接消除的重复 I/O。

Torch 的若干测试已有 1/2 个 CPU thread 限制，少数设置没有恢复。没有 pytest-xdist
或 gate 外并行；尚无 profiler/线程计数证明默认 Torch/BLAS 过订阅是主因。
纯 curation/API 在 Windows 放大更大，而四 recipe v2 在两 OS 接近，反对
“所有慢都是 Torch”推断。后续只对一个已定位真实 tiny case 做有界 profiling，
记录新建对象、重复 key、fsync 次数和训练/进程启动计时；禁止直接开全套 profile。

## 测试质量、资源、触发与缓存

当前分层和触发总体合理：router 对未知路径、锁、工具、治理、部署选 full；
Python owner 选整套 Python + repository guards，两 OS 均保留；独立 topic 执行，
仅允许已有精确 tree/workflow/年龄/attempt 回执规则用于 integration/promotion。
不能用路径跳过 required workflow 或把祖先绿复制给新 head。npm cache 绑定
root/python 两份 lock；uv --locked 安装和 SDK consumer 安装保留，warm install
已很短。没有理由缓存 .venv/node_modules 或共享可写模型/owner/SQLite 来提速。

公共套件已有 reporter recovery/immutable completion、controller dispatch/resume、
CLI 输入和 SDK 参数、真实本地 HTTP/上传边界。FakeQwen 是计算替身，
并不证明私有 main 的真实初始化调用正确，也不证明云 SDK 的大结果 blob 上传。
委派提供的 M2 Reporter 缺 slots 和网络大结果事件是私有执行上下文证据；
此次未读取私有控制器或原始结果，不能把 handoff 写成新鲜复现。

应将通用入口的真实构造签名、tiny 本地 store/reporter/controller 串接、序列化
和 result-upload adapter 放入 owning repo 的可复用边界回归。不能仅以宽松
Mock/SimpleNamespace 接受不存在的调用。真实 Provider blob 上传仍需要显式
授权的 provider 小 probe；本地 fake 与 small GPU parity 都不能代替它。
预算、身份授权、费用核验、个人路径和 private control 保留在私有 owner 证据，
不把私有驱动打包公开。

Modal poll 目前把非 timeout SDK 错误统一成 result_unavailable，现有测试要求
不泄露 signed URL。可增加有限、content-free 的 failure category 和单调阶段
计时（构造、提交、poll、serialize/upload）；保留 unknown、无自动重试和
原有外部结果语义。不要输出异常原文、endpoint、请求/结果或凭据。
本轮只审计，未抢写 M2 owner 的诊断逻辑。

路径/端口是双 OS 真实边界。[PR154 run37184608041](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37184608041)
Windows 失败日志核对到 boundary.test.mjs:44，实际 rc.21、期望 rc.22。
这是已有廉价源码检查能捕捉的版本不一致；归原 Product owner 修复。
可把既有 Game Mod metadata 检查放到相关开发候选的最早本地检查，而非新增版本 authority。
历史 /tmp Windows cwd 与固定端口问题按 owning 测试保留平台负例；
真实 game/runtime/Human 和 provider 资格仍在 portable gate 之外。

另核对 [PR154 replacement run37185096547](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37185096547)
Windows job111385305169：doctor0.296s、Ruff0.297s 后立即 Mypy49.000s，
因 install_developer_kit.py 六处 os.geteuid 的 attr-defined 失败；未执行 Pytest。
当前 Python lane 已是 doctor -> Ruff -> Mypy -> SDK -> Pytest，不能把
“把 Mypy 移到 Pytest 前”当新优化。root full gate 在 Python owner 前仍有约数分钟
的其他组件检查；跨 OS 静态预检可在 owning 开发流程更早执行。
本机已有锁定环境上的五行合成 probe，用 Mypy --platform linux 通过0.379s、
--platform win32 在0.312s 捕捉同一 os.geteuid attr-defined 模式；无 incremental
缓存写入，未运行目标 API。该试验只证明 typeshed 平台检查能捕捉该模式，
不证明完整套件跨平台无误，也不代替真实 Windows。后续可评估在 Linux 的
Python 静态预检加 --platform win32；先核对条件分支、依赖 stubs 和 native-only
代码的误报，不在本候选扩大 gate 或抢修 Product owner。

本轮本地只读审计与获批单 CPU 合成测试；未开全 Pytest/npm/build、真实模型、
服务或 GPU，未共享 cache/venv 写入。后续若试 xdist，必须先划定含全局线程、
模块 monkeypatch、固定端口、进程 owner 的串行集合，给每 worker 独立目录并测峰值内存；
10 核/16 GiB 不能直接推导“开 10 worker”。

## 候选、实测与优先级

此次生产改动属于 G1：本地 immutable storage 的幂等路径优化，公开协议和
身份不变。CI/doc 为 G0，CI 工具变动按原 router 仍要求 full 双 OS。
未改变 component 源码、pins、lock、版本或 BOM；不创建新的数据/模型产物。

| 优先级 | 动作 | 基线/预计收益证据 | 风险与验证 |
|---|---|---|---|
| P1，本候选 | 已存在 blob 先明确 lstat存在再 bounded get/精确比字节，缺失走原 durable 原子路径 | consumer安全修复后 macOS重复发布 median12.299ms ->3.037ms/20次；每20次临时写20->0、fsync20->0 | 新 key 多一次metadata检查；需要双 OS 跟踪热点，不能外推整 CI |
| P1，下一有界试验 | 对 M0remote/budget 相同输入共享 immutable producer、每用例独立 owner/store | remote 累计311–479s Win；五 budget setup约56–73s Win，已是真实热区 | 不能共享可写 ledger/roster或伪造 provenance；未试验，节省未知 |
| P2，本候选 | plan 阶段检查实际 PR/push committed range | clean CI 裸 git diff --check漏已提交错误；负例在新预检失败 | 质量与早失败改进；成功 run 不承诺提速 |
| P2 | 在保留独立 package 语义下减少重复 TS rebuild；最早跑 Game Mod metadata check | 组件区间几十秒；metadata源码检查<1s | 不省 installed consumer/build验证，不另建版本表 |
| P2，下一有界试验 | 在 Python owning 本地静态预检评估 Linux 上的 Mypy --platform win32 | 现有 Mypy 已在 Pytest 前；五行 probe0.312s 抓到 os.geteuid 平台错误 | 不代 Windows；完整库误报/耗时尚未评估，不更改本轮 gate |
| P2 | 统一有恢复的测试 CPU thread约束，并测一真实 tiny模型/子进程 | 只有代码上的未恢复设置，未证过订阅 | 数值/进程隔离不变，不能全局改训练默认线程 |
| P3 | content-free阶段和错误分类，复用现有运行记录 | SDK根因泛化、跨任务证据重复搬运增加诊断成本 | 诊断不授权重试/提交/付费，不公开私有控制面 |
| 不采用 | 再加 timeout、删真实边界、skip Windows、缓存绿色状态或共享全局env | 无实际性能改进证据 | 降低质量或混淆来源 |

微实验用 Python3.11.15，强制被测模块路径指向本任务 clone；210000 bytes纯合成数据，
3 组 ×20次首次/同字节重发布，基线路径直接从 develop 原源码加载，
未改 checkout 或关闭 fsync。consumer安全修复后重新执行 probe：首次发布
median12.369->11.635ms/20次，两者均40次 fsync和20次临时写，差异视为噪声。
重复路径约4.05倍，绝对中位数节省9.261ms/20次，仅描述本机热路径。
峰值 RSS27.5MB，主脚本正常exit0。最初8bdafba0候选的4.96倍只对应当时
热路径；该候选随后在真实consumer负例失败，不能将其成绩沿用给修复后源码。
最初时间 wrapper 因 kern.clockrate sysctl权限拒绝退出1，未重试该拒绝动作。

55 个有界 storage/backup回归通过（含两版旧实现负例复核的脚本总时0.595s，峰值86.7MB），含首次文件/目录 fsync、并发同字节、
冲突/partial winner、删除竞争 winner fail-closed、bounds/type/只读、
原路径验证（含模拟 Windows reparse point）和 payload完整性。新增“相同对象不得再写临时文件”回归在
base原方法上确实失败，新方法通过；首次publication不读未创建key以及原backup
负例均在8bdafba0方法上失败，修复后通过。backup原七个用例全部保留并通过。
存储协议仍要求外部不要改已发布对象；读后被外部 hardlink writer 删除/替换的
保证不属于此协议，旧方法也没有该保证。此候选没有声称抵御不受控目录攻击者。

18 个 Node router/CI预检小回归通过，覆盖干净工作树上的已提交空白错误、跨多个
commit 的 PR范围、无/zero base、无效和选项形 ref、早预检顺序、双 OS aggregate。
新增预检失败直接使 plan失败、portable失败，未选叶 job跳过不会变成PASS。
初始 push 的 zero-base fallback 仅检查 tip commit；后续 PR 检查完整 base-to-merge 范围。
候选保持draft，由最终新 head 的实际双 OS/portable终态决定是否可集成。

首轮 [PR155 run37186725916](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37186725916)
head8bdafba0d63bbf2a0c4eab443110108439243ff8、actualcheckout
30b666882f99dcd088a88b54295ff39c56c1e424，在Linux失败。
pytest459.315s；JUnit2259条、1failure、4skip，唯一失败为
deploy/hub/test_backup.py::BackupTests::test_failed_remote_readback_does_not_publish_commit_manifest。
失败日志和JUnit11297476891均保存，ZIP SHA256为
bdfde8a55c2896a29fc5540b9a777a460d7b5582edf5128a658ef8a4c031be5b。
准确调用栈为 upload_snapshot -> put_verified -> LocalBlobStore.put_if_absent
-> 新快路self.get -> immutable_key_collision。该负例用真实SQLite输入，
get fault注入固定wrong，原合同是先durable首次发布，再由put_verified显式get
读回触发BackupError(remote_readback_mismatch)，禁止发布receipts/commit manifest。
不能改期望错误或删除负例来隐藏阶段回归。

修复仅在lstat明确存在时进入读取快路；只吞FileNotFoundError，其他metadata
错误传播。缺失新key不提前触发consumer readback hook；既存对象仍逐字节校验。
stat之后删除对象仍走原publication，stat之后出现竞争对象仍由原atomic link收敛。
这也说明isolated storage回归不足，低成本真实consumer fault边界应加入最早回归。
在保存上述失败证据和Windows当前phase后，parent已授权仅取消本任务的已失败run
剩余工作，再推修复源码的一次新CI；旧failure/cancelled绝不计为PASS。
本任务不取消、rerun或修改任何其他PR/run，不为同一旧head盲重跑。

## 复用操作与协调边界

开发时先跑 lowest-cost faithful owning 回归，稳定后用
npm run check:plan -- --base origin/develop；不要每改一行重跑 full。
源/test树稳定后 batch一次候选 push。Hosted正在执行 full时，不重复开同树本地full。

改 LocalBlobStore 时，最早的低成本 faithful 回归应包含真实 backup consumer，
不能只测 storage类。在已完成锁定bootstrap的 Python工作目录运行：

```bash
uv run --locked python -m pytest -q tests/test_local_blob_publication.py \
  tests/test_local_store_paths.py tests/test_artifact_store_v1.py deploy/hub/test_backup.py
```

这些用例使用合成字节/临时SQLite，无生产store或模型；本轮55例只需0.41s。
性能机制负例单独校验不创建临时文件/fsync次数，避免用易抖动的墙钟阈值作为测试。

审计下一 run：只读该 run 的 jobs/steps，下载其 pytest-OS-attempt artifacts，
核对 ZIP digest，再按 classname 累加 testcase time；用已有 portable_duration
提取 Pytest/Mypy/E2E/build。记录 PR head、实际 checkout、attempt、scope和runner，
同一 source/同一环境才可将比较视为受控。必须区分等待、进程墙钟、testcase累计；
保留失败/skips和缺失报告。此操作不取消、rerun或为 benchmark触发他人的CI。

通用 GitHub只读入口为 actions/runs/RUN/jobs、actions/jobs/JOB/logs、
actions/runs/RUN/artifacts；现有已授权 gh也可 run view/run download。
保存在 ignored .local 的报告即可，无需新增测试平台、常驻服务或另一套ledger。

跨任务将准确源码/配置/真实任务ID/已授权资源/下一门禁一次交给唯一writer。
先完成检查/构造签名/小边界probe和所有不依赖费用证据的准备，再由owner在真正
submit前读取有时效的账户/费用/权限事实，缩短 fresh evidence 到使用的距离；
复用仍有效的源码事实，避免多人循环搬运过期截图。不得延长门禁或把等待当授权，
未知任务先reconcile，不自动重复submit。一个真实有界只读观察者可跟 exact run，
owner继续独立工作；通知不是持续执行授权。

剩余限制：没有真实 Windows 本地 probe、没有 fsync 全套profile、未测整个CI
before/after、未测共享fixture迁移或并行pytest。微实验支持因果机制，但 CI阶段
收益仍需当前候选记录；runner/源树/测试集变化要单列，不能抹掉混杂因素。
