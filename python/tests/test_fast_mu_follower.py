"""WP8 mu handoff: learner publication -> FastInferenceService hot swap."""
from __future__ import annotations

import copy
import os
import tempfile
import threading
from typing import Any

import pytest
import torch
from test_p1_fast_inference import (  # noqa: F401 - fixtures
    AGENT_SPEC,
    MU,
    SAMPLING,
    FakeAssembler,
    FakeEnv,
    _aggregator,
    _decision_line,
    _observations,
    _service,
    model,
    vocabulary,
)

from spireagent.json_boundary import BoundaryError
from stpd.p1.fast_mu_follower import FastMuFollower
from stpd.p1.text_cache import TextVectorCache
from stpd.rl.fast_assembly import MuHandoff

NEW = "c" * 64
COMPILER = {"vocabulary_sha256": "v" * 64, "input_spec": {"version": 1}}


def identity(snapshot: str, generation: str = "gen-1", version: int = 2,
             updates: int = 8, compiler: Any = COMPILER) -> dict[str, Any]:
    return {"snapshot_sha256": snapshot, "training_generation": generation,
            "version": version, "updates": updates, "model_sha256": "m" * 64,
            "compiler_identity": compiler}


class Source:
    def __init__(self, latest: dict[str, Any], loaded: Any = "model") -> None:
        self.current, self.model = latest, loaded
        self.loads: list[str] = []
        self.fail_latest = 0
        self.fail_load = False

    def latest(self) -> dict[str, Any]:
        if self.fail_latest:
            self.fail_latest -= 1
            raise OSError("learner_unreachable")
        return dict(self.current)

    def load(self, value: dict[str, Any]) -> Any:
        if self.fail_load:
            raise ValueError("behavior_snapshot_checksum_changed")
        self.loads.append(value["snapshot_sha256"])
        return self.model


class Service:
    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.compiler_identity = copy.deepcopy(COMPILER)
        self.published: list[tuple[Any, MuHandoff]] = []
        self.stopping = threading.Event()
        self.stop_reason: str | None = None
        self.closed_while_following: bool | None = None
        self.follower: FastMuFollower | None = None

    def publish(self, scorer: Any, handoff: MuHandoff) -> bool:
        self.published.append((scorer, handoff))
        return self.accept

    def _stop(self, reason: str, detail: Any = None) -> None:
        self.stop_reason = reason
        self.stopping.set()

    def close(self) -> dict[str, Any]:
        assert self.follower is not None
        self.closed_while_following = not self.follower.stopped()
        self.stopping.set()
        return {"closed": True}


def test_each_identity_is_offered_once_and_a_new_generation_again():
    source, service = Source(identity(NEW)), Service()
    follower = FastMuFollower(source, service)
    assert follower.step() is True
    assert follower.step() is False and follower.counters["unchanged"] == 1
    assert service.published[0][1] == MuHandoff(NEW, "gen-1", 2, 8)
    assert service.published[0][1].weights is None
    # Same weights, CP restore's new generation: offered again (assembler rebinds).
    source.current = identity(NEW, generation="gen-2", version=0, updates=8)
    assert follower.step() is True
    assert [h.training_generation for _, h in service.published] == ["gen-1", "gen-2"]
    assert source.loads == [NEW, NEW]
    assert follower.status()["published"] == 2


_UNSET = object()


class Card(Service):
    """An InferenceCard's publish: one answer per service offered (`services=`)."""

    def __init__(self, answers: list[Any], raw: Any = _UNSET) -> None:
        super().__init__()
        self.answers = answers  # per service: what it answers next
        self.raw = raw  # when set, answered as is (an invalid shape)
        self.calls: list[list[int] | None] = []

    def publish(self, scorer: Any, handoff: MuHandoff,  # type: ignore[override]
                services: list[int] | None = None) -> Any:
        self.published.append((scorer, handoff))
        self.calls.append(services)
        if self.raw is not _UNSET:
            return self.raw
        return [self.answers[index] for index in
                (range(len(self.answers)) if services is None else services)]


@pytest.mark.parametrize(("answer", "accepted", "outcome"), [
    ([True, True, True], True, "published"),
    ([True, False, True], True, "partial"),
    ([False, False], False, "refused"),
])
def test_per_service_answers_are_recorded_as_such(answer, accepted, outcome):
    source, service = Source(identity(NEW)), Card(answer)
    follower = FastMuFollower(source, service)
    assert follower.step() is accepted
    assert follower.counters[outcome] == 1
    assert sum(follower.counters[key] for key in ("published", "partial", "refused")) == 1
    assert follower.history[-1]["accepted_by"] == answer
    assert follower.behind == [index for index, ok in enumerate(answer) if not ok]
    assert source.loads == [NEW]


