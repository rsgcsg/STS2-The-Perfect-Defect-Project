"""Synthetic evidence-boundary fixtures; no source or research-use admission."""

from dataclasses import replace

import pytest

from spireagent.json_boundary import BoundaryError
from stpd.canonical import semantic_hash
from stpd.fullrun.features import ModelSample
from stpd.fullrun.public_inputs import COMPACT_VERSION
from stpd.fullrun.public_m2_sequences import (
    PublicM2EvidenceRow,
    compile_public_m2_input,
    nested_train_chains,
    project_public_m2_chains,
)


def fixture(count=3, *, run="one", split="train"):
    rows, samples = [], []
    for i in range(count):
        identity = semantic_hash([run, i])
        rows.append(PublicM2EvidenceRow(
            identity, semantic_hash(["archive", run]), "session", run, i + 1,
            semantic_hash(["frame", run, i]), semantic_hash(["frame", run, i + 1]),
            semantic_hash(["commit", run, i]), semantic_hash(["proof", run, i]),
            semantic_hash(["recording-segment", run]),
        ))
        samples.append(ModelSample(
            identity, "session/" + run, split, "fixture", "fixture",
            f'[STPD_STATE version={COMPACT_VERSION} profile=public_compact]\n'
            '{"visible":"same page on a later decision"}\n[/STPD_STATE]',
            tuple(f'[STPD_ACTION version={COMPACT_VERSION}]\n'
                  + f'{{"verb":"{verb}"}}\n[/STPD_ACTION]' for verb in ("select", "cancel")),
            ("b", "a"), i % 2,
        ))
    return tuple(samples), tuple(rows)


def test_long_chain_keeps_all_65_steps_and_same_page_occurrences():
    samples, rows = fixture(65)
    chains = project_public_m2_chains(samples, rows)
    assert len(chains) == 1 and len(chains[0].samples) == 65
    result = compile_public_m2_input(chains, chains, source_binding_digest="a" * 64,
                                    max_state_tokens=1000, max_action_bytes=1000)
    assert len(result.chains[0].steps) == 65
    assert sum(step.reset_before for step in result.chains[0].steps) == 1
    assert all(step.previous_actual_action is None for step in result.chains[0].steps)
    assert all(step.action_ids == ("b", "a") for step in result.chains[0].steps)
    assert result.chains[0].steps[64].position == 64


@pytest.mark.parametrize("kind", ["edge", "ordinal", "journal"])
def test_numeric_adjacency_alone_does_not_prove_carry(kind):
    samples, rows = fixture(3)
    if kind == "edge":
        rows = (rows[0], replace(rows[1], pre_frame_sha256="f" * 64), rows[2])
    elif kind == "ordinal":
        rows = (rows[0], replace(rows[1], action_sequence=3),
                replace(rows[2], action_sequence=4))
    else:
        rows = (rows[0], *(replace(row, recording_segment_id="e" * 64) for row in rows[1:]))
    chains = project_public_m2_chains(samples, rows)
    assert sorted(len(chain.samples) for chain in chains) == [1, 2]


def test_membership_duplicates_split_and_native_text_fail_closed():
    samples, rows = fixture(3)
    with pytest.raises(BoundaryError, match="bounded_exact_membership"):
        project_public_m2_chains(samples, rows[:-1])
    with pytest.raises(BoundaryError, match="duplicate_sample"):
        project_public_m2_chains((samples[0], samples[0], samples[2]), rows)
    with pytest.raises(BoundaryError, match="run_crosses_split"):
        project_public_m2_chains((replace(samples[0], split="dev"), *samples[1:]), rows)
    with pytest.raises(BoundaryError, match="complete_public_compact"):
        project_public_m2_chains(
            (replace(samples[0], state_text="native text"), *samples[1:]), rows)
    with pytest.raises(BoundaryError, match="source_sample_binding"):
        project_public_m2_chains(samples, (replace(rows[0], transition_id="b" * 64), *rows[1:]))


def test_nested_whole_chain_selection_is_stable_and_never_splits():
    batches = [fixture(size, run=str(i)) for i, size in enumerate((2, 3, 4, 5))]
    samples = tuple(sample for batch, _ in batches for sample in batch)
    rows = tuple(row for _, batch in batches for row in batch)
    chains = project_public_m2_chains(samples, rows)
    selected = nested_train_chains(chains, targets=(3, 7, 12))
    assert selected == nested_train_chains(tuple(reversed(chains)), targets=(3, 7, 12))
    assert all(set(a).issubset(b) for a, b in zip(selected, selected[1:], strict=False))
    assert all(chain in chains for subset in selected for chain in subset)
    with pytest.raises(BoundaryError, match="insufficient_train_labels"):
        nested_train_chains(chains, targets=(15,))


def test_fixed_train_codec_and_dev_text_does_not_change_fit():
    train_samples, train_rows = fixture(3)
    dev_samples, dev_rows = fixture(2, run="dev", split="dev")
    train = project_public_m2_chains(train_samples, train_rows)
    chains = project_public_m2_chains(train_samples + dev_samples, train_rows + dev_rows)
    common = dict(source_binding_digest="a" * 64, max_state_tokens=1000, max_action_bytes=1000)
    first = compile_public_m2_input(chains, train, **common)
    modified = tuple(replace(sample, state_text=sample.state_text.replace(
        "same page", "秘密字符新状态")) for sample in dev_samples)
    changed = project_public_m2_chains(train_samples + modified, train_rows + dev_rows)
    second = compile_public_m2_input(
        changed, train, state_tokenizer=first.state_tokenizer, **common)
    assert first.state_tokenizer == second.state_tokenizer
    assert first.identity != second.identity
    assert first.codec_fit_transition_ids == tuple(sample.transition_id for sample in train_samples)
    with pytest.raises(BoundaryError, match="codec_fit_must_be_exact_train_subset"):
        compile_public_m2_input(chains, tuple(c for c in chains if c.split == "dev"), **common)
    with pytest.raises(BoundaryError, match="shared_codec_fit_identity_mismatch"):
        compile_public_m2_input(chains, train, state_tokenizer=b"{}", **common)


def test_rebound_chain_and_truncation_are_rejected():
    samples, rows = fixture(3)
    chains = project_public_m2_chains(samples, rows)
    common = dict(source_binding_digest="a" * 64, max_state_tokens=1000, max_action_bytes=1000)
    with pytest.raises(BoundaryError, match="chain_identity_mismatch"):
        compile_public_m2_input((replace(chains[0], chain_id="e" * 64),), chains, **common)
    with pytest.raises(BoundaryError, match="state_limit_no_truncation"):
        compile_public_m2_input(chains, chains, **{**common, "max_state_tokens": 1})
