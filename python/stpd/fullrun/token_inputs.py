"""Verified Stage 1a token inputs over existing decision allocations and ArtifactStore.

Both B and D consume the same token IDs. Scratch fitting reads train text only;
dev is encoded with the fixed vocabulary. No labels, IDs or successors enter text.
This is an engineering train/dev input, not a new dataset or permission ledger.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.store import ArtifactStore

from ..qwen.l1 import load_pin
from .dataset_policy import training_sources
from .decision_training import VIEW_SCHEMA
from .features import ModelSample, load_model_view
from .public_bc import LEGACY_VIEW_SCHEMA
from .public_bc import VIEW_SCHEMA as PUBLIC_BC_SCHEMA

SCHEMA = "stpd/stage1a-token-input-v1"
FORMAT = "separate-obs-act-text-v1"
MAX_TOKENS = 8192
MAX_PAYLOAD = 256 * 1024**2


def input_texts(state: str, actions: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Delimit roles explicitly. Encode separately, then concatenate IDs for B."""
    return "OBS\n" + state + "\n", tuple("ACT\n" + a + "\n" for a in actions)


def fit_scratch(samples: tuple[ModelSample, ...], *, vocab_size: int = 8192) -> bytes:
    if type(vocab_size) is not int or not 256 <= vocab_size <= 8192:
        raise BoundaryError("tokens", "invalid_vocab_target")
    training = [s for s in samples if s.split == "train"]
    if not training:
        raise BoundaryError("tokens", "empty_train")
    # The full byte alphabet covers unseen UTF-8 without a reserved text marker that
    # could accidentally swallow a literal card/rule string during decoding.
    tokenizer = Tokenizer(models.BPE())
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=vocab_size, min_frequency=2, show_progress=False,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), special_tokens=[],
    )
    texts = (
        text
        for sample in training
        for text in (input_texts(sample.state_text, sample.action_texts)[0],
                     *input_texts(sample.state_text, sample.action_texts)[1])
    )
    tokenizer.train_from_iterator(texts, trainer=trainer)
    return tokenizer.to_str().encode("utf-8")  # type: ignore[no-any-return]


@dataclass(frozen=True)
class TokenRow:
    state: tuple[int, ...]
    actions: tuple[tuple[int, ...], ...]

    def to_dict(self) -> dict[str, Any]:
        return {"state": list(self.state), "actions": [list(a) for a in self.actions]}


def encode_texts(
    tokenizer: Any, state: str, actions: tuple[str, ...], *, max_tokens: int = MAX_TOKENS,
) -> TokenRow:
    if type(max_tokens) is not int or max_tokens <= 0:
        raise BoundaryError("tokens", "invalid_token_budget")
    if not actions:
        raise BoundaryError("tokens", "empty_catalog")
    state_text, action_texts = input_texts(state, actions)
    row = TokenRow(
        tuple(tokenizer.encode(state_text, add_special_tokens=False).ids),
        tuple(tuple(tokenizer.encode(a, add_special_tokens=False).ids) for a in action_texts),
    )
    if not row.state or any(not a for a in row.actions):
        raise BoundaryError("tokens", "empty_encoding")
    # Common input contract for all four graphs; retain every candidate or fail as a unit.
    if any(len(row.state) + len(a) + 1 > max_tokens for a in row.actions):
        raise BoundaryError("tokens", "joint_limit_exceeded_no_truncation")
    return row


def _source(store: ArtifactStore, view_id: str) -> tuple[ModelSample, ...]:
    training_sources(store, view_id)
    view, samples = load_model_view(store, view_id)
    allowed = {VIEW_SCHEMA, PUBLIC_BC_SCHEMA, LEGACY_VIEW_SCHEMA, "stpd/text-menu-bc-view-v1"}
    if view.parameters.value().get("schema") not in allowed:
        raise BoundaryError("tokens", "fixed_decision_allocation_required")
    if {s.split for s in samples} != {"train", "dev"}:
        raise BoundaryError("tokens", "engineering_train_dev_only")
    return samples