def test_a_refusing_service_is_offered_the_current_mu_again_until_it_catches_up():
    """Review S3: a refusal is a known answer; only the refusing services get the
    current mu again, so each service's lag is bounded. A newer mu goes to all."""
    source, service = Source(identity(NEW)), Card([True, False, False])
    follower = FastMuFollower(source, service)
    assert follower.step() is True and follower.behind == [1, 2]
    assert follower.step() is False and service.calls[-1] == [1, 2]  # still refused
    service.answers[2] = True
    assert follower.step() is True and service.calls[-1] == [1, 2]
    assert follower.behind == [1] and follower.counters["caught_up"] == 1
    status = follower.status()
    assert [mu and mu["version"] for mu in status["accepted_mu"]] == [2, None, 2]
    assert status["behind"] == [1] and follower.history[-1]["reoffer_to"] == [1, 2]
    # A newer publication replaces the re-offer: every service gets the new one.
    source.current = identity("d" * 64, version=3, updates=12)
    service.answers[1] = True
    assert follower.step() is True and service.calls[-1] is None
    assert follower.behind == [] and follower.model is None
    assert [mu["version"] for mu in follower.status()["accepted_mu"]] == [3, 3, 3]
    assert follower.step() is False and follower.counters["unchanged"] == 1
    assert source.loads == [NEW, "d" * 64]  # a re-offer reuses the loaded model
    assert follower.counters["reoffered"] == 2 and follower.counters["partial"] == 1


@pytest.mark.parametrize("answer", [[], [True, 1], None, 1, "yes"])
def test_an_answer_of_any_other_shape_is_fatal_at_once(answer):
    """Review S1: whether the service took the mu is unknown: give up (by default
    the service stops), never count it as one transient failure."""
    source = Source(identity(NEW))
    service = Card([True, True], raw=answer)
    follower = FastMuFollower(source, service, interval=0.01, max_failures=3).start()
    assert follower.thread is not None
    follower.thread.join(10)
    assert follower.stopped() and follower.fatal is not None
    assert service.stop_reason == "mu_follower_stopped"
    with pytest.raises(BoundaryError, match="MU_PUBLISH_ANSWER_INVALID"):
        follower.check()
    assert len(service.published) == 1 and follower.behind == []  # never repeated
    assert follower.history[-1]["accepted"] is None
    assert follower.status()["total_failures"] == 1


def test_an_unknown_publish_is_never_repeated_even_to_a_service_behind():
    source, service = Source(identity(NEW)), Card([True, False])
    follower = FastMuFollower(source, service)
    assert follower.step() is True and follower.behind == [1]
    service.raw = OSError  # any non-bool shape stands for an unknown answer
    with pytest.raises(BoundaryError, match="MU_PUBLISH_ANSWER_INVALID"):
        follower.step()
    assert follower.behind == [] and follower.fatal is not None


def test_an_answer_for_a_different_number_of_services_is_invalid():
    source, service = Source(identity(NEW)), Card([True, True])
    follower = FastMuFollower(source, service)
    assert follower.step() is True
    source.current = identity("d" * 64, version=3)
    service.answers = [True, True, True]
    with pytest.raises(BoundaryError, match="MU_PUBLISH_ANSWER_INVALID"):
        follower.step()
    assert follower.fatal is not None and service.stop_reason == "mu_follower_stopped"


def test_one_service_history_keeps_its_old_shape():
    follower = FastMuFollower(Source(identity(NEW)), Service())
    assert follower.step() is True
    assert "accepted_by" not in follower.history[-1]
    status = follower.status()
    assert status["accepted_mu"][0]["version"] == 2 and "failures" not in status
    assert status["consecutive_failures"] == 0 and status["total_failures"] == 0


def test_a_refused_offer_is_counted_never_forced_and_offered_again():
    source, service = Source(identity(NEW)), Service(accept=False)
    follower = FastMuFollower(source, service)
    assert follower.step() is False and follower.step() is False
    assert len(service.published) == 2 and source.loads == [NEW]
    assert follower.counters["refused"] == 1 and follower.counters["reoffered"] == 1
    service.accept = True
    assert follower.step() is True and follower.behind == []
    assert follower.step() is False and follower.counters["unchanged"] == 1


