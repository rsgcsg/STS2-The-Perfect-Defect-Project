# Project Development Workflow

The single active repository is `rsgcsg/STS2-The-Perfect-Defect-Project`.
This is the one owner of branch, release and deployment coordination. Native installation,
cloud commands and research admission remain in their focused guides; do not copy new
versions of those procedures into campaigns or incident notes.

## 日常怎么做（维护与发布速查）

**develop 收集已审查的日常改动；main 记录选定的正式交付批次。运行版本由发布清单和实际产物决定。**

| 你在做什么 | 操作到哪里结束 | 不需要附带做什么 |
|---|---|---|
| 普通功能、修复、文档 | 最新 develop 开 topic → 相关回归/检查 → PR → develop → 核对集成检查 | 不顺手开 release PR、重启 Hub 或要求成员升级 |
| 正式交付一批功能 | 选定 develop → 临时 release 分支 → main PR → 固定发布清单/原样产物 | 不把之后继续前进的 develop 自动带入候选 |
| 只更新 Hub | 已审查源码、锁和镜像 → runbook 预检/备份/部署/观察 | 不重编 Mod，不重新登记电脑 |
| 更新本机工作台或 Mod | 选择已验收发布，按受影响的安装/队列升级入口操作 | 不覆盖 pending 数据，不复制账号凭据 |
| 紧急修复当前正式版 | 从受影响发布/main 开 hotfix；经 PR 修复，必要时回流 develop | 不在服务器直接改源码作为最终修复 |

日常三个固定入口：`check:plan -- --base origin/develop --run` 选择检查，
`project:closeout` 提醒影响，PR 完成审查和集成。命令前均为 `npm run`。
开发过程中先跑最小忠实回归；待改动稳定后再跑计划检查，不在每次改一行后重复全仓。
普通 topic PR 总是执行所选检查；合并/正式晋级允许按 TESTING 的回执规则复用同内容的已执行结果，
并重新核对当前 Git 身份。手动 full 是强制重新执行入口。

两个分支树相同时，不因 merge ancestry 或 SHA 不同开空同步 PR。
main 有独有热修复时才通过 PR 带回 develop。组件源码继续普通 merge，保留来源。
不要把 main 当生产服务开关，也不要移动旧 release tag 覆盖历史。

维护频率按需要：日常看现有 System 健康/容量/失败任务；团队每周集中看依赖、安全、CI慢项和备份新鲜度，
需要时安排恢复演练。确认的安全或正确性问题及时处理。正常机器继续使用已验收固定版，
没有“每个提交都发布/每周所有人都重装”的要求。

发布负责人先让本批候选收敛，再一次性完成所需发布流程。发现新缺陷就修复并重验受影响部分，
不要在仍不断修补的同时对每个中间提交启动完整 main 晋级。只更新流程规范的任务通常到 develop 结束；
如果任务明确包含正式发布，则完成 main 与发布回执。不要为了演示流程去重启无关生产服务。

## Branches: integration, publication and work

| Ref | Purpose | What it does not do |
|---|---|---|
| `develop` | shared integration of reviewed changes | automatically deploy a Hub or update collectors |
| `main` | reviewed release/integration record | claim that every running machine executes its latest SHA |
| one short-lived topic branch | one task/repair and its PR to develop | become a permanent component or engineer branch |
| temporary `release/**` or `hotfix/**` | prepare an explicit main promotion or urgent correction | create a second long-lived development line |
| immutable release tag / exact artifact | an identified distributable candidate and its evidence | silently follow a branch or overwrite an older artifact |

Main and develop are the only long-lived branches. Fetch/prune and inspect status, exact
refs and open PRs before work. Start normal work from current origin/develop, with one
writable branch/worktree per writer. Never edit the running collector checkout, direct-push
main/develop, force-push, bypass required CI or reuse an already merged topic branch.
Root AGENTS.md and Engineering Governance own G0-G6; historical STPD classes are not a
second governance system. A PR may cross components when one causal change requires it.

## Normal change and release sequence

1. Create a topic branch from current origin/develop; record exact base/head and owner.
   Implement the first owning correction, add the cheapest faithful regression, run the
   relevant component/root gates, closeout and diff review. Open a PR to develop.
2. Review the latest head and its selected CI scope/portable result. If integration requires a
   newer base, merge that base into the topic branch, review conflicts and revalidate.
   Use a normal merge commit for any component source change; docs-only squash remains
   permitted by root policy. Preserve path-scoped component provenance.
