"""The real public projector, compiled wire and normal M-std remain identical."""
from __future__ import annotations

import base64
import copy
import gzip
import json
import math
from pathlib import Path

import pytest
import safetensors
import torch
from test_p1_input_r18 import claws, trial
from test_p1_input_r19 import sphere
from test_p1_input_v2 import public_state

from spireagent.json_boundary import BoundaryError
from stpd.p1 import frame_compiler
from stpd.p1.data import digest, json_bytes
from stpd.p1.execution import MStdExecution
from stpd.p1.export import adapter_identity
from stpd.p1.formal_training import MStdTrainer, Population
from stpd.p1.frame_compiler import FrameCompiler, observation_hashes
from stpd.p1.input import V2_INPUT_SPEC, V2_R18_INPUT_SPEC, V2_R19_INPUT_SPEC, project
from stpd.p1.model import MStd
from stpd.p1.numerics import TrainingNumerics
from stpd.p1.spec import MStdConfig
from stpd.p1.tensor_input import compile_frame
from stpd.p1.tensor_pack import compiler_identity
from stpd.p1.training import Trainer
from stpd.p1.vocabulary import public_vocabulary
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


def assert_frame_equal(left, right):
    assert left.choices == right.choices
    assert left.entity_count == right.entity_count
    assert left.tensors.keys() == right.tensors.keys()
    assert left.reductions.keys() == right.reductions.keys()
    for key, tensor in left.tensors.items():
        other = right.tensors[key]
        assert tensor.dtype == other.dtype and tensor.shape == other.shape
        assert torch.equal(tensor, other), key
    for key, reduction in left.reductions.items():
        other = right.reductions[key]
        assert torch.equal(reduction.inverse, other.inverse), key
        for before, after in zip(reduction.buckets, other.buckets, strict=True):
            assert before[:2] == after[:2]
            assert before[2].dtype == after[2].dtype
            assert torch.equal(before[2], after[2]), key


@pytest.mark.parametrize("source,spec", [
    (public_state, V2_INPUT_SPEC), (trial, V2_R18_INPUT_SPEC),
    (claws, V2_R18_INPUT_SPEC), (sphere, V2_R19_INPUT_SPEC),
])
def test_weight_free_compile_and_wire_match_real_original_catalog(source, spec, monkeypatch):
    observation = source()
    vocabulary = public_vocabulary(spec)
    with TrainingNumerics(1, 2, torch.device("cpu")).scope():
        model = MStd(vocabulary)
        reference = compile_frame(project(observation, input_projection=spec), model)
        cached_tokens = copy.deepcopy(model._tokens)
        before = torch.get_rng_state().clone()

        def no_model(*args, **kwargs):
            raise AssertionError("weight-free compiler constructed MStd")

        monkeypatch.setattr(MStd, "__init__", no_model)
        compiler = FrameCompiler(vocabulary)
        encoded = json.loads(json.dumps(compiler.encode(observation)))
        compiled = compiler.compile(observation)
        assert torch.equal(before, torch.get_rng_state())
        assert model._tokens == cached_tokens
        assert_frame_equal(reference, compiled)

        def no_recompile(*args, **kwargs):
            raise AssertionError("decode projected/compiled the raw observation")

        monkeypatch.setattr(frame_compiler, "project", no_recompile)
        monkeypatch.setattr(frame_compiler, "compile_frame", no_recompile)
        decoded = compiler.decode(encoded, observation=observation)
        assert_frame_equal(reference, decoded)
        assert decoded.host is None and decoded.metadata_cache == {}
        assert_frame_equal(decoded, compiler.decode(encoded, **observation_hashes(observation)))
        assert torch.equal(before, torch.get_rng_state())