def test_transient_read_errors_recover_and_repeated_failures_stop_loudly():
    source, service = Source(identity(NEW)), Service()
    source.fail_latest = 1
    follower = FastMuFollower(source, service, interval=0.01, max_failures=3).start()
    for _ in range(500):
        if service.published:
            break
        follower.stopping.wait(0.01)
    follower.check()  # one failure, then success: still running
    assert service.published and follower.counters["failures"] == 1
    assert follower.failures == 0 and not follower.stopped()
    follower.stop()

    broken = Source(identity(NEW))
    broken.fail_load = True
    service = Service()
    follower = FastMuFollower(broken, service, interval=0.01, max_failures=2).start()
    follower.thread.join(10)
    assert follower.stopped() and follower.counters["failures"] == 2
    with pytest.raises(ValueError, match="behavior_snapshot_checksum_changed"):
        follower.check()
    assert follower.offered is None  # nothing was offered in its place
    # Fatal by default: the service stops loudly instead of scoring with an old mu forever.
    assert service.stop_reason == "mu_follower_stopped" and service.stopping.is_set()


def test_a_different_compiler_identity_is_never_loaded_and_becomes_fatal():
    for compiler in ({**COMPILER, "vocabulary_sha256": "w" * 64}, None):
        source, service = Source(identity(NEW, compiler=compiler)), Service()
        fatal: list[BaseException] = []
        follower = FastMuFollower(source, service, interval=0.01, max_failures=2,
                                  on_fatal=fatal.append).start()
        follower.thread.join(10)
        assert source.loads == [] and service.published == []
        assert follower.counters["failures"] == 2 and follower.offered is None
        assert len(fatal) == 1 and "MU_COMPILER_IDENTITY_DIFFERS" in str(fatal[0])
        assert service.stop_reason is None  # on_fatal replaced the default stop
        with pytest.raises(BoundaryError, match="MU_COMPILER_IDENTITY_DIFFERS"):
            follower.check()


def test_the_last_error_is_kept_and_raised():
    source, service = Source(identity(NEW)), Service()
    source.fail_latest, source.fail_load = 1, True  # first OSError, then checksum errors
    follower = FastMuFollower(source, service, interval=0.01, max_failures=3).start()
    follower.thread.join(10)
    assert follower.counters["failures"] == 3
    with pytest.raises(ValueError, match="behavior_snapshot_checksum_changed"):
        follower.check()
    assert "checksum" in follower.status()["error"]


def test_close_stops_following_before_the_service_and_a_stopping_service_is_not_fed():
    source, service = Source(identity(NEW)), Service()
    follower = FastMuFollower(source, service, interval=0.01)
    service.follower = follower
    follower.start()
    for _ in range(500):
        if service.published:
            break
        follower.stopping.wait(0.01)
    assert follower.close() == {"closed": True}
    assert service.closed_while_following is False
    source.current = identity("d" * 64)
    assert follower.step() is False and follower.counters["service_stopping"] == 1
    assert len(service.published) == 1


class HangingSource(Source):
    """s1d: a get_mu read that never returns (the follower froze, no error, old mu)."""

    def __init__(self, latest: dict[str, Any]) -> None:
        super().__init__(latest)
        self.hang, self.release = threading.Event(), threading.Event()

    def latest(self) -> dict[str, Any]:
        if self.hang.is_set():
            self.release.wait(30)
        return super().latest()


def test_a_poll_that_hangs_stops_the_service_loudly_instead_of_following_nothing():
    source, service = HangingSource(identity(NEW)), Service()
    follower = FastMuFollower(source, service, interval=0.01, stall_seconds=0.3).start()
    for _ in range(500):
        if service.published:
            break
        follower.stopping.wait(0.01)
    assert len(service.published) == 1
    source.hang.set()
    source.current = identity("d" * 64, version=3)  # the learner moves on; the read hangs
    follower.watchdog.join(10)
    # Detected and loud: the service stops (rows refused) instead of scoring with v2 forever.
    assert service.stop_reason == "mu_follower_stopped" and service.stopping.is_set()
    with pytest.raises(BoundaryError, match="MU_FOLLOWER_STALLED"):
        follower.check()
    assert follower.status()["fatal"] and follower.counters["failures"] == 1
    source.release.set()  # the hung read returns late: nothing more is offered
    follower.thread.join(10)
    assert follower.stopped() and len(service.published) == 1


class HangingPublishService(Service):
    """A publish that waits on the device (cold compile) past the stall limit."""

    def __init__(self) -> None:
        super().__init__()
        self.hang, self.release = threading.Event(), threading.Event()

    def publish(self, scorer: Any, handoff: MuHandoff) -> bool:
        if self.hang.is_set():
            self.release.wait(30)
        return super().publish(scorer, handoff)


