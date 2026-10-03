"""STPD observation-only public M2 scorer for the generic Snapshot policy port 4.

Platform owns segment issuance, exact Connector admission and native submission.
This adapter retains only latent memory and ordered scores; Runtime delivery facts
are protocol control, never previous-action or feedback model features.
"""

from __future__ import annotations

import copy
import hashlib
import math
from typing import Any, Protocol, TextIO, cast

import torch
from tokenizers import Tokenizer
from torch import Tensor

from spireagent.encoding import canonical_json, semantic_hash
from spireagent.json_boundary import BoundaryError, decode_json, json_bytes, object_fields

from ..fullrun.public_inputs import COMPACT_IDENTITY, project_public_snapshot
from ..fullrun.token_inputs import input_texts
from ..light_action_codec import encode_action
from ..workers.public_m2_engine import (
    PUBLIC_M2_FEEDBACK_PROFILE,
    PUBLIC_M2_PRIOR_ACTION_PROFILE,
    PUBLIC_M2_SEQUENCE_PROFILE,
    PublicM2EngineConfig,
    load_public_m2_weights,
)

PORT_SCHEMA = "sts2.policy-runtime/policy-port-4"
RUNTIME_PROFILE = "sts2.policy-runtime/public-observation-stateful-v1"
SNAPSHOT_SCHEMA = "sts2.player-environment/snapshot-1"
MAX_LINE_BYTES = 20 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


class _Scorer(Protocol):
    def initial_memory(self) -> Tensor: ...
    def step(self, page: Tensor, actions: tuple[Tensor, ...], memory: Tensor, *,
             previous_actual_action: Tensor | None, public_feedback: Tensor | None,
             reset_before: bool) -> tuple[Tensor, Tensor]: ...


