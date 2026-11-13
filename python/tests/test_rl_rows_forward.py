"""S3 rows-v2: the padding-free RL training forward against the padded original.

CPU fp32: outputs and every parameter gradient equal padded-v1 within float
accumulation tolerance, for multi-bucket layouts, varied scene/text lengths,
policy and non-policy groups. GPU BF16 step time and tolerance are Friday facts.
"""
from __future__ import annotations

import copy
import dataclasses as dc
import itertools
import os

import pytest
import test_rl41_w1 as w1
import torch
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_rl41_w1 import model as model  # noqa: F401  (fixture)

from spireagent.json_boundary import BoundaryError
from stpd.rl.batch_preparation import assemble, pack_transport, row_layout, unpack_transport
from stpd.rl.loss import LossConfig, prepared_update
from stpd.rl.performance import PerformanceConfig

EXTRA = ("STRIKE_SILENT", "STRIKE_REGENT", "STRIKE_NECROBINDER", "BASH")


@pytest.fixture
def varied(monkeypatch):
    """Rows with 0-4 extra deck cards: different scene lengths and text sets."""
    counter = itertools.count()
    original = w1.public_state

    def state():
        value = original()
        extra = next(counter) % 5
        value["deck"]["content"]["cards"].extend(
            {"entity_id": f"x{i}", "definition_id": EXTRA[i % 4], "cost": i}
            for i in range(extra))
        return value

    monkeypatch.setattr(w1, "public_state", state)


def prepared(model, *layouts, microbatch_size=8):
    """The same segments assembled once per layout (default: padded-v1 only)."""
    values = [w1.segment(model, name=f"actor/{i}", run_id=f"episode-{i}", rows=5)
              for i in range(2)]
    batches = [assemble(w1.request(values), tuple(w1.source(model, v) for v in values),
                        microbatch_size=microbatch_size, deduplicate_text=True,
                        prepared_layout=layout) for layout in layouts or ("padded-v1",)]
    return batches if len(batches) > 1 else batches[0]


def forward_and_gradients(model, group, *, packed=None, kernels=""):
    model.zero_grad(set_to_none=True)
    if packed is None:
        scores, logits, hp = model.forward_prepared_rl4(group, kernels=kernels)
    else:
        from stpd.p1.rows_forward import forward_rows

        scores, logits, hp = forward_rows(model, group, packed=packed, kernels=kernels)
    generator = torch.Generator().manual_seed(5)
    total = hp.mul(torch.randn(hp.shape, generator=generator)).sum()
    for value in (scores, logits):
        weights = torch.randn(value.shape, generator=generator)
        total = total + value.masked_fill(~value.isfinite(), 0).mul(weights).sum()
    total.backward()
    gradients = {name: p.grad.clone() for name, p in model.named_parameters()
                 if p.grad is not None}
    return (scores.detach(), logits.detach(), hp.detach()), gradients


def assert_same_function(model, padded, rows, *, packed=None):
    (s0, l0, h0), g0 = forward_and_gradients(model, padded)
    (s1, l1, h1), g1 = forward_and_gradients(model, rows, packed=packed)
    for left, right in ((s0, s1), (l0, l1), (h0, h1)):
        assert left.shape == right.shape
        assert torch.equal(left.isneginf(), right.isneginf())
        finite = left.isfinite()
        torch.testing.assert_close(left[finite], right[finite], rtol=1e-5, atol=2e-6)
    assert set(g0) == set(g1)
    for name in g0:
        torch.testing.assert_close(g0[name], g1[name], rtol=1e-4, atol=2e-6, msg=name)


@pytest.mark.parametrize("bucket_rows,width_multiple", [(256, 64), (3, 8), (1, 1)])
def test_rows_forward_equals_padded_forward_on_cpu(model, varied, bucket_rows, width_multiple):
    batch = prepared(model)
    groups = [g for mb in batch.minibatches for g in mb.groups]
    assert groups and all(g.layout is None for g in groups)
    lengths = {int(n) for g in groups for n in (~g.tensors["scene_mask"][:len(g.rows)]).sum(1)}
    assert len(lengths) > 1  # the fixture really varies scene length
    for group in groups:
        layout = row_layout(group, bucket_rows=bucket_rows, width_multiple=width_multiple)
        if bucket_rows == 1:
            assert len(layout.scene_buckets) == len(group.rows)
        assert sum(b[3] for b in layout.scene_buckets) == int((~group.tensors["scene_mask"][
            :len(group.rows)]).sum())
        assert_same_function(model, group, dc.replace(group, layout=layout))
        plain = dc.replace(group, policy=False)
        assert_same_function(model, plain, dc.replace(plain, layout=row_layout(
            plain, bucket_rows=bucket_rows, width_multiple=width_multiple)))


@pytest.mark.parametrize("bucket_rows,width_multiple", [(256, 64), (3, 8), (1, 1)])
def test_packed_rows_forward_equals_padded_forward_on_cpu(model, varied, bucket_rows,
                                                          width_multiple):
    """C-SCALE step 2: the packed path (every attention over real tokens only:
    packed text, scene, both heads, cross-attention) is the same function. On CPU
    it runs the reference attention one sequence at a time; CUDA runs flash."""
    batch = prepared(model)
    groups = [g for mb in batch.minibatches for g in mb.groups]
    for group in groups:
        layout = row_layout(group, bucket_rows=bucket_rows, width_multiple=width_multiple)
        texts = len(layout.text_order)
        assert layout.text_offsets is not None and len(layout.text_offsets) == texts + 1
        lengths = group.tensors["text_lengths"][:texts].long()
        assert int(layout.text_offsets[-1]) == int(lengths.sum())
        assert layout.text_longest == int(lengths.max())
        assert_same_function(model, group, dc.replace(group, layout=layout), packed=True)
        plain = dc.replace(group, policy=False)
        assert_same_function(model, plain, dc.replace(plain, layout=row_layout(
            plain, bucket_rows=bucket_rows, width_multiple=width_multiple)), packed=True)


def test_packed_forward_reads_a_layout_without_packed_text(model, varied):
    """Layouts written before step 2 (offline dumps) carry no packed text: the text
    encoder then runs bucketed, the scene/heads/cross still packed."""
    group = prepared(model).minibatches[0].groups[0]
    layout = dc.replace(row_layout(group), text_tokens=None, text_offsets=None,
                        text_longest=0)
    assert_same_function(model, group, dc.replace(group, layout=layout), packed=True)