def test_a_publish_that_returns_after_the_watchdog_gave_up_changes_nothing():
    source, service = Source(identity(NEW)), HangingPublishService()
    follower = FastMuFollower(source, service, interval=0.01, stall_seconds=0.3).start()
    for _ in range(500):
        if service.published:
            break
        follower.stopping.wait(0.01)
    assert len(service.published) == 1 and follower.accepted_mu[0]["snapshot_sha256"] == NEW
    service.hang.set()
    source.current = identity("d" * 64, version=3)
    follower.watchdog.join(10)
    with pytest.raises(BoundaryError, match="MU_FOLLOWER_STALLED"):
        follower.check()
    service.release.set()  # the hung publish answers True after the give-up
    follower.thread.join(10)
    status = follower.status()
    # The late answer is recorded, but the status stays consistent: still the old
    # accepted mu, the failure is not cleared and nothing counts as published.
    assert follower.accepted_mu[0]["snapshot_sha256"] == NEW
    assert status["consecutive_failures"] == 1
    assert follower.counters["failures"] == 1 and follower.counters["published"] == 1
    assert follower.history[-1]["after_fatal"] is True
    assert status["fatal"] and follower.stopped()


def test_a_slow_but_returning_poll_is_not_a_stall():
    source, service = HangingSource(identity(NEW)), Service()
    follower = FastMuFollower(source, service, interval=0.01, stall_seconds=5).start()
    source.hang.set()
    threading.Timer(0.2, source.release.set).start()
    for _ in range(500):
        if service.published:
            break
        follower.stopping.wait(0.01)
    follower.check()
    assert service.published and service.stop_reason is None
    follower.stop()


def test_invalid_settings_are_refused():
    for options in ({"interval": 0}, {"max_failures": 0}, {"interval": 2, "stall_seconds": 1}):
        with pytest.raises(ValueError, match="invalid_fast_mu_follower_settings"):
            FastMuFollower(Source(identity(NEW)), Service(), **options)


def test_rows_after_the_swap_carry_the_new_mu(vocabulary, model):  # noqa: F811
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler)
    newer = copy.deepcopy(model)
    with torch.no_grad():
        for parameter in newer.rl4_heads.parameters():
            parameter.add_(0.01)
    # As load_behavior_mu returns it: a process-local text cache (holds an RLock).
    newer.text_cache, newer.text_cache_namespace = TextVectorCache(limit=1024**2), NEW
    path = os.path.join(tempfile.mkdtemp(), "link.sock")
    aggregator = _aggregator(vocabulary, service, path)
    try:
        env = FakeEnv(path)
        env.send({"type": "hello", "env_process_id": "env-1", "slots": 1, "p1_revision": "x",
                  "input_spec": AGENT_SPEC})
        env.send({"type": "episode_open",
                  "ascension": 0, "game_instance_id": "g1", "actor_id": "actor-1",
                  "sampling": SAMPLING, "single_choice": "score", "start": {"kind": "new_run"}})
        observations = _observations(2)
        env.socket.sendall(_decision_line(1, "g1", 0, observations[0]))
        assert env.reply()["mu_snapshot_sha256"] == MU
        follower = FastMuFollower(
            Source(identity(NEW, compiler=service.compiler_identity), newer), service)
        assert follower.step() is True
        env.send({"type": "step_outcome", "row_id": 1, "game_instance_id": "g1",
                  "step_index": 0, "status": "completed", "receipt_sha256": "b" * 64,
                  "public_facts": {"act": 1, "floor": 3, "hp": 50, "max_hp": 70},
                  "terminal": None})
        env.socket.sendall(_decision_line(2, "g1", 1, observations[1]))
        assert env.reply()["mu_snapshot_sha256"] == NEW
        assert assembler.mu == MuHandoff(NEW, "gen-1", 2, 8)
    finally:
        aggregator.close()
        service.close()


