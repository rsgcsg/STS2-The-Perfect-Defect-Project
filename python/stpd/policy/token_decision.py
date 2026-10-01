"""Standalone Stage 1a model exports; no training store, labels or game authority."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import torch
from tokenizers import Tokenizer

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields
from spireagent.storage.store import ArtifactStore

from ..fullrun.contracts import SemanticAction, SemanticState
from ..fullrun.decision_training import VIEW_SCHEMA as CANONICAL_LIGHT_ACTION_M0_VIEW_SCHEMA
from ..fullrun.light_action_inputs import (
    CANONICAL_INPUT_FORMAT as CANONICAL_LIGHT_ACTION_M0_INPUT_FORMAT,
)
from ..fullrun.light_action_inputs import (
    CANONICAL_SCHEMA as CANONICAL_LIGHT_ACTION_M0_INPUT_SCHEMA,
)
from ..fullrun.light_action_inputs import (
    INPUT_FORMAT as LIGHT_ACTION_M0_INPUT_FORMAT,
)
from ..fullrun.light_action_inputs import (
    SCHEMA as LIGHT_ACTION_M0_INPUT_SCHEMA,
)
from ..fullrun.light_action_inputs import (
    TEXT_MENU_VIEW_SCHEMA as LIGHT_ACTION_M0_VIEW_SCHEMA,
)
from ..fullrun.public_inputs import COMPACT_IDENTITY, project_public_snapshot
from ..fullrun.public_inputs import IDENTITY as PUBLIC_IDENTITY
from ..fullrun.representation import FullRunSerializer
from ..fullrun.text_menu_inputs import IDENTITY as TEXT_MENU_IDENTITY
from ..fullrun.text_menu_inputs import SNAPSHOT_SCHEMA as TEXT_MENU_SCHEMA
from ..fullrun.text_menu_inputs import project_text_menu_snapshot
from ..fullrun.token_inputs import FORMAT, encode_texts
from ..light_action_codec import SPEC as LIGHT_ACTION_CODEC_SPEC
from ..light_action_codec import SPEC_BYTES as LIGHT_ACTION_CODEC_BYTES
from ..light_action_codec import SPEC_SHA256 as LIGHT_ACTION_CODEC_SHA256
from ..light_action_codec import encode_action
from ..models.stage1a import recipe_for
from ..qwen.l1 import load_pin
from ..workers.token_ranking import (
    LightActionM0Config,
    TokenConfig,
    config_payload,
    construct_model,
    restore_weights,
)
from ..workers.token_worker import MODEL_SCHEMA

SCHEMA = "stpd/stage1a-export-v1"
FILES = {"weights": "weights.safetensors", "tokenizer": "tokenizer.json"}
LIMITS = {"weights": 128 * 1024**2, "tokenizer": 16 * 1024**2}
LIGHT_ACTION_M0_MODEL_SCHEMA = "stpd/stage1a-light-action-m0-model-v1"
LIGHT_ACTION_M0_EXPORT_SCHEMA = "stpd/stage1a-light-action-m0-export-v1"
CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA = "stpd/stage1a-light-action-m0-canonical-model-v1"
CANONICAL_LIGHT_ACTION_M0_EXPORT_SCHEMA = "stpd/stage1a-light-action-m0-canonical-export-v1"
LIGHT_ACTION_M0_FILES = {
    "weights": "weights.safetensors",
    "state_tokenizer": "state-tokenizer.json",
    "action_codec": "action-codec.json",
}


def check_model(model: Manifest) -> tuple[TokenConfig, FullRunSerializer | None]:
    info = model.parameters.value()
    if (model.kind != "model" or info.get("schema") != MODEL_SCHEMA
            or info.get("qualification") != "engineering_only" or info.get("dtype") != "float32"
            or info.get("input_format") != FORMAT
            or sorted(p.role for p in model.payloads) != ["tokenizer", "weights"]):
        raise BoundaryError("token_policy", "unsupported_model")
    config = TokenConfig.decode(info.get("config"))
    recipe = recipe_for(config.recipe)
    if (info.get("graph") != recipe.graph or info.get("steps") != config.steps
            or type(info.get("vocab_size")) is not int or info["vocab_size"] < 256):
        raise BoundaryError("token_policy", "model_config_mismatch")
    for role, limit in LIMITS.items():
        if model.payload(role).size > limit:
            raise BoundaryError("token_policy", "payload_size_limit")
    if recipe.backbone == "pf":
        if model.payload("tokenizer").sha256 != load_pin().file_by_name["tokenizer.json"].sha256:
            raise BoundaryError("token_policy", "qwen_tokenizer_mismatch")
    elif info["vocab_size"] > 8192:
        raise BoundaryError("token_policy", "scratch_vocab_limit")
    if info.get("serializer") in (PUBLIC_IDENTITY, COMPACT_IDENTITY, TEXT_MENU_IDENTITY):
        return config, None
    serializer = FullRunSerializer(info.get("serializer", {}).get("profile", ""))
    if info["serializer"] != serializer.identity:
        raise BoundaryError("token_policy", "serializer_mismatch")
    return config, serializer


def export_token_model(store: ArtifactStore, identity: str, destination: Path) -> dict:
    model = store.get_manifest(identity)
    check_model(model)
    raw = {role: b"".join(store.read_payload(model.payload(role))) for role in FILES}
    envelope = {"schema": SCHEMA, "model_id": identity, "model": json.loads(model.to_bytes())}
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise BoundaryError("token_policy", "export_destination_exists")
    with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
        stage = Path(folder) / "model"
        stage.mkdir(mode=0o700)
        (stage / "model.json").write_bytes(json_bytes(envelope))
        for role, name in FILES.items():
            (stage / name).write_bytes(raw[role])
        os.rename(stage, destination)
    return {"model_id": identity, "schema": SCHEMA,
            "payload_bytes": sum(len(value) for value in raw.values())}


def check_light_action_m0_model(
    model: Manifest,
) -> tuple[LightActionM0Config, dict[str, Any]]:
    """Validate the additive stateless M0 artifact without changing legacy model identity."""
    info = model.parameters.value()
    model_schema = info.get("schema")
    canonical = model_schema == CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA
    expected_model_schema = (CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA if canonical
                             else LIGHT_ACTION_M0_MODEL_SCHEMA)
    expected_input_schema = (CANONICAL_LIGHT_ACTION_M0_INPUT_SCHEMA if canonical
                             else LIGHT_ACTION_M0_INPUT_SCHEMA)
    expected_input_format = (CANONICAL_LIGHT_ACTION_M0_INPUT_FORMAT if canonical
                             else LIGHT_ACTION_M0_INPUT_FORMAT)
    if (model.kind != "model" or model_schema != expected_model_schema
            or info.get("qualification") != "engineering_only" or info.get("dtype") != "float32"
            or info.get("input_schema") != expected_input_schema
            or info.get("input_format") != expected_input_format
            or sorted(payload.role for payload in model.payloads)
            != ["action_codec", "state_tokenizer", "weights"]):
        raise BoundaryError("token_policy", "unsupported_light_action_model")
    config = LightActionM0Config.decode(info.get("config"))
    recipe = recipe_for(config.recipe)
    expected_view_schema = (CANONICAL_LIGHT_ACTION_M0_VIEW_SCHEMA if canonical
                            else LIGHT_ACTION_M0_VIEW_SCHEMA)
    if (info.get("recipe") != config.recipe or info.get("graph") != recipe.graph
            or info.get("steps") != config.steps
            or info.get("source_view_schema") != expected_view_schema):
        raise BoundaryError("token_policy", "light_action_model_config_mismatch")
    if info.get("config") != config_payload(config):
        raise BoundaryError("token_policy", "light_action_model_config_mismatch")
    if canonical:
        binding = info.get("training_binding")
        if (not isinstance(binding, dict) or binding.get("schema")
                != "stpd/light-action-m0-training-binding-v1"
                or not isinstance(binding.get("dataset_ids"), list)
                or not binding["dataset_ids"]
                or any(not isinstance(identity, str) for identity in binding["dataset_ids"])
                or binding["dataset_ids"] != sorted(set(binding["dataset_ids"]))
                or not isinstance(binding.get("training_operation_id"), str)
                or len(binding["training_operation_id"]) != 32
                or any(character not in "0123456789abcdef"
                       for character in binding["training_operation_id"])
                or set(binding) != {"schema", "dataset_ids", "training_operation_id",
                                    "allocation_id", "model_view_id"}):
            raise BoundaryError("token_policy", "light_action_training_binding_mismatch")
        digest(binding["allocation_id"], "light_action_m0.allocation_id")
        digest(binding["model_view_id"], "light_action_m0.model_view_id")
        if (info.get("input_schema") != CANONICAL_LIGHT_ACTION_M0_INPUT_SCHEMA
                or info.get("input_format") != CANONICAL_LIGHT_ACTION_M0_INPUT_FORMAT
                or info.get("source_view_schema") != CANONICAL_LIGHT_ACTION_M0_VIEW_SCHEMA
                or sorted(parent.role for parent in model.parents)
                != ["checkpoint", "model_view", "run", "training_input"]
                or model.parent("model_view") != binding["model_view_id"]):
            raise BoundaryError("token_policy", "light_action_training_binding_mismatch")
    elif (info.get("input_schema") != LIGHT_ACTION_M0_INPUT_SCHEMA
          or info.get("input_format") != LIGHT_ACTION_M0_INPUT_FORMAT):
        raise BoundaryError("token_policy", "light_action_model_config_mismatch")
    state_codec = info.get("state_codec")
    action_codec = info.get("action_codec")
    renderer = info.get("source_renderer")
    expected_renderer = (info.get("source_renderer") if canonical else TEXT_MENU_IDENTITY)
    if canonical:
        from ..fullrun.representation import FullRunSerializer

        if not isinstance(renderer, dict):
            raise BoundaryError("token_policy", "light_action_renderer_mismatch")
        serializer = FullRunSerializer(renderer.get("profile", ""))
        if serializer.identity != renderer:
            raise BoundaryError("token_policy", "light_action_renderer_mismatch")
        expected_renderer = serializer.identity
    if renderer != expected_renderer:
        raise BoundaryError("token_policy", "light_action_renderer_mismatch")
    if (not isinstance(state_codec, dict) or not isinstance(action_codec, dict)
            or not isinstance(renderer, dict)
            or state_codec.get("sha256") != model.payload("state_tokenizer").sha256
            or info.get("state_tokenizer_sha256") != model.payload("state_tokenizer").sha256
            or action_codec.get("sha256") != LIGHT_ACTION_CODEC_SHA256
            or info.get("action_codec_sha256") != LIGHT_ACTION_CODEC_SHA256
            or action_codec.get("schema") != LIGHT_ACTION_CODEC_SPEC.get("schema")
            or action_codec.get("source_renderer") != expected_renderer):
        raise BoundaryError("token_policy", "light_action_codec_identity_mismatch")
    expected_state_family = "train-only-byte-bpe" if recipe.backbone == "s" else "pinned-qwen3"
    expected_state_schema = (
        "stpd/state-byte-bpe-v1" if recipe.backbone == "s" else "qwen3-tokenizer-json-v1"
    )
    expected_action_codec = {
        **LIGHT_ACTION_CODEC_SPEC,
        "sha256": LIGHT_ACTION_CODEC_SHA256,
        "source_renderer": expected_renderer,
    }
    if (state_codec.get("family") != expected_state_family
            or state_codec.get("schema") != expected_state_schema
            or state_codec.get("max_tokens") != config.max_state_tokens
            or state_codec.get("add_special_tokens") is not False):
        raise BoundaryError("token_policy", "light_action_state_codec_mismatch")
    if recipe.backbone in {"pf", "pl"}:
        pin = load_pin()
        tokenizer_pin = pin.file_by_name["tokenizer.json"]
        roles = {role: token.token_id for token in pin.special_tokens
                 for role in token.roles}
        pinned_special_ids = {
            "bos": roles.get("bos_token"),
            "eos": roles.get("eos_token"),
            "pad": roles.get("pad_token"),
            "additional": [
                token.token_id for token in pin.special_tokens
                if "additional_special_tokens" in token.roles
            ],
        }
        if (state_codec.get("model_id") != pin.model_id
                or state_codec.get("revision") != pin.repo_revision
                or state_codec.get("sha256") != tokenizer_pin.sha256
                or model.payload("state_tokenizer").size != tokenizer_pin.size_bytes
                or state_codec.get("tokenizer_bundle_sha256") != pin.tokenizer_bundle_sha256
                or state_codec.get("special_token_ids") != pinned_special_ids
                or config.max_state_tokens > pin.hard_limit):
            raise BoundaryError("token_policy", "light_action_state_codec_pin_mismatch")
    if action_codec != expected_action_codec:
        raise BoundaryError("token_policy", "light_action_codec_identity_mismatch")
    if (model.payload("weights").size > LIMITS["weights"]
            or model.payload("state_tokenizer").size > LIMITS["tokenizer"]
            or model.payload("action_codec").size != len(LIGHT_ACTION_CODEC_BYTES)):
        raise BoundaryError("token_policy", "payload_size_limit")
    if info.get("weights_sha256") != model.payload("weights").sha256:
        raise BoundaryError("token_policy", "light_action_weights_identity_mismatch")
    backbone = info.get("backbone")
    expected_kind = {"s": "scratch", "pf": "pf", "pl": "pl"}[recipe.backbone]
    if not isinstance(backbone, dict) or backbone.get("kind") != expected_kind:
        raise BoundaryError("token_policy", "light_action_backbone_mismatch")
    names = info.get("adapter_tensor_names")
    if (not isinstance(names, list) or names != sorted(set(names))
            or any(not isinstance(name, str) or not name for name in names)
            or (recipe.backbone == "pl") != bool(names)):
        raise BoundaryError("token_policy", "light_action_adapter_inventory_mismatch")
    if recipe.backbone == "pl" and info.get("adapter_config") != backbone.get("adapter_config"):
        raise BoundaryError("token_policy", "light_action_adapter_config_mismatch")
    if recipe.backbone != "pl" and (names or info.get("adapter_config") is not None):
        raise BoundaryError("token_policy", "light_action_adapter_inventory_mismatch")
    return config, info


def export_light_action_m0_model(
    store: ArtifactStore, identity: str, destination: Path,
) -> dict[str, Any]:
    """Export the exact three-payload M0 artifact into a standalone directory."""
    model = store.get_manifest(identity)
    _, info = check_light_action_m0_model(model)
    raw: dict[str, bytes] = {}
    for role in LIGHT_ACTION_M0_FILES:
        payload = model.payload(role)
        if role == "weights" and payload.size > LIMITS["weights"]:
            raise BoundaryError("token_policy", "payload_size_limit")
        if role == "state_tokenizer" and payload.size > LIMITS["tokenizer"]:
            raise BoundaryError("token_policy", "payload_size_limit")
        raw[role] = b"".join(store.read_payload(payload))
        if (len(raw[role]) != payload.size
                or hashlib.sha256(raw[role]).hexdigest() != payload.sha256):
            raise BoundaryError("token_policy", "payload_digest_mismatch")
    if raw["action_codec"] != LIGHT_ACTION_CODEC_BYTES:
        raise BoundaryError("token_policy", "light_action_codec_identity_mismatch")
    export_schema = (CANONICAL_LIGHT_ACTION_M0_EXPORT_SCHEMA
                     if info["schema"] == CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA
                     else LIGHT_ACTION_M0_EXPORT_SCHEMA)
    envelope = {"schema": export_schema, "model_id": identity,
                "model": json.loads(model.to_bytes())}
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise BoundaryError("token_policy", "export_destination_exists")
    with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
        stage = Path(folder) / "model"
        stage.mkdir(mode=0o700)
        (stage / "model.json").write_bytes(json_bytes(envelope))
        for role, filename in LIGHT_ACTION_M0_FILES.items():
            (stage / filename).write_bytes(raw[role])
        os.rename(stage, destination)
    return {"model_id": identity, "schema": export_schema,
            "payload_bytes": sum(len(value) for value in raw.values())}


class LightActionM0DecisionScorer:
    """Standalone M0 scorer for exact text-menu inputs and the complete ordered catalog."""

    def __init__(self, directory: Path, *, snapshot: Path | None = None) -> None:
        from ..workers.token_ranking import (
            LoRAQwenTokenCore,
            construct_model,
            restore_weights,
        )

        envelope_path = directory / "model.json"
        if envelope_path.stat().st_size > 1024**2:
            raise BoundaryError("token_policy", "manifest_size_limit")
        value = object_fields(
            json.loads(envelope_path.read_bytes()), {"schema", "model_id", "model"},
            "light_action_m0_export",
        )
        if value["schema"] not in {LIGHT_ACTION_M0_EXPORT_SCHEMA,
                                    CANONICAL_LIGHT_ACTION_M0_EXPORT_SCHEMA}:
            raise BoundaryError("token_policy", "unsupported_light_action_export")
        if not isinstance(value["model_id"], str):
            raise BoundaryError("token_policy", "light_action_export_identity_mismatch")
        self.artifact = Manifest.from_bytes(json_bytes(value["model"]), value["model_id"])
        self.config, self.info = check_light_action_m0_model(self.artifact)
        canonical = self.info["schema"] == CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA
        expected_export_schema = (CANONICAL_LIGHT_ACTION_M0_EXPORT_SCHEMA if canonical
                                  else LIGHT_ACTION_M0_EXPORT_SCHEMA)
        if value["schema"] != expected_export_schema:
            raise BoundaryError("token_policy", "light_action_export_family_mismatch")
        self.canonical_input = canonical
        raw: dict[str, bytes] = {}
        for role, filename in LIGHT_ACTION_M0_FILES.items():
            path = directory / filename
            expected = self.artifact.payload(role)
            if path.stat().st_size != expected.size:
                raise BoundaryError("token_policy", "payload_size_mismatch")
            raw[role] = path.read_bytes()
            if hashlib.sha256(raw[role]).hexdigest() != expected.sha256:
                raise BoundaryError("token_policy", "payload_digest_mismatch")
        if (raw["action_codec"] != LIGHT_ACTION_CODEC_BYTES
                or hashlib.sha256(raw["action_codec"]).hexdigest() != LIGHT_ACTION_CODEC_SHA256):
            raise BoundaryError("token_policy", "light_action_codec_identity_mismatch")
        try:
            self.state_tokenizer = Tokenizer.from_str(raw["state_tokenizer"].decode("utf-8"))
        except Exception as error:
            raise BoundaryError("token_policy", "invalid_light_action_state_tokenizer") from error
        state_codec = self.info["state_codec"]
        if (self.state_tokenizer.truncation is not None or self.state_tokenizer.padding is not None
                or self.state_tokenizer.get_vocab_size() != state_codec["vocab_size"]):
            raise BoundaryError("token_policy", "light_action_state_tokenizer_mismatch")
        recipe = recipe_for(self.config.recipe)
        vocab_size = self.state_tokenizer.get_vocab_size()
        self.model, backbone = construct_model(
            self.config, vocab_size, snapshot, state_codec=state_codec,
        )
        if backbone != self.info["backbone"]:
            raise BoundaryError("token_policy", "light_action_backbone_identity_mismatch")
        if recipe.backbone == "pl":
            assert isinstance(self.model.core, LoRAQwenTokenCore)
            expected_names = sorted(
                f"core.model.{name}" for name in self.model.core.adapter_tensor_names
            )
            if (self.info["adapter_tensor_names"] != expected_names
                    or self.info["adapter_config"] != self.model.core.adapter_config):
                raise BoundaryError("token_policy", "light_action_adapter_identity_mismatch")
            adapter_names = set(expected_names)
        else:
            adapter_names = None
        restore_weights(
            self.model, raw["weights"], frozen=recipe.backbone == "pf",
            adapter_tensor_names=adapter_names, strict_frozen_core=True,
        )
        self.model.eval()

    def score_texts(self, state: str, actions: tuple[str, ...]) -> tuple[float, ...]:
        from ..fullrun.token_inputs import input_texts

        if (not isinstance(state, str) or not isinstance(actions, tuple) or not actions
                or any(not isinstance(a, str) for a in actions)):
            raise BoundaryError("token_policy", "invalid_light_action_request")
        state_text, _ = input_texts(state, ())
        state_ids = tuple(self.state_tokenizer.encode(state_text, add_special_tokens=False).ids)
        if not state_ids:
            raise BoundaryError("token_policy", "empty_light_action_state")
        if len(state_ids) > self.config.max_state_tokens:
            raise BoundaryError("token_policy", "state_token_limit_exceeded_no_truncation")
        action_ids = tuple(
            encode_action(action, max_bytes=self.config.max_action_bytes) for action in actions
        )
        device = self.config.device
        with torch.no_grad():
            scores = self.model(
                torch.tensor(state_ids, dtype=torch.long, device=device),
                tuple(
                    torch.tensor(action, dtype=torch.long, device=device)
                    for action in action_ids
                ),
            )
        if scores.shape != (len(actions),) or not bool(torch.isfinite(scores).all()):
            raise BoundaryError("token_policy", "invalid_light_action_scores")
        return tuple(float(score) for score in scores.detach().cpu().tolist())

    def score_snapshot(self, snapshot: dict[str, Any]) -> dict[str, float]:
        if self.canonical_input:
            raise BoundaryError("token_policy", "canonical_semantic_input_required")
        if snapshot.get("schema") != TEXT_MENU_SCHEMA:
            raise BoundaryError("token_policy", "text_menu_snapshot_required")
        current = project_text_menu_snapshot(snapshot)
        if (not current.action_ids or len(set(current.action_ids)) != len(current.action_ids)
                or len(current.action_ids) != len(current.action_texts)):
            raise BoundaryError("token_policy", "complete_unique_candidate_catalog_required")
        scores = self.score_texts(current.state_text, current.action_texts)
        return dict(zip(current.action_ids, scores, strict=True))

    def score_semantic(
        self, state: SemanticState, actions: tuple[SemanticAction, ...], *, profile: str,
    ) -> dict[str, float]:
        if not self.canonical_input:
            raise BoundaryError("token_policy", "text_menu_snapshot_model_required")
        from ..fullrun.representation import FullRunSerializer

        expected = self.info["source_renderer"].get("profile")
        if profile != expected:
            raise BoundaryError("token_policy", "canonical_renderer_profile_mismatch")
        serializer = FullRunSerializer(profile)
        if (serializer.identity != self.info["source_renderer"] or not actions
                or len({action.key for action in actions}) != len(actions)):
            raise BoundaryError("token_policy", "complete_semantic_catalog_required")
        scores = self.score_texts(serializer.serialize_state(state), tuple(
            serializer.serialize_action(action) for action in actions
        ))
        return {action.key: score for action, score in zip(actions, scores, strict=True)}


class TokenDecisionScorer:
    def __init__(self, directory: Path, *, snapshot: Path | None = None) -> None:
        envelope = directory / "model.json"
        if envelope.stat().st_size > 1024**2:
            raise BoundaryError("token_policy", "manifest_size_limit")
        value = object_fields(json.loads(envelope.read_bytes()), {"schema", "model_id", "model"},
                              "token_export")
        if value["schema"] != SCHEMA:
            raise BoundaryError("token_policy", "unsupported_export")
        self.artifact = Manifest.from_bytes(json_bytes(value["model"]), value["model_id"])
        self.config, self.serializer = check_model(self.artifact)
        raw = {}
        for role, filename in FILES.items():
            file = directory / filename
            expected = self.artifact.payload(role)
            if file.stat().st_size != expected.size:
                raise BoundaryError("token_policy", "payload_size_mismatch")
            raw[role] = file.read_bytes()
            if hashlib.sha256(raw[role]).hexdigest() != expected.sha256:
                raise BoundaryError("token_policy", "payload_digest_mismatch")
        self.tokenizer = Tokenizer.from_str(raw["tokenizer"].decode())
        info = self.artifact.parameters.value()
        if (self.tokenizer.get_vocab_size() != info["vocab_size"]
                or self.tokenizer.truncation is not None or self.tokenizer.padding is not None):
            raise BoundaryError("token_policy", "tokenizer_config_mismatch")
        self.model, backbone = construct_model(self.config, info["vocab_size"], snapshot)
        if backbone != info["backbone"]:
            raise BoundaryError("token_policy", "backbone_runtime_mismatch")
        restore_weights(self.model, raw["weights"],
                        frozen=recipe_for(self.config.recipe).backbone == "pf")
        self.model.eval()

    def score_texts(self, state: str, actions: tuple[str, ...]) -> tuple[float, ...]:
        row = encode_texts(self.tokenizer, state, actions, max_tokens=self.config.max_tokens)
        with torch.no_grad():
            values = self.model(
                torch.tensor(row.state, dtype=torch.long, device=self.config.device),
                tuple(torch.tensor(a, dtype=torch.long, device=self.config.device)
                      for a in row.actions),
            )
        if values.shape != (len(actions),) or not bool(torch.isfinite(values).all()):
            raise BoundaryError("token_policy", "invalid_scores")
        return tuple(float(v) for v in values.cpu().tolist())

    def score(self, state: SemanticState, actions: tuple[SemanticAction, ...]) -> dict[str, float]:
        if self.serializer is None:
            raise BoundaryError("token_policy", "public_snapshot_model_requires_snapshot")
        if not actions or len({a.key for a in actions}) != len(actions):
            raise BoundaryError("token_policy", "unique_nonempty_candidates_required")
        scores = self.score_texts(self.serializer.serialize_state(state),
                                  tuple(self.serializer.serialize_action(a) for a in actions))
        return {a.key: value for a, value in zip(actions, scores, strict=True)}

    def score_snapshot(self, snapshot: dict) -> dict[str, float]:
        if self.serializer is not None:
            raise BoundaryError("token_policy", "semantic_model_cannot_score_public_snapshot")
        if self.artifact.parameters.value()["serializer"] == TEXT_MENU_IDENTITY:
            if snapshot.get("schema") != TEXT_MENU_SCHEMA:
                raise BoundaryError("token_policy", "text_menu_snapshot_required")
            current = project_text_menu_snapshot(snapshot)
            scores = current.scores_in_catalog_order(
                self.score_texts(current.state_text, current.action_texts))
            return dict(zip(current.action_ids, scores, strict=True))
        if snapshot.get("schema") == TEXT_MENU_SCHEMA:
            raise BoundaryError("token_policy", "text_menu_model_required")
        public = project_public_snapshot(
            snapshot, compact=self.artifact.parameters.value()["serializer"] == COMPACT_IDENTITY,
        )
        scores = self.score_texts(public.state_text, public.action_texts)
        return {a.key: value for a, value in zip(public.actions, scores, strict=True)}