def test_real_numeric_missing_empty_unicode_and_rl4_forward_are_bitwise_equal():
    observation = public_state()
    observation["scene"]["content"].update(
        prompt="新的说明 🃏: Gain -2.5 Block and +3 energy.",
        empty="", missing=None, zero=0, negative=-17.5,
        aliases=["e1", "e1", "e2"],
    )
    vocabulary = public_vocabulary()
    with TrainingNumerics(13, 2, torch.device("cpu")).scope():
        model = MStd(vocabulary, MStdExecution.for_plan("mstd-training-v1")).eval()
        model.enable_rl4_heads(19)
        with torch.no_grad():
            model.rl4_heads.outcome.mlp[-1].weight.normal_(0, .02)
            model.rl4_heads.hp.mlp[-1].weight.normal_(0, .02)
        compiler = FrameCompiler(vocabulary)
        assert compiler._encoding.index("ids", "never-published-id") == 0
        assert compiler._encoding.index("ids", "BASH") == vocabulary["ids"].index("BASH")
        empty = compiler._encoding.tokenize("")
        assert empty == ([model.tokenizer.token_to_id("<EMPTY>")], [0.])
        text = "新的说明 🃏: Gain -2.5 Block and +3 energy."
        ids, numbers = compiler._encoding.tokenize(text)
        assert (ids, numbers) == model.tokenize(text)
        number_token = model.tokenizer.token_to_id("<NUM>")
        assert [number for token, number in zip(ids, numbers, strict=True)
                if token == number_token] == [-math.log1p(2.5), math.log1p(3)]
        frame = project(observation, input_projection=V2_INPUT_SPEC)
        original = compile_frame(frame, model)
        decoded = compiler.decode(compiler.encode(observation), observation=observation)
        assert_frame_equal(original, decoded)
        with torch.inference_mode():
            expected = model.forward_rl4([frame], [3])
            actual = model.forward_rl4([decoded], [3])
        assert all(torch.equal(left, right)
                   for left, right in zip(expected[0], actual[0], strict=True))
        assert torch.equal(expected[1], actual[1])
        assert torch.equal(expected[2], actual[2])
        identity = compiler.identity
        with torch.no_grad():
            model.ids.weight.add_(1)
        assert FrameCompiler(vocabulary).identity == identity
        assert set(decoded.state()) == {"tensors", "reductions", "choices", "entity_count"}


def test_zero_text_and_catalog_lengths_survive_the_same_codec():
    observation = {"profile": "p1-global-r1", "revision": "r1.5", "catalog": []}
    compiler = FrameCompiler(public_vocabulary())
    frame = compiler.compile(observation)
    assert frame.choices == () and frame.tensors["text_ids"].shape == (0, 0)
    decoded = compiler.decode(compiler.encode(observation), observation=observation)
    assert_frame_equal(frame, decoded)


def seal(wire):
    wire["record_sha256"] = digest(json_bytes(
        {key: value for key, value in wire.items() if key != "record_sha256"}))
    return wire


def replace_frame(wire, edit):
    wire = copy.deepcopy(wire)
    state = decode_checkpoint(gzip.decompress(base64.b64decode(wire["data"])))
    edit(state)
    raw = encode_checkpoint(state)
    wire["data"] = base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")
    wire["frame_sha256"] = digest(raw)
    return seal(wire)


def test_identity_input_catalog_and_payload_changes_are_typed_rejections(monkeypatch):
    observation = public_state()
    vocabulary = public_vocabulary()
    compiler = FrameCompiler(vocabulary)
    wire = compiler.encode(observation)
    changed = copy.deepcopy(observation)
    changed["catalog"].reverse()
    with pytest.raises(BoundaryError, match="public_input_hash_mismatch"):
        compiler.decode(wire, observation=changed)
    with pytest.raises(BoundaryError, match="public_input_hash_mismatch"):
        compiler.decode(wire, catalog_sha256="0" * 64)
    changed = copy.deepcopy(wire)
    changed["data"] = "AAAA" + changed["data"][4:]
    with pytest.raises(BoundaryError, match="wire_hash_mismatch"):
        compiler.decode(changed, observation=observation)
    changed = copy.deepcopy(wire)
    changed["frame_sha256"] = "0" * 64
    with pytest.raises(BoundaryError, match="frame_hash_mismatch"):
        compiler.decode(seal(changed))
    changed = copy.deepcopy(wire)
    changed["data"] = base64.b64encode(b"invalid compressed payload").decode("ascii")
    with pytest.raises(BoundaryError, match="invalid_frame_payload"):
        compiler.decode(seal(changed))
    for other in (public_vocabulary(V2_R19_INPUT_SPEC),
                  {**vocabulary, "actions": vocabulary["actions"] + ["public-extension"]}):
        with pytest.raises(BoundaryError, match="compiler_identity_mismatch"):
            FrameCompiler(other).decode(wire)
    monkeypatch.setattr(frame_compiler, "compiler_digest", lambda: "0" * 64)
    with pytest.raises(BoundaryError, match="compiler_identity_mismatch"):
        FrameCompiler(vocabulary).decode(wire)
    # External mutation cannot silently alter an already constructed compiler.
    vocabulary["actions"].append("later-extension")
    returned_identity = compiler.identity
    returned_identity["input_spec"]["revision"] = "bad"
    assert_frame_equal(compiler.compile(observation),
                       compiler.decode(wire, observation=observation))