def test_a_failed_offer_drops_the_model_it_just_loaded(vocabulary, model):  # noqa: F811
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler)
    controls: list[tuple[str, str]] = []
    send = service._device_control

    def recording(kind: str, key: str, blob: bytes | None) -> None:
        controls.append((kind, key))
        send(kind, key, blob)

    class Ledger:
        resident = {MU: object()}

    def broken_offer(handoff: MuHandoff) -> Any:
        raise RuntimeError("audit_mu_archive_unconfigured")

    service._device_control = recording  # type: ignore[method-assign]
    assembler.ledger = Ledger()  # type: ignore[attr-defined]
    assembler.offer_mu = broken_offer  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="audit_mu_archive_unconfigured"):
            service.publish(model, MuHandoff(NEW, "gen-1", 2, 8))
        assert controls == [("model", NEW), ("drop", NEW)]
        controls.clear()
        # A key the ledger still holds (an earlier accepted offer) stays loaded.
        with pytest.raises(RuntimeError):
            service.publish(model, MuHandoff(MU, "gen-1", 1, 4))
        assert controls == [("model", MU)]
    finally:
        service.close()


class StagedHangingSource(HangingSource):
    """s1f: the read hung inside one of its sub-steps; the source names them."""

    def __init__(self, latest: dict[str, Any]) -> None:
        super().__init__(latest)
        self.on_stage: Any = None

    def latest(self) -> dict[str, Any]:
        self.on_stage("read.lock")
        if self.hang.is_set():
            self.on_stage("read.auth")
            self.release.wait(30)
        return dict(self.current)


def test_a_stage_over_its_own_limit_gives_up_and_names_the_stage():
    source, service = StagedHangingSource(identity(NEW)), Service()
    follower = FastMuFollower(source, service, interval=0.01, stall_seconds=30,
                              stage_seconds={"read": 0.3, "load": 5, "publish": 5}).start()
    assert source.on_stage == follower._enter
    for _ in range(500):
        if service.published:
            break
        follower.stopping.wait(0.01)
    assert len(service.published) == 1
    source.hang.set()
    source.current = identity("d" * 64, version=3)
    follower.watchdog.join(10)
    assert not follower.watchdog.is_alive()  # well before the 30 s poll limit
    with pytest.raises(BoundaryError, match="MU_FOLLOWER_STALLED.*stalled_stage=read.auth"):
        follower.check()
    status = follower.status()
    assert status["stalled"]["stage"] == "read.auth" and status["stalled"]["limit"] == 0.3
    assert status["stage"] == "read.auth" and service.stop_reason == "mu_follower_stopped"
    assert {"read.lock", "load", "publish"} <= set(status["stage_max_seconds"])
    source.release.set()
    follower.thread.join(10)
    assert follower.stopped() and len(service.published) == 1


def test_stage_limits_are_checked():
    with pytest.raises(ValueError, match="invalid_fast_mu_follower_settings"):
        FastMuFollower(Source(identity(NEW)), Service(), stall_seconds=10,
                       stage_seconds={"read": 20})
    with pytest.raises(ValueError, match="invalid_fast_mu_follower_settings"):
        FastMuFollower(Source(identity(NEW)), Service(), stage_seconds={"connect": 5})
    follower = FastMuFollower(Source(identity(NEW)), Service(), interval=0.01,
                              stall_seconds=0.3)
    assert follower.stage_seconds == {"read": 0.3, "load": 0.3, "publish": 0.3}


def test_slow_reads_stay_below_the_read_limit_and_settings_are_typed():
    class Timed(Source):
        transfer_timeout, handshake_timeout = 60.0, 30.0
        on_stage: Any = None

    follower = FastMuFollower(Timed(identity(NEW)), Service())
    assert follower.stage_seconds == {"read": 120.0, "load": 120.0, "publish": 300.0}
    for bad in ({"read": 60}, {"read": True}, {"read": "5"}, {"read": float("inf")}):
        with pytest.raises(ValueError, match="invalid_fast_mu_follower_settings"):
            FastMuFollower(Timed(identity(NEW)), Service(), stage_seconds=bad)
    with pytest.raises(ValueError, match="invalid_fast_mu_follower_settings"):
        FastMuFollower(Source(identity(NEW)), Service(), interval=True)


def test_after_the_give_up_a_late_read_changes_no_mark_and_the_hook_is_unbound():
    source, service = StagedHangingSource(identity(NEW)), Service()
    follower = FastMuFollower(source, service, interval=0.01, stall_seconds=30,
                              stage_seconds={"read": 0.3, "load": 5, "publish": 5}).start()
    for _ in range(500):
        if service.published:
            break
        follower.stopping.wait(0.01)
    assert follower.status()["stage_max_seconds"]["read.total"] >= 0
    hook = source.on_stage
    source.hang.set()
    follower.watchdog.join(10)
    assert source.on_stage is None and follower.status()["stage"] == "read.auth"
    hook("read.pin")  # the hung read's cache still holds the old hook
    source.release.set()
    follower.thread.join(10)
    assert follower.status()["stage"] == "read.auth"
