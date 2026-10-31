"""Opt-in Z-next-fact A training supervision, outside the exported action model.

Targets are public facts from a verified next link, never model inputs. Statistics
fit TRAIN only. Missing fields and uncertain enemy matches remain separately masked.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, cast

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from .data import digest, json_bytes

NUMERIC = (
    "hp_delta",
    "max_hp",
    "block",
    "energy",
    "max_energy",
    "stars",
    "gold_delta",
    "hand_count",
    "draw_count",
    "discard_count",
    "exhaust_count",
    "orb_slots",
    "orb_count",
    "orb_lightning",
    "orb_frost",
    "orb_dark",
    "orb_plasma",
    "orb_glass",
    "orb_other",
)
BOOLEAN = ("turn_ended", "combat_ended", "self_dead")
ENEMY = ("hp_delta", "block", "dead")
CATEGORY = ("screen", "room")


def content(observation: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = observation.get(key)
    return (value.get("content") or {}) if isinstance(value, dict) else {}


def number(value: Any) -> float | None:
    return float(value) if type(value) in {int, float} and math.isfinite(value) else None


def categories(observation: Mapping[str, Any]) -> dict[str, str | None]:
    scene = observation.get("scene") or {}
    position = content(observation, "map").get("current_position") or {}
    combat = content(observation, "combat")
    screen = (
            "terminal" if observation.get("status") == "terminal" else scene.get("native_kind")
        )
    if screen is not None and combat.get("encounter_type"):
        screen += ":" + combat["encounter_type"]
    return {"screen": screen, "room": position.get("point_type")}


def project_next(
    current: dict[str, Any], successor: dict[str, Any] | None, *, next_known: bool,
    chosen: str | None = None,
) -> dict[str, Any]:
    """Caller supplies the producer's next mask; never join by adjacent N indices."""
    empty: dict[str, Any] = {"numeric": {}, "boolean": {}, "category": {}, "enemies": []}
    if not next_known or successor is None:
        return empty
    if successor.get("status") not in {"decision", "terminal"}:
        return empty
    before, after = content(current, "player"), content(successor, "player")
    combat, following = content(current, "combat"), content(successor, "combat")
    player = following.get("player") or {}
    n = empty["numeric"]
    for label, key in (("hp_delta", "hp"), ("gold_delta", "gold")):
        a, b = number(before.get(key)), number(after.get(key))
        if a is not None and b is not None:
            n[label] = b - a
    for key in ("max_hp",):
        if number(after.get(key)) is not None:
            n[key] = float(after[key])
    for key in ("block", "energy", "max_energy", "stars", "orb_slots"):
        if number(player.get(key)) is not None:
            n[key] = float(player[key])
    if isinstance(player.get("hand"), list):
        n["hand_count"] = float(len(player["hand"]))
    for pile in ("draw", "discard", "exhaust"):
        key = pile + "_pile_count"
        if number(player.get(key)) is not None:
            n[pile + "_count"] = float(player[key])
    if isinstance(player.get("orbs"), list):
        orbs = Counter(o.get("definition_id") for o in player["orbs"])
        n["orb_count"] = float(sum(orbs.values()))
        known = {"LIGHTNING_ORB", "FROST_ORB", "DARK_ORB", "PLASMA_ORB", "GLASS_ORB"}
        for key in ("lightning", "frost", "dark", "plasma", "glass"):
            n["orb_" + key] = float(orbs[key.upper() + "_ORB"])
        n["orb_other"] = float(sum(v for k, v in orbs.items() if k not in known))
    b = empty["boolean"]
    if number(after.get("hp")) is not None:
        b["self_dead"] = float(after["hp"] <= 0)
    terminal = successor.get("terminal") or {}
    if terminal.get("outcome") in {"win", "loss"}:
        b["self_dead"] = float(terminal["outcome"] == "loss")
    if combat:
        # Compare the actual immediate successor, including cross-turn transitions.
        next_combat_part = successor.get("combat") or {}
        ended = next_combat_part.get("availability") in {"not_applicable", "terminal"}
        if following or ended:
            b["combat_ended"] = float(ended)
        if ended:
            b["turn_ended"] = 1.0
        elif number(combat.get("round")) is not None and number(following.get("round")) is not None:
            b["turn_ended"] = float(combat["round"] != following["round"])
    empty["category"] = {k: v for k, v in categories(successor).items() if v is not None}
    # Alias continuity is a producer guarantee within one projection/segment.
    # Never pair by definition, position, nearest HP, or a freshly spawned enemy.
    future = following.get("enemies") or []
    counts = Counter(e.get("entity_id") for e in future)
    lookup = {e.get("entity_id"): e for e in future}
    action: dict[str, Any] = next(
        (c for c in current.get("catalog", []) if c.get("choice_id") == chosen), {})
    old_counts = Counter(e.get("entity_id") for e in combat.get("enemies") or [])
    for position, old in enumerate(combat.get("enemies") or []):
        alias = old.get("entity_id")
        if not alias or counts[alias] != 1 or old_counts[alias] != 1:
            continue
        new = lookup[alias]
        if old.get("definition_id") != new.get("definition_id"):
            continue
        facts: dict[str, float] = {}
        hp0, hp1 = number(old.get("hp")), number(new.get("hp"))
        if hp0 is not None and hp1 is not None:
            facts["hp_delta"] = hp1 - hp0
        if hp1 is not None:
            facts["dead"] = float(hp1 <= 0)
        if number(new.get("block")) is not None:
            facts["block"] = float(new["block"])
        empty["enemies"].append(
            {
                "position": position,
                "targeted": float(action.get("target") == alias),
                "definition_id": old.get("definition_id"),
                "facts": facts,
            }
        )
    return empty


