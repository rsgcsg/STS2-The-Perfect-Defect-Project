"""Layout = which chain role runs on which card; a parameter, not a tier script.

A layout is a list of cards in job GPU order, each a set of roles from
{"inference", "train"}. Shorthands: "shared" (1 card, both roles under MPS),
"<a>i+<b>t" (a inference cards then b training cards, e.g. "1i+1t", "1i+2t"), or an
explicit "inference+train,train" (one comma-separated entry per card).

What the current chain code supports, and is therefore enforced here:
- exactly one card carries inference (the K services share it under MPS);
- training ranks occupy contiguous cards (rank r -> cuda:<first>+r, sgd_process);
- a single rank on a card of its own sees only that card and uses plain "cuda"
  (the single-rank SGD child refuses "cuda:1": c2b); several ranks see every job
  card and start at cuda:<first training card> (V3-yin §3, c3c/c3s).

Naming: `N卡 · 布局 · 卡型`, e.g. `2卡 · 1推理+1训练 · A100-40`.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

ROLES = ("inference", "train")


class LayoutError(ValueError):
    """A layout the chain cannot run as written."""


@dataclass(frozen=True)
class Placement:
    cards: int
    card_type: str
    layout: tuple[tuple[str, ...], ...]
    service_card: int            # index into the job's GPU list
    train_cards: tuple[int, ...]  # indices into the job's GPU list
    ranks: int
    learner_visible: tuple[int, ...]  # indices the learner process sees
    learner_device: str
    shared_inference_card: bool  # learner shares the inference card
    gpu_cores: int
    learner_cores: int
    name: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse(spec: str, cards: int) -> tuple[tuple[str, ...], ...]:
    text = spec.strip().replace(" ", "")
    if text == "shared":
        if cards != 1:
            raise LayoutError("layout 'shared' is one card; name each card for more")
        return (("inference", "train"),)
    match = re.fullmatch(r"(\d+)i\+(\d+)t", text)
    if match:
        layout = (("inference",),) * int(match[1]) + (("train",),) * int(match[2])
    else:
        layout = tuple(tuple(sorted(set(card.split("+")))) for card in text.split(","))
        for card in layout:
            if not card or set(card) - set(ROLES):
                raise LayoutError(f"layout {spec!r}: each card is a '+' list of {ROLES}")
    if len(layout) != cards:
        raise LayoutError(f"layout {spec!r} names {len(layout)} cards, resources give {cards}")
    return layout


def describe(layout: tuple[tuple[str, ...], ...]) -> str:
    if len(layout) == 1 and set(layout[0]) == set(ROLES):
        return "推理训练共用"
    inference = sum(1 for card in layout if card == ("inference",))
    train = sum(1 for card in layout if card == ("train",))
    if inference + train == len(layout):
        return f"{inference}推理+{train}训练"
    return ",".join("+".join({"inference": "推理", "train": "训练"}[r] for r in card)
                    for card in layout)


def name(cards: int, layout: tuple[tuple[str, ...], ...], card_type: str) -> str:
    return f"{cards}卡 · {describe(layout)} · {card_type}"


def place(spec: str, cards: int, card_type: str, *, service_cores: int,
          rank_cores: int) -> Placement:
    layout = parse(spec, cards)
    inference = [i for i, card in enumerate(layout) if "inference" in card]
    train = [i for i, card in enumerate(layout) if "train" in card]
    if len(inference) != 1:
        raise LayoutError(f"layout {spec!r}: exactly one inference card is supported "
                          f"(got {len(inference)})")
    if not train:
        raise LayoutError(f"layout {spec!r}: no training card")
    if train != list(range(train[0], train[0] + len(train))):
        raise LayoutError(f"layout {spec!r}: training cards must be contiguous "
                          "(rank r -> cuda:<first>+r)")
    ranks = len(train)
    service = inference[0]
    shared = service in train
    if ranks > 1 and shared:
        raise LayoutError(f"layout {spec!r}: a data-parallel rank on the inference card is "
                          "not wired yet (mixed layouts are a later design item)")
    if ranks == 1:
        visible, device = (train[0],), "cuda"
    else:
        visible, device = tuple(range(cards)), f"cuda:{train[0]}"
    learner_cores = rank_cores * ranks
    return Placement(
        cards=cards, card_type=card_type, layout=layout, service_card=service,
        train_cards=tuple(train), ranks=ranks, learner_visible=visible,
        learner_device=device, shared_inference_card=shared,
        gpu_cores=service_cores + learner_cores, learner_cores=learner_cores,
        name=name(cards, layout, card_type))


def target(cards: int, targets: dict[str, int], ready: dict[str, int]) -> dict[str, Any]:
    """The target that belongs to this card count (CHAIN-CONFIG-MODEL §2)."""
    key = str(cards)
    return {"cards": cards, "target_rows_h": targets.get(key),
            "ready_rows_h": ready.get(key),
            "note": None if key in targets else
            f"no target set for {cards} cards: report the loss table"}


def perfect_packing(cards: int, inference_rows_h: int, training_rows_h: int) -> float:
    """Ideal rows/h for N cards: N / (1/inference + 1/training) (playbook §11.5)."""
    return cards / (1 / inference_rows_h + 1 / training_rows_h)


def service_cpus(cpus: list[int], workers: int, index: int) -> list[int]:
    """The prepare CPUs of service `index` (roles.service; preflight warns on sharing).

    Services take `workers` CPUs each from the end of the list (bounds before the start
    index from the end, as Python slices do). An empty or short slice falls back to the
    last `workers` CPUs: FastInferenceService needs exactly `workers` (V4 smoke-h1b:
    14 CPUs, 8 workers -> service 3 got 4)."""
    mine = cpus[len(cpus) - workers * (index + 1):len(cpus) - workers * index]
    return mine if len(mine) == workers else cpus[-workers:]
