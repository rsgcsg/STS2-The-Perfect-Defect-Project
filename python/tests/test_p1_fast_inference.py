"""rl-inference-3 service + node aggregator, end to end on CPU (P-REWRITE WP4)."""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from dataclasses import dataclass
from multiprocessing.connection import Client
from typing import Any

import pytest
import torch
from test_p1_input_v2 import public_state

from spireagent.json_boundary import BoundaryError
from stpd.p1.data import digest, json_bytes
from stpd.p1.fast_aggregator import LINK_SCHEMA, NodeAggregator
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.inference_service import (
    V3_MAX_MESSAGE,
    V3_SCHEMA,
    FastInferenceService,
    audit_pick,
    pack_message,
    unpack_message,
)
from stpd.p1.input import V2_INPUT_SPEC
from stpd.p1.model import MStd, MStdExecution
from stpd.p1.rows_encoder import RowsEncoder
from stpd.p1.rows_encoder_wire import decode_row
from stpd.p1.rows_scorer import RowsScorer
from stpd.p1.sampling import distribution, draw
from stpd.p1.vocabulary import public_vocabulary

ENV = "e" * 64
MU = "a" * 64
SAMPLING = {"mode": "temperature_uniform", "seed": "draw-seed-1", "arm_id": "control",
            "classes": {"combat": {"temperature": 1.0, "uniform_mix": 0.0},
                        "noncombat": {"temperature": 1.5, "uniform_mix": 0.05}}}


@dataclass(frozen=True)
class Handoff:
    snapshot_sha256: str
    training_generation: str = "gen-1"
    version: int = 1
    updates: int = 0


@dataclass(frozen=True)
class Offer:
    accepted: bool
    evicted: tuple[str, ...] = ()
    generation_changed: bool = False


@dataclass(frozen=True)
class Plan:
    game_instance_id: str
    request_id: int
    step_index: int
    segment_id: str
    row_in_segment: int
    mu: Handoff
    tail: Any = None


class FakeAssembler:
    """The WP5 call surface with one segment per game; records every call."""

    def __init__(self) -> None:
        self.games: dict[str, list[Plan]] = {}
        self.calls: list[tuple[str, Any]] = []
        self.mu: Handoff | None = None
        self.lock = threading.Lock()

    def offer_mu(self, handoff: Handoff) -> Offer:
        self.mu = handoff
        return Offer(True)

    def open_episode(self, message: dict[str, Any]) -> None:
        self.games[message["game_instance_id"]] = []
        self.calls.append(("open", message["game_instance_id"]))

    def begin_row(self, row: dict[str, Any], row_bytes: bytes) -> Plan:
        if row["game_instance_id"] not in self.games:
            raise ValueError("episode_not_open")
        assert self.mu is not None
        plans = self.games[row["game_instance_id"]]
        plan = Plan(row["game_instance_id"], row["request_id"], row["step_index"],
                    f"seg-{row['game_instance_id']}", len(plans), self.mu)
        plans.append(plan)
        return plan

    def finish_row(self, plan: Plan, scored: dict[str, Any], values: list[float],
                   tail: list[float] | None = None) -> None:
        with self.lock:
            self.calls.append(("finish", (plan.request_id, scored, values)))

    def abort_row(self, plan: Plan, code: str) -> None:
        with self.lock:
            self.calls.append(("abort", (plan.request_id, code)))

    def step_outcome(self, message: dict[str, Any]) -> None:
        self.calls.append(("outcome", message))

    def close_episode(self, message: dict[str, Any]) -> None:
        self.calls.append(("close", message))
        self.games.pop(message["game_instance_id"], None)


@pytest.fixture(scope="module")
def vocabulary() -> dict[str, Any]:
    return public_vocabulary(V2_INPUT_SPEC)


@pytest.fixture(scope="module")
def model(vocabulary: dict[str, Any]) -> MStd:
    torch.manual_seed(3)
    model = MStd(vocabulary, MStdExecution(
        implementation="experimental-packed-v1", microbatch_size=4,
        scene_attention_area=67108864, text_attention_area=67108864, cpu_threads=1))
    model.enable_rl4_heads(7)
    return model.eval().requires_grad_(False)


