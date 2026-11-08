"""R7 faithful audit tools for fast_chain: sampler and comparators (design v2 §2.9).

R7 replays 10-20 fast-chain games per hour, chosen before they open, through
today's complete chain (the evaluation entry) following the fast game's
de-handled action record, and compares step by step. This module owns the
choice of games and the comparison verdicts; it runs no game and decides no
legality. Running the replay itself is integration work (WP8).

Verdicts (`R7Report.stops_output`): a `divergence` stops this build's output
(originals kept, owner investigates, trained data is not re-judged). Two
classes are reported but never stop output: `display_text_number` (only
numbers inside display strings differ, S1-A1 §4) and `score_drift` (scores
differ beyond tolerance: CPU fp32 replay vs GPU forward; R4, the lead judges by
learning, not bit equality).

Comparators:
- `compare_action_records`: per step de-handled input, content hash (compared
  separately), chosen de-handled option, receipt, native steps, score multiset
  and selected score, then decisions and terminal. Duplicate identical options
  match by the n-th-equal rule and are marked ambiguous; a later fork after an
  ambiguous match is a divergence (no relaxed comparison).
- `compare_compact_reconstruction`: native compact reconstruction against the
  untouched legacy full writer (design v2 §4.1): key order kept, compact UTF-8,
  `native-N` renamed `ref-k` by first appearance across the game on each side
  (a bijection: two refs collapsing into one is caught), `facts` and
  `diagnostics` not compared.
- `compare_encoded_row`: a fast `stpd/rows-v2-row-1` row against the Python
  reference (`FrameCompiler.compile` -> `encode_row`); bytes first, tensor-level
  first difference for the report.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LONG_HEX = re.compile(r"[0-9a-f]{24,64}")
ENTITY = re.compile(r"^e\d+$")
NATIVE_REF = re.compile(r"^native-[1-9][0-9]*$")
DIGITS = re.compile(r"\d+")
NOT_COMPARED = frozenset({"facts", "diagnostics"})


# --------------------------------------------------------------------- sampler
@dataclass
class R7Sampler:
    """Fixed per-hour choice of R7 games, decided before each game opens.

    At the start of every window the sampler draws which game ordinals of that
    window are R7 (`quota` of `expected_games`; a fractional quota is rounded
    by the sampler's own RNG). `select()` is called once per game, before it
    opens, and depends only on the window and the ordinal: never on the seed,
    the game content, the model or anything the inference side sees. If fewer
    games open than expected, fewer are chosen; the report says so.

    `key` is the sampler's own secret (not a game seed); one sampler per game
    manager, whose share of the global 10-20/h the launcher sets as `quota`.
    """

    key: str
    quota: float
    expected_games: int
    window_seconds: float = 3600.0
    window: int | None = None
    chosen: frozenset[int] = frozenset()
    ordinal: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if (not isinstance(self.key, str) or not self.key or type(self.expected_games) is not int
                or self.expected_games < 1 or not math.isfinite(self.quota)
                or not 0 <= self.quota <= self.expected_games
                or not 0 < self.window_seconds <= 86400):
            raise ValueError("invalid_r7_sampler")

    def _roll(self, window: int) -> None:
        if self.window is not None:
            self.history.append({"window": self.window, "opened": self.ordinal,
                                 "chosen": len(self.chosen),
                                 "replayed_by_ordinal": sorted(i for i in self.chosen
                                                               if i < self.ordinal)})
        rng = random.Random(hashlib.sha256(f"{self.key}\0r7\0{window}".encode()).digest())
        count = int(self.quota) + int(rng.random() < self.quota - int(self.quota))
        self.window, self.ordinal = window, 0
        self.chosen = frozenset(rng.sample(range(self.expected_games), count))

    def select(self, now: float) -> bool:
        window = int(now // self.window_seconds)
        if window != self.window:
            if self.window is not None and window < self.window:
                raise ValueError("r7_sampler_clock_went_backwards")
            self._roll(window)
        chosen = self.ordinal in self.chosen
        self.ordinal += 1
        return chosen

    def report(self) -> dict[str, Any]:
        current = {"window": self.window, "opened": self.ordinal,
                   "chosen_so_far": sum(1 for i in self.chosen if i < self.ordinal),
                   "chosen": len(self.chosen)}
        return {"quota": self.quota, "expected_games": self.expected_games,
                "history": list(self.history), "current": current}


# ------------------------------------------------------------------ helpers
def dehandle(value: Any) -> Any:
    """Replace per-process handles with placeholders (U4-1/S1 rule, recursive)."""
    if isinstance(value, dict):
        return {key: dehandle(item) for key, item in value.items()}
    if isinstance(value, list):
        return [dehandle(item) for item in value]
    if isinstance(value, str):
        if ENTITY.match(value):
            return "<e>"
        if NATIVE_REF.match(value):
            return "<native>"
        return LONG_HEX.sub("<hex>", UUID.sub("<uuid>", value))
    return value


def compact_json(value: Any) -> bytes:
    """Key order kept, no whitespace, UTF-8 (never sorted: order is part of the input)."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def _differences(left: Any, right: Any, path: str = "$") -> list[tuple[str, Any, Any]]:
    if type(left) is not type(right):
        return [(path, left, right)]
    if isinstance(left, dict):
        if list(left) != list(right):
            return [(path + ".<keys>", list(left), list(right))]
        return [d for key in left for d in _differences(left[key], right[key], f"{path}.{key}")]
    if isinstance(left, list):
        if len(left) != len(right):
            return [(path + ".<length>", len(left), len(right))]
        return [d for i, (a, b) in enumerate(zip(left, right, strict=True))
                for d in _differences(a, b, f"{path}[{i}]")]
    return [] if left == right else [(path, left, right)]


def _only_display_numbers(differences: list[tuple[str, Any, Any]]) -> bool:
    return bool(differences) and all(
        isinstance(a, str) and isinstance(b, str) and DIGITS.sub("#", a) == DIGITS.sub("#", b)
        for _, a, b in differences)


@dataclass
class Finding:
    step: int | None
    kind: str
    category: str  # divergence | display_text_number | score_drift | ambiguous_match
    detail: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "kind": self.kind, "category": self.category,
                "detail": self.detail}


