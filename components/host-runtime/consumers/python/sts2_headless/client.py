from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections import deque
from dataclasses import dataclass
import json
from queue import Empty, Queue
import subprocess
import threading
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4


class DriverError(RuntimeError):
    pass


class DriverCleanupError(DriverError):
    """The driver stopped without confirming its owned native child was reaped."""
    pass


class DriverInitializationError(DriverError):
    """Startup failed after spawn; the original failure is available as __cause__."""

    def __init__(self, cause: Exception, cleanup_error: DriverCleanupError | None):
        super().__init__(f"Driver initialization failed: {cause}")
        self.cleanup_error = cleanup_error
        self.cleanup_confirmed = cleanup_error is None


def canonicalize_episode_seed(seed: str) -> str:
    if not isinstance(seed, str):
        raise TypeError("Episode seed must be a string.")
    canonical = seed.strip().upper().replace("O", "0").replace("I", "1")
    if not canonical or len(canonical) > 64 or not canonical.isascii() or not canonical.isalnum():
        raise ValueError("Episode seed must canonicalize to 1-64 ASCII letters or digits.")
    return canonical


def _text_result_matches_delivery_contract(
    result: Any, mutation_id: str, action_id: str, input_profile: str,
) -> bool:
    """Check the public text-menu delivery matrix at the JSONL boundary."""
    version = "2" if input_profile == "text-menu-v2" else "1"
    if not isinstance(result, dict) or set(result) != {
        "protocol_version", "schema", "input_profile", "request_id", "status",
        "effect_domain", "native_delivery", "action", "reason_code", "detail",
        "retry", "successor", "attribution",
    }:
        return False
    if (result["protocol_version"] != "1.0.0"
            or result["schema"] != f"sts2.player-environment/text-menu-action-result-{version}"
            or result["input_profile"] != input_profile
            or result["request_id"] != mutation_id):
        return False
    status, effect = result["status"], result["effect_domain"]
    delivery, retry = result["native_delivery"], result["retry"]
    action, successor = result["action"], result["successor"]
    if (not isinstance(status, str) or status not in {"applied", "not_applied", "unknown"}
            or (effect is not None and not isinstance(effect, str))
            or effect not in {None, "text_menu", "native_input"}
            or (delivery is not None and not isinstance(delivery, str))
            or delivery not in {None, "delivered", "not_delivered", "unknown"}
            or not isinstance(retry, str) or retry not in {"never", "reobserve"}
            or (successor is not None and (
                not isinstance(successor, dict)
                or successor.get("schema") != f"sts2.player-environment/text-menu-snapshot-{version}"
                or successor.get("input_profile") != input_profile))):
        return False
    if action is not None and (
            not isinstance(action, dict) or action.get("action_id") != action_id
            or not isinstance(action.get("effect_domain"), str)
            or action.get("effect_domain") not in {"text_menu", "native_input"}
            or action.get("kind") not in (("system_navigation", "system_selection")
                if input_profile == "text-menu-v2" and action.get("effect_domain") == "text_menu"
                else ("system_navigation",) if action.get("effect_domain") == "text_menu"
                else ("native_input",))):
        return False
    if input_profile == "text-menu-v2" and action is not None:
        if (set(action) != {"action_id", "kind", "verb", "label", "subject_referent_id",
                            "arguments", "effect_domain"}
                or not isinstance(action["verb"], str) or not action["verb"]
                or not isinstance(action["label"], str) or not action["label"]
                or not isinstance(action["arguments"], list)):
            return False
        if action["kind"] == "system_selection" and (
                action["verb"] not in {"select_card", "select_target", "cancel_selection"}
                or action["arguments"]
                or (action["subject_referent_id"] is None) !=
                   (action["verb"] == "cancel_selection")):
            return False
        if action["kind"] == "system_navigation" and (
                action["subject_referent_id"] is not None or action["arguments"]):
            return False
    if status == "applied":
        return (action is not None and effect == action["effect_domain"]
                and delivery == (None if effect == "text_menu" else "delivered")
                and retry == "never"
                and (input_profile != "text-menu-v2" or effect != "text_menu" or successor is not None))
    if status == "unknown":
        return (effect == "native_input" and delivery == "unknown"
                and retry == "never" and successor is None
                and action is not None and action["kind"] == "native_input")
    return (delivery not in {"delivered", "unknown"}
            and (effect != "text_menu" or delivery is None)
            and (action is None or effect is None or action["effect_domain"] == effect)
            and (successor is None or retry == "reobserve"))