def test_preparation_builds_the_same_layout_and_it_survives_transport(model, varied):
    padded, rows = prepared(model, "padded-v1", "rows-v2")
    for mb0, mb1 in zip(padded.minibatches, rows.minibatches, strict=True):
        for g0, g1 in zip(mb0.groups, mb1.groups, strict=True):
            assert g1.layout is not None
            expected = row_layout(g0)
            moved = unpack_transport(pack_transport(g1))
            for layout in (g1.layout, moved.layout):
                for field in dc.fields(expected):
                    left, right = getattr(expected, field.name), getattr(layout, field.name)
                    assert repr(left) == repr(right), field.name
            assert_same_function(model, g0, moved)


def test_prepared_update_refuses_a_layout_that_does_not_match_the_selection(model, varied):
    batch, rows = prepared(model, "padded-v1", "rows-v2")
    optimizer = torch.optim.AdamW(model.parameters())
    config = LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01)
    for selected, minibatch in (("rows-v2", batch.minibatches[0]),
                                ("padded-v1", rows.minibatches[0])):
        with pytest.raises(BoundaryError, match="prepared_layout_does_not_match"):
            prepared_update(model, optimizer, minibatch, config=config,
                            performance=PerformanceConfig(prepared_forward=selected))


def test_one_rows_v2_optimizer_step_matches_padded_v1_on_cpu(model, varied):
    padded, rows = prepared(model, "padded-v1", "rows-v2")
    config = LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01)
    results, weights = [], []
    for selected, batch in (("padded-v1", padded), ("rows-v2", rows)):
        trained = copy.deepcopy(model)
        optimizer = torch.optim.AdamW(trained.parameters(), lr=1e-3)
        result = prepared_update(trained, optimizer, batch.minibatches[0], config=config,
                                 performance=PerformanceConfig(prepared_forward=selected),
                                 target_kl=1e9)
        assert result["stepped"]
        results.append(result)
        weights.append(trained.state_dict())
    for key in ("td_loss", "policy_loss", "terminal_loss", "hp_loss", "approx_kl_mu_theta",
                "gradient_norm"):
        assert results[0][key] == pytest.approx(results[1][key], rel=1e-5, abs=1e-7), key
    for name, value in weights[0].items():
        torch.testing.assert_close(value, weights[1][name], rtol=1e-4, atol=1e-6, msg=name)


def test_update_reports_packed_when_the_device_selects_flash_varlen(model, varied,
                                                                   monkeypatch):
    """The step report names the attention path: "packed" (every attention on flash
    varlen, C-SCALE step 2) where the device supports it, else "masked". The CPU
    reference of flash_varlen stands in for the CUDA kernel here."""
    import stpd.p1.rows_forward as rows_forward

    padded, rows = prepared(model, "padded-v1", "rows-v2")
    config = LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01)
    monkeypatch.setattr(rows_forward, "varlen_supported", lambda device: True)
    trained = copy.deepcopy(model)
    result = prepared_update(trained, torch.optim.AdamW(trained.parameters(), lr=1e-3),
                             rows.minibatches[0], config=config, target_kl=1e9,
                             performance=PerformanceConfig(prepared_forward="rows-v2"))
    expected = prepared_update(copy.deepcopy(model), torch.optim.AdamW(
        copy.deepcopy(model).parameters(), lr=1e-3), padded.minibatches[0], config=config,
        target_kl=1e9, performance=PerformanceConfig())
    assert result["rows_forward"] == {"path": "packed", "compiled": False}
    assert result["policy_loss"] == pytest.approx(expected["policy_loss"], rel=1e-4,
                                                  abs=1e-6)


def test_selection_is_explicit_and_compile_needs_rows_v2():
    assert PerformanceConfig().prepared_forward == "padded-v1"
    with pytest.raises(BoundaryError, match="invalid_prepared_forward"):
        PerformanceConfig(prepared_forward="rows")
    with pytest.raises(BoundaryError, match="prepared_compile_requires_rows_v2"):
        PerformanceConfig(prepared_compile=True)
    with pytest.raises(ValueError, match="unsupported_prepared_layout"):
        assemble(None, (), prepared_layout="rows")  # type: ignore[arg-type]


def test_rows_forward_refuses_training_mode(model, varied):
    group = prepared(model, "rows-v2").minibatches[0].groups[0]
    model.train()
    try:
        with pytest.raises(ValueError, match="rows_forward_requires_eval_mode"):
            model.forward_prepared_rl4(group)
    finally:
        model.eval()


@pytest.mark.skipif(os.name != "posix", reason="real spawned SGD process")
def test_rows_v2_trains_in_the_sgd_process_close_to_padded_in_process(tmp_path, monkeypatch):
    """Real socket/WAL/preparation workers and SGD child: the selection reaches the
    workers, the layout survives the binary hand-off into the child, and 2 datasets
    train like padded-v1 in-process. Each update reports the attention path."""
    import test_rl_sgd_process as split

    original = split.configuration
    trained = {}
    for layout, sgd in (("padded-v1", False), ("rows-v2", True)):
        def configured(path, patch, _layout=layout, **kwargs):
            config, io = original(path, patch, **kwargs)
            return dc.replace(config, performance=dc.replace(
                config.performance, prepared_forward=_layout)), io

        monkeypatch.setattr(split, "configuration", configured)
        owner, _, _ = split.terminal_owner(tmp_path / layout, monkeypatch, sgd=sgd)
        try:
            events = split.train(owner, 2)
            assert owner.updates == 4
            facts = [event.get("rows_forward") for event in events]
            assert facts == ([{"path": "masked", "compiled": False}] * 4 if sgd
                             else [None] * 4)  # CPU: masked; varlen is CUDA sm80+ BF16
            owner.checkpoint(final=True)
            trained[layout] = split.read_cp(owner)[1].weights
        finally:
            owner.close()
    for name, value in trained["padded-v1"].items():
        torch.testing.assert_close(value, trained["rows-v2"][name], rtol=1e-4, atol=1e-5,
                                   msg=name)


@pytest.mark.parametrize("compile_", [False, True])
def test_rows_v2_without_the_sgd_process_is_a_typed_config_error(tmp_path, monkeypatch,
                                                                  compile_):
    import test_rl_sgd_process as split

    original = split.configuration

    def configured(path, patch, **kwargs):
        config, io = original(path, patch, **kwargs)
        return dc.replace(config, performance=dc.replace(
            config.performance, prepared_forward="rows-v2", prepared_compile=compile_)), io

    monkeypatch.setattr(split, "configuration", configured)
    with pytest.raises(BoundaryError, match="rows_v2_requires_sgd_process"):
        split.terminal_owner(tmp_path, monkeypatch, sgd=False, episodes=0)


