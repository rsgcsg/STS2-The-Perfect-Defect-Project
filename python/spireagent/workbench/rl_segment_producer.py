"""Incrementally consume the ordinary Runtime verifier, then commit short segments.

No polling/replay of game operations: a child reads the original journal once.
The same Managed executor remains the only lifecycle owner. Prefix admission is
independent of eventual whole-run closure; a natural terminal label is supplied
only by the normal typed save pipeline after it has checked the original run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sts2_platform_evidence import AgentSessionRunEvidence, VerificationResult

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.rl_contracts import LocalMuScorer
from spireagent.storage.store import copy_artifact
from stpd.p1.data import json_bytes as input_bytes
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.sampling import distribution
from stpd.p1.trajectory import validate_behavior

FAILURE_STATUSES = frozenset({"unknown", "save_failed"})
_FAILURE_TAIL_BYTES = 1024**2


def failure_facts(attempt: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Classified last-receipt facts of one non-natural close (U3 recording gap).

    Read-only and bounded: the Runtime final status plus the last MiB of the
    original journal. Codes, labels and a short public detail only; never a
    retry, label or training input. Missing files give ``None`` fields.
    """
    import re

    facts: dict[str, Any] = {"execution_reason": row.get("execution_reason"),
        "save_error": row.get("save_error"),
        "max_observed_floor": (row.get("game_metrics") or {}).get("max_observed_floor"),
        "last_result": None, "taint_reason": None, "last_unknown_receipt": None,
        "handoff_reason": None}
    with suppress(OSError, ValueError, KeyError, TypeError):
        status = json.loads((attempt / row["runtime_final_status"]["path"]).read_bytes())
        last = status.get("last_result") or {}
        facts["last_result"] = {k: last.get(k) for k in ("delivery", "reason", "action_id")}
        facts["taint_reason"] = status.get("taint_reason")
    with suppress(OSError, ValueError, KeyError, TypeError):
        journal = attempt / "runs" / row["run_id"] / "events.jsonl"
        with journal.open("rb") as stream:
            offset = max(0, journal.stat().st_size - _FAILURE_TAIL_BYTES)
            stream.seek(offset)
            lines = stream.read().splitlines()[1 if offset else 0:]  # skip a cut line
        labels: dict[str, Any] = {}
        for raw in lines:
            event = json.loads(raw)
            payload = event.get("payload") or {}
            request_id = payload.get("request_id")
            if not isinstance(request_id, str):
                request_id = ""  # an absent id cannot pair a dispatch with a receipt
            if event.get("kind") == "global_dispatch":
                if request_id:
                    labels[request_id] = (payload.get("bound_action") or {}).get("label")
            elif event.get("kind") == "global_receipt":
                receipt = payload.get("receipt") or {}
                if receipt.get("delivery") != "delivered":
                    facts["last_unknown_receipt"] = {
                        "reason_code": receipt.get("reason_code"),
                        "delivery": receipt.get("delivery"),
                        "label": labels.get(rid) if isinstance(
                            rid := receipt.get("request_id"), str) else None,
                        "detail": re.sub(r"https?://\S+", "<url>",
                                         str(receipt.get("detail")))[:200]}
            elif (event.get("kind") in {"handoff_to_human", "fail_closed"}
                  and facts["handoff_reason"] is None):
                facts["handoff_reason"] = payload.get("reason")
    return facts


class OriginalBarrier:
    """Fsync originals once per segment and bind the exact verified journal prefix."""

    def __init__(self, run: Path, trace: Path) -> None:
        self.run, self.trace = run, trace
        self.journal = (run / "events.jsonl").open("rb")
        self.hash = hashlib.sha256()
        self.sequence = 0
        self.bytes = 0
        self.trace_reader = trace.open("rb")
        self.trace_hash = hashlib.sha256()
        self.trace_bytes = 0

    def commit(
        self, steps: list[dict[str, Any]], *, trace_through_bytes: int
    ) -> tuple[dict[str, Any], bytes]:
        started = time.perf_counter()
        hash_seconds = sync_seconds = 0.0

        def sync(descriptor: int) -> None:
            nonlocal sync_seconds
            began = time.perf_counter()
            os.fsync(descriptor)
            sync_seconds += time.perf_counter() - began

        def update(hasher: Any, raw: bytes) -> None:
            nonlocal hash_seconds
            began = time.perf_counter()
            hasher.update(raw)
            hash_seconds += time.perf_counter() - began

        refs: list[dict[str, Any]] = []
        for name in sorted({name for step in steps for name in step["original_paths"]}):
            path = self.run / name
            if path.parent != self.run or path.is_symlink():
                raise BoundaryError("rl_segment", "original_path_changed")
            with path.open("rb") as stream:
                sync(stream.fileno())
                began = time.perf_counter()
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
                hash_seconds += time.perf_counter() - began
            refs.append({"path": name, "sha256": sha, "bytes": path.stat().st_size})
        through = steps[-1]["verified_through_sequence"]
        journal_start, sequence_start = self.bytes, self.sequence
        trace_start = self.trace_bytes
        while self.sequence < through:
            raw = self.journal.readline()
            if not raw.endswith(b"\n"):
                raise BoundaryError("rl_segment", "verified_journal_prefix_missing")
            event = json.loads(raw)
            if event["sequence"] != self.sequence + 1:
                raise BoundaryError("rl_segment", "verified_journal_sequence_changed")
            update(self.hash, raw)
            self.bytes += len(raw)
            self.sequence += 1
        sync(self.journal.fileno())
        while self.trace_bytes < trace_through_bytes:
            chunk = self.trace_reader.read(min(1024**2, trace_through_bytes - self.trace_bytes))
            if not chunk:
                raise BoundaryError("rl_segment", "original_behavior_prefix_missing")
            update(self.trace_hash, chunk)
            self.trace_bytes += len(chunk)
        sync(self.trace_reader.fileno())
        for name in ("manifest.json", "agent-manifest.json", "adapter-attestation.json"):
            with (self.run / name).open("rb") as stream:
                sync(stream.fileno())
        for directory in {self.run, self.trace.parent}:
            fd = os.open(directory, os.O_RDONLY)
            try:
                sync(fd)
            finally:
                os.close(fd)
        metadata: dict[str, Any] = {
            "run_id": steps[0]["run_id"],
            "segment_id": steps[0]["behavior"]["lease"],
            "journal_start_bytes": journal_start,
            "journal_start_sequence": sequence_start,
            "journal_bytes": self.bytes,
            "verified_through_sequence": self.sequence,
            "journal_prefix_sha256": self.hash.hexdigest(),
            "artifacts": refs,
            "behavior_trace": self.trace.name,
            "behavior_trace_start_bytes": trace_start,
            "behavior_trace_bytes": self.trace_bytes,
            "behavior_trace_prefix_sha256": self.trace_hash.hexdigest(),
            "at": time.time(),
        }
        metadata["cost_seconds"] = {"original_hash_read": hash_seconds,
            "original_small_file_fsync": sync_seconds,
            "original_barrier_total": time.perf_counter() - started,
            "copy": 0.0, "compression": 0.0}
        metadata["representation"] = "live-prefix"
        return metadata, b""

    def close(self) -> None:
        self.journal.close()
        self.trace_reader.close()