def _service(vocabulary: dict[str, Any], model: MStd, assembler: Any,
             **options: Any) -> FastInferenceService:
    identity = FrameCompiler(vocabulary).identity
    service = FastInferenceService(vocabulary, identity, assembler, training_generation="gen-1",
                                   address=("127.0.0.1", 0), env_sha256=ENV,
                                   prepare_workers=1, **options)
    assert service.publish(model, Handoff(MU))
    return service


def _observations(count: int) -> list[dict[str, Any]]:
    out = []
    for index in range(count):
        observation = public_state()
        observation["status"] = {"hp": index, "cards": [{"id": index, "x": [1]}]}
        out.append(observation)
    return out


def _decision_line(row_id: int, game: str, step: int, observation: dict[str, Any], *,
                   catalog_len: int | None = None) -> bytes:
    """The WP2 kernel's field order: the routing head first, then the input."""
    catalog = observation["catalog"]
    head = (f'{{"schema":"{LINK_SCHEMA}","type":"decision","row_id":{row_id},'
            f'"game_instance_id":"{game}","step_index":{step},"consumption_id":"c-{game}-{step}",'
            f'"catalog_len":{len(catalog) if catalog_len is None else catalog_len},'
            f'"catalog_sha256":"{digest(json_bytes(catalog))}","observation_sha256":"{"0" * 64}",'
            f'"in_combat":false,"floor":3,')
    body = json.dumps({"input_spec": V2_INPUT_SPEC, "observation": observation,
                       "catalog": catalog})
    return (head + '"input":' + body + "}\n").encode()


class FakeEnv:
    def __init__(self, path: str) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(path)
        self.reader = self.socket.makefile("rb")

    def send(self, message: dict[str, Any]) -> None:
        self.socket.sendall(json.dumps({"schema": LINK_SCHEMA, **message}).encode() + b"\n")

    def reply(self) -> dict[str, Any]:
        self.socket.settimeout(60)
        return json.loads(self.reader.readline())


def _aggregator(vocabulary: dict[str, Any], service: FastInferenceService, path: str,
                **options: Any) -> NodeAggregator:
    return NodeAggregator(vocabulary, service_address=service.address, authkey=service.auth,
                          socket_path=path, aggregator_id="node-1", python_env_sha256=ENV,
                          encoder_workers=1, **options)


def test_message_codec_round_trip_and_truncation():
    data = pack_message({"schema": V3_SCHEMA, "kind": "x"}, b"\x00\x01")
    envelope, binary = unpack_message(data)
    assert envelope == {"schema": V3_SCHEMA, "kind": "x"} and bytes(binary) == b"\x00\x01"
    for bad in (data[:3], data[:8]):
        with pytest.raises(BoundaryError, match="message_truncated"):
            unpack_message(bad)
    with pytest.raises(BoundaryError, match="envelope_schema"):
        unpack_message(pack_message({"schema": "other"}))