def _manifest_profile(manifest: dict[str, Any], config: PublicM2EngineConfig, *,
                      weights_sha256: str, input_digest: str) -> None:
    try:
        adapter = manifest["adapter"]
        representation = manifest["representation"]
        requirements = manifest["requirements"]
        settings = manifest["adapter_config"]
        profile = settings["stpd_public_m2"]
        support = manifest["support"]
        valid = (
            adapter["protocol"] == "sts2.policy-runtime/decision-only-ndjson-4"
            and representation["input_schema"] == SNAPSHOT_SCHEMA
            and requirements["reads"] == []
            and settings["public_stateful_profile"] == RUNTIME_PROFILE
            and profile == {
                "sequence_profile": PUBLIC_M2_SEQUENCE_PROFILE,
                "prior_action_profile": PUBLIC_M2_PRIOR_ACTION_PROFILE,
                "feedback_profile": PUBLIC_M2_FEEDBACK_PROFILE,
                "renderer": COMPACT_IDENTITY,
                "weights_sha256": weights_sha256,
                "state_tokenizer_sha256": config.state_tokenizer_sha256,
                "engine_input_digest": input_digest,
            }
            and isinstance(support["interaction_kinds"], list)
            and isinstance(support["action_verbs"], list)
            and all(isinstance(x, str) and x for x in support["interaction_kinds"])
            and all(isinstance(x, str) and x for x in support["action_verbs"])
        )
    except (KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise BoundaryError("public_m2_policy", "observation_only_manifest_required")


def _sha256(value: object) -> bool:
    return (type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _previous_request_id(value: object) -> str | None:
    if value is None:
        return None
    action = object_fields(value, {
        "decision_id", "source_snapshot_id", "candidate_digest", "bound_action_id",
        "request_id", "receipt", "successor",
    }, "public_m2_policy.previous_action")
    receipt = object_fields(action["receipt"], {"delivery", "reason_code"},
                            "public_m2_policy.receipt")
    successor = object_fields(action["successor"], {"snapshot_id", "sequence"},
                              "public_m2_policy.successor")
    if (any(not isinstance(action[key], str) or not action[key]
            for key in ("decision_id", "source_snapshot_id", "bound_action_id", "request_id"))
            or not isinstance(action["candidate_digest"], str)
            or len(action["candidate_digest"]) != 64
            or any(c not in "0123456789abcdef" for c in action["candidate_digest"])
            or receipt["delivery"] != "delivered"
            or receipt["reason_code"] is not None
            and not isinstance(receipt["reason_code"], str)
            or not isinstance(successor["snapshot_id"], str)
            or not successor["snapshot_id"]
            or type(successor["sequence"]) is not int
            or successor["sequence"] < 0):
        raise BoundaryError("public_m2_policy", "invalid_previous_action_control")
    return cast(str, action["request_id"])


class PublicM2PolicyAdapter:
    """One live segment at a time; a new token explicitly resets latent memory."""

    def __init__(self, model: _Scorer, tokenizer: Tokenizer, config: PublicM2EngineConfig,
                 manifest: dict[str, Any], *, weights_sha256: str,
                 input_digest: str) -> None:
        config.validate(require_device_available=False)
        if (not isinstance(manifest, dict) or not isinstance(tokenizer, Tokenizer)
                or tokenizer.truncation is not None or tokenizer.padding is not None
                or tokenizer.get_vocab_size() != config.shape.vocab_size
                or not _sha256(weights_sha256) or not _sha256(input_digest)):
            raise BoundaryError("public_m2_policy", "invalid_model_codec_binding")
        _manifest_profile(manifest, config, weights_sha256=weights_sha256,
                          input_digest=input_digest)
        self.model = model
        self.tokenizer = tokenizer
        self.config = config
        self.manifest = copy.deepcopy(manifest)
        self.closed = False
        self._active: tuple[str, str, str, str, str] | None = None
        self._closed_tokens: set[str] = set()
        self._ordinal = 0
        self._watermark: tuple[str, int] | None = None
        self._observation_digest: str | None = None
        self._candidate_digest: str | None = None
        self._output: dict[str, Any] | None = None
        self._memory: Tensor | None = None

    @classmethod
    def from_export(cls, weights: bytes, config: PublicM2EngineConfig, *,
                    input_digest: str, completed_epochs: int, tokenizer_bytes: bytes,
                    manifest: dict[str, Any], inference_device: str = "cpu"
                    ) -> PublicM2PolicyAdapter:
        if (hashlib.sha256(tokenizer_bytes).hexdigest()
                != config.state_tokenizer_sha256):
            raise BoundaryError("public_m2_policy", "tokenizer_identity_mismatch")
        weights_sha256 = hashlib.sha256(weights).hexdigest()
        _manifest_profile(manifest, config, weights_sha256=weights_sha256,
                          input_digest=input_digest)
        try:
            tokenizer = Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise BoundaryError("public_m2_policy", "invalid_tokenizer") from error
        model = load_public_m2_weights(
            weights, config, input_digest=input_digest,
            completed_epochs=completed_epochs, inference_device=inference_device,
        )
        return cls(model, tokenizer, config, manifest,
                   weights_sha256=weights_sha256, input_digest=input_digest)

    def decide(self, decision: object, control: object) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.closed:
            raise BoundaryError("public_m2_policy", "adapter_closed_after_unknown")
        request = object_fields(decision, {
            "run_id", "manifest", "bundle", "candidate_digest", "candidate_count",
        }, "public_m2_policy.decision")
        metadata = object_fields(control, {
            "continuity_token", "episode_scope", "episode_id", "segment_id",
            "observation_ordinal", "previous_action",
        }, "public_m2_policy.control")
        if (request["manifest"] != self.manifest
                or not isinstance(request["run_id"], str) or not request["run_id"]
                or any(not isinstance(metadata[key], str) or not metadata[key]
                       for key in ("continuity_token", "episode_id", "segment_id"))
                or metadata["episode_scope"] not in {
                    "single_game_episode", "bounded_policy_segment"}
                or type(metadata["observation_ordinal"]) is not int
                or metadata["observation_ordinal"] < 1):
            raise BoundaryError("public_m2_policy", "request_control_invalid")
        previous_request_id = _previous_request_id(metadata["previous_action"])
        bundle = object_fields(request["bundle"], {"observation", "reads"},
                               "public_m2_policy.bundle")
        if bundle["reads"] != [] or not isinstance(bundle["observation"], dict):
            raise BoundaryError("public_m2_policy", "generic_snapshot_without_reads_required")
        snapshot = bundle["observation"]
        if (snapshot.get("schema") != SNAPSHOT_SCHEMA
                or len(json_bytes(snapshot)) > MAX_SNAPSHOT_BYTES
                or not isinstance(snapshot.get("snapshot_id"), str)
                or not snapshot["snapshot_id"]
                or type(snapshot.get("sequence")) is not int or snapshot["sequence"] < 0):
            raise BoundaryError("public_m2_policy", "snapshot_identity_invalid")
        public = project_public_snapshot(snapshot, compact=True)
        keys = tuple(action.key for action in public.actions)
        if (type(request["candidate_count"]) is not int
                or request["candidate_count"] != len(keys)
                or request["candidate_digest"] != public.candidate_digest
                or len(keys) > self.config.max_actions_per_step):
            raise BoundaryError("public_m2_policy", "complete_catalog_binding_mismatch")
        support = self.manifest["support"]
        if (snapshot["interaction"]["kind"] not in support["interaction_kinds"]
                or any(action["verb"] not in support["action_verbs"]
                       for action in snapshot["bound_actions"]["actions"])):
            raise BoundaryError("public_m2_policy", "unsupported_whole_decision")
        # observed_at is a poll timestamp, not a new model observation. Every
        # other Snapshot field, including the full ordered catalog, is bound.
        observation_digest = semantic_hash({key: item for key, item in snapshot.items()
                                            if key != "observed_at"})
        watermark = (snapshot["snapshot_id"], snapshot["sequence"])
        namespace = (request["run_id"], metadata["continuity_token"],
                     metadata["episode_scope"], metadata["episode_id"],
                     metadata["segment_id"])
        ordinal = metadata["observation_ordinal"]
        if namespace == self._active:
            if ordinal == self._ordinal:
                if (watermark != self._watermark
                        or observation_digest != self._observation_digest
                        or public.candidate_digest != self._candidate_digest
                        or self._output is None):
                    raise BoundaryError("public_m2_policy", "same_ordinal_observation_changed")
                return copy.deepcopy(self._output), self._completion(
                    namespace, ordinal, watermark, previous_request_id)
            if (ordinal != self._ordinal + 1 or self._watermark is None
                    or watermark[1] <= self._watermark[1]):
                raise BoundaryError("public_m2_policy", "observation_ordinal_out_of_order")
            memory = self._memory
        else:
            if (ordinal != 1 or namespace[1] in self._closed_tokens
                    or self._active is not None and (
                        namespace[0] != self._active[0]
                        or namespace[1] == self._active[1]
                        or namespace[4] == self._active[4])):
                raise BoundaryError("public_m2_policy", "closed_or_unbegun_segment")
            memory = self.model.initial_memory()
        if memory is None:
            raise BoundaryError("public_m2_policy", "missing_memory")
        if (not isinstance(memory, Tensor) or not bool(torch.isfinite(memory).all())):
            raise BoundaryError("public_m2_policy", "invalid_memory")

        # Complete preflight before one possible model write; no truncation and
        # no candidate filtering. Control fields are never encoded as tokens.
        text, _ = input_texts(public.state_text, ())
        page_ids = tuple(self.tokenizer.encode(text, add_special_tokens=False).ids)
        byte_actions = tuple(encode_action(text, max_bytes=self.config.max_action_bytes)
                             for text in public.action_texts)
        if (not page_ids or len(page_ids) > self.config.shape.max_tokens
                or len(page_ids) + sum(map(len, byte_actions)) > self.config.max_window_tokens
                or any(type(token) is not int or token < 0
                       or token >= self.config.shape.vocab_size for token in page_ids)):
            raise BoundaryError("public_m2_policy", "page_limit_no_truncation")
        device = memory.device
        page = torch.tensor(page_ids, dtype=torch.long, device=device)
        actions = tuple(torch.tensor(row, dtype=torch.long, device=device)
                        for row in byte_actions)
        try:
            with torch.no_grad():
                scores, updated = self.model.step(
                    page, actions, memory.detach().clone(),
                    previous_actual_action=None, public_feedback=None,
                    reset_before=(ordinal == 1 or self.config.reset_each_step),
                )
            if (scores.shape != (len(keys),) or scores.device != memory.device
                    or not bool(torch.isfinite(scores).all())
                    or updated.shape != memory.shape or updated.device != memory.device
                    or updated.dtype != memory.dtype or not bool(torch.isfinite(updated).all())):
                raise BoundaryError("public_m2_policy", "score_or_memory_invalid")
            values = [float(value) for value in scores.detach().cpu().tolist()]
            if any(not math.isfinite(value) for value in values):
                raise BoundaryError("public_m2_policy", "score_or_memory_invalid")
        except Exception:
            # A failed step may have performed an unknown partial model write.
            # Only a fresh adapter instance may continue; never auto-retry.
            self.closed = True
            raise
        output = {"candidate_digest": public.candidate_digest, "scores": values,
                  "selected_index": max(range(len(values)), key=values.__getitem__)}
        if self._active is not None and namespace != self._active:
            self._closed_tokens.add(self._active[1])
        self._active = namespace
        self._ordinal = ordinal
        self._watermark = watermark
        self._observation_digest = observation_digest
        self._candidate_digest = public.candidate_digest
        self._output = output
        self._memory = updated.detach().clone()
        return copy.deepcopy(output), self._completion(namespace, ordinal, watermark,
                                              previous_request_id)

    @staticmethod
    def _completion(namespace: tuple[str, str, str, str, str], ordinal: int,
                    watermark: tuple[str, int], previous_request_id: str | None
                    ) -> dict[str, Any]:
        return {"continuity_token": namespace[1], "episode_id": namespace[3],
                "segment_id": namespace[4], "observation_ordinal": ordinal,
                "snapshot_id": watermark[0], "sequence": watermark[1],
                "previous_action_request_id": previous_request_id}

    def close(self) -> None:
        self.closed = True


def serve(adapter: PublicM2PolicyAdapter, source: TextIO, destination: TextIO) -> int:
    """Minimal independent V4 NDJSON entry point; no text-menu compatibility path."""
    def emit(message: dict[str, Any]) -> None:
        destination.write(canonical_json(message) + "\n")
        destination.flush()

    emit({"schema": PORT_SCHEMA, "message_type": "ready",
          "adapter": adapter.manifest["adapter"]})
    try:
        while line := source.readline(MAX_LINE_BYTES + 1):
            if not line.strip():
                continue
            request_id = "unknown"
            try:
                if len(line.encode("utf-8")) > MAX_LINE_BYTES or not line.endswith("\n"):
                    raise BoundaryError("public_m2_policy", "request_line_limit")
                request = object_fields(decode_json(line.encode("utf-8")), {
                    "schema", "message_type", "request_id", "decision", "control",
                }, "public_m2_policy.port")
                if (request["schema"] != PORT_SCHEMA or request["message_type"] != "decide"
                        or not isinstance(request["request_id"], str)
                        or not request["request_id"]):
                    raise BoundaryError("public_m2_policy", "invalid_port_request")
                request_id = request["request_id"]
                output, completion = adapter.decide(request["decision"], request["control"])
                emit({"schema": PORT_SCHEMA, "message_type": "decision",
                      "request_id": request_id, "output": output,
                      "completion": completion})
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                emit({"schema": PORT_SCHEMA, "message_type": "error",
                      "request_id": request_id,
                      "error": {"code": "policy_error", "message": str(error)}})
                if len(line) > MAX_LINE_BYTES and not line.endswith("\n"):
                    break
    finally:
        adapter.close()
    return 0
