"""Tiny synthetic port-4 checks; no loaded-game or model-quality claim."""

from __future__ import annotations

import copy
import hashlib
import io
from dataclasses import replace

import pytest
import torch
from test_public_inputs import snapshot as public_snapshot
from tokenizers import Tokenizer, models, pre_tokenizers

import stpd.policy.public_m2_port as port_module
from spireagent.json_boundary import BoundaryError, decode_json, json_bytes
from stpd.fullrun.public_inputs import COMPACT_IDENTITY, project_public_snapshot
from stpd.models.token_core import ScratchShape
from stpd.policy.public_m2_port import (
    PORT_SCHEMA,
    RUNTIME_PROFILE,
    PublicM2PolicyAdapter,
    serve,
)
from stpd.workers.public_m2_engine import (
    PUBLIC_M2_FEEDBACK_PROFILE,
    PUBLIC_M2_PRIOR_ACTION_PROFILE,
    PUBLIC_M2_SEQUENCE_PROFILE,
    PublicM2EngineConfig,
)


class CountingScorer:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[int, ...], tuple[tuple[int, ...], ...], bool]] = []
        self.fail = False

    def initial_memory(self) -> torch.Tensor:
        return torch.zeros(8, 384)

    def step(self, page, actions, memory, *, previous_actual_action, public_feedback,
             reset_before):
        assert previous_actual_action is None and public_feedback is None
        self.calls.append((tuple(page.tolist()), tuple(tuple(a.tolist()) for a in actions),
                           reset_before))
        if self.fail:
            raise RuntimeError("synthetic unknown write")
        return torch.arange(len(actions), dtype=torch.float), memory + 1