def test_node_aggregator_scores_rows_end_to_end(vocabulary, model):
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler)
    path = os.path.join(tempfile.mkdtemp(), "link.sock")
    aggregator = _aggregator(vocabulary, service, path)
    try:
        env = FakeEnv(path)
        env.send({"type": "hello", "env_process_id": "env-1", "slots": 2, "p1_revision": "x",
                  "input_spec": V2_INPUT_SPEC})
        observations = _observations(3)
        for game in ("g1", "g2"):
            env.send({"type": "episode_open", "game_instance_id": game, "actor_id": "actor-1",
                      "sampling": SAMPLING, "single_choice": "score", "start": {"kind": "new_run"}})
        replies = {}
        row_id = 0
        for step, observation in enumerate(observations):
            sent = {}
            for game in ("g1", "g2"):
                row_id += 1
                env.socket.sendall(_decision_line(row_id, game, step, observation))
                sent[row_id] = game
            for _ in sent:
                reply = env.reply()
                replies[reply["row_id"]] = reply
            for rid, game in sent.items():
                env.send({"type": "step_outcome", "row_id": rid, "game_instance_id": game,
                          "step_index": step, "status": "completed", "receipt_sha256": "b" * 64,
                          "public_facts": {"act": 1, "floor": 3, "hp": 50, "max_hp": 70},
                          "terminal": None})
        for game in ("g1", "g2"):
            env.send({"type": "episode_close", "game_instance_id": game, "reason": "terminal",
                      "last_step_index": 2})

        # Same scores and draw as the scorer run directly on the reference frames.
        compiler = FrameCompiler(vocabulary)
        frames = [compiler.compile(observation) for observation in observations]
        expected = RowsScorer(model, bf16=False).score(frames, [3] * len(frames))
        policy = SAMPLING["classes"]["noncombat"]
        for rid, reply in replies.items():
            step = (rid - 1) // 2
            assert reply["type"] == "scored" and reply["schema"] == LINK_SCHEMA
            _, q = distribution(expected[step].scores, policy["temperature"],
                                policy["uniform_mix"])
            game = "g1" if rid % 2 else "g2"
            assert reply["game_instance_id"] == game
            assert reply["catalog_sha256"] == digest(json_bytes(observations[step]["catalog"]))
            assert reply["behavior_q"] == pytest.approx(q, abs=1e-6)
            assert reply["selected_index"] == draw(reply["behavior_q"], SAMPLING["seed"],
                                                   f"c-{game}-{step}")
            assert reply["selected_q"] == reply["behavior_q"][reply["selected_index"]]
            assert reply["applied"] == {"arm_id": "control", "decision_class": "noncombat",
                                        "temperature": 1.5, "uniform_mix": 0.05}
            assert reply["mu_snapshot_sha256"] == MU and reply["row_in_segment"] == step
        deadline = threading.Event()
        for _ in range(200):
            if sum(kind == "close" for kind, _ in assembler.calls) == 2:
                break
            deadline.wait(0.05)
        outcomes = [body for kind, body in assembler.calls if kind == "outcome"]
        assert len(outcomes) == 6
        # step_outcome carries the service-side request id of its row.
        finished = {body[0] for kind, body in assembler.calls if kind == "finish"}
        assert {o["row_request_id"] for o in outcomes} == finished
        assert all(o["schema"] == V3_SCHEMA and "row_id" not in o for o in outcomes)
    finally:
        stats = aggregator.close()
        report = service.close()
    assert stats["rows"] == 6 and stats["failure"] is None and stats["pending"] == 0
    assert report["rows"] == 6 and report["refused"] == {}


def test_binding_difference_is_refused_before_the_service(vocabulary, model):
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler)
    path = os.path.join(tempfile.mkdtemp(), "link.sock")
    aggregator = _aggregator(vocabulary, service, path)
    try:
        env = FakeEnv(path)
        env.send({"type": "hello", "env_process_id": "env-1", "slots": 1, "p1_revision": "x",
                  "input_spec": V2_INPUT_SPEC})
        env.send({"type": "episode_open", "game_instance_id": "g1", "actor_id": "actor-1",
                  "sampling": SAMPLING, "single_choice": "score", "start": {"kind": "new_run"}})
        observation = _observations(1)[0]
        env.socket.sendall(_decision_line(1, "g1", 0, observation, catalog_len=9))
        reply = env.reply()
        assert reply == {"schema": LINK_SCHEMA, "type": "refused", "row_id": 1,
                         "game_instance_id": "g1", "error_code": "IDENTITY_MISMATCH",
                         "catalog_sha256": digest(json_bytes(observation["catalog"]))}
        broken = _decision_line(2, "g1", 1, observation).replace(b'"input":{', b'"input":{,')
        env.socket.sendall(broken)
        assert env.reply()["error_code"] == "ROW_DECODE_FAILED"
    finally:
        stats = aggregator.close()
        service.close()
    assert stats["refused_here"] == {"IDENTITY_MISMATCH": 1, "ROW_DECODE_FAILED": 1}
    assert stats["rows"] == 0
    assert not any(kind == "finish" for kind, _ in assembler.calls)


