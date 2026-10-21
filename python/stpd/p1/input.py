"""Lossless public-field projection for M-std; transport IDs never become features.

Containers are entities too, so ordered lists, missing values and reference edges
remain explicit. The only omitted public keys are fixed schema/profile/revision
and catalog choice_id (a binding returned by the Agent, not a learned feature).
The SDK's modelView removes envelope; this implementation does the same.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from tokenizers import Tokenizer, models, pre_tokenizers, trainers

from .data import digest, json_bytes

NUMBER = re.compile(r"(?<![\w])[-+]?(?:\d+\.?\d*|\.\d+)(?![\w])")
TEXT_KEYS = {"description", "name", "label", "title", "prompt", "relic_description"}
LEGACY_INPUT_SPEC: dict[str, Any] = {
    "id": "stpd-p1-mstd-input",
    "version": "1",
    "profile": "p1-global-r1",
    "revision": "r1.4",
    "observation": "SDK modelView; current observation only; complete catalog",
    "projection": "containers and primitive fields; parent/child and alias references",
    "binding_only": ["envelope", "entity_id aliases", "choice_id"],
    "constant_metadata": ["schema", "profile", "revision"],
    "text": "train-only byte-level BPE 4096; numeric spans replaced by <NUM>",
    "numeric": "field-specific signed log1p; explicit null and absent markers",
    "truncate": False,
    "model_state": "stateless every observation",
    "targets_in_input": False,
}
# r1.5 changes the owning catalog semantics, not the model representation.
INPUT_SPEC = {**LEGACY_INPUT_SPEC, "version": "2", "revision": "r1.5"}


def validate_input_spec(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or (value != INPUT_SPEC and value != LEGACY_INPUT_SPEC):
        raise ValueError("unsupported P1 M-std InputSpec")
    return value


def normalized(text: str) -> tuple[str, list[float]]:
    values: list[float] = []

    def replace(match: re.Match[str]) -> str:
        values.append(float(match.group()))
        return "<NUM>"

    return NUMBER.sub(replace, text), values


@dataclass
class Entity:
    kind: str
    position: int
    identities: list[tuple[str, str]] = field(default_factory=list)
    text: list[tuple[str, str]] = field(default_factory=list)
    numeric: list[tuple[str, float | None]] = field(default_factory=list)
    refs: list[int] = field(default_factory=list)


@dataclass
class Frame:
    entities: list[Entity]
    choices: list[dict[str, Any]]
    roles: list[tuple[int, int]]


def project(
    observation: dict[str, Any], *, input_projection: dict[str, Any] | None = None
) -> Frame:
    spec = validate_input_spec(INPUT_SPEC if input_projection is None else input_projection)
    if (observation.get("profile"), observation.get("revision")) != (
        spec["profile"], spec["revision"]
    ):
        raise ValueError("P1 M-std observation differs from bound InputSpec revision")
    entities: list[Entity] = []
    aliases: dict[str, int] = {}
    pending: list[tuple[int, str]] = []

    def visit(value: Any, path: str, position: int = 0, parent: int | None = None) -> int:
        row = len(entities)
        node = Entity(path, position)
        entities.append(node)
        if parent is not None:
            entities[parent].refs.append(row)
        if isinstance(value, dict):
            if isinstance(value.get("entity_id"), str):
                first = aliases.setdefault(value["entity_id"], row)
                if first != row:
                    node.refs.append(first)
                    entities[first].refs.append(row)
            for key, item in sorted(value.items()):
                if key == "envelope" and path == "$":
                    continue
                if key in {"schema", "profile", "revision", "choice_id", "entity_id"}:
                    continue
                if isinstance(item, (dict, list)):
                    visit(item, path + "." + key, parent=row)
                elif item is None:
                    node.text.append((key, "<MISSING>"))
                    node.numeric.append((key, None))
                elif type(item) is bool:
                    node.text.append((key, "true" if item else "false"))
                elif type(item) in {int, float}:
                    if not math.isfinite(item):
                        raise ValueError("non-finite public numeric field")
                    node.numeric.append((key, float(item)))
                elif isinstance(item, str):
                    if key in {"subject", "target", "owner_entity_id", "target_entity_id"}:
                        pending.append((row, item))
                    elif key.endswith("_id"):
                        # Definition IDs are semantic IDs. Public entity aliases are
                        # resolved below and never passed through this embedding.
                        node.identities.append((key, item))
                    else:
                        node.text.append((key, item))
                else:
                    raise ValueError("unsupported public field")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                visit(item, path + "[]", i, row)
        elif value is None:
            node.text.append(("value", "<MISSING>"))
            node.numeric.append(("value", None))
        elif type(value) in {int, float}:
            node.numeric.append(("value", float(value)))
        else:
            node.text.append(("value", str(value)))
        return row

    visit(observation, "$")
    for row, alias in pending:
        if alias not in aliases:
            # P1 can expose selector owner identity without its body. Preserve
            # that fact as a reference-only node; never invent an entity body.
            aliases[alias] = len(entities)
            entities.append(
                Entity("public_reference_only", 0, text=[("availability", "reference_only")])
            )
        entities[row].refs.append(aliases[alias])
    # A few public fields carry aliases under domain-specific names. Resolve all
    # exact alias values in strings and remove the occurrence-dependent spelling.
    for node in entities:
        text = []
        for key, value in node.text:
            if value in aliases:
                node.refs.append(aliases[value])
                text.append((key, "<REF>"))
            else:
                text.append((key, value))
        node.text = text
        identities = []
        for key, value in node.identities:
            if value in aliases:
                node.refs.append(aliases[value])
            else:
                identities.append((key, value))
        node.identities = identities
    choices = observation["catalog"]
    roles = []
    for choice in choices:
        roles.append(
            (aliases.get(choice.get("subject"), -1), aliases.get(choice.get("target"), -1))
        )
    return Frame(entities, choices, roles)


def fit_vocabulary(frames: list[Frame]) -> dict[str, Any]:
    tokenizer = Tokenizer(models.BPE(unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    trainer = trainers.BpeTrainer(
        vocab_size=4096,
        show_progress=False,  # stdout is the private worker's structured event channel.
        min_frequency=2,
        special_tokens=["[PAD]", "[UNK]", "<NUM>", "<MISSING>", "<REF>"],
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
    )
    descriptions = sorted(
        {
            normalized(text)[0]
            for f in frames
            for e in f.entities
            for key, text in e.text
            if key in TEXT_KEYS and text
        }
    )
    tokenizer.train_from_iterator(descriptions, trainer=trainer)
    return {
        "tokenizer": tokenizer.to_str(),
        "ids": [
            "[UNK]",
            *sorted({value for f in frames for e in f.entities for _, value in e.identities}),
        ],
        "kinds": ["[UNK]", *sorted({e.kind for f in frames for e in f.entities})],
        "fields": [
            "[UNK]",
            *sorted(
                {
                    key
                    for f in frames
                    for e in f.entities
                    for key, _ in e.text + e.numeric + e.identities
                }
            ),
        ],
        "actions": ["[UNK]", *sorted({c["kind"] for f in frames for c in f.choices})],
        "training_description_sha256": digest(json_bytes(descriptions)),
        "input_spec": INPUT_SPEC,
    }


def signed_log(value: float) -> float:
    return math.copysign(math.log1p(abs(value)), value)