class SegmentAssembler:
    """Join verified actual directives to the Agent's mu journal, preserving all rows."""

    def __init__(
        self,
        identity: dict[str, Any],
        barrier: OriginalBarrier,
        commit: Callable[[dict[str, Any], bytes], dict[str, Any] | None],
        *,
        step_limit: int = 64,
        compiler: FrameCompiler,
        scorer: LocalMuScorer,
        generation_discarded: Callable[[str], bool],
    ) -> None:
        self.identity, self.barrier, self.commit_segment = identity, barrier, commit
        self.step_limit = step_limit
        self.compiler = compiler
        self.scorer = scorer
        self.generation_discarded = generation_discarded
        if compiler is None or scorer is None:
            raise BoundaryError("rl_segment", "hot_segment_compiler_and_local_mu_required")
        self.retained_mu: str | None = None
        self.max_floor = 0
        self.steps: list[dict[str, Any]] = []
        self.terminal_pending = False
        self.trace = barrier.trace.open("rb")
        self.trace_rows: dict[str, dict[str, Any]] = {}
        self.trace_end = 0
        self.committed: list[dict[str, Any]] = []
        self.commit_started = False
        self.battle_start: int | None = None
        self.battle_end: int | None = None
        self.hp_pending: list[dict[str, Any]] = []

    def _behavior(self, acquisition: str) -> dict[str, Any]:
        while acquisition not in self.trace_rows:
            raw = self.trace.readline()
            if not raw.endswith(b"\n"):
                raise BoundaryError("rl_segment", "original_behavior_record_missing")
            value = json.loads(raw)
            self.trace_rows[value["acquisition_id"]] = value
        return self.trace_rows.pop(acquisition)

    def transition(self, value: dict[str, Any]) -> None:
        if self.terminal_pending:
            raise BoundaryError("rl_segment", "transition_after_terminal")
        row = dict(value)
        behavior = self._behavior(row["input"]["acquisition_id"])
        if self.steps and (behavior["lease"] != self.steps[0]["behavior"]["lease"]
                           or behavior["mu"] != self.steps[0]["behavior"]["mu"]):
            self._commit(None)
        snapshot = behavior["mu"]["snapshot_sha256"]
        if self.retained_mu is None:
            self.scorer.retain_mu(snapshot)
            self.retained_mu = snapshot
        self.max_floor = max(self.max_floor, behavior["floor"])
        if behavior.get("outcome_probabilities") is None:
            if len(row["input"]["catalog"]) != 1:
                raise BoundaryError("rl_segment", "decision_outcome_distribution_missing")
            scored = self.scorer.score_compiled(snapshot, behavior["compiled_input"],
                behavior["floor"], policy=False)
            if scored.snapshot_sha256 != snapshot:
                raise BoundaryError("rl_segment", "behavior_mu_changed")
            behavior["outcome_probabilities"] = list(scored.outcome_probabilities)
        scores = behavior["scores"]
        policy = self.identity["behavior_policy"]
        if policy["temperature"] != 1 or policy["uniform_mix"] != 0:
            raise BoundaryError("rl_segment", "RL4_requires_untempered_behavior")
        raw, probabilities = distribution(scores, policy["temperature"], policy["uniform_mix"])
        directive = row["directive"]["directive"]
        selected = [item["choice_id"] for item in row["input"]["catalog"]].index(
            directive["selection"]["action_id"]
        )
        sampling = {
            "mode": "temperature_uniform",
            "temperature": 1.0,
            "uniform_mix": 0.0,
            "probabilities": probabilities,
            "model_probabilities": raw,
            "model_logits": scores,
            "selected_probability": probabilities[selected],
        }
        validate_behavior(sampling, len(scores), selected)
        row.update(behavior=behavior, sampling=sampling)
        self.steps.append(row)
        self.trace_end = self.trace.tell()
        fighting = (row["input"]["observation"].get("combat") or {}).get(
            "availability"
        ) == "available"
        if fighting and (self.battle_start is None or self.battle_end is not None):
            self.battle_start = row["step_index"]
            self.battle_end = None
        after = row["successor"]["observation"]
        if self.battle_start is not None and (
            (after.get("combat") or {}).get("availability") != "available"
        ):
            if self.battle_end is None:
                self.battle_end = row["step_index"] + 1
            kind = str((after.get("scene") or {}).get("native_kind", ""))
            hp = ((after.get("player") or {}).get("content") or {}).get("hp")
            if (
                kind.startswith("reward")
                and isinstance(hp, (int, float))
                and not isinstance(hp, bool)
                and hp >= 0
            ):
                self.hp_pending.append(
                    {
                        "battle_id": f"{row['run_id']}:{self.battle_start}",
                        "start_step": self.battle_start,
                        "end_step": self.battle_end,
                        "hp": hp / 100,
                        "post_hp": hp,
                        "boundary_step_index": row["step_index"],
                        "boundary_acquisition_id": row["successor"]["acquisition_id"],
                        "boundary_input_sha256": hashlib.sha256(
                            input_bytes(row["successor"])
                        ).hexdigest(),
                    }
                )
                self.battle_start = None
                self.battle_end = None
        if row["successor"]["observation"]["status"] == "terminal":
            self.terminal_pending = True
        elif len(self.steps) == self.step_limit:
            self._commit(None)

    def _commit(self, terminal: dict[str, Any] | None) -> None:
        if not self.steps:
            return
        if self.commit_started:
            raise BoundaryError("rl_segment", "original_segment_commit_requires_reconciliation")
        # A lost Store/Exchange/admission receipt is never permission to submit
        # this segment again. The immutable original intent remains for recovery.
        self.commit_started = True
        battles = []
        for battle in self.hp_pending:
            states = []
            for previous in self.committed:
                if previous.get("discarded_on_restore"):
                    continue
                if (
                    previous["training_generation"]
                    != self.steps[0]["behavior"]["mu"]["training_generation"]
                ):
                    continue
                for local in range(previous["rows"]):
                    index = previous["start_step"] + local
                    if battle["start_step"] <= index < battle["end_step"]:
                        states.append(
                            {
                                "source": {"run_id": self.steps[0]["run_id"],
                                    "segment_id": previous["segment_id"],
                                    "training_generation": previous["training_generation"],
                                    "row": local, "key": f"{self.steps[0]['run_id']}:{index}"},
                                "hp": battle["hp"],
                            }
                        )
            for local, step in enumerate(self.steps):
                if battle["start_step"] <= step["step_index"] < battle["end_step"]:
                    states.append(
                        {
                            "source": {"run_id": step["run_id"],
                                "segment_id": step["behavior"]["lease"],
                                "training_generation": step["behavior"]["mu"][
                                    "training_generation"],
                                "row": local, "key": f"{step['run_id']}:{step['step_index']}"},
                            "hp": battle["hp"],
                        }
                    )
            battles.append({**{key: item for key, item in battle.items() if key != "hp"},
                            "post_hp": battle["post_hp"], "states": states})
        original_commit, original_bytes = self.barrier.commit(
            self.steps, trace_through_bytes=self.trace_end
        )
        value = {
            "training_split": "TRAIN",
            "identity": self.identity,
            "run_id": self.steps[0]["run_id"],
            "rows": len(self.steps),
            "segment_id": self.steps[0]["behavior"]["lease"],
            "mu": self.steps[0]["behavior"]["mu"],
            "steps": self.steps,
            "terminal": terminal,
            "hp_battles": battles,
            "original_commit": original_commit,
            "previous_segment_artifact_id": (
                self.committed[-1]["segment_artifact_id"] if self.committed
                and not self.committed[-1].get("discarded_on_restore")
                and self.committed[-1]["training_generation"]
                == self.steps[0]["behavior"]["mu"]["training_generation"] else None
            ),
        }
        value.update(compiler_identity=self.compiler.identity,
            compiled_boundary=(self.compiler.encode(
                self.steps[-1]["successor"]["observation"]) if terminal is None else None))
        value.update(run=value["mu"]["run"],
                     training_generation=value["mu"]["training_generation"], bootstrap=None)
        if terminal is None and not self.generation_discarded(value["training_generation"]):
            successor = self.steps[-1]["successor"]
            floor = ((successor["observation"].get("run") or {}).get("content") or {}).get(
                "floor"
            )
            floor = max(self.max_floor, floor if type(floor) is int else 0)
            if value.get("compiled_boundary") is None:
                raise BoundaryError("rl_segment", "compiled_bootstrap_required")
            try:
                scored = self.scorer.score_compiled(value["mu"]["snapshot_sha256"],
                    value["compiled_boundary"], floor, policy=False)
            except (OSError, EOFError, TimeoutError, ValueError, BoundaryError):
                if not self.generation_discarded(value["training_generation"]):
                    raise
                scored = None  # explicitly discarded; never delivered as a trainable segment
            if scored is not None and scored.snapshot_sha256 != value["mu"]["snapshot_sha256"]:
                raise BoundaryError("rl_segment", "bootstrap_mu_changed")
            if scored is not None:
                value["bootstrap"] = {"mu_snapshot_sha256": scored.snapshot_sha256,
                "successor_input_sha256": hashlib.sha256(input_bytes(successor)).hexdigest(),
                "floor": floor, "outcome_probabilities": list(scored.outcome_probabilities)}
        result = self.commit_segment(value, original_bytes)
        if self.retained_mu is not None:
            self.scorer.release_mu(self.retained_mu)
            self.retained_mu = None
        self.committed.append(
            {
                "segment_id": value["segment_id"],
                "rows": value["rows"],
                "start_step": self.steps[0]["step_index"],
                **(result or {}),
            }
        )
        self.steps = []
        self.hp_pending = []
        self.commit_started = False

    def finish(self, terminal: dict[str, Any] | None) -> None:
        if self.terminal_pending and terminal is None:
            # The unqualified terminal tail remains retained, without a fabricated
            # outcome or bootstrap. Earlier immutable verified prefixes stay valid.
            return
        self._commit(terminal if self.terminal_pending else None)

    def close(self) -> None:
        if self.retained_mu is not None:
            self.scorer.release_mu(self.retained_mu)
            self.retained_mu = None
        self.trace.close()
        self.barrier.close()