def projection_rows(value: dict[str, Any], references: Sequence[dict[str, Any]]
                    ) -> dict[str, dict[str, Any]]:
    """Project selected N points from the original next/end producer, without rewriting it.

    The owning SnapshotReader/load_projection must first verify this source and
    selection. Here the selected observations and edge bindings are checked again.
    Native timeline edges, rather than adjacency in the filtered N population,
    identify the immediate successor. Segment sources never cross their boundary.
    """
    schema = value.get("schema")
    groups = ({"points": value["points"]} if schema == "sts2.p1/native-targets-1" else
              {s["segment_id"]: s["points"] for s in value["segments"]}
              if schema == "sts2.p1/native-segments-1" else None)
    if groups is None:
        raise ValueError("next_fact_original_projection_required")
    points = {p["point_id"]: p for rows in groups.values() for p in rows
              if "point_id" in p}
    edges: dict[str, list[dict[str, Any]]] = {}
    for edge in value.get("edges", []):
        edges.setdefault(edge["from_point_id"], []).append(edge)
    result = {}
    for ref in references:
        rows = groups[ref["segment_id"]]
        index = ref["point_index"]
        point = rows[index]
        current = point["observation"]
        if (point["mask"]["N"] is not True or point["chosen"] != ref["chosen"]
                or point.get("source_decision_id") != ref["source_decision_id"]
                or digest(json_bytes(current)) != ref["observation_sha256"]):
            raise ValueError("next_fact_N_selection_drift")
        known = point["mask"]["next"] is True
        successor = None
        if known:
            if schema == "sts2.p1/native-targets-1":
                eligible = [e for e in edges.get(point["point_id"], [])
                            if e["eligible"] is True]
                if len(eligible) != 1 or eligible[0]["to_point_id"] not in points:
                    raise ValueError("next_fact_qualified_edge_missing_or_ambiguous")
                successor = points[eligible[0]["to_point_id"]]["observation"]
            else:
                if index + 1 == len(rows):
                    raise ValueError("next_fact_segment_boundary_crossed")
                successor = rows[index + 1]["observation"]
        row = project_next(current, successor, next_known=known, chosen=point["chosen"])
        row["baseline"] = project_next(current, current, next_known=True, chosen=point["chosen"])
        row["next_known"] = known
        row["current_screen"] = categories(current)
        row["encounter_type"] = content(current, "combat").get("encounter_type")
        row["kind"] = next(c["kind"] for c in current["catalog"]
                           if c["choice_id"] == point["chosen"])
        row["split"] = ref["split"]
        result[ref["point_id"]] = row
    return result