@pytest.mark.parametrize("edit,reason", [
    (lambda state: state["tensors"].update(actions=torch.tensor([999999, 1])),
     "invalid_frame_index"),
    (lambda state: state["tensors"].update(numbers=state["tensors"]["numbers"].double()),
     "invalid_frame_tensor"),
    (lambda state: state["tensors"].update(text_lengths=torch.tensor([999999])),
     "invalid_text_lengths"),
    (lambda state: state["reductions"]["refs"].update(inverse=torch.tensor([0, 0])),
     "invalid_reduction_structure"),
    (lambda state: state.update(choices=tuple(reversed(state["choices"]))),
     "catalog_choice_binding_mismatch"),
])
def test_rehashed_but_invalid_frame_cannot_reach_model(edit, reason):
    compiler = FrameCompiler(public_vocabulary())
    observation = public_state()
    wire = replace_frame(compiler.encode(observation), edit)
    with pytest.raises(BoundaryError, match=reason):
        compiler.decode(wire, observation=observation)


def test_compressed_payload_limit_is_applied_before_decode(monkeypatch):
    compiler = FrameCompiler(public_vocabulary())
    wire = compiler.encode(public_state())
    monkeypatch.setattr(frame_compiler, "MAX_FRAME_BYTES", 64)
    wire["data"] = base64.b64encode(gzip.compress(b"a" * 1024, mtime=0)).decode("ascii")
    with pytest.raises(BoundaryError, match="frame_size_limit"):
        compiler.decode(seal(wire))


def test_extracted_encoding_changes_every_existing_executable_identity(monkeypatch):
    vocabulary = public_vocabulary()
    frame = project(public_state(), input_projection=V2_INPUT_SPEC)
    population = Population((frame,), (0,), ("point-1",))

    def identities():
        formal = MStdTrainer({"train": population,
                              "dev": Population((frame,), (0,), ("dev-1",)),
                              "test": Population((), (), ())}, vocabulary, "a" * 64,
                             MStdConfig(max_epochs=2))
        legacy = Trainer([frame], [0], vocabulary, "a" * 64, 2)
        return {"adapter": adapter_identity()["code_sha256"],
                "formal": formal.identity["code"], "legacy": legacy.identity["code"],
                "pack": compiler_identity(), "wire": frame_compiler.compiler_digest()}

    before = identities()
    read_bytes = Path.read_bytes

    def changed_encoding(path):
        raw = read_bytes(path)
        return raw + b"\n# alternate encoding source\n" if path.name == "input_encoding.py" else raw

    monkeypatch.setattr(Path, "read_bytes", changed_encoding)
    after = identities()
    assert before.keys() == after.keys()
    assert all(before[key] != after[key] for key in before)
    for key in ("formal", "legacy", "pack"):
        assert before[key]["input_encoding.py"] != after[key]["input_encoding.py"]


@pytest.mark.parametrize("dependency", ["json_boundary.py", "encoding.py", "safetensors"])
def test_wire_identity_tracks_its_actual_codec_dependencies(monkeypatch, dependency):
    before = frame_compiler.compiler_digest()
    if dependency == "safetensors":
        monkeypatch.setattr(safetensors, "__version__", "different-codec-version")
    else:
        read_bytes = Path.read_bytes

        def changed_codec(path):
            raw = read_bytes(path)
            if path.name == dependency and path.parent.name == "spireagent":
                return raw + b"\n# alternate codec source\n"
            return raw

        monkeypatch.setattr(Path, "read_bytes", changed_codec)
    assert frame_compiler.compiler_digest() != before
