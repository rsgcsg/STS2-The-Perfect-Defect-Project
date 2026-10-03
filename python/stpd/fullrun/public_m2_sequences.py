"""Public observation-only M2 inputs over explicitly verified evidence segments.

The caller owns source verification, use admission and public-view projection. This
module binds those products by exact transition identity; it does not manufacture
causal evidence, native actions or a complete-game claim. Segment endpoints are
observed pre / proved successor boundaries, not necessarily run start / terminal.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, cast

from tokenizers import Tokenizer

from spireagent.json_boundary import BoundaryError, digest, json_bytes

from ..canonical import semantic_hash
from ..light_action_codec import SPEC_SHA256, encode_action
from ..models.light_action_m2_training_data import LightActionM2TrainingStep
from .features import ModelSample
from .light_action_inputs import fit_state_bpe
from .public_inputs import COMPACT_IDENTITY, COMPACT_VERSION
from .token_inputs import input_texts

SCHEMA = "stpd/public-m2-sequence-input-v1"
SEQUENCE_PROFILE = "stpd/public-m2-observation-only-v1"
PRIOR_ACTION_PROFILE = "stpd/public-m2-no-prior-action-v1"
FEEDBACK_PROFILE = "stpd/public-m2-no-feedback-v1"
EDGE_PROFILE = "exact-proved-successor-next-pre-frame-v1"
MAX_ROWS = 100_000


@dataclass(frozen=True)
class PublicM2EvidenceRow:
    """Metadata copied from the verified source and exact selected public row."""

    transition_id: str
    source_archive_sha256: str
    session_id: str
    native_run_id: str
    action_sequence: int
    pre_frame_sha256: str
    successor_frame_sha256: str
    commit_ref: str
    proof_ref: str
    # A verifier-derived recording segment changes at pause/reload/gap boundaries.
    recording_segment_id: str
    # Copied from the verified public-view row, not recomputed from an arbitrary
    # caller replacement after verification. Binds full text, action order and label.
    public_sample_sha256: str

    def validate(self) -> None:
        for name in ("transition_id", "source_archive_sha256", "pre_frame_sha256",
                     "successor_frame_sha256", "commit_ref", "proof_ref",
                     "recording_segment_id", "public_sample_sha256"):
            digest(getattr(self, name), "public_m2." + name)
        if (not self.session_id or not self.native_run_id
                or not isinstance(self.session_id, str) or not isinstance(self.native_run_id, str)
                or type(self.action_sequence) is not int or self.action_sequence < 1
                or self.pre_frame_sha256 == self.successor_frame_sha256):
            raise BoundaryError("public_m2_sequence", "invalid_source_occurrence")

    @property
    def run_id(self) -> str:
        return self.session_id + "/" + self.native_run_id


@dataclass(frozen=True)
class PublicM2TextChain:
    chain_id: str
    split: str
    evidence: tuple[PublicM2EvidenceRow, ...]
    samples: tuple[ModelSample, ...]


@dataclass(frozen=True)
class PublicM2Chain:
    chain_id: str
    split: str
    evidence: tuple[PublicM2EvidenceRow, ...]
    steps: tuple[LightActionM2TrainingStep, ...]


@dataclass(frozen=True)
class PublicM2Input:
    """Immutable derived content; publishing/admission remain application owned."""

    source_binding_digest: str
    codec_fit_chain_ids: tuple[str, ...]
    codec_fit_transition_ids: tuple[str, ...]
    state_tokenizer: bytes
    chains: tuple[PublicM2Chain, ...]
    max_state_tokens: int
    max_action_bytes: int

    def content(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "sequence_profile": SEQUENCE_PROFILE,
            "prior_action_profile": PRIOR_ACTION_PROFILE, "feedback_profile": FEEDBACK_PROFILE,
            "edge_profile": EDGE_PROFILE, "renderer": COMPACT_IDENTITY,
            "source_binding_digest": self.source_binding_digest,
            "codec_fit_chain_ids": list(self.codec_fit_chain_ids),
            "codec_fit_transition_ids": list(self.codec_fit_transition_ids),
            "state_tokenizer_sha256": hashlib.sha256(self.state_tokenizer).hexdigest(),
            "action_codec_sha256": SPEC_SHA256,
            "max_state_tokens": self.max_state_tokens, "max_action_bytes": self.max_action_bytes,
            "reset_policy": "observed-segment-start-only",
            "end_policy": "last-selected-transition-proved-successor",
            "complete_game_claim": False, "physical_game_independence": "unresolved",
            "chains": [asdict(chain) for chain in self.chains],
        }

    @property
    def identity(self) -> str:
        return cast(str, semantic_hash(self.content()))

    def payload_bytes(self) -> bytes:
        return cast(bytes, json_bytes({**self.content(), "identity": self.identity}))


def _validate_sample(sample: ModelSample) -> None:
    if (not isinstance(sample, ModelSample) or sample.split not in {"train", "dev"}
            or not sample.state_text.startswith(
                f"[STPD_STATE version={COMPACT_VERSION} profile=public_compact]\n")
            or not sample.state_text.endswith("\n[/STPD_STATE]")
            or not sample.action_keys or len(set(sample.action_keys)) != len(sample.action_keys)
            or len(sample.action_texts) != len(sample.action_keys)
            or type(sample.chosen_index) is not int
            or not 0 <= sample.chosen_index < len(sample.action_keys)
            or any(not isinstance(key, str) or not key for key in sample.action_keys)
            or any(not text.startswith(f"[STPD_ACTION version={COMPACT_VERSION}]\n")
                   or not text.endswith("\n[/STPD_ACTION]") for text in sample.action_texts)):
        raise BoundaryError("public_m2_sequence", "complete_public_compact_sample_required")


def project_public_m2_chains(
    samples: tuple[ModelSample, ...], evidence: tuple[PublicM2EvidenceRow, ...],
) -> tuple[PublicM2TextChain, ...]:
    """Make maximal exact-edge segments without any length-induced reset.

    A gap or unequal edge starts an explicitly reset observed segment. Neither
    sorted ordinals nor matching visible text proves an edge. Recording segment
    identity must come from verified journal/trace boundaries at the caller.
    """
    if (not isinstance(samples, tuple) or not isinstance(evidence, tuple)
            or not 1 <= len(samples) <= MAX_ROWS or len(evidence) != len(samples)):
        raise BoundaryError("public_m2_sequence", "bounded_exact_membership_required")
    by_id: dict[str, ModelSample] = {}
    run_splits: dict[str, str] = {}
    for sample in samples:
        _validate_sample(sample)
        if sample.transition_id in by_id:
            raise BoundaryError("public_m2_sequence", "duplicate_sample")
        if run_splits.setdefault(sample.run_id, sample.split) != sample.split:
            raise BoundaryError("public_m2_sequence", "run_crosses_split")
        by_id[sample.transition_id] = sample
    groups: dict[tuple[str, str, str, str], list[PublicM2EvidenceRow]] = {}
    seen: set[str] = set()
    occurrences: set[tuple[str, str, int]] = set()
    for row in evidence:
        if not isinstance(row, PublicM2EvidenceRow):
            raise BoundaryError("public_m2_sequence", "typed_evidence_required")
        row.validate()
        sample = by_id.get(row.transition_id)
        occurrence = (row.session_id, row.native_run_id, row.action_sequence)
        if (row.transition_id in seen or occurrence in occurrences or sample is None
                or sample.run_id != row.run_id
                or semantic_hash(sample.to_dict()) != row.public_sample_sha256):
            raise BoundaryError("public_m2_sequence", "source_sample_binding_mismatch")
        seen.add(row.transition_id)
        occurrences.add(occurrence)
        key = (row.source_archive_sha256, row.session_id, row.native_run_id,
               row.recording_segment_id)
        groups.setdefault(key, []).append(row)
    if seen != set(by_id):
        raise BoundaryError("public_m2_sequence", "source_sample_membership_mismatch")
    chains: list[PublicM2TextChain] = []

    def emit(rows: list[PublicM2EvidenceRow]) -> None:
        if rows:
            split = by_id[rows[0].transition_id].split
            identity = semantic_hash({"profile": EDGE_PROFILE, "split": split,
                                      "evidence": [asdict(row) for row in rows]})
            chains.append(PublicM2TextChain(identity, split, tuple(rows), tuple(
                by_id[row.transition_id] for row in rows)))

    for key in sorted(groups):
        segment: list[PublicM2EvidenceRow] = []
        for row in sorted(groups[key], key=lambda item: item.action_sequence):
            if segment and (segment[-1].action_sequence + 1 != row.action_sequence
                            or segment[-1].successor_frame_sha256 != row.pre_frame_sha256):
                emit(segment)
                segment = []
            segment.append(row)
        emit(segment)
    return tuple(chains)


def nested_train_chains(
    chains: tuple[PublicM2TextChain, ...], *, targets: tuple[int, ...] = (1000, 2000, 3000),
    seed: int = 1701,
) -> tuple[tuple[PublicM2TextChain, ...], ...]:
    """Stable whole-chain prefixes nearest the requested counts; never cut a chain."""
    if (not targets or any(type(n) is not int or n < 1 for n in targets)
            or tuple(sorted(set(targets))) != targets or type(seed) is not int or seed < 0):
        raise BoundaryError("public_m2_sequence", "invalid_nested_selection")
    ordered = tuple(sorted((chain for chain in chains if chain.split == "train"),
                           key=lambda chain: semantic_hash([seed, chain.chain_id])))
    if not ordered or len({chain.chain_id for chain in chains}) != len(chains):
        raise BoundaryError("public_m2_sequence", "empty_or_duplicate_chains")
    totals: list[int] = []
    count = 0
    for chain in ordered:
        count += len(chain.samples)
        totals.append(count)
    if count < targets[-1]:
        raise BoundaryError("public_m2_sequence", "insufficient_train_labels")
    return tuple(ordered[:min(range(len(totals)), key=lambda i: (abs(totals[i] - n), i)) + 1]
                 for n in targets)


def compile_public_m2_input(
    chains: tuple[PublicM2TextChain, ...], codec_fit_chains: tuple[PublicM2TextChain, ...],
    *, source_binding_digest: str, max_state_tokens: int, max_action_bytes: int,
    state_tokenizer: bytes | None = None,
) -> PublicM2Input:
    """Fit once on the named train subset, reuse its exact BPE for every arm/dev."""
    digest(source_binding_digest, "public_m2.source_binding_digest")
    if (not chains or not codec_fit_chains
            or any(type(n) is not int or not 1 <= n <= 1_000_000
                   for n in (max_state_tokens, max_action_bytes))):
        raise BoundaryError("public_m2_sequence", "invalid_compile_limits")
    samples = tuple(sample for chain in chains for sample in chain.samples)
    evidence = tuple(row for chain in chains for row in chain.evidence)
    # Reconstruct every chain rather than trusting caller-mutated membership/IDs.
    rebuilt = project_public_m2_chains(samples, evidence)
    if {chain.chain_id: chain for chain in rebuilt} != {chain.chain_id: chain for chain in chains}:
        raise BoundaryError("public_m2_sequence", "chain_identity_mismatch")
    by_id = {chain.chain_id: chain for chain in chains}
    if (len(by_id) != len(chains)
            or len({c.chain_id for c in codec_fit_chains}) != len(codec_fit_chains)
            or any(chain.split != "train" or by_id.get(chain.chain_id) != chain
                   for chain in codec_fit_chains)):
        raise BoundaryError("public_m2_sequence", "codec_fit_must_be_exact_train_subset")
    fit_samples = tuple(sample for chain in codec_fit_chains for sample in chain.samples)
    fitted = fit_state_bpe(fit_samples)
    if state_tokenizer is not None and state_tokenizer != fitted:
        raise BoundaryError("public_m2_sequence", "shared_codec_fit_identity_mismatch")
    raw = fitted if state_tokenizer is None else state_tokenizer
    tokenizer = Tokenizer.from_str(raw.decode("utf-8"))
    if tokenizer.truncation is not None or tokenizer.padding is not None:
        raise BoundaryError("public_m2_sequence", "padding_or_truncation_forbidden")
    compiled: list[PublicM2Chain] = []
    for chain in chains:
        steps: list[LightActionM2TrainingStep] = []
        for index, sample in enumerate(chain.samples):
            text, _ = input_texts(sample.state_text, ())
            tokens = tuple(tokenizer.encode(text, add_special_tokens=False).ids)
            if not tokens or len(tokens) > max_state_tokens:
                raise BoundaryError("public_m2_sequence", "state_limit_no_truncation")
            steps.append(LightActionM2TrainingStep(
                index, tokens, sample.action_keys,
                tuple(encode_action(action, max_bytes=max_action_bytes)
                      for action in sample.action_texts),
                sample.action_keys[sample.chosen_index], None, index == 0,
            ))
        compiled.append(PublicM2Chain(chain.chain_id, chain.split, chain.evidence, tuple(steps)))
    return PublicM2Input(source_binding_digest, tuple(c.chain_id for c in codec_fit_chains),
                         tuple(s.transition_id for s in fit_samples), raw, tuple(compiled),
                         max_state_tokens, max_action_bytes)
