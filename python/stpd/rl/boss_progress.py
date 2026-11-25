"""Boss progress (RL5, third training; default off): how far one Boss fight got.

Public facts only: each enemy row of a Boss fight is what the player sees in
`combat.content.enemies` (entity id, definition id, hp, max hp, MINION_POWER among its
statuses, hp display mode). Nothing here enters the model input or legality.

Definition (lead 11-26, one enemy at a time):
- every enemy's max hp is recorded the first time it appears; once it is gone from the
  list it is dead (0 hp left);
- progress = 1 - (hp left on the enemies that must die) / (their first max hp), the
  denominator fixed for the whole fight; a won fight is 1;
- minions (MINION_POWER) never count; both Kaiser Crab claws count; the Waterfall Giant's
  infinite hp bar (at 0 hp: display mode not "normal", or hp >= 1e8) is "hp done" (0
  left), and only its: another enemy showing one, or an hp below 0 or above its max, is
  not certain (unscored); heals count as they are;
- the Test Subject is one enemy with three lives (A0 max 100 -> 200 -> 300, same entity
  id): the denominator is the sum of the lives (600), hp left = this life's hp + the lives
  still to come; an empty list ends a life, it is no death. Only the A0 table is measured,
  so a Test Subject at any other level is never scored (counted);
- a fight that cannot be read with certainty is never scored, only counted by reason.

`BossBaselines` is the per-Boss running mean of the final progress that the score is
measured against (`coef x (progress - baseline)`): each fight reads it before its own
progress is added, so the baseline never depends on the fight it scores.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

MINION = "MINION_POWER"
INFINITE_HP = 100_000_000  # >= this with mode "normal" is still the infinite bar
# The one enemy whose infinite bar means "hp done" (A98: 999999999 after 0, 4 of 4 fights).
WATERFALL_GIANT = "WATERFALL_GIANT"
TEST_SUBJECT = "TEST_SUBJECT"
# Max hp of each life by ascension (A103: 38 A0 fights, entity id never changes).
TEST_SUBJECT_LIVES: dict[int, tuple[int, ...]] = {0: (100, 200, 300)}
# Why a Boss fight was not scored (the readout counts each).
UNSCORED = ("enemies_missing", "no_target", "new_enemy", "enemy_returned", "minion_changed",
            "max_hp_changed", "hp_hidden", "infinite_at_first", "test_subject_ascension",
            "test_subject_phase", "duplicate_entity", "infinite_not_waterfall",
            "hp_inconsistent", "encounter_mixed")
MAX_COEF = 0.05
DEFAULT_DECAY = 0.05

# One enemy as the row carries it: [entity_id, definition_id, hp, max_hp, minion, mode].
Enemy = Sequence[Any]


def public_enemies(enemies: Any) -> list[list[Any]] | None:
    """The row's enemy table from `combat.content.enemies` (the model's own public input);
    None, never an exception, when the list or any part of it has another shape (that
    fight is then not scored: `enemies_missing`)."""
    if not isinstance(enemies, list):
        return None
    table = []
    for enemy in enemies:
        if not isinstance(enemy, Mapping):
            return None
        statuses = enemy.get("statuses", [])
        if not isinstance(statuses, list) or not all(isinstance(s, Mapping) for s in statuses):
            return None
        row = [enemy.get("entity_id"), enemy.get("definition_id"), enemy.get("hp"),
               enemy.get("max_hp"), any(s.get("definition_id") == MINION for s in statuses),
               enemy.get("hp_display_mode")]
        table.append(row)
    return table if valid_table(table) else None


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def valid_table(table: Any) -> bool:
    """Shape of one row's enemy table (the assembler and the segment reader)."""
    return isinstance(table, list) and all(
        isinstance(e, list) and len(e) == 6
        and isinstance(e[0], str) and e[0] and isinstance(e[1], str) and e[1]
        and (e[2] is None or _number(e[2])) and (e[3] is None or _number(e[3]))
        and type(e[4]) is bool and isinstance(e[5], str) and e[5]
        for e in table)


def boss_key(table: Sequence[Enemy], ascension: int) -> str:
    """The Boss of a fight: its level and the non-minion enemies it opens with."""
    names = sorted(str(e[1]) for e in table if not e[4])
    return f"a{ascension}:" + "+".join(names)


class _Entity:
    __slots__ = ("definition", "first_max", "max", "hp", "minion", "present", "infinite",
                 "lives")

    def __init__(self, definition: str, minion: bool) -> None:
        self.definition, self.minion = definition, minion
        self.first_max = self.max = self.hp = 0.0
        self.present = True
        self.infinite = False
        self.lives: tuple[int, ...] | None = None  # Test Subject: max hp per life
        # (for the Test Subject `max` is the current life's and `first_max` unused)

    def life(self) -> int:
        assert self.lives is not None
        return self.lives.index(int(self.max))

    def left(self) -> float:
        later = 0.0 if self.lives is None else float(sum(self.lives[self.life() + 1:]))
        if not self.present or self.infinite:
            return later
        return self.hp + later

    def total(self) -> float:
        return float(sum(self.lives)) if self.lives is not None else self.first_max


