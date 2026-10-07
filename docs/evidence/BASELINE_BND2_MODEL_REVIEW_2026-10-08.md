# 暂定BND-2与两个M2设计：审查记录

日期：2026-10-08。source基点`638b7698a9279788f9a83e2e2971ba0cf86969bc`。对象：[双模型详解](../design/BASELINE_BND2_TWO_M2_MODELS.zh-CN.md)。用户暂定BND-2，不是完整D01/模型/G1接受。本轮只做设计与源码核对，没有执行数值训练、生产模型实现或游戏操作。

## 来源与范围

- 核对现行根指南、README/CURRENT/Architecture/Components，沿用同一设计分支与文档治理。
- 直接重读[LightAction](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/638b7698a9279788f9a83e2e2971ba0cf86969bc/python/stpd/models/light_action_m2.py#L140)的page/feedback core、固定槽writer、独立候选编码及只读score。Model-T来自现存图，但新展开renderer/事件接线不是已完成实现。
- 独立模型审查补充Entity/Field/Relation tensorizer、ragged字段、已知mask、顺序/集合、候选role gather、past-action语义快照、cache/参数与训练梯度条件。Model-S为新图，旧checkpoint不自动兼容。
- [Deep Sets](https://arxiv.org/abs/1703.06114)及[Set Transformer](https://proceedings.mlr.press/v97/lee19d.html)作为集合排列与交互的设计参考；未移用其benchmark成绩或声称已实现其全部架构。

## 设计检查点

1. BND-2共用收发/去重/分页/等待机制，AgentSpec定义实际输入消费、时机与选择。共享代码可以位于Agent进程，不新造固定服务层数。
2. “完整”分别指当前页、资料可达、当前C、结构语义、声明历史和模型投影；不等于全游戏隐藏状态或一次发送所有页面，当前source还未获全范围资格。
3. T接状态tokens、W、先前实际输入、可用反馈和完整候选；feedback另core、候选单独byte-CNN，不能宣称严格一次Transformer或无候选成本。
4. S保留实体局部文本、typed数值/缺失、公开关系及必要顺序。候选动作引用当前E再读W，避免K1独自无损压缩全部实体；候选不写E/W。
5. 候选ID/行号仅绑定；不当语义embedding。先前动作引用可能消失，保留当时公开语义，不按同名补绑。多参数/无subject动作都有表达方式。
6. 全场景有具体实体/关系/动作映射；新必需字段不支持时诚实拒绝。可变数组不是运行覆盖，Model-S没有实测性能或能力。
7. 两图各自训练与AgentSpec；相同外层条件比较设计，不能将不同decoder/current-state读取冒充严格单变量表示实验。

## 验证与独立review

完成设计后独立审查返回bounded PASS / READY_FOR_OWNER_DISCUSSION，无新增P1/P2；核读主文档、当前方向/数据边界、LightAction/动作encoder及路由。主文档SHA256为`8875fc7f656b4452f5ca5dd78ac36884afac13783894fec4ea92d9575f9e29d0`，与root一致。检查范围为可讨论设计，不是模型数值或G1批准。旧RC2的7组文本预算仍限原fixture，不能当结构模型性能证据。

本地check:docs的17个project/governance fixtures、零warnings通过；project:closeout与diff hygiene通过。29份纳入文件的SHA均核对，独立review也核对一致；审查回执填写后行政重生manifest。没有新增数值或runtime测试，不将设计诊断清单算执行结果。