def setup():
    tokenizer = Tokenizer(models.WordLevel({f"token-{i}": i for i in range(258)},
                                          unk_token="token-0"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    config = PublicM2EngineConfig(
        source_digest="a" * 64, state_tokenizer_sha256="b" * 64,
        shape=ScratchShape(vocab_size=258, width=12, layers=1, heads=2,
                           feedforward=24, dropout=0.0, max_tokens=512),
        max_action_bytes=8192, max_actions_per_step=8, max_chain_steps=16,
        max_total_steps=32, max_total_input_tokens=65536,
    )
    manifest = {
        "adapter": {"id": "test-public-m2",
                    "protocol": "sts2.policy-runtime/decision-only-ndjson-4"},
        "representation": {"input_schema": "sts2.player-environment/snapshot-1"},
        "requirements": {"reads": []},
        "support": {"interaction_kinds": ["combat_turn"],
                    "action_verbs": ["play", "end_turn"]},
        "adapter_config": {
            "public_stateful_profile": RUNTIME_PROFILE,
            "stpd_public_m2": {
                "sequence_profile": PUBLIC_M2_SEQUENCE_PROFILE,
                "prior_action_profile": PUBLIC_M2_PRIOR_ACTION_PROFILE,
                "feedback_profile": PUBLIC_M2_FEEDBACK_PROFILE,
                "renderer": COMPACT_IDENTITY,
                "weights_sha256": "c" * 64,
                "state_tokenizer_sha256": config.state_tokenizer_sha256,
                "engine_input_digest": "d" * 64,
            },
        },
    }
    model = CountingScorer()
    adapter = PublicM2PolicyAdapter(model, tokenizer, config, manifest,
                                    weights_sha256="c" * 64, input_digest="d" * 64)
    return adapter, model


def request(adapter, *, ordinal=1, sequence=7, token="token-a", snapshot=None,
            previous=None):
    observation = public_snapshot() if snapshot is None else copy.deepcopy(snapshot)
    observation.update(snapshot_id=f"snapshot-{sequence}", sequence=sequence,
                       observed_at="2026-10-03T00:00:00Z")
    return {
        "decision": {
            "run_id": "run-1", "manifest": adapter.manifest,
            "bundle": {"observation": observation, "reads": []},
            "candidate_digest": project_public_snapshot(
                observation, compact=True).candidate_digest,
            "candidate_count": len(observation["bound_actions"]["actions"]),
        },
        "control": {
            "continuity_token": token, "episode_scope": "bounded_policy_segment",
            "episode_id": "episode-1", "segment_id": "segment-" + token,
            "observation_ordinal": ordinal, "previous_action": previous,
        },
    }


def decide(adapter, payload):
    return adapter.decide(payload["decision"], payload["control"])


def previous(request_id="receipt-a"):
    return {
        "decision_id": "decision", "source_snapshot_id": "snapshot-6",
        "candidate_digest": "a" * 64, "bound_action_id": "candidate-play",
        "request_id": request_id, "receipt": {"delivery": "delivered", "reason_code": None},
        "successor": {"snapshot_id": "snapshot-7", "sequence": 7},
    }


def test_same_observation_cache_ignores_poll_time_and_control_ack():
    adapter, model = setup()
    first = request(adapter)
    output, completion = decide(adapter, first)
    assert len(model.calls) == 1 and model.calls[0][2] is True
    assert output["selected_index"] == 1 and len(output["scores"]) == 2
    replay = copy.deepcopy(first)
    replay["decision"]["bundle"]["observation"]["observed_at"] = "2026-10-03T00:00:01Z"
    replay["control"]["previous_action"] = previous()
    cached, acknowledged = decide(adapter, replay)
    assert cached == output and len(model.calls) == 1
    assert completion["previous_action_request_id"] is None
    assert acknowledged["previous_action_request_id"] == "receipt-a"


def test_changed_same_ordinal_body_or_catalog_rejected_before_write():
    adapter, model = setup()
    first = request(adapter)
    decide(adapter, first)
    altered = copy.deepcopy(first)
    altered["decision"]["bundle"]["observation"]["interaction"]["content"]["new_public_value"] = 1
    with pytest.raises(BoundaryError, match="same_ordinal_observation_changed"):
        decide(adapter, altered)
    reordered = copy.deepcopy(first)
    reordered["decision"]["bundle"]["observation"]["bound_actions"]["actions"].reverse()
    reordered["decision"]["candidate_digest"] = project_public_snapshot(
        reordered["decision"]["bundle"]["observation"], compact=True).candidate_digest
    with pytest.raises(BoundaryError, match="same_ordinal_observation_changed"):
        decide(adapter, reordered)
    assert len(model.calls) == 1


def test_new_ordinal_writes_once_and_new_token_resets_without_resurrection():
    adapter, model = setup()
    first = request(adapter)
    decide(adapter, first)
    second = request(adapter, ordinal=2, sequence=8, previous=previous())
    output, completion = decide(adapter, second)
    assert len(model.calls) == 2 and model.calls[-1][2] is False
    assert completion["observation_ordinal"] == 2 and output["selected_index"] == 1
    with pytest.raises(BoundaryError, match="observation_ordinal_out_of_order"):
        decide(adapter, first)
    with pytest.raises(BoundaryError, match="observation_ordinal_out_of_order"):
        decide(adapter, request(adapter, ordinal=4, sequence=9))
    decide(adapter, request(adapter, token="token-b", sequence=9))
    assert len(model.calls) == 3 and model.calls[-1][2] is True
    with pytest.raises(BoundaryError, match="closed_or_unbegun_segment"):
        decide(adapter, second)


def test_bad_control_or_catalog_preflight_never_writes_and_step_failure_poisons():
    adapter, model = setup()
    bad = request(adapter, previous={
        **previous(), "receipt": {"delivery": "unknown", "reason_code": None},
    })
    with pytest.raises(BoundaryError, match="invalid_previous_action_control"):
        decide(adapter, bad)
    assert not model.calls
    bad = request(adapter)
    bad["decision"]["candidate_count"] = 1
    with pytest.raises(BoundaryError, match="complete_catalog_binding_mismatch"):
        decide(adapter, bad)
    assert not model.calls
    model.fail = True
    with pytest.raises(RuntimeError, match="synthetic unknown write"):
        decide(adapter, request(adapter))
    assert adapter.closed
    with pytest.raises(BoundaryError, match="adapter_closed_after_unknown"):
        decide(adapter, request(adapter, token="new-token"))


def test_manifest_profiles_are_stpd_owned_and_v4_wire_has_completion():
    adapter, model = setup()
    wrong = copy.deepcopy(adapter.manifest)
    wrong["adapter_config"]["stpd_public_m2"]["prior_action_profile"] = "action-history"
    with pytest.raises(BoundaryError, match="observation_only_manifest_required"):
        PublicM2PolicyAdapter(model, adapter.tokenizer, adapter.config, wrong,
                              weights_sha256="c" * 64, input_digest="d" * 64)
    wire = json_bytes({"schema": PORT_SCHEMA, "message_type": "decide",
                       "request_id": "request-1", **request(adapter)})
    source = io.StringIO(wire.decode() + "\n")
    destination = io.StringIO()
    assert serve(adapter, source, destination) == 0
    lines = [decode_json(row.encode()) for row in destination.getvalue().splitlines()]
    assert [row["message_type"] for row in lines] == ["ready", "decision"]
    assert lines[1]["completion"]["previous_action_request_id"] is None
    assert len(model.calls) == 1


def test_from_export_checks_pins_then_calls_strict_engine_loader(monkeypatch):
    prototype, model = setup()
    tokenizer_bytes = prototype.tokenizer.to_str().encode("utf-8")
    weights = b"synthetic typed export, loader is stubbed"
    config = replace(prototype.config,
                     state_tokenizer_sha256=hashlib.sha256(tokenizer_bytes).hexdigest())
    manifest = copy.deepcopy(prototype.manifest)
    profile = manifest["adapter_config"]["stpd_public_m2"]
    profile["state_tokenizer_sha256"] = config.state_tokenizer_sha256
    profile["weights_sha256"] = hashlib.sha256(weights).hexdigest()
    seen = []

    def load(raw, bound_config, *, input_digest, completed_epochs, inference_device):
        seen.append((raw, bound_config, input_digest, completed_epochs, inference_device))
        return model

    monkeypatch.setattr(port_module, "load_public_m2_weights", load)
    adapter = PublicM2PolicyAdapter.from_export(
        weights, config, input_digest="d" * 64, completed_epochs=3,
        tokenizer_bytes=tokenizer_bytes, manifest=manifest,
    )
    assert adapter.model is model and seen == [(weights, config, "d" * 64, 3, "cpu")]
    with pytest.raises(BoundaryError, match="tokenizer_identity_mismatch"):
        PublicM2PolicyAdapter.from_export(
            weights, config, input_digest="d" * 64, completed_epochs=3,
            tokenizer_bytes=b"wrong", manifest=manifest,
        )