def _attach(service: FastInferenceService, encoder_identity: dict[str, Any],
            changes: dict[str, Any] | None = None) -> Any:
    client = Client(service.address, authkey=service.auth)
    identity = RowsEncoder(service.vocabulary).identity
    message = {"schema": V3_SCHEMA, "kind": "attach", "request_id": 1, "aggregator_id": "n",
               "chain": "fast_chain", "python_env_sha256": ENV,
               "compiler_identity": service.compiler_identity, "encoder": encoder_identity,
               "row_format": identity["row_format"], **(changes or {})}
    client.send_bytes(pack_message(message))
    return client


def test_attach_refuses_any_identity_difference(vocabulary, model):
    service = _service(vocabulary, model, FakeAssembler())
    encoder = RowsEncoder(vocabulary).identity["encoder"]
    try:
        for changes in ({"encoder": {**encoder, "sha256": "f" * 64}},
                        {"row_format": "stpd/rows-v2-row-0"}, {"python_env_sha256": "d" * 64},
                        {"compiler_identity": {**service.compiler_identity,
                                               "compiler_sha256": "c" * 64}}):
            client = _attach(service, encoder, changes)
            with pytest.raises(EOFError):
                client.recv_bytes(V3_MAX_MESSAGE)  # closed loudly, never attached
            client.close()
        client = _attach(service, encoder)
        envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
        assert envelope["kind"] == "attached" and envelope["max_rows_per_packet"] == 1024
        client.close()
    finally:
        report = service.close()
    assert report["attach_refused"] == 4


def test_many_connections_share_batches_without_starvation(vocabulary, model):
    """48 connections (above the old 32) each get their row scored; batches span them."""
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler, window_ms=50.0)
    encoder = RowsEncoder(vocabulary)
    observation = _observations(1)[0]
    encoded = encoder.encode(json.dumps(observation).encode())
    clients = []
    try:
        for index in range(48):
            client = _attach(service, encoder.identity["encoder"])
            assert unpack_message(client.recv_bytes(V3_MAX_MESSAGE))[0]["kind"] == "attached"
            game = f"g{index}"
            client.send_bytes(pack_message({
                "schema": V3_SCHEMA, "kind": "episode_open", "request_id": 2,
                "game_instance_id": game, "actor_id": "a", "sampling": SAMPLING,
                "single_choice": "score", "start": {"kind": "new_run"}}))
            clients.append((client, game))
        for client, game in clients:
            row = {"request_id": 3, "game_instance_id": game, "step_index": 0,
                   "consumption_id": "c", "catalog_len": encoded.catalog_len,
                   "catalog_sha256": encoded.catalog_sha256,
                   "observation_sha256": encoded.observation_sha256, "in_combat": False,
                   "floor": 2, "payload": {"offset": 0, "length": len(encoded.row),
                                           "row_sha256": encoded.row_sha256}}
            client.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                            "packet_id": 1, "rows": [row]}, encoded.row))
        for client, game in clients:
            envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
            [result] = envelope["results"]
            assert result["status"] == "scored" and result["game_instance_id"] == game
    finally:
        for client, _ in clients:
            client.close()
        report = service.close()
    assert report["rows"] == 48 and report["batch_rows_max"] > 8


def test_bad_row_bytes_and_unknown_game_are_refused(vocabulary, model):
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler)
    encoder = RowsEncoder(vocabulary)
    encoded = encoder.encode(json.dumps(_observations(1)[0]).encode())
    client = _attach(service, encoder.identity["encoder"])
    try:
        unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
        client.send_bytes(pack_message({
            "schema": V3_SCHEMA, "kind": "episode_open", "request_id": 2,
            "game_instance_id": "g1", "actor_id": "a", "sampling": SAMPLING,
            "single_choice": "score", "start": {"kind": "new_run"}}))
        garbage = b"\x00" * 40
        rows = []
        for request_id, game, data, catalog_len in ((3, "g1", garbage, 2),
                                                    (4, "nope", encoded.row, 2),
                                                    (5, "g1", encoded.row, 5),
                                                    (6, "g1", encoded.row, 0)):
            rows.append({"request_id": request_id, "game_instance_id": game, "step_index": 0,
                         "consumption_id": "c", "catalog_len": catalog_len,
                         "catalog_sha256": encoded.catalog_sha256,
                         "observation_sha256": encoded.observation_sha256, "in_combat": False,
                         "floor": 0, "payload": {"offset": 0, "length": len(data),
                                                 "row_sha256": digest(data)}})
        for row, data in zip(rows, (garbage, encoded.row, encoded.row, encoded.row),
                             strict=True):
            client.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                            "packet_id": row["request_id"], "rows": [row]},
                                           data))
        codes = {}
        for _ in rows:
            envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
            codes[envelope["packet_id"]] = envelope["results"][0]["error_code"]
        assert codes == {3: "ROW_DECODE_FAILED", 4: "EPISODE_NOT_OPEN", 5: "IDENTITY_MISMATCH",
                         6: "IDENTITY_MISMATCH"}
    finally:
        client.close()
        service.close()
    aborted = {body[0]: body[1] for kind, body in assembler.calls if kind == "abort"}
    assert aborted == {3: "ROW_DECODE_FAILED", 5: "IDENTITY_MISMATCH"}