@pytest.fixture
def bare(monkeypatch):
    """No deck, no playable cards (so no role terms) and an empty catalog label."""
    original = w1.public_state

    def state():
        value = original()
        value["deck"]["content"]["cards"] = []
        value["scene"]["content"]["surface"] = {"playable_cards": []}
        value["catalog"][0]["label"] = ""
        return value

    monkeypatch.setattr(w1, "public_state", state)


def test_rows_forward_handles_empty_text_and_absent_roles(model, bare):
    """Card edge cases: an empty text string and candidates with no role terms.
    (A group with no text at all is not producible: kind/field texts are always
    present in a public frame.)"""
    batch = prepared(model)
    groups = [g for mb in batch.minibatches for g in mb.groups]
    for group in groups:
        layout = row_layout(group)
        assert len(layout.roles["target"][0]) == 0  # no target terms anywhere
        assert not bool(layout.role_present["target"].any())
        assert not bool(layout.role_present["source"].all())
        assert_same_function(model, group, dc.replace(group, layout=layout))


def test_effective_config_records_rows_v2_only_when_selected_and_resume_ignores_it():
    from stpd.rl.learner import LearnerConfig
    from stpd.rl.segment_learner import _resume_config
    from stpd.rl.segments import SegmentConfig

    base = LearnerConfig(run="rows-unit", peer={"kind": "local", "directory": "/nonexistent"},
                         performance=PerformanceConfig(), segments=SegmentConfig())
    selected = dc.replace(base, performance=PerformanceConfig(prepared_forward="rows-v2"))
    assert "prepared_forward" not in base.frozen()["performance"]
    assert selected.frozen()["performance"]["prepared_forward"] == "rows-v2"
    assert "prepared_compile" not in selected.frozen()["performance"]
    # The binary recipe digest and the CP resume comparison see the same objectives.
    assert _resume_config(dc.asdict(base)) == _resume_config(dc.asdict(selected))


def test_compiled_rows_forward_trains_and_diagnostic_steps_run_eager(model, varied):
    """A compiled backward refuses retain_graph (per-head diagnostics); those steps
    run the same block eager, every other step compiled."""
    padded, rows = prepared(model, "padded-v1", "rows-v2")
    config = LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01)
    reference = copy.deepcopy(model)
    eager_optimizer = torch.optim.AdamW(reference.parameters(), lr=1e-3)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    for diagnostics in (True, False):
        result = prepared_update(
            model, optimizer, rows.minibatches[0], config=config, target_kl=1e9,
            performance=PerformanceConfig(prepared_forward="rows-v2", prepared_compile=True),
            diagnostics=diagnostics)
        expected = prepared_update(reference, eager_optimizer, padded.minibatches[0],
                                   config=config, target_kl=1e9,
                                   performance=PerformanceConfig(), diagnostics=diagnostics)
        assert result["stepped"] and ("trunk_gradients" in result) == diagnostics
        facts = result["rows_forward"]
        assert facts["path"] == "masked" and facts["compiled"] is not diagnostics
        assert facts["dynamo_frames_ok"] >= (0 if diagnostics else 1)
        assert result["policy_loss"] == pytest.approx(expected["policy_loss"], rel=1e-4,
                                                      abs=1e-6)
    for name, value in reference.state_dict().items():
        torch.testing.assert_close(model.state_dict()[name], value, rtol=1e-4, atol=1e-5,
                                   msg=name)


def _equal_structure(left, right):
    """Bitwise: same tensors (dtype, shape, values) through dataclasses and containers."""
    if isinstance(left, torch.Tensor):
        assert isinstance(right, torch.Tensor) and left.dtype == right.dtype
        assert left.shape == right.shape and torch.equal(left, right)
    elif dc.is_dataclass(left):
        assert type(left) is type(right)
        for item in dc.fields(left):
            _equal_structure(getattr(left, item.name), getattr(right, item.name))
    elif isinstance(left, dict):
        assert list(left) == list(right)
        for key in left:
            _equal_structure(left[key], right[key])
    elif isinstance(left, (tuple, list)):
        assert len(left) == len(right)
        for a, b in zip(left, right, strict=True):
            _equal_structure(a, b)
    else:
        assert left == right


def _frames(model, count=3):
    sources = [w1.source(model, w1.segment(model, name=f"actor/{i}", run_id=f"episode-{i}",
                                           rows=5)) for i in range(count)]
    frames = [frame for source in sources for frame in source.frames]
    floors = [floor for source in sources for floor in source.floors]
    return frames, floors


@pytest.mark.parametrize("policy", [True, False])
@pytest.mark.parametrize("layout", ["rows-v2", "padded-v1"])
def test_concatenated_subgroups_equal_one_prepared_group_bitwise(model, varied, policy, layout):
    """Lead 11-09 (a): merging prepared parts is the one-shot group, bit for bit."""
    from stpd.rl.batch_preparation import concatenate_groups, prepare_group

    frames, floors = _frames(model)
    cuts = [(0, 1), (1, 7), (7, 15)]  # uneven parts: widths, catalogs, row buckets differ
    parts = [prepare_group(frames[a:b], tuple(range(a, b)), policy,
                           torch.tensor(floors[a:b]), deduplicate_text=False, layout=layout)
             for a, b in cuts]
    whole = prepare_group(frames, tuple(range(len(frames))), policy, torch.tensor(floors),
                          deduplicate_text=False, layout=layout)
    merged = concatenate_groups(parts)
    _equal_structure(merged, whole)
    model.eval()
    with torch.no_grad():
        for left, right in zip(model.forward_prepared_rl4(merged),
                               model.forward_prepared_rl4(whole), strict=True):
            assert torch.equal(left, right)


def test_concatenated_deduplicated_subgroups_score_like_their_parts(model, varied):
    """rl-inference-4 parts (`rows_scorer.prepare`, deduplicated per part): one merged
    forward gives each part's own outputs bit for bit (CPU fp32 eager)."""
    from stpd.rl.batch_preparation import concatenate_groups, prepare_group

    frames, floors = _frames(model)
    cuts = [(0, 4), (4, 9), (9, 15)]
    parts = [prepare_group(frames[a:b], tuple(range(b - a)), True, torch.tensor(floors[a:b]),
                           deduplicate_text=True, layout="rows-v2") for a, b in cuts]
    merged = concatenate_groups(parts, rows=tuple(range(len(frames))))
    assert merged.rows == tuple(range(len(frames)))
    model.eval()
    with torch.no_grad():
        together = model.forward_prepared_rl4(merged)
        alone = [model.forward_prepared_rl4(part) for part in parts]
    for index, outputs in enumerate(zip(*alone, strict=True)):
        joined = together[index]
        width = joined.shape[1] if joined.ndim > 1 else None
        expected = torch.cat([value if width is None else torch.nn.functional.pad(
            value, (0, width - value.shape[1]), value=-torch.inf) for value in outputs])
        if index == 0:  # scores: catalog padding of a narrower part is -inf in both
            assert torch.equal(joined.isneginf(), expected.isneginf())
            finite = joined.isfinite()
            assert torch.equal(joined[finite], expected[finite])
        else:
            assert torch.equal(joined, expected)


