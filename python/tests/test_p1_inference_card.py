"""One card's K rl-inference-3 services: per-game binding, MPS daemon, loud failure (CPU)."""
from __future__ import annotations

import os
import signal
import stat
import tempfile
import threading
from typing import Any

import pytest
import torch
from test_p1_fast_inference import (
    AGENT_SPEC,
    ENV,
    MU,
    SAMPLING,
    FakeAssembler,
    FakeEnv,
    Handoff,
    Offer,
    _decision_line,
    _observations,
)

from spireagent.json_boundary import BoundaryError
from stpd.p1.fast_aggregator import NodeAggregator, service_for_game
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.inference_card import InferenceCard, MpsDaemon
from stpd.p1.inference_service import FastInferenceService
from stpd.p1.input import V2_INPUT_SPEC
from stpd.p1.model import MStd, MStdExecution
from stpd.p1.vocabulary import public_vocabulary


class RefusingAssembler(FakeAssembler):
    def offer_mu(self, handoff: Handoff) -> Offer:
        return Offer(False)


def make_service(index: int, settings: dict[str, Any]) -> FastInferenceService:
    """The card factory (runs in each host process)."""
    if settings.get("fail") == index:
        raise RuntimeError("device unavailable")
    if "env_file" in settings:
        with open(f"{settings['env_file']}.{index}", "w") as handle:
            handle.write(os.environ.get("CUDA_MPS_PIPE_DIRECTORY", ""))
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    assembler = RefusingAssembler() if settings.get("refuse") == index else FakeAssembler()
    return FastInferenceService(vocabulary, FrameCompiler(vocabulary).identity, assembler,
                                training_generation="gen-1", address=("127.0.0.1", 0),
                                env_sha256=ENV, prepare_workers=1, device="cpu")


FACTORY = "test_p1_inference_card:make_service"


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


def _games(services: int, per_service: int) -> list[str]:
    games: dict[int, list[str]] = {index: [] for index in range(services)}
    index = 0
    while any(len(bound) < per_service for bound in games.values()):
        game = f"game-{index}"
        bound = games[service_for_game(game, services)]
        if len(bound) < per_service:
            bound.append(game)
        index += 1
    return [game for bound in games.values() for game in bound]


def test_service_for_game_is_a_fixed_function_of_the_game():
    assert all(service_for_game(f"g{i}", 1) == 0 for i in range(50))
    picks = [service_for_game(f"g{i}", 3) for i in range(3000)]
    assert picks == [service_for_game(f"g{i}", 3) for i in range(3000)]
    assert set(picks) == {0, 1, 2} and min(picks.count(k) for k in range(3)) > 900


def test_aggregator_takes_one_service_or_a_list_never_both():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    common = {"socket_path": "/nonexistent", "aggregator_id": "n", "python_env_sha256": ENV,
              "encoder_workers": 1}
    for options in ({}, {"service_address": ("127.0.0.1", 1)},
                    {"services": []},
                    {"service_address": ("127.0.0.1", 1), "authkey": b"k",
                     "services": [(("127.0.0.1", 1), b"k")]}):
        with pytest.raises(ValueError, match="fast_aggregator_service"):
            NodeAggregator(vocabulary, **common, **options)


def _wait(condition: Any, seconds: float = 30) -> None:
    event = threading.Event()
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        event.wait(0.05)
    raise AssertionError("condition not reached")


def test_card_scores_each_game_on_its_bound_service(vocabulary, model):
    card = InferenceCard(FACTORY, services=2, mps=False)
    assert card.mps is None and len(card.services) == 2
    path = os.path.join(tempfile.mkdtemp(), "link.sock")
    aggregator = None
    try:
        assert card.publish(model, Handoff(MU))
        aggregator = NodeAggregator(vocabulary, services=card.services, socket_path=path,
                                    aggregator_id="node-1", python_env_sha256=ENV,
                                    encoder_workers=1)
        assert aggregator.service_instances == card.service_instances
        env = FakeEnv(path)
        env.send({"type": "hello", "env_process_id": "env-1", "slots": 4, "p1_revision": "x",
                  "input_spec": AGENT_SPEC})
        games = _games(2, 2)
        for game in games:
            env.send({"type": "episode_open", "game_instance_id": game, "actor_id": "actor-1",
                      "sampling": SAMPLING, "single_choice": "score",
                      "start": {"kind": "new_run"}})
        row_id = 0
        for step, observation in enumerate(_observations(2)):
            sent = {}
            for game in games:
                row_id += 1
                env.socket.sendall(_decision_line(row_id, game, step, observation))
                sent[row_id] = game
            replies = [env.reply() for _ in sent]
            assert {r["row_id"] for r in replies} == set(sent)
            assert all(r["type"] == "scored" and r["mu_snapshot_sha256"] == MU
                       and r["row_in_segment"] == step for r in replies)
            for rid, game in sent.items():
                env.send({"type": "step_outcome", "row_id": rid, "game_instance_id": game,
                          "step_index": step, "status": "completed",
                          "receipt_sha256": "b" * 64,
                          "public_facts": {"act": 1, "floor": 3, "hp": 50, "max_hp": 70},
                          "terminal": None})
        for game in games:
            env.send({"type": "episode_close", "game_instance_id": game, "reason": "terminal",
                      "last_step_index": 1})
        # Two games per service, two rows each: every row on its game's service.
        assert [stats["rows"] for stats in card.stats()] == [4, 4]
    finally:
        report = aggregator.close() if aggregator is not None else {}
        closed = card.close()
    assert report["failure"] is None and report["rows"] == 8 and report["pending"] == 0
    assert report["service_rows"] == [4, 4]
    assert closed["failure"] is None and [r["rows"] for r in closed["services"]] == [4, 4]
    assert closed["exitcodes"] == [0, 0]