def _text_profile(input_profile: str) -> str:
    if input_profile not in {"text-menu-v1", "text-menu-v2"}:
        raise ValueError("Text-menu input_profile must be text-menu-v1 or text-menu-v2.")
    return "2" if input_profile == "text-menu-v2" else "1"


@dataclass(frozen=True)
class FiniteActionView:
    snapshot_id: str
    action_ids: tuple[str, ...]
    actions: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> "FiniteActionView":
        catalog = snapshot.get("bound_actions")
        if not isinstance(catalog, Mapping) or catalog.get("status") != "complete":
            raise DriverError("Snapshot does not contain a complete finite BoundAction projection.")
        actions = catalog.get("actions")
        if not isinstance(actions, list):
            raise DriverError("Snapshot BoundActions must be a list.")
        if any(not isinstance(action, Mapping) for action in actions):
            raise DriverError("Every Snapshot BoundAction must be an object.")
        ids = tuple(action.get("bound_action_id") for action in actions)
        if any(not isinstance(identifier, str) or not identifier for identifier in ids):
            raise DriverError("Snapshot contains an empty BoundAction identity.")
        if len(ids) != len(set(ids)):
            raise DriverError("Snapshot contains duplicate BoundAction identities.")
        return cls(str(snapshot["snapshot_id"]), ids, tuple(actions))