def test_concatenate_refuses_mixed_parts(model, varied):
    from stpd.rl.batch_preparation import concatenate_groups, prepare_group

    frames, floors = _frames(model, count=1)
    a = prepare_group(frames[:2], (0, 1), True, torch.tensor(floors[:2]),
                      deduplicate_text=True, layout="rows-v2")
    b = prepare_group(frames[2:], (2, 3, 4), False, torch.tensor(floors[2:]),
                      deduplicate_text=True, layout="rows-v2")
    with pytest.raises(ValueError, match="one_policy_and_layout"):
        concatenate_groups([a, b])
    with pytest.raises(ValueError, match="rows_mismatch"):
        concatenate_groups([a], rows=(0,))


def _bitwise(left, right):
    assert left.shape == right.shape and left.dtype == right.dtype
    assert torch.equal(left.isneginf(), right.isneginf())
    finite = left.isfinite()
    assert torch.equal(left[finite], right[finite])


@pytest.mark.parametrize("index_dtype", [torch.int32, torch.int64])
@pytest.mark.parametrize("packed", [False, True])
def test_forward_inputs_are_the_same_function_bitwise(model, varied, index_dtype, packed):
    """C-SCALE step 3: only what the rows-v2 forward reads, int32 indices: same
    outputs and gradients bit for bit (CPU fp32), policy and non-policy."""
    from stpd.rl.batch_preparation import forward_inputs, is_forward_inputs

    group = prepared(model, "rows-v2").minibatches[0].groups[0]
    for value in (group, dc.replace(group, policy=False, layout=row_layout(
            dc.replace(group, policy=False)))):
        inputs = forward_inputs(value, index_dtype=index_dtype)
        assert is_forward_inputs(inputs) and forward_inputs(inputs) is inputs
        assert "scene_gather" not in inputs.tensors and not inputs.joint.reductions
        if index_dtype == torch.int32:
            assert inputs.layout.scene_nodes.dtype == torch.int32
            assert inputs.joint.tensors["identities"].dtype == torch.int32
        (s0, l0, h0), g0 = forward_and_gradients(model, value, packed=packed)
        (s1, l1, h1), g1 = forward_and_gradients(model, inputs, packed=packed)
        for left, right in ((s0, s1), (l0, l1), (h0, h1)):
            _bitwise(left, right)
        assert set(g0) == set(g1) and all(torch.equal(g0[k], g1[k]) for k in g0)


