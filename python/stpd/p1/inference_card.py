"""One GPU card's rl-inference-3 services: K independent services, optionally under MPS.

`InferenceCard` starts `services` (K, default 1) host processes. Each builds one
`FastInferenceService` through an importable ``module:function`` factory, so each
has its own main process (links, assembler, draws, replies) and its own device
process; with K > 1 they share the card through one CUDA MPS control daemon
started for this card before any service touches CUDA (`mps`, default on when
K > 1). Node aggregators connect to every service (`NodeAggregator(services=
card.services)`) and bind each game to one service by `service_for_game`.

Failure is loud and final. A service that does not start stops the card before
it serves; a host that exits, a publish that fails or that the services answer
differently, and an MPS daemon that does not start each stop every service of
the card (`failure`, `on_failure`). Nothing is restarted or retried, and the
card never continues on fewer services: the aggregators lose a connection and
fail their links, so their games end as not_started.

Factory contract: ``factory(index, settings) -> FastInferenceService``. Each
service needs its own assembler (distinct `assembler_id`, own segment output);
a mu handoff reaches every service through `publish`, and all must accept or
all refuse it. It decides no legality and holds no World.
"""
from __future__ import annotations

import contextlib
import copy
import importlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError

STAGE = "p1_inference_card"
MPS_CONTROL = "nvidia-cuda-mps-control"


def _say(event: dict[str, Any]) -> None:
    print(json.dumps({"inference_card": event}, default=str), file=sys.stderr, flush=True)


class MpsDaemon:
    """A CUDA MPS control daemon with this card's own pipe and log directories."""

    def __init__(self, root: str | Path, *, control: str = MPS_CONTROL,
                 timeout: float = 30.0) -> None:
        self.root = Path(root)
        self.control = control
        self.timeout = timeout
        self.env: dict[str, str] | None = None

    def _run(self, args: list[str], stdin: bytes | None) -> subprocess.CompletedProcess[bytes]:
        assert self.env is not None
        return subprocess.run([self.binary, *args], input=stdin, capture_output=True,
                              env={**os.environ, **self.env}, timeout=self.timeout, check=False)

    def start(self) -> dict[str, str]:
        """Start the daemon; returns the environment every client must start with."""
        binary = shutil.which(self.control)
        if binary is None:
            raise BoundaryError(STAGE, "mps_control_missing", "run on a node with CUDA MPS")
        self.binary = binary
        pipe, log = self.root / "mps-pipe", self.root / "mps-log"
        pipe.mkdir(parents=True, exist_ok=True)
        log.mkdir(parents=True, exist_ok=True)
        self.env = {"CUDA_MPS_PIPE_DIRECTORY": str(pipe), "CUDA_MPS_LOG_DIRECTORY": str(log)}
        started = self._run(["-d"], None)
        if started.returncode != 0:
            _say({"mps_start_failed": started.returncode,
                  "stderr": started.stderr.decode(errors="replace")[:300]})
            raise BoundaryError(STAGE, "mps_start_failed", "check the MPS log directory")
        answered = self._run([], b"get_server_list\n")
        if answered.returncode != 0:
            self.stop()
            raise BoundaryError(STAGE, "mps_not_answering", "check the MPS log directory")
        return dict(self.env)

    def stop(self) -> str:
        """Ask the daemon to quit; returns "quit" or why it did not (never raises)."""
        if self.env is None:
            return "not_started"
        try:
            quit_ = self._run([], b"quit\n")
        except (OSError, subprocess.SubprocessError) as error:
            reason = f"quit_failed:{type(error).__name__}"
        else:
            reason = "quit" if quit_.returncode == 0 else f"quit_exit:{quit_.returncode}"
        if reason != "quit":
            _say({"mps_stop": reason})
        self.env = None
        return reason


def _factory(name: str) -> Callable[[int, dict[str, Any]], Any]:
    module, _, function = name.partition(":")
    if not module or not function:
        raise BoundaryError(STAGE, "factory_module_function")
    target = getattr(importlib.import_module(module), function)
    if not callable(target):
        raise BoundaryError(STAGE, "factory_not_callable")
    return target  # type: ignore[no-any-return]


