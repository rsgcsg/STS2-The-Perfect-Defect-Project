"""Synchronous M2 scoring over caller-bound observed text-menu pages.

The caller owns continuity and snapshot provenance. This module has no controller,
delivery, cancellation, native-action, or Human-evidence authority.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import torch
from tokenizers import Tokenizer

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes

from ..fullrun.memory_token_inputs import (
    MAX_TOKENIZER_BYTES,
    encode_memory_texts,
    project_memory_profile_snapshot,
)
from ..fullrun.text_menu_inputs import INPUT_PROFILE, V2_INPUT_PROFILE
from ..models.dsimple_memory import ExperimentalDSimpleM2
from ..workers.memory_ranking import MemoryConfig, load_memory_export

MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_RETIRED_CONTINUITIES = 1024
SNAPSHOT_FIELDS = frozenset({
    "protocol_version", "schema", "input_profile", "snapshot_id", "sequence",
    "observed_at", "status", "persistent", "interaction", "referents",
    "completeness", "session", "information_policy", "menu", "menu_actions",
})


@dataclass(frozen=True)
class OnlineScores:
    action_ids: tuple[str, ...]
    scores: tuple[float, ...]
    candidate_digest: str


class OnlineM2Scorer:
    """One accepted observation write, then read-only complete-catalog scores.

    A changed continuity token resets memory. Retired tokens cannot be revived.
    Repeating the exact current snapshot returns its cached scores without a write.
    """

    def __init__(self, model: ExperimentalDSimpleM2, tokenizer: Tokenizer,
                 config: MemoryConfig, *, input_profile: str = INPUT_PROFILE) -> None:
        if (not isinstance(model, ExperimentalDSimpleM2)
                or not isinstance(tokenizer, Tokenizer)
                or not isinstance(config, MemoryConfig)
                or tokenizer.get_vocab_size() != config.vocab_size
                or tokenizer.truncation is not None or tokenizer.padding is not None
                or not isinstance(input_profile, str)
                or input_profile not in {INPUT_PROFILE, V2_INPUT_PROFILE}):
            raise BoundaryError("online_m2", "model_tokenizer_config_mismatch")
        self._model = model.eval()
        self._tokenizer = tokenizer
        self._config = config
        self._input_profile = input_profile
        self._lock = Lock()
        self._memory = model.initial_memory()
        self._continuity: str | None = None
        self._retired: set[str] = set()
        self._session: tuple[str, str] | None = None
        self._snapshot_id: str | None = None
        self._sequence = 0
        self._snapshot_digest: str | None = None
        self._cached: OnlineScores | None = None

    @classmethod
    def from_export(cls, weights: bytes, config: MemoryConfig,
                    tokenizer_bytes: bytes, *,
                    input_profile: str = INPUT_PROFILE) -> OnlineM2Scorer:
        if (not isinstance(weights, bytes) or not isinstance(tokenizer_bytes, bytes)
                or not isinstance(config, MemoryConfig)):
            raise BoundaryError("online_m2", "export_bytes_required")
        if not 0 < len(tokenizer_bytes) <= MAX_TOKENIZER_BYTES:
            raise BoundaryError("online_m2", "tokenizer_size_limit")
        try:
            tokenizer = Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
        except Exception as error:
            raise BoundaryError("online_m2", "tokenizer_invalid") from error
        if (tokenizer.truncation is not None or tokenizer.padding is not None
                or tokenizer.get_vocab_size() != config.vocab_size):
            raise BoundaryError("online_m2", "model_tokenizer_config_mismatch")
        model = load_memory_export(
            weights, config, hashlib.sha256(tokenizer_bytes).hexdigest())
        return cls(model, tokenizer, config, input_profile=input_profile)

    @classmethod
    def from_package(cls, directory: Path, *,
                     input_profile: str = INPUT_PROFILE) -> OnlineM2Scorer:
        """Score detached bytes only under their validated, explicit profile."""
        from .memory_export import validate_memory_package

        _, weights, tokenizer, config = validate_memory_package(
            directory, input_profile=input_profile)
        return cls.from_export(weights, config, tokenizer, input_profile=input_profile)

    def observe_and_score(self, *, continuity_token: str,
                          snapshot_bytes: bytes, expected_candidate_digest: str | None = None,
                          expected_candidate_count: int | None = None) -> OnlineScores:
        if not self._lock.acquire(blocking=False):
            raise BoundaryError("online_m2", "concurrent_observation")
        try:
            return self._observe_and_score(continuity_token=continuity_token,
                                           snapshot_bytes=snapshot_bytes,
                                           expected_candidate_digest=expected_candidate_digest,
                                           expected_candidate_count=expected_candidate_count)
        finally:
            self._lock.release()

    def _observe_and_score(self, *, continuity_token: str,
                           snapshot_bytes: bytes, expected_candidate_digest: str | None,
                           expected_candidate_count: int | None) -> OnlineScores:
        if (not isinstance(continuity_token, str) or not continuity_token
                or not isinstance(snapshot_bytes, bytes)):
            raise BoundaryError("online_m2", "observation_identity_required")
        if not 0 < len(snapshot_bytes) <= MAX_SNAPSHOT_BYTES:
            raise BoundaryError("online_m2", "snapshot_size_limit")
        snapshot = decode_json(snapshot_bytes)
        if not isinstance(snapshot, dict):
            raise BoundaryError("online_m2", "snapshot_object_required")
        if set(snapshot) != SNAPSHOT_FIELDS:
            raise BoundaryError("online_m2", "snapshot_fields_mismatch")
        # Connector samples observed_at at each Observe, while TextMenuSession
        # excludes it from snapshot identity. Preserve every other field/order.
        identity = {key: value for key, value in snapshot.items() if key != "observed_at"}
        digest = hashlib.sha256(json_bytes(identity)).hexdigest()
        snapshot_id = snapshot.get("snapshot_id")
        sequence = snapshot.get("sequence")
        session = snapshot.get("session")
        if (not isinstance(snapshot_id, str) or not snapshot_id
                or type(sequence) is not int or sequence < 0
                or not isinstance(snapshot.get("observed_at"), str)
                or not snapshot["observed_at"]
                or not isinstance(session, dict)
                or set(session) != {"runtime_instance_id", "environment_fingerprint"}
                or not isinstance(session.get("runtime_instance_id"), str)
                or not session["runtime_instance_id"]
                or not isinstance(session.get("environment_fingerprint"), str)
                or not session["environment_fingerprint"]):
            raise BoundaryError("online_m2", "snapshot_identity_required")
        session_key = (session["runtime_instance_id"], session["environment_fingerprint"])
        if continuity_token in self._retired:
            raise BoundaryError("online_m2", "retired_continuity")
        changed = self._continuity is not None and continuity_token != self._continuity
        if changed and len(self._retired) >= MAX_RETIRED_CONTINUITIES:
            raise BoundaryError("online_m2", "continuity_limit")
        if not changed and self._continuity is not None:
            if session_key != self._session:
                raise BoundaryError("online_m2", "session_identity_changed")
            if snapshot_id == self._snapshot_id:
                if sequence != self._sequence or digest != self._snapshot_digest:
                    raise BoundaryError("online_m2", "snapshot_identity_reused")
                assert self._cached is not None
                if (expected_candidate_digest is not None
                        or expected_candidate_count is not None):
                    self._check_candidate_binding(self._cached, expected_candidate_digest,
                                                  expected_candidate_count)
                return self._cached
            if sequence <= self._sequence:
                raise BoundaryError("online_m2", "observation_order_reversed")

        # Catalog and per-page encoding limits precede any memory write. Training
        # episode budgets do not govern a live continuity or autonomy duration.
        # The projector renders object insertion order. Normalize that order so
        # equivalent JSON spellings produce the same model input on first read.
        public = project_memory_profile_snapshot(snapshot, self._input_profile)
        if (expected_candidate_digest is not None or expected_candidate_count is not None):
            self._check_candidate_binding(
                OnlineScores(public.action_ids, (), public.candidate_digest),
                expected_candidate_digest, expected_candidate_count)
        if len(public.action_ids) > self._config.max_actions_per_step:
            raise BoundaryError("online_m2", "catalog_limit_no_truncation")
        row = encode_memory_texts(
            self._tokenizer, public.state_text, public.action_texts,
            max_tokens=self._model.core.max_tokens, slots=self._model.slots)
        input_tokens = len(row.state) + sum(map(len, row.actions))
        if input_tokens > self._config.max_chunk_input_tokens:
            raise BoundaryError("online_m2", "observation_budget_no_truncation")
        device = self._model.write_queries.device
        page = torch.tensor(row.state, dtype=torch.long, device=device)
        actions = tuple(torch.tensor(ids, dtype=torch.long, device=device)
                        for ids in row.actions)
        old = self._model.initial_memory() if changed else self._memory
        with torch.inference_mode():
            # step preflights every action before advance; returned memory remains
            # provisional until the complete finite score vector is checked.
            values, proposed = self._model.step(page, actions, old)
            if (values.shape != (len(public.action_ids),)
                    or not bool(torch.isfinite(values).all())
                    or not bool(torch.isfinite(proposed).all())):
                raise BoundaryError("online_m2", "nonfinite_or_unbound_scores")
            scores = tuple(float(value) for value in values.cpu().tolist())
        if any(not math.isfinite(value) for value in scores):
            raise BoundaryError("online_m2", "nonfinite_or_unbound_scores")
        result = OnlineScores(public.action_ids, scores, public.candidate_digest)

        # The only state commit follows successful model computation. This is
        # observation acceptance, not native action delivery or causal success.
        if changed:
            assert self._continuity is not None
            self._retired.add(self._continuity)
        self._continuity = continuity_token
        self._memory = proposed
        self._session = session_key
        self._snapshot_id = snapshot_id
        self._sequence = sequence
        self._snapshot_digest = digest
        self._cached = result
        return result

    @staticmethod
    def _check_candidate_binding(result: OnlineScores, digest: str | None,
                                 count: int | None) -> None:
        if (not isinstance(digest, str) or len(digest) != 64
                or type(count) is not int or count != len(result.action_ids)
                or digest != result.candidate_digest):
            raise BoundaryError("online_m2", "candidate_binding_mismatch")