@pytest.mark.parametrize("policy", [True, False])
@pytest.mark.parametrize("deduplicate", [True, False])
@pytest.mark.parametrize("repeat", [1, 18])  # 18: 270 rows, two joined scene buckets
def test_arena_parts_join_like_concatenate_groups_bitwise(model, varied, policy, deduplicate,
                                                          repeat):
    """Lead 11-09 step 3 (2): parts written into one shared arena and joined
    (planning on host views) equal `forward_inputs(concatenate_groups(...))`."""
    import pickle

    from stpd.rl.batch_preparation import (
        ROW_BUCKET_ROWS,
        allocate_forward_arena,
        concatenate_groups,
        forward_arena_bytes,
        forward_inputs,
        plan_forward_join,
        prepare_group,
        stage_forward_parts,
        stage_joined,
        write_forward_inputs,
    )

    frames, floors = _frames(model)
    frames, floors = frames * repeat, floors * repeat
    step = max(1, len(frames) // 4)
    cuts = [(0, 1), (1, 7), (7, step + 7), (step + 7, len(frames))]
    parts = [prepare_group(frames[a:b], tuple(range(b - a)), policy, torch.tensor(floors[a:b]),
                           deduplicate_text=deduplicate, layout="rows-v2") for a, b in cuts]
    rows = tuple(range(len(frames)))
    expected = forward_inputs(concatenate_groups(parts, rows=rows))
    if repeat > 1:
        assert len(expected.layout.scene_buckets) == -(-len(frames) // ROW_BUCKET_ROWS) > 1
    sizes = [forward_arena_bytes(part) for part in parts]
    arena = allocate_forward_arena(sum(sizes) + 64 * len(parts) + 100, shared=True)
    written, cursor = [], 37  # unaligned start: rounded up
    for part in reversed(parts):  # any slot order
        item = write_forward_inputs(arena, cursor, part)
        assert item.start % 64 == 0 and item.end - item.start == forward_arena_bytes(part)
        written.insert(0, pickle.loads(pickle.dumps(item)))
        cursor = item.end
    joined = stage_forward_parts(arena, written, torch.device("cpu"), rows=rows)
    _equal_structure(joined, expected)
    # Planned in a prepare/joiner process, staged on the device: plan in the arena
    # next to the parts (one copy), or in a separate buffer; JoinPlan pickles.
    plan_arena = allocate_forward_arena(1 << 22, shared=True)
    for target in (None, plan_arena):
        if target is None:
            big = allocate_forward_arena(arena.numel() + (1 << 22), shared=True)
            big[:arena.numel()] = arena
            source, offset = big, arena.numel() + 5
        else:
            source, offset = arena, 3
        plan = pickle.loads(pickle.dumps(plan_forward_join(source, written, offset, rows=rows,
                                                           plan_arena=target)))
        assert plan.plan_start % 64 == 0 and plan.in_arena == (target is None)
        _equal_structure(stage_joined(source, plan, torch.device("cpu"), plan_arena=target),
                         expected)
    single = forward_inputs(concatenate_groups(parts[1:2], rows=rows[:6]))
    plan = plan_forward_join(arena, written[1:2], 0, rows=rows[:6], plan_arena=plan_arena)
    _equal_structure(stage_joined(arena, plan, torch.device("cpu"), plan_arena=plan_arena),
                     single)
    model.eval()
    with torch.no_grad():
        for left, right in zip(model.forward_prepared_rl4(joined),
                               model.forward_prepared_rl4(concatenate_groups(parts, rows=rows)),
                               strict=True):
            _bitwise(left, right)


def test_forward_arena_fails_loudly(model, varied):
    from stpd.rl.batch_preparation import (
        allocate_forward_arena,
        forward_arena_bytes,
        forward_inputs,
        join_forward_inputs,
        write_forward_inputs,
    )

    group = prepared(model, "rows-v2").minibatches[0].groups[0]
    arena = allocate_forward_arena(forward_arena_bytes(group) - 1)
    with pytest.raises(ValueError, match="forward_arena_overflow"):
        write_forward_inputs(arena, 0, group)
    with pytest.raises(ValueError, match="host_uint8_vector"):
        write_forward_inputs(torch.empty(10, dtype=torch.int32), 0, group)
    huge = dc.replace(group, joint=dc.replace(group.joint, tensors={
        **group.joint.tensors, "kinds": group.joint.tensors["kinds"] + 2 ** 31}))
    with pytest.raises(ValueError, match="exceeds_int32:kinds"):
        forward_inputs(huge)
    with pytest.raises(ValueError, match="forward_inputs_require_rows_v2"):
        forward_inputs(dc.replace(group, layout=None))
    with pytest.raises(ValueError, match="join_requires_forward_inputs"):
        join_forward_inputs([group, group])
    with pytest.raises(ValueError, match="join_requires_one_index_dtype"):
        join_forward_inputs([forward_inputs(group),
                             forward_inputs(group, index_dtype=torch.int64)])
    from stpd.rl.batch_preparation import plan_forward_join, stage_joined

    full = allocate_forward_arena(forward_arena_bytes(group))
    part = write_forward_inputs(full, 0, group)
    with pytest.raises(ValueError, match="forward_arena_overflow"):
        plan_forward_join(full, [part, part], 0, plan_arena=allocate_forward_arena(64))
    with pytest.raises(ValueError, match="join_requires_parts"):
        plan_forward_join(full, [], 0)
    plan = plan_forward_join(full, [part, part], 0, plan_arena=allocate_forward_arena(1 << 22))
    with pytest.raises(ValueError, match="plan_arena_mismatch"):
        stage_joined(full, plan, torch.device("cpu"))


@pytest.mark.parametrize("kernels", ["heads", "cross", "nodes", "embed+nodes",
                                     "cross+embed+heads+nodes"])
@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("bucket_rows,width_multiple", [(256, 64), (3, 8)])
def test_kernel_levers_are_the_same_function_on_cpu(model, varied, kernels, packed, bucket_rows,
                                                    width_multiple):
    """V3 T6: folded value heads and folded cross-attention change only how the
    readouts are computed (FP32 on CPU: tight tolerance; BF16 on CUDA: tolerance)."""
    batch = prepared(model)
    for group in (g for mb in batch.minibatches for g in mb.groups):
        for plain in (group, dc.replace(group, policy=False)):
            rows = dc.replace(plain, layout=row_layout(plain, bucket_rows=bucket_rows,
                                                       width_multiple=width_multiple))
            (s0, l0, h0), g0 = forward_and_gradients(model, rows, packed=packed)
            (s1, l1, h1), g1 = forward_and_gradients(model, rows, packed=packed,
                                                     kernels=kernels)
            for left, right in ((s0, s1), (l0, l1), (h0, h1)):
                assert left.shape == right.shape
                assert torch.equal(left.isneginf(), right.isneginf())
                finite = left.isfinite()
                torch.testing.assert_close(left[finite], right[finite], rtol=1e-5, atol=2e-6)
            assert set(g0) == set(g1)
            for name in g0:
                torch.testing.assert_close(g0[name], g1[name], rtol=1e-4, atol=2e-6, msg=name)


def test_kernel_lever_selection_is_explicit_and_needs_rows_v2():
    from stpd.p1.rows_kernels import FOLD_CATALOG_LIMIT, parse_kernels

    assert PerformanceConfig().prepared_kernels == ""
    assert parse_kernels("") == () and parse_kernels("cross+heads") == ("cross", "heads")
    for bad in ("heads+cross", "heads+heads", "flash", "+", None, "embed", "cross+embed"):
        with pytest.raises(ValueError, match="invalid_prepared_kernels"):
            parse_kernels(bad)  # type: ignore[arg-type]
    with pytest.raises(BoundaryError, match="invalid_prepared_kernels"):
        PerformanceConfig(prepared_forward="rows-v2", prepared_kernels="heads+cross")
    with pytest.raises(BoundaryError, match="prepared_kernels_requires_rows_v2"):
        PerformanceConfig(prepared_kernels="heads")
    assert FOLD_CATALOG_LIMIT == 64
    assert parse_kernels("cross+heads+nodes") == ("cross", "heads", "nodes")
    assert parse_kernels("cross+embed+heads+nodes") == ("cross", "embed", "heads", "nodes")
    with pytest.raises(BoundaryError, match="prepared_kernels_embed_requires_compile"):
        PerformanceConfig(prepared_forward="rows-v2", prepared_kernels="embed+nodes")
    PerformanceConfig(prepared_forward="rows-v2", prepared_compile=True,
                      prepared_kernels="cross+embed+heads+nodes")


def test_embed_lever_private_copies_give_the_embedding_gradient():
    """The private-copy weight gradient (eager and under compile: aot_eager, as
    in this file's other compiled tests) equals the embedding gradient, repeats
    included, for one and several copies; non-plain tables are refused."""
    from stpd.p1 import rows_kernels
    from stpd.p1.rows_kernels import lookup

    torch.manual_seed(3)
    for rows, width in ((5, 7), (300, 8)):
        assert rows_kernels._copies(rows, width) == min(32, (1 << 21) // (rows * width))
        table = torch.nn.Embedding(rows, width)
        indices = torch.randint(0, rows, (97,))
        indices[:9] = 4
        upstream = torch.randn(len(indices), width)
        reference = torch.autograd.grad((table(indices) * upstream).sum(), table.weight)[0]
        def call(module, picked):
            return lookup(module, picked, private=True)

        for run in (call, torch.compile(call, dynamic=True, backend="aot_eager")):
            out = run(table, indices)
            torch.testing.assert_close(out, table(indices))
            got = torch.autograd.grad((out * upstream).sum(), table.weight)[0]
            torch.testing.assert_close(got, reference, rtol=1e-6, atol=1e-6)
    assert rows_kernels._copies(8192, 192) == 1
    for options in ({"padding_idx": 0}, {"max_norm": 1.0}, {"scale_grad_by_freq": True},
                    {"sparse": True}):
        with pytest.raises(ValueError, match="embed_lever_requires_plain_embedding"):
            lookup(torch.nn.Embedding(5, 7, **options), torch.tensor([0, 1]), private=True)


def test_nodes_lever_permutation_gathers_backward_as_gathers_and_refuse_non_permutations():
    from stpd.p1.rows_kernels import permutation_inverse, permute_rows

    torch.manual_seed(5)
    values = torch.randn(6, 4, requires_grad=True)
    perm = torch.tensor([3, 0, 5, 1, 4, 2])
    grad = torch.randn(6, 4)
    (permute_rows(values, perm, "unit") * grad).sum().backward()
    expected = torch.zeros(6, 4).index_add(0, perm, grad)
    assert torch.equal(values.grad, expected)
    assert torch.equal(permutation_inverse(perm, "unit")[perm], torch.arange(6))
    with pytest.raises(RuntimeError, match="nodes_lever_requires_permutation:unit"):
        permutation_inverse(torch.tensor([3, 0, 5, 1, 4, 4]), "unit")
    with pytest.raises(ValueError, match="nodes_lever_requires_permutation:unit"):
        permute_rows(values, perm[:5], "unit")


def test_nodes_lever_owner_maps_refuse_broken_reductions_loudly():
    """A reduction that is not a partition, or whose row order is not a
    permutation, fails with the lever's name instead of reading unset values."""
    from stpd.p1.rows_kernels import _owners

    buckets = ((2, 1), (1, 2), (0, 1))  # widths 2, 1, 1, 0 -> 4 terms, 4 rows
    inverse = torch.tensor([2, 0, 3, 1])
    owner, scale, empty = _owners((torch.tensor([3, 1, 0, 2]), buckets, inverse), 4, 4, True)
    order = torch.empty(4, dtype=torch.long)
    order[inverse] = torch.arange(4)
    assert torch.equal(owner[torch.tensor([3, 1, 0, 2])],
                       torch.repeat_interleave(order, torch.tensor([2, 1, 1, 0])))
    assert torch.equal(empty, torch.tensor([False, False, False, True])[order.argsort()])
    assert torch.equal(scale > 0, ~empty)
    with pytest.raises(RuntimeError, match="nodes_lever_requires_partition"):
        _owners((torch.tensor([3, 1, 1, 2]), buckets, inverse), 4, 4, True)
    with pytest.raises(RuntimeError, match="nodes_lever_requires_permutation:reduction_rows"):
        _owners((torch.tensor([3, 1, 0, 2]), buckets, torch.tensor([2, 0, 2, 1])), 4, 4, True)
    with pytest.raises(ValueError, match="nodes_lever_requires_complete_reduction"):
        _owners((torch.tensor([3, 1, 0, 2]), buckets, inverse), 4, 5, True)
    with pytest.raises(ValueError, match="nodes_lever_requires_complete_reduction"):
        _owners((torch.tensor([3, 1, 0, 2]), buckets, inverse[:3]), 4, 4, True)


@pytest.mark.parametrize("kernels", ["cross+heads+nodes", "cross+embed+heads+nodes"])
def test_compiled_kernel_levers_are_the_same_function_on_cpu(model, varied, monkeypatch, kernels):
    """The compiled lever path (dynamic-shape block variants and node builder:
    dynamo capture and AOT autograd, `aot_eager` backend to keep the CPU test
    short; inductor codegen is the CUDA benchmark's) computes what the eager
    original computes (CPU, FP32)."""
    compile_ = torch.compile
    monkeypatch.setattr(torch, "compile",
                        lambda function, **keywords: compile_(
                            function, **{**keywords, "backend": "aot_eager"}))

    def compiled_names():
        return [name for name in vars(model) if name.startswith("_rows_") and "compiled" in name]

    for name in compiled_names():  # compiled once per model; restored after this test
        monkeypatch.delitem(model.__dict__, name)
    try:
        check_compiled_levers(model, kernels)
    finally:
        for name in compiled_names():
            del model.__dict__[name]


def check_compiled_levers(model, kernels):
    from stpd.p1.rows_forward import forward_rows

    batch = prepared(model)
    groups = [g for mb in batch.minibatches for g in mb.groups]
    for group in (groups[0], dc.replace(groups[0], policy=False)):
        rows = dc.replace(group, layout=row_layout(group, bucket_rows=256, width_multiple=64))
        (s0, l0, h0), g0 = forward_and_gradients(model, rows, packed=False)
        model.zero_grad(set_to_none=True)
        out = forward_rows(model, rows, compile=True, packed=False, kernels=kernels)
        (s1, l1, h1) = (value.detach() for value in out)
        generator = torch.Generator().manual_seed(5)
        total = out[2].mul(torch.randn(out[2].shape, generator=generator)).sum()
        for value in out[:2]:
            weights = torch.randn(value.shape, generator=generator)
            total = total + value.masked_fill(~value.isfinite(), 0).mul(weights).sum()
        total.backward()
        g1 = {name: p.grad.clone() for name, p in model.named_parameters() if p.grad is not None}
        for left, right in ((s0, s1), (l0, l1), (h0, h1)):
            assert torch.equal(left.isneginf(), right.isneginf())
            finite = left.isfinite()
            torch.testing.assert_close(left[finite], right[finite], rtol=1e-5, atol=2e-6)
        assert set(g0) == set(g1)
        for name in g0:
            torch.testing.assert_close(g0[name], g1[name], rtol=1e-4, atol=2e-6, msg=name)


def test_levered_block_wrapper_passes_kernels_by_keyword_only(model, varied, monkeypatch):
    """forward_rows reaches the block through the levered wrapper with the 12
    positional block arguments plus `kernels` by keyword (keyword-only, so no
    positional argument can fill it)."""
    import inspect

    from stpd.p1 import rows_forward

    for packed, function in ((False, rows_forward.rows_block),
                             (True, rows_forward.rows_block_packed)):
        parameter = inspect.signature(function).parameters["kernels"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert rows_forward._block(packed, ()) is function
    calls = []
    original = rows_forward.rows_block

    def spy(*args, **keywords):
        calls.append((len(args), keywords))
        return original(*args, **keywords)

    monkeypatch.setattr(rows_forward, "rows_block", spy)
    group = prepared(model).minibatches[0].groups[0]
    rows = dc.replace(group, layout=row_layout(group, bucket_rows=256, width_multiple=64))
    rows_forward.forward_rows(model, rows, packed=False, kernels="cross+heads")
    assert calls and all(call == (12, {"kernels": ("cross", "heads") if group.policy
                                       else ("heads",)}) for call in calls)


def test_padded_cross_long_catalog_projects_keys_like_the_folded_form():
    """Both cross forms (folded for short catalogs, projected for long) agree."""
    from stpd.p1 import rows_kernels

    torch.manual_seed(3)
    module = torch.nn.MultiheadAttention(192, 4, batch_first=True).eval()
    lengths = torch.tensor([5, 1, 7])
    keep = torch.arange(7)[None, :] < lengths[:, None]
    padded = torch.randn(3, 7, 192).masked_fill(~keep[..., None], 0)
    action = torch.randn(3, 9, 192)
    folded = rows_kernels.padded_cross(module, action, padded, keep)
    expected, _ = module(action, padded, padded, key_padding_mask=~keep, need_weights=False)
    torch.testing.assert_close(folded, expected, rtol=1e-5, atol=2e-6)
    limit = rows_kernels.FOLD_CATALOG_LIMIT
    try:
        rows_kernels.FOLD_CATALOG_LIMIT = 0
        projected = rows_kernels.padded_cross(module, action, padded, keep)
    finally:
        rows_kernels.FOLD_CATALOG_LIMIT = limit
    torch.testing.assert_close(projected, expected, rtol=1e-5, atol=2e-6)
    # Packed real tokens (flash varlen layout; CPU reference one row at a time).
    encoded = torch.cat([padded[row, :int(length)] for row, length in enumerate(lengths)])
    offsets = torch.cat((torch.zeros(1, dtype=torch.long), lengths.cumsum(0))).to(torch.int32)
    packed = rows_kernels.packed_cross(module, action, encoded, offsets, 7)
    torch.testing.assert_close(packed, expected, rtol=1e-5, atol=2e-6)


def test_cross_lever_keeps_the_original_attention_for_long_catalogs(model, varied, monkeypatch):
    from stpd.p1 import rows_kernels

    group = prepared(model, "rows-v2").minibatches[0].groups[0]
    (s0, l0, h0), _ = forward_and_gradients(model, group, packed=True)
    monkeypatch.setattr(rows_kernels, "FOLD_CATALOG_LIMIT", 0)
    (s1, l1, h1), _ = forward_and_gradients(model, group, packed=True, kernels="cross")
    assert torch.equal(s0, s1) and torch.equal(l0, l1) and torch.equal(h0, h1)


def test_effective_config_records_prepared_kernels_only_when_selected():
    from stpd.rl.learner import LearnerConfig
    from stpd.rl.segment_learner import _resume_config
    from stpd.rl.segments import SegmentConfig

    rows = PerformanceConfig(prepared_forward="rows-v2")
    base = LearnerConfig(run="rows-unit", peer={"kind": "local", "directory": "/nonexistent"},
                         performance=rows, segments=SegmentConfig())
    selected = dc.replace(base, performance=dc.replace(rows, prepared_kernels="cross+heads"))
    assert "prepared_kernels" not in base.frozen()["performance"]
    assert selected.frozen()["performance"]["prepared_kernels"] == "cross+heads"
    assert _resume_config(dc.asdict(base)) == _resume_config(dc.asdict(selected))


def test_prepared_update_with_kernel_levers_steps_like_the_original_on_cpu(model, varied):
    rows = prepared(model, "rows-v2")
    config = LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01)
    reference = copy.deepcopy(model)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    reference_optimizer = torch.optim.AdamW(reference.parameters(), lr=1e-3)
    for minibatch in rows.minibatches:
        result = prepared_update(
            model, optimizer, minibatch, config=config, target_kl=1e9,
            performance=PerformanceConfig(prepared_forward="rows-v2",
                                          prepared_kernels="cross+heads+nodes"))
        expected = prepared_update(reference, reference_optimizer, minibatch, config=config,
                                   target_kl=1e9,
                                   performance=PerformanceConfig(prepared_forward="rows-v2"))
        assert result["stepped"] and result["rows_forward"]["kernels"] == "cross+heads+nodes"
        assert "kernels" not in expected["rows_forward"]
        for key in ("policy_loss", "td_loss", "terminal_loss", "hp_loss", "gradient_norm"):
            assert result[key] == pytest.approx(expected[key], rel=1e-4, abs=1e-6)
    for name, value in reference.state_dict().items():
        torch.testing.assert_close(model.state_dict()[name], value, rtol=1e-4, atol=1e-5,
                                   msg=name)


def _inference_groups(model):
    """Real policy and value groups re-laid out so buckets hold 1, 2, 3 or all rows:
    single-row buckets and role term counts of 0, 1 and more (dynamo's 0/1 classes)."""
    batch = prepared(model)
    groups = [g for mb in batch.minibatches for g in mb.groups]
    out = []
    for group, bucket_rows in itertools.product(groups[:2], (1, 2, 3, 256)):
        for policy in (True, False):
            base = group if policy else dc.replace(group, policy=False)
            out.append(dc.replace(base, layout=row_layout(base, bucket_rows=bucket_rows,
                                                          width_multiple=8)))
    # one-term roles (rare in these fixtures, common enough online): first term kept
    one = out[6]  # policy, one bucket of all rows
    assert one.policy and len(one.layout.scene_buckets) == 1
    roles = {key: (c[:1], s[:1], w[:1], (min(1, len(c)),))
             for key, (c, s, w, _terms) in one.layout.roles.items()}
    out.append(dc.replace(one, layout=dc.replace(one.layout, roles=roles)))
    return out


def _as_inference(value):
    """The group as the service's device process stages it: inference tensors."""
    if isinstance(value, torch.Tensor):
        with torch.inference_mode():
            return value.clone()
    if dc.is_dataclass(value) and not isinstance(value, type):
        return dc.replace(value, **{f.name: _as_inference(getattr(value, f.name))
                                    for f in dc.fields(value) if f.init})
    if isinstance(value, dict):
        return {key: _as_inference(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return type(value)(_as_inference(item) for item in value)
    return value


def _term_classes(groups):
    seen = set()
    for group in groups:
        if not group.policy:
            continue
        for key in ("source", "target"):
            seen.update(min(int(n), 2) for n in group.layout.roles[key][3])
    return seen


def test_compiled_inference_block_never_falls_back_to_eager(model, varied, monkeypatch):
    """Friday 1463865-1463901: the levered block hit dynamo's recompile_limit (8) and
    ran eager. With one code object per (levers, policy), role terms of at least two
    and inference-tensor groups, every shape class the service sends (single-row buckets,
    0/1/n role terms, fold and wide catalogs, value groups) needs at most two graphs
    per variant (one-row buckets and the rest), so a limit of 2 holds; dynamo is told
    to fail instead of falling back, so any new specialization turns this red."""
    from torch._dynamo import config as dynamo_config

    from stpd.p1 import rows_kernels
    from stpd.p1.rows_forward import compile_counters, forward_rows

    compile_ = torch.compile
    monkeypatch.setattr(torch, "compile",
                        lambda function, **keywords: compile_(
                            function, **{**keywords, "backend": "eager"}))
    monkeypatch.setattr(dynamo_config, "recompile_limit", 2)
    monkeypatch.setattr(dynamo_config, "fail_on_recompile_limit_hit", True)
    for name in [n for n in vars(model) if n.startswith("_rows_") and "compiled" in n]:
        monkeypatch.delitem(model.__dict__, name)
    torch._dynamo.reset()
    groups = [_as_inference(group) for group in _inference_groups(model)]
    assert {0, 1, 2} <= _term_classes(groups)  # the 0/1 classes are really exercised
    assert any(bucket[0] == 1 for g in groups for bucket in g.layout.scene_buckets)
    before = compile_counters()
    try:
        for fold in (64, 0, 64):  # fold (cross lever) and wide catalogs, then fold again
            monkeypatch.setattr(rows_kernels, "FOLD_CATALOG_LIMIT", fold)
            for group in groups:
                with torch.inference_mode():
                    compiled = forward_rows(model, group, compile=True, packed=False,
                                            kernels="cross+heads+nodes")
                    eager = forward_rows(model, group, packed=False,
                                         kernels="cross+heads+nodes")
                for left, right in zip(eager, compiled, strict=True):
                    assert torch.equal(left, right)  # role padding adds exact zeros
    finally:
        for name in [n for n in vars(model) if n.startswith("_rows_") and "compiled" in n]:
            del model.__dict__[name]
        torch._dynamo.reset()
    after = compile_counters()
    assert (after["dynamo_frames_total"] - before["dynamo_frames_total"]
            == after["dynamo_frames_ok"] - before["dynamo_frames_ok"])


def _fresh_compiled(model):
    for name in [n for n in vars(model) if n.startswith("_rows_") and "compiled" in n]:
        del model.__dict__[name]


def test_a_new_mu_reuses_the_compiled_block_graphs(model, varied, monkeypatch):
    """Each published mu is a new model in the device process. Its compiled blocks
    share the variant code objects of the first, so dynamo's graphs on them are
    reused: no compile frame for the second model on the same groups."""
    import copy

    from torch._dynamo import config as dynamo_config

    from stpd.p1.rows_forward import compile_counters, forward_rows

    compile_ = torch.compile
    monkeypatch.setattr(torch, "compile",
                        lambda function, **keywords: compile_(
                            function, **{**keywords, "backend": "eager"}))
    monkeypatch.setattr(dynamo_config, "fail_on_recompile_limit_hit", True)
    _fresh_compiled(model)
    torch._dynamo.reset()
    groups = [_as_inference(group) for group in _inference_groups(model)[:8]]
    second = copy.deepcopy(model)
    try:
        frames = []
        for mu in (model, second):
            before = compile_counters()["dynamo_frames_ok"]
            for group in groups:
                with torch.inference_mode():
                    forward_rows(mu, group, compile=True, packed=False,
                                 kernels="cross+heads+nodes")
            frames.append(compile_counters()["dynamo_frames_ok"] - before)
    finally:
        _fresh_compiled(model)
        _fresh_compiled(second)
        torch._dynamo.reset()
    assert frames[0] > 0 and frames[1] == 0, frames


def test_device_process_stages_inference_tensors(model, varied):
    """The device process's arena staging and part join yield inference tensors, the
    dispatch key set of the groups the forward stages itself (one compiled graph)."""
    from stpd.p1.inference_service import _collect_inference, _Collector, _stage_inference
    from stpd.rl.batch_preparation import (
        allocate_forward_arena,
        forward_arena_bytes,
        plan_forward_join,
        prepare_group,
        stage_joined,
        write_forward_inputs,
    )

    def tensors(value):
        if isinstance(value, torch.Tensor):
            yield value
        elif dc.is_dataclass(value) and not isinstance(value, type):
            for field in dc.fields(value):
                yield from tensors(getattr(value, field.name))
        elif isinstance(value, dict):
            for item in value.values():
                yield from tensors(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                yield from tensors(item)

    frames, floors = _frames(model)
    cuts = [(0, 3), (3, len(frames))]
    parts = [prepare_group(frames[a:b], tuple(range(b - a)), True, torch.tensor(floors[a:b]),
                           deduplicate_text=True, layout="rows-v2") for a, b in cuts]
    arena = allocate_forward_arena(sum(map(forward_arena_bytes, parts)) + (1 << 22),
                                   shared=True)
    written, cursor = [], 0
    for part in parts:
        written.append(write_forward_inputs(arena, cursor, part))
        cursor = written[-1].end
    rows = tuple(range(len(frames)))
    plan = plan_forward_join(arena, written, cursor, rows=rows)
    staged = _stage_inference(stage_joined, arena, plan, torch.device("cpu"))
    collector = _Collector()
    assert _collect_inference(collector, {"job": 1, "part": 0, "parts": 2, "group": parts[0],
                                          "load_seconds": 0.0}) is None
    _, joined = _collect_inference(collector, {"job": 1, "part": 1, "parts": 2,
                                               "group": parts[1], "load_seconds": 0.0})
    for group in (staged, joined):
        found = list(tensors(group))
        assert found and all(t.is_inference() for t in found)


def test_role_padding_is_exact_in_the_block(model, varied):
    """`_at_least_two_terms` adds zero-weight terms only: the block's outputs are
    bitwise those of the unpadded roles (CPU FP32, masked and packed blocks)."""
    from stpd.p1.rows_forward import _at_least_two_terms, forward_rows

    for group in _inference_groups(model):
        if not group.policy:
            continue
        with torch.inference_mode():
            plain = forward_rows(model, group, packed=False)
        layout = group.layout
        padded_roles = {}
        for key in ("source", "target"):
            candidate, slot, weight, terms = layout.roles[key]
            parts = [_at_least_two_terms((c, s, w, torch.ones(1, dtype=torch.bool)))
                     for c, s, w in zip(candidate.split(list(terms)), slot.split(list(terms)),
                                        weight.split(list(terms)), strict=True)]
            for n, part in zip(terms, parts, strict=True):  # the added terms: +0, at 0
                assert len(part[0]) == max(2, n)
                added = part[2][n:]
                assert torch.equal(added, torch.zeros_like(added))
                assert not torch.signbit(added).any()  # +0, not -0 nor a tiny weight
                assert not part[0][n:].any() and not part[1][n:].any()
            padded_roles[key] = (torch.cat([p[0] for p in parts]),
                                 torch.cat([p[1] for p in parts]),
                                 torch.cat([p[2] for p in parts]),
                                 tuple(len(p[0]) for p in parts))
        padded = dc.replace(group, layout=dc.replace(layout, roles=padded_roles))
        with torch.inference_mode():
            again = forward_rows(model, padded, packed=False)
        for left, right in zip(plain, again, strict=True):
            assert torch.equal(left, right)
