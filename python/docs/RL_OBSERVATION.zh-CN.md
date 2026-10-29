# 长跑评估与只读报告

本页管已保存结果的展示和下一轮评估的操作。训练、数据准入、停止和换代仍由
[长跑运行图](RL_LONG_RUN.zh-CN.md)与[Friday 原入口](FRIDAY_ACTOR.md)负责。
读报告不会启动游戏、查询运行中进程、回写原账本或决定上线。

## 先看已有能力

- `tools/friday/evaluate.py` 已用原账本和保存池，在一个独立 Slurm 作业内开1–16路；
  `--workers` 冻结进新 plan。并行度同时受执行配置 `pipeline.pending_limit` 限制：
  只有1个待保存位时，申请8核也不能变成8路。保存线程用既有
  `pipeline.save_workers`（见[保存手册](MANAGED_PRESERVATION.md)），先量再调。
- `tools/friday/watch.py` 的 `evaluation_mode="submit"` 使用独立多核作业；
  `direct` 在看守自己的1核分配里运行。新部署按下次评估时刻提前唤醒，健康轮询仍按原间隔。
  调度、下载和当前 tick 仍会耗时，不承诺精确秒启动。
- `spireagent.rl_tensorboard` 已发布关闭后的不可变事件对象；学习者现有的
  policy/anchor loss、KL、clip、ratio、梯度与缓冲/过期指标应复用这一条线。
  当前 episode-return 策略更新没有训练 critic，不能把 value loss / explained variance
  填成0。熵与 advantage 分布未记录时也留缺口。

## 每次评估生成同一报告

输入是原 `result.json`（含 `ledger_report`），或原 ledger report。
一份 cohort 只选一个模型、角色、难度；有多模型时显式指定 `--NAME-selection`，
按 `step` 标签筛选。原整账本分母和选中模型分母同时保留。

```bash
uv run --project python --locked python -m stpd.evaluation.rl_observation \
  --current /ABS/current-result.json \
  --baseline /ABS/start-result.json --baseline-selection START_LABEL \
  --previous /ABS/previous-result.json \
  --output /ABS/new-report-directory
```

首次评估没有上一轮就不传 `--previous`，不复制起点冒充上一轮。
目录必须新建；重做报告写新目录，不覆盖历史。
产物为 `report.json`、`seeds.csv`、`paired.csv`、`distribution.svg` 与说明页。
图分自然死亡终局层数和 `max_observed_floor >= x` 的累计观测数量。
后者不是“确实访问每一层”；未知/截断仍在分母，曲线只是已观测下界。
图例附 death-floor 的有据数量，没有终局细节时不以最高层冒充死亡层。

可为每个输入附 `--NAME-details /ABS/details.json`。
细节 JSON 可以是保存的 episode report `{"episodes":[...]}`，或只读分析
`{"rows":[...]}`，每行有 `run_id`、可选 `seed`、`observations`、
`terminal_public`、`trajectory.outcome`。原完整 P1 trajectory 也可放进 `episodes`，
复用原 validator 后投影。混合分析文件用 `--NAME-detail-cohort baseline|current`筛选。
按 run_id 联接并核已有seed/floor；不按文件顺序配对。
细节必须来自相同、已验证原件；摘要的哈希只绑定输入，不代替原件完整性校验。

Boss 到达只数公开 combat.encounter_type 的 Boss 正见证；击败只数原 trajectory
`boss_clearance` 的正见证。无见证不等于证实未到达或击败0次。
每种子保留最高层、死亡层、最后一场已观测战斗与缺失标记。
最高层17不自动成为“第一幕Boss”；最后一场战斗也不自动成为死亡原因。
游戏unknown、任务operation_end.censored和证据保存失败不得混为同一个数字。

吞吐与首次使用的计量复用[链路计速](RL_MEASUREMENT.zh-CN.md)；
`trajectory.step_count` 包含单候选强制行，不能直接叫游戏决策数。