@dataclass
class R7Report:
    compared_steps: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def stops_output(self) -> bool:
        return any(f.category == "divergence" for f in self.findings)

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for finding in self.findings:
            result[finding.category] = result.get(finding.category, 0) + 1
        return result

    def as_dict(self) -> dict[str, Any]:
        return {"compared_steps": self.compared_steps, "stops_output": self.stops_output,
                "counts": self.counts(), "findings": [f.as_dict() for f in self.findings]}


# ----------------------------------------------------- action-record replay
def _scores_close(left: Sequence[float], right: Sequence[float], tolerance: float) -> bool:
    return len(left) == len(right) and all(
        math.isclose(a, b, rel_tol=0, abs_tol=tolerance)
        for a, b in zip(sorted(left), sorted(right), strict=True))


def compare_action_records(fast: Mapping[str, Any], replay: Mapping[str, Any], *,
                           score_tolerance: float = 1e-4) -> R7Report:
    """Compare a fast game and its complete-chain replay, both as R7 records.

    A record is `{"steps": [...], "decisions": int, "native_steps": int,
    "terminal": {"result": ...} | None}`; a step is
    `{"step_index", "input", "content_sha256", "choice": {"content", "occurrence",
    "ambiguous"}, "receipt": {"delivery", "reason"}, "native_steps", "scores",
    "selected_score"}`. `input` is the Agent input as recorded (handles are removed
    here); `content_sha256` is the content hash each chain computed itself.
    """
    report = R7Report()
    ambiguous_seen = False
    fast_steps, replay_steps = list(fast["steps"]), list(replay["steps"])
    for left, right in zip(fast_steps, replay_steps, strict=False):
        step = left["step_index"]
        report.compared_steps += 1
        if left["step_index"] != right["step_index"]:
            report.findings.append(Finding(step, "step_index", "divergence",
                                           [left["step_index"], right["step_index"]]))
            break
        differences = _differences(dehandle(left["input"]), dehandle(right["input"]))
        if differences:
            if _only_display_numbers(differences):
                report.findings.append(Finding(step, "input", "display_text_number",
                                               [d[0] for d in differences]))
            else:
                report.findings.append(Finding(step, "input", "divergence", {
                    "after_ambiguous_match": ambiguous_seen,
                    "paths": [d[0] for d in differences[:8]]}))
        if left["content_sha256"] != right["content_sha256"] and not differences:
            report.findings.append(Finding(step, "content_sha256", "divergence"))
        left_choice, right_choice = left["choice"], right["choice"]
        if (dehandle(left_choice["content"]) != dehandle(right_choice["content"])
                or left_choice["occurrence"] != right_choice["occurrence"]):
            report.findings.append(Finding(step, "choice", "divergence"))
        if left_choice.get("ambiguous") or right_choice.get("ambiguous"):
            ambiguous_seen = True
            report.findings.append(Finding(step, "choice", "ambiguous_match"))
        if left["receipt"] != right["receipt"]:
            report.findings.append(Finding(step, "receipt", "divergence",
                                           [left["receipt"], right["receipt"]]))
        if left["native_steps"] != right["native_steps"]:
            report.findings.append(Finding(step, "native_steps", "divergence",
                                           [left["native_steps"], right["native_steps"]]))
        if not _scores_close(left["scores"], right["scores"], score_tolerance):
            kind = ("score_multiset" if len(left["scores"]) == len(right["scores"])
                    else "score_count")
            report.findings.append(Finding(step, kind, "score_drift" if kind == "score_multiset"
                                           else "divergence"))
        elif not math.isclose(left["selected_score"], right["selected_score"], rel_tol=0,
                              abs_tol=score_tolerance):
            report.findings.append(Finding(step, "selected_score", "score_drift"))
        if any(f.category == "divergence" and f.step == step for f in report.findings):
            break  # later steps of a forked game are not comparable
    else:
        if len(fast_steps) != len(replay_steps):
            report.findings.append(Finding(None, "step_count", "divergence",
                                           [len(fast_steps), len(replay_steps)]))
    if not report.stops_output:
        for key in ("decisions", "native_steps", "terminal"):
            if fast[key] != replay[key]:
                report.findings.append(Finding(None, key, "divergence", [fast[key], replay[key]]))
    return report


