"""rl-inference-3 service + node aggregator, end to end on CPU (P-REWRITE WP4)."""
from __future__ import annotations

import contextlib
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
from stpd.p1.export import input_spec
from stpd.p1.fast_aggregator import LINK_SCHEMA, NodeAggregator, agent_input_spec
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
from stpd.p1.rows_scorer import RowScores, RowsScorer
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


# What the fast-env kernel sends: its model manifest's input_spec, not the projection.
AGENT_SPEC = input_spec(public_vocabulary(V2_INPUT_SPEC))


def test_agent_input_spec_is_the_model_manifest_binding(vocabulary):
    # The kernel's hello/decision lines carry manifest.input.input_spec (WP8 whole-chain
    # finding: the projection dict never matched a real fast-env process).
    identity = FrameCompiler(vocabulary).identity
    assert agent_input_spec(identity) == input_spec(vocabulary) == AGENT_SPEC
    assert agent_input_spec(identity) != identity["input_spec"]


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
    body = json.dumps({"input_spec": AGENT_SPEC, "observation": observation,
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
                  "input_spec": AGENT_SPEC})
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
                  "input_spec": AGENT_SPEC})
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
                         [(256 * 1024, 0), (256 * 1024, 2), (None, 0), (None, 2)])
def test_job_parts_are_joined_into_one_forward_with_the_same_scores(vocabulary, model,
                                                                     arena_row_bytes,
                                                                     join_workers):
    """Two prepare workers each prepare half a job: written into the shared arena
    and staged by `stage_forward_parts` (0) or planned in a joiner and staged by
    `stage_joined` (2), or (no arena) joined by concatenate_groups
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
                         [(256 * 1024, 0), (256 * 1024, 1), (None, 1)])
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
        assert service.stopping.is_set() and service.stop_reason == "arena_overflow"
        assert _score(client, encoded, 4, "g1")["error_code"] == "SERVICE_STOPPING"
    finally:
        client.close()
        service.close()
    err = capfd.readouterr().err
    assert "rl_inference_3_arena_overflow" in err
    assert '"rl_inference_3_stopping": "arena_overflow"' in err


def test_rows_conversion_matches_the_per_row_rule():
    """The vectorized RowsScorer.rows keeps the per-row values and finiteness rule."""
    import math

    generator = torch.Generator().manual_seed(7)
    rows, width = 9, 6
    scores = torch.randn(rows, width, generator=generator)
    logits = torch.randn(rows, 3, generator=generator)
    probabilities = logits.softmax(-1)
    hp = torch.randn(rows, generator=generator)
    lengths = [6, 3, 0, 4, 2, 6, 5, 1, 3]
    scores[1, 4] = float("nan")  # padding past the catalog: still scored
    scores[3, 2] = float("inf")  # inside the catalog: refused
    logits[4, 1] = float("nan")
    logits[5, 0] = float("-inf")  # -inf outcome logit is allowed
    logits[6, 2] = float("inf")
    hp[7] = float("nan")
    got = RowsScorer.rows((scores, probabilities, logits, hp), lengths)
    for row, count in enumerate(lengths):
        values = scores[row, :count]
        refused = (count < 1 or not bool(torch.isfinite(values).all())
                   or bool(logits[row].isnan().any()) or bool(logits[row].isposinf().any())
                   or not math.isfinite(float(hp[row])))
        if refused:
            assert got[row] is None
        else:
            assert got[row] == RowScores(values.tolist(), probabilities[row].tolist(),
                                         float(hp[row]))
    assert [row is None for row in got] == [False, False, True, True, True, False, True, True,
                                             False]


# -- #431 review fixes: loud stops, never a silent stall ------------------------

class RaisingMStd(MStd):
    """A model whose forward raises `failure` (pickled to the device process)."""

    failure: type[BaseException] = RuntimeError

    def forward_prepared_rl4(self, group: Any, **options: Any) -> Any:
        raise self.failure("injected forward failure")


def _raising(model: MStd, failure: type[BaseException]) -> MStd:
    import copy

    bad = copy.copy(model)
    bad.__class__ = RaisingMStd
    bad.failure = failure
    return bad


def _bad_samplings() -> list[Any]:
    def classes(**change: Any) -> dict[str, Any]:
        noncombat = {**SAMPLING["classes"]["noncombat"], **change}
        return {**SAMPLING, "classes": {**SAMPLING["classes"], "noncombat": noncombat}}

    missing_seed = {key: value for key, value in SAMPLING.items() if key != "seed"}
    return [None, missing_seed, {**SAMPLING, "extra": 1}, {**SAMPLING, "mode": "greedy"},
            {**SAMPLING, "seed": 7}, {**SAMPLING, "arm_id": "a b"},
            {**SAMPLING, "classes": {"combat": SAMPLING["classes"]["combat"]}},
            classes(temperature=0), classes(temperature=3.5), classes(temperature="1"),
            classes(temperature=True), classes(uniform_mix=-0.01), classes(uniform_mix=0.2),
            {**SAMPLING, "classes": {**SAMPLING["classes"], "combat": {"temperature": 1.0}}}]


def test_episode_open_sampling_is_checked_and_a_bad_one_is_refused(vocabulary, model, capfd):
    """Review M1: an out-of-contract sampling never opens the game, and the
    service keeps serving every other game."""
    from stpd.p1.inference_service import check_sampling

    assert check_sampling(SAMPLING) is None
    assert all(check_sampling(bad) is not None for bad in _bad_samplings())
    encoder = RowsEncoder(vocabulary)
    encoded = encoder.encode(json.dumps(_observations(1)[0]).encode())
    service = _service(vocabulary, model, FakeAssembler())
    client = _open_client(service, encoder, "good")
    try:
        bad = {**SAMPLING, "classes": {**SAMPLING["classes"],
                                       "combat": {"temperature": 0, "uniform_mix": 0.0}}}
        client.send_bytes(pack_message({
            "schema": V3_SCHEMA, "kind": "episode_open", "request_id": 3,
            "game_instance_id": "bad", "actor_id": "a", "sampling": bad,
            "single_choice": "score", "start": {"kind": "new_run"}}))
        assert _score(client, encoded, 4, "bad")["error_code"] == "EPISODE_NOT_OPEN"
        assert _score(client, encoded, 5, "good")["status"] == "scored"
        assert not service.stopping.is_set()
    finally:
        client.close()
        report = service.close()
    assert report["episodes_refused"] == 1
    assert "rl_inference_3_episode_refused" in capfd.readouterr().err


def test_a_results_thread_failure_stops_and_refuses_the_rows_in_flight(vocabulary, model,
                                                                      capfd):
    """Review M1: the results thread never dies silently."""
    encoder = RowsEncoder(vocabulary)
    encoded = encoder.encode(json.dumps(_observations(1)[0]).encode())
    service = _service(vocabulary, model, FakeAssembler())
    client = _open_client(service, encoder, "g1")

    def broken(part: dict[str, Any]) -> None:
        raise KeyError("temperature")

    try:
        service._finish_part = broken  # type: ignore[method-assign]
        assert _score(client, encoded, 3, "g1")["error_code"] == "SERVICE_STOPPING"
        assert service.stop_reason == "results_failed" and service.stopping.is_set()
        assert _score(client, encoded, 4, "g1")["error_code"] == "SERVICE_STOPPING"
    finally:
        client.close()
        service.close()
    assert "rl_inference_3_results_failed" in capfd.readouterr().err


@pytest.mark.parametrize("device_rows", [False, True])
@pytest.mark.parametrize(("failure", "reason"), [(RuntimeError, "device_job_failed"),
                                                 (SystemExit, "device_process_gone")])
def test_a_failing_forward_stops_the_service_never_per_row(vocabulary, model, failure, reason,
                                                           device_rows, capfd):
    """Review S3 (an exception in the forward: SERVICE_STOPPING and stop, not
    NONFINITE_SCORES) and S1 (the forward thread itself dies: the device process
    exits, so the service sees EOF and stops)."""
    encoder = RowsEncoder(vocabulary)
    encoded = encoder.encode(json.dumps(_observations(1)[0]).encode())
    assembler = FakeAssembler()
    service = _service(vocabulary, _raising(model, failure), assembler,
                       device_rows=device_rows)
    client = _open_client(service, encoder, "g1")
    try:
        assert _score(client, encoded, 3, "g1")["error_code"] == "SERVICE_STOPPING"
        assert service.stop_reason == reason and service.stopping.is_set()
        assert _score(client, encoded, 4, "g1")["error_code"] == "SERVICE_STOPPING"
    finally:
        client.close()
        report = service.close()
    assert "NONFINITE_SCORES" not in report["refused"]
    assert not any(kind == "finish" for kind, _ in assembler.calls)
    err = capfd.readouterr().err
    assert "rl_inference_3_stopping" in err
    if failure is SystemExit:
        assert "rl_inference_3_forward_thread_failed" in err


def _send_row(client: Any, encoded: Any, request_id: int, game: str) -> None:
    row = {"request_id": request_id, "game_instance_id": game, "step_index": request_id,
           "consumption_id": f"c{request_id}", "catalog_len": encoded.catalog_len,
           "catalog_sha256": encoded.catalog_sha256,
           "observation_sha256": encoded.observation_sha256, "in_combat": False,
           "floor": 2, "payload": {"offset": 0, "length": len(encoded.row),
                                   "row_sha256": encoded.row_sha256}}
    client.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                    "packet_id": request_id, "rows": [row]}, encoded.row))


def _replies(client: Any, count: int) -> dict[int, dict[str, Any]]:
    out = {}
    for _ in range(count):
        assert client.poll(60)
        envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
        out[envelope["packet_id"]] = dict(envelope["results"][0])
    return out


def test_a_dead_device_refuses_rows_waiting_for_a_slot_and_close_returns(vocabulary, model):
    """Review S2: a row in flight and a row the batcher holds (waiting for the
    only slot) are both refused and aborted; close does not wait out a timeout."""
    import signal
    import time

    encoder = RowsEncoder(vocabulary)
    encoded = encoder.encode(json.dumps(_observations(1)[0]).encode())
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler, in_flight=1)
    client = _open_client(service, encoder, "g1")
    pid = service.device_process.pid
    try:
        assert _score(client, encoded, 3, "g1")["status"] == "scored"
        os.kill(pid, signal.SIGSTOP)  # the job of row 4 holds the slot, never returns
        _send_row(client, encoded, 4, "g1")
        time.sleep(0.5)
        _send_row(client, encoded, 5, "g1")  # waits in the batcher for the slot
        time.sleep(0.5)
        os.kill(pid, signal.SIGKILL)
        replies = _replies(client, 2)
        assert {key: value["error_code"] for key, value in replies.items()} == {
            4: "SERVICE_STOPPING", 5: "SERVICE_STOPPING"}
        assert service.stop_reason == "device_process_gone"
    finally:
        client.close()
        started = time.perf_counter()
        service.close()
    assert time.perf_counter() - started < 30
    aborted = {body[0]: body[1] for kind, body in assembler.calls if kind == "abort"}
    assert aborted == {4: "SERVICE_STOPPING", 5: "SERVICE_STOPPING"}


def test_an_audit_mismatch_keeps_rows_in_flight_out_of_segments(vocabulary, model):
    """Review S4: rows already in flight when a spot check fails are refused, not
    finished into a segment, and the stop is visible outside the service."""
    import signal

    encoder = RowsEncoder(vocabulary)
    encoded = encoder.encode(json.dumps(_observations(1)[0]).encode())
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler, in_flight=1)
    client = _open_client(service, encoder, "g1")
    pid = service.device_process.pid
    try:
        assert _score(client, encoded, 3, "g1")["status"] == "scored"
        assert service.build_output_stopped() is None
        os.kill(pid, signal.SIGSTOP)
        _send_row(client, encoded, 4, "g1")
        import time
        time.sleep(0.5)  # row 4 is dispatched and in flight
        service._audit_failure([])  # another part's spot check differed
        os.kill(pid, signal.SIGCONT)
        assert _replies(client, 1)[4]["error_code"] == "SERVICE_STOPPING"
        assert service.build_output_stopped() == "audit_mismatch"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGCONT)
        client.close()
        service.close()
    finished = {body[0] for kind, body in assembler.calls if kind == "finish"}
    assert finished == {3}
    aborted = {body[0]: body[1] for kind, body in assembler.calls if kind == "abort"}
    assert aborted == {4: "SERVICE_STOPPING"}


def test_a_broken_encoder_pool_fails_the_aggregator(vocabulary, model, capfd):
    """Review S5(a): a dead encoder pool is the aggregator's failure, not a row's."""
    service = _service(vocabulary, model, FakeAssembler())
    path = os.path.join(tempfile.mkdtemp(), "link.sock")
    aggregator = _aggregator(vocabulary, service, path)
    try:
        env = FakeEnv(path)
        env.send({"type": "hello", "env_process_id": "env-1", "slots": 1, "p1_revision": "x",
                  "input_spec": AGENT_SPEC})
        env.send({"type": "episode_open", "game_instance_id": "g1", "actor_id": "actor-1",
                  "sampling": SAMPLING, "single_choice": "score", "start": {"kind": "new_run"}})
        aggregator.pool._break(RuntimeError("worker gone"))
        env.socket.sendall(_decision_line(1, "g1", 0, _observations(1)[0]))
        env.socket.settimeout(60)
        assert env.reader.readline() == b""  # the link is closed, no row refusal
    finally:
        stats = aggregator.close()
        service.close()
    assert stats["failure"] == "encoder_pool_broken"
    assert stats["refused_here"] == {}
    assert "fast_aggregator_failed" in capfd.readouterr().err


def test_a_bad_link_message_closes_only_that_env_connection(vocabulary, model, capfd):
    """Review S5(b): a link message that raises closes its connection loudly; the
    aggregator keeps serving other connections."""
    service = _service(vocabulary, model, FakeAssembler())
    path = os.path.join(tempfile.mkdtemp(), "link.sock")
    aggregator = _aggregator(vocabulary, service, path)
    try:
        for bad in ({"type": "episode_open", "game_instance_id": "g1"},  # before hello
                    {"type": "unknown_kind"}):
            env = FakeEnv(path)
            if bad["type"] != "episode_open":
                env.send({"type": "hello", "env_process_id": "env-1", "slots": 1,
                          "p1_revision": "x", "input_spec": AGENT_SPEC})
            env.send(bad)
            env.socket.settimeout(60)
            assert env.reader.readline() == b""
        env = FakeEnv(path)  # a fresh connection is still served
        env.send({"type": "hello", "env_process_id": "env-2", "slots": 1, "p1_revision": "x",
                  "input_spec": AGENT_SPEC})
        env.send({"type": "episode_open", "game_instance_id": "g2", "actor_id": "actor-1",
                  "sampling": SAMPLING, "single_choice": "score", "start": {"kind": "new_run"}})
        env.socket.sendall(_decision_line(1, "g2", 0, _observations(1)[0]))
        assert env.reply()["type"] == "scored"
    finally:
        stats = aggregator.close()
        service.close()
    assert stats["failure"] is None
    err = capfd.readouterr().err
    assert "hello_required" in err and "unexpected_link_message" in err


# -- V3 gui-infer option (default off): part sizing ----------------------------------
def _packet(encoded: list[Any], game: str, first_id: int) -> tuple[list[dict[str, Any]], bytes]:
    rows, binary = [], bytearray()
    for index, item in enumerate(encoded):
        rows.append({"request_id": first_id + index, "game_instance_id": game,
                     "step_index": index, "consumption_id": f"c{index}",
                     "catalog_len": item.catalog_len, "catalog_sha256": item.catalog_sha256,
                     "observation_sha256": item.observation_sha256, "in_combat": False,
                     "floor": 2, "payload": {"offset": len(binary), "length": len(item.row),
                                             "row_sha256": item.row_sha256}})
        binary += item.row
    return rows, bytes(binary)


@pytest.mark.parametrize(("part_rows", "parts"), [(0, 3), (3, 2)])
def test_part_rows_sets_the_parts_of_a_job_with_the_same_scores(vocabulary, model, part_rows,
                                                               parts):
    """6 rows, job_parts 4: off = ceil(6/4) rows a part = 3 parts; part_rows 3 = 2 parts."""
    encoder = RowsEncoder(vocabulary)
    compiler = FrameCompiler(vocabulary)
    encoded = [encoder.encode(json.dumps(item).encode()) for item in _observations(6)]
    frames = [decode_row(e.row, validator=compiler, expected_sha256=e.row_sha256)
              for e in encoded]
    expected = RowsScorer(model).score(frames, [2] * len(frames))
    assembler = FakeAssembler()
    identity = FrameCompiler(vocabulary).identity
    service = FastInferenceService(vocabulary, identity, assembler, training_generation="gen-1",
                                   address=("127.0.0.1", 0), env_sha256=ENV,
                                   prepare_workers=4, job_parts=4, part_rows=part_rows,
                                   join_workers=1, window_ms=200.0)
    assert service.publish(model, Handoff(MU))
    client = _open_client(service, encoder, "g1")
    try:
        rows, binary = _packet(encoded, "g1", 3)
        client.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                        "packet_id": 1, "rows": rows}, binary))
        envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
    finally:
        client.close()
        report = service.close()
    assert [result["status"] for result in envelope["results"]] == ["scored"] * 6
    assert report["jobs"] == 1 and report["parts"] == parts and report["batches"] == 1
    finished = {body[0]: body[1] for kind, body in assembler.calls if kind == "finish"}
    for index, want in enumerate(expected):
        assert want is not None
        _, behavior = distribution(want.scores, 1.5, 0.05)
        assert torch.allclose(torch.tensor(finished[3 + index]["behavior_q"]),
                              torch.tensor(behavior), atol=1e-6)


@pytest.mark.parametrize("part_rows", [-1, 1.5])
def test_invalid_part_rows_is_refused(vocabulary, model, part_rows):
    with pytest.raises(ValueError, match="invalid_rl_inference_3_job_shape"):
        _service(vocabulary, model, FakeAssembler(), part_rows=part_rows)


# -- V3 option (default off): per-row scores built in the device process -----------
@pytest.mark.parametrize("join_workers", [0, 1])
def test_device_rows_gives_the_same_scores_and_values(vocabulary, model, join_workers):
    """`device_rows`: the device process runs RowsScorer.rows; every finished row's
    behavior_q, selected index and outcome probabilities equal the default path."""
    encoder = RowsEncoder(vocabulary)
    encoded = [encoder.encode(json.dumps(item).encode()) for item in _observations(6)]
    finished: dict[bool, dict[int, Any]] = {}
    for device_rows in (False, True):
        assembler = FakeAssembler()
        service = _service(vocabulary, model, assembler, device_rows=device_rows,
                           join_workers=join_workers, window_ms=200.0)
        client = _open_client(service, encoder, "g1")
        try:
            rows, binary = _packet(encoded, "g1", 3)
            client.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                            "packet_id": 1, "rows": rows}, binary))
            envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
        finally:
            client.close()
            service.close()
        assert [result["status"] for result in envelope["results"]] == ["scored"] * 6
        finished[device_rows] = {body[0]: (body[1]["behavior_q"], body[1]["selected_index"],
                                           body[2])
                                 for kind, body in assembler.calls if kind == "finish"}
    assert len(finished[True]) == 6 and finished[True] == finished[False]


class NanRowMStd(MStd):
    """A model whose forward gives row 1 a non-finite catalog score."""

    def forward_prepared_rl4(self, group: Any, **options: Any) -> Any:
        scores, logits, hp = super().forward_prepared_rl4(group, **options)
        scores = scores.clone()
        scores[1, 0] = float("nan")
        return scores, logits, hp


@pytest.mark.parametrize("device_rows", [False, True])
def test_device_rows_refuses_a_non_finite_row_like_the_default(vocabulary, model,
                                                               device_rows):
    """Review L1: with `device_rows` a non-finite row is still NONFINITE_SCORES
    (None from the device process), the other rows are scored, nothing stops."""
    import copy

    bad = copy.copy(model)
    bad.__class__ = NanRowMStd
    encoder = RowsEncoder(vocabulary)
    encoded = [encoder.encode(json.dumps(item).encode()) for item in _observations(3)]
    assembler = FakeAssembler()
    service = _service(vocabulary, bad, assembler, device_rows=device_rows, window_ms=200.0)
    client = _open_client(service, encoder, "g1")
    try:
        rows, binary = _packet(encoded, "g1", 3)
        client.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                        "packet_id": 1, "rows": rows}, binary))
        envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
        assert not service.stopping.is_set()
    finally:
        client.close()
        report = service.close()
    results = {result["request_id"]: result for result in envelope["results"]}
    statuses = [results[row["request_id"]].get("error_code", results[row["request_id"]]
                                               ["status"]) for row in rows]
    assert statuses == ["scored", "NONFINITE_SCORES", "scored"]
    assert report["refused"] == {"NONFINITE_SCORES": 1}


INFERENCE_LEVERS = "cross+heads+nodes"


def _rich_frames(vocabulary: dict[str, Any], count: int) -> tuple[list[Any], list[Any]]:
    """Rows with several entities, texts and catalog choices (the levers' owner maps)."""
    encoder = RowsEncoder(vocabulary)
    compiler = FrameCompiler(vocabulary)
    observations = []
    for index in range(count):
        observation = public_state()
        observation["status"] = {"hp": index, "cards": [{"id": index + k, "x": [1, k]}
                                                        for k in range(1 + index % 4)]}
        observations.append(observation)
    encoded = [encoder.encode(json.dumps(item).encode()) for item in observations]
    frames = [decode_row(e.row, validator=compiler, expected_sha256=e.row_sha256)
              for e in encoded]
    return encoded, frames


def test_rows_scorer_levers_match_the_original_forward(vocabulary, model):
    """`kernels` changes how the readouts are computed, not the function (CPU FP32)."""
    _, frames = _rich_frames(vocabulary, 7)
    floors = [2] * len(frames)
    original = RowsScorer(model).score(frames, floors)
    levered = RowsScorer(model, kernels=INFERENCE_LEVERS).score(frames, floors)
    for want, got in zip(original, levered, strict=True):
        assert want is not None and got is not None
        assert len(got.scores) == len(want.scores)
        assert torch.allclose(torch.tensor(got.scores), torch.tensor(want.scores), atol=1e-4)
        assert torch.allclose(torch.tensor(got.outcome_probabilities),
                              torch.tensor(want.outcome_probabilities), atol=1e-5)
        assert abs(got.hp - want.hp) <= 1e-4
        assert max(range(len(got.scores)), key=got.scores.__getitem__) == max(
            range(len(want.scores)), key=want.scores.__getitem__)


@pytest.mark.parametrize("kernels", ["heads+cross", "fast", "nodes+nodes", 3])
def test_invalid_kernels_are_refused(vocabulary, model, kernels):
    with pytest.raises(ValueError, match="invalid_prepared_kernels"):
        RowsScorer(model, kernels=kernels)
    with pytest.raises(ValueError, match="invalid_prepared_kernels"):
        _service(vocabulary, model, FakeAssembler(), kernels=kernels)


def test_service_levers_score_rows_like_the_original(vocabulary, model):
    """The device process runs the levered forward: same behavior_q within tolerance,
    same draws (selected_index) as the original forward on the same rows."""
    encoded, frames = _rich_frames(vocabulary, 6)
    expected = RowsScorer(model).score(frames, [2] * len(frames))
    results = {}
    for kernels in ("", INFERENCE_LEVERS):
        assembler = FakeAssembler()
        service = _service(vocabulary, model, assembler, window_ms=200.0, kernels=kernels)
        client = _open_client(service, RowsEncoder(vocabulary), "g1")
        try:
            rows, binary = _packet(encoded, "g1", 3)
            client.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                            "packet_id": 1, "rows": rows}, binary))
            envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
        finally:
            client.close()
            service.close()
        assert [result["status"] for result in envelope["results"]] == ["scored"] * 6
        results[kernels] = ({body[0]: body[1] for kind, body in assembler.calls
                             if kind == "finish"}, envelope["results"])
    (base, base_results), (lever, lever_results) = results[""], results[INFERENCE_LEVERS]
    for index, want in enumerate(expected):
        assert want is not None
        _, behavior = distribution(want.scores, 1.5, 0.05)
        assert torch.allclose(torch.tensor(base[3 + index]["behavior_q"]),
                              torch.tensor(behavior), atol=1e-6)  # off: the old path
        assert torch.allclose(torch.tensor(lever[3 + index]["behavior_q"]),
                              torch.tensor(behavior), atol=1e-4)
    picks = [lever[3 + index]["selected_index"] for index in range(6)]
    assert all(type(pick) is int for pick in picks)
    assert picks == [base[3 + index]["selected_index"] for index in range(6)]
    assert len(lever_results) == len(base_results) == 6