def _open_client(service: FastInferenceService, encoder: RowsEncoder, game: str) -> Any:
    client = _attach(service, encoder.identity["encoder"])
    assert unpack_message(client.recv_bytes(V3_MAX_MESSAGE))[0]["kind"] == "attached"
    client.send_bytes(pack_message({
        "schema": V3_SCHEMA, "kind": "episode_open", "request_id": 2, "game_instance_id": game,
        "actor_id": "a", "sampling": SAMPLING, "single_choice": "score",
        "start": {"kind": "new_run"}}))
    return client


def _score(client: Any, encoded: Any, request_id: int, game: str, *,
           row_sha256: str | None = None, floor: int = 2) -> dict[str, Any]:
    row = {"request_id": request_id, "game_instance_id": game, "step_index": request_id,
           "consumption_id": f"c{request_id}", "catalog_len": encoded.catalog_len,
           "catalog_sha256": encoded.catalog_sha256,
           "observation_sha256": encoded.observation_sha256, "in_combat": False,
           "floor": floor, "payload": {"offset": 0, "length": len(encoded.row),
                                       "row_sha256": row_sha256 or encoded.row_sha256}}
    client.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                    "packet_id": request_id, "rows": [row]}, encoded.row))
    envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
    [result] = envelope["results"]
    return dict(result)


def test_row_hash_is_a_spot_check_and_a_mismatch_stops_the_build(vocabulary, model):
    """Design §2.7: the hot path does not recompute row_sha256; an audited row does,
    and a difference stops this service's output with the original kept."""
    encoder = RowsEncoder(vocabulary)
    encoded = encoder.encode(json.dumps(_observations(1)[0]).encode())
    wrong = "f" * 64
    service = _service(vocabulary, model, FakeAssembler(), audit_fraction=0.0)
    client = _open_client(service, encoder, "g1")
    try:
        assert _score(client, encoded, 3, "g1", row_sha256=wrong)["status"] == "scored"
    finally:
        client.close()
        report = service.close()
    assert report["audited"] == 0 and report["audit_failures"] == 0

    with tempfile.TemporaryDirectory() as failures:
        assembler = FakeAssembler()
        service = _service(vocabulary, model, assembler, audit_fraction=0.0,
                           failure_dir=failures)
        service.mark_full_audit("g1")  # an R7 game: every row audited
        client = _open_client(service, encoder, "g1")
        try:
            assert _score(client, encoded, 3, "g1")["status"] == "scored"
            refused = _score(client, encoded, 4, "g1", row_sha256=wrong)
            assert refused["error_code"] == "ROW_DECODE_FAILED"
            after = _score(client, encoded, 5, "g1")
            assert after["error_code"] == "SERVICE_STOPPING"
        finally:
            client.close()
            report = service.close()
        kept = sorted(os.listdir(failures))
        assert len(kept) == 2 and kept[0].endswith(".json") and kept[1].endswith(".row")
        with open(os.path.join(failures, kept[1]), "rb") as handle:
            assert handle.read() == encoded.row
        with open(os.path.join(failures, kept[0]), "rb") as handle:
            mismatch = json.loads(handle.read())["mismatch"]
        assert mismatch["expected_row_sha256"] == wrong
        assert mismatch["recomputed_row_sha256"] == encoded.row_sha256
        assert service.audit_count("g1") == 2
    assert report["audited"] == 2 and report["audit_failures"] == 1

    aborted = {body[0]: body[1] for kind, body in assembler.calls if kind == "abort"}
    assert aborted == {4: "ROW_DECODE_FAILED"}


