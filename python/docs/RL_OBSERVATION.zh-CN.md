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
产物为 `report.json`、`seeds.csv`、`paired.csv`、`distribution.svg`、`reach.csv`、
`bosses.csv` 与说明页。可加 `--through-floor 49` 展开完整楼层横轴。
图分自然死亡终局层数和 `max_observed_floor >= x` 的累计观测比例，
都以完整计划槽为分母（图例同时给实际n）。浅色带表示未决结局可能增加的范围，
不是置信区间；`reach.csv` 保留见证数、未决数及上下界。
后者不是“确实访问每一层”；未知/截断仍在分母，曲线只是已观测下界。
图例附 death-floor 的有据数量，没有终局细节时不以最高层冒充死亡层。

可为每个输入附 `--NAME-details /ABS/details.json`。
细节 JSON 可以是保存的 episode report `{"episodes":[...]}`，或只读分析
`{"rows":[...]}`，每行有 `run_id`、可选 `seed`、`observations`、
`terminal_public`、`trajectory.outcome`。原完整 P1 trajectory 也可放进 `episodes`，
复用原 validator 后投影。混合分析文件用 `--NAME-detail-cohort baseline|current`筛选。
按 run_id 联接并核已有seed/floor；不按文件顺序配对。
细节必须来自相同、已验证原件；摘要的哈希只绑定输入，不代替原件完整性校验。
未决槽可附原已验证前缀：额外见到的最高层只记入
`observed_floor_lower_bound`，原账本floor/result/status与计分资格保持不变。
前缀不得低于账本已有观测，完整自然终点冲突仍拒绝；保存失败即使有终局
公开见证也不因此获得成绩。

Boss 到达只数公开 combat.encounter_type 的 Boss 正见证；击败只数原 trajectory
`boss_clearance` 的正见证。无见证不等于证实未到达或击败0次。
`bosses.csv` 对三幕分别列全槽正见证、可计分子集正见证、细节可用数及
“有通过但缺到达见证”的数量，不把缺失补成0。每幕每局去重一次；A10
第三幕通过一个Boss不等于清幕，双Boss遭遇保留原始见证，通关只认可靠win。
每种子保留最高层、死亡层、最后一场已观测战斗与缺失标记。
最高层17不自动成为“第一幕Boss”；最后一场战斗也不自动成为死亡原因。
游戏unknown、任务operation_end.censored和证据保存失败不得混为同一个数字。
自然死亡终局同层的最后战斗类型只作上下文索引；缺少直接死因证据时，
不能认定最后战斗就是死亡原因。

吞吐与首次使用的计量复用[链路计速](RL_MEASUREMENT.zh-CN.md)；
`trajectory.step_count` 包含单候选强制行，不能直接叫游戏决策数。

## 人类、RL轨迹与混合接手使用同一尺子

纯模型评估沿上面CLI；已保存RL轨迹可作details，训练温采与固定argmax
各自成cohort，不合并为强度成绩。人类/混合数据的既有reader可调用
`progress_summary(rows, through_floor=49)` 与 `distribution_svg(cohorts)`，
只适配读出的事实，不再定义第二套统计或新原件格式。

每行保留 `status`、`floor`（公开最高层，缺则None）、可选的
`observed_floor_lower_bound`、`boss_reached_acts`、`boss_cleared_acts`、
`details_present` 及原source/root/run/seed身份。自然死亡层只有见证才填
`death_floor`；最后战斗沿原同名字段。上游负责去重、来源、哈希及观察完整性。
`scored` 只能沿原评估准入决定；人类/混合行一律False。已验证的完整自然
人类终点可显式给 `endpoint_complete=True`，只收紧进度未知上界，不授模型成绩。
结果未知的前缀不给完整终点。Boss缺失不作负证据。

人类、A0/A10和混合来源分别画线、列分母；`distribution_svg` 可直接画描述性
叠图，不能把它们交给要求同Host/难度/seed的 `compare` 冒充配对效果。
`write_report` 的模型比较协议门保持不变。接手局始终不算评估；接手片段
能否训练由用户定，报告函数不改变H1隔离与数据用途。

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

## RL3 两档看守（显式开启）

旧看守配置默认保留原行为。新 run 可加入下面字段；`evaluation_manifest`
必须指向已登记的 `managed-rl3-learning-curve-100-20261030-Defect-a0` 原清单，
`evaluation_mode` 必须为 `submit`：

