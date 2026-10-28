"""Public P1 trajectory v1, independent of a future RL learner or sampler.

The current producer is deterministic M-std: probability one for its argmax,
no temperature, and no value head. Missing model outputs remain explicit nulls.
"""
from __future__ import annotations

import json
import math
from typing import Any, NoReturn, cast

from sts2_platform_evidence import AgentSessionRunEvidence, read_global_artifact

from spireagent.json_boundary import BoundaryError, json_bytes

LEGACY_SCHEMA = "stpd/p1-trajectory-1"
SCHEMA = "stpd/p1-trajectory-2"
MAX_EPISODE_BYTES = 256 * 1024**2
IDENTITY_FIELDS = {"producer", "host_pin", "runtime_pin", "model_artifact_id", "model_sha256",
                   "input_spec", "seed", "ascension", "character", "initial_progress_sha256",
                   "retention", "behavior_policy"}


def reject(reason: str) -> NoReturn:
    raise BoundaryError("trajectory", reason)


def _finite(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def softmax(scores: list[float]) -> list[float]:
    if not scores or not all(_finite(v) for v in scores):
        reject("invalid_scores")
    weights = [math.exp(v - max(scores)) for v in scores]
    total = sum(weights)
    return [v / total for v in weights]


def validate_behavior(behavior: dict[str, Any], count: int, selected: int) -> None:
    """Check recorded probabilities; this function never chooses an action."""
    probabilities = behavior.get("probabilities")
    raw = behavior.get("model_probabilities")
    if (type(count) is not int or count < 1 or type(selected) is not int
            or not 0 <= selected < count):
        reject("invalid_selection")
    for vector in (probabilities, raw):
        if (not isinstance(vector, list) or len(vector) != count
                or not all(_finite(v) and 0 <= v <= 1 for v in vector)
                or not math.isclose(sum(vector), 1, abs_tol=1e-7)):
            reject("invalid_probabilities")
    probabilities = cast(list[float], probabilities)
    raw = cast(list[float], raw)
    if behavior.get("model_logits") is not None:
        derived = softmax(behavior["model_logits"])
        if len(derived) != count or any(not math.isclose(a, b, abs_tol=1e-7)
                                        for a, b in zip(derived, raw, strict=True)):
            reject("model_probability_mismatch")
    if behavior.get("mode") == "argmax":
        if behavior.get("temperature") is not None or behavior.get("uniform_mix") != 0:
            reject("argmax_is_not_sampling")
        expected = [float(i == selected) for i in range(count)]
        if selected != max(range(count), key=raw.__getitem__):
            reject("argmax_selection_changed")
    elif behavior.get("mode") == "temperature_uniform":
        temperature: Any = behavior.get("temperature")
        mix: Any = behavior.get("uniform_mix")
        if (not _finite(temperature) or not 1 <= temperature <= 1.3
                or not _finite(mix) or not 0 <= mix <= 1):
            reject("invalid_sampling_parameters")
        temperature, mix = cast(float, temperature), cast(float, mix)
        weights = [p ** (1 / temperature) for p in raw]
        expected = [(1 - mix) * w / sum(weights) + mix / count for w in weights]
    else:
        reject("unsupported_behavior")
        return
    if (any(not math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-9)
            for a, b in zip(probabilities, expected, strict=True))
            or not _finite(behavior.get("selected_probability"))
            or not math.isclose(behavior["selected_probability"], probabilities[selected],
                                rel_tol=1e-7, abs_tol=1e-9)
            or probabilities[selected] <= 0):
        reject("behavior_probability_mismatch")


def _optional_public_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        reject("invalid_boss_witness_source")
    return value


