"""RL5 P-REWRITE WP7: K games per environment process for ``fast_chain`` (default off).

The old chain never imports this module. One environment process (WP2,
``fast-env-config-1``) owns K slots; each slot runs one Host driver, one native child
and one execution kernel. This module is the Python owner of that process:

- one ledger worker thread per game (``EvaluationLedger.run_managed(workers=K)``): the
  existing actor/ledger loop stays the only run loop (M5);
- reserve a slot, pass learner admission, durably offer the world, then ``open``;
- route ``closed`` by ``game_instance_id``; a late or unknown-instance close is counted
  and discarded, never applied to a later game;
- a game that does not close by its deadline gets ``close_slot``; if it still has not
  closed after the Stop bound, that slot is fenced, nothing new is admitted, the other
  games finish, and only then the process group is killed: only the fenced game is
  unknown;
- an environment process crash makes every game it carried unknown, and the process
  group is killed to confirm no native child survives; the manager is then broken and
  the actor stops (an operator restarts it; no silent fallback to the old chain);
- Stop: admission closes first (an open not yet written is censored, never sent);
  ``stop`` closes in-flight games at their next boundary (censored ``stop``); past the
  Stop latency bound the group is killed and a game still open is unknown
  ``stop_latency_bound_exceeded``; a sent game left unclosed by an exit is resolved
  at once (unknown), never at its deadline;
- inference liveness: an explicit probe, or a streak of ``inference_timeout`` closes,
  refuses admission before any world offer; without a probe the streak lock lifts
  after ``inference_retry_seconds`` for one trial game (one more timeout relocks);
- a game is admitted only at the env process's own ``host.ascension`` (one level per
  process; its ``episode_open`` names that level, the Host checks it against the native
  run at mount and the learner labels with that level's table; the service refuses a
  level its table does not support).

Failure semantics follow ``stpd/rl-inference-3`` and design v2 §2.6: nothing is resent;
an inference timeout is ``not_started`` (here: a censored game, never unknown).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, NoReturn

from spireagent.json_boundary import BoundaryError, json_bytes

from .episode_start import EpisodeStart, decode_episode_starts, require_first_frame_start

CONFIG_SCHEMA = "sts2.policy-runtime/fast-env-config-1"
COMMAND_SCHEMA = "sts2.policy-runtime/fast-env-command-1"
EVENT_SCHEMA = "sts2.policy-runtime/fast-env-event-1"
CLOSE_REASONS = frozenset({"terminal", "not_started", "unknown", "stop", "budget",
                           "env_crash"})
EVENTS = frozenset({"ready", "opened", "open_refused", "closed", "build_output_stopped",
                    "status", "stopped", "fatal", "evidence_failed"})
_MAX_EVENT_BYTES = 1 << 20


def reject(code: str) -> NoReturn:
    raise BoundaryError("fast_chain", code)


def new_game_ref() -> str:
    """Opaque manager-side id; never derived from a seed or world identity."""
    return "g-" + secrets.token_hex(16)


@dataclass(frozen=True)
class StopBound:
    """Design v2 §2.2 / WP2 §1.5: K x step sync + the longest in-flight resolution."""

    slots: int
    step_sync_ms: float
    inference_timeout_ms: float
    request_timeout_ms: float
    margin_ms: float = 500

    @property
    def seconds(self) -> float:
        return (self.slots * self.step_sync_ms
                + max(self.inference_timeout_ms, self.request_timeout_ms)
                + self.margin_ms) / 1000


def load_env_config(path: Path) -> dict[str, Any]:
    """The prepared ``fast-env-config-1`` (written 0600 by the launcher)."""
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or value.get("schema") != CONFIG_SCHEMA
            or type(value.get("slots")) is not int or not 1 <= value["slots"] <= 16
            or not isinstance(value.get("env_process_id"), str)
            or not value["env_process_id"]):
        reject("fast_env_config_invalid")
    inference, host = value.get("inference"), value.get("host")
    if (not isinstance(inference, dict) or not isinstance(host, dict)
            or not _positive(inference.get("timeout_ms"))
            or not _positive(host.get("request_timeout_ms"))):
        reject("fast_env_config_invalid")
    return value


def _positive(value: Any) -> bool:
    return type(value) in {int, float} and value > 0


class FastEnvProcess:
    """One NDJSON child: commands on stdin, events on stdout, diagnostics to a file."""

    # After the process exits, wait this long for stdout EOF (every written event read).
    # Only a native child that inherited stdout can hold EOF back past it.
    eof_grace_seconds = 3.0

    def __init__(self, argv: Sequence[str], *, stderr: Path,
                 on_event: Callable[[dict[str, Any]], None],
                 on_exit: Callable[[str], None]) -> None:
        stderr.parent.mkdir(parents=True, exist_ok=True)
        self._stderr = stderr.open("ab")
        # Its own process group, so one signal reaches every native child it started.
        self.process = subprocess.Popen(list(argv), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self._stderr,
                                        start_new_session=True)
        self.pid = self.process.pid
        self._lock = threading.Lock()
        self._on_event, self._on_exit = on_event, on_exit
        self._exit_once = threading.Lock()
        self._exit_reported = False
        self._reader = threading.Thread(target=self._read, name="fast-env-events",
                                        daemon=True)
        self._reader.start()
        # Exit is watched apart from stdout EOF: a native child that inherited stdout
        # keeps the pipe open after the environment process itself has died.
        self._watcher = threading.Thread(target=self._watch, name="fast-env-exit",
                                         daemon=True)
        self._watcher.start()

    def _report_exit(self, reason: str) -> None:
        with self._exit_once:
            if self._exit_reported:
                return
            self._exit_reported = True
        self._on_exit(reason)

    def _watch(self) -> None:
        code = self.process.wait()
        # Every event the process wrote is read before its exit is judged (a normal
        # stop's last closed/stopped must not look like a crash).
        self._reader.join(timeout=self.eof_grace_seconds)
        self._report_exit(f"fast_env_exited:{code}")

    def _read(self) -> None:
        stream: IO[bytes] = self.process.stdout  # type: ignore[assignment]
        reason = None
        try:
            for raw in stream:
                if len(raw) > _MAX_EVENT_BYTES:
                    reason = "fast_env_event_too_large"
                    break
                try:
                    event = json.loads(raw)
                except ValueError:
                    reason = "fast_env_protocol_violation"
                    break
                if (not isinstance(event, dict) or event.get("event") not in EVENTS
                        or event.get("schema", EVENT_SCHEMA) != EVENT_SCHEMA):
                    reason = "fast_env_protocol_violation"
                    break
                self._on_event(event)
        except (OSError, ValueError):
            reason = "fast_env_stdout_failed"
        if reason is not None:
            self._report_exit(reason)

    def send(self, command: dict[str, Any]) -> None:
        line = json.dumps({"schema": COMMAND_SCHEMA, **command},
                          separators=(",", ":")).encode() + b"\n"
        with self._lock:
            stdin = self.process.stdin
            assert stdin is not None
            stdin.write(line)
            stdin.flush()

    def kill_group(self, timeout: float = 10) -> bool:
        """SIGKILL the whole process group; True only when no member survives."""
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            return False
        try:
            self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.killpg(self.pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                # macOS answers EPERM while a killed member is still an unreaped
                # zombie (reparented to init): still present, keep polling.
                pass
            time.sleep(0.02)
        return False

    def close(self) -> None:
        self._watcher.join(timeout=self.eof_grace_seconds + 5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                with suppress(OSError):
                    stream.close()
        self._stderr.close()


@dataclass
class _Game:
    game_ref: str
    slot: int
    command_id: str
    opened: dict[str, Any] | None = None
    refused: dict[str, Any] | None = None
    closed: dict[str, Any] | None = None
    fenced: bool = False
    start: EpisodeStart | None = None
    start_mismatch: str | None = None
    admission_failure: str | None = None
    evidence_failure: str | None = None  # an ``evidence_failed`` event named this game
    hold_slot: bool = False  # a close_slot for this game is being written
    slot_freed: bool = False


@dataclass
class Reservation:
    slot: int
    game_ref: str = field(default_factory=new_game_ref)
    used: bool = False


class FastEnvUnavailable(BoundaryError):
    """Admission refused before any world offer: the slot's game is ``not_started``."""

    def __init__(self, code: str) -> None:
        super().__init__("fast_chain", code)


