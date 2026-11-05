"""Binary representation boundaries; fixtures are public synthetic engineering inputs."""

from __future__ import annotations

import copy
import dataclasses as dc
import hashlib
import json
import random

import pytest
import torch
from test_rl41_w1 import model as model
from test_rl41_w1 import request, segment, source

from spireagent.rl_dataplane_ports import TENSOR_SEGMENT_MAGIC
from stpd.p1.data import json_bytes
from stpd.rl import batch_preparation as bp
from stpd.rl.tensor_segment import (
    TensorSegmentEncoder,
    decode_packet,
    packet_header,
    packet_header_fields,
)


def encoder(model, tmp_path):
    from stpd.p1.frame_compiler import FrameCompiler

    return TensorSegmentEncoder(
        vocabulary=model.vocabulary,
        compiler_identity=FrameCompiler(model.vocabulary).identity,
        recipe={
            "producer": {"name": "public-synthetic-fixture", "revision": "a" * 40},
            "recipe_digest": "b" * 64,
            "use_and_seed_digest": "c" * 64,
        },
        spool=tmp_path,
    )


def batch_digest(batch):
    """Hash numerical/parameter-free semantics, excluding physical request locators."""
    h = hashlib.sha256()

    def visit(x):
        if isinstance(x, torch.Tensor):
            h.update(str((str(x.dtype), tuple(x.shape))).encode())
            h.update(x.detach().cpu().contiguous().numpy().tobytes())
        elif dc.is_dataclass(x):
            h.update(type(x).__name__.encode())
            for f in dc.fields(x):
                if f.name in {
                    "request",
                    "prepared_at",
                    "timings",
                    "worker_pids",
                    "pid",
                    "seconds",
                    "host",
                    "metadata_cache",
                }:
                    continue
                h.update(f.name.encode())
                visit(getattr(x, f.name))
        elif isinstance(x, dict):
            for k in sorted(x):
                h.update(k.encode())
                visit(x[k])
        elif isinstance(x, (tuple, list)):
            h.update(str((type(x).__name__, len(x))).encode())
            for v in x:
                visit(v)
        else:
            h.update(json_bytes(x))

    visit(batch)
    return h.hexdigest()


def mutate_header(packet, mutate):
    n = int.from_bytes(packet[8:16], "little")
    header = json.loads(packet[16 : 16 + n])
    mutate(header)
    raw = json_bytes(header)
    return packet[:8] + len(raw).to_bytes(8, "little") + raw + packet[16 + n :]


def test_same_source_and_complete_batch_hash_without_rng_consumption(model, tmp_path):
    value = segment(model, terminal=True)
    enc = encoder(model, tmp_path)
    before = random.getstate(), torch.get_rng_state().clone()
    encoded = enc.encode(value)
    decoded, header = decode_packet(
        encoded.packet, envelope=encoded.envelope, compiler=enc.compiler
    )
    req = request([value])
    old = bp.assemble(req, (source(model, value),))
    new = bp.assemble(req, (decoded,))
    assert batch_digest(old) == batch_digest(new)
    assert before[0] == random.getstate() and torch.equal(before[1], torch.get_rng_state())
    assert enc.encode(value) == encoded
    assert header["envelope"]["original_commit"] == value["original_commit"]
    assert "observation" not in header["witness"]["steps"][0]["input"]