class ManagedPlayerEnvironment:
    def __init__(self, command: Sequence[str], *, response_timeout_seconds: float = 120.0):
        if not command:
            raise ValueError("A non-empty driver command is required.")
        if response_timeout_seconds <= 0:
            raise ValueError("response_timeout_seconds must be positive.")
        self._response_timeout_seconds = response_timeout_seconds
        self._process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._closed = False
        self._cleanup_error: DriverCleanupError | None = None
        self._close_lock = threading.Lock()
        self._cleanup_done = threading.Event()
        self._cleanup_started = False
        self._forced_fallback = False
        self._exchange_lock = threading.RLock()
        self._stdout_lines: Queue[str | None] = Queue()
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._stdout_thread = threading.Thread(target=self._drain_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        try:
            self.ready = self._read_message()
            if self.ready.get("type") != "ready":
                raise DriverError(f"Driver did not become ready: {self.ready!r}")
        except Exception as error:
            try:
                self.close(force=True)
            except DriverCleanupError as cleanup:
                raise DriverInitializationError(error, cleanup) from error
            raise DriverInitializationError(error, None) from error

    def _quarantine_preserving(self, original: Exception) -> None:
        try:
            self.close(force=True)
        except DriverCleanupError as cleanup:
            # Python 3.9 has no BaseException.add_note(). Preserve the first
            # failure and make the independent cleanup failure inspectable.
            original.cleanup_error = cleanup

    def _drain_stdout(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            self._stdout_lines.put(line)
        self._stdout_lines.put(None)

    def _drain_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            self._stderr_tail.append(line.rstrip())

    def _stderr_detail(self) -> str:
        return "\n".join(self._stderr_tail).strip()

    def _read_message(self) -> dict[str, Any]:
        try:
            line = self._stdout_lines.get(timeout=self._response_timeout_seconds)
        except Empty as error:
            detail = self._stderr_detail()
            suffix = f": {detail}" if detail else ""
            raise DriverError(
                f"Driver response timed out after {self._response_timeout_seconds:g}s{suffix}"
            ) from error
        if line is None:
            detail = self._stderr_detail()
            raise DriverError(f"Driver exited before replying: {detail}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise DriverError("Driver response must be a JSON object.")
        return value

    def _exchange(self, command: str, **payload: Any) -> dict[str, Any]:
        with self._exchange_lock:
            if self._closed and command != "close":
                raise DriverError("Driver is closed.")
            request_id = uuid4().hex
            request = {"command": command, "request_id": request_id, **payload}
            encoded = json.dumps(request, separators=(",", ":")) + "\n"
            assert self._process.stdin is not None
            try:
                # Once offered to the child, an unparsable/missing response can
                # no longer establish whether a mutation reached native code.
                self._process.stdin.write(encoded)
                self._process.stdin.flush()
                response = self._read_message()
            except Exception as error:
                if command != "close":
                    self._quarantine_preserving(error)
                if isinstance(error, DriverError):
                    raise
                raise DriverError("Driver exchange failed after request offer; delivery is uncertain.") from error
            if response.get("request_id") != request_id:
                error = DriverError("Driver response request identity mismatch.")
                if command != "close":
                    self._quarantine_preserving(error)
                raise error
            if response.get("type") == "error":
                error = DriverError(str(response.get("message", "driver request failed")))
                if command in {"reset", "step", "text_submit"}:
                    self._quarantine_preserving(error)
                raise error
            return response

    def reset(self, seed: str) -> Mapping[str, Any]:
        return self._exchange("reset", seed=canonicalize_episode_seed(seed))["snapshot"]

    def observe(self) -> Mapping[str, Any]:
        return self._exchange("observe")["snapshot"]

    def observe_text_menu(self, *, input_profile: str = "text-menu-v1") -> Mapping[str, Any]:
        """Get one text-menu Snapshot and Host-owned game continuity together."""
        version = _text_profile(input_profile)
        profile_payload = {"input_profile": input_profile} if version == "2" else {}
        response = self._exchange("text_observe", **profile_payload)
        context = response.get("context")
        if (response.get("type") != "text_observe_result"
                or not isinstance(context, dict)
                or set(context) != {"schema", "snapshot", "game_continuity_id"}
                or context["schema"] != f"sts2.player-environment/text-menu-observation-context-{version}"
                or not isinstance(context["snapshot"], dict)
                or context["snapshot"].get("schema") != f"sts2.player-environment/text-menu-snapshot-{version}"
                or context["snapshot"].get("input_profile") != input_profile
                or not isinstance(context["game_continuity_id"], str)
                or not context["game_continuity_id"]):
            error = DriverError("Driver text-menu observation context is invalid.")
            self._quarantine_preserving(error)
            raise error
        return context

    def submit_text_menu(
        self, action_id: str, expected_snapshot_id: str,
        expected_game_continuity_id: str, request_id: str | None = None,
        *, input_profile: str = "text-menu-v1",
    ) -> Mapping[str, Any]:
        """Submit one exact current text action; never retry unknown delivery."""
        version = _text_profile(input_profile)
        if any(not isinstance(value, str) or not value for value in (
                action_id, expected_snapshot_id, expected_game_continuity_id)):
            raise ValueError("Text-menu submission requires action, Snapshot and continuity IDs.")
        if request_id is not None and (not isinstance(request_id, str) or not request_id):
            raise ValueError("Text-menu request ID must be a non-empty string.")
        mutation_id = request_id or uuid4().hex
        response = self._exchange(
            "text_submit", action_id=action_id,
            expected_snapshot_id=expected_snapshot_id,
            expected_game_continuity_id=expected_game_continuity_id,
            mutation_request_id=mutation_id,
            **({"input_profile": input_profile} if version == "2" else {}),
        )
        result = response.get("result")
        if (response.get("type") != "text_submit_result"
                or not _text_result_matches_delivery_contract(result, mutation_id, action_id, input_profile)):
            error = DriverError("Driver text-menu action result is invalid.")
            self._quarantine_preserving(error)
            raise error
        if result["status"] == "unknown":
            # Return the original correlated result, but this child can never
            # receive another mutation after an unknown native delivery.
            self._quarantine_preserving(DriverError("Native delivery is unknown."))
        return result

    def read(self, read_id: str, expected_snapshot_id: str) -> Mapping[str, Any]:
        return self._exchange(
            "read", read_id=read_id, expected_snapshot_id=expected_snapshot_id
        )["read"]

    def step(
        self,
        bound_action_id: str,
        expected_snapshot_id: str,
        request_id: str | None = None,
    ) -> Mapping[str, Any]:
        return self._exchange(
            "step",
            bound_action_id=bound_action_id,
            expected_snapshot_id=expected_snapshot_id,
            mutation_request_id=request_id or uuid4().hex,
        )["receipt"]

    def episode_identity(self) -> Mapping[str, Any]:
        return self._exchange("episode_identity")["identity"]

    def close(self, force: bool = False) -> None:
        with self._close_lock:
            owner = not self._cleanup_started
            self._cleanup_started = True
            self._closed = True
        if not owner:
            if force and not self._cleanup_done.is_set():
                self._close_stdin()
                if not self._cleanup_done.wait(timeout=3):
                    self._forced_fallback = True
                    if self._process.poll() is None:
                        self._process.terminate()
                    if not self._cleanup_done.wait(timeout=2):
                        if self._process.poll() is None:
                            self._process.kill()
                        if not self._cleanup_done.wait(timeout=5):
                            raise DriverCleanupError("concurrent forced cleanup did not finish")
            elif not self._cleanup_done.wait(timeout=10):
                raise DriverCleanupError("driver cleanup is still in progress")
            if self._cleanup_error is not None:
                raise self._cleanup_error
            return
        cleanup_reason: str | None = None
        try:
            if not force and self._process.poll() is None:
                self._exchange("close")
        finally:
            if self._process.poll() is None:
                # EOF is the cross-platform request for Node to close the
                # native child it owns. Windows terminate() is abrupt and
                # cannot run Node's JavaScript signal handler.
                self._close_stdin()
            try:
                try:
                    code = self._process.wait(timeout=3 if force else 5)
                except subprocess.TimeoutExpired:
                    # Terminating Node does not prove cleanup of its child.
                    cleanup_reason = "driver did not finish EOF cleanup before fallback termination"
                    self._process.terminate()
                    try:
                        code = self._process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        code = self._process.wait(timeout=5)
                if self._forced_fallback:
                    cleanup_reason = "concurrent forced cleanup required fallback termination"
                if code != 0:
                    cleanup_reason = f"driver exited with code {code}; native-child cleanup unconfirmed"
            except Exception as error:
                cleanup_reason = f"driver cleanup failed: {error}; native-child cleanup unconfirmed"
            finally:
                try:
                    for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
                        if stream is not None and not stream.closed:
                            stream.close()
                    self._stdout_thread.join(timeout=1)
                    self._stderr_thread.join(timeout=1)
                finally:
                    if cleanup_reason is not None:
                        self._cleanup_error = DriverCleanupError(cleanup_reason)
                    self._cleanup_done.set()
        if self._cleanup_error is not None:
            raise self._cleanup_error

    def _close_stdin(self) -> None:
        if self._process.stdin is not None and not self._process.stdin.closed:
            try:
                self._process.stdin.close()
            except (OSError, ValueError):
                pass

    def __enter__(self) -> "ManagedPlayerEnvironment":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class SyncVectorPlayerEnvironment:
    def __init__(self, environments: Iterable[ManagedPlayerEnvironment]):
        self.environments = tuple(environments)
        if not self.environments:
            raise ValueError("At least one environment is required.")

    def reset(self, seeds: Sequence[str]) -> tuple[Mapping[str, Any], ...]:
        if len(seeds) != len(self.environments):
            raise ValueError("One seed per environment is required.")
        return tuple(environment.reset(seed) for environment, seed in zip(self.environments, seeds))

    def step(
        self, actions: Sequence[tuple[str, str]]
    ) -> tuple[Mapping[str, Any], ...]:
        if len(actions) != len(self.environments):
            raise ValueError("One action per environment is required.")
        return tuple(
            environment.step(bound_action_id, snapshot_id)
            for environment, (bound_action_id, snapshot_id) in zip(self.environments, actions)
        )

    def close(self) -> None:
        for environment in self.environments:
            environment.close()


class ThreadedVectorPlayerEnvironment(SyncVectorPlayerEnvironment):
    def reset(self, seeds: Sequence[str]) -> tuple[Mapping[str, Any], ...]:
        if len(seeds) != len(self.environments):
            raise ValueError("One seed per environment is required.")
        pairs = tuple(zip(self.environments, seeds))
        with ThreadPoolExecutor(max_workers=len(pairs)) as executor:
            return tuple(executor.map(lambda pair: pair[0].reset(pair[1]), pairs))

    def step(
        self, actions: Sequence[tuple[str, str]]
    ) -> tuple[Mapping[str, Any], ...]:
        if len(actions) != len(self.environments):
            raise ValueError("One action per environment is required.")
        pairs = tuple(zip(self.environments, actions))
        with ThreadPoolExecutor(max_workers=len(pairs)) as executor:
            return tuple(executor.map(
                lambda pair: pair[0].step(pair[1][0], pair[1][1]), pairs
            ))
