"""Tensor compilation retains inputs, dropout, gradients and microbatch membership."""
import copy

import pytest
import torch

from stpd.p1.execution import MStdExecution, microbatches
from stpd.p1.input import V2_INPUT_SPEC
from stpd.p1.model import MStd
from stpd.p1.numerics import TrainingNumerics
from stpd.p1.optimization import optimizer_update
from stpd.p1.profiling import synthetic_frame
from stpd.p1.tensor_input import TensorFrame, compile_frame
from stpd.p1.vocabulary import public_vocabulary
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


def frames_v2():
    # Convert the synthetic source shape into v2 via its input Frame for these
    # graph tests; the pack integration tests exercise the original projector.
    frames = [synthetic_frame(2), synthetic_frame(3)]
    frames[0].entities[0].numeric += [("missing", None), ("zero", 0.0), ("negative", -3.)]
    frames[0].entities[0].refs += [1, 1, 2]
    frames[0].roles[0] = ((0, 1), -1)
    return frames


@pytest.mark.parametrize("training", [False, True])
def test_compiled_graph_is_bitwise_equal_including_dropout_and_gradients(training):
    with TrainingNumerics(1, 2, torch.device('cpu')).scope():
        model = MStd(public_vocabulary(), MStdExecution.for_plan('mstd-training-v1'),
                     value_heads=True)
        frames = frames_v2()
        packed = [TensorFrame.restore(decode_checkpoint(encode_checkpoint(
            compile_frame(frame, model).state()))) for frame in frames]
        assert list(microbatches(frames, [0, 1], model)) == list(
            microbatches(packed, [0, 1], model))
        model.train(training)
        state = torch.get_rng_state()
        scores, values = model.forward_with_values(frames)
        loss = sum(x.sum() for x in scores) + values.sum()
        loss.backward()
        grads = {k: None if v.grad is None else v.grad.clone() for k, v in model.named_parameters()}
        model.zero_grad(set_to_none=True)
        torch.set_rng_state(state)
        other, other_values = model.forward_with_values(packed)
        for a, b in zip(scores, other, strict=True):
            assert torch.equal(a, b)
        assert torch.equal(values, other_values)
        (sum(x.sum() for x in other) + other_values.sum()).backward()
        for name, parameter in model.named_parameters():
            if grads[name] is None:
                assert parameter.grad is None
            else:
                assert torch.equal(grads[name], parameter.grad), name


def test_two_updates_same_parameters_rng_and_weighted_masked_targets():
    frames = frames_v2()
    with TrainingNumerics(1, 2, torch.device('cpu')).scope():
        raw = MStd(public_vocabulary(), MStdExecution.for_plan('mstd-training-v1'),
                   value_heads=True)
        packed = copy.deepcopy(raw)
        tensors = [compile_frame(f, raw) for f in frames]
        left = torch.optim.AdamW(raw.parameters(), lr=3e-4)
        right = torch.optim.AdamW(packed.parameters(), lr=3e-4)
        for _ in range(2):
            state = torch.get_rng_state()
            a = optimizer_update(raw, left, frames, [0, 1], 3e-4, weights=[.5, 1.5],
                                 value_targets=[(1., .8), (None, None)])
            after = torch.get_rng_state()
            torch.set_rng_state(state)
            b = optimizer_update(packed, right, tensors, [0, 1], 3e-4, weights=[.5, 1.5],
                                 value_targets=[(1., .8), (None, None)])
            assert torch.equal(after, torch.get_rng_state())
            assert a['mean_N_loss'] == b['mean_N_loss']
            assert a['auxiliary_loss'] == b['auxiliary_loss']
            for name, value in raw.state_dict().items():
                assert torch.equal(value, packed.state_dict()[name]), name


