"""Standalone Stage 1a model exports; no training store, labels or game authority."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import torch
from tokenizers import Tokenizer

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, json_bytes, object_fields
from spireagent.storage.store import ArtifactStore

from ..fullrun.contracts import SemanticAction, SemanticState
from ..fullrun.public_inputs import COMPACT_IDENTITY, project_public_snapshot
from ..fullrun.public_inputs import IDENTITY as PUBLIC_IDENTITY
from ..fullrun.representation import FullRunSerializer
from ..fullrun.text_menu_inputs import IDENTITY as TEXT_MENU_IDENTITY
from ..fullrun.text_menu_inputs import SNAPSHOT_SCHEMA as TEXT_MENU_SCHEMA
from ..fullrun.text_menu_inputs import project_text_menu_snapshot
from ..fullrun.token_inputs import FORMAT, encode_texts
from ..models.stage1a import recipe_for
from ..qwen.l1 import load_pin
from ..workers.token_ranking import TokenConfig, construct_model, restore_weights
from ..workers.token_worker import MODEL_SCHEMA

SCHEMA = "stpd/stage1a-export-v1"
FILES = {"weights": "weights.safetensors", "tokenizer": "tokenizer.json"}
LIMITS = {"weights": 128 * 1024**2, "tokenizer": 16 * 1024**2}


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
