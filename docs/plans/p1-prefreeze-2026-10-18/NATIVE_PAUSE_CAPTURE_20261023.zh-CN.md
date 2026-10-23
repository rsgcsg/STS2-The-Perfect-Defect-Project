# 原生 Pause 取消后误 taint 修复

本轮 root 授权 Runtime/必要 App 消费者修复，唯一源码负责人 training_state_derivation，基线 develop55ca53d8，topic codex/native-pause-capture-20261023。源码硬止15:40 UTC含收尾；无现场、原数据、训练、费用、发布或 CI 授权。

链路位置为在线模型→Policy Runtime→公开环境/Receipt，属于局部实现修正。原生 Pause 主动撤销同一 owner 的 epoch 后，旧 post-delivery capture 正确拒绝；错误在其 catch 把这次主动取消当成新环境错误而 taint。修复使用同一操作取消 token 的身份，保留原错误/拒绝、已知 Receipt 与 interrupted_known，不放宽 epoch 或 native identity。SDK 包装后的无状态码 HTTP 失败只在被撤销的 capture.await 连接归一化，证据持久化、cleanup/释放及后续身份验证不进入豁免。

Runtime41 是独立版本，startup/status/event2不改；App 显式支持40和41，旧1–39仍走原v1，未知版本拒绝。已有 Mod60/Tool/Host37/Evidence34/SDK及原 Runtime40资产不变。验证用真实 Runtime+owner+HTTP+NDJSON，在已知送达后阻塞完整 source 写入或 HTTP 响应，再从公开 Human 操作暂停；继续同 Actor 后不会重新投递旧动作，最后正常 Stop。身份漂移、unknown及真实释放/持久化失败继续保留失败。准确候选、各次失败/通过分母、包SHA和原现场未知由外部本轮收据记录；源码/测试/包不授新的游戏现场资格。
