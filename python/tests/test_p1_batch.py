"""Packed full-catalog scores retain independent-scene and loss semantics."""

from __future__ import annotations

import copy

import torch
from test_p1_t0 import observation
from torch import nn
from torch.nn import functional as F

from stpd.p1.formal_training import MStdTrainer
from stpd.p1.input import fit_vocabulary, project
from stpd.p1.model import MStd


def frames():
    short = observation()
    longer = copy.deepcopy(short)
    longer["scene"]["content"]["additional"] = [
        {"description": "Gain 123 Block and draw 3 cards.", "amount": 123},
        {"description": "This additional public field must not be truncated."},
    ]
    longer["catalog"].append({**longer["catalog"][0], "choice_id": "extra", "kind": "new"})
    no_roles = copy.deepcopy(short)
    for choice in no_roles["catalog"]:
        choice.pop("subject", None)
        choice.pop("target", None)
    return [project(short), project(longer), project(no_roles)]


def test_packed_masks_keep_each_complete_catalog_scores_and_gradients():
    examples = frames()
    torch.manual_seed(3)
    model = MStd(fit_vocabulary(examples))
    # Dropout masks are deliberately not expected to match between packing shapes.
    model.eval()
    for module in model.modules():
        if isinstance(module, nn.MultiheadAttention):
            module.dropout = 0.0
    targets = [0, 2, 1]
    scores = [model(frame) for frame in examples]
    expected = [value.detach().clone() for value in scores]
    sum(
        F.cross_entropy(score[None], torch.tensor([target])) / len(examples)
        for score, target in zip(scores, targets, strict=True)
    ).backward()
    gradients = {name: p.grad.clone() for name, p in model.named_parameters() if p.grad is not None}
    model.zero_grad(set_to_none=True)
    scores = model.forward_batch(examples)
    assert [len(value) for value in scores] == [2, 3, 2]
    for actual, wanted in zip(scores, expected, strict=True):
        torch.testing.assert_close(actual, wanted, rtol=2e-5, atol=2e-6)
    packed = nn.utils.rnn.pad_sequence(scores, batch_first=True, padding_value=-torch.inf)
    F.cross_entropy(packed, torch.tensor(targets)).backward()
    for name, parameter in model.named_parameters():
        if name in gradients:
            torch.testing.assert_close(parameter.grad, gradients[name], rtol=3e-4, atol=2e-6)


def test_microbatch_resource_bound_preserves_order_and_oversize_singleton():
    examples = tuple(frames())
    order = [0, 1] * 10
    batches = list(MStdTrainer.batches(examples, order))
    assert [i for group in batches for i in group] == order
    assert all(len(group) <= 8 for group in batches)
    huge = copy.deepcopy(examples[0])
    huge.entities *= 100
    batches = list(MStdTrainer.batches((huge, examples[0]), [1, 0, 1]))
    assert batches == [[1], [0], [1]]


def test_text_padding_bound_keeps_long_text_complete_and_separate():
    from stpd.p1.profiling import synthetic_frame

    short = synthetic_frame(2)
    long = synthetic_frame(2, extra_text=" ".join(f"word{i:04d}" for i in range(500)))
    examples = (short, long)
    model = MStd(fit_vocabulary([short]))
    order = [0, 1, 0]
    assert list(MStdTrainer.batches(examples, order, model)) == [[0], [1], [0]]
    tokens = [len(model.tokenize(text)[0]) for node in long.entities for _, text in node.text]
    assert max(tokens) > 500  # The long observation is retained, never truncated.


def test_profile_advances_normal_trainer_and_timing_does_not_enter_checkpoint():
    from test_p1_formal_training import engine

    from stpd.p1.profiling import profile_trainer
    from stpd.workers.checkpoint_codec import decode_checkpoint

    trainer = engine(train_count=3)
    report = profile_trainer(trainer, 1)
    assert report["final_step"] == trainer.step == 1
    assert report["checkpoint_bytes"] > 0
    assert report["samples"][0]["timing"]["train_seconds"] > 0
    state = decode_checkpoint(trainer.checkpoint())
    assert all("timing" not in sample for sample in state["history"])
    assert "timing" not in state["dev_history"][0]
