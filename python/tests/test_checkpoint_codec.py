from __future__ import annotations

import io

import pytest
import torch

from spireagent.json_boundary import BoundaryError
from stpd.rl import checkpoint_codec as rl_codec
from stpd.workers import checkpoint_codec as legacy_codec
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


def test_tensor_tree_roundtrip_preserves_optimizer_keys_and_tuples() -> None:
    value = {
        "head": {"weight": torch.tensor([[1.0, 2.0]])},
        "optimizer": {"state": {0: {"step": torch.tensor(1.0)}}, "betas": (0.9, 0.999)},
        "flags": [True, None, "value", 17],
    }
    raw = encode_checkpoint(value)
    restored = decode_checkpoint(raw)
    assert torch.equal(restored["head"]["weight"], value["head"]["weight"])
    assert set(restored["optimizer"]["state"]) == {0}
    assert restored["optimizer"]["betas"] == (0.9, 0.999)
    assert restored["flags"] == value["flags"]
    assert raw == encode_checkpoint(value)


@pytest.mark.parametrize("codec", [legacy_codec, rl_codec], ids=["legacy", "rl"])
def test_pickle_and_malformed_or_non_finite_checkpoints_rejected(codec) -> None:
    buffer = io.BytesIO()
    torch.save({"example": torch.tensor([1])}, buffer)
    for raw in (buffer.getvalue(), b"", b"STPDCKP1" + (999999999).to_bytes(8, "little") + b"x"):
        with pytest.raises(BoundaryError):
            codec.decode_checkpoint(raw)
    with pytest.raises(BoundaryError, match="non_finite"):
        codec.encode_checkpoint({"bad": torch.tensor([float("nan")])})
    with pytest.raises(BoundaryError, match="unsupported"):
        codec.encode_checkpoint({"bad": object()})


def test_rl_codec_keeps_wire_bytes_and_pending_capacity_separate_from_legacy() -> None:
    small = {"weights": torch.tensor([1.0]), "selection": (7, None)}
    assert rl_codec.encode_checkpoint(small) == encode_checkpoint(small)
    pending = {"pending": [{"request": [None] * 25_000} for _ in range(4)]}
    with pytest.raises(BoundaryError, match="structure_limit"):
        encode_checkpoint(pending)
    raw = rl_codec.encode_checkpoint(pending)
    assert rl_codec.decode_checkpoint(raw) == pending
    with pytest.raises(BoundaryError, match="structure_limit"):
        decode_checkpoint(raw)
    with pytest.raises(BoundaryError, match="structure_limit"):
        rl_codec.encode_checkpoint({"pending": [None] * 400_000})
