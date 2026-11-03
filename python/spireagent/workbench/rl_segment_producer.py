"""Incrementally consume the ordinary Runtime verifier, then commit short segments.

No polling/replay of game operations: a child reads the original journal once.
The same Managed executor remains the only lifecycle owner. Prefix admission is
independent of eventual whole-run closure; a natural terminal label is supplied
only by the normal typed save pipeline after it has checked the original run.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.archives import archive_bundle
from stpd.p1.sampling import distribution
from stpd.p1.trajectory import validate_behavior

from .managed_segment_store import SCHEMA


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
        refs: list[dict[str, Any]] = []
        for name in sorted({name for step in steps for name in step["original_paths"]}):
            path = self.run / name
            if path.parent != self.run or path.is_symlink():
                raise BoundaryError("rl_segment", "original_path_changed")
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            refs.append({"path": name, "sha256": sha, "bytes": path.stat().st_size})
        through = steps[-1]["verified_through_sequence"]
        journal_start, sequence_start = self.bytes, self.sequence
        trace_start = self.trace_bytes
        journal_delta, trace_delta = [], []
        while self.sequence < through:
            raw = self.journal.readline()
            if not raw.endswith(b"\n"):
                raise BoundaryError("rl_segment", "verified_journal_prefix_missing")
            event = json.loads(raw)
            if event["sequence"] != self.sequence + 1:
                raise BoundaryError("rl_segment", "verified_journal_sequence_changed")
            self.hash.update(raw)
            self.bytes += len(raw)
            self.sequence += 1
            journal_delta.append(raw)
        os.fsync(self.journal.fileno())
        while self.trace_bytes < trace_through_bytes:
            chunk = self.trace_reader.read(min(1024**2, trace_through_bytes - self.trace_bytes))
            if not chunk:
                raise BoundaryError("rl_segment", "original_behavior_prefix_missing")
            self.trace_hash.update(chunk)
            self.trace_bytes += len(chunk)
            trace_delta.append(chunk)
        os.fsync(self.trace_reader.fileno())
        for name in ("manifest.json", "agent-manifest.json", "adapter-attestation.json"):
            with (self.run / name).open("rb") as stream:
                os.fsync(stream.fileno())
        for directory in {self.run, self.trace.parent}:
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        metadata: dict[str, Any] = {
            "schema": "stpd/segment-original-commit-1",
            "run_id": steps[0]["run_id"],
            "segment_id": steps[0]["behavior"]["lease"],
            "journal_start_bytes": journal_start,
            "journal_start_sequence": sequence_start,
            "journal_bytes": self.bytes,
            "verified_through_sequence": self.sequence,
            "journal_prefix_sha256": self.hash.hexdigest(),
            "artifacts": refs,
            "behavior_trace": str(self.trace),
            "behavior_trace_start_bytes": trace_start,
            "behavior_trace_bytes": self.trace_bytes,
            "behavior_trace_prefix_sha256": self.trace_hash.hexdigest(),
            "at": time.time(),
        }
        # This is the existing observer thread, separate from the actual game.
        # Archive immutable original files and only the newly verified journal/
        # behavior bytes. The previous-segment parent preserves the full chain.
        with tempfile.TemporaryDirectory(prefix="segment-originals-") as stage_directory:
            stage = Path(stage_directory)
            (stage / "originals").mkdir()
            for reference in refs:
                target = stage / "originals" / reference["path"]
                shutil.copyfile(self.run / reference["path"], target)
                with target.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != reference["sha256"]:
                        raise BoundaryError("rl_segment", "original_changed_during_archive")
            (stage / "events-delta.jsonl").write_bytes(b"".join(journal_delta))
            (stage / "behavior-delta.jsonl").write_bytes(b"".join(trace_delta))
            for name in ("agent-manifest.json", "adapter-attestation.json"):
                shutil.copyfile(self.run / name, stage / name)
            # Preserve the actual bytes used by the existing verifier at its
            # initialization, without racing the live finalization rewrite.
            (stage / "manifest.json").write_bytes(
                base64.b64decode(steps[-1]["manifest_original_base64"], validate=True)
            )
            (stage / "commit.json").write_bytes(json_bytes(metadata))
            original_bytes = archive_bundle(stage)
        return (
            {
                **metadata,
                "archive_sha256": hashlib.sha256(original_bytes).hexdigest(),
                "archive_bytes": len(original_bytes),
            },
            original_bytes,
        )

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
    ) -> None:
        self.identity, self.barrier, self.commit_segment = identity, barrier, commit
        self.step_limit = step_limit
        self.steps: list[dict[str, Any]] = []
        self.terminal_pending = False
        self.trace = barrier.trace.open("rb")
        self.trace_rows: dict[str, dict[str, Any]] = {}
        self.trace_end = 0
        self.committed: list[dict[str, Any]] = []
        self.commit_started = False
        self.battle_start: int | None = None
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
        if self.steps and behavior["lease"] != self.steps[0]["behavior"]["lease"]:
            self._commit(None)
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
        if fighting and self.battle_start is None:
            self.battle_start = row["step_index"]
        after = row["successor"]["observation"]
        if self.battle_start is not None and (
            (after.get("combat") or {}).get("availability") != "available"
        ):
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
                        "end_step": row["step_index"] + 1,
                        "hp": hp / 100,
                        "boundary_step_index": row["step_index"],
                        "boundary_acquisition_id": row["successor"]["acquisition_id"],
                    }
                )
            self.battle_start = None
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
                for local in range(previous["rows"]):
                    index = previous["start_step"] + local
                    if battle["start_step"] <= index < battle["end_step"]:
                        states.append(
                            {
                                "key": f"{self.steps[0]['run_id']}:{index}",
                                "segment_artifact_id": previous["segment_artifact_id"],
                                "row": local,
                                "hp": battle["hp"],
                            }
                        )
            for local, step in enumerate(self.steps):
                if battle["start_step"] <= step["step_index"] < battle["end_step"]:
                    states.append(
                        {
                            "key": f"{step['run_id']}:{step['step_index']}",
                            "current_row": local,
                            "hp": battle["hp"],
                        }
                    )
            battles.append({**battle, "states": states})
        original_commit, original_bytes = self.barrier.commit(
            self.steps, trace_through_bytes=self.trace_end
        )
        value = {
            "schema": SCHEMA,
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
                self.committed[-1]["segment_artifact_id"] if self.committed else None
            ),
        }
        result = self.commit_segment(value, original_bytes)
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
    ) -> None:
        self.assembler = SegmentAssembler(
            identity, OriginalBarrier(run, trace), commit, step_limit=step_limit
        )
        self.error: BaseException | None = None
        self.report: dict[str, Any] | None = None
        self.stop_requested = False
        self.interruption: dict[str, Any] | None = None
        self.after_interruption = 0
        self.end_started = False
        self.stderr = (private / "segment-verifier.stderr").open("xb")
        self.process = subprocess.Popen(
            ["node", str(runtime / "global-live-replay.js"), "--run", str(run)],
            stdout=subprocess.PIPE,
            stderr=self.stderr,
        )
        self.thread = threading.Thread(
            target=self._read, name="verified-short-segments", daemon=True
        )
        self.thread.start()

    def _read(self) -> None:
        try:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                value = json.loads(line)
                if value.get("schema") == "sts2.policy-runtime/verified-global-end-1":
                    self.report = value["report"]
                elif value.get("schema") == "sts2.policy-runtime/verified-global-boundary-1":
                    if self.interruption is None:
                        self.interruption = value
                        self.stop_requested = True
                        self.assembler.finish(None)
                elif self.interruption is not None:
                    self.after_interruption += 1
                else:
                    self.assembler.transition(value)
            if self.process.wait() != 0 or self.report is None:
                raise BoundaryError("rl_segment", "original_prefix_verifier_failed")
        except BaseException as error:
            self.error = error

    def finish(self, terminal: dict[str, Any] | None) -> None:
        self.thread.join(timeout=120)
        if self.thread.is_alive():
            raise BoundaryError("rl_segment", "prefix_verifier_close_pending")
        if self.error:
            self.assembler.finish(None)
            raise self.error
        self.assembler.finish(terminal)

    def close(self) -> None:
        if self.process.poll() is None:
            # Only the read-only verifier child is stopped here; the original
            # Managed owner has already closed its Runtime and Host.
            self.process.terminate()
            self.process.wait(timeout=10)
        self.thread.join(timeout=10)
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
        for local in range(segment["rows"]):
            references[segment["start_step"] + local] = {
                "key": f"{episode['run_id']}:{segment['start_step'] + local}",
                "segment_artifact_id": segment["segment_artifact_id"],
                "row": local,
            }
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
from .managed_pipeline import PipelinedManagedExecutor  # noqa: E402


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
        self.observers: dict[str, LiveSegmentProducer] = {}
        self.connections: dict[str, Any] = {}

    def _before_world_offer(self, row: dict[str, Any]) -> None:
        result = self.admission.request("episode_open", actor=self.actor, episode=row["intent_id"])
        if result.get("admitted") is not True:
            raise BoundaryError("rl_segment", "learner_stopped_before_world_offer")

    def __call__(
        self, row: dict[str, Any], manifest: dict[str, Any], artifacts: Any, checkpoint: Any
    ) -> None:
        super().__call__(row, manifest, artifacts, checkpoint)
        if row.get("run_id") is None and row.get("cleanup_accepted") is True:
            try:
                self.admission.request(
                    "episode_end",
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
            # Typed failure/technical close has no terminal label. Deliver only
            # the earlier verified tail that has never attempted admission.
            observer.finish(None)
            if observer.end_started:
                raise BoundaryError("rl_segment", "original_episode_end_requires_reconciliation")
            observer.end_started = True
            self.connections[run_id].request(
                "episode_end",
                run_id=run_id,
                terminal=None,
                outcome_states=[],
                hp_battles=[],
                unqualified=True,
                segments=observer.assembler.committed,
                actor=self.actor,
                episode=observer.assembler.identity["intent_id"],
            )
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
            row["segment_stream_close"] = {
                "segments": observer.assembler.committed,
                "interruption": observer.interruption,
                "completed_rows_after_interruption": observer.after_interruption,
                "commit_requires_reconciliation": observer.assembler.commit_started,
            }
            observer.close()
            self.connections.pop(run_id).close()
            self.observers.pop(run_id)
            from .reference_suite import _Artifacts

            _Artifacts.reopen(path.parent).save(report)
        return report

    def _inference_binding(self, row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        from stpd.p1.local_inference import AUTH_ENV

        row.update(inference_mode="central_segments", trajectory_encoding="short-segment-1")
        return (
            {**self.attachment, "actor": self.actor, "episode": row["intent_id"]},
            {AUTH_ENV: os.environ[AUTH_ENV]},
        )

    def _runtime_started(
        self, startup: dict[str, Any], row: dict[str, Any], artifacts: Any, registration: Path
    ) -> LiveSegmentProducer:
        from stpd.p1.local_inference import SegmentConnection

        from .managed_segment_store import publish_segment, read_segment

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

        def commit(segment: dict[str, Any], originals: bytes) -> dict[str, Any]:
            # Parent model artifacts already reside at the peer. Actors never
            # download a new full model merely to acknowledge a mu publication.
            manifest = publish_segment(self.peer, segment, self.producer, originals=originals)
            observed = read_segment(
                self.peer, manifest.artifact_id, scratch=artifacts.root / "private"
            )
            if observed != segment:
                raise BoundaryError("rl_segment", "segment_peer_readback_changed")
            record = {key: segment[key] for key in ("segment_id", "run_id", "rows", "mu")}
            record["segment_artifact_id"] = manifest.artifact_id
            sequence = int(segment["segment_id"].rpartition("/")[2])
            self.exchange.publish_segment(self.actor, sequence, record)
            accepted = connection.request("commit", **record)
            if accepted.get("segment_id") != segment["segment_id"]:
                raise BoundaryError("rl_segment", "segment_admission_receipt_changed")
            return {"segment_artifact_id": manifest.artifact_id}

        observer = LiveSegmentProducer(
            self.runtime,
            artifacts.root / "runs" / startup["run_id"],
            registration / "behavior-decisions.jsonl",
            identity,
            commit,
            artifacts.root / "private",
            step_limit=self.attachment["segment_steps"],
        )
        self.connections[startup["run_id"]] = connection
        self.observers[startup["run_id"]] = observer
        return observer

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
        auxiliary = completed_auxiliary(episode, observer.assembler.committed, terminal)
        auxiliary["typed_original"] = result.value.content_id
        auxiliary.update(actor=self.actor, episode=observer.assembler.identity["intent_id"])
        observer.end_started = True
        self.connections[row["run_id"]].request("episode_end", **auxiliary)
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
