"""Fixed public vocabulary and explicit append-only parameter transfer.

Training never fits this vocabulary. Extensions make a new immutable vocabulary
version; old packages retain their original bytes and exact resume identity.
"""
from __future__ import annotations

import copy
import json
from importlib.resources import files
from typing import TYPE_CHECKING, Any, cast

from .input import V2_INPUT_SPEC

if TYPE_CHECKING:
    from .model import MStd


def public_vocabulary() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(
        files("stpd.p1").joinpath("public_vocabulary_v1.json").read_text()))


def validate_vocabulary(value: dict[str, Any]) -> None:
    """Old prefixes and new append-only extensions share the pinned encoding."""
    base = public_vocabulary()
    if value.get("tokenizer") != base["tokenizer"]:
        raise ValueError("v2 tokenizer must retain its fixed public encoding")
    for table in ("ids", "fields", "kinds", "actions"):
        terms = value.get(table)
        if (not isinstance(terms, list) or not terms or terms[0] != "[UNK]"
                or any(not isinstance(term, str) for term in terms)
                or len(set(terms)) != len(terms)):
            raise ValueError("invalid v2 vocabulary rows")
        common = min(len(terms), len(base[table]))
        if terms[:common] != base[table][:common]:
            raise ValueError("v2 vocabulary must preserve the published row prefix")


def require_extension(old: dict[str, Any], new: dict[str, Any]) -> None:
    if (old.get("input_spec") != V2_INPUT_SPEC or new.get("input_spec") != V2_INPUT_SPEC
            or old["tokenizer"] != new["tokenizer"]):
        raise ValueError("parameter transfer requires the same v2 encoding and tokenizer")
    for table in ("ids", "fields", "kinds", "actions"):
        if (new[table][:len(old[table])] != old[table]
                or len(new[table]) != len(set(new[table]))):
            raise ValueError("vocabulary extension must preserve every old row")
    if len(new["ids"]) > 8192:
        raise ValueError("vocabulary extension exceeds M-std ID capacity")
    if (any(new[k] != old[k] for k in ("ids", "fields", "kinds", "actions"))
            and new.get("vocabulary_version", 0) <= old.get("vocabulary_version", 0)):
        raise ValueError("vocabulary extension requires a new version")


def parameters_from_checkpoint(raw: bytes, vocabulary: dict[str, Any]) -> MStd:
    """Load the exact old learned parameters before an explicit new-Run transfer.

    The caller retains the old checkpoint and its vocabulary artifact. This does
    not relabel optimizer/sampler state as an exact resume of a changed input.
    """
    import torch

    from stpd.workers.checkpoint_codec import decode_checkpoint

    from .data import digest, json_bytes
    from .execution import MStdExecution
    from .export import input_spec
    from .model import ARCHITECTURE, MStd
    from .spec import CHECKPOINT_SCHEMA

    state = decode_checkpoint(raw)
    identity = state["identity"]
    if (state.get("schema") != CHECKPOINT_SCHEMA
            or vocabulary.get("input_spec") != V2_INPUT_SPEC
            or identity.get("architecture") != ARCHITECTURE
            or identity.get("vocabulary_sha256") != digest(json_bytes(vocabulary))
            or identity.get("input_spec") != input_spec(vocabulary)):
        raise ValueError("old checkpoint/vocabulary binding differs")
    with torch.random.fork_rng(devices=[]):
        model = MStd(vocabulary, MStdExecution.decode(identity["execution"]))
    model.load_state_dict(state["model"], strict=True)
    if not all(bool(torch.isfinite(value).all()) for value in model.state_dict().values()):
        raise ValueError("non-finite checkpoint parameters")
    return model


def extend_parameters(model: MStd, vocabulary: dict[str, Any]) -> MStd:
    """Transfer learned parameters into a new Run; exact optimizer resume is separate.

    New embedding rows start at zero, including newly assigned preallocated ID
    rows. All old rows and every non-vocabulary parameter remain bit-identical.
    """
    import torch

    from .model import MStd

    require_extension(model.vocabulary, vocabulary)
    with torch.random.fork_rng(devices=[]):
        result = MStd(copy.deepcopy(vocabulary), model.execution).to(
            device=model.ids.weight.device, dtype=model.ids.weight.dtype)
    old = model.state_dict()
    state = result.state_dict()
    growing = {"kinds.weight", "fields.weight", "number_slope.weight", "number_bias.weight",
               "number_missing.weight", "action_types.weight"}
    for name, tensor in state.items():
        if name in growing:
            tensor.zero_()
            tensor[:old[name].shape[0]].copy_(old[name])
        else:
            tensor.copy_(old[name])
    state["ids.weight"][len(model.vocabulary["ids"]):len(vocabulary["ids"])].zero_()
    result.load_state_dict(state, strict=True)
    return result.train(model.training)