def _compile(
    samples: tuple[ModelSample, ...], raw: bytes, backbone: str, max_tokens: int = MAX_TOKENS,
) -> tuple[Any, tuple[TokenRow, ...], dict[str, Any]]:
    if backbone not in {"s", "pf"}:
        raise BoundaryError("tokens", "unsupported_backbone")
    digest = hashlib.sha256(raw).hexdigest()
    if backbone == "pf" and digest != load_pin().file_by_name["tokenizer.json"].sha256:
        raise BoundaryError("tokens", "qwen_tokenizer_pin_mismatch")
    if backbone == "s" and raw != fit_scratch(samples):
        raise BoundaryError("tokens", "scratch_must_be_fit_on_train_only")
    tokenizer = Tokenizer.from_str(raw.decode("utf-8"))
    if tokenizer.truncation is not None or tokenizer.padding is not None:
        raise BoundaryError("tokens", "padding_or_truncation_forbidden")
    rows = tuple(encode_texts(tokenizer, s.state_text, s.action_texts, max_tokens=max_tokens)
                 for s in samples)
    lengths = sorted(len(row.state) + len(a) + 1 for row in rows for a in row.actions)
    return tokenizer, rows, {
        "schema": SCHEMA, "backbone": backbone, "format": FORMAT,
        "tokenizer_sha256": digest, "vocab_size": tokenizer.get_vocab_size(),
        "max_tokens": max_tokens, "samples": len(samples),
        "counts": {split: sum(s.split == split for s in samples) for split in ("train", "dev")},
        "joint_lengths": {"min": lengths[0], "max": lengths[-1],
                          "p50": lengths[(len(lengths) - 1) // 2],
                          "p95": lengths[max(0, (95 * len(lengths) + 99) // 100 - 1)]},
        "purpose": "engineering", "fit_scope": "train_only" if backbone == "s" else "pinned",
    }


@dataclass(frozen=True)
class LoadedTokenInputs:
    manifest: Manifest
    samples: tuple[ModelSample, ...]
    rows: tuple[TokenRow, ...]
    tokenizer: Any


def publish_token_inputs(
    store: ArtifactStore, view_id: str, backbone: str, producer: Producer,
    *, snapshot: Path | None = None, max_tokens: int = MAX_TOKENS,
) -> Manifest:
    samples = _source(store, view_id)
    if backbone == "s" and snapshot is None:
        raw = fit_scratch(samples)
    elif backbone == "pf" and snapshot is not None:
        path = snapshot / "tokenizer.json"
        if path.stat().st_size > 16 * 1024**2:
            raise BoundaryError("tokens", "tokenizer_size_limit")
        raw = path.read_bytes()
    else:
        raise BoundaryError("tokens", "snapshot_only_for_pf")
    _, rows, info = _compile(samples, raw, backbone, max_tokens)
    encoded = b"".join(json_bytes(row.to_dict()) for row in rows)
    if len(encoded) > MAX_PAYLOAD:
        raise BoundaryError("tokens", "input_size_limit")
    tokenizer_payload = store.put_payload("tokenizer", io.BytesIO(raw), "application/json")
    rows_payload = store.put_payload("rows", io.BytesIO(encoded), "application/x-ndjson")
    manifest = Manifest(
        "training_input", producer, (Parent("model_view", view_id),),
        (tokenizer_payload, rows_payload), FrozenObject.of(info),
    )
    store.publish(manifest)
    return manifest


def load_token_inputs(store: ArtifactStore, identity: str) -> LoadedTokenInputs:
    manifest = store.get_manifest(identity)
    info = manifest.parameters.value()
    if (
        manifest.kind != "training_input" or info.get("schema") != SCHEMA
        or [p.role for p in manifest.parents] != ["model_view"]
        or sorted(p.role for p in manifest.payloads) != ["rows", "tokenizer"]
    ):
        raise BoundaryError("tokens", "unsupported_contract")
    samples = _source(store, manifest.parent("model_view"))
    if (manifest.payload("tokenizer").size > 16 * 1024**2
            or manifest.payload("rows").size > MAX_PAYLOAD):
        raise BoundaryError("tokens", "payload_size_limit")
    raw = b"".join(store.read_payload(manifest.payload("tokenizer")))
    tokenizer, rows, expected = _compile(samples, raw, info.get("backbone", ""),
                                          info["max_tokens"])
    encoded = b"".join(json_bytes(row.to_dict()) for row in rows)
    if info != expected or b"".join(store.read_payload(manifest.payload("rows"))) != encoded:
        raise BoundaryError("tokens", "input_projection_mismatch")
    return LoadedTokenInputs(manifest, samples, rows, tokenizer)
