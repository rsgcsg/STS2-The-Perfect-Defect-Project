# 大视图／查询协议与I/F审查

日期：2026-10-08。source核对基点`7df97961bb14cb26638308f6dbd8e21f1812e425`；历史PublicM2来源`e30247defd859dfc75708fea4612c668e93cdbd2`。对象：[新设计](../design/BASELINE_LARGE_VIEWS_AND_QUERY_PROTOCOL.zh-CN.md)。BND-2暂定，混合提供合同为推荐候选，G1未批准。结构M2前期纳入为用户明确要求，具体实现和真实训练未获本轮执行授权。

## 实际工作

- 只读核对现有LightAction advance/score/校验、canonical序列及trainer、Experimental observed/online history、历史PublicM2 no-I/no-F和export准入。不是只根据函数参数猜已有产品能力。
- 运行[新规模程序](../../tools/g1_large_view_budget_probe.py)，复用[原fixture](../../tools/g1_input_budget_probe.py)，4组200/500/1000/10000对象；固定Qwen3 tokenizer及tokenizers0.22.2，资产与原预算相同。报告保存两脚本SHA及tokenizer身份。没有下载权重、训练、神经推理、GPU内存分配、真实语料或游戏操作。
- 数字分为实际生成字节/BPE计数、结构算术成本和明确假设的网络例子。naive attention字节不是实际kernel显存；实体矩阵不代表总峰值；page数不含metadata/catalog/dependent reads。
- 独立架构挑战明确Host捕获/完整C构建不是返回ref即免费；选择性曝光和query-response的消费规则需要新InputSpec；提前pin不延长动作合法性。独立源调查明确I/F在不同旧路径的实际差异。

## 关键源码

- [LightAction校验与advance](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/7df97961bb14cb26638308f6dbd8e21f1812e425/python/stpd/models/light_action_m2.py#L115)：page+feedback容量检查、可选I/F与独立core；[score](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/7df97961bb14cb26638308f6dbd8e21f1812e425/python/stpd/models/light_action_m2.py#L182)目前逐候选再stack，并非已实现流式。
- [canonical序列I](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/7df97961bb14cb26638308f6dbd8e21f1812e425/python/stpd/fullrun/light_action_m2_sequences.py#L373)取同源相邻chosen；[trainer](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/7df97961bb14cb26638308f6dbd8e21f1812e425/python/stpd/models/light_action_m2_training.py#L146)传I但F=None。
- [observed bridge](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/7df97961bb14cb26638308f6dbd8e21f1812e425/python/stpd/fullrun/memory_sequence_bridge.py#L239)按可用时间/来源构造history；[online](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/7df97961bb14cb26638308f6dbd8e21f1812e425/python/stpd/policy/memory_scorer.py#L166)核上一snapshot/catalog及已知输入，不能据此宣称Commit。
- [PublicM2来源合同](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/stpd/fullrun/public_m2_sequences.py#L27)及[实际推理](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/stpd/policy/public_m2_port.py#L257)：I/F均None。
- [export gate](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/7df97961bb14cb26638308f6dbd8e21f1812e425/python/stpd/policy/memory_export.py#L95)：不从核心签名推导可部署的新反馈能力。

分页参考AIP158、条件请求参考RFC9110均限设计借鉴；本项目sealed版本、payload过期、原生复验/完整C与研究曝光比普通列表API要求更多。没有引入通用HTTP语义替代native proof。

## 验证状态

新程序执行完成，按当前Ruff0.16.4规则修正import/长行后通过；独立复现4组输出与保存JSON完整一致，所有CNN/实体/naive attention算术通过。script SHA `9cc0e1cf10342be97e5450e71b771e6a4349b67da3e76394ab2a99df3de7746d`；report SHA `bca042a8f58e5e5c4cd24ec2d31ff72f6fbd4c04f70597bab36d25222d7e36ff`。正文诊断列表不是已执行生产测试。没有新的模型速度、胜率、全角色运行或10000对象实际游戏资格。

独立架构/计划审查无P1，和模型计量review共同发现一处P2：不能从Qwen参考计数推导未拟合scratch词表的实际超限。已限定结论到参考计量，并要求scratch独立测量；200/500功能目标不变。最终修订复核返回bounded discussion-design PASS，无未处理P1/P2；主文档SHA为`b8873dbb3e258bea175cd9aa0389676812551d392396e96b6051277651577f52`。本地check:docs 17 fixtures、0 warnings，closeout与diff通过；manifest33文件hash核对。

PROJECT_SYSTEM新增性能审阅要求经独立规范复核PASS：与最低成本验证/TESTING一致，明确不要求琐碎修改benchmark或重复全套检查；没有新增生产门禁。