class GameManager:
    """Thread-safe owner of one environment process and its K slots."""

    def __init__(self, argv: Sequence[str], env_config: Mapping[str, Any], *, work: Path,
                 step_sync_ms: float = 4, ready_seconds: float = 300,
                 open_seconds: float = 300, inference_failure_streak: int = 3,
                 inference_probe: Callable[[], bool] | None = None,
                 probe_interval_seconds: float = 5, inference_retry_seconds: float = 60,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if type(inference_failure_streak) is not int or inference_failure_streak < 1:
            reject("fast_chain_inference_streak_invalid")
        self.argv, self.config, self.work = list(argv), dict(env_config), work
        self.slots = int(env_config["slots"])
        self.bound = StopBound(self.slots, step_sync_ms,
                               env_config["inference"]["timeout_ms"],
                               env_config["host"]["request_timeout_ms"])
        self.ready_seconds, self.open_seconds = ready_seconds, open_seconds
        self.streak_limit, self.probe = inference_failure_streak, inference_probe
        self.probe_interval, self.clock = probe_interval_seconds, clock
        self.inference_retry = inference_retry_seconds
        self.inference_down_at: float | None = None
        self.condition = threading.Condition()
        self.free = set(range(self.slots))
        self.games: dict[str, _Game] = {}
        self.instances: dict[str, _Game] = {}
        self.opens: dict[str, _Game] = {}
        self.commands = itertools.count(1)
        self.counters: Counter[str] = Counter()
        self.ready: dict[str, Any] | None = None
        self.unavailable: str | None = None  # broken, stopping, draining or output stop
        self.exit_reason: str | None = None
        self.stopped = False
        self.cleanup_confirmed: bool | None = None
        self.inference_failures = 0
        self.inference_down = False
        self.probed_at: float | None = None
        self.stop_report: dict[str, Any] | None = None
        self.process: FastEnvProcess | None = None
        self._stop_lock = threading.Lock()
        self.kill_reason: str | None = None

    # -- lifecycle -------------------------------------------------------------------
    def start(self) -> dict[str, Any]:
        self.process = FastEnvProcess(self.argv, stderr=self.work / "fast-env.stderr",
                                      on_event=self._event, on_exit=self._exited)
        deadline = self.clock() + self.ready_seconds
        with self.condition:
            while self.ready is None and self.exit_reason is None:
                left = deadline - self.clock()
                if left <= 0:
                    break
                self.condition.wait(min(left, 1))
            ready = self.ready
        if ready is None:
            self._kill("fast_env_not_ready")
            # Loud: a failed fast_chain start never falls back to the old chain.
            reject("fast_env_start_failed")
        if (ready.get("slots") != self.slots
                or ready.get("env_process_id") != self.config["env_process_id"]):
            self._kill("fast_env_ready_identity_changed")
            reject("fast_env_ready_identity_changed")
        return ready

    def _kill(self, reason: str) -> None:
        """Kill the group, then every unresolved game is unknown (never resent)."""
        with self.condition:
            # The first cause names the games; the exit this kill causes is not a crash.
            self.kill_reason = reason = self.kill_reason or reason
            # No open is written from here on (play checks this under the lock).
            self.unavailable = self.unavailable or reason
            self.condition.notify_all()
        process = self.process
        confirmed = process.kill_group() if process is not None else True
        with self.condition:
            self.cleanup_confirmed = confirmed and self.cleanup_confirmed is not False
            self.unavailable = self.unavailable or reason
            for game in self.games.values():
                if game.closed is None:
                    game.closed = {"event": "closed", "synthetic": True, "slot": game.slot,
                                   "game_ref": game.game_ref, "reason": "unknown",
                                   "detail": reason, "native": "process_killed"}
            self.condition.notify_all()

    def _exited(self, reason: str) -> None:
        with self.condition:
            self.exit_reason = reason
            expected = self.stopped or self.kill_reason is not None
            unresolved = any(game.closed is None for game in self.games.values())
            self.condition.notify_all()
        if not expected:
            # A crash: every game this process carried is unknown; only it is isolated.
            self._kill("env_crash:" + reason)
        elif unresolved:
            # An expected exit left a sent game unclosed (WP2 closes every game before
            # ``stopped``): resolve it now as unknown, group killed to confirm, never
            # at its far-away deadline.
            self._kill("fast_env_exited_without_close")

    # -- events ----------------------------------------------------------------------
    def _event(self, event: dict[str, Any]) -> None:
        kind = event["event"]
        with self.condition:
            if kind == "ready":
                self.ready = event
            elif kind == "opened":
                game = self.opens.pop(str(event.get("command_id")), None)
                instance = event.get("game_instance_id")
                if (game is None or event.get("slot") != game.slot
                        or event.get("game_ref") != game.game_ref
                        or not isinstance(instance, str) or instance in self.instances):
                    self.counters["open_replies_discarded"] += 1
                else:
                    game.opened = event
                    self.instances[instance] = game
                    if game.start is not None:
                        try:
                            # Checked on arrival, before the world produces more rows;
                            # a missing echo fails closed.
                            require_first_frame_start(event.get("start"), start=game.start)
                        except BoundaryError as error:
                            game.start_mismatch = error.code
            elif kind == "open_refused":
                game = self.opens.pop(str(event.get("command_id")), None)
                if game is None:
                    self.counters["open_replies_discarded"] += 1
                else:
                    game.refused = event
                    self._free(game)
            elif kind == "closed":
                game = self.instances.get(str(event.get("game_instance_id")))
                if game is None or game.closed is not None:
                    # Late close (closed, restarted or loaded game): never reapplied.
                    self.counters["late_closes_discarded"] += 1
                else:
                    game.closed = event
                    self._liveness(event)
                    if not game.fenced and not game.hold_slot:
                        self._free(game)
            elif kind == "build_output_stopped":
                self.counters["build_output_stopped"] += 1
                self.unavailable = self.unavailable or (
                    "fast_env_build_output_stopped:" + str(event.get("reason")))
            elif kind == "evidence_failed":
                # The environment's evidence sink failed (WP2 #438: a ``fatal`` follows).
                # Loud; the named game is excluded from statistics at close.
                self.counters["evidence_failed_events"] += 1
                game = self.instances.get(str(event.get("game_instance_id")))
                if game is not None and game.evidence_failure is None:
                    game.evidence_failure = str(event.get("reason"))
                print(f"fast_env evidence_failed slot={event.get('slot')} "
                      f"game={event.get('game_instance_id')} reason={event.get('reason')}",
                      file=sys.stderr, flush=True)
            elif kind == "stopped":
                self.stopped = True
            elif kind == "fatal":
                self.unavailable = self.unavailable or "fast_env_fatal:" + str(
                    event.get("reason"))
            self.condition.notify_all()

    def _free(self, game: _Game) -> None:
        # Once per game: a slot already handed to a later game is never freed again.
        if not game.slot_freed:
            game.slot_freed = True
            self.free.add(game.slot)
        self.games.pop(game.game_ref, None)

    def _liveness(self, close: Mapping[str, Any]) -> None:
        if close.get("detail") == "inference_timeout":
            self.inference_failures += 1
            if self.inference_failures >= self.streak_limit and not self.inference_down:
                self.inference_down = True
                self.inference_down_at = self.clock()
        else:
            self.inference_failures = 0

    # -- admission and one game ----------------------------------------------------------
    def admission_closed(self) -> str | None:
        """Why no new game can be offered now (``None`` = open)."""
        with self.condition:
            if self.unavailable is not None:
                return self.unavailable
        if self._inference_unavailable():
            return "inference_service_unavailable_before_offer"
        return None

    def _inference_unavailable(self) -> bool:
        """Probe when one is wired; otherwise a timeout streak locks admission until
        ``inference_retry_seconds`` pass, then lets one trial game through: one more
        inference timeout locks it again (the streak counter stays one short)."""
        now = self.clock()
        if self.probe is not None:
            with self.condition:
                due = self.probed_at is None or now - self.probed_at >= self.probe_interval
                if due:
                    self.probed_at = now
            if due:
                alive = bool(self.probe())
                with self.condition:
                    self.inference_down = not alive
                    self.inference_down_at = None if alive else now
                    if alive:
                        self.inference_failures = 0
        else:
            with self.condition:
                if (self.inference_down and self.inference_down_at is not None
                        and now - self.inference_down_at >= self.inference_retry):
                    self.inference_down, self.inference_down_at = False, None
                    self.inference_failures = self.streak_limit - 1
                    self.counters["inference_retry_trials"] += 1
        with self.condition:
            return self.inference_down

    def reserve(self, timeout: float = 60) -> Reservation:
        """One free slot, before any world offer. Refusals are ``not_started``."""
        closed = self.admission_closed()
        if closed is not None:
            raise FastEnvUnavailable(closed if closed.endswith("before_offer")
                                     else "fast_env_unavailable_before_offer")
        deadline = self.clock() + timeout
        with self.condition:
            while not self.free and self.unavailable is None:
                left = deadline - self.clock()
                if left <= 0:
                    raise FastEnvUnavailable("fast_env_no_free_slot_before_offer")
                self.condition.wait(min(left, 1))
            if self.unavailable is not None:
                raise FastEnvUnavailable("fast_env_unavailable_before_offer")
            slot = min(self.free)
            self.free.remove(slot)
            return Reservation(slot)

    def release(self, reservation: Reservation) -> None:
        """Return a reserved slot whose world was never offered."""
        with self.condition:
            if not reservation.used:
                self.free.add(reservation.slot)
                self.condition.notify_all()

    def play(self, reservation: Reservation, *, seed: str,
             initial_progress: Mapping[str, Any] | None, start: EpisodeStart, r7: bool,
             deadline_seconds: float, retain_originals: bool = False,
             on_opened: Callable[[Mapping[str, Any]], None] | None = None) -> dict[str, Any]:
        """Open on the reserved slot (the world is already durably offered) and wait.

        Returns the environment's ``closed`` event, or a synthetic close. Never raises
        after the open was sent; never sends a second open for this game. ``on_opened``
        runs once, off the reader thread, after a checked ``opened``; if it raises, the
        game is closed (``close_slot``) and the failure is recorded, never retried.
        """
        if reservation.used:
            reject("fast_chain_reservation_reused")
        reservation.used = True
        command_id = f"c{next(self.commands)}"
        game = _Game(reservation.game_ref, reservation.slot, command_id, start=start)
        with self.condition:
            if (self.unavailable is not None or self.exit_reason is not None
                    or self.process is None):
                # Stopping, fenced, output stopped, killed or exited: nothing is sent,
                # so no world exists for this offer (censored, not unknown).
                self.free.add(game.slot)
                self.condition.notify_all()
                return {"event": "open_not_sent", "slot": game.slot,
                        "game_ref": game.game_ref,
                        "reason": self.unavailable or self.exit_reason}
            self.games[game.game_ref] = game
            self.opens[command_id] = game
            process = self.process
        try:
            process.send({"cmd": "open", "command_id": command_id,
                          "slot": game.slot, "game_ref": game.game_ref, "seed": seed,
                          "initial_progress": (dict(initial_progress)
                                               if initial_progress is not None else None),
                          "start": start.env_start(), "r7": r7,
                          "retain_originals": retain_originals})
        except (OSError, ValueError):
            # The open may or may not have been read: unknown, and the group is killed.
            self._kill("fast_env_open_delivery_unknown")
            return self._result(game)
        began = self.clock()
        with self.condition:
            self._wait(lambda: game.opened is not None or game.refused is not None
                       or game.closed is not None, began + self.open_seconds)
            if game.refused is not None:
                return {"event": "open_refused", **game.refused}
            if game.closed is not None and game.opened is None:
                return self._result(game)
            # A game that opened is attested even if it already closed (a short game
            # still has segments the learner must be able to account for).
            opened = dict(game.opened) if game.opened is not None else None
            if opened is None:
                # No answer in time. The registration stays, so a late ``opened`` and
                # its ``closed`` still route here; the slot is asked to close now.
                self.counters["open_timeouts"] += 1
            elif game.start_mismatch is not None:
                self.counters["start_mismatch_close_slot"] += 1
        playing = opened is not None and game.start_mismatch is None
        if opened is not None and playing and on_opened is not None:
            try:
                on_opened(opened)
            except Exception as error:  # noqa: BLE001 - recorded, typed, never resent
                game.admission_failure = (getattr(error, "code", None)
                                          or type(error).__name__)
                self.counters["instance_admission_close_slot"] += 1
                playing = False
        with self.condition:
            if playing:
                self._wait(lambda: game.closed is not None, began + deadline_seconds)
                if game.closed is not None:
                    return self._result(game)
                self.counters["deadline_close_slot"] += 1
            if game.closed is not None:
                return self._result(game)
            # Keep the slot until close_slot is written, so it can never reach a
            # later game opened on the same slot.
            game.hold_slot = True
            # The environment ignores (and counts) a close_slot naming another
            # instance; an open that was never answered has no instance to name.
            instance = (game.opened or {}).get("game_instance_id")
        # Ask this slot to close at its next boundary.
        with suppress(OSError, ValueError):
            process.send({"cmd": "close_slot", "command_id": f"c{next(self.commands)}",
                          "slot": game.slot,
                          **({"game_instance_id": instance} if instance else {})})
        with self.condition:
            game.hold_slot = False
            if game.closed is not None and not game.fenced:
                self._free(game)
                self.condition.notify_all()
            self._wait(lambda: game.closed is not None, self.clock() + self.bound.seconds)
            if game.closed is not None:
                return self._result(game)
            # Fence: this slot stays occupied, nothing new is admitted, the other
            # games finish, then the process group is killed. Only this game is unknown.
            game.fenced = True
            self.counters["slots_fenced"] += 1
            self.unavailable = self.unavailable or "fast_env_slot_fenced"
            self.condition.notify_all()
            self._wait(lambda: game.closed is not None or all(
                other.closed is not None or other.fenced for other in self.games.values()),
                None)
        if game.closed is None:
            self._kill("fast_env_game_close_unconfirmed")
        return self._result(game)

    def _wait(self, ready: Callable[[], bool], deadline: float | None) -> None:
        while not ready():
            if deadline is None:
                self.condition.wait(1)
                continue
            left = deadline - self.clock()
            if left <= 0:
                return
            self.condition.wait(min(left, 1))

    def _result(self, game: _Game) -> dict[str, Any]:
        with self.condition:
            close = dict(game.closed or {})
            if game.opened is not None:
                close.setdefault("game_instance_id", game.opened.get("game_instance_id"))
                close["opened"] = {k: game.opened.get(k) for k in (
                    "game_instance_id", "start", "native", "mount_ms")}
            close["slot"], close["game_ref"] = game.slot, game.game_ref
            if game.start_mismatch is not None:
                close["start_mismatch"] = game.start_mismatch
            if game.admission_failure is not None:
                close["instance_admission_failure"] = game.admission_failure
            if game.evidence_failure is not None:
                close["evidence_failure_event"] = game.evidence_failure
            if close.get("synthetic"):
                close["cleanup_confirmed"] = self.cleanup_confirmed is True
            self.games.pop(game.game_ref, None)
            return close

    # -- Stop ----------------------------------------------------------------------------
    def stop(self, reason: str = "stop") -> dict[str, Any]:
        """Stop admission, ``stop`` the process, wait at most the bound, then kill.

        Idempotent and safe from any thread (a signal handler starts it on its own
        thread); a second caller waits for the first and gets the same report.
        """
        with self._stop_lock:
            if self.stop_report is None:
                self.stop_report = self._stop(reason)
            return self.stop_report

    def _stop(self, reason: str) -> dict[str, Any]:
        with self.condition:
            self.unavailable = self.unavailable or "fast_env_stopping_before_offer"
            began = self.clock()
            exited = self.exit_reason is not None
        sent = False
        if not exited and self.process is not None:
            try:
                self.process.send({"cmd": "stop", "command_id": f"c{next(self.commands)}"})
                sent = True
            except (OSError, ValueError):
                pass
        with self.condition:
            if sent:
                self._wait(lambda: self.stopped and self.exit_reason is not None,
                           began + self.bound.seconds)
            latency = self.clock() - began
            within = self.stopped or exited
        killed = False
        if not within or self.exit_reason is None:
            killed = True
            self._kill("stop_latency_bound_exceeded")
        elif self.process is not None:
            self.cleanup_confirmed = self.process.kill_group(timeout=2)
        report = {"reason": reason, "latency_seconds": latency,
                  "bound_seconds": self.bound.seconds, "within_bound": within and not killed,
                  "killed": killed, "cleanup_confirmed": self.cleanup_confirmed is True,
                  "counters": dict(self.counters), "exit_reason": self.exit_reason}
        if self.process is not None:
            self.process.close()
        return report


# -- R7 selection ------------------------------------------------------------------------
R7_STATE_SCHEMA = "spireagent/fast-chain-r7-state-1"
R7_CONFIG_KEYS = frozenset({"key", "quota", "expected_games", "window_seconds", "state_path"})


class R7Selection:
    """One actor's ``open.r7`` decision: ``stpd.rl.r7_audit.R7Sampler`` (D line), persisted.

    Called once per game before its learner admission and world offer (design v2 §2.9:
    chosen before the game opens). The sampler sees only the clock window and the game
    ordinal, never the seed, content or model. The decision is written into the ledger
    row (``r7``) and made durable by the offer checkpoint. After every choice the
    window's opened count is persisted (atomic replace) so a restarted actor continues
    the window with ``R7Sampler.resume``; a state written with other sampler
    parameters, or for an older window, is not resumed (coverage only). An unreadable
    state file is refused loudly. The key is never written out, only its digest.

    One instance per actor: executors are rebuilt per model version and share it.
    """

    def __init__(self, sampler: Any, *, state_path: Path,
                 clock: Callable[[], float] = time.time) -> None:
        self.sampler, self.state_path, self.clock = sampler, state_path, clock
        self.lock = threading.Lock()
        self.identity = {
            "key_sha256": hashlib.sha256(sampler.key.encode()).hexdigest(),
            "quota": sampler.quota, "expected_games": sampler.expected_games,
            "window_seconds": sampler.window_seconds}
        self.resumed: dict[str, Any] | None = None
        # Coverage from what actually happened to chosen games (the sampler's own report
        # counts ordinals: one chosen before a failed admission never ran).
        self.coverage: Counter[str] = Counter()
        self._resume()

    @classmethod
    def from_configuration(cls, value: Any, *, work: Path,
                           clock: Callable[[], float] = time.time) -> R7Selection:
        from stpd.rl.r7_audit import R7Sampler

        if (not isinstance(value, dict) or set(value) - R7_CONFIG_KEYS
                or not {"key", "quota", "expected_games"} <= set(value)
                or "state_path" in value and (not isinstance(value["state_path"], str)
                                              or not value["state_path"])):
            reject("fast_chain_r7_configuration_invalid")
        try:
            sampler = R7Sampler(key=value["key"], quota=value["quota"],
                                expected_games=value["expected_games"],
                                window_seconds=value.get("window_seconds", 3600.0))
        except (TypeError, ValueError):
            reject("fast_chain_r7_configuration_invalid")
        state = Path(value["state_path"]) if "state_path" in value else (
            work / "fast-chain-r7-state.json")
        return cls(sampler, state_path=state, clock=clock)

    def _resume(self) -> None:
        try:
            raw = self.state_path.read_bytes()
        except FileNotFoundError:
            return
        try:
            state = json.loads(raw)
            fields = {key: state[key] for key in self.identity}
            window, opened = state["window"], state["opened"]
            if (state.get("schema") != R7_STATE_SCHEMA or type(window) is not int
                    or type(opened) is not int or opened < 0):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            reject("fast_chain_r7_state_invalid")
        now = self.clock()
        current = int(now // self.sampler.window_seconds)
        if fields != self.identity or window != current:
            self.resumed = {"resumed": False, "state_window": window, "window": current,
                            "same_parameters": fields == self.identity}
            return
        self.sampler.resume(now, opened)
        self.resumed = {"resumed": True, "window": window, "opened": opened}

    def _persist(self) -> None:
        body = json_bytes({"schema": R7_STATE_SCHEMA, **self.identity,
                           "window": self.sampler.window, "opened": self.sampler.ordinal})
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        with open(temporary, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_path)

    def __call__(self) -> dict[str, Any]:
        """Decide one game; a failure is the caller's typed not_started (never a default)."""
        with self.lock:
            selected = self.sampler.select(self.clock())
            record = {"selected": selected, "window": self.sampler.window,
                      "ordinal": self.sampler.ordinal - 1}
            self._persist()
        return record

    def count(self, stage: str) -> None:
        """One chosen game reached ``stage`` (offered, opened, finished, censored, ...)."""
        with self.lock:
            self.coverage[stage] += 1

    def report(self) -> dict[str, Any]:
        with self.lock:
            return {"sampler_ordinals": self.sampler.report(), "resume": self.resumed,
                    "r7_games": dict(self.coverage),
                    "state_path_name": self.state_path.name}


# -- ledger executor ---------------------------------------------------------------------
def retain_originals(row: Mapping[str, Any]) -> bool:
    """``open.retain_originals``: the old chain's keep-the-original rule at open.

    The same facts ``managed_retention.preservation_route`` uses before any result:
    every evaluation game, and a training game the open-time 1/``audit_every`` sample
    chose (``training_audit_selected``, set by learner admission; 5% at 20). Abnormal
    closes are kept by the environment's sink at close regardless (WP2).
    """
    retention = row.get("retention") or {}
    return (retention.get("purpose") == "evaluation"
            or row.get("training_audit_selected") is True)


_CENSORED = {"stop", "budget", "not_started"}


def apply_close(row: dict[str, Any], close: Mapping[str, Any], *, start: EpisodeStart) -> None:
    """Map one environment close onto the ledger row (statuses the ledger already owns).

    ``not_started`` closes (inference timeout, binding mismatch, refused, Stop before
    send, compact divergence) are censored games, never unknown and never resent.
    """
    reason, detail = close.get("reason"), close.get("detail")
    native = close.get("native")
    row["game_instance_id"] = close.get("game_instance_id")
    row["fast_chain_slot"] = close.get("slot")
    row["fast_chain_close"] = {k: close.get(k) for k in (
        "reason", "detail", "decisions", "native_steps", "last_step_index", "terminal",
        "public_facts", "native", "evidence_dir", "evidence_failed", "synthetic")}
    if close.get("event") == "open_not_sent":
        row.update(status="censored", reason="fast_chain_open_not_sent:" + str(
            close.get("reason")), cleanup_accepted=True)
        return

    if close.get("event") == "open_refused":
        # The environment refused the open; its native child was replaced if it started.
        row.update(status="censored", reason="fast_chain_open_refused:" + str(
            close.get("reason")), cleanup_accepted=True)
        return
    if reason not in CLOSE_REASONS:
        row.update(status="unknown", reason="fast_chain_close_reason_invalid",
                   cleanup_accepted=False)
        return
    if reason not in {"unknown", "env_crash"}:
        # Fail closed: the opened echo is the fast chain's only proof that the world
        # began as offered; a missing echo or missing ``start`` is a mismatch too.
        mismatch = close.get("start_mismatch")
        if mismatch is None:
            try:
                require_first_frame_start((close.get("opened") or {}).get("start"),
                                          start=start)
            except BoundaryError as error:
                mismatch = error.code
        if mismatch is not None:
            row.update(status="unknown", reason=mismatch,
                       cleanup_accepted=native in {"kept", "replaced"})
            return
        # WP2 #438: ``closed.evidence_failed`` (absent = false; true => evidence_dir null),
        # or an ``evidence_failed`` event naming this game. Its record is incomplete:
        # never scored, never trained on, counted on its own and reported loudly.
        flag = close.get("evidence_failed", False)
        if type(flag) is not bool:
            row.update(status="unknown", reason="fast_chain_close_evidence_failed_invalid",
                       cleanup_accepted=native in {"kept", "replaced"})
            return
        if flag or close.get("evidence_failure_event") is not None:
            row["evidence_failed"] = {"close_flag": flag,
                                      "event": close.get("evidence_failure_event"),
                                      "evidence_dir": close.get("evidence_dir")}
            row.update(status="censored", reason=f"fast_chain_evidence_failed:{reason}",
                       training_excluded=True,
                       cleanup_accepted=native in {"kept", "replaced"})
            print(f"fast_chain game {row['game_instance_id']} evidence_failed "
                  f"(close {reason}); excluded from statistics and training",
                  file=sys.stderr, flush=True)
            return
        admission = close.get("instance_admission_failure")
        if admission is not None:
            # Closed early; never scored, never resent. Whether the learner attested
            # the instance is not known here: after a delivery failure it may have
            # (the reply was lost). The learner treats any instance the actor closed
            # this way as truncated (FAST_INSTANCE_ADMISSION, C-SCALE WP8).
            row.update(status="censored",
                       reason=f"fast_chain_instance_admission_failed:{admission}",
                       cleanup_accepted=native in {"kept", "replaced"})
            return
    if reason == "terminal":
        terminal = close.get("terminal")
        if not isinstance(terminal, Mapping) or terminal.get("result") not in {
                "victory", "defeat"}:
            row.update(status="unknown", reason="fast_chain_terminal_without_result",
                       cleanup_accepted=native in {"kept", "replaced"})
            return
        row.update(status="finished", reason=None, cleanup_accepted=True,
                   observed_result=terminal["result"])
        return
    if reason in _CENSORED:
        row.update(status="censored", reason=f"fast_chain_{reason}:{detail}",
                   cleanup_accepted=native in {"kept", "replaced"})
        return
    # unknown or env_crash: the native process must be gone (replaced or killed).
    if close.get("evidence_failed") is True or close.get("evidence_failure_event") is not None:
        row["evidence_failed"] = {"close_flag": close.get("evidence_failed") is True,
                                  "event": close.get("evidence_failure_event"),
                                  "evidence_dir": close.get("evidence_dir")}
    cleanup = (native == "replaced" if not close.get("synthetic")
               else close.get("cleanup_confirmed") is True)
    row.update(status="unknown", reason=f"fast_chain_{reason}:{detail}",
               cleanup_accepted=cleanup)
    row["failure_facts"] = {"execution_reason": row["reason"],
                            "fast_chain_close": row["fast_chain_close"]}


class FastChainEpisodeExecutor:
    """Ledger-compatible executor: one call = one game on a shared environment process."""

    def __init__(self, configuration: dict[str, Any], *, manager: GameManager,
                 episode_seconds: float,
                 before_world_offer: Callable[[dict[str, Any]], None] | None = None,
                 world_offered: Callable[[dict[str, Any]], None] | None = None,
                 instance_opened: Callable[[dict[str, Any], Mapping[str, Any]], None]
                 | None = None,
                 r7_select: Callable[[], Mapping[str, Any]] | None = None,
                 reserve_seconds: float = 60,
                 instance_excluded: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.configuration, self.manager = configuration, manager
        self.instance_opened = instance_opened
        # After the close of an opened game whose evidence failed: the learner drops
        # the instance's unused segments (``fast_instance_excluded``, WP8).
        self.instance_excluded = instance_excluded
        # R7 is chosen before learner admission and the world offer by the actor's one
        # ``R7Selection`` (``stpd.rl.r7_audit.R7Sampler``, D line), recorded in the row.
        # Not wired (``fast_chain.r7`` absent): no game is R7, and the row says so.
        self.r7_select = r7_select
        # The same frozen starts and progress-lineage check as the old chain (S4).
        self.episode_starts = decode_episode_starts(
            configuration.get("episode_starts", {}),
            initial_progress=configuration.get("initial_progress"))
        self.episode_seconds, self.reserve_seconds = episode_seconds, reserve_seconds
        self.before_world_offer = before_world_offer or (lambda row: None)
        self.world_offered = world_offered or (lambda row: None)
        self.preparation = {"chain": "fast_chain", "env_process_id":
                            manager.config["env_process_id"], "slots": manager.slots}
        self.execution_path: Path | None = None

    def __call__(self, row: dict[str, Any], manifest: dict[str, Any], artifacts: Any,
                 checkpoint: Callable[[], None]) -> None:
        row.update(host_kind="managed", chain="fast_chain", status="preparing", reason=None,
                   world_mutation_offered=False, cleanup_accepted=True)
        row["scenario"] = {"character": manifest["character"], "ascension": manifest["ascension"]}
        served = self.manager.config["host"].get("ascension")
        if type(served) is not int or manifest["ascension"] != served:
            # One level per env process (its Host session): another level would run at
            # the process's level while the row says otherwise. Never a default.
            row.update(status="not_started",
                       reason=f"fast_chain_ascension_unsupported:{manifest['ascension']}")
            print(f"fast_chain refuses ascension {manifest['ascension']} "
                  f"(this env process serves {served})", file=sys.stderr, flush=True)
            return
        try:
            start = (self.episode_starts.get(row["seed"])
                     or EpisodeStart.new_run(row["seed"]))
            start.require_scenario(character=manifest["character"],
                                   ascension=manifest["ascension"])
            reservation = self.manager.reserve(self.reserve_seconds)
        except BoundaryError as error:
            row.update(status="not_started", reason=error.code)
            return
        hook = self.instance_opened
        handed = False
        try:
            if self.r7_select is None:
                row["r7"] = {"selected": False, "sampler": None}
            else:
                try:
                    decision = dict(self.r7_select())
                    if type(decision.get("selected")) is not bool:
                        raise ValueError("r7_selected_not_bool")
                except Exception as error:  # noqa: BLE001 - never a silent R7 default
                    row.update(status="not_started", reason="fast_chain_r7_select_failed:"
                               + (getattr(error, "code", None) or str(error)
                                  or type(error).__name__))
                    return
                row["r7"] = decision
            try:
                self.before_world_offer(row)
            except Exception as error:  # noqa: BLE001 - any admission failure, typed
                row.update(status="not_started", reason=getattr(error, "code", None)
                           or type(error).__name__)
                return
            if start.kind != "new_run":
                row["episode_start"] = start.record()
            row.update(world_mutation_offered=True, fast_chain_slot=reservation.slot,
                       fast_chain_game_ref=reservation.game_ref, status="running")
            checkpoint()  # Durable offer BEFORE open; a lost reply is never reoffered.
            self._r7_count(row, "offered")
            try:
                self.world_offered(row)
            except Exception as error:  # noqa: BLE001
                # Offered but no open was ever sent: the slot comes back; the offered
                # row stays unknown (its watcher fact may be partial), never reoffered.
                row.update(status="unknown", reason="fast_chain_world_offer_event_failed:"
                           + type(error).__name__)
                checkpoint()
                return
            handed = True
            close = self.manager.play(
                reservation, seed=row["seed"],
                initial_progress=self.configuration.get("initial_progress"), start=start,
                r7=row["r7"]["selected"], retain_originals=retain_originals(row),
                deadline_seconds=self.episode_seconds + self.manager.bound.seconds,
                on_opened=(None if hook is None else lambda opened: hook(row, opened)))
        finally:
            if not handed:
                self.manager.release(reservation)
        apply_close(row, close, start=start)
        if close.get("opened") is not None:
            self._r7_count(row, "opened")
        self._r7_count(row, "status:" + str(row["status"]))
        if "evidence_failed" in row:
            with self.manager.condition:
                self.manager.counters["evidence_failed_games"] += 1
            if self.instance_excluded is not None and close.get("opened") is not None:
                self._exclude(row)
        row["fast_chain_close_record"] = artifacts.put("fast-chain-close.json",
                                                       json_bytes(close))
        checkpoint()

    def _exclude(self, row: dict[str, Any]) -> None:
        """Once; a failed or refused request is recorded, counted, loud, never resent."""
        assert self.instance_excluded is not None
        try:
            self.instance_excluded(row)
        except Exception as error:  # noqa: BLE001 - typed record, never a retry
            code = getattr(error, "code", None) or type(error).__name__
            row["fast_instance_excluded"] = {"excluded": False, "error": code}
            counter = "fast_instance_excluded_failed"
            print(f"fast_chain game {row.get('game_instance_id')} exclusion request "
                  f"failed: {code}; the learner may still use its unused segments",
                  file=sys.stderr, flush=True)
        else:
            counter = "fast_instance_excluded_sent"
        with self.manager.condition:
            self.manager.counters[counter] += 1

    def _r7_count(self, row: Mapping[str, Any], stage: str) -> None:
        count = getattr(self.r7_select, "count", None)
        if count is not None and (row.get("r7") or {}).get("selected") is True:
            count(stage)

    def admission_closed(self) -> str | None:
        return self.manager.admission_closed()

    def close(self) -> dict[str, Any] | None:
        report = getattr(self.r7_select, "report", None)
        return {"fast_chain_stop": self.manager.stop(),
                "r7_sampler": None if report is None else report()}

    def preserve_report(self, path: Path) -> dict[str, Any]:
        # Fast-chain rows never enter save_pending: originals stay with the environment
        # evidence sink (WP6). Reaching here is a ledger wiring defect, loudly.
        reject("fast_chain_has_no_save_stage")

    def finalize_trajectories(self, paths: list[Path]) -> NoReturn:
        reject("fast_chain_has_no_trajectory_stage")