class LiveSegmentProducer:
    """A bounded observer of the normal Runtime subprocess, never a second executor."""

    def __init__(
        self,
        runtime: Path,
        run: Path,
        trace: Path,
        identity: dict[str, Any],
        commit: Callable[[dict[str, Any], bytes], dict[str, Any] | None],
        private: Path,
        *,
        step_limit: int = 64,
        compiler: FrameCompiler,
        scorer: LocalMuScorer,
        generation_discarded: Callable[[str], bool],
    ) -> None:
        self.assembler = SegmentAssembler(
            identity, OriginalBarrier(run, trace), commit, step_limit=step_limit, compiler=compiler,
            scorer=scorer, generation_discarded=generation_discarded
        )
        self.error: BaseException | None = None
        self.error_record: dict[str, Any] | None = None
        self.error_path = private / "segment-observer-error.json"
        self.report: dict[str, Any] | None = None
        self.stop_requested = False
        self.interruption: dict[str, Any] | None = None
        self.after_interruption = 0
        self.end_started = False
        self.stderr = (private / "segment-verifier.stderr").open("xb")
        from sts2_platform_evidence.global_agent_session_run_evidence import LiveGlobalReplay

        self.verifier = LiveGlobalReplay(run, runtime, stderr=self.stderr)
        self.process = self.verifier.process
        self.thread = threading.Thread(
            target=self._read, name="verified-short-segments", daemon=True
        )
        self.thread.start()

    def _read(self) -> None:
        stage = "verifier_events"
        try:
            assert self.process.stdout is not None
            for value in self.verifier.events():
                if value.get("schema") == "sts2.policy-runtime/verified-global-end-1":
                    self.report = value["report"]
                elif value.get("schema") == "sts2.policy-runtime/verified-global-boundary-1":
                    if self.interruption is None:
                        self.interruption = value
                        self.stop_requested = True
                        stage = "interruption_finish"
                        self.assembler.finish(None)
                        stage = "verifier_events"
                elif self.interruption is not None:
                    self.after_interruption += 1
                else:
                    stage = "transition"
                    self.assembler.transition(value)
                    stage = "verifier_events"
            stage = "verifier_exit"
            if self.process.wait() != 0 or self.report is None:
                raise BoundaryError("rl_segment", "original_prefix_verifier_failed")
        except BaseException as error:
            self._record_error(error, stage)

    def _record_error(self, error: BaseException, operation: str) -> None:
        if self.error is not None:
            return  # A later reconciliation failure cannot replace the first error.
        self.error = error
        # Never persist raw SDK messages, URLs, credentials or game inputs.
        self.error_record = {"at": time.time(), "operation": operation,
            "error_type": type(error).__name__}
        from spireagent.local_diagnostics import exception_facts

        self.error_record["diagnostic"] = exception_facts(error)
        if isinstance(error, BoundaryError):
            self.error_record.update(stage=error.stage, code=error.code)
        elif isinstance(error, OSError):
            self.error_record["errno"] = error.errno
        # The original failure remains authoritative if this disk write fails.
        with suppress(OSError):
            self.error_path.write_bytes(json_bytes(self.error_record))

    def finish(self, terminal: dict[str, Any] | None) -> None:
        self.wait()
        try:
            self.assembler.finish(terminal)
        except BaseException as error:
            self._record_error(error, "tail_finish")
            raise

    def wait(self) -> None:
        self.thread.join(timeout=120)
        if self.thread.is_alive():
            raise BoundaryError("rl_segment", "prefix_verifier_close_pending")
        if self.error:
            self.assembler.finish(None)
            raise self.error

    def close(self) -> None:
        if self.process.poll() is None:
            # Only the read-only verifier child is stopped here; the original
            # Managed owner has already closed its Runtime and Host.
            self.process.terminate()
            self.process.wait(timeout=10)
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            # A still-reading observer retains its models and payload owners.
            raise BoundaryError("rl_segment", "prefix_verifier_close_pending")
        self.assembler.close()
        self.stderr.close()
        if self.process.stdout is not None:
            self.process.stdout.close()


