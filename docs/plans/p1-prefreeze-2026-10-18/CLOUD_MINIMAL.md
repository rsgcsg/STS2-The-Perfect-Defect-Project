# K5 云最简方案（只读调查；2026-10-18）

**结论：直接复用现有 storage 库和 `push/pull`，不把 Hub、团队账号或云页面设为第一轮训练的前置。** 现有 R2 字节保全有真实旧验收；Modal 有真实 GPU 运行证据，但当前 P1→R2→Modal 的新候选路径未验收。冻结第 9 条仍进行中：新冻结英文原件 **0/3**，待 B 给最终 manifest。当前只交方案，没有上传、云写、Modal 调用、GPU、原始 Human 正文读取或删除。

历史查过：Round19入口、冻结提案0.5/4.9、全景、卡K K5、K1账本/K2地图、Q3第3版；复查旧B云流程、实际09-13至09-18验收及10-15 J-min失败/观测原报告。代码读取身份 `eafa9a7c6cb737548bccb2f3856336f0050ab979`；root后报PR204合入`ea7c`同树，本报告不以转述建立新服务资格。

最简链路是：**冻结原始/P1记录与来源清单 → 现有 ArtifactStore → R2 → 同库完整回读 → Modal只读同一SHA → 冻结后数据投影/训练 → 现有注册/Agent/评测链**。R2/Modal提供存储与算力，传输不会决定Human来源或训练入选。

1. 原始记录、P1记录分开保存，绑定同一session、冻结候选、转换器与协议身份。已有Artifact用CLI；目录用薄的一次性流式包装调用现有`put_payload`，不造新云平台、不改公共schema。
2. 存储已有 **8MiB块、SHA256索引、逐块+整payload回读核验、manifest最后发布**。`push/pull`直接支持`s3`，不需要登录Hub。大根不要走内存`archive_bundle`（256MiB/20000文件），也不要用`copy_artifact`临时落整40G payload；直接流式库调用、逐组归档与流式完整回读即可。
3. Modal只需一个单次、有界CPU读探针：现有受限storage Secret + 同一锁定源码/库，读取指定manifest/payload，流式计算SHA/长度并返回元数据。**本轮不实现/部署/执行**；这一步与GPU数值资格、Q3正式训练分开，上传及新计算须用户批准。现行`cloud_jobs`仍是旧FeatureJob/FeatureSet/TrainingRun派发器，不能直接冒充新P1训练消费者。
4. Hub、Cloudflare登录、campaign/团队账号、Workbench云页面全部保留冻结；仅在地图写明职责。薄包装和探针估算0.5–1工作日，前提是B产物、凭据授权、精确镜像可用；未实测，网络时间及新训练适配另计。

实际本地验证：25,165,855字节合成数据经现有库分4块，现有CLI push/pull退出0，回读完全相等，破坏一块后明确报`payload_chunk_integrity_failure`。这是系统Python3.9的局部机制检查，**不是锁定3.11项目门或R2/Modal实测**。临时fixture已随TemporaryDirectory清理；首次receipt路径误写导致写报告失败，本文件按已打印结果补存，没有重跑。见局部证据：`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-K5-20261018/local-storage-proof.json`。

## 组件与实际证据表