def test_a_dead_service_stops_the_card_and_the_aggregator(vocabulary, model):
    failures: list[str] = []
    card = InferenceCard(FACTORY, services=2, mps=False, on_failure=failures.append)
    path = os.path.join(tempfile.mkdtemp(), "link.sock")
    aggregator = None
    try:
        card.publish(model, Handoff(MU))
        aggregator = NodeAggregator(vocabulary, services=card.services, socket_path=path,
                                    aggregator_id="node-1", python_env_sha256=ENV,
                                    encoder_workers=1)
        os.kill(card.processes[1].pid, signal.SIGKILL)
        _wait(lambda: card.failure is not None and aggregator.failure is not None)
        assert card.failure == f"service_1_exited:{-signal.SIGKILL}"
        assert failures == [card.failure]
        assert aggregator.failure.startswith("service_receive:")
        _wait(lambda: not card.processes[0].is_alive())  # never continues on one service
        with pytest.raises(BoundaryError, match="card_stopped"):
            card.publish(model, Handoff(MU))
    finally:
        if aggregator is not None:
            aggregator.close()
        closed = card.close()
    assert closed["failure"] == failures[0]


def test_a_service_that_does_not_start_stops_the_card():
    with pytest.raises(BoundaryError, match="service_start_failed") as caught:
        InferenceCard(FACTORY, services=2, mps=False, settings={"fail": 1})
    assert "device unavailable" in caught.value.recovery


def test_services_that_answer_a_publish_differently_stop_the_card(model):
    card = InferenceCard(FACTORY, services=2, mps=False, settings={"refuse": 1})
    try:
        with pytest.raises(BoundaryError, match="publish_disagreement"):
            card.publish(model, Handoff(MU))
        assert card.failure == "publish_disagreement:[True, False]"
        _wait(lambda: not any(process.is_alive() for process in card.processes))
    finally:
        card.close()


def _fake_control(root: str, code: int = 0) -> tuple[str, str]:
    log = os.path.join(root, "control.log")
    script = os.path.join(root, "mps-control")
    with open(script, "w") as handle:
        handle.write(f'#!/bin/sh\necho "args:$* pipe:$CUDA_MPS_PIPE_DIRECTORY" >> {log}\n'
                     f'if [ "$#" -eq 0 ]; then cat >> {log}; fi\nexit {code}\n')
    os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
    return script, log


def test_card_runs_its_services_under_its_own_mps_daemon(model):
    root = tempfile.mkdtemp()
    control, log = _fake_control(root)
    env_file = os.path.join(root, "env")
    card = InferenceCard(FACTORY, services=2, mps_root=root, mps_control=control,
                         settings={"env_file": env_file})
    try:
        assert card.mps is not None
        pipe = card.mps.env["CUDA_MPS_PIPE_DIRECTORY"]  # type: ignore[index]
        for index in range(2):
            with open(f"{env_file}.{index}") as handle:
                assert handle.read() == pipe  # set before the service started
        assert card.publish(model, Handoff(MU))
    finally:
        closed = card.close()
    assert closed["mps"] == "quit" and not os.path.exists(pipe)
    with open(log) as handle:
        lines = handle.read().splitlines()
    assert lines == [f"args:-d pipe:{pipe}", f"args: pipe:{pipe}", "get_server_list",
                     f"args: pipe:{pipe}", "quit"]


def test_mps_daemon_that_does_not_start_is_loud():
    root = tempfile.mkdtemp()
    control, _ = _fake_control(root, code=1)
    with pytest.raises(BoundaryError, match="mps_start_failed"):
        InferenceCard(FACTORY, services=2, mps_root=root, mps_control=control)
    with pytest.raises(BoundaryError, match="mps_control_missing"):
        MpsDaemon(root, control=os.path.join(root, "missing")).start()
