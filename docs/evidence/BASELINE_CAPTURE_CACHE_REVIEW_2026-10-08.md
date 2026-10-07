# 采集与缓存默认／I-F关闭：源码审查

日期：2026-10-08。source基点`9786ba2d9373c9797e5981b8aa5ce6202a99498a`。对象：[采集缓存默认](../design/BASELINE_CAPTURE_CACHE_DEFAULT.zh-CN.md)。用户明确I/F先关闭；其他cache/采集推荐仍为G1待审设计，没有生产实现或模型运行。

## 当前源码事实

| 固定source | 实际行为 |
| --- | --- |
| [Observe](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9786ba2d9373c9797e5981b8aa5ce6202a99498a/components/connector/host/PlayerEnvironment/Core/PlayerEnvironmentService.cs#L151) | 每次调用BuildSnapshot，非共享sealed-view读取 |
| [Read](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9786ba2d9373c9797e5981b8aa5ce6202a99498a/components/connector/host/PlayerEnvironment/Reads/ReadService.cs#L23) | 重建请求read及snapshot后才检查expected ID |
| [text-menu Observe](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9786ba2d9373c9797e5981b8aa5ce6202a99498a/components/connector/host/PlayerEnvironment/TextMenu/TextMenuV2Executor.cs#L29) | 先capture，再session hash/投影；request result缓存另在49行 |
| [SnapshotBuilder](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9786ba2d9373c9797e5981b8aa5ce6202a99498a/components/connector/host/PlayerEnvironment/Observation/SnapshotBuilder.cs#L85) | persistent/native/read构建在前，191行以后计算稳定signature；稳定ID不能证明跳过读取 |
| [资料页](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9786ba2d9373c9797e5981b8aa5ce6202a99498a/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuInformation.cs#L365) | 已进入deck/pile每次capture读取资料；1048后inspect读取rendered字段 |

独立source审查完成，root重读关键入口/调用顺序。没有执行game GET、runtime探测、性能测试或数据操作。共享cache与lazy大资料是新建议，旧cap512不是已实现大关系分页。

## 设计响应

1. source notice与sealed view不混用。未采集大payload不能提前承诺可恢复过去全部内容；ReadCurrent在请求时捕获，ReadSealed仅服务已冻结scope。
2. 必要当前事实与短暂required历史及时保留；可延后大资料按需capture；表示/传输可进一步懒生成，但仅使用冻结值。
3. 可靠native version/dirty支持跳过扫描；否则仍需有界一致capture。后算hash和HTTP304都不自动省掉原生读取。
4. 共享key含scope/actor/interaction，客户端私有控制/虚拟导航独立。pin保留字节，不延长动作授权；Act依然native复验。
5. I/F默认off，first Model不输入历史own-action/receipt日志，也不换名到P/E；程序保留它们用于控制与追溯。当前HP/selected/focus/summary仍属于观察，W仍有持续历史。

## 外部参照与验证范围

主文档引用Kubernetes分页同collection版本/过期、PostgreSQL repeatable read与HTTP conditional GET。只借鉴版本/缓存合同，不引入对应服务器，不推断STS2已有MVCC/可靠全局版本。

独立完成稿审查无P1，提出一项P2：GetModelInput(full)不得把通知时global与请求时大列表拼成伪一致输入。正文已明确同一capture或依赖未变证明，required partial/inconsistent/building不得成功交付；最终复核返回bounded PASS / READY_FOR_OWNER_DISCUSSION，无未处理P1/P2；主文档SHA `7b02e01dcc2c52f93308f18e97dae861eab36e3bb6f0efe7aeeb954d5de134d9`与root一致。本地文档检查17 fixtures通过，CURRENT长度提示已通过缩短路由修正，未削弱门禁。没有新缓存capture次数、帧耗时、GPU或I/F-off数值/行为测试；这些是E1/E3实现时的明确反例与测量计划，不是本轮完成结果。