## 配对比较和解释边界

`comparisons.baseline/previous` 按共同且可计分的seed配对；未启动、unknown、
保存未完成或未通过原计分门的行保留为 unpaired。均值/中位/最高层用可计分层数的实际n。
host、角色、难度不一致拒绝比较；调用者仍需核完整Host/游戏/模型输入、
manifest和动作选择方式。没有这些事实时，报告不是受控效果结论。

报告给差值均值、样本方差、标准误和固定seed bootstrap敏感区间。
规划另区分95%区间半宽1层与双侧alpha=.05、80%power检测1层，使用正态近似。
这是假定不同seed近似独立、代表未来分布且差值方差可复用的预算估计；
小样本和后续策略可使它失准，正式比较应预先定协议并复核方差。
同一确定性seed重复执行不增加独立n；换随机动作需要嵌套重复设计。
固定20/40看趋势；更多独立评估seed的扩充要用户另定，始终排除训练。
反复看版本、挑最优会有选择偏差：开发种子挑候选，预留确认种子验收，
不把同一组种子的最好一次当无偏泛化结果。

## 下一轮的并行与定时操作

1. 使用新输出目录、新plan和独立评估分配，保留准确模型artifact/权重、清单与源码。
   在公平份额、磁盘和保存容量足够时从4路开始，再用相同固定工作量验证8路；
   不把20核理论最短值当当前工具能力。逐次保存排队、下载、执行、保存/读回和总墙钟。
2. 按现有共享资源登记流程协调；不改运行中actor/学习者的分支、作业、学校GPU或原件。
   `submit --dry-run`仍会只读查询调度器，并在本plan目录生成launch脚本，不会提交。
3. 对**已经冻结的**模型plan，可以提前交给Slurm等时刻：

```bash
python tools/friday/evaluate.py submit --plan /ABS/NEW-plan/plan.json \
  --begin 2099-01-01T12:00:00+10:00 --dry-run
# 核对后去掉 --dry-run；示例日期须替换为本轮已授权的未来时刻。
```

`--begin`必须有时区且是未来时刻，转成提交机当地时间交Slurm。
当前空闲核只是一张快照，既有公平容量门仍生效；`--begin`不是预约核，
到点可能排队。记录请求开始、scheduler eligible/start、实际游戏/保存结束，分开报延迟。
需要“到点取最新已发布模型”时，用新版原看守的 submit 模式；不提前绑定未知未来版本。

4. 有未决提交、原owner、STOP或unknown时按原请求核结果，不以新目录重投同一局。
   准备与计分继续使用原审计、保存和R2读回门；图表成功不代替它们。
5. 这些是未来部署能力。源码合并不安装、不替换任何在跑的看守或训练节点；
   何时采用由运行owner按该轮授权决定。

## 现成工具与后续选择

首选[TensorBoard](https://www.tensorflow.org/tensorboard/get_started)，已有事件写入和R2发布；
Mac只读同步关闭的对象，按hash去重，面板标明数据时刻和缺失，不做反向控制。
[Aim](https://aimstack.readthedocs.io/en/latest/quick_start/setup.html)适合以后跨run切片，
[MLflow自托管](https://mlflow.org/docs/latest/self-hosting/)适合以后有明确模型登记/多人追踪需求时；
目前迁移会增加第二个索引和运维，先不装。

独立评估池参考[RLlib的并行评估](https://docs.ray.io/en/latest/rllib/rllib-advanced-api.html)，
复用本项目原ledger，不移植框架自己的失败重试或种子策略。
[Sample Factory](https://www.samplefactory.dev/07-advanced-topics/sync-async/)和
[IMPALA](https://arxiv.org/abs/1802.01561)说明异步吞吐与策略落后需要分开考虑；
它们不是将当前训练改成V-trace的授权。
调参方案与成本由本轮OBS报告提出，学习率、熵、奖励、课程或PBT一律另行决定，
本工具不修改训练配置。