| 部分/链路位置/现行入口 | 最后查到的真实成功及身份 | 好用、混乱与未验范围；冻结前去向 |
|---|---|---|
| **R2**：私有S3兼容不可变对象存储；原始/P1记录保存→数据消费→训练产物保存。`open_store("s3") / ManifestArtifactStore / Workbench push,pull` | 2026-09-18 S01共享ArtifactStore发布/17节点Hub索引；直接R2逐字节独立校验明确见2026-09-15；S01 producer d0b061ab; Model24cea626;09-15 STPD d478ec10 / archiveSHA24c88f87。[E07](../../../python/spireagent/storage/store.py)、[E08](../../../python/spireagent/storage/s3.py)、[E09](../../../python/spireagent/storage/config.py)、[E10](../../../python/spireagent/workbench/__main__.py)、[E15](../../../python/docs/evidence/B_DEFAULT_COLLECTION_HUMAN_AUDIT_2026-09-15.md)、[E20](../../../python/docs/research/evidence/S01_2026-09-18.md) | 字节保全、按SHA存取有旧实证；当前源库支持8MiB chunk流式读写；云上传成功≠来源/数据/训练入选；当前P1未实测；当前shell无S3环境变量，私有配置文件存在但未读取。**保留，最简主路径**。 |
| **Modal**：有界一次性远程计算容器/GPU提供者；冻结输入→远程读数据/训练→产物回存。`现行deploy/cloud-worker/modal_app.py + stpd.cloud_jobs.ModalProvider；旧PR181另外一套Structured remote service未在所查HEAD` | 2026-10-15 obs08真A10两步数值观测、原SUCCESS输出取回与stopped/tasks0/containers0；普通training service无成功验收；旧6cc2a0d7;image im-I1q5U3MP0EnlZ3ydu6H3Mk;call fc-01M4XVHQMG0171P2CHVT8ETRKB;Linux/Torch2.13.0+cu130/CUDA13.0/A10。[E12](../../../python/deploy/cloud-worker/README.md)、[E13](../../../python/deploy/cloud-worker/modal_app.py)、[E14](../../../python/stpd/cloud_jobs/contracts.py)、E21 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/J-min/reviews/independent-obs08-result-and-repair-boundary.md`)、E22 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/J-min/q07-numerical-failure-readonly-diagnosis-2026-10-15.md`)、E23 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/J-min/pr-body.md`)、E04 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/Q3_BATCH1_PLAN_2026-10-17.zh-CN.md`) | 真实provider/GPU可用有证据；不能升级成现行P1训练资格；q07 raw-weight FAIL，obs08 raw-score24项FAIL；旧分支与当前旧FeatureJob派发器不同；Q3批准L4基线不等于A10数值验收继承。**保留接口；先只读SHA探针提案，GPU/训练另批**。 |
| **Hub服务器**：CPU应用服务，持久operations SQLite、上传核验、成员/任务/预算调度；可选的投递/目录/协作管理层。`python -m spireagent.hub；python/deploy/hub RUNBOOK/OPERATIONS` | 2026-09-18 S01现有Hub索引17节点、用途登记；09-15 HTTPS/镜像/数据集读回有exact记录；09-15 2aa69bdf / OCI238338e7；S01原发布producer bafef714。[E17](../../../python/docs/evidence/DECISION_DATASETS_CLOSEOUT_2026-09-15.md)、[E20](../../../python/docs/research/evidence/S01_2026-09-18.md)、[E28](../../../python/docs/CLOUD_PIPELINE_B.md) | 旧部署真实用过；运行服务身份与仓库身份不同；包含上传、核验、索引、训练编排多职责入口；现部署未联机核验。**保留冻结，首训最简传输不依赖Hub**。 |
| **Cloudflare登录**：云Web前端Access JWT/email OTP身份层；人→云console访问，不是R2 SDK或Modal凭据。`Hub console_auth.py / existing Access应用` | 2026-09-13真实认证云浏览器+现账号/设备/记录一致；09-14新成员登出重连另有记录；974c7704初次批准；3555f096现账号连续性；a5593758 schema4成员重连。[E18](../../../python/docs/evidence/B_UNIFIED_WORKFLOW_RELEASE_2026-09-13.md)、[E19](../../../python/docs/evidence/B_MULTI_PROFILE_REVIEW_2026-09-14.md)、[E25](../../../python/spireagent/hub/console_auth.py)、[E27](../../../python/docs/IDENTITY_PROTOCOL.md) | 旧真实登录有实证；OTP、Hub成员、设备token、SSH、R2/Modal凭据相互不同；不能用云登录推断上传许可。**保留冻结，不修登录或团队系统**。 |
| **Evidence投递服务**：Recorder Close后固定工具打包核验、durable outbox、上传及receiver receipt；独立记录→传输/收据；不制造Human事实或训练资格。`python -m sts2_platform_evidence.delivery_cli / DeliveryConfig / inspect_delivery_status` | 2026-09-15新Human在途至自然败局Close→Hub verified receipt；此前同日完整四角色R2读回；平台3da01ecb/STPD67530981新记录见E16；完整回读d478ec10见E15。[E15](../../../python/docs/evidence/B_DEFAULT_COLLECTION_HUMAN_AUDIT_2026-09-15.md)、[E16](../../../python/docs/evidence/B_WORKFLOW_BOUNDED_ACCEPTANCE_2026-09-15.md)、[E24](../../../components/evidence/DELIVERY.md)、[E28](../../../python/docs/CLOUD_PIPELINE_B.md) | 旧trace4/bundle3链路实际可用；原始Source4/P1新格式不能假设旧fixed tool/Hub verifier直接接收；新大根超旧archive限额。**保留旧链路；本轮原件存取直接复用storage库，不改delivery协议**。 |
| **Workbench云页面**：现有项目console的云端映射；呈现账号/采集/任务/模型状态，调用业务owner。`python/docs/PROJECT_CONSOLE.md / Hub console_routes` | 2026-09-13真实浏览器accessibility tree确认云内容；09-15 exact Hub/本地冷启source记录；3555f096浏览器一致性；2aa69bdf/OCI238338e7后续服务记录。[E18](../../../python/docs/evidence/B_UNIFIED_WORKFLOW_RELEASE_2026-09-13.md)、[E17](../../../python/docs/evidence/DECISION_DATASETS_CLOSEOUT_2026-09-15.md)、[E20](../../../python/docs/research/evidence/S01_2026-09-18.md)、[E26](../../../python/docs/PROJECT_CONSOLE.md) | 旧页面实际访问过；当前像素/业务旅程未验；网页源码提交≠生产部署（S01报告明确未部署新网页）；状态不同步易误认为失败。**保留冻结薄映射，K6另定位**。 |
| **campaign**：固定采集活动、记录目录/worker、来源声明/上传/共享同意及研究预算活动各有owner；采集授权/来源绑定与后续明确研究使用。`collection-campaigns/ + workbench campaign_prepare + Hub campaigns；Q3研究计划` | 2026-09-15 daily-v2新成员采集绑定与收据；10-15 J-min有独立synthetic预算台账；d478ec10 daily v2；J-min6cc2a0d7 synthetic USD1/600s旧窗口。[E15](../../../python/docs/evidence/B_DEFAULT_COLLECTION_HUMAN_AUDIT_2026-09-15.md)、[E19](../../../python/docs/evidence/B_MULTI_PROFILE_REVIEW_2026-09-14.md)、E23 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/J-min/pr-body.md`)、E04 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/Q3_BATCH1_PLAN_2026-10-17.zh-CN.md`) | 活动与凭据绑定有旧实证；采集campaign、研究experiment与云预算campaign名称相近，批准一项不自动批准另项。**现有保留；本轮不新建活动、不代签同意**。 |
| **账号/设备**：Hub member/admin、个人session、durable device registration；云提供商账号独立；谁能操作/看什么/谁的来源/用途。`IDENTITY_PROTOCOL.md / Hub identity,membership / Workbench profile` | 2026-09-14两账号两设备schema4同机logout/reconnect保留原token；09-15新成员采集访问成功；a5593758；d478ec10。[E19](../../../python/docs/evidence/B_MULTI_PROFILE_REVIEW_2026-09-14.md)、[E15](../../../python/docs/evidence/B_DEFAULT_COLLECTION_HUMAN_AUDIT_2026-09-15.md)、[E27](../../../python/docs/IDENTITY_PROTOCOL.md) | 旧真实账户连续性有证据；一台Mac可有多个registration；本地profile不是OS隔离；成员角色不等于云账单权限或数据使用许可。**保留冻结，不建团队/账号新系统**。 |