3. Merge the reviewed PR to develop and verify the actual merge-head CI (fresh checks or a verified execution receipt plus current identity checks).
   This completes an ordinary task unless publication/deployment is explicitly in scope. No service is
   deployed merely because that merge happened. Several compatible improvements may be
   grouped into one intentional release; do not make every commit a user update.
4. When promotion is intended, create a temporary release branch from the exact selected
   develop commit and open a PR to main. Merge current main into that release branch if
   needed for strict up-to-date rules. Review the final diff and latest checks. If main
   contains a hotfix absent from develop, first reconcile it through a PR to develop;
   do not allow independent implementations to diverge.
5. For changed executable artifacts, build one immutable candidate, qualify affected
   build/install/runtime/Human/cloud boundaries and retain rollback before recommending
   promotion. A docs-only release needs source/doc checks, not a new Mod, image or canary.
   An explicitly authorized candidate deployment stays identified as a candidate until
   its own gate passes. Never use a failed Human gate as a green release receipt.
6. Merge the release PR normally and verify exact main CI under the same receipt/identity rule. If stabilization/hotfix changes
   occurred only on the release/main line, synchronize them back to develop through a PR
   and check its merge head. When main/develop trees already match, a merge-only ancestry
   difference does not require an empty synchronization PR.
7. Publish the same verified bytes and their source/lock/artifact/compatibility/rollback
   receipts. Download and verify the published files. Delete only merged temporary
   branches after checks; retain detached runtime/evidence worktrees and historical tags.

Urgent fixes start in a new hotfix branch from the actual affected release/main ancestry,
use a PR to main and then a PR to develop where needed. Emergency authorized pause/revoke/
rollback may contain an incident, but direct host edits are not the final repair: follow
with an owning source/config-template PR and regression. Preserve evidence throughout.

## One source repository, independently pinned running components

Use a development worktree for changing code and a durable detached release worktree for
collection. The Hub runs an exact OCI digest and external private configuration; workers
are independently selected profiles of the same codebase. Models are separate immutable
artifacts. These components may run different compatible release SHAs. Equality to current
main, to another computer or to the Hub is not a general compatibility requirement.

| Thing | Normal update boundary | Evidence to retain |
|---|---|---|
| docs/governance | reviewed PR and main promotion when desired | source/CI; keep working runtime producer unchanged |
| local Workbench | deliberate application release, owned process stopped | old/new source+lock and private configuration |
| Mod / fixed collection tool | affected native/tool compatibility and explicit upgrade | game/Mod/tool identities, old queue/tool, cold-load and affected canary |
| Hub | operator schedules a compatible immutable image rollout | old/new image/profile/config, schema, backup and service receipt |
| worker/provider | separate compatible worker qualification and authorized budget | producer, input/job/attempt/checkpoint and provider result |
| model | explicit artifact/adapter/environment admission | weights, representation, support, training/evaluation identities |
| dataset | new immutable selection or derived version | original bytes, policy, included/excluded IDs, dedupe and split |

The current Python producer records an exact whole-checkout SHA plus lock hash; native
component identities are path-scoped. Do not confuse those provenance schemes or rewrite
an installed producer because documentation changed. A source edit to docs inside python/
does not require rebuilding or restarting a healthy deployed executable.

## Compatibility and update policy

Record versions and exact origins; decide compatibility using the actual public contracts,
capabilities and affected native facts. See [Versioning](VERSIONING.md). Do not invent a
second compatibility database, infer arbitrary-version support or remove existing exact
native/queue checks to make a mismatch disappear.

A working qualified installation stays pinned until an operator chooses an update for a
needed capability, an observed compatibility failure, a correctness defect or a security
issue. Review dependency/security findings at a regular team maintenance checkpoint and
before releases; triage urgent exposure promptly. Do not run automatic dependency upgrades,
`npm audit fix --force`, restart loops or client self-updaters as a substitute for review.
There is no blanket promise that old software is safe forever or that updates cannot fail.

A game update does not by itself require updating Hub, research, all models or all users.
First inspect the actual native load/read/action/recording compatibility; an unsupported
native check remains blocked until qualified. A model/record format change affects consumers
that use that contract. Data from multiple versions may be selected only under explicit
compatible semantics and recorded policy; a code-version mismatch alone is not a reason
to delete data. Schema IDs, hashes and lineage remain immutable.

Software-only changes do not create another member, device or daily consent. Changed
purpose/sharing requires explicit consent under the owning collection policy. Existing
outboxes retain their original tool; completed-queue generation rollover uses the documented
collection-upgrade command, never an in-place rewrite of pending data.