# ------------------------------------------------ compact vs legacy states
def rename_native_refs(states: Iterable[Any]) -> list[bytes]:
    """Step-persistent `native-N` -> `ref-k` by first appearance; `facts`/`diagnostics` dropped."""
    names: dict[str, str] = {}

    def walk(value: Any, top: bool) -> Any:
        if isinstance(value, dict):
            return {key: walk(item, False) for key, item in value.items()
                    if not (top and key in NOT_COMPARED)}
        if isinstance(value, list):
            return [walk(item, False) for item in value]
        if isinstance(value, str) and NATIVE_REF.match(value):
            return names.setdefault(value, f"ref-{len(names) + 1}")
        return value

    return [compact_json(walk(state, True)) for state in states]


def compare_compact_reconstruction(reconstructed: Sequence[Any],
                                   legacy: Sequence[Any]) -> R7Report:
    report = R7Report()
    left, right = rename_native_refs(reconstructed), rename_native_refs(legacy)
    for step, (a, b) in enumerate(zip(left, right, strict=False)):
        report.compared_steps += 1
        if a != b:
            paths = [d[0] for d in _differences(json.loads(a), json.loads(b))[:8]]
            report.findings.append(Finding(step, "compact_reconstruction", "divergence", paths))
            break
    if len(left) != len(right) and not report.stops_output:
        report.findings.append(Finding(None, "state_count", "divergence",
                                       [len(left), len(right)]))
    return report


# ------------------------------------------------------ encoding vs reference
def reference_row(compiler: Any, observation: Mapping[str, Any]) -> bytes:
    """The Python reference row for one Agent observation (today's compile path)."""
    from stpd.p1.rows_encoder_wire import encode_row

    return encode_row(compiler.compile(observation))


def compare_encoded_row(fast_row: bytes, fast_row_sha256: str, reference: bytes, *,
                        validator: Any) -> Finding | None:
    """None when the fast row equals the Python reference row byte for byte.

    `fast_row_sha256` is the `payload.row_sha256` the fast chain recorded, so the
    audited bytes are the bytes the service scored. `validator` is the bound
    `FrameCompiler` (decode_row's required bounds check).
    """
    import torch

    from stpd.p1.rows_encoder_wire import decode_row

    if bytes(fast_row) == reference:
        from stpd.p1.rows_encoder_wire import row_sha256
        if row_sha256(fast_row) != fast_row_sha256:
            return Finding(None, "encoded_row", "divergence", "recorded_row_sha256_mismatch")
        return None
    try:
        decoded = decode_row(fast_row, validator=validator, expected_sha256=fast_row_sha256)
    except ValueError as error:
        return Finding(None, "encoded_row", "divergence", {"decode": str(error)})
    expected = decode_row(reference, validator=validator, expected_sha256=None)
    if decoded.choices != expected.choices or decoded.entity_count != expected.entity_count:
        return Finding(None, "encoded_row", "divergence", "choices_or_entity_count")
    if decoded.tensors.keys() != expected.tensors.keys() or \
            decoded.reductions.keys() != expected.reductions.keys():
        return Finding(None, "encoded_row", "divergence", "tensor_names")
    for name, tensor in expected.tensors.items():
        other = decoded.tensors[name]
        if other.dtype != tensor.dtype or other.shape != tensor.shape \
                or not torch.equal(other, tensor):
            return Finding(None, "encoded_row", "divergence", f"tensor:{name}")
    for name, reduction in expected.reductions.items():
        bucketed = decoded.reductions[name]
        if not torch.equal(bucketed.inverse, reduction.inverse) or len(
                bucketed.buckets) != len(reduction.buckets) or any(
                (a[0], a[1]) != (b[0], b[1]) or not torch.equal(a[2], b[2])
                for a, b in zip(bucketed.buckets, reduction.buckets, strict=True)):
            return Finding(None, "encoded_row", "divergence", f"reduction:{name}")
    return Finding(None, "encoded_row", "divergence", "bytes_differ_tensors_equal")
