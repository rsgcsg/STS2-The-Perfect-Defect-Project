"""Versioned dual-codec inputs for the Stage1a stateless light-action M0 graph."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.store import ArtifactStore

from ..light_action_codec import SPEC, SPEC_BYTES, SPEC_SHA256, encode_action
from ..qwen.l1 import load_pin
from ..stage1a_recipes import LIGHT_ACTION_M0_GRAPH
from .features import ModelSample
from .text_menu_inputs import IDENTITY as TEXT_MENU_RENDERER
from .token_inputs import MAX_PAYLOAD, _source, input_texts

SCHEMA = "stpd/stage1a-light-action-m0-dual-input-v1"
INPUT_FORMAT = "stpd-token-light-action-m0-v1"
CANONICAL_SCHEMA = "stpd/stage1a-light-action-m0-canonical-input-v1"
CANONICAL_INPUT_FORMAT = "stpd-token-light-action-m0-canonical-v1"
TRAINING_BINDING_SCHEMA = "stpd/light-action-m0-training-binding-v1"
MAX_TOKENIZER_BYTES = 16 * 1024 * 1024
TEXT_MENU_VIEW_SCHEMA = "stpd/text-menu-bc-view-v1"
DEFAULT_MAX_STATE_TOKENS = 8192
DEFAULT_MAX_ACTION_BYTES = 8192


@dataclass(frozen=True)
class LightActionTokenRow:
    state: tuple[int, ...]
    actions: tuple[tuple[int, ...], ...]
    action_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"state": list(self.state),
                "actions": [list(action) for action in self.actions],
                "action_ids": list(self.action_ids)}


@dataclass(frozen=True)
class LoadedLightActionInputs:
    manifest: Manifest
    samples: tuple[ModelSample, ...]
    rows: tuple[LightActionTokenRow, ...]
    state_tokenizer: Tokenizer


def fit_state_bpe(samples: tuple[ModelSample, ...], *, vocab_size: int = 8192) -> bytes:
    """Fit state text only on train rows; actions use the independent byte codec."""
    if type(vocab_size) is not int or not 256 <= vocab_size <= 8192:
        raise BoundaryError("light_action_inputs", "invalid_state_vocab_target")
    training = [sample for sample in samples if sample.split == "train"]
    if not training:
        raise BoundaryError("light_action_inputs", "empty_train")
    tokenizer = Tokenizer(models.BPE())
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=vocab_size, min_frequency=2, show_progress=False,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), special_tokens=[],
    )
    tokenizer.train_from_iterator(
        (input_texts(sample.state_text, ())[0] for sample in training), trainer=trainer,
    )
    return tokenizer.to_str().encode("utf-8")  # type: ignore[no-any-return]


def _source_view(
    store: ArtifactStore, view_id: str,
) -> tuple[Manifest, tuple[ModelSample, ...], dict[str, Any]]:
    view = store.get_manifest(view_id)
    schema = view.parameters.value().get("schema")
    if (view.kind != "model_view"
            or schema not in {TEXT_MENU_VIEW_SCHEMA, "stpd/decision-model-view-v1"}):
        raise BoundaryError("light_action_inputs", "supported_m0_model_view_required")
    if schema == TEXT_MENU_VIEW_SCHEMA:
        renderer = TEXT_MENU_RENDERER
    else:
        from .representation import FullRunSerializer

        serializer_info = view.parameters.value().get("serializer")
        if not isinstance(serializer_info, dict):
            raise BoundaryError("light_action_inputs", "canonical_serializer_required")
        serializer = FullRunSerializer(serializer_info.get("profile", ""))
        if serializer.identity != serializer_info:
            raise BoundaryError("light_action_inputs", "canonical_serializer_mismatch")
        renderer = serializer.identity
    samples = _source(store, view_id)
    if any(not sample.action_texts or len(sample.action_texts) != len(sample.action_keys)
           or len(set(sample.action_keys)) != len(sample.action_keys)
           or not 0 <= sample.chosen_index < len(sample.action_keys)
           for sample in samples):
        raise BoundaryError("light_action_inputs", "complete_action_catalog_required")
    if {sample.split for sample in samples} != {"train", "dev"}:
        raise BoundaryError("light_action_inputs", "engineering_train_dev_only")
    return view, samples, renderer


def _training_binding(store: ArtifactStore, view: Manifest, value: object) -> dict[str, Any]:
    from spireagent.json_boundary import digest

    if not isinstance(value, dict) or set(value) != {
        "schema", "dataset_ids", "training_operation_id", "allocation_id", "model_view_id",
    }:
        raise BoundaryError("light_action_inputs", "training_binding_required")
    datasets = value["dataset_ids"]
    if (value["schema"] != TRAINING_BINDING_SCHEMA
            or not isinstance(datasets, list) or not datasets
            or any(not isinstance(identity, str) for identity in datasets)
            or datasets != sorted(set(datasets))):
        raise BoundaryError("light_action_inputs", "training_binding_invalid")
    for identity in datasets:
        digest(identity, "light_action_inputs.dataset_id")
    digest(value["training_operation_id"], "light_action_inputs.training_operation_id",
           length=32)
    digest(value["allocation_id"], "light_action_inputs.allocation_id")
    digest(value["model_view_id"], "light_action_inputs.model_view_id")
    if (view.parameters.value().get("schema") != "stpd/decision-model-view-v1"
            or sorted(parent.role for parent in view.parents) != ["allocation", "dataset"]
            or datasets != [view.parent("dataset")]
            or value["allocation_id"] != view.parent("allocation")
            or value["model_view_id"] != view.artifact_id):
        raise BoundaryError("light_action_inputs", "training_binding_ancestry_mismatch")
    allocation = store.get_manifest(view.parent("allocation"))
    if (allocation.kind != "protocol" or allocation.parent("dataset") != datasets[0]):
        raise BoundaryError("light_action_inputs", "training_binding_ancestry_mismatch")
    return value


def canonical_training_binding(store: ArtifactStore, identity: str) -> dict[str, Any]:
    """Read and validate the immutable canonical M0 dataset/operation binding.

    This intentionally reads only manifests. Callers use it to ask the configured
    curation owner for admission before loading the decision rows or model payloads.
    The full input loader repeats the checks and verifies every derived payload.
    """
    manifest = store.get_manifest(identity)
    info = manifest.parameters.value()
    if (manifest.kind != "training_input" or info.get("schema") != CANONICAL_SCHEMA
            or [parent.role for parent in manifest.parents] != ["model_view"]):
        raise BoundaryError("light_action_inputs", "canonical_training_input_required")
    view = store.get_manifest(manifest.parent("model_view"))
    if view.parameters.value().get("schema") != "stpd/decision-model-view-v1":
        raise BoundaryError("light_action_inputs", "input_family_source_mismatch")
    return _training_binding(store, view, info.get("training_binding"))


def _state_codec(raw: bytes, tokenizer: Tokenizer, family: Literal["s", "qwen3"],
                 max_state_tokens: int) -> dict[str, Any]:
    pin = load_pin() if family == "qwen3" else None
    if pin is None:
        special_ids: dict[str, Any] = {"bos": None, "eos": None, "pad": None,
                                       "additional": []}
        return {
            "schema": "stpd/state-byte-bpe-v1", "family": "train-only-byte-bpe",
            "sha256": hashlib.sha256(raw).hexdigest(), "vocab_size": tokenizer.get_vocab_size(),
            "special_token_ids": special_ids, "add_special_tokens": False,
            "max_tokens": max_state_tokens,
            "renderer": "OBS\\n<public state>\\n",
        }
    roles = {role: value.token_id for value in pin.special_tokens
             for role in value.roles}
    return {
        "schema": "qwen3-tokenizer-json-v1", "family": "pinned-qwen3",
        "model_id": pin.model_id, "revision": pin.repo_revision,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "tokenizer_bundle_sha256": pin.tokenizer_bundle_sha256,
        "vocab_size": tokenizer.get_vocab_size(),
        "special_token_ids": {
            "bos": roles.get("bos_token"), "eos": roles.get("eos_token"),
            "pad": roles.get("pad_token"),
            "additional": [value.token_id for value in pin.special_tokens
                            if "additional_special_tokens" in value.roles],
        },
        "add_special_tokens": False, "max_tokens": max_state_tokens,
        "renderer": "OBS\\n<public state>\\n",
    }


def _compile(
    samples: tuple[ModelSample, ...], raw: bytes, family: Literal["s", "qwen3"], *,
    max_state_tokens: int, max_action_bytes: int,
    source_schema: str = TEXT_MENU_VIEW_SCHEMA,
    source_renderer: dict[str, Any] | None = None,
) -> tuple[Tokenizer, tuple[LightActionTokenRow, ...], dict[str, Any]]:
    if (type(max_state_tokens) is not int or not 1 <= max_state_tokens <= 8192
            or type(max_action_bytes) is not int or not 1 <= max_action_bytes <= 8192):
        raise BoundaryError("light_action_inputs", "invalid_independent_length_limits")
    if family not in {"s", "qwen3"}:
        raise BoundaryError("light_action_inputs", "unsupported_state_codec")
    digest = hashlib.sha256(raw).hexdigest()
    if family == "qwen3":
        pin = load_pin()
        expected = pin.file_by_name["tokenizer.json"]
        if (digest != expected.sha256 or len(raw) != expected.size_bytes
            or max_state_tokens > pin.hard_limit):
            raise BoundaryError("light_action_inputs", "qwen_state_tokenizer_pin_mismatch")
    elif raw != fit_state_bpe(samples):
        raise BoundaryError("light_action_inputs", "state_bpe_must_fit_train_only")
    try:
        tokenizer = Tokenizer.from_str(raw.decode("utf-8"))
    except Exception as error:
        raise BoundaryError("light_action_inputs", "invalid_state_tokenizer") from error
    if tokenizer.truncation is not None or tokenizer.padding is not None:
        raise BoundaryError("light_action_inputs", "state_padding_or_truncation_forbidden")
    rows: list[LightActionTokenRow] = []
    state_lengths: list[int] = []
    action_lengths: list[int] = []
    for sample in samples:
        if (not sample.action_texts or len(sample.action_texts) != len(sample.action_keys)
                or len(set(sample.action_keys)) != len(sample.action_keys)
                or not 0 <= sample.chosen_index < len(sample.action_keys)):
            raise BoundaryError("light_action_inputs", "source_catalog_binding_mismatch")
        state_text, _ = input_texts(sample.state_text, ())
        state_ids = tuple(tokenizer.encode(state_text, add_special_tokens=False).ids)
        action_ids = tuple(encode_action(action, max_bytes=max_action_bytes)
                           for action in sample.action_texts)
        state_lengths.append(len(state_ids))
        action_lengths.extend(len(action) - 2 for action in action_ids)
        if not state_ids:
            raise BoundaryError("light_action_inputs", "empty_state_encoding")
        if len(state_ids) > max_state_tokens:
            raise BoundaryError("light_action_inputs", "state_token_limit_exceeded_no_truncation")
        rows.append(LightActionTokenRow(state_ids, action_ids, sample.action_keys))
    # Recheck every row/catalog as a unit before any worker is constructed.
    if len(rows) != len(samples) or any(
        len(row.actions) != len(sample.action_keys)
        or row.action_ids != sample.action_keys
        for row, sample in zip(rows, samples, strict=True)
    ):
        raise BoundaryError("light_action_inputs", "catalog_projection_mismatch")
    state_codec = _state_codec(raw, tokenizer, family, max_state_tokens)
    canonical = source_schema == "stpd/decision-model-view-v1"
    if source_schema not in {TEXT_MENU_VIEW_SCHEMA, "stpd/decision-model-view-v1"}:
        raise BoundaryError("light_action_inputs", "supported_m0_model_view_required")
    if source_renderer is None:
        source_renderer = TEXT_MENU_RENDERER
    info = {
        "schema": CANONICAL_SCHEMA if canonical else SCHEMA,
        "format": CANONICAL_INPUT_FORMAT if canonical else INPUT_FORMAT,
        "graph": LIGHT_ACTION_M0_GRAPH,
        "source_schema": source_schema, "source_renderer": source_renderer,
        "state_codec": state_codec,
        "action_codec": {**SPEC, "sha256": SPEC_SHA256,
                         "source_renderer": source_renderer},
        "max_state_tokens": max_state_tokens, "max_action_bytes": max_action_bytes,
        "samples": len(samples),
        "counts": {split: sum(sample.split == split for sample in samples)
                   for split in ("train", "dev")},
        "state_lengths": {
            "min": min(state_lengths), "max": max(state_lengths),
            "p50": sorted(state_lengths)[(len(state_lengths) - 1) // 2],
        },
        "action_byte_lengths": {
            "min": min(action_lengths), "max": max(action_lengths),
            "p50": sorted(action_lengths)[(len(action_lengths) - 1) // 2],
        },
        "purpose": "engineering", "fit_scope": "train_only" if family == "s" else "pinned",
        "projection": ("recompiled_from_exact_canonical_decision_model_view_v1" if canonical
                       else "recompiled_from_exact_text_menu_model_view_v1"),
    }
    return tokenizer, tuple(rows), info


def publish_light_action_inputs(
    store: ArtifactStore, view_id: str, state_family: Literal["s", "qwen3"],
    producer: Producer, *, snapshot: Path | None = None,
    max_state_tokens: int = DEFAULT_MAX_STATE_TOKENS,
    max_action_bytes: int = DEFAULT_MAX_ACTION_BYTES,
    training_binding: dict[str, Any] | None = None,
) -> Manifest:
    view, samples, renderer = _source_view(store, view_id)
    canonical = view.parameters.value().get("schema") == "stpd/decision-model-view-v1"
    binding = _training_binding(store, view, training_binding) if canonical else None
    if not canonical and training_binding is not None:
        raise BoundaryError("light_action_inputs", "training_binding_only_for_canonical_m0")
    if state_family == "s" and snapshot is None:
        raw = fit_state_bpe(samples)
    elif state_family == "qwen3" and snapshot is not None:
        path = snapshot / "tokenizer.json"
        if path.stat().st_size > MAX_TOKENIZER_BYTES:
            raise BoundaryError("light_action_inputs", "state_tokenizer_size_limit")
        raw = path.read_bytes()
    else:
        raise BoundaryError("light_action_inputs", "snapshot_only_for_qwen_state_codec")
    _, rows, info = _compile(samples, raw, state_family, max_state_tokens=max_state_tokens,
                             max_action_bytes=max_action_bytes,
                             source_schema=view.parameters.value()["schema"],
                             source_renderer=renderer)
    if binding is not None:
        info["training_binding"] = binding
    encoded_rows = b"".join(json_bytes(row.to_dict()) for row in rows)
    if len(encoded_rows) > MAX_PAYLOAD:
        raise BoundaryError("light_action_inputs", "input_size_limit")
    state_payload = store.put_payload("state_tokenizer", io.BytesIO(raw), "application/json")
    action_payload = store.put_payload("action_codec", io.BytesIO(SPEC_BYTES), "application/json")
    rows_payload = store.put_payload("rows", io.BytesIO(encoded_rows), "application/x-ndjson")
    manifest = Manifest(
        "training_input", producer, (Parent("model_view", view_id),),
        (state_payload, action_payload, rows_payload), FrozenObject.of(info),
    )
    store.publish(manifest)
    return manifest


def load_light_action_inputs(store: ArtifactStore, identity: str) -> LoadedLightActionInputs:
    manifest = store.get_manifest(identity)
    info = manifest.parameters.value()
    if (manifest.kind != "training_input" or info.get("schema") not in {SCHEMA, CANONICAL_SCHEMA}
            or [parent.role for parent in manifest.parents] != ["model_view"]
            or sorted(payload.role for payload in manifest.payloads)
            != ["action_codec", "rows", "state_tokenizer"]):
        raise BoundaryError("light_action_inputs", "unsupported_contract")
    view_id = manifest.parent("model_view")
    # Cross-check the declared input family against its immutable source manifest
    # before _source_view can read the source dataset's row payloads. In particular,
    # a forged legacy input schema over a canonical allocation must fail closed
    # before loading training examples.
    view_manifest = store.get_manifest(view_id)
    canonical = (view_manifest.kind == "model_view"
                 and view_manifest.parameters.value().get("schema")
                 == "stpd/decision-model-view-v1")
    if canonical != (info.get("schema") == CANONICAL_SCHEMA):
        raise BoundaryError("light_action_inputs", "input_family_source_mismatch")
    binding = (_training_binding(store, view_manifest, info.get("training_binding"))
               if canonical else None)
    if not canonical and "training_binding" in info:
        raise BoundaryError("light_action_inputs", "unexpected_training_binding")
    view, samples, renderer = _source_view(store, view_id)
    if (manifest.payload("state_tokenizer").size > MAX_TOKENIZER_BYTES
            or manifest.payload("action_codec").size != len(SPEC_BYTES)
            or manifest.payload("rows").size > MAX_PAYLOAD):
        raise BoundaryError("light_action_inputs", "payload_size_limit")
    raw = b"".join(store.read_payload(manifest.payload("state_tokenizer")))
    action_raw = b"".join(store.read_payload(manifest.payload("action_codec")))
    if action_raw != SPEC_BYTES or hashlib.sha256(action_raw).hexdigest() != SPEC_SHA256:
        raise BoundaryError("light_action_inputs", "action_codec_identity_mismatch")
    state_info = info.get("state_codec")
    if not isinstance(state_info, dict):
        raise BoundaryError("light_action_inputs", "state_codec_identity_mismatch")
    family_name = state_info.get("family")
    if family_name == "train-only-byte-bpe":
        family: Literal["s", "qwen3"] = "s"
    elif family_name == "pinned-qwen3":
        family = "qwen3"
    else:
        raise BoundaryError("light_action_inputs", "state_codec_identity_mismatch")
    max_state_tokens = info.get("max_state_tokens")
    max_action_bytes = info.get("max_action_bytes")
    if type(max_state_tokens) is not int or type(max_action_bytes) is not int:
        raise BoundaryError("light_action_inputs", "invalid_independent_length_limits")
    tokenizer, rows, expected = _compile(
        samples, raw, family, max_state_tokens=max_state_tokens,
        max_action_bytes=max_action_bytes,
        source_schema=view.parameters.value()["schema"], source_renderer=renderer,
    )
    if binding is not None:
        expected["training_binding"] = binding
    encoded_rows = b"".join(json_bytes(row.to_dict()) for row in rows)
    if (info != expected
            or b"".join(store.read_payload(manifest.payload("rows"))) != encoded_rows):
        raise BoundaryError("light_action_inputs", "source_projection_mismatch")
    return LoadedLightActionInputs(manifest, samples, rows, tokenizer)
