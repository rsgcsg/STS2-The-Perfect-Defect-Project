# develop 合并队列：用户设置单

**当前先停在资格门槛：2026-10-28 实查仓库是个人账号名下的公开仓库，不能直接启用。**
GitHub [官方资格说明](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)
要求组织名下的公开仓库（私有组织仓库另需 Enterprise Cloud）。公开本身不足够。
用户须先决定是否迁移至自己管理的组织；本卡没有迁移授权，不要为找勾选项删除保护规则。
迁移会改变规范仓库地址并需核对集成/权限/其他卡；这是待决定项，不属于下面的点击授权。

具备资格且 MQ 的 CI PR 已普通合入 develop 后，再按以下步骤操作；设置仅由用户完成。

1. 打开仓库 **Settings → General → Pull Requests**，保留 **Allow merge commits**，
   勾 **Allow auto-merge**（当前关闭；用于检查未结束就申请自动入队）。保存。
2. **Settings → Branches → Branch protection rules**，编辑准确匹配 `develop` 的现有规则。
   不用通配符，不改 `main`。若保护由 Rulesets 管理，编辑准确匹配 develop 的有效规则集，
   避免保留另一条重叠规则继续强制旧分支同步。
3. 保留 **Require a pull request before merging**、**Require status checks to pass before merging**
   及必需状态 **portable**；保留对话解决、管理员约束及禁止强推/删除。
   勾 **Require merge queue**。不要启用 Require linear history，它与普通 merge commit 冲突。
4. 队列 **Merge method = Merge commit**，不选 Squash/Rebase；文档 PR 也统一普通合并。
   **Build concurrency = 1**；**Only merge non-failing pull requests = 开**；
   **Status check timeout = 90 minutes**（覆盖现有 55 分钟产品任务和规划/聚合余量）。
   **Minimum pull requests = 1、Maximum = 1、Wait time = 0 minutes**；
   若界面最小等待是 1 分钟则填 1，最小组数 1 不需要凑批。
5. 在该规则内取消 **Require branches to be up to date before merging**；保留必需状态检查。
   队列负责基于最新 develop 验证组合，继续 strict 会保留人工同步门槛。保存。
6. 回来告诉 MQ “设置完了”。MQ 先只读核对设置，再开一个真正的小文档 PR，
   审查后申请入队，记录 merge_group 的 portable、队列合并提交/父链、合并后 push CI 与 BOM。
   在这条真实链通过前，报告只能写“已配置/待验收”。

日后 PR 审查好后由对应卡运行：
`gh pr merge <PR> --auto --match-head-commit <已审查的完整提交号>`。
[CLI 说明](https://cli.github.com/manual/gh_pr_merge)：队列目标无需指定合并策略，
检查未过时开启自动合并，已过时直接入队；策略由队列设置控制。命令成功不等于已合并。
组合内容变化、真实冲突、失败/超时会重新检查或出队；保留原失败，修原因再入队，不手动求绿重跑。

已有 PR 无需为了 merge_group 增加提交；如自行改过 CI/router，先核合并后的有效配置。
用户未批准组织迁移、没有队列开关、或自动合并不可用时停止启用，保留原保护与普通合并流程。
