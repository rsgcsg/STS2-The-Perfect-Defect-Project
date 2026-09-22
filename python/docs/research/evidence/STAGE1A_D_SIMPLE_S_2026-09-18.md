# D-Simple-S：真实小样恢复、评测与独立导出核验

这是工程链路记录，不是策略质量或真实游戏验收。训练、恢复及本次导出评分均使用
源码 `e50076f7c2caf6a265b525420dc8db1ec1882441`；后续输入适配源码不追溯改写它。

## 固定身份与结果

| 项目 | 身份／结果 |
|---|---|
| 配方 | `stage1a.dsimple.s.v1`，MPS FP32，seed 1701 |
| 输入 | `87bec5319ecd976649ec8216d20fd4388efa9a891cba07d26a2670f61bf6421a` |
| Run | `4d9068a6ee49b2480b5e70756c884d492270ea547055aabf7f37def77946205f` |
| 第 2 步 checkpoint | `3b17b3af9c32ab660b14216b9707f63ff99f78d4d05c14ddde7568a22e4c8be1` |
| 第 10 步 checkpoint | `8b23c1551cc0b2dcbab11830ca9b51222728b1edb956c682dd6a0127f6b8091e` |
| model | `1cf0b87a8f233f3262932dfd249a67b4ce6fc2b3162815cc11e1d712f523aa49` |
| dev report | `17671671133aa96f16d10539cb85148d7b5dcf6be251efb6924ccba0c2f82f63` |
| RunResult | `831c7bce13067f87d9c5fc08d1ca6a01919b0678731b7f825d0bbc3dee487422` |
| 导出载荷 | 20,834,475 bytes，weights＋tokenizer；另有 model.json |

固定 50 train／16 dev；实际训练 10 次单决策更新，不是完整 epoch。第一次进程完成两步后
主动暂停，第二个进程恢复参数与优化器，从第 3 步完成到第 10 步，随后发布 dev 报告。
两次包装进程耗时分别为 24.65 秒和 38.06 秒，包含输入验证、检查点写盘等，不能当纯训练吞吐。
stderr 均为空。完成产物的来源链和载荷哈希已重新核验。

Dev Top-1 12/16（75%），剔除两个单候选后为 10/14；NLL 1.7151，MRR 0.796875。
dev 只有一个独立 run，不能据此判断泛化、胜率或预训练优势。现有报告保留同一评测器的
uniform/action-only 对照、family/run/candidate-count 切片和独立局数不足的状态。

## 独立加载核验

- 第 10 步恢复后的权重与发布模型的 safetensors 字节一致。
- 16 条 dev 的全部候选均检查：checkpoint engine 与导出 scorer 的最大绝对分数差为 0；
  候选反转后对应分数差也为 0。本次实测一致不等于任意硬件永远位级一致。
- 另一个新进程仅接收三文件模型和一个不带标签／后继的输入，调用正式 `score-tokens`；
  返回全部 14 个候选，与参考分数最大差为 0，没有传入训练 store。
- 新进程模型初始化约 0.171 秒，第一条完整评分约 1.646 秒；不含 Python 启动时间。
  常驻进程 16 条检查输入每条约 0.016–0.265 秒，包含 tokenization 和 CPU 取分数同步。
  这是串行功能核验中的单次测量，缓存／kernel 暖机条件不受控，不是线上延迟分布。
- 原始输入、完整日志、模型和 `export-check.json` 留在私有研究目录，不入仓库。

这没有证明真实 MPS 不间断训练与恢复训练完全相同；两个 scratch 图的该性质已由 CPU
回归检查，但本次 MPS 验证只证明真实恢复和导出一致。

## 接入游戏前发现的输入差异

现有 66 条模型输入中，56 条 `DECISION` 包含录制时的 `execution` 补充（42 条战斗状态、
14 条 domain），另外 10 条没有。部分录制动作使用 `travel`、`claim`、`choose_rest_option`
等 native 语义名称，当前公开 BoundAction 协议采用固定的公共动词集合。
这来自当前输入和 owner 源码核对，不是游戏实时 replay 已通过的声明。

因此该模型保持 `trained/exported`，不能直接改名为可在游戏运行。
`stpd.fullrun.public_inputs` 引入独立的 `stpd-public-snapshot-lite-v1` 候选输入合同：
仅使用当前完整公开快照及其完整候选，不读执行见证，不假装公开观察等同于因果 S。
`live_input_audit` 从同一固定 allocation 的原始帧核对候选语义多重集合及唯一选择映射；
失败只是该公开输入试跑的排除原因，不删除原记录或否定其其他研究用途。

先做原始记录覆盖核验，再发布新 ModelView/token 输入并重新训练四格。历史输入／模型不
原地变更。在线适配必须使用同一新投影，并继续由既有 Runtime 管理控制权、接管及 Receipt。
公共投影源码和合成测试不等于实时游戏验证；工作台选择、Manifest、NDJSON adapter、
训练视图接入和 Mod 全流程仍是后续工作。