def test_publish_ships_a_model_that_carries_a_compiled_node_builder(vocabulary, model):
    """The nodes lever's compiled builder is a runtime cache too, never shipped."""
    def unpicklable() -> None:
        pass

    model.__dict__["_rows_nodes_compiled"] = unpicklable
    try:
        service = _service(vocabulary, model, FakeAssembler(), kernels=INFERENCE_LEVERS)
        service.close()
        assert model.__dict__["_rows_nodes_compiled"] is unpicklable
    finally:
        del model.__dict__["_rows_nodes_compiled"]


@pytest.mark.parametrize("kernels", ["embed+nodes", "cross+embed+heads+nodes"])
def test_the_backward_only_embed_lever_is_refused_for_inference(vocabulary, model, kernels):
    with pytest.raises(ValueError, match="invalid_inference_kernels:embed_is_backward_only"):
        RowsScorer(model, kernels=kernels)
    with pytest.raises(ValueError, match="invalid_inference_kernels:embed_is_backward_only"):
        _service(vocabulary, model, FakeAssembler(), kernels=kernels)


def test_levered_service_scores_each_published_mu_with_its_own_weights(vocabulary, model):
    """#480 review S2: two mus with different weights published in turn to a service
    with levers; rows planned on each mu score like that mu's own original forward."""
    torch.manual_seed(11)
    other = MStd(vocabulary, MStdExecution(
        implementation="experimental-packed-v1", microbatch_size=4,
        scene_attention_area=67108864, text_attention_area=67108864, cpu_threads=1))
    other.enable_rl4_heads(7)
    other = other.eval().requires_grad_(False)
    encoded, frames = _rich_frames(vocabulary, 4)
    wants = {name: RowsScorer(mu).score(frames, [2] * len(frames))
             for name, mu in (("first", model), ("second", other))}
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler, window_ms=200.0, kernels=INFERENCE_LEVERS)
    got: dict[str, dict[int, Any]] = {}
    try:
        for name, game, first_id in (("first", "g1", 3), ("second", "g2", 30)):
            if name == "second":
                assert service.publish(other, Handoff("b" * 64))
            client = _open_client(service, RowsEncoder(vocabulary), game)
            try:
                rows, binary = _packet(encoded, game, first_id)
                client.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                                "packet_id": 1, "rows": rows}, binary))
                envelope, _ = unpack_message(client.recv_bytes(V3_MAX_MESSAGE))
            finally:
                client.close()
            assert [r["status"] for r in envelope["results"]] == ["scored"] * 4
            got[name] = {body[0]: body[1] for kind, body in assembler.calls
                         if kind == "finish" and first_id <= body[0] < first_id + 4}
            assert {item["mu_snapshot_sha256"] for item in got[name].values()} == {
                MU if name == "first" else "b" * 64}
    finally:
        service.close()
    for name, first_id in (("first", 3), ("second", 30)):
        for index, want in enumerate(wants[name]):
            assert want is not None
            _, behavior = distribution(want.scores, 1.5, 0.05)
            assert torch.allclose(torch.tensor(got[name][first_id + index]["behavior_q"]),
                                  torch.tensor(behavior), atol=1e-4)
    assert any(not torch.allclose(torch.tensor(got["first"][3 + i]["behavior_q"]),
                                  torch.tensor(got["second"][30 + i]["behavior_q"]), atol=1e-3)
               for i in range(4))