这里的“最后”限定为本轮查到的原件范围，没有联机确认现部署。用户“R2/Modal好用、其他难用”的经验有旧成功链支撑；能确认的摩擦是多身份、多入口、旧流程与新数据/训练接口并存、源码与部署混淆。没有凭一次只读调查测量当前UX，不能据此给所有云部分下好坏定论。

## 迁移表（提案，未上传）

| 来源 | 本轮metadata实测 | 提议私有R2 prefix | 校验/回读/本地保留 |
|---|---:|---|---|
| `/Users/fire/.local/share/spireagent/research/s01-stage1-20260918` | 27,097,484,865字节逻辑 / 27,124,170,752字节分配，11,197文件 | `k5-archive/2026-10-18/s01-stage1-20260918` | 源metadata前后无变化；remote read_payload完整流读并核每块/整payloadSHA；恢复tar成员与源inventory逐文件SHA/长度一致；只验证不覆盖源。**全部原地保留；本地删除另需用户批准**。 |
| `/Users/fire/Desktop/SpireAgentProject/SpireAgent/Re-SpireAgent/data` | 16,159,458,050字节逻辑 / 16,273,670,144字节分配，55,816文件 | `k5-archive/2026-10-18/re-spireagent-data` | 源metadata前后无变化；remote read_payload完整流读并核每块/整payloadSHA；恢复tar成员与源inventory逐文件SHA/长度一致；只验证不覆盖源。**全部原地保留；本地删除另需用户批准**。 |
| `本轮冻结候选英文原始记录` | 待B最终manifest；未齐，不能猜大小 | `p1-freeze/<candidate-identity>/<session-id>/raw` | 冻结源manifest/逐文件SHA→R2完整回读一致→Modal只读同SHA。**全部保留**。 |
| `同局P1转换记录/转换与严格重放报告` | 待B最终manifest；未齐，不能猜大小 | `p1-freeze/<candidate-identity>/<session-id>/p1` | 不可变P1记录/源引用/转换器身份及SHA→R2回读→Modal同SHA；不据传输判训练入选。**全部保留**。 |

