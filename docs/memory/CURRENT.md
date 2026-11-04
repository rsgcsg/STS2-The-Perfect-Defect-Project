# RL4.1：G0 已测；G1 恢复有更新，整体验证尚未通过

I1续跑3已从真实CP16完成G0，实际A100为40GB；整组BF16 eager、无checkpoint，
320行局部SGD热0.514s、峰10.44GiB，compile无稳态收益，正式profile已入库。
源码`2def25eb`的两个32路G1档保留：首档被辅助磁盘观察中断；第二档恢复后实际继续33次SGD，
但仍有2未决lease/2未收口episode，不能算整链通过。零段close写旧代已由原件证实，
当前唯一i1 writer修准入归属与原异常记录并补反例；不重投旧World/seed，不修改原分母。
本轮下一步是精确冻结修复后走新的正常G1短验，并收齐保全和量测；G2/H1尚未启动。
本轮实物和剩余义务看外部任务`replies/RL4.1/I1-REPORT.md`；接口和SOP在[原RL指南](../../python/docs/RL_ACTOR_LEARNER.zh-CN.md)。

## 原轮结果与剩余授权

RL4-PREP按未达标结卡，准确实物、原件replay超时、低吞吐和前后测边界由[RL4任务](../plans/RL_CRITIC_20261101.md)及其原收据负责。已运行source2d77049e/Runtime63/r1.9；#378/#379/#380已合；cache033a48ed只有CPU/CI证据，未热换。362SGD/CP16和旧unknown分母不被I1升级，RL2/RL3不重开。
旧RL4原12个GPU作业累计10911秒，是原历史成本，不改写。11-04 22:00用户将本轮GPU额度改为累计≤8h、同时一张A100或A30；G0≤1h、G1≤2h，排队不计。当前G0新GPU共161秒，G1两档354+1342=1696秒，均结束；失败和实际World分母保留在本轮原收据。
actor可按本轮卡已批准的200–500核上限、实际空闲和瓶颈起调、先登记原GAME-SLOT；不是旧45路上限继续生效。正式规模CPU SGD彩排已取消并入G1。
不长训、不push、不删资产、不读凭据；H1是G1后的推荐交接，本轮不自行启动；固定评估及永久heldout不训练。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 其他已派工作入口

CARD-1A仍待用户试1–2局与原件/入库；[STATUS](../STATUS.md)路由原安装证据，本卡不安装。
原RL3最终12unknown的9同因覆盖/3根因未证及原分母保留，#373不重开。
U1的Host63/Runtime62/SDK19/r1.9沿正常冻结采用，原件看[U1](../evidence/UNKNOWN_FIX_U1_20261101.md)。

## Remaining Platform non-claims（运行与证据边界）

unknown不重试、不当负局；CI/合并、owner排空、R2可读、数据资格和模型效果各看原收据。
平台其他未完归[STATUS](../STATUS.md)、[U1证据](../evidence/UNKNOWN_FIX_U1_20261101.md)及原Issues，不在本卡自动扩范围。