def completed_auxiliary(
    episode: dict[str, Any], segments: list[dict[str, Any]], terminal: dict[str, Any] | None
) -> dict[str, Any]:
    """Original state references, with observed labels; no policy/first-use replay."""
    from stpd.rl.segments import uniform_depth_indices

    references = {}
    for segment in segments:
        if segment.get("discarded_on_restore"):
            continue
        for local in range(segment["rows"]):
            references[segment["start_step"] + local] = {"source": {
                "run_id": episode["run_id"], "segment_id": segment["segment_id"],
                "training_generation": segment["training_generation"],
                "key": f"{episode['run_id']}:{segment['start_step'] + local}",
                "row": local,
            }}
    outcomes = []
    if terminal is not None:
        keys = sorted(references)
        outcomes = [
            references[keys[index]] | {"outcome": terminal["class"]}
            for index in uniform_depth_indices(len(keys))
        ]
    hp = []
    observations = episode["observations"]
    for span in episode["combat_spans"]:
        start, end = span["start_observation_index"], span["end_observation_index"]
        if end is None:
            if terminal is None:
                continue
            # Native terminal can retain combat availability. Use its actual
            # public HP, without inventing a noncombat transition or a zero.
            end = len(observations) - 1
        boundary = observations[end]["observation"]
        scene = boundary.get("scene") or {}
        player = (boundary.get("player") or {}).get("content") or {}
        health = player.get("hp")
        # The normal public reader publishes reward scenes only after native
        # combat/end/victory settlement. Noncombat map/event/camp is not a label.
        # Reward-boundary battles already entered the HP pool through their
        # committed segment. Only a real terminal battle needs this final label.
        settled = terminal is not None and boundary.get("status") == "terminal"
        if (
            not settled
            or not isinstance(health, (int, float))
            or isinstance(health, bool)
            or health < 0
        ):
            continue
        states = [
            references[step["index"]] | {"hp": health / 100}
            for step in episode["steps"]
            if start <= step["observation_index"] < end and step["index"] in references
        ]
        if states:
            hp.append(
                {
                    "battle_id": f"{episode['run_id']}:{start}",
                    "states": states,
                    "post_observation_index": end,
                    "post_hp": health,
                    "public_native_kind": scene.get("native_kind"),
                }
            )
    return {
        "run_id": episode["run_id"],
        "terminal": terminal,
        "outcome_states": outcomes,
        "hp_battles": hp,
    }


# The application composes one research observer with the original executor.
# No alternative Reset/Read/Act/Close program is introduced.
from .managed_evaluation import _phase  # noqa: E402
from .managed_pipeline import PipelinedManagedExecutor  # noqa: E402


def open_learner_episode(row: dict[str, Any], *, exchange: Any, admission: Any, actor: str,
                         budget_id: str | None, whole_game_sample: bool = True) -> None:
    """Learner admission of one game before its world offer (both chains).

    ``whole_game_sample`` False (the fast chain, CARD-RL5 §12.6): no ordinary
    training game is chosen for a whole-game sample; the row records False.
    """
    from .managed_retention import training_audit_selected

    if exchange.admission_pause(budget_id) is not None:
        raise BoundaryError("rl_segment", "sampling_audit_admission_paused")
    row["training_audit_selected"] = whole_game_sample and training_audit_selected(
        row, audit_every=row["retention"]["audit_every"])
    result = admission.request("episode_open", actor=actor, episode=row["intent_id"])
    row["segment_admission_owner"] = {
        k: result[k] for k in ("run", "training_generation", "budget_id") if k in result}
    row["segment_episode_admitted"] = result.get("admitted") is True
    if not row["segment_episode_admitted"]:
        raise BoundaryError("rl_segment", "learner_stopped_before_world_offer")


def publish_world_offered(row: dict[str, Any], *, exchange: Any, actor: str) -> None:
    """The watcher's durable offer fact; fast-chain rows add their slot and game ref."""
    extra = {k: row[k] for k in ("fast_chain_slot", "fast_chain_game_ref") if k in row}
    exchange.publish_event("actor_world_offered", actor=actor,
        intent_id=row["intent_id"], seed=row["seed"], **{
            k: v for k, v in row["segment_admission_owner"].items() if k != "run"}, **extra)


FAST_INSTANCE_ADMISSION = "fast_instance_admission"
FAST_INSTANCE_ADMISSION_FIELDS = ("actor", "intent_id", "seed", "game_instance_id",
                                  "training_split", "run", "training_generation")
_LEARNER_REASON = re.compile(r"[a-z0-9_]{1,64}")


def fast_instance_admission(row: dict[str, Any], opened: Mapping[str, Any], *,
                            admission: Any, actor: str) -> None:
    """Fast chain only: attest one opened instance to the learner, once.

    Sent on the existing learner control connection after the environment's checked
    ``opened`` (the learner owns the receiving side; C-SCALE, WP8). The request is
    ``fast_instance_admission`` with exactly :data:`FAST_INSTANCE_ADMISSION_FIELDS`:
    ``actor``, ``intent_id``, ``seed``, ``game_instance_id``, ``training_split``
    (always ``TRAIN``) and the ``run``/``training_generation`` the game's
    ``episode_open`` was admitted under. The learner checks the seed (holdout guard,
    once per game) and maps ``game_instance_id`` to ``(actor, intent_id)``; the
    assembler's segments for that instance are keyed by the same id. The reply must
    be ``{"admitted": true, ...}``. Anything else, or a transport failure, raises: the
    game is closed (censored ``fast_chain_instance_admission_failed:<code>``) and never
    re-attested (it is a non-mutation request, so the connection does not replay it
    either). The actor sends no fast-chain ``episode_end``.

    For the learner side (C-SCALE, WP8):

    - ``fast_instance_admission_delivery_failed`` means the outcome is unknown: the
      request may have been applied and only the reply lost. The learner must handle
      an instance it attested whose game the actor then closed: such an instance (and
      any instance the actor never confirms) is treated as truncated, never as a
      clean attested game.
    - The environment emits rows and seals segments while this request is in flight
      (about one segment per 1.5 s). Whether segments sealed before attestation are
      buffered (bounded) or discarded is the learner's decision; discarding biases
      the sample against game openings.
    """
    owner = row.get("segment_admission_owner") or {}
    instance = opened.get("game_instance_id")
    if not isinstance(instance, str) or not instance or not {
            "run", "training_generation"} <= set(owner):
        raise BoundaryError("rl_segment", "fast_instance_admission_input_invalid")
    request = {"actor": actor, "intent_id": row["intent_id"], "seed": row["seed"],
               "game_instance_id": instance, "training_split": "TRAIN",
               "run": owner["run"], "training_generation": owner["training_generation"]}
    try:
        result = admission.request(FAST_INSTANCE_ADMISSION, **request)
    except (OSError, EOFError, TimeoutError) as error:
        raise BoundaryError("rl_segment", "fast_instance_admission_delivery_failed") from error
    if not isinstance(result, Mapping) or result.get("admitted") is not True:
        reason = result.get("reason") if isinstance(result, Mapping) else None
        reason = reason if isinstance(reason, str) and _LEARNER_REASON.fullmatch(
            reason) else "unspecified"
        raise BoundaryError("rl_segment", f"fast_instance_admission_refused:{reason}")
    row["fast_instance_admission"] = {"admitted": True, "game_instance_id": instance}


