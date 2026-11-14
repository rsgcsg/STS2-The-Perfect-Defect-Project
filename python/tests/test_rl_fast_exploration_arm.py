"""An exploration arm (T != 1, uniform mix != 0) from the service reply to the segment.

NEW-METHODS-PLAN I4: a ladder arm's sampling must be what the rows record. One game with
the plan's draft "high" arm runs through the real service reply (`_scored`: behavior q,
draw, applied), the real assembler and segment format 3, then decode; applied and
behavior_q must come back as the service computed them, for both decision classes.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("torch")

from stpd.p1.inference_service import FastInferenceService  # noqa: E402
from stpd.p1.sampling import distribution, draw  # noqa: E402
from stpd.rl.fast_assembly import FastSegmentAssembler, MuHandoff  # noqa: E402
from stpd.rl.fast_segment import FORMAT_3, decode_segment  # noqa: E402
from stpd.rl.heads import OUTCOME_CLASSES  # noqa: E402

# As the env driver sends it: the profile's arm through JSON (fast env -> link -> service).
HIGH = json.loads(json.dumps({
    "mode": "temperature_uniform", "seed": "wp8-n0-p3", "arm_id": "high",
    "classes": {"combat": {"temperature": 1.4, "uniform_mix": 0.005},
                "noncombat": {"temperature": 2.5, "uniform_mix": 0.01}}}))


def values() -> list[float]:
    out = [0.0] * OUTCOME_CLASSES
    out[1], out[49] = 0.5, 0.5
    return out


def test_high_arm_applied_and_behavior_q_survive_service_assembler_and_segment():
    sealed: list[Any] = []
    assembler = FastSegmentAssembler(
        assembler_id="node-a", emit=sealed.append, segment_format=FORMAT_3,
        compiler_identity={"input_spec": {"id": "test"}, "vocabulary_sha256": "a" * 64,
                           "compiler_sha256": "b" * 64})
    assembler.offer_mu(MuHandoff(hashlib.sha256(b"mu").hexdigest(), "gen-1", 1, 1))
    assembler.open_episode({
        "schema": "stpd/rl-inference-4", "kind": "episode_open", "request_id": 1,
        "game_instance_id": "g1", "actor_id": "v3g-n0-p3", "sampling": HIGH,
        "single_choice": "omit", "start": {"kind": "new_run"}, "ascension": 0})
    replies: list[dict[str, Any]] = []
    service = SimpleNamespace(assembler=assembler, stats={"rows": 0, "refused": {}},
                              _complete=lambda item, result: replies.append(result),
                              _refuse=lambda item, code, **_: pytest.fail(code))
    logits = {}
    for step in range(65):  # 64 rows seal a bootstrap segment on the 65th
        data = f"row-{step}".encode() * 3
        row = {"request_id": step + 1, "game_instance_id": "g1", "step_index": step,
               "consumption_id": f"c-{step}", "catalog_len": 3,
               "catalog_sha256": hashlib.sha256(f"cat-{step}".encode()).hexdigest(),
               "observation_sha256": hashlib.sha256(data).hexdigest(),
               "in_combat": step % 2 == 0, "floor": 1,
               "payload": {"offset": 0, "length": len(data),
                           "row_sha256": hashlib.sha256(data).hexdigest()}}
        plan = assembler.begin_row(row, data)
        logits[step] = [0.1 * (step % 7), 1.5, -0.8]
        FastInferenceService._scored(
            service, SimpleNamespace(row=row, plan=plan, sampling=HIGH),
            SimpleNamespace(scores=logits[step], outcome_probabilities=values()),
            values() if plan.tail is not None else None)
        assembler.step_outcome({
            "schema": "stpd/rl-inference-4", "kind": "step_outcome",
            "row_request_id": row["request_id"], "game_instance_id": "g1",
            "step_index": step, "status": "completed", "receipt_sha256": "c" * 64,
            "terminal": None, "public_facts": {"act": 1, "floor": 1, "hp": 50, "max_hp": 75}})
    assert service.stats == {"rows": 65, "refused": {}}
    [segment] = sealed
    header, _ = decode_segment(segment.data, expected_sha256=segment.sha256)
    assert header["format"] == FORMAT_3 and header["sampling"] == HIGH
    assert header["seal"]["kind"] == "bootstrap" and len(header["rows"]) == 64
    for step, meta in enumerate(header["rows"]):
        reply = replies[step]
        decision = "combat" if step % 2 == 0 else "noncombat"
        policy = HIGH["classes"][decision]
        applied = {"arm_id": "high", "decision_class": decision, **policy}
        _, q = distribution(logits[step], policy["temperature"], policy["uniform_mix"])
        _, plain = distribution(logits[step], 1.0, 0.0)
        assert reply["applied"] == meta["applied"] == applied
        assert reply["behavior_q"] == meta["behavior_q"] == q != plain
        assert meta["selected_index"] == reply["selected_index"] == \
            draw(q, HIGH["seed"], f"c-{step}")
        assert meta["selected_q"] == q[meta["selected_index"]]