def test_pack_real_store_worker_pause_resume_and_exact_population(tmp_path):
    from test_artifact_store_v1 import PRODUCER
    from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

    from stpd.cloud_jobs.contracts import ComputeRequest
    from stpd.cloud_jobs.execution import dispatch, validate_receipt
    from stpd.p1.dataset import SnapshotReader
    from stpd.p1.formal_training import MStdTrainer
    from stpd.p1.spec import MStdConfig
    from stpd.p1.tensor_pack import load_tensor_pack, publish_tensor_pack
    from stpd.p1.workload import populations_from_reader, prepare_p1_run, verify_p1_run
    from stpd.workers.p1_run import verify_p1_checkpoint

    store = store_at(tmp_path / 'store')
    projections = [projection_fixture(store, f'tensor-{i}')[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    reader = SnapshotReader(store, snapshot.artifact_id)
    vocabulary = public_vocabulary()
    pack, packed = publish_tensor_pack(store, reader, vocabulary, PRODUCER)
    loaded = load_tensor_pack(store, pack.artifact_id, reader, vocabulary)
    raw = populations_from_reader(reader, input_projection=V2_INPUT_SPEC)
    config = MStdConfig(max_epochs=2)
    a = MStdTrainer(raw, vocabulary, snapshot.artifact_id, config,
                    MStdExecution.for_plan('mstd-training-v1'))
    b = MStdTrainer(loaded, vocabulary, snapshot.artifact_id, config,
                    MStdExecution.for_plan('mstd-training-v1'))
    assert a.identity == b.identity
    for _ in range(2):
        left, right = a.advance(), b.advance()
        assert left['mean_N_loss'] == right['mean_N_loss']
        assert a.best_loss == b.best_loss
    for key, tensor in a.model.state_dict().items():
        assert torch.equal(tensor, b.model.state_dict()[key])
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER, config, 'b'*32,
                         input_projection=V2_INPUT_SPEC, tensor_pack_id=pack.artifact_id)
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    assert workload.prepared_populations is not None
    assert workload.prepared_populations['train'].point_ids == packed['train'].point_ids
    first = ComputeRequest('training', run.artifact_id, PRODUCER, 'tensor-pause', stop_after=1)
    paused = dispatch(store, first, PRODUCER)
    validate_receipt(store, first, paused)
    from dataclasses import replace

    resume = replace(first, attempt_id='tensor-resume', stop_after=None,
                     resume=paused.checkpoint_id)
    result = dispatch(store, resume, PRODUCER)
    validate_receipt(store, resume, result)
    saved = verify_p1_checkpoint(store, result.checkpoint_id, workload)
    assert saved.state['step'] == 2
    for key, tensor in a.model.state_dict().items():
        assert torch.equal(tensor, saved.state['model'][key])


@pytest.mark.parametrize('mutation', ['target', 'weight', 'value', 'compiler', 'source'])
def test_pack_rejects_self_consistent_tampered_labels_weights_and_identity(tmp_path, mutation):
    import io
    import json
    from dataclasses import replace

    from test_artifact_store_v1 import PRODUCER
    from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

    from spireagent.json_boundary import BoundaryError, FrozenObject
    from stpd.p1.data import json_bytes
    from stpd.p1.dataset import SnapshotReader
    from stpd.p1.tensor_pack import load_tensor_pack, publish_tensor_pack

    store = store_at(tmp_path / 'store')
    projections = [projection_fixture(store, f'bad-tensor-{i}')[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    reader = SnapshotReader(store, snapshot.artifact_id)
    vocab = public_vocabulary()
    pack, _ = publish_tensor_pack(store, reader, vocab, PRODUCER, value_heads=True)
    index = json.loads(b''.join(store.read_payload(pack.payload('index'))))
    params = pack.parameters.value()
    payloads = list(pack.payloads)
    if mutation in {'compiler', 'source'}:
        index['binding']['compiler']['model.py'] = '0'*64
        params['binding'] = index['binding']
    else:
        shard = index['shards'][0]
        old = pack.payload(shard['role'])
        state = decode_checkpoint(b''.join(store.read_payload(old)))
        row = state['rows'][0]
        if mutation == 'target':
            row['target'] = (row['target'] + 1) % len(row['frame']['choices'])
        elif mutation == 'weight':
            row['weight'] = 7.
        else:
            row['values'] = (1., 1.)
        new = store.put_payload(old.role, io.BytesIO(encode_checkpoint(state)))
        payloads[payloads.index(old)] = new
        shard['sha256'] = new.sha256
        # No reliance on the pack's self-reported summary: forge it as well.
        split = row['split']
        if mutation == 'target':
            index['partitions'][split]['targets_sha256'] = '1'*64
        elif mutation == 'weight':
            index['partitions'][split]['weights_sha256'] = '2'*64
        else:
            index['partitions'][split]['values_sha256'] = '3'*64
        params['partitions'] = index['partitions']
    old_index = pack.payload('index')
    payloads[payloads.index(old_index)] = store.put_payload('index', io.BytesIO(json_bytes(index)),
                                                        'application/json')
    forged = replace(pack, payloads=tuple(payloads), parameters=FrozenObject.of(params))
    store.publish(forged)
    with pytest.raises(BoundaryError, match='pack_identity_mismatch|point_supervision_mismatch'):
        load_tensor_pack(store, forged.artifact_id, reader, vocab, value_heads=True)
