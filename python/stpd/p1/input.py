"""Lossless public-field projection for M-std; transport IDs never become features.

Containers are entities too, so ordered lists, missing values and reference edges
remain explicit. The only omitted public keys are fixed schema/profile/revision
and catalog choice_id (a binding returned by the Agent, not a learned feature).
The SDK's modelView removes envelope; this implementation does the same.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
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
SORTED_INPUT_SPEC = {
    **INPUT_SPEC, "id": "stpd-p1-mstd-content-sorted", "version": "1",
    "ordering": "public graph content order; P1 unordered domains only",
}
V2_INPUT_SPEC = {
    **INPUT_SPEC, "id": "stpd-p1-mstd-input-v2", "version": "1",
    "ordering": "shared position for P1 unordered domains; ordered domains retained",
    "text": "fixed public-glossary byte-level BPE 4096; append-only public vocabulary",
    "alias_views": "symmetric public references; mean of all public role views",
}


def validate_input_spec(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value not in (
        INPUT_SPEC, LEGACY_INPUT_SPEC, SORTED_INPUT_SPEC, V2_INPUT_SPEC
    ):
        raise ValueError("unsupported P1 M-std InputSpec")
    return value


def training_projection(value: object) -> dict[str, Any]:
    spec = validate_input_spec(value)
    if spec not in (INPUT_SPEC, V2_INPUT_SPEC):
        raise ValueError("new training requires v1 or v2 projection")
    return spec


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


Role = int | tuple[int, ...]


@dataclass
class Frame:
    entities: list[Entity]
    choices: list[dict[str, Any]]
    roles: list[tuple[Role, Role]]


@lru_cache(maxsize=4096)
def _field_suffix(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_]+", key):
        return "." + key
    return "[" + json.dumps(key, ensure_ascii=False, separators=(",", ":")) + "]"


def public_field_path(path: str, key: str) -> str:
    """SDK publicFieldPath: literal extension keys cannot impersonate structure."""
    return path + _field_suffix(key)


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
    views: dict[str, list[int]] = {}
    sorting_bindings: dict[int, set[str]] = {}
    symmetric = spec in (SORTED_INPUT_SPEC, V2_INPUT_SPEC)
    pending: list[tuple[int, str]] = []
    relation = None
    identity_relation = None
    if symmetric:
        from .input_identity import identity_paths

        identity_relation = identity_paths(observation)
        relation = identity_relation if spec == V2_INPUT_SPEC else None

    def visit(value: Any, path: str, position: int = 0, parent: int | None = None,
              source_path: str = "$") -> int:
        row = len(entities)
        node = Entity(path, position)
        entities.append(node)
        if parent is not None:
            entities[parent].refs.append(row)
        if (spec == SORTED_INPUT_SPEC and isinstance(value, str)
                and identity_relation is not None and identity_relation(source_path) is not None):
            sorting_bindings[row] = {"value"}
        if isinstance(value, dict):
            field_roles = ({key: identity_relation(public_field_path(source_path, key))
                            for key, item in value.items() if isinstance(item, str)}
                           if identity_relation is not None else {})
            if spec == SORTED_INPUT_SPEC:
                sorting_bindings[row] = {key for key, role in field_roles.items()
                                         if role is not None}
            declarations = ([value[key] for key, role in field_roles.items() if role == "declared"]
                            if relation is not None else
                            [value["entity_id"]] if isinstance(value.get("entity_id"), str) else [])
            for alias in declarations:
                first = aliases.setdefault(alias, row)
                if symmetric:
                    members = views.setdefault(alias, [])
                    if row not in members:
                        members.append(row)
                elif first != row:
                    node.refs.append(first)
                    entities[first].refs.append(row)
            for key, item in sorted(value.items()):
                if key == "envelope" and path == "$":
                    continue
                constant = key in {"schema", "profile", "revision"} and (
                    relation is None or path == "$")
                binding = key == "choice_id" and (relation is None or path == "$.catalog[]")
                if constant or binding or relation is None and key == "entity_id":
                    continue
                role = field_roles.get(key) if relation is not None else None
                if role == "declared" and isinstance(item, str):
                    continue
                if role == "reference" and isinstance(item, str):
                    pending.append((row, item))
                    node.text.append((key, "<REF>"))
                    continue
                if isinstance(item, (dict, list)):
                    child_source = (public_field_path(source_path, key) if symmetric
                                    else source_path + "." + key)
                    child_path = public_field_path(path, key) if symmetric else path + "." + key
                    visit(item, child_path, parent=row, source_path=child_source)
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
                    if relation is None and key in {
                        "subject", "target", "owner_entity_id", "target_entity_id"
                    }:
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
                visit(item, path + "[]", i, row, source_path + f"[{i}]")
        elif (isinstance(value, str) and relation is not None
              and relation(source_path) == "reference"):
            pending.append((row, value))
            node.text.append(("value", "<REF>"))
        elif value is None:
            node.text.append(("value", "<MISSING>"))
            node.numeric.append(("value", None))
        elif type(value) in {int, float}:
            node.numeric.append(("value", float(value)))
        else:
            node.text.append(("value", str(value)))
        return row

    visit(observation, "$")
    tree_refs = [list(entity.refs) for entity in entities] if spec == SORTED_INPUT_SPEC else None
    reference_links = list(pending) if spec == SORTED_INPUT_SPEC else None
    if symmetric:
        # All public views of one entity have the same status. A first-visited
        # star anchor would make unordered Reads change both edges and roles.
        for members in views.values():
            for row in members:
                entities[row].refs.extend(other for other in members if other != row)
    for row, alias in pending:
        if alias not in aliases:
            # P1 can expose selector owner identity without its body. Preserve
            # that fact as a reference-only node; never invent an entity body.
            aliases[alias] = len(entities)
            entities.append(
                Entity("public_reference_only", 0, text=[("availability", "reference_only")])
            )
            if tree_refs is not None:
                tree_refs.append([])
            if symmetric:
                views[alias] = [aliases[alias]]
        entities[row].refs.extend(views[alias] if symmetric else [aliases[alias]])
    # A few public fields carry aliases under domain-specific names. Resolve all
    # exact alias values in strings and remove the occurrence-dependent spelling.
    for row, node in enumerate(entities):
        if relation is not None:
            continue
        text = []
        for key, value in node.text:
            if value in aliases:
                node.refs.extend(views[value] if symmetric else [aliases[value]])
                if reference_links is not None:
                    reference_links.append((row, value))
                text.append((key, "<REF>"))
            else:
                text.append((key, value))
        node.text = text
        identities = []
        for key, value in node.identities:
            if value in aliases:
                node.refs.extend(views[value] if symmetric else [aliases[value]])
                if reference_links is not None:
                    reference_links.append((row, value))
            else:
                identities.append((key, value))
        node.identities = identities
    choices = observation["catalog"]
    roles: list[tuple[Role, Role]] = []
    for choice in choices:
        if symmetric:
            source_views = views.get(choice.get("subject"), [])
            target_views = views.get(choice.get("target"), [])
            roles.append((tuple(source_views) if source_views else -1,
                          tuple(target_views) if target_views else -1))
        else:
            roles.append((aliases.get(choice.get("subject"), -1),
                          aliases.get(choice.get("target"), -1)))
    frame = Frame(entities, choices, roles)
    if spec in (SORTED_INPUT_SPEC, V2_INPUT_SPEC):
        from .input_order import order_frame

        return order_frame(frame, observation, sorted_positions=spec == SORTED_INPUT_SPEC,
                           alias_views=views if spec == SORTED_INPUT_SPEC else None,
                           reference_links=reference_links, tree_refs=tree_refs,
                           sorting_bindings=sorting_bindings)
    return frame


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