def _host(conn: Connection, index: int, factory: str, settings: dict[str, Any],
          env: dict[str, str]) -> None:
    """One service's process: build, report the address, then serve card commands."""
    os.environ.update(env)  # before anything here or in the service touches CUDA
    # The card stops a host with SIGTERM; the service still closes its children.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        service = _factory(factory)(index, settings)
        conn.send(("ready", service.address, service.auth, service.service_instance))
    except BaseException as error:
        with contextlib.suppress(OSError):
            conn.send(("failed", f"{type(error).__name__}: {str(error)[:300]}"))
        raise
    try:
        while True:
            try:
                command = conn.recv()
            except EOFError:
                return  # the card is gone
            kind = command[0]
            if kind == "close":
                return
            try:
                if kind == "publish":
                    import torch

                    model = torch.load(io.BytesIO(command[1]), weights_only=False)
                    answer: Any = bool(service.publish(model, command[2]))
                elif kind == "audit":
                    answer = service.mark_full_audit(command[1])
                elif kind == "stats":
                    answer = json.loads(json.dumps(
                        {**service.stats, "stop_reason": service.stop_reason,
                         "service_instance": service.service_instance}, default=str))
                else:
                    raise BoundaryError(STAGE, "unknown_command")
            except Exception as error:
                conn.send(("failed", f"{type(error).__name__}: {str(error)[:300]}"))
            else:
                conn.send(("ok", answer))
    finally:
        report = service.close()
        with contextlib.suppress(OSError):
            conn.send(("closed", json.loads(json.dumps(report, default=str))))


def model_blob(model: Any) -> bytes:
    """The model's parameters and construction, as FastInferenceService.publish ships them."""
    import torch

    shipped = copy.copy(model)
    shipped.__dict__ = {key: value for key, value in model.__dict__.items()
                        if not key.startswith("_rows_block")}
    blob = io.BytesIO()
    torch.save(shipped, blob)
    return blob.getvalue()