class NextFactSupervision(nn.Module):
    """Two-layer width128 global and shared-enemy heads. No dropout/RNG in forward."""

    def __init__(self, rows: Mapping[str, dict[str, Any]], train_ids: Sequence[str]):
        super().__init__()
        self.rows = dict(rows)
        train = [self.rows[key] for key in train_ids]
        self.numeric = tuple(k for k in NUMERIC if any(k in r["numeric"] for r in train))
        self.boolean = tuple(k for k in BOOLEAN if any(k in r["boolean"] for r in train))
        self.enemy = tuple(k for k in ENEMY if any(
            k in e["facts"] for r in train for e in r["enemies"]))
        self.statistics: dict[str, tuple[float, float]] = {}
        for key in (*NUMERIC, "enemy.hp_delta", "enemy.block"):
            values = (
                [
                    e["facts"][key[6:]]
                    for row in train
                    for e in row["enemies"]
                    if key[6:] in e["facts"]
                ]
                if key.startswith("enemy.")
                else [r["numeric"][key] for r in train if key in r["numeric"]]
            )
            mean = sum(values) / len(values) if values else 0.0
            scale = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) if values else 1.0
            self.statistics[key] = (mean, scale if scale > 0 else 1.0)
        self.classes = {
            key: ("<other>", *sorted({r["category"][key] for r in train if key in r["category"]}))
            for key in CATEGORY if any(key in r["category"] for r in train)
        }
        size = len(self.numeric) + len(self.boolean) + sum(map(len, self.classes.values()))
        # Preserve action-model initialization AND continuing training RNG.
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(2001)
            self.global_head = nn.Sequential(nn.Linear(384, 128), nn.GELU(),
                                             nn.Linear(128, max(1, size)))
            self.enemy_head = nn.Sequential(nn.Linear(577, 128), nn.GELU(), nn.Linear(128, 3))
        self.spec = {
            "schema": "stpd/z-next-fact-a-1",
            "weight": 0.2,
            "numeric": self.numeric,
            "boolean": self.boolean,
            "enemy": self.enemy,
            "classes": self.classes,
            "statistics": self.statistics,
            "cross_turn": True,
            "rows_sha256": digest(json_bytes(self.rows)),
            "train_ids_sha256": digest(json_bytes(list(train_ids))),
            "enemy_input": "current/action + current enemy scene node + selected target bit",
            "reduction": ("TRAIN-supported fixed field mean; valid targets averaged over batch"),
        }

    def batch(self, ids: Sequence[str]) -> NextFactBatch:
        return NextFactBatch(self, [self.rows[key] for key in ids])


class NextFactBatch:
    def __init__(self, head: NextFactSupervision, rows: list[dict[str, Any]]):
        self.head, self.rows = head, rows
        self.denominators: Counter[str] = Counter()
        for row in rows:
            self.denominators.update(row["numeric"].keys())
            self.denominators.update(row["boolean"].keys())
            self.denominators.update(row["category"].keys())
            for enemy in row["enemies"]:
                self.denominators.update("enemy." + k for k in enemy["facts"])
        self.fields = max(1, len(head.numeric) + len(head.boolean)
                          + len(head.classes) + len(head.enemy))

    def enemy_positions(self, group: list[int]) -> list[list[int]]:
        return [[e["position"] for e in self.rows[i]["enemies"]] for i in group]

    def loss(self, encoded: tuple[Tensor, list[Tensor]], group: list[int]) -> Tensor:
        features, enemy_features = encoded
        head = self.head
        prediction = cast(Tensor, head.global_head(features))
        total = prediction.sum() * 0
        column = 0
        for family, keys in (("numeric", head.numeric), ("boolean", head.boolean),
                             ("category", tuple(head.classes))):
            for key in keys:
                width = len(head.classes[key]) if family == "category" else 1
                selected = [j for j, i in enumerate(group) if key in self.rows[i][family]]
                if selected:
                    labels = [self.rows[group[j]][family][key] for j in selected]
                    scores = prediction[selected, column : column + width]
                    if family == "numeric":
                        mean, scale = head.statistics[key]
                        target = features.new_tensor([(x - mean) / scale for x in labels])
                        loss = F.huber_loss(scores[:, 0], target, reduction="sum")
                    elif family == "boolean":
                        loss = F.binary_cross_entropy_with_logits(
                            scores[:, 0], features.new_tensor(labels), reduction="sum"
                        )
                    else:
                        classes = head.classes[key]
                        target = torch.tensor(
                            [classes.index(x) if x in classes else 0 for x in labels],
                            device=features.device,
                        )
                        loss = F.cross_entropy(scores, target, reduction="sum")
                    total = total + loss / self.denominators[key]
                column += width
        enemy_rows = [(j, e) for j, i in enumerate(group) for e in self.rows[i]["enemies"]]
        if enemy_rows:
            if len(enemy_features) != len(enemy_rows):
                raise ValueError("next_fact_enemy_features_misaligned")
            source = torch.stack(
                [
                    torch.cat((features[j], enemy_features[k],
                               features.new_tensor([e["targeted"]])))
                    for k, (j, e) in enumerate(enemy_rows)
                ]
            )
            predictions = cast(Tensor, head.enemy_head(source))
            for k, key in enumerate(ENEMY):
                if key not in head.enemy:
                    continue
                selected = [j for j, (_, e) in enumerate(enemy_rows) if key in e["facts"]]
                if not selected:
                    continue
                target = features.new_tensor([enemy_rows[j][1]["facts"][key] for j in selected])
                if key == "dead":
                    loss = F.binary_cross_entropy_with_logits(
                        predictions[selected, k], target, reduction="sum"
                    )
                else:
                    mean, scale = head.statistics["enemy." + key]
                    loss = F.huber_loss(
                        predictions[selected, k], (target - mean) / scale, reduction="sum"
                    )
                total = total + loss / self.denominators["enemy." + key]
        return 0.2 * total / self.fields