FAST_INSTANCE_EXCLUDED = "fast_instance_excluded"
FAST_INSTANCE_EXCLUDED_FIELDS = ("actor", "game_instance_id", "reason", "run",
                                 "training_generation")


def fast_instance_excluded(row: dict[str, Any], *, admission: Any, actor: str,
                           reason: str = "evidence_failed") -> None:
    """Fast chain only: tell the learner one opened instance must not be trained on.

    Sent once, on the learner control connection, after the close of a game whose
    environment evidence failed (``evidence_failed`` in the row: the close flag, or
    an ``evidence_failed`` event that may arrive after the decision link closed, so
    the assembler's instance-closed message cannot carry it). The request is
    ``fast_instance_excluded`` with exactly :data:`FAST_INSTANCE_EXCLUDED_FIELDS`; ``run``
    and ``training_generation`` are the ones ``fast_instance_admission`` sent. The
    learner (semantics A, WP8) drops the instance's unused queued segments and keeps
    the already-used ones counted, as the old chain does. The reply must be
    ``{"excluded": true, "game_instance_id": <id>}``; anything else, or a transport
    failure, raises and is never resent (the caller records and counts it).
    """
    owner = row.get("segment_admission_owner") or {}
    instance = row.get("game_instance_id")
    if not isinstance(instance, str) or not instance or not {
            "run", "training_generation"} <= set(owner):
        raise BoundaryError("rl_segment", "fast_instance_excluded_input_invalid")
    request = {"actor": actor, "game_instance_id": instance, "reason": reason,
               "run": owner["run"], "training_generation": owner["training_generation"]}
    try:
        result = admission.request(FAST_INSTANCE_EXCLUDED, **request)
    except (OSError, EOFError, TimeoutError) as error:
        raise BoundaryError("rl_segment", "fast_instance_excluded_delivery_failed") from error
    if (not isinstance(result, Mapping) or result.get("excluded") is not True
            or result.get("game_instance_id") != instance):
        refusal = result.get("reason") if isinstance(result, Mapping) else None
        refusal = refusal if isinstance(refusal, str) and _LEARNER_REASON.fullmatch(
            refusal) else "unspecified"
        raise BoundaryError("rl_segment", f"fast_instance_excluded_refused:{refusal}")
    row["fast_instance_excluded"] = {"excluded": True, "game_instance_id": instance,
                                     "reason": reason}


