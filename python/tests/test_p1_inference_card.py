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
from stpd.p1.fast_mu_follower import FastMuFollower
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
            handle.write(os.environ.get("CUDA_MPS_PIPE_DIRECTORY", "") + "|"
                         + os.environ.get("CUDA_VISIBLE_DEVICES", ""))
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    assembler = RefusingAssembler() if settings.get("refuse") == index else FakeAssembler()
    assembler.assembler_id = settings.get("assembler_id", f"asm-{index}")  # type: ignore[attr-defined]
    compiler = FrameCompiler(vocabulary).identity
    if settings.get("other_compiler") == index:
        compiler = {**compiler, "encoder": "other"}
    return FastInferenceService(vocabulary, compiler, assembler,
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
        assert card.publish(model, Handoff(MU)) == [True, True]
        assert card.assembler_ids == ["asm-0", "asm-1"]
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


def test_a_refusing_service_is_its_own_answer_not_a_card_failure(model):
    """Review M1: each assembler accepts or refuses a mu from its own resident
    state, so answers differ in normal running; the card keeps serving."""
    card = InferenceCard(FACTORY, services=2, mps=False, settings={"refuse": 1})
    try:
        assert card.publish(model, Handoff(MU)) == [True, False]
        assert card.failure is None and len(card.stats()) == 2
        assert all(process.is_alive() for process in card.processes)
    finally:
        closed = card.close()
    assert closed["failure"] is None and closed["exitcodes"] == [0, 0]


def test_services_sharing_an_assembler_id_never_serve():
    with pytest.raises(BoundaryError, match="service_start_failed") as caught:
        InferenceCard(FACTORY, services=2, mps=False, settings={"assembler_id": "asm"})
    assert "assembler_id_not_distinct" in caught.value.recovery


_ABANDON = """
import os, sys
sys.path[:0] = {paths!r}
from stpd.p1.inference_card import InferenceCard
card = InferenceCard("test_p1_inference_card:make_service", services=2, mps_root={root!r},
                     mps_control={control!r})
print(" ".join(str(process.pid) for process in card.processes), flush=True)
raise RuntimeError("launcher failed without close()")
"""


def test_a_launcher_that_dies_without_close_leaves_nothing_behind():
    """Review M2: at interpreter exit the card closes its hosts and its MPS daemon
    (before, the hosts waited on their pipes and the launcher never exited)."""
    import subprocess
    import sys

    root = tempfile.mkdtemp()
    control, log = _fake_control(root)
    here = os.path.dirname(os.path.abspath(__file__))
    script = _ABANDON.format(paths=[here, os.path.dirname(here)], root=root, control=control)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 1 and "launcher failed without close()" in done.stderr
    pids = [int(pid) for pid in done.stdout.split()]
    assert len(pids) == 2
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    with open(log) as handle:
        assert handle.read().splitlines()[-1] == "quit"


def _fake_control(root: str, code: int = 0) -> tuple[str, str]:
    log = os.path.join(root, "control.log")
    script = os.path.join(root, "mps-control")
    with open(script, "w") as handle:
        handle.write(f'#!/bin/sh\necho "args:$* pipe:$CUDA_MPS_PIPE_DIRECTORY'
                     f' gpu:$CUDA_VISIBLE_DEVICES" >> {log}\n'
                     f'if [ "$#" -eq 0 ]; then cat >> {log}; fi\nexit {code}\n')
    os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
    return script, log


def test_card_runs_its_services_under_its_own_mps_daemon(model):
    root = tempfile.mkdtemp()
    control, log = _fake_control(root)
    env_file = os.path.join(root, "env")
    card = InferenceCard(FACTORY, services=2, mps_root=root, mps_control=control, gpu=3,
                         settings={"env_file": env_file})
    try:
        assert card.mps is not None
        pipe = card.mps.env["CUDA_MPS_PIPE_DIRECTORY"]  # type: ignore[index]
        for index in range(2):
            with open(f"{env_file}.{index}") as handle:
                assert handle.read() == f"{pipe}|3"  # set before the service started
        assert card.publish(model, Handoff(MU)) == [True, True]
    finally:
        closed = card.close()
    assert closed["mps"] == "quit" and not os.path.exists(pipe)
    with open(log) as handle:
        lines = handle.read().splitlines()
    assert lines == [f"args:-d pipe:{pipe} gpu:3", f"args: pipe:{pipe} gpu:3",
                     "get_server_list", f"args: pipe:{pipe} gpu:3", "quit"]


def test_mps_daemon_that_does_not_start_is_loud():
    root = tempfile.mkdtemp()
    control, _ = _fake_control(root, code=1)
    with pytest.raises(BoundaryError, match="mps_start_failed"):
        InferenceCard(FACTORY, services=2, mps_root=root, mps_control=control)
    with pytest.raises(BoundaryError, match="mps_control_missing"):
        MpsDaemon(root, control=os.path.join(root, "missing")).start()


class _Source:
    def __init__(self, compiler: Any, loaded: Any) -> None:
        self.current = {"snapshot_sha256": "c" * 64, "training_generation": "gen-1",
                        "version": 2, "updates": 8, "compiler_identity": compiler}
        self.model = loaded
        self.fail_load = False

    def latest(self) -> dict[str, Any]:
        return dict(self.current)

    def load(self, value: dict[str, Any]) -> Any:
        if self.fail_load:
            raise ValueError("behavior_snapshot_checksum_changed")
        return self.model


def test_a_follower_publishes_through_the_card_and_records_each_service(model):
    """K > 1: the follower follows the card; one refusing service is `partial`."""
    card = InferenceCard(FACTORY, services=2, mps=False, settings={"refuse": 1})
    follower = FastMuFollower(_Source(card.compiler_identity, model), card)
    try:
        assert card.compiler_identity is not None and not card.stopping.is_set()
        assert follower.step() is True
        assert follower.counters["partial"] == 1 and follower.counters["published"] == 0
        assert follower.history[-1]["accepted_by"] == [True, False]
        assert follower.behind == [1]
        # Review S3: the refusing service alone gets the current mu again.
        assert follower.step() is False and follower.counters["reoffered"] == 1
        assert follower.history[-1]["reoffer_to"] == [1]
        assert card.publish(model, Handoff(MU), services=[1]) == [False]
        assert card.publish(model, Handoff(MU), services=[0]) == [True]
        for services in ([], [2], [0, 0]):
            with pytest.raises(ValueError, match="inference_card_publish_services"):
                card.publish(model, Handoff(MU), services=services)
        assert card.failure is None
    finally:
        closed = follower.close()  # stops following, then closes the card
    assert closed["failure"] is None and card.stopping.is_set()


def test_a_follower_that_gives_up_stops_every_service_of_the_card(model):
    card = InferenceCard(FACTORY, services=2, mps=False)
    source = _Source(card.compiler_identity, model)
    source.fail_load = True
    follower = FastMuFollower(source, card, interval=0.01, max_failures=2).start()
    try:
        assert follower.thread is not None
        follower.thread.join(30)
        assert follower.stopped() and card.stopping.is_set()
        assert card.failure is not None and card.failure.startswith("mu_follower_stopped:")
        with pytest.raises(ValueError, match="behavior_snapshot_checksum_changed"):
            follower.check()
        with pytest.raises(BoundaryError, match="card_stopped"):
            card.publish(model, Handoff(MU))  # no later mu reaches any service
    finally:
        closed = card.close()
    assert closed["failure"].startswith("mu_follower_stopped:")


def test_services_with_different_compiler_identities_never_serve():
    with pytest.raises(BoundaryError, match="service_start_failed") as caught:
        InferenceCard(FACTORY, services=2, mps=False, settings={"other_compiler": 1})
    assert "compiler_identity_not_equal" in caught.value.recovery


def test_publish_after_or_during_close_is_card_stopped_and_keeps_the_failure(model):
    """Review S2: a closing card refuses new commands as card_stopped (its failure
    stays None) and close() waits for the command in progress to answer."""
    card = InferenceCard(FACTORY, services=2, mps=False)
    card._lock.acquire()  # stands for a publish in progress
    closing = threading.Thread(target=card.close)
    closing.start()
    try:
        for _ in range(500):
            if card.stopping.is_set():
                break
            closing.join(0.01)
        assert card.stopping.is_set() and closing.is_alive()  # waits for the command
        assert all(process.is_alive() for process in card.processes)
    finally:
        card._lock.release()
    closing.join(120)
    assert not closing.is_alive()
    with pytest.raises(BoundaryError, match="card_stopped") as caught:
        card.publish(model, Handoff(MU))
    assert caught.value.recovery == "closing"
    assert card.failure is None and card.close()["failure"] is None


def test_aggregator_close_delivers_what_a_link_already_read(vocabulary, model, monkeypatch):
    """CI flake (run 42998622726): close() closed the service connections while a
    link was still forwarding an episode_close it had read, so a clean close
    reported `service_send:OSError`. close() now waits for every forwarder."""
    import time

    link, close = NodeAggregator._link_message, NodeAggregator.close

    def slow_link(self: Any, env: Any, message: dict[str, Any]) -> None:
        if message.get("type") == "episode_close":
            time.sleep(0.2)  # still forwarding when close() runs
        link(self, env, message)

    def slow_close(self: Any, timeout: float = 30.0) -> dict[str, Any]:
        pool_close = self.pool.close

        def slow_pool() -> None:
            time.sleep(1.0)  # a loaded runner: the encoder pool stops slowly
            pool_close()
        self.pool.close = slow_pool
        return close(self, timeout)

    monkeypatch.setattr(NodeAggregator, "_link_message", slow_link)
    monkeypatch.setattr(NodeAggregator, "close", slow_close)
    test_card_scores_each_game_on_its_bound_service(vocabulary, model)
