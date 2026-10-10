"""One train-only union tokenizer and complete A/B preflight for paired M0."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Any

from tokenizers import Tokenizer

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.store import ArtifactStore

from ..light_action_codec import SPEC_BYTES, SPEC_SHA256, encode_action
from ..reward_m0_spec import INPUT_SCHEMA, INPUT_SPECS, MAX_SOURCE_BYTES, RewardM0Config
from ..stage1a_recipes import LIGHT_ACTION_M0_GRAPH
from .features import ModelSample
from .light_action_inputs import (
    LightActionTokenRow,
    LoadedLightActionInputs,
    _state_codec,
    fit_state_bpe,
)
from .reward_m0_source import VerifiedRewardM0Source, verify_reward_m0_source
from .token_inputs import input_texts

FEATURE_SCHEMA = "stpd/reward-m0-paired-union-token-features-v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def samples_for(source: VerifiedRewardM0Source, arm: str) -> tuple[ModelSample, ...]:
    if arm not in INPUT_SPECS:
        raise BoundaryError("reward_m0_inputs", "explicit_arm_required")
    return tuple(
        ModelSample(
            **{
                **row[arm],
                "action_texts": tuple(row[arm]["action_texts"]),
                "action_keys": tuple(row[arm]["action_keys"]),
            }
        )
        for row in source.value["rows"]
    )


def _compile(
    source: VerifiedRewardM0Source, tokenizer_raw: bytes, config: RewardM0Config
) -> tuple[Tokenizer, dict[str, Any]]:
    if (
        config.paired_schedule() != source.value["paired_config"]
        or not tokenizer_raw
        or len(tokenizer_raw) > 16 * 1024**2
    ):
        raise BoundaryError("reward_m0_inputs", "paired_configuration_or_tokenizer_mismatch")
    tokenizer = Tokenizer.from_str(tokenizer_raw.decode("utf-8"))
    if tokenizer.truncation is not None or tokenizer.padding is not None:
        raise BoundaryError("reward_m0_inputs", "padding_or_truncation_forbidden")
    rows = {}
    length_stats = {}
    for arm in ("A", "B"):
        encoded = []
        lengths: list[int] = []
        action_lengths: list[int] = []
        for sample in samples_for(source, arm):
            state = tuple(
                tokenizer.encode(
                    input_texts(sample.state_text, ())[0], add_special_tokens=False
                ).ids
            )
            actions = tuple(
                encode_action(text, max_bytes=config.max_action_bytes)
                for text in sample.action_texts
            )
            if not state or len(state) > config.max_state_tokens:
                raise BoundaryError("reward_m0_inputs", "complete_arm_state_limit_no_truncation")
            lengths.append(len(state))
            action_lengths.extend(len(action) - 2 for action in actions)
            encoded.append(LightActionTokenRow(state, actions, sample.action_keys).to_dict())
        rows[arm] = encoded
        length_stats[arm] = {
            "max_state_tokens": max(lengths),
            "total_state_tokens": sum(lengths),
            "max_action_bytes": max(action_lengths),
            "total_action_bytes": sum(action_lengths),
        }
    if any(
        left["actions"] != right["actions"] or left["action_ids"] != right["action_ids"]
        for left, right in zip(rows["A"], rows["B"], strict=True)
    ):
        raise BoundaryError("reward_m0_inputs", "paired_complete_C_mismatch")
    return tokenizer, {"rows": rows, "length_stats": length_stats}


def publish_pair_features(
    store: ArtifactStore, source: VerifiedRewardM0Source, config: RewardM0Config, producer: Producer
) -> Manifest:
    if config.paired_schedule() != source.value["paired_config"]:
        raise BoundaryError("reward_m0_inputs", "paired_configuration_mismatch")
    raw = fit_state_bpe(
        samples_for(source, "A") + samples_for(source, "B"), vocab_size=config.state_vocab_size
    )
    tokenizer, compiled = _compile(source, raw, config)
    data = json_bytes(compiled)
    if len(data) > MAX_SOURCE_BYTES:
        raise BoundaryError("reward_m0_inputs", "paired_token_capacity")
    payloads = (
        store.put_payload("state_tokenizer", io.BytesIO(raw), "application/json"),
        store.put_payload("action_codec", io.BytesIO(SPEC_BYTES), "application/json"),
        store.put_payload("paired_rows", io.BytesIO(data), "application/json"),
    )
    manifest = Manifest(
        "feature_set",
        producer,
        (Parent("source", source.manifest.artifact_id),),
        payloads,
        FrozenObject.of(
            {
                "schema": FEATURE_SCHEMA,
                "input_specs": INPUT_SPECS,
                "fit_partition": "paired_TRAIN_A_B_union",
                "config": config.paired_schedule(),
                "state_codec": _state_codec(raw, tokenizer, "s", config.max_state_tokens),
                "action_codec_sha256": SPEC_SHA256,
                "length_stats": compiled["length_stats"],
                "occurrence_ids": [row["occurrence_id"] for row in source.value["rows"]],
            }
        ),
    )
    store.publish(manifest)
    return manifest


def verify_pair_features(
    store: ArtifactStore, identity: str, config: RewardM0Config
) -> tuple[Manifest, VerifiedRewardM0Source, Tokenizer, dict[str, Any]]:
    feature = store.get_manifest(identity)
    if feature.kind != "feature_set" or feature.parameters.value().get("schema") != FEATURE_SCHEMA:
        raise BoundaryError("reward_m0_inputs", "typed_pair_features_required")
    source = verify_reward_m0_source(store, feature.parent("source"))
    raw = b"".join(store.read_payload(feature.payload("state_tokenizer")))
    tokenizer, expected = _compile(source, raw, config)
    info = feature.parameters.value()
    if (
        b"".join(store.read_payload(feature.payload("paired_rows"))) != json_bytes(expected)
        or b"".join(store.read_payload(feature.payload("action_codec"))) != SPEC_BYTES
        or info
        != {
            "schema": FEATURE_SCHEMA,
            "input_specs": INPUT_SPECS,
            "fit_partition": "paired_TRAIN_A_B_union",
            "config": config.paired_schedule(),
            "state_codec": _state_codec(raw, tokenizer, "s", config.max_state_tokens),
            "action_codec_sha256": SPEC_SHA256,
            "length_stats": expected["length_stats"],
            "occurrence_ids": [row["occurrence_id"] for row in source.value["rows"]],
        }
    ):
        raise BoundaryError("reward_m0_inputs", "paired_features_replay_mismatch")
    return feature, source, tokenizer, expected


def publish_reward_m0_inputs(
    store: ArtifactStore, source: VerifiedRewardM0Source, config: RewardM0Config, producer: Producer
) -> Manifest:
    # A/B use one immutable feature identity, independent of operation IDs and arm.
    features = publish_pair_features(store, source, config, producer)
    info = features.parameters.value()
    manifest = Manifest(
        "training_input",
        producer,
        (
            Parent("source", source.manifest.artifact_id),
            Parent("paired_features", features.artifact_id),
        ),
        features.payloads,
        FrozenObject.of(
            {
                "schema": INPUT_SCHEMA,
                "format": "stpd-reward-m0-dual-codec-v1",
                "arm": config.arm,
                "input_spec": INPUT_SPECS[config.arm],
                "graph": LIGHT_ACTION_M0_GRAPH,
                "paired_schedule": config.paired_schedule(),
                "paired_feature_id": features.artifact_id,
                "state_codec": info["state_codec"],
                "action_codec_sha256": SPEC_SHA256,
                "max_state_tokens": config.max_state_tokens,
                "max_action_bytes": config.max_action_bytes,
                "paired_preflight": info["length_stats"],
            }
        ),
    )
    store.publish(manifest)
    return manifest


@dataclass(frozen=True)
class LoadedRewardM0Inputs(LoadedLightActionInputs):
    arm: str
    source: VerifiedRewardM0Source
    config: RewardM0Config


def load_reward_m0_inputs(
    store: ArtifactStore, identity: str, config: RewardM0Config
) -> LoadedRewardM0Inputs:
    manifest = store.get_manifest(identity)
    info = manifest.parameters.value()
    if manifest.kind != "training_input" or info.get("schema") != INPUT_SCHEMA:
        raise BoundaryError("reward_m0_inputs", "typed_input_required")
    feature, source, tokenizer, compiled = verify_pair_features(
        store, manifest.parent("paired_features"), config
    )
    if (
        manifest.parent("source") != source.manifest.artifact_id
        or manifest.payloads != feature.payloads
        or info
        != {
            "schema": INPUT_SCHEMA,
            "format": "stpd-reward-m0-dual-codec-v1",
            "arm": config.arm,
            "input_spec": INPUT_SPECS[config.arm],
            "graph": LIGHT_ACTION_M0_GRAPH,
            "paired_schedule": config.paired_schedule(),
            "paired_feature_id": feature.artifact_id,
            "state_codec": feature.parameters.value()["state_codec"],
            "action_codec_sha256": SPEC_SHA256,
            "max_state_tokens": config.max_state_tokens,
            "max_action_bytes": config.max_action_bytes,
            "paired_preflight": compiled["length_stats"],
        }
    ):
        raise BoundaryError("reward_m0_inputs", "input_arm_source_binding_mismatch")
    rows = tuple(
        LightActionTokenRow(
            tuple(row["state"]),
            tuple(tuple(action) for action in row["actions"]),
            tuple(row["action_ids"]),
        )
        for row in compiled["rows"][config.arm]
    )
    return LoadedRewardM0Inputs(
        manifest, samples_for(source, config.arm), rows, tokenizer, config.arm, source, config
    )