class SegmentManagedExecutor(PipelinedManagedExecutor):
    def __init__(
        self,
        configuration: dict[str, Any],
        *,
        episode_seconds: float,
        cleanup_seconds: float,
        exchange: Any,
        actor: str,
        attachment: dict[str, Any],
        admission: Any,
        budget_id: str | None = None,
    ) -> None:
        # Capacity waits use ACK/next/await; only the score RPC has a 120s limit.
        # Keep the existing Agent call limit; native Stop/deadlines still own exit.
        configuration = {**configuration}
        configuration.setdefault("agent_timeout_ms", 270000)
        super().__init__(
            configuration, episode_seconds=episode_seconds, cleanup_seconds=cleanup_seconds
        )
        self.exchange, self.actor, self.attachment = exchange, actor, attachment
        self.admission = admission
        self.budget_id = budget_id
        self.observers: dict[str, LiveSegmentProducer] = {}
        self.connections: dict[str, Any] = {}
        self.new_originals: dict[str, tuple[Path, Path]] = {}
        self.binary_encoders: dict[str, Any] = {}
        self.binary_spools: dict[str, Any] = {}

    def _before_world_offer(self, row: dict[str, Any]) -> None:
        open_learner_episode(row, exchange=self.exchange, admission=self.admission,
                             actor=self.actor, budget_id=self.budget_id)

    def _world_offer_checkpointed(self, row: dict[str, Any]) -> None:
        publish_world_offered(row, exchange=self.exchange, actor=self.actor)

    def _end_episode(self, connection: Any, **fields: Any) -> dict[str, Any]:
        # The actor owns the actual accepted episode admission, including a new
        # generation with zero committed segments. A startup socket binding or
        # the last old segment cannot identify that owner after learner restore.
        fields.update(self.admission.episode_owner(fields["actor"], fields["episode"]))
        fields["segments"] = [segment for segment in fields.get("segments", [])
            if segment["training_generation"] == fields["training_generation"]
            and not segment.get("discarded_on_restore")]
        if not fields["segments"]:
            # The original typed World result stays intact, but this generation
            # has no verified rows to which training labels can be attached.
            fields.update(terminal=None, outcome_states=[], hp_battles=[])
        if self.attachment.get("dataplane"):
            fields["binary_aux"] = self._upload_auxiliary(fields)
            from spireagent.rl_dataplane_ports import CONTROL_SCHEMA
            from spireagent.rl_exchange import control_input

            original = control_input({**fields, "operation": "episode_end",
                                      "control_schema": CONTROL_SCHEMA})
        else:
            original = self.exchange.persist_episode_end(fields, self.producer)
        result: dict[str, Any] = connection.request("episode_end",
            **{key: value for key, value in original.items() if key != "operation"})
        if result.get("reason") == "generation_changed":
            self.exchange.publish_event("input_disposition", stage="discarded_on_restore",
                training_generation=fields["training_generation"],
                request_key=original.get("request_key"),
                run_id=fields["run_id"], operation="episode_end")
        return result

    def _release_episode_payloads(self, observer: LiveSegmentProducer) -> None:
        if self.attachment.get("dataplane"):
            run_id = observer.assembler.barrier.run.name
            self.binary_encoders.pop(run_id, None)
            spool = self.binary_spools.pop(run_id, None)
            if spool is not None:
                spool.cleanup()
            return
        owner = "episode:" + observer.assembler.identity["intent_id"]
        for segment in observer.assembler.committed:
            if segment.get("payload") is not None:
                self.exchange.release_hot_payload(segment["payload"], owner)

    def __call__(
        self, row: dict[str, Any], manifest: dict[str, Any], artifacts: Any, checkpoint: Any
    ) -> None:
        super().__call__(row, manifest, artifacts, checkpoint)
        if (row.get("run_id") is None and row.get("cleanup_accepted") is True
                and row.get("segment_episode_admitted") is True):
            try:
                self._end_episode(
                    self.admission,
                    actor=self.actor,
                    episode=row["intent_id"],
                    run_id=None,
                    terminal=None,
                    outcome_states=[],
                    hp_battles=[],
                    unqualified=True,
                )
            except (OSError, ValueError, TimeoutError):
                row["segment_control_cleanup_pending"] = True
                checkpoint()

    def preserve_report(self, path: Path) -> dict[str, Any]:
        report = super().preserve_report(path)
        row = report["episodes"][0]
        run_id = row.get("run_id")
        observer = self.observers.get(run_id)
        if observer is None:
            return report
        try:
            if observer.end_started:
                # The verified close already sent its one End and lost the ACK
                # (post_verification_processing_failed). That End stays unknown
                # and is never resent; the preserved failure is not a save
                # failure, so the row keeps its own unknown status (U3).
                row["segment_episode_end"] = "attempted_before_preservation_unknown"
                return report
            # Typed failure/technical close has no terminal label. Deliver only
            # the earlier verified tail that has never attempted admission.
            observer.finish(None)
            observer.end_started = True
            self._end_episode(
                self.connections[run_id],
                run_id=run_id,
                terminal=None,
                outcome_states=[],
                hp_battles=[],
                unqualified=True,
                segments=observer.assembler.committed,
                actor=self.actor,
                episode=observer.assembler.identity["intent_id"],
            )
            self._release_episode_payloads(observer)
        except Exception as error:
            row.update(
                status="save_failed",
                reason="segment_delivery_incomplete",
                save_error=(
                    error.code if isinstance(error, BoundaryError) else type(error).__name__
                ),
            )
            report.update(status="stopped", stop_reason=row["reason"])
        finally:
            if observer.error_record is not None:
                row["segment_observer_error"] = observer.error_record
            row["segment_stream_close"] = {
                "segments": observer.assembler.committed,
                "interruption": observer.interruption,
                "completed_rows_after_interruption": observer.after_interruption,
                "commit_requires_reconciliation": observer.assembler.commit_started,
            }
            observer.close()
            # A failed observer is now fully stopped. Close its admitted episode
            # once, without assigning a terminal label or retrying an unknown End.
            episode = observer.assembler.identity["intent_id"]
            if not observer.end_started:
                observer.end_started = True
                try:
                    self._end_episode(self.connections[run_id], run_id=run_id,
                        terminal=None, outcome_states=[], hp_battles=[], unqualified=True,
                        segments=observer.assembler.committed, actor=self.actor,
                        episode=episode)
                except Exception:
                    row["segment_control_cleanup_pending"] = True
            if hasattr(self.admission, "release_closed_episode"):
                self.admission.release_closed_episode(self.actor, episode)
            self.connections.pop(run_id).close()
            self.observers.pop(run_id)
            from .reference_suite import _Artifacts

            if row.get("status") in FAILURE_STATUSES:
                # Read before the local original is released; diagnostic only.
                row["failure_facts"] = failure_facts(path.parent, row)
            artifacts = _Artifacts.reopen(path.parent)
            if row.get("stored_failure_id") or row.get("stored_evidence_id"):
                try:
                    self._preserve_behavior(row, artifacts)
                    self._release_new_originals(row, self.new_originals[run_id][0])
                except Exception as error:
                    row.update(
                        status="save_failed",
                        reason="behavior_preservation_failed",
                        save_error=error.code
                        if isinstance(error, BoundaryError)
                        else type(error).__name__,
                    )
                    report.update(status="stopped", stop_reason=row["reason"])
            artifacts.save(report)
        return report

    def _inference_binding(self, row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        from stpd.p1.local_inference import AUTH_ENV

        row.update(inference_mode="central_segments", trajectory_encoding="short-segment-1")
        return (
            {**self.attachment, "actor": self.actor, "episode": row["intent_id"]},
            {AUTH_ENV: os.environ[AUTH_ENV]},
        )

    def _discard_hot_segment(self, segment: dict[str, Any]) -> dict[str, Any]:
        self.exchange.publish_event("input_disposition", stage="discarded_on_restore",
            training_generation=segment["training_generation"],
            segment_id=segment["segment_id"], original_commit=segment["original_commit"])
        self.admission.release_discarded_lease(
            segment["training_generation"], segment["segment_id"])
        return {"training_generation": segment["training_generation"],
                "discarded_on_restore": True, "original_commit": segment["original_commit"]}

    def _commit_hot_segment(self, row: dict[str, Any], connection: Any,
                            segment: dict[str, Any]) -> dict[str, Any]:
        if self.attachment.get("dataplane"):
            return self._commit_binary_segment(row, connection, segment)
        if self.exchange.hot_generation_discarded(segment["training_generation"]):
            return self._discard_hot_segment(segment)
        # No archive/compressed duplicate/Store manifest on the training edge.
        began = time.perf_counter()
        costs: dict[str, float] = {}
        try:
            payload = self.exchange.put_hot_segment(
                segment, owner="episode:" + row["intent_id"], costs=costs
            )
        except BoundaryError as error:
            if error.code != "hot_generation_discarded_on_restore":
                raise
            return self._discard_hot_segment(segment)
        hot_write = time.perf_counter() - began
        record = {key: segment[key] for key in (
            "run", "training_generation", "segment_id", "run_id", "rows", "mu")}
        record.update(payload=asdict(payload), segment_artifact_id="hot:" + payload.slot)
        sequence = int(segment["segment_id"].rpartition("/")[2])
        began = time.perf_counter()
        self.exchange.publish_segment(self.actor, sequence, record)
        record_write = time.perf_counter() - began
        began = time.perf_counter()
        accepted = connection.request("commit", **record)
        if accepted.get("reason") == "generation_changed":
            self.exchange.publish_event(
                "input_disposition",
                stage="discarded_on_restore",
                training_generation=segment["training_generation"],
                segment_id=segment["segment_id"],
                payload=asdict(payload),
            )
        elif accepted.get("segment_id") != segment["segment_id"]:
            raise BoundaryError("rl_segment", "segment_admission_receipt_changed")
        return {"segment_artifact_id": "hot:" + payload.slot, "payload": asdict(payload),
                "training_generation": segment["training_generation"],
                "original_commit": segment["original_commit"],
                "discarded_on_restore": accepted.get("reason") == "generation_changed",
                "cost_seconds": {**segment["original_commit"]["cost_seconds"], **costs,
                    "hot_write": hot_write, "control_record": record_write,
                    "commit_ack": time.perf_counter() - began},
                "hot_bytes": payload.size}

    def _upload_auxiliary(self, labels: dict[str, Any]) -> list[dict[str, Any]]:
        from spireagent.rl_socket_transport import upload
        from stpd.rl.tensor_segment import packet_header

        encoder = self.binary_encoders.get(str(labels.get("run_id")))
        if encoder is None:
            if labels.get("outcome_states") or labels.get("hp_battles"):
                raise BoundaryError("rl_segment", "binary_auxiliary_spool_missing")
            return []
        endpoint = self.attachment.get("learner_endpoint", self.attachment)
        result = []
        for encoded in encoder.auxiliary(labels):
            header = packet_header(encoded.packet)
            ref = upload(endpoint, encoded)
            result.append({"segment_id": header["witness"]["segment_id"], "payload": asdict(ref),
                           "envelope": encoded.envelope})
        return result

    def _commit_binary_segment(self, row: dict[str, Any], connection: Any,
                               segment: dict[str, Any]) -> dict[str, Any]:
        from spireagent.rl_dataplane_ports import CONTROL_SCHEMA
        from spireagent.rl_socket_transport import upload

        endpoint = self.attachment.get("learner_endpoint", self.attachment)
        if segment["training_generation"] != endpoint["training_generation"]:
            return self._discard_hot_segment(segment)
        with self.admission.pool.controls:
            generated = self.admission.pool.generated
            generated["generated_R"] += segment["rows"]
            generated["generated_D"] += sum(len(s["input"]["catalog"]) > 1
                                             for s in segment["steps"])
            generated["generated_segments"] += 1
        started = time.perf_counter()
        encoded = self.binary_encoders[segment["run_id"]].encode(segment)
        compiled = time.perf_counter() - started
        started = time.perf_counter()
        payload = upload(endpoint, encoded)
        auxiliaries = []
        if segment["hp_battles"]:
            needed = {s["source"]["segment_id"] for battle in segment["hp_battles"]
                      for s in battle["states"]} | {segment["segment_id"]}
            previous = [s for s in self.observers[segment["run_id"]].assembler.committed
                        if s["segment_id"] in needed]
            auxiliaries = self._upload_auxiliary({
                **{key: segment[key] for key in ("run", "training_generation", "run_id")},
                "actor": self.actor, "episode": row["intent_id"], "terminal": None,
                "outcome_states": [], "hp_battles": segment["hp_battles"],
                "segments": [*previous, {"segment_id": segment["segment_id"],
                                         "payload": asdict(payload)}]})
        uploaded = time.perf_counter() - started
        record = {key: segment[key] for key in (
            "run", "training_generation", "segment_id", "run_id", "rows", "mu")}
        record.update(payload=asdict(payload), segment_artifact_id="tensor:" + payload.slot,
                      envelope=encoded.envelope, binary_aux=auxiliaries,
                      control_schema=CONTROL_SCHEMA)
        started = time.perf_counter()
        accepted = connection.request("commit", **record)
        if accepted.get("segment_id") != segment["segment_id"]:
            raise BoundaryError("rl_segment", "binary_segment_admission_receipt_changed")
        inference = [s["behavior"].get("server_seconds") for s in segment["steps"]
                     if s["behavior"].get("score_source") != "reused_consumption"]
        costs = {**segment["original_commit"]["cost_seconds"], "tensor_compile": compiled,
                 "tensor_upload": uploaded, "commit_ack": time.perf_counter() - started,
                 "game_step_roundtrip": sum(s["behavior"].get(
                     "game_step_roundtrip_seconds", 0.) for s in segment["steps"]),
                 "mu_inference": sum(inference) if all(type(t) in (int, float)
                                                       for t in inference) else None,
                 "begin_wait": sum(s["behavior"].get("begin_wait_seconds", 0.)
                                   for s in segment["steps"])}
        for key in ("game_step_roundtrip", "mu_inference", "begin_wait", "tensor_upload",
                    "original_small_file_fsync"):
            if costs[key] is None:
                row.setdefault("timing_unknown", {})[key] = True
                continue
            phases = row.setdefault("phase_seconds", {})
            phases[key] = phases.get(key, 0.) + costs[key]
            metric = {"game_step_roundtrip": "game_step_seconds", "mu_inference":
                "producer_mu_inference_seconds", "begin_wait": "producer_begin_wait_seconds",
                "tensor_upload": "upload_seconds",
                "original_small_file_fsync": "original_fsync_seconds"}[key]
            self.admission.pool.observe_timing(metric, costs[key])
        return {"segment_artifact_id": "tensor:" + payload.slot, "payload": asdict(payload),
                "training_generation": segment["training_generation"],
                "original_commit": segment["original_commit"], "discarded_on_restore": False,
                "cost_seconds": costs, "binary_bytes": payload.size}

    def _runtime_started(
        self, startup: dict[str, Any], row: dict[str, Any], artifacts: Any, registration: Path
    ) -> LiveSegmentProducer:
        from stpd.p1.local_inference import SegmentConnection

        connection = SegmentConnection(self.attachment)
        identity = {
            "producer": self.producer.to_dict(),
            "host_pin": self.configuration["host_pin"],
            "runtime_pin": self.configuration["runtime_pin"],
            "seed": row["seed"],
            "intent_id": row["intent_id"],
            "character": row["scenario"]["character"],
            "ascension": row["scenario"]["ascension"],
            "behavior_policy": row["behavior_policy"],
            "initial_progress_sha256": self.configuration["initial_progress"]["progress_sha256"],
        }
        if self.attachment.get("dataplane"):
            import tempfile

            from stpd.rl.tensor_segment import TensorSegmentEncoder

            endpoint = self.attachment.get("learner_endpoint", self.attachment)
            spool = tempfile.TemporaryDirectory(prefix="stpd-binary-actor-")
            self.binary_spools[startup["run_id"]] = spool
            recipe = {**endpoint["binary_recipe"], "actor": self.actor,
                      "producer": self.producer.to_dict()}
            self.binary_encoders[startup["run_id"]] = TensorSegmentEncoder(
                vocabulary=endpoint["vocabulary"], compiler_identity=endpoint["compiler_identity"],
                recipe=recipe, spool=spool.name)

        def commit(segment: dict[str, Any], _originals: bytes) -> dict[str, Any]:
            return self._commit_hot_segment(row, connection, segment)

        observer = LiveSegmentProducer(
            self.runtime,
            artifacts.root / "runs" / startup["run_id"],
            registration / "behavior-decisions.jsonl",
            identity,
            commit,
            artifacts.root / "private",
            step_limit=self.attachment["segment_steps"],
            compiler=FrameCompiler(self.attachment["vocabulary"]),
            scorer=self.admission.scorer,
            generation_discarded=self.exchange.hot_generation_discarded,
        )
        self.new_originals[startup["run_id"]] = (
            artifacts.root / "runs" / startup["run_id"], registration / "behavior-decisions.jsonl")
        self.connections[startup["run_id"]] = connection
        self.observers[startup["run_id"]] = observer
        return observer

    def _verify_original(self, row: dict[str, Any], run: Path,
                         expected: dict[str, Any]) -> VerificationResult[AgentSessionRunEvidence]:
        observer = self.observers.get(row["run_id"])
        if observer is None:
            raise BoundaryError("rl_segment", "original_live_observer_missing")
        observer.wait()
        return observer.verifier.verify(expected)

    def _preserve_behavior(self, row: dict[str, Any], artifacts: Any) -> None:
        """Full-retention runs keep their actual per-mu journal alongside native originals."""
        from .managed_pipeline import _referenced

        owned = self.new_originals.get(row["run_id"])
        if owned is None or row.get("behavior_preservation"):
            return
        _, trace = owned
        segments = row.get("segment_stream_close", {}).get("segments", [])
        if row.get("segment_delivery"):
            segments = json.loads(_referenced(artifacts.root, row["segment_delivery"]))["segments"]
        prefix = segments[-1]["original_commit"] if segments else None
        original = row.get("stored_evidence_id") or row["stored_failure_id"]
        with _phase(row, "behavior_preserve"):
            with trace.open("rb") as stream:
                os.fsync(stream.fileno())
                payload = self.store.put_payload("behavior", stream, "application/x-ndjson")
            manifest = Manifest(
                "run_event",
                self.producer,
                parents=(Parent("original", original),),
                payloads=(payload,),
                parameters=FrozenObject.of(
                    {
                        "purpose": "rl-behavior-original",
                        "run": self.exchange.run,
                        "run_id": row["run_id"],
                        "verified_prefix_bytes": prefix["behavior_trace_bytes"] if prefix else 0,
                        "verified_prefix_sha256": prefix["behavior_trace_prefix_sha256"]
                        if prefix
                        else None,
                    }
                ),
            )
            self.store.publish(manifest)
            row["behavior_preservation_pending"] = artifacts.put(
                "behavior-preservation-pending.json",
                json_bytes(
                    {
                        "artifact_id": manifest.artifact_id,
                        "sha256": payload.sha256,
                        "bytes": payload.size,
                    }
                ),
            )
            copy_artifact(self.store, self.peer, manifest.artifact_id, reuse_published_parents=True)
            observed = self.peer.get_manifest(manifest.artifact_id)
            if observed != manifest:
                raise BoundaryError("rl_segment", "behavior_original_manifest_changed")
            prefix_hash, prefix_bytes = hashlib.sha256(), 0
            for chunk in self.peer.read_payload(observed.payload("behavior")):
                if prefix is not None:
                    part = chunk[:max(0, prefix["behavior_trace_bytes"] - prefix_bytes)]
                    prefix_hash.update(part)
                    prefix_bytes += len(part)
            if prefix is not None and (prefix_bytes != prefix["behavior_trace_bytes"]
                    or prefix_hash.hexdigest() != prefix["behavior_trace_prefix_sha256"]):
                self._request_training_stop("behavior_original_changed_after_commit")
                raise BoundaryError("rl_segment", "behavior_original_changed_after_commit")
            row["behavior_preservation"] = artifacts.put("behavior-preservation.json", json_bytes({
                "artifact_id": manifest.artifact_id, "sha256": payload.sha256,
                "bytes": payload.size, "full_readback": True}))

    def _after_preservation(self, row: dict[str, Any], artifacts: Any, run: Path,
                            expected: dict[str, Any], route: str) -> None:
        if route == "full_peer":
            self._preserve_behavior(row, artifacts)
        if row.get("training_audit_selected"):
            from .rl_sampling_audit import SamplingAudits

            if not hasattr(self, "sampling_audits"):
                self.sampling_audits = SamplingAudits(
                    self.exchange, getattr(self, "actor", "audit"), self.budget_id,
                    self._request_training_stop)
            typed = json.loads((artifacts.root / row["typed_verification"]["path"]).read_bytes())
            retained_row = dict(row)
            self.sampling_audits.submit(row, artifacts, run, expected, typed["content_id"],
                lambda: self._release_new_originals(retained_row, run))
            return
        # Only our own fresh Runtime run can be released, after labels and all
        # required original preservation/audit. Failures use the full-peer path.
        owned = self.new_originals.get(row["run_id"])
        if owned is None or row.get("status") not in {"saved", "finished", "censored"}:
            return
        if route == "full_peer" and not row.get("stored_evidence_id"):
            return
        if route not in {"ledger_only", "full_peer"}:
            return
        self._release_new_originals(row, run)

    def close(self) -> dict[str, Any] | None:
        try:
            if hasattr(self, "sampling_audits"):
                self.sampling_audits.close()
        finally:
            result = super().close()
        return result

    def _request_training_stop(self, reason: str) -> None:
        # A failed audit stops the actual current learner. Its immutable finding
        # remains; after diagnosis a fresh generation can resume the same run.
        self.exchange.publish_event("integrity_failed", reason=reason, budget_id=self.budget_id)
        from stpd.p1.local_inference import SegmentConnection

        control = SegmentConnection(self.admission.attachment)
        try:
            state = control.request("status")
        except (OSError, ValueError, TimeoutError) as error:
            self.exchange.publish_event("integrity_stop_pending", reason=reason,
                                        budget_id=self.budget_id,
                                        error_type=type(error).__name__)
            return  # The normal watcher consumes the failure even without control.
        finally:
            control.close()
        if state["run"] != self.exchange.run:
            raise BoundaryError("rl_segment", "audit_stop_run_changed")
        self.exchange.request_stop(reason, training_generation=state["training_generation"])

    def _release_new_originals(self, row: dict[str, Any], run: Path) -> None:
        owned = self.new_originals.get(row["run_id"])
        if owned is None:
            return
        owned_run, trace = owned
        if owned_run != run:
            raise BoundaryError("rl_segment", "new_original_owner_changed")
        released = sum(path.stat().st_size for path in run.iterdir()) + trace.stat().st_size
        shutil.rmtree(run)
        trace.unlink()
        copied_model = trace.parent / "model.tensor-tree"
        if copied_model.is_file():
            row["temporary_model_copy_released_bytes"] = copied_model.stat().st_size
            copied_model.unlink()
        self.new_originals.pop(row["run_id"])
        row["temporary_originals_released_bytes"] = released

    def _record_verified_original(
        self,
        row: dict[str, Any],
        artifacts: Any,
        run: Path,
        expected: dict[str, Any],
        package: Path,
        runtime_instance_id: str,
        result: Any,
    ) -> None:
        from stpd.p1.trajectory import extract_trajectory
        from stpd.rl.heads import checked_terminal_class
        from stpd.rl.reward import episode_reward

        super()._record_verified_original(
            row, artifacts, run, expected, package, runtime_instance_id, result
        )
        observer = self.observers.get(row["run_id"])
        if observer is None:
            raise BoundaryError("rl_segment", "original_live_observer_missing")
        # This temporary closed projection is used for native labels only. Live
        # training samples come exclusively from per-mu segments, never this old
        # one-model episode encoding.
        identity = {
            **observer.assembler.identity,
            "model_artifact_id": self.configuration["models"][row["selection_id"]]["artifact_id"],
            "model_sha256": expected["agent_artifact_sha256"],
            "input_spec": result.value.agent_manifest["input"]["input_spec"],
            "retention": row["retention"],
        }
        identity.pop("intent_id")
        episode = extract_trajectory(
            result.value,
            identity=identity,
            outcome=row["agent_task_outcome"],
            status=row["status"],
            reason=row["reason"],
            max_floor=(row.get("game_metrics") or {}).get("max_observed_floor"),
        )
        reward, disposition = episode_reward(episode)
        terminal = None
        if reward is not None and disposition in {"win", "loss"}:
            terminal = {
                "class": checked_terminal_class(
                    result=disposition,
                    floor=episode["outcome"]["max_observed_floor"],
                    reward=reward,
                ),
                "reward": reward,
                "typed_original": result.value.content_id,
            }
        observer.finish(terminal)
        row["segment_budget_close"] = (
            row["status"] == "censored"
            and observer.report is not None
            and observer.report.get("program_close_reason") == "training_segment_budget_complete"
        )
        generation = self.admission.episode_owner(
            self.actor, observer.assembler.identity["intent_id"])["training_generation"]
        current_segments = [s for s in observer.assembler.committed
            if s["training_generation"] == generation and not s.get("discarded_on_restore")]
        auxiliary = completed_auxiliary(
            episode, current_segments, terminal if current_segments else None)
        auxiliary["typed_original"] = result.value.content_id
        auxiliary.update(actor=self.actor, episode=observer.assembler.identity["intent_id"],
                         segments=observer.assembler.committed)
        observer.end_started = True
        self._end_episode(self.connections[row["run_id"]], **auxiliary)
        self._release_episode_payloads(observer)
        row["segment_delivery"] = artifacts.put(
            "segment-delivery.json",
            json_bytes(
                {
                    "schema": "stpd/closed-segment-delivery-1",
                    "segments": observer.assembler.committed,
                    "terminal": terminal,
                    "hp_battles": len(auxiliary["hp_battles"]),
                }
            ),
        )
        observer.close()
        self.connections.pop(row["run_id"]).close()
        self.observers.pop(row["run_id"])