@pytest.mark.parametrize(
    "change,error",
    [
        (lambda h: h.update(schema="future"), "unknown_schema"),
        (lambda h: h["envelope"].pop("producer"), "envelope_fields"),
        (lambda h: h["envelope"].update(input_spec={"bogus": "incompatible"}), "input_spec"),
        (lambda h: h["source"]["fields"]["labels"].update(run_id="other"), "label_source"),
        (
            lambda h: h["witness"]["steps"][-1]["successor"]["observation"].update(
                player={"content": {"hp": 99}}
            ),
            "row_binding",
        ),
        (
            lambda h: h["source"]["fields"]["probabilities"]["fields"].update(offset=2**60),
            "offset_out_of_bounds",
        ),
        (
            lambda h: h["source"]["fields"]["frames"]["$tuple"][0]["fields"].update(
                choices={"$tuple": []}
            ),
            "catalog_or_probability",
        ),
        (lambda h: h["witness"].update(training_generation="other"), "membership"),
    ],
)
def test_invalid_descriptors_rejected(model, tmp_path, change, error):
    encoded = encoder(model, tmp_path).encode(segment(model))
    with pytest.raises(ValueError, match=error):
        decode_packet(mutate_header(encoded.packet, change))


def test_prefix_duplicate_fields_and_envelope_hash_rejected(model, tmp_path):
    encoded = encoder(model, tmp_path).encode(segment(model))
    with pytest.raises(ValueError, match="header_length"):
        decode_packet(TENSOR_SEGMENT_MAGIC + (2**40).to_bytes(8, "little") + b"x" * 20)
    n = int.from_bytes(encoded.packet[8:16], "little")
    duplicate = b'{"schema":"a",' + encoded.packet[17 : 16 + n]
    with pytest.raises(ValueError, match="duplicate_field"):
        decode_packet(
            encoded.packet[:8]
            + len(duplicate).to_bytes(8, "little")
            + duplicate
            + encoded.packet[16 + n :]
        )
    with pytest.raises(ValueError, match="envelope_binding"):
        decode_packet(encoded.packet, envelope=dict(encoded.envelope, packet_sha256="0" * 64))


def test_late_aux_uses_original_row_and_mu_and_rejects_new_generation(model, tmp_path):
    value = segment(model, terminal=True)
    enc = encoder(model, tmp_path)
    ppo = enc.encode(value)
    decoded, _ = decode_packet(ppo.packet)
    labels = {
        "run_id": value["run_id"],
        "training_generation": value["training_generation"],
        "episode": value["identity"]["intent_id"],
        "typed_original": "c" * 64,
        "outcome_states": [{"source": decoded.row_refs[2], "outcome": 3}],
        "segments": [{"segment_id": value["segment_id"]}],
        "hp_battles": [],
    }
    (packet,) = enc.auxiliary(labels)
    aux, header = decode_packet(packet.packet, envelope=packet.envelope, compiler=enc.compiler)
    assert packet.envelope["payload_kind"] == "aux" and packet.envelope["rows"] == 1
    assert aux.frames[0] is None and aux.frames[2].choices == decoded.frames[2].choices
    assert header["bindings"][2]["mu"] == value["mu"]
    bad = copy.deepcopy(labels)
    bad["training_generation"] = "next"
    with pytest.raises(ValueError, match="late_label_generation"):
        enc.auxiliary(bad)
    bad = copy.deepcopy(labels)
    bad["outcome_states"][0]["source"]["row"] = -1
    with pytest.raises(ValueError, match="row_binding"):
        enc.auxiliary(bad)
    assert enc.auxiliary(dict(labels, outcome_states=[], segments=[])) == ()


def test_spool_bound_and_immutable_identity(model, tmp_path):
    enc = encoder(model, tmp_path)
    value = segment(model)
    enc.max_spool_bytes = 1
    with pytest.raises(ValueError, match="spool_byte_limit"):
        enc.encode(value)
    assert not list(tmp_path.glob("*.stseg"))


def test_worker_header_fields_match_in_process_header_and_reject_size_change(model, tmp_path):
    encoded = encoder(model, tmp_path).encode(segment(model))
    path = tmp_path / "packet.bin"
    path.write_bytes(encoded.packet)
    header = packet_header(encoded.packet)
    assert packet_header_fields(str(path), len(encoded.packet), ("envelope",)) == {
        "envelope": header["envelope"]}
    with pytest.raises(ValueError, match="size_changed"):
        packet_header_fields(str(path), len(encoded.packet) + 1, ("envelope",))