def fight_progress(tables: Sequence[Any], *, won: bool, ascension: int
                   ) -> tuple[float | None, str | None, str | None]:
    """(progress, unscored reason, Boss key) of one ended Boss fight.

    `tables`: the enemy table of each decision row of the fight, in order (the last one is
    the last state the fight showed: the hp the last action dealt is not seen). `won`: the
    fight ended in a win. Progress is None exactly when the reason is not.
    """
    if not tables or any(not valid_table(t) for t in tables):
        return None, "enemies_missing", None
    key = boss_key(tables[0], ascension)
    entities: dict[str, _Entity] = {}
    for index, table in enumerate(tables):
        seen: set[str] = set()
        for entity_id, definition, hp, max_hp, minion, mode in table:
            if entity_id in seen:
                return None, "duplicate_entity", key
            seen.add(entity_id)
            infinite = mode != "normal" or (hp is not None and hp >= INFINITE_HP)
            if infinite and not minion and definition != WATERFALL_GIANT:
                return None, "infinite_not_waterfall", key
            entity = entities.get(entity_id)
            returning = False
            if entity is None:
                if index and not minion:
                    return None, "new_enemy", key
                entity = entities[entity_id] = _Entity(definition, minion)
                if not minion:
                    if infinite:
                        return None, "infinite_at_first", key
                    if hp is None or max_hp is None:
                        return None, "hp_hidden", key
                    if not 0 <= hp <= max_hp or not max_hp > 0:
                        return None, "hp_inconsistent", key
                    if definition == TEST_SUBJECT:
                        lives = TEST_SUBJECT_LIVES.get(ascension)
                        if lives is None:
                            return None, "test_subject_ascension", key
                        if max_hp not in lives:
                            return None, "test_subject_phase", key
                        entity.lives = lives
                    entity.first_max = entity.max = float(max_hp)
            else:
                if entity.minion != minion or entity.definition != definition:
                    return None, "minion_changed", key
                returning = not entity.present
                if returning and entity.lives is None and not minion:
                    return None, "enemy_returned", key
            entity.present = True
            entity.infinite = infinite
            if minion or infinite:
                continue
            if hp is None or max_hp is None:
                return None, "hp_hidden", key
            if not 0 <= hp <= max_hp:
                return None, "hp_inconsistent", key
            if max_hp != entity.max:
                if entity.lives is None:
                    return None, "max_hp_changed", key
                life = entity.life()
                if life + 1 >= len(entity.lives) or max_hp != entity.lives[life + 1]:
                    return None, "test_subject_phase", key
                entity.max = float(max_hp)  # the next life (an empty list may be skipped)
            elif returning:  # a Test Subject back from an empty list with the same life
                return None, "test_subject_phase", key
            entity.hp = float(hp)
        for entity_id, entity in entities.items():
            if entity_id not in seen:
                entity.present = False
    targets = [e for e in entities.values() if not e.minion]
    if not targets:
        return None, "no_target", key
    if won:
        return 1.0, None, key
    total = sum(e.total() for e in targets)
    progress = 1.0 - sum(e.left() for e in targets) / total
    return min(max(progress, 0.0), 1.0), None, key


class BossBaselines:
    """Per-Boss running mean of the final progress, weighted by each fight's rows (one per
    assembler process): a fight's score lands on every one of its rows, so the mean is
    taken per row and the score is mean zero per row, not per fight.

    With n_i rows and progress p_i per fight, b = S / R, S = sum of f^k n_i p_i and
    R = sum of f^k n_i (k = fights since): f = 1 for the first 1 / decay fights (the plain
    row-weighted mean), then f = 1 - decay per fight. A fight reads `baseline(key)` before
    `add(key, progress, rows)`; the first fight of a Boss in this process has none (no
    score, counted). Not kept across a restart: a resumed run starts empty, and each
    Boss's first fight per service is unscored again.
    """

    def __init__(self, decay: float = DEFAULT_DECAY) -> None:
        if type(decay) not in (int, float) or not 0 < decay <= 1:
            raise ValueError("invalid_boss_progress_decay")
        self.decay = float(decay)
        self.state: dict[str, list[float]] = {}  # key -> [fights, S, R]

    def baseline(self, key: str) -> float | None:
        state = self.state.get(key)
        return None if state is None else state[1] / state[2]

    def add(self, key: str, progress: float, rows: int) -> None:
        if type(rows) is not int or rows < 1:
            raise ValueError("invalid_boss_progress_rows")
        state = self.state.setdefault(key, [0, 0.0, 0.0])
        state[0] += 1
        keep = 1.0 if state[0] <= 1 / self.decay else 1.0 - self.decay
        state[1] = keep * state[1] + rows * progress
        state[2] = keep * state[2] + rows
