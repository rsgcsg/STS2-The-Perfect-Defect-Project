"""R7 faithful audit tools for fast_chain: sampler and comparators (design v2 §2.9).

R7 replays 10-20 fast-chain games per hour, chosen before they open, through
today's complete chain (the evaluation entry) following the fast game's
de-handled action record, and compares step by step. This module owns the
choice of games and the comparison verdicts; it runs no game and decides no
legality. Running the replay itself is integration work (WP8).

Verdicts (`R7Report.stops_output`): a `divergence` stops this build's output
(originals kept, owner investigates, trained data is not re-judged). Two
classes are reported but never stop output: `display_text_number` (only
numbers differ, and only on a whitelisted display-only path, `DISPLAY_ONLY_TEXT`)
and `score_drift` (finite scores differ beyond tolerance: CPU fp32 replay vs
GPU forward; R4, the lead judges by learning, not bit equality). A non-finite
score and a record of the wrong shape are divergences.

Comparators:
- `compare_action_records`: per step de-handled input, content hash (compared
  separately), chosen de-handled option, receipt, native steps, score multiset
  and selected score, then decisions and terminal. Duplicate identical options
  match by the n-th-equal rule and are marked ambiguous; a later fork after an
  ambiguous match is a divergence (no relaxed comparison).
  `single_choice: omit` (L1): a fast record lists its forced single-choice steps in
  order with `forced: true`, `step_index: null`, no scores and no selected score;
  the env counts only model rows in `decisions` and `step_index`. Both sides are
  aligned by position (every P1 choice either chain acted on); a forced step is
  compared like any other except scores, and the replay must have had exactly one
  option there. `decisions` is compared as model rows plus forced steps, so a
  score-mode replay (today's complete chain) and an omit fast game agree.
- `compare_compact_reconstruction`: native compact reconstruction against the
  untouched legacy full writer (design v2 §4.1): key order kept, compact UTF-8,
  `native-N` renamed `ref-k` by first appearance across the game on each side
  (a bijection: two refs collapsing into one is caught), `facts` and
  `diagnostics` not compared.
- `compare_encoded_row`: a fast `stpd/rows-v2-row-1` row against the Python
  reference (`FrameCompiler.compile` -> `encode_row`); bytes first, tensor-level
  first difference for the report.
- `compare_terminal_label`: the fast public terminal label equals the old
  chain's label game by game (lead decision 2026-11-09).
- `public_battle_hp` / `compare_hp_labels`: the candidate public hp auxiliary
  rule and its per-battle check against the old chain; the rule is not emitted
  until this check passes on the equivalence corpus.
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

# Display-only text whose numbers may differ between two runs of one seed, by
# scene family (S1-A1 §4: the event body, e.g. the Trial's "474号进入者", which
# the native draws per process). Card, power, relic, option, label and every
# other text is model input (`FrameCompiler` -> `text_numbers`): a number change
# there is a divergence. A path not listed here fails closed. The table is the
# lead's to extend, with the evidence that the text is display-only.
DISPLAY_ONLY_TEXT: Mapping[str, frozenset[str]] = {"event": frozenset({"$.scene.prompt"})}

RECORD_KEYS = frozenset({"steps", "decisions", "native_steps", "terminal"})
STEP_KEYS = frozenset({"step_index", "input", "content_sha256", "choice", "receipt",
                       "native_steps", "scores", "selected_score"})


# --------------------------------------------------------------------- sampler
@dataclass
class R7Sampler:
    """Fixed per-hour choice of R7 games, decided before each game opens.

    At the start of every window the sampler draws which game ordinals of that
    window are R7 (`quota` of `expected_games`; a fractional quota is rounded
    by the sampler's own RNG). `select()` is called once per game, before it
    opens, and depends only on the window and the ordinal: never on the seed,
    the game content, the model or anything the inference side sees. If fewer
    games open than expected, fewer are chosen; the report says so. Games
    opened beyond `expected_games` are never chosen; the report counts them as
    `overflow` (raise `expected_games` when it is not 0).

    A restarted manager continues a window with `resume(now, opened)`, the
    games it already opened in it (the launcher persists that count). Without
    it the ordinals restart at 0 and the window may choose more than `quota`;
    this changes coverage only, never which information the choice uses.

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
    resumed_at: int | None = None

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
                                 "chosen_and_opened": sorted(i for i in self.chosen
                                                             if i < self.ordinal),
                                 "overflow": max(0, self.ordinal - self.expected_games),
                                 "resumed_at": self.resumed_at})
        rng = random.Random(hashlib.sha256(f"{self.key}\0r7\0{window}".encode()).digest())
        count = int(self.quota) + int(rng.random() < self.quota - int(self.quota))
        self.window, self.ordinal, self.resumed_at = window, 0, None
        self.chosen = frozenset(rng.sample(range(self.expected_games), count))

    def _enter(self, now: float) -> None:
        window = int(now // self.window_seconds)
        if window != self.window:
            if self.window is not None and window < self.window:
                raise ValueError("r7_sampler_clock_went_backwards")
            self._roll(window)

    def resume(self, now: float, opened: int) -> None:
        """After a restart: `opened` games of the current window were already opened."""
        if self.window is not None or type(opened) is not int or opened < 0:
            raise ValueError("r7_sampler_resume_only_before_first_select")
        self._enter(now)
        self.ordinal = self.resumed_at = opened

    def select(self, now: float) -> bool:
        self._enter(now)
        chosen = self.ordinal in self.chosen
        self.ordinal += 1
        return chosen

    def report(self) -> dict[str, Any]:
        current = {"window": self.window, "opened": self.ordinal,
                   "chosen_so_far": sum(1 for i in self.chosen if i < self.ordinal),
                   "chosen": len(self.chosen),
                   "overflow": max(0, self.ordinal - self.expected_games),
                   "resumed_at": self.resumed_at}
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


def _family(value: Any) -> Any:
    scene = value.get("scene") if isinstance(value, Mapping) else None
    return scene.get("family") if isinstance(scene, Mapping) else None


def _only_display_numbers(differences: list[tuple[str, Any, Any]], left: Any,
                          right: Any) -> bool:
    """Only digits differ, and only on `DISPLAY_ONLY_TEXT` paths of this scene family."""
    family = _family(left)
    paths = DISPLAY_ONLY_TEXT.get(family, frozenset()) if family == _family(right) else ()
    return bool(differences) and all(
        path in paths and isinstance(a, str) and isinstance(b, str)
        and DIGITS.sub("#", a) == DIGITS.sub("#", b)
        for path, a, b in differences)


def _number(value: Any) -> bool:
    return type(value) in (int, float)


def _forced(step: Mapping[str, Any]) -> bool:
    return step.get("forced") is True


def _record_shape(record: Any) -> str | None:
    """The first shape problem of one R7 record, or None."""
    if not isinstance(record, Mapping) or not set(record) >= RECORD_KEYS:
        return "record_keys"
    if not isinstance(record["steps"], list):
        return "steps"
    model_rows = 0
    omitted = any(isinstance(s, Mapping) and _forced(s) for s in record["steps"])
    for index, step in enumerate(record["steps"]):
        if not isinstance(step, Mapping) or not set(step) >= STEP_KEYS:
            return f"steps[{index}].keys"
        choice, scores = step["choice"], step["scores"]
        if (not isinstance(choice, Mapping) or not {"content", "occurrence"} <= set(choice)
                or step.get("forced", False) not in (True, False)):
            return f"steps[{index}].types"
        if _forced(step):
            # An omitted single choice: no row, no score; it was the only option.
            if step["step_index"] is not None or scores != [] or step["selected_score"] is not None:
                return f"steps[{index}].forced"
            continue
        if (type(step["step_index"]) is not int or not isinstance(scores, list)
                or not all(_number(v) for v in scores) or not _number(step["selected_score"])):
            return f"steps[{index}].types"
        # A record with forced steps numbers model rows only (the env's own counter).
        if omitted and step["step_index"] != model_rows:
            return f"steps[{index}].step_index"
        model_rows += 1
    return None


def _choices_acted(record: Mapping[str, Any]) -> Any:
    """Model rows plus forced single choices: what a score-mode chain counts as decisions."""
    decisions = record["decisions"]
    if type(decisions) is not int:
        return decisions
    return decisions + sum(1 for step in record["steps"] if _forced(step))


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
    for side, record in (("fast", fast), ("replay", replay)):
        problem = _record_shape(record)
        if problem is not None:
            report.findings.append(Finding(None, "record_shape", "divergence",
                                           {"side": side, "problem": problem}))
    if report.findings:
        return report
    ambiguous_seen = False
    fast_steps, replay_steps = list(fast["steps"]), list(replay["steps"])
    # With forced steps on either side, steps align by position (`step` = position) and
    # the per-side `step_index` (model rows only) is checked by `_record_shape`.
    by_position = any(_forced(s) for s in (*fast_steps, *replay_steps))
    for position, (left, right) in enumerate(zip(fast_steps, replay_steps, strict=False)):
        step = position if by_position else left["step_index"]
        report.compared_steps += 1
        if not by_position and left["step_index"] != right["step_index"]:
            report.findings.append(Finding(step, "step_index", "divergence",
                                           [left["step_index"], right["step_index"]]))
            break
        forced = _forced(left) or _forced(right)
        if forced and not all(_forced(side) or len(side["scores"]) == 1
                              for side in (left, right)):
            # One side omitted this choice as single, the other had several options.
            report.findings.append(Finding(step, "forced_single_choice", "divergence",
                                           [len(left["scores"]), len(right["scores"])]))
            break
        differences = _differences(dehandle(left["input"]), dehandle(right["input"]))
        if differences:
            if _only_display_numbers(differences, left["input"], right["input"]):
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
        if forced:
            pass  # an omitted single choice has no score to compare
        elif not all(math.isfinite(v) for side in (left, right)
                     for v in (*side["scores"], side["selected_score"])):
            report.findings.append(Finding(step, "non_finite_score", "divergence"))
        elif not _scores_close(left["scores"], right["scores"], score_tolerance):
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
        if _choices_acted(fast) != _choices_acted(replay):
            report.findings.append(Finding(None, "decisions", "divergence",
                                           [fast["decisions"], replay["decisions"]]))
        for key in ("native_steps", "terminal"):
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
    # The reference bytes were just encoded here from the observation: there is
    # no recorded digest to bind them to (the fast row above is bound to its own).
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


# ----------------------------------------------------------- labels vs old chain
def compare_terminal_label(fast: Mapping[str, Any] | None,
                           legacy: Mapping[str, Any] | None) -> Finding | None:
    """Fast segment `seal.terminal` against the old chain's terminal label, per game.

    Both are None when neither chain labelled the game (no natural terminal, or
    the class check refused it). Class and reward must be equal exactly: both
    chains run the same reward v2 arithmetic and `checked_terminal_class`.
    """
    left = None if fast is None else (fast["class"], fast["reward"])
    right = None if legacy is None else (legacy["class"], legacy["reward"])
    if left == right:
        return None
    return Finding(None, "terminal_label", "divergence", [left, right])


def public_battle_hp(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Candidate hp auxiliary rule (lead 2026-11-09), from one game's fast rows in order.

    A battle is a maximal run of combat rows; it ends where the next row is not
    in combat (or the run's last row carried the natural terminal). Its label is
    the public hp in that last combat row's step_outcome (the successor state).
    No combat_end is read. Not emitted: it is enabled only after
    `compare_hp_labels` shows it equal to the old chain on the equivalence corpus.
    """
    battles: list[dict[str, Any]] = []
    steps: list[int] = []
    for index, row in enumerate(rows):
        if row["in_combat"]:
            steps.append(row["step_index"])
        following = rows[index + 1] if index + 1 < len(rows) else None
        outcome = row.get("outcome") or {}
        ends = bool(steps) and row["in_combat"] and (
            (following is not None and not following["in_combat"])
            or (following is None and outcome.get("terminal") is not None))
        if ends:
            hp = (outcome.get("public_facts") or {}).get("hp")
            if type(hp) is int:
                battles.append({"steps": list(steps), "post_hp": hp})
            steps = []
        elif not row["in_combat"]:
            steps = []
    return battles


def compare_hp_labels(fast: Sequence[Mapping[str, Any]],
                      legacy: Sequence[Mapping[str, Any]], *,
                      legacy_single_choice_steps: Iterable[int] = ()) -> Finding | None:
    """Per battle: the labelled state steps and the post-combat public hp, in order.

    Under `single_choice: omit` the fast rows skip single-choice states and number model
    rows only. Pass the old chain's single-choice step indices: they are dropped from the
    legacy battles and the rest renumbered as model rows, so both sides name the same
    rows. The post-combat hp is compared unchanged: a fast outcome after a forced chain
    describes the next decision state, so an hp change inside that chain is reported.
    """
    single = sorted(set(legacy_single_choice_steps))

    def renumber(steps: Iterable[int]) -> list[int]:
        import bisect
        return [step - bisect.bisect_left(single, step) for step in steps
                if bisect.bisect_left(single, step) == bisect.bisect_right(single, step)]

    left = [(list(b["steps"]), b["post_hp"]) for b in fast]
    right = [(renumber(b["steps"]), b["post_hp"]) for b in legacy]
    right = [battle for battle in right if battle[0]]
    if left == right:
        return None
    first = next((i for i, (a, b) in enumerate(zip(left, right, strict=False)) if a != b),
                 min(len(left), len(right)))
    return Finding(None, "hp_labels", "divergence",
                   {"first_battle": first, "fast": len(left), "legacy": len(right)})