```json
{
  "evaluation_tiers": [
    {"name": "quick", "seconds": 14400, "seed_prefix": 20, "wall_seconds": 7200},
    {"name": "milestone", "seconds": 28800, "seed_prefix": 100, "wall_seconds": 10800}
  ]
}
```

两档都冻结4 worker、pending8、save2。快速档使用新100清单的原前20，里程碑档
用全部100；不访问原T2预留确认20。该模式禁用旧“首胜自动跑40确认”的规则。
8小时重合时只提交100，并记录20已被覆盖；正在评估时到点，只记录该次跳过的
时刻/版本/原因，不补投。重启沿 `watch-state.json` 恢复固定墙钟时刻和原提交意图；
没有调度器回执的提交需人工核原请求，不重新提交。计划、原件、R2计分/审计门不变。

两档plan显式启用 `observation_report`（普通plan默认关闭）。原评估job在snapshot前
经原轨迹reader生成细节和cohort，计入 `timing.phases.observation_report`；
同一job保存轻量source/details/cohort及SHA，最终snapshot保全。看守只读这些本地文件
核原ledger/plan/source/details绑定，不在健康tick冷读R2轨迹；然后调用同一
`rl_observation` 生成 JSON/CSV/SVG，
保存在本评估plan旁 `report/`。同清单/前缀/角色/难度/Host/输入/动作模式才比较，
基线是同协议第一次已完成评估，另保留上一轮；没有基线或Boss见证就保留缺失。
至少已有一轮基线、连续3轮可比完整评估均未提高配对平均层数时，只在状态里提醒，
不以平台期自动停止、调参或更换训练模型。原有显式安全停止规则仍由该轮运行卡决定。

每次完整评估追加不可变 `references/candidate/` 引用。选候选规则预先固定为
`wins-Boss-CDF-paired-v1`：胜局数优先，其次有完整见证的Boss指标，再看到达分布
是否一阶支配；若CDF交叉，只有完整配对差的95%bootstrap敏感区间下界>0
才取代候选。Boss见证缺失则明确跳过Boss层转CDF，不把missing当0；分布相同、
配对区间不支持提升、unknown或计分分母不足都不自动取代已有候选。两种清单前缀各自排名。记录所有评估的选择/不选择原因和前任引用；
它是开发种子的候选索引，不是独立确认，也不删除落选模型或自动换训练模型。

每4小时追加 `references/milestone/`，绑定同一版本/更新数的已发布模型和完整CP。
引用前正常reader全读CP，核权重、optimizer、Torch RNG、learner RNG/buffer/counts、
配置和producer；CP权重还与原发布模型逐tensor核对。最新完整CP若略落后于最新模型，
引用它自身对应的准确模型、版本、更新数和发布时间，不把两个版本拼起来；没有完整
CP就保留pending。引用写入意图先落本地，外部写入结果不明时只对同一内容幂等核取。
调整前后沿同一 `checkpoint_binding` / `append_reference` 能力显式加引用，调整仍要
先按原运行规则停止并核完整存档；没有热调API，不能直接编辑在跑配置。

STOP信号先持久记录意图，每个Slurm job最多发一次USR1；即使调度响应丢失也不盲发。
本地STOP文件仍保留，R2永久STOP继续走已有幂等请求；保存/排空完成前不恢复默认信号
处理。需要补保存时，从原owner的scratch调用原snapshot接续，不重开原游戏。

计时保留旧 `elapsed_seconds` 含义，另有 `timing.process_wall_seconds`，覆盖ledger准备、
执行器准备、游戏/保存队列、关闭和最终snapshot。`submission-intent.json` 在提交前保存，
`submission.json` 记录请求/响应时间；watch收集的 `scheduler_accounting` 保留Slurm
Submit/Eligible/Start/End/ElapsedRaw，以此分开排队、实际分配和过程时间。调度器时间
不能当CPU实际忙时；`--begin`仍只表示最早开始。新root result在snapshot回执后才发布。

TensorBoard沿同一关闭后上传链增加 `histograms(values, step)` 和 `text(name,value,step)`；
可记录advantage/原始回报/复用/过期/实际采样熵分布，模型策略熵与温采分布熵分开。
当前无critic，`algorithm/value_loss` 和 `algorithm/state_value` 记文字
`not applicable: no critic`，不写数值0。`quick/`、`milestone/` 各保留评估指标，
不把不同规模的评估接成一条未标记曲线。