## Small-team operation and distribution

Hub owns the only project member roster. GitHub collaborator permissions and host/cloud
operator credentials are separate; share neither administrator credentials nor private
profiles with collectors. The existing Hub/domain/R2 are shared services, not something each
new engineer bootstraps. Keep one production scheduler; isolate staging/synthetic state and
keep compute budget zero without an explicit compute authorization.

Use the [member handoff](NEW_MEMBER_HANDOFF.zh-CN.md) for onboarding, the
[collection procedure](../python/docs/B_PIPELINE_HANDOFF.md) for local setup/upgrades,
and the [Hub runbook](../python/deploy/hub/RUNBOOK.md) plus
[operations guide](../python/deploy/hub/OPERATIONS.md) for deployment/recovery.
A release note links these owners rather than duplicating their commands.

The normal member path is reopen the same Workbench profile, record, Close, inspect the
queue and verified remote receipt. No mandatory topic activity or recurring registration.
Keep one accepted developer kit distribution; models download separately. The current kit
still needs operator-assisted native installation, and the Workbench has no installed OS
autostart/update service. Do not claim a one-click installer or remote game control.

Healthy-day maintenance is the existing System capacity/backup view plus waiting/failed
operations and recording quality. On an incident preserve exact source/game/Mod/tool/image,
session/receipt IDs and redacted reasons; report one owning failure mechanism in the new
repository. Automatic issue creation/off-host notification is not currently guaranteed.
After reboot check service identity, mounts, TLS, backup and queue continuity; a missing
status means unknown, not success. Failed decisions remain diagnostic input to repair,
while independently eligible decisions can remain useful under the dataset contract.

Before deployment use the existing preflight, verified off-host backup and compatible
rollback pair. Check both source paths and persistent bind mounts/systemd references when
moving a checkout. Restore into a fresh paused directory and reconcile revocations/unknown
external work; do not restore a DB merely to roll back application code or prune data volumes
for disk space. The deployed source may remain an older accepted release while this guide
advances; operators consult the approved current runbook and actual deployment identity.

## Checks, cleanup and traceability

For initial/full environment validation: npm ci, npm ci --prefix python, npm run setup:python, npm run check.
For subsequent tasks use npm run check:plan -- --base origin/develop --run;
TESTING.md defines editorial, Python-owner, full and verified integration scopes plus native gates.
Run npm run project:closeout and git diff --check; match higher gates to TESTING.md.
Review contracts/BOM/pins/version/ADR/docs only where affected. Record exact tested head,
actual evidence, rollback and non-claims in the PR. Source merge never creates Human or
scientific evidence; a flaky retry is not proof that the first failure was harmless.

The initial history import and legacy retirement are complete. The old repositories are
archived; their releases, identities, commit histories and evidence remain for consumers.
Remove obsolete instructions and proven-unused implementation only after checking concrete
callers/tests. Retain schema adapters, package pins, wire/database names and rollback paths
with actual current or archival consumers. Do not rename stpd-prefixed persistent state to
make the tree look new. Keep raw recordings, credentials, models and installed artifacts
outside Git. New work and incident reports belong in this repository only.

## 长任务与用户交接

主要训练、完整 CI、长编码／构建启动后，确认任务可脱离当前对话持续运行并且日志可查，
就结束当前回复并交接，不持续轮询。预计超过约两分钟的等待默认采用此方式；短回归可当场完成。
若进程不能可靠存活，给出精确的人类启动命令，不能只留下会随会话消失的进程。

交接必须包含：任务名称/ID、源码或数据/配置身份、当前状态、日志路径或页面、
最少人工步骤（例如保持电脑唤醒）、成功/失败/需要操作的识别方式，以及下次恢复的动作。
用户可回复“完成，继续”或“失败，检查”，Agent 再自行核对结果，无需用户抄长日志。
不自动启动下一批主要训练；阶段预算和原有授权仍有效，不重复索要已授予权限。
等待期间没有审查、合并或运行成功声明；正常检查、精确身份、Human 与部署 gate 不因交接减少。
后续 Agent 先核对相同任务的终态，保留失败/取消/unknown，不能以重新启动替代恢复。
本机一次性任务必须明确禁用退出后自动重启；macOS 使用显式 RunAtLoad/KeepAlive 配置，
不要把 launchctl submit 当作一次性任务保证。原结果防覆盖仍需保留，但不能用它代替正确的进程生命周期。