def test_audit_pick_is_reproducible_and_about_the_fraction():
    threshold = int(0.01 * 2 ** 64)
    picks = [audit_pick(f"g{game}", step, threshold) for game in range(100) for step in range(100)]
    assert picks == [audit_pick(f"g{game}", step, threshold)
                     for game in range(100) for step in range(100)]
    assert 60 <= sum(picks) <= 140


@dataclass(frozen=True)
class Tail:
    segment_id: str
    mu: str
    floor: int


class TailAssembler(FakeAssembler):
    """Every row asks for the tail value of another mu (a segment boundary)."""

    def __init__(self, tail_mu: str) -> None:
        super().__init__()
        self.tail_mu = tail_mu

    def begin_row(self, row: dict[str, Any], row_bytes: bytes) -> Plan:
        plan = super().begin_row(row, row_bytes)
        return Plan(plan.game_instance_id, plan.request_id, plan.step_index, plan.segment_id,
                    plan.row_in_segment, plan.mu, Tail("seg-old", self.tail_mu, 5))

    def finish_row(self, plan: Plan, scored: dict[str, Any], values: list[float],
                   tail: list[float] | None = None) -> None:
        with self.lock:
            self.calls.append(("finish", (plan.request_id, scored, values, tail)))


def test_tail_value_uses_the_previous_mu_at_the_tail_floor(vocabulary, model):
    torch.manual_seed(11)
    old = MStd(vocabulary, MStdExecution(
        implementation="experimental-packed-v1", microbatch_size=4,
        scene_attention_area=67108864, text_attention_area=67108864, cpu_threads=1))
    old.enable_rl4_heads(7)
    old = old.eval().requires_grad_(False)
    old_mu = "b" * 64
    encoder = RowsEncoder(vocabulary)
    encoded = encoder.encode(json.dumps(_observations(1)[0]).encode())
    frame = decode_row(encoded.row, validator=FrameCompiler(vocabulary),
                       expected_sha256=encoded.row_sha256)
    expected = RowsScorer(old).score([frame], [5])[0]
    own = RowsScorer(model).score([frame], [2])[0]
    assert expected is not None and own is not None

    assembler = TailAssembler(old_mu)
    service = _service(vocabulary, model, assembler)
    assert service.publish(old, Handoff(old_mu))
    client = _open_client(service, encoder, "g1")
    try:
        assert _score(client, encoded, 3, "g1")["status"] == "scored"
    finally:
        client.close()
        service.close()
    [(_, _, values, tail)] = [body for kind, body in assembler.calls if kind == "finish"]
    assert torch.allclose(torch.tensor(tail), torch.tensor(expected.outcome_probabilities),
                          atol=1e-6)
    assert torch.allclose(torch.tensor(values), torch.tensor(own.outcome_probabilities),
                          atol=1e-6)

    assembler = TailAssembler("c" * 64)  # never published
    service = _service(vocabulary, model, assembler)
    client = _open_client(service, encoder, "g1")
    try:
        assert _score(client, encoded, 3, "g1")["error_code"] == "MU_NOT_RESIDENT"
    finally:
        client.close()
        service.close()
    assert [body for kind, body in assembler.calls if kind == "abort"] == [
        (3, "MU_NOT_RESIDENT")]


@pytest.mark.parametrize(("arena_row_bytes", "join_workers"),
                         [(256 * 1024, 0), (None, 0), (None, 2)])
