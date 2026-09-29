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


def canonicalize_episode_seed(seed: str) -> str:
    if not isinstance(seed, str):
        raise TypeError("Episode seed must be a string.")
    canonical = seed.strip().upper().replace("O", "0").replace("I", "1")
    if not canonical or len(canonical) > 64 or not canonical.isascii() or not canonical.isalnum():
        raise ValueError("Episode seed must canonicalize to 1-64 ASCII letters or digits.")
    return canonical


def _text_result_matches_delivery_contract(
    result: Any, mutation_id: str, action_id: str,
) -> bool:
    """Check the published text-menu v1 delivery matrix at the JSONL boundary."""
    if not isinstance(result, dict) or set(result) != {
        "protocol_version", "schema", "input_profile", "request_id", "status",
        "effect_domain", "native_delivery", "action", "reason_code", "detail",
        "retry", "successor", "attribution",
    }:
        return False
    if (result["protocol_version"] != "1.0.0"
            or result["schema"] != "sts2.player-environment/text-menu-action-result-1"
            or result["input_profile"] != "text-menu-v1"
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
                or successor.get("schema") != "sts2.player-environment/text-menu-snapshot-1"
                or successor.get("input_profile") != "text-menu-v1"))):
        return False
    if action is not None and (
            not isinstance(action, dict) or action.get("action_id") != action_id
            or not isinstance(action.get("effect_domain"), str)
            or action.get("effect_domain") not in {"text_menu", "native_input"}
            or action.get("kind") != (
                "system_navigation" if action.get("effect_domain") == "text_menu" else "native_input")):
        return False
    if status == "applied":
        return (action is not None and effect == action["effect_domain"]
                and delivery == (None if effect == "text_menu" else "delivered")
                and retry == "never")
    if status == "unknown":
        return (effect == "native_input" and delivery == "unknown"
                and retry == "never" and successor is None
                and action is not None and action["kind"] == "native_input")
    return (delivery not in {"delivered", "unknown"}
            and (effect != "text_menu" or delivery is None)
            and (action is None or effect is None or action["effect_domain"] == effect)
            and (successor is None or retry == "reobserve"))


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
        self._exchange_lock = threading.RLock()
        self._stdout_lines: Queue[str | None] = Queue()
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._stdout_thread = threading.Thread(target=self._drain_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        self.ready = self._read_message()
        if self.ready.get("type") != "ready":
            self.close(force=True)
            raise DriverError(f"Driver did not become ready: {self.ready!r}")

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
            self.close(force=True)
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
            if self._closed:
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
                self.close(force=True)
                if isinstance(error, DriverError):
                    raise
                raise DriverError("Driver exchange failed after request offer; delivery is uncertain.") from error
            if response.get("request_id") != request_id:
                self.close(force=True)
                raise DriverError("Driver response request identity mismatch.")
            if response.get("type") == "error":
                if command in {"reset", "step", "text_submit"}:
                    self.close(force=True)
                raise DriverError(str(response.get("message", "driver request failed")))
            return response

    def reset(self, seed: str) -> Mapping[str, Any]:
        return self._exchange("reset", seed=canonicalize_episode_seed(seed))["snapshot"]

    def observe(self) -> Mapping[str, Any]:
        return self._exchange("observe")["snapshot"]

    def observe_text_menu(self) -> Mapping[str, Any]:
        """Get one text-menu Snapshot and Host-owned game continuity together."""
        response = self._exchange("text_observe")
        context = response.get("context")
        if (response.get("type") != "text_observe_result"
                or not isinstance(context, dict)
                or set(context) != {"schema", "snapshot", "game_continuity_id"}
                or context["schema"] != "sts2.player-environment/text-menu-observation-context-1"
                or not isinstance(context["snapshot"], dict)
                or context["snapshot"].get("input_profile") != "text-menu-v1"
                or not isinstance(context["game_continuity_id"], str)
                or not context["game_continuity_id"]):
            raise DriverError("Driver text-menu observation context is invalid.")
        return context

    def submit_text_menu(
        self, action_id: str, expected_snapshot_id: str,
        expected_game_continuity_id: str, request_id: str | None = None,
    ) -> Mapping[str, Any]:
        """Submit one exact current text action; never retry unknown delivery."""
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
        )
        result = response.get("result")
        if (response.get("type") != "text_submit_result"
                or not _text_result_matches_delivery_contract(result, mutation_id, action_id)):
            self.close(force=True)
            raise DriverError("Driver text-menu action result is invalid.")
        if result["status"] == "unknown":
            # Return the original correlated result, but this child can never
            # receive another mutation after an unknown native delivery.
            self.close(force=True)
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
        if self._closed:
            return
        try:
            if not force and self._process.poll() is None:
                self._exchange("close")
        finally:
            self._closed = True
            if self._process.poll() is None:
                self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
            self._stdout_thread.join(timeout=1)
            self._stderr_thread.join(timeout=1)

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
