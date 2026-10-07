"""Deterministic synthetic sizing, not native reachability or model performance.

Requires an explicitly supplied tokenizer.json. No network, model weights, raw
recordings, token fitting, game process, or project mutation is used by this tool.
"""

import argparse
import hashlib
import json
from pathlib import Path

import tokenizers
from tokenizers import Tokenizer


def fixture(name, *, cards, enemies, relics, potions, statuses, orbs, mode, long=False):
    def desc(i):
        text = (
            f"示例对象{i}：造成{8 + i % 13}点伤害，获得{3 + i % 7}点格挡。"
            "本回合首次使用时获得公开资源；升级预览尚未打开。"
        )
        if long:
            text += "".join(
                f"附加条件{j}：若公开标记{j}存在，本次效果变化{j + 1}，在回合结束时移除此公开标记。"
                for j in range(8)
            )
        return text

    objects = [
        {
            "ref": f"c{i}",
            "kind": "card",
            "title": f"示例牌{i}",
            "energy": i % 4,
            "star": i % 3,
            "upgraded": i % 2 == 0,
            "text": desc(i),
        }
        for i in range(cards)
    ]
    creatures = [
        {
            "ref": f"e{i}",
            "kind": "enemy",
            "hp": 120 + i * 7,
            "block": i * 2,
            "intent": f"攻击{12 + i}点伤害，共{i % 3 + 1}次",
        }
        for i in range(enemies)
    ]
    effects = [
        {
            "ref": f"s{i}",
            "owner": f"e{i % max(enemies, 1)}",
            "name": f"公开效果{i}",
            "amount": i % 15 + 1,
        }
        for i in range(statuses)
    ]
    inventory = [
        {"ref": f"r{i}", "name": f"已持有遗物{i}", "public_counter": i % 7} for i in range(relics)
    ]
    potion_items = [{"ref": f"p{i}", "name": f"药水{i}", "slot": i} for i in range(potions)]
    orb_items = [
        {"ref": f"o{i}", "name": f"充能球{i}", "passive": i + 1, "evoke": i + 5}
        for i in range(orbs)
    ]
    frame = {
        "mode": mode,
        "player": {"hp": 63, "max_hp": 85, "energy": 3, "stars": 2, "gold": 378},
        "cards": objects,
        "enemies": creatures,
        "effects": effects,
        "relics": inventory,
        "potions": potion_items,
        "orbs": orb_items,
        "visible_tip": {"owner": "s0", "text": "当前已打开提示：此公开效果会在回合末减少。"},
        "scope": "synthetic current public page; no hidden pile order or unopened detail",
    }
    lines = [f"页面：{mode}。生命63/85；能量3；星数2；金币378。"]
    for c in objects:
        lines.append(
            f"卡牌 {c['ref']} {c['title']} 费用{c['energy']} 星费{c['star']} "
            f"升级{c['upgraded']}：{c['text']}"
        )
    for e in creatures:
        lines.append(f"敌人 {e['ref']} 生命{e['hp']} 格挡{e['block']} 意图：{e['intent']}")
    for e in effects:
        lines.append(f"状态 {e['ref']} 持有者{e['owner']} {e['name']} 层数{e['amount']}")
    for r in inventory:
        lines.append(f"遗物 {r['ref']} {r['name']} 公开计数{r['public_counter']}")
    for p in potion_items:
        lines.append(f"药水 {p['ref']} {p['name']} 槽位{p['slot']}")
    for o in orb_items:
        lines.append(f"球 {o['ref']} 被动{o['passive']} 激发{o['evoke']}")
    lines.append("已打开提示 s0：此公开效果会在回合末减少。")
    actions = []

    def add(verb, ref=None):
        actions.append(verb + (f" {ref}" if ref else ""))

    if mode == "combat":
        for c in objects:
            add("开始出牌", c["ref"])
            add("显示卡牌提示", c["ref"])
            add("进入卡牌详情", c["ref"])
        for r in inventory:
            add("显示遗物提示", r["ref"])
            add("进入遗物详情", r["ref"])
        for p in potion_items:
            add("打开药水弹窗", p["ref"])
            add("显示药水提示", p["ref"])
        for e in effects:
            add("显示状态提示", e["ref"])
        for e in creatures:
            add("显示意图提示", e["ref"])
        for o in orb_items:
            add("显示球提示", o["ref"])
        for v in [
            "结束回合",
            "打开牌组",
            "打开抽牌堆",
            "打开弃牌堆",
            "打开消耗堆",
            "打开地图",
            "显示生命提示",
            "显示金币提示",
            "显示楼层提示",
            "显示Boss提示",
            "显示牌组提示",
            "显示地图提示",
        ]:
            add(v)
        formula = 3 * cards + 2 * relics + 2 * potions + statuses + enemies + orbs + 12
    elif mode == "selector":
        for c in objects:
            add("选择或撤选", c["ref"])
        add("确认选择")
        add("取消选择")
        formula = cards + 2
    else:
        for c in objects:
            add("进入卡牌详情", c["ref"])
        add("返回")
        formula = cards + 1
    assert len(actions) == formula
    return {
        "name": name,
        "assumptions": {
            "cards": cards,
            "enemies": enemies,
            "relics": relics,
            "occupied_potions": potions,
            "statuses": statuses,
            "orbs": orbs,
            "mode": mode,
            "all_listed_actions_native_available_assumed": True,
            "long_synthetic_text": long,
        },
        "frame": frame,
        "state_text": "\n".join(lines),
        "candidate_texts": actions,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--tokenizer-revision", required=True)
    args = parser.parse_args()
    raw = Path(args.tokenizer).read_bytes()
    tok = Tokenizer.from_file(args.tokenizer)
    tok.no_truncation()
    tok.no_padding()
    cases = [
        fixture(
            "combat_typical_envelope",
            cards=10,
            enemies=3,
            relics=12,
            potions=3,
            statuses=15,
            orbs=3,
            mode="combat",
        ),
        fixture(
            "combat_late_stress",
            cards=10,
            enemies=8,
            relics=50,
            potions=5,
            statuses=80,
            orbs=10,
            mode="combat",
        ),
        fixture(
            "combat_late_long_text",
            cards=10,
            enemies=8,
            relics=50,
            potions=5,
            statuses=80,
            orbs=10,
            mode="combat",
            long=True,
        ),
        fixture(
            "pile_50", cards=50, enemies=0, relics=50, potions=5, statuses=0, orbs=0, mode="pile"
        ),
        fixture(
            "pile_200", cards=200, enemies=0, relics=50, potions=5, statuses=0, orbs=0, mode="pile"
        ),
        fixture(
            "selector_200",
            cards=200,
            enemies=0,
            relics=50,
            potions=5,
            statuses=0,
            orbs=0,
            mode="selector",
        ),
        fixture(
            "pile_200_long_text",
            cards=200,
            enemies=0,
            relics=50,
            potions=5,
            statuses=0,
            orbs=0,
            mode="pile",
            long=True,
        ),
    ]

    def tokens(s):
        return len(tok.encode(s, add_special_tokens=False).ids)

    results = []
    for f in cases:
        state = f["state_text"]
        actions = f["candidate_texts"]
        catalog = "\n".join(actions)
        encoded = json.dumps(f["frame"], ensure_ascii=False, separators=(",", ":"))
        action_lens = [len(a.encode("utf8")) + 2 for a in actions]
        results.append(
            {
                "name": f["name"],
                "assumptions": f["assumptions"],
                "state_utf8_bytes": len(state.encode("utf8")),
                "state_characters": len(state),
                "state_reference_bpe_tokens": tokens(state),
                "typed_json_reference_bpe_tokens": tokens(encoded),
                "candidate_count": len(actions),
                "candidate_reference_bpe_tokens": tokens(catalog),
                "state_plus_catalog_reference_bpe_tokens": tokens(
                    state + "\n可选操作：\n" + catalog
                ),
                "max_action_byte_tokens": max(action_lens),
                "sum_action_byte_tokens": sum(action_lens),
                "state_text_sha256": hashlib.sha256(state.encode()).hexdigest(),
                "candidate_text_sha256": hashlib.sha256(catalog.encode()).hexdigest(),
            }
        )
    script = Path(__file__).read_bytes()
    report = {
        "schema": "g1-synthetic-input-budget-v1",
        "source_script_sha256": hashlib.sha256(script).hexdigest(),
        "tokenizer": {
            "repo": "Qwen/Qwen3-0.6B",
            "revision": args.tokenizer_revision,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "library": "tokenizers",
            "version": tokenizers.__version__,
            "add_special_tokens": False,
            "truncation": False,
            "padding": False,
        },
        "limitations": [
            "Synthetic sizing only; no sampled gameplay or reachability proof.",
            "Reference Qwen BPE counts are not the future train-only scratch tokenizer counts.",
            "No chat template, tool schema, output tokens, full history, "
            "neural inference or timing measured.",
            "Flat information actions are a proposed envelope, not current text-menu-v2 root size.",
            "Current exposed descriptions only; no automatic inclusion of unopened detail.",
        ],
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