def test_job_parts_are_joined_into_one_forward_with_the_same_scores(vocabulary, model,
                                                                     arena_row_bytes,
                                                                     join_workers):
    """Two prepare workers each prepare half a job: written into the shared arena
    and staged by `stage_forward_parts` (default), or joined by concatenate_groups
    in the device process (0) or in a joiner process (2)."""
    encoder = RowsEncoder(vocabulary)
    compiler = FrameCompiler(vocabulary)
    observations = _observations(6)
    encoded = [encoder.encode(json.dumps(item).encode()) for item in observations]
    frames = [decode_row(e.row, validator=compiler, expected_sha256=e.row_sha256)
              for e in encoded]
    expected = RowsScorer(model).score(frames, [2] * len(frames))
    assembler = FakeAssembler()
    identity = FrameCompiler(vocabulary).identity
    service = FastInferenceService(vocabulary, identity, assembler, training_generation="gen-1",
                                   address=("127.0.0.1", 0), env_sha256=ENV,
                                   prepare_workers=2, job_parts=2, window_ms=200.0,
                                   join_workers=join_workers,
                                   arena_row_bytes=arena_row_bytes)
    assert service.publish(model, Handoff(MU))
    client = _open_client(service, encoder, "g1")
    try:
        rows, binary = [], bytearray()
        for index, item in enumerate(encoded):
            rows.append({"request_id": 3 + index, "game_instance_id": "g1", "step_index": index,
                         "consumption_id": f"c{index}", "catalog_len": item.catalog_len,
                         "catalog_sha256": item.catalog_sha256,
                         "observation_sha256": item.observation_sha256, "in_combat": False,
                         "floor": 2, "payload": {"offset": len(binary), "length": len(item.row),
                                                 "row_sha256": item.row_sha256}})
            binary += item.row
        client.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                        "packet_id": 1, "rows": rows}, binary))
        envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
    finally:
        client.close()
        report = service.close()
    assert [result["status"] for result in envelope["results"]] == ["scored"] * 6
    assert report["jobs"] == 1 and report["batches"] == 1 and report["batch_rows_max"] == 6
    finished = {body[0]: body[1] for kind, body in assembler.calls if kind == "finish"}
    for index, want in enumerate(expected):
        assert want is not None
        _, behavior = distribution(want.scores, 1.5, 0.05)
        got = finished[3 + index]["behavior_q"]
        assert torch.allclose(torch.tensor(got), torch.tensor(behavior), atol=1e-6)


@pytest.mark.parametrize(("arena_row_bytes", "join_workers"),
                         [(256 * 1024, 0), (None, 1)])
def test_a_dead_prepare_worker_stops_the_service_loudly(vocabulary, model, arena_row_bytes,
                                                        join_workers):
    encoder = RowsEncoder(vocabulary)
    encoded = encoder.encode(json.dumps(_observations(1)[0]).encode())
    service = _service(vocabulary, model, FakeAssembler(), join_workers=join_workers,
                       arena_row_bytes=arena_row_bytes)
    client = _open_client(service, encoder, "g1")
    try:
        assert _score(client, encoded, 3, "g1")["status"] == "scored"
        service.workers[0][0].kill()
        service.device_process.join(30)
        assert not service.device_process.is_alive()
        service.threads[2].join(30)
        assert _score(client, encoded, 4, "g1")["error_code"] == "SERVICE_STOPPING"
    finally:
        client.close()
        service.close()


def test_publish_ships_a_model_that_carries_a_compiled_block(vocabulary, model):
    """A learner-side compiled rows block is a runtime cache, never shipped."""
    def unpicklable() -> None:
        pass

    model._rows_block_compiled = unpicklable  # stand-in: a local function cannot pickle
    try:
        service = _service(vocabulary, model, FakeAssembler())
        service.close()
        assert model._rows_block_compiled is unpicklable
    finally:
        del model._rows_block_compiled


def test_an_arena_slice_too_small_stops_the_service_loudly(vocabulary, model, capfd):
    """No truncation and no other route: the rows are refused and the service stops."""
    encoder = RowsEncoder(vocabulary)
    encoded = encoder.encode(json.dumps(_observations(1)[0]).encode())
    service = _service(vocabulary, model, FakeAssembler(), arena_row_bytes=1)
    client = _open_client(service, encoder, "g1")
    try:
        assert _score(client, encoded, 3, "g1")["error_code"] == "SERVICE_STOPPING"
        assert service.stopping.is_set()
    finally:
        client.close()
        service.close()
    assert "rl_inference_3_arena_overflow" in capfd.readouterr().err