class InferenceCard:
    def __init__(self, factory: str, *, services: int = 1,
                 settings: dict[str, Any] | list[dict[str, Any]] | None = None,
                 mps: bool | None = None, mps_root: str | Path | None = None,
                 mps_control: str = MPS_CONTROL, start_timeout: float = 600.0,
                 command_timeout: float = 300.0,
                 on_failure: Callable[[str], None] | None = None) -> None:
        import multiprocessing

        if type(services) is not int or services < 1:
            raise ValueError("invalid_inference_card_services")
        if isinstance(settings, list) and len(settings) != services:
            raise ValueError("inference_card_settings_per_service")
        self.count = services
        self.command_timeout = command_timeout
        self.on_failure = on_failure
        self.failure: str | None = None
        self._closing = False
        self._lock = threading.Lock()  # one card command at a time
        self._failed = threading.Lock()
        self.mps: MpsDaemon | None = None
        env: dict[str, str] = {}
        if services > 1 and mps is None or mps:
            self._mps_dir = tempfile.mkdtemp(prefix="inference-card-mps-",
                                             dir=None if mps_root is None else str(mps_root))
            self.mps = MpsDaemon(self._mps_dir, control=mps_control)
            try:
                env = self.mps.start()
            except BaseException:
                shutil.rmtree(self._mps_dir, ignore_errors=True)
                raise
        context: Any = multiprocessing.get_context("spawn")
        self.connections: list[Connection] = []
        self.processes: list[Any] = []
        try:
            for index in range(services):
                own = settings[index] if isinstance(settings, list) else (settings or {})
                parent, child = context.Pipe()
                # Not daemonic: each host spawns its service's own processes.
                process = context.Process(
                    target=_host, name=f"inference-card-{index}", daemon=False,
                    args=(child, index, factory, {**own, "index": index, "services": services},
                          env))
                process.start()
                child.close()
                self.connections.append(parent)
                self.processes.append(process)
            self.services: list[tuple[tuple[str, int], bytes]] = []
            self.service_instances: list[str] = []
            for index, connection in enumerate(self.connections):
                answer = self._receive(index, connection, start_timeout, "start")
                if answer[0] != "ready":
                    raise BoundaryError(STAGE, "service_start_failed",
                                        f"service {index}: {answer[1]}")
                self.services.append((tuple(answer[1]), answer[2]))
                self.service_instances.append(answer[3])
        except BaseException as error:
            self._closing = True  # the raise reports it; on_failure is for a serving card
            self._fail(f"start:{getattr(error, 'code', type(error).__name__)}")
            self.close()
            raise
        self._monitor = threading.Thread(target=self._watch, daemon=True,
                                         name="inference-card-monitor")
        self._monitor.start()

    # -- commands ------------------------------------------------------------
    def _receive(self, index: int, connection: Connection, timeout: float,
                 what: str) -> tuple[Any, ...]:
        try:
            if not connection.poll(timeout):
                raise BoundaryError(STAGE, f"service_{what}_timeout", f"service {index}")
            answer = connection.recv()
        except (EOFError, OSError) as error:
            raise BoundaryError(STAGE, "service_gone", f"service {index}") from error
        return answer  # type: ignore[no-any-return]

    def _broadcast(self, command: tuple[Any, ...], what: str) -> list[Any]:
        with self._lock:
            if self.failure is not None:
                raise BoundaryError(STAGE, "card_stopped", self.failure)
            try:
                for connection in self.connections:
                    connection.send(command)
                answers = [self._receive(index, connection, self.command_timeout, what)
                           for index, connection in enumerate(self.connections)]
            except (BoundaryError, OSError) as error:
                self._fail(f"{what}:{getattr(error, 'code', type(error).__name__)}")
                raise BoundaryError(STAGE, f"{what}_failed", str(self.failure)) from error
            failed = [(index, answer[1]) for index, answer in enumerate(answers)
                      if answer[0] != "ok"]
            if failed:
                self._fail(f"{what}_failed:service_{failed[0][0]}:{failed[0][1]}")
                raise BoundaryError(STAGE, f"{what}_failed", str(self.failure))
            return [answer[1] for answer in answers]

    def publish(self, model: Any, handoff: Any) -> bool:
        """Offer one mu to every service; all must give the same answer."""
        accepted = self._broadcast(("publish", model_blob(model), handoff), "publish")
        if len(set(accepted)) != 1:
            self._fail(f"publish_disagreement:{accepted}")
            raise BoundaryError(STAGE, "publish_disagreement", str(self.failure))
        return bool(accepted[0])

    def mark_full_audit(self, game_instance_id: str) -> None:
        """An R7 game (every row audited) on whichever service it is bound to."""
        self._broadcast(("audit", game_instance_id), "audit")

    def stats(self) -> list[dict[str, Any]]:
        return self._broadcast(("stats",), "stats")

    # -- failure and lifecycle -------------------------------------------------
    def _watch(self) -> None:
        sentinels = {process.sentinel: index for index, process in enumerate(self.processes)}
        while sentinels and not self._closing:
            for ready in wait(list(sentinels), timeout=1.0):
                index = sentinels.pop(ready)
                if not self._closing:
                    self._fail(f"service_{index}_exited:{self.processes[index].exitcode}")
                    return

    def _fail(self, reason: str) -> None:
        with self._failed:
            if self.failure is not None:
                return
            self.failure = reason
        _say({"failed": reason})
        for process in self.processes:
            if process.is_alive():
                process.terminate()  # its service closes; no restart, no fewer-service mode
        if self.on_failure is not None and not self._closing:
            self.on_failure(reason)

    def close(self) -> dict[str, Any]:
        self._closing = True
        reports: list[Any] = []
        for index, (connection, process) in enumerate(zip(self.connections, self.processes,
                                                          strict=True)):
            report: Any = None
            with contextlib.suppress(OSError, EOFError):
                if process.is_alive():
                    connection.send(("close",))
                while connection.poll(60):
                    answer = connection.recv()
                    if answer[0] == "closed":
                        report = answer[1]
                        break
            process.join(30)
            if process.is_alive():
                _say({"killed": index})
                process.kill()
                process.join(5)
            connection.close()
            reports.append(report)
        mps = self.mps.stop() if self.mps is not None else None
        if self.mps is not None:
            shutil.rmtree(self._mps_dir, ignore_errors=True)
        return {"failure": self.failure, "services": reports, "mps": mps,
                "exitcodes": [process.exitcode for process in self.processes]}