两历史根合计 **43,256,942,915逻辑字节（40.286GiB），43,397,840,896分配字节，67,013文件**。本轮仅stat，不读数据正文，不生成源内容哈希，不改变源。mtime不能证明无人写入；批准执行前要确认写者停止并以同一清单前后核对。它们是历史保全对象，不是自动训练数据。

实际对象沿现有结构：`<prefix>/objects/sha256/<chunk_sha256>`、`payload-indexes/v1/<payload_sha256>.json`、`manifests/<artifact_id>.json`。批准后对原件生成逐文件长度/SHA清单，直接tar流进入`put_payload`（不落40G中间包），再完整远端流读并逐成员核对清单；现有evidence manifest容纳归档payload/清单，记录这是保全而非训练入选。tar流包装/恢复核对是尚需的小工具工作，不声称已有CLI接收任意目录。

按[R2官方Standard价目](https://developers.cloudflare.com/r2/pricing/)核对的粗估：44GB保存一整月、未扣免费额约 **USD0.66/月**，请求费另外计、tar清单开销另计，不是账号账单或支出批准。迁移至少上传43.257GB并完整回读43.257GB；当前网速没测，不能承诺腾盘时间。所有本地原件继续保留，因而**本方案当前释放0字节**；以后删除仍需用户另批。

## 供review的命令/库路径（未执行云操作）

从批准的精确clean checkout的`python/`运行；现有命令不需要启动Workbench GUI。实际凭据通过已有私有环境加载器/SDK链供应，不能`cat`、`env`、`set -x`或把值放进参数；本轮只确认`~/.config/spireagent/cloud/r2-modal.env`、`r2-hub.env`存在且mode0600，`~/.modal.toml`存在，均未读内容。当前shell的所查S3/Modal凭据变量均未设置；repo本地`.venv`不存在，不能宣称即刻可联机执行。

```sh
# 准备环境；会安装依赖，只是计划
uv sync --locked --extra cloud --extra research
# 仅对用户明确批准的artifact闭包执行；push会云写，pull会完整读回并写本地
uv run --locked python -m spireagent.workbench push --store /ABS/APPROVED_LOCAL_STORE --other-store s3 --artifact APPROVED_ARTIFACT_ID
uv run --locked python -m spireagent.workbench pull --store /ABS/NEW_READBACK_STORE --other-store s3 --artifact APPROVED_ARTIFACT_ID
```

已有artifact闭包可能包含原始父源，执行前按exact ID核清单，不能只批准一个model名就自动上传所有祖先。大根流式回读不应落完整副本：

```python
# 同一方法既可在已授权本机也可在未来已授权Modal CPU探针使用；本轮未运行
from spireagent.storage.config import open_store
store = open_store("s3")
manifest = store.get_manifest(APPROVED_ARTIFACT_ID)
for payload in manifest.payloads:
    for block in store.read_payload(payload):
        pass  # read_payload完整迭代后已核每块、长度和整payload SHA256
```

不使用全库`doctor --store s3`代替指定对象验收，不运行会写探针的`doctor --smoke`。Modal环境/Secret的已有CLI只证明配置元数据；真正云门须将同一冻结候选原始/P1记录完整回读并由远端返回相同SHA。本轮没有这一结果。

剩余仅三项：B最终新英文记录及P1产物；用户批准精确迁移清单/目的prefix/费用与上传；另批一次有界Modal CPU读验证。Q3第3版只批准未来L4一次基线实测，训练配置、消费者适配与后续预算按冻结后流程处理，不借旧A10诊断消耗授权。

机器可读字段与全部证据路径：`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-K5-20261018/K5-cloud-minimal.json`；新鲜目录元数据：`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-K5-20261018/large-data-metadata-fresh.json`。