def boss_clearance(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Count only public consecutive-act advances or completed Boss rewards."""
    witnesses = []
    cleared: set[int] = set()
    previous = None
    for item in observations:
        if not isinstance(item, dict) or type(item.get("index")) is not int:
            reject("invalid_boss_witness_source")
        observation = _optional_public_object(item.get("observation"))
        run_part = _optional_public_object(observation.get("run"))
        run = _optional_public_object(run_part.get("content"))
        act = run.get("act")
        if type(act) is not int or act < 1:
            continue
        if previous is not None and act == previous + 1 and previous not in cleared:
            cleared.add(previous)
            witnesses.append({"kind": "public_act_advance", "act": previous,
                              "observation_index": item["index"], "next_act": act})
        scene = _optional_public_object(observation.get("scene"))
        content = _optional_public_object(scene.get("content"))
        surface = _optional_public_object(content.get("surface"))
        if (scene.get("native_kind") == "reward_completion" and surface.get("is_boss") is True
                and act not in cleared):
            cleared.add(act)
            witnesses.append({"kind": "completed_boss_reward", "act": act,
                              "observation_index": item["index"]})
        previous = act
    return {"count": len(cleared) if cleared else None,
            "cleared_acts": sorted(cleared), "witnesses": witnesses}


def trajectory_score_eligible(identity: dict[str, Any]) -> bool:
    retention = identity.get("retention") or {}
    return (retention.get("schema") == "spireagent/managed-retention-v2"
            and retention.get("tier") == "trajectory"
            and retention.get("purpose") == "evaluation"
            and retention.get("score_eligible") is True)


def extract_trajectory(verified: AgentSessionRunEvidence, *, identity: dict[str, Any],
                       outcome: dict[str, Any], status: str, reason: str | None,
                       max_floor: int | None) -> dict[str, Any]:
    """Project only the already typed-verified original; never query a live Host."""
    if verified.agent_manifest.get("adapter", {}).get("id") != "stpd-p1-mstd-agent":
        reject("unsupported_trajectory_producer")
    root = verified.directory
    inputs: dict[str, int] = {}
    observations: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    directive = None
    results = {}
    for line in (root / "events.jsonl").read_bytes().splitlines():
        event = json.loads(line)
        payload, kind = event["payload"], event["kind"]
        if kind == "global_input":
            value = json.loads(read_global_artifact(root, payload["artifact"]))
            observation = value["observation"]
            if observation["catalog"] != value["catalog"]:
                reject("incomplete_catalog")
            inputs[payload["acquisition_id"]] = len(observations)
            observations.append({"index": len(observations), "event_sequence": event["sequence"],
                "acquisition_id": payload["acquisition_id"],
                "source_ordinal": payload["source_ordinal"],
                "occurrence": payload["occurrence"], "consumption_unit": value["consumption_unit"],
                "input_spec": value["input_spec"], "observation": observation,
                "combat": observation.get("combat"), "scene": observation.get("scene")})
        elif kind == "agent_directive":
            directive = payload["output"]["directive"]
        elif kind == "global_choice":
            if (directive is None or directive["type"] != "act"
                    or directive["basis_acquisition_id"] != payload["acquisition_id"]
                    or directive["selection"]["action_id"] != payload["choice_id"]):
                reject("choice_without_original_directive")
            index = inputs[payload["acquisition_id"]]
            catalog = observations[index]["observation"]["catalog"]
            selected = [i for i, c in enumerate(catalog) if c["choice_id"] == payload["choice_id"]]
            scores = directive.get("scores", {}).get("values")
            if len(selected) != 1 or not isinstance(scores, list) or len(scores) != len(catalog):
                reject("complete_original_scores_required")
            probabilities = softmax(scores)
            behavior = {"mode": "argmax", "temperature": None, "uniform_mix": 0,
                "probabilities": [float(i == selected[0]) for i in range(len(catalog))],
                "selected_probability": 1.0, "model_probabilities": probabilities,
                "model_probability_source": "softmax_of_recorded_logits",
                "model_logits": scores}
            validate_behavior(behavior, len(catalog), selected[0])
            steps.append({"index": len(steps), "observation_index": index,
                "acquisition_id": payload["acquisition_id"], "event_sequence": event["sequence"],
                "action_id": payload["choice_id"], "action_index": selected[0],
                "behavior": behavior, "state_value": None, "action_values": None,
                "value_status": "model_has_no_value_head"})
        elif kind == "global_result":
            results[payload["acquisition_id"]] = payload
    for i, step in enumerate(steps):
        after = (steps[i + 1]["observation_index"] if i + 1 < len(steps)
                 else len(observations) - 1)
        step["next_step_index"] = i + 1 if i + 1 < len(steps) else None
        step["next_observation_index"] = after if after > step["observation_index"] else None
        step["delivery"] = results.get(step["acquisition_id"])
    spans = []
    active = None
    for item in observations:
        fighting = (item["combat"] or {}).get("availability") == "available"
        if fighting and active is None:
            active = {"start_observation_index": item["index"], "end_observation_index": None}
            spans.append(active)
        elif not fighting and active is not None:
            active["end_observation_index"] = item["index"]
            active = None
    value = {"schema": SCHEMA, "run_id": verified.manifest["run_id"],
        "identity": identity, "original_content_id": verified.content_id,
        "observations": observations, "steps": steps, "combat_spans": spans,
        "outcome": {"status": status, "reason": reason, "unknown": status == "unknown",
                    "terminal": outcome.get("terminal_observation"),
                    "max_observed_floor": max_floor,
                    "boss_clearance": boss_clearance(observations)},
        "rl_ready": False, "rl_unavailable_reason": "argmax_and_no_value_head",
        "score_eligible": trajectory_score_eligible(identity)}
    validate_trajectory(value)
    return value


def validate_trajectory(value: dict[str, Any]) -> None:
    if (value.get("schema") not in {LEGACY_SCHEMA, SCHEMA}
            or not isinstance(value.get("identity"), dict)
            or not set(value["identity"]) >= IDENTITY_FIELDS
            or not isinstance(value.get("observations"), list)
            or not isinstance(value.get("steps"), list)
            or len(json_bytes(value)) > MAX_EPISODE_BYTES):
        reject("invalid_trajectory")
    expected_eligible = (value.get("schema") == SCHEMA
                         and trajectory_score_eligible(value["identity"]))
    if value.get("score_eligible") is not expected_eligible:
        reject("invalid_score_intent")
    observations, steps = value["observations"], value["steps"]
    if (value["schema"] == SCHEMA
            and json_bytes(value.get("outcome", {}).get("boss_clearance"))
            != json_bytes(boss_clearance(observations))):
        reject("boss_clearance_witness_mismatch")
    for i, item in enumerate(observations):
        if (item.get("index") != i
                or item.get("input_spec") != value["identity"]["input_spec"]
                or not isinstance(item.get("observation", {}).get("catalog"), list)):
            reject("invalid_observation_sequence")
    outcome = value.get("outcome", {})
    if (outcome.get("status") not in {"finished", "censored", "unknown"}
            or outcome.get("unknown") is not (outcome.get("status") == "unknown")
            or outcome.get("status") == "unknown" and not outcome.get("reason")):
        reject("invalid_outcome")
    previous = -1
    for i, step in enumerate(steps):
        index = step.get("observation_index")
        if (step.get("index") != i or type(index) is not int
                or not previous <= index < len(observations)):
            reject("invalid_step_sequence")
        catalog = observations[index]["observation"]["catalog"]
        selected = step.get("action_index")
        if (type(selected) is not int or not 0 <= selected < len(catalog)
                or catalog[selected]["choice_id"] != step.get("action_id")):
            reject("invalid_action")
        validate_behavior(step["behavior"], len(catalog), selected)
        if any(step["behavior"].get(key) != value["identity"]["behavior_policy"].get(key)
               for key in ("mode", "temperature", "uniform_mix")):
            reject("episode_behavior_policy_changed")
        for scores in ([step.get("state_value")] if step.get("state_value") is not None else [],
                       step.get("action_values") or []):
            if not all(_finite(v) for v in scores):
                reject("invalid_value")
        if step.get("action_values") is not None and len(step["action_values"]) != len(catalog):
            reject("incomplete_action_values")
        next_step = i + 1 if i + 1 < len(steps) else None
        next_observation = step.get("next_observation_index")
        if (step.get("next_step_index") != next_step
                or next_observation is not None and (type(next_observation) is not int
                    or not index < next_observation < len(observations))
                or next_step is not None and next_observation != steps[i + 1]["observation_index"]):
            reject("broken_successor_link")
        if value.get("rl_ready") is True and (step["behavior"]["mode"] != "temperature_uniform"
                or step.get("state_value") is None):
            reject("missing_rl_outputs")
        previous = index
