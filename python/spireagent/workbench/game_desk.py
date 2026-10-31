"""Game-desk use cases over the existing Recorder, LocalModelService and Store.

Window state is never business state. Native mouse originals remain ordinary
recordings; model originals remain Runtime evidence. This index joins references
and versioned manual labels without rewriting either original or granting Human
proof, research admission, or a model score.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes

from .developer import ROOT
from .local_models import GLOBAL_MODEL_ADAPTERS, LocalModelService
from .native_tasks import P1_PROFILE

SCHEMA = "spireagent/game-desk-1"
INDEX_SCHEMA = "spireagent/game-desk-recording-1"
RANK_SCHEMA = "spireagent/human-ranking-labels-1"
MODEL_KINDS = frozenset(
    {
        "global_input",
        "agent_directive",
        "human_directive",
        "global_choice",
        "global_dispatch",
        "global_receipt",
        "global_dispatch_error",
        "global_result",
        "mode_changed",
        "global_control_fence",
        "runtime_tainted",
        "stopped",
    }
)


def reject(code: str) -> NoReturn:
    raise BoundaryError("game_desk", code)


def now() -> str:
    return datetime.now(UTC).isoformat()


def key(value: Any) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def control_request_context(value: dict[str, Any]) -> dict[str, Any]:
    result = {
        name: value[name] for name in ("runtime_run_id", "runtime_instance_id", "recovery_epoch")
    }
    LocalModelService.read_control_context(result)
    return result


def read_artifact(directory: Path, reference: dict[str, Any]) -> dict[str, Any]:
    """The same immutable Runtime artifact encoding; no arbitrary paths or pickle."""
    digest = reference["sha256"]
    name = reference["path"]
    if name not in {f"global-{digest}.bin", f"global-{digest}.bin.gz"}:
        reject("runtime_artifact_reference_invalid")
    path = directory / name
    if path.is_symlink() or not path.is_file() or not 0 < reference["bytes"] <= 96 * 1024**2:
        reject("runtime_artifact_unavailable")
    with gzip.open(path, "rb") if name.endswith(".gz") else path.open("rb") as source:
        raw = source.read(reference["bytes"] + 1)
    if len(raw) != reference["bytes"] or hashlib.sha256(raw).hexdigest() != digest:
        reject("runtime_artifact_changed")
    value = json.loads(raw)
    if not isinstance(value, dict):
        reject("runtime_artifact_invalid")
    return value


def present_choices(
    catalog: list[dict[str, Any]],
    observation: dict[str, Any] | None,
    model_choice: str | None = None,
) -> list[dict[str, Any]]:
    """Labels/order only. Every authoritative candidate and choice ID is retained."""
    combat = ((observation or {}).get("combat") or {}).get("content") or {}
    hand = (combat.get("player") or {}).get("hand") or []
    enemies = combat.get("enemies") or []
    cards = {card.get("entity_id"): (i, card) for i, card in enumerate(hand)}
    targets = {enemy.get("entity_id"): (i, enemy) for i, enemy in enumerate(enemies)}
    presented = []
    for ordinal, choice in enumerate(catalog):
        row = {**choice, "model_selected": choice["choice_id"] == model_choice}
        kind = choice.get("kind")
        if kind == "combat.play_card" and choice.get("subject") in cards:
            position, card = cards[choice["subject"]]
            row["label"] = f"手牌 {position + 1} · {card['name']}"
            target = targets.get(choice.get("target"))
            if target:
                row["label"] += f" → {target[1]['name']}（敌人 {target[0] + 1}）"
            elif choice.get("target"):
                row["label"] = choice["label"]  # Do not invent a missing target name.
            if "cost" in card:
                row["cost"] = card["cost"]
            order = (0, position, target[0] if target else -1, ordinal)
        elif kind in {"combat.use_potion", "combat.discard_potion"}:
            order = (1, ordinal, 0, 0)
        elif kind == "combat.end_turn":
            row["label"] = "结束回合"
            order = (2, ordinal, 0, 0)
        else:
            order = (3, ordinal, 0, 0)
        presented.append((order, row))
    return [row for _, row in sorted(presented, key=lambda item: item[0])]


def read_game_desk_recording(store: Any, artifact_id: str) -> dict[str, Any]:
    """Read a sealed index/label snapshot through the ordinary ArtifactStore reader.

    This validates the index's own binding, not the Recorder/Runtime originals.
    Their typed readers continue to own those stronger facts.
    """
    manifest = store.get_manifest(artifact_id)
    if (
        manifest.kind != "evidence"
        or manifest.parameters.value().get("schema") != INDEX_SCHEMA
        or {payload.role for payload in manifest.payloads} != {"index", "ranking-labels"}
        or manifest.payload("index").size > 64 * 1024**2
        or manifest.payload("ranking-labels").size > 8 * 1024**2
    ):
        reject("game_desk_index_artifact_required")
    index = json.loads(b"".join(store.read_payload(manifest.payload("index"))))
    labels = json.loads(b"".join(store.read_payload(manifest.payload("ranking-labels"))))
    if index.get("schema") != INDEX_SCHEMA or labels.get("schema") != RANK_SCHEMA:
        reject("game_desk_index_schema_mismatch")
    steps = {step["step_id"]: step for step in index["steps"]}
    if len(steps) != len(index["steps"]) or index.get("score_eligible") is not False:
        reject("game_desk_index_binding_mismatch")
    for label in labels["labels"]:
        step = steps.get(label["step_id"])
        if (
            step is None
            or step["input_method"] != "ranking"
            or step["recording"] is not True
            or step["ranking"] != label["ranking"]
            or step["source"] != label["source"]
        ):
            reject("game_desk_ranking_binding_mismatch")
    return {
        **index,
        "artifact_id": artifact_id,
        "original_verification": "use_original_typed_readers",
    }


class GameDeskApplication:
    def __init__(self, app: Any) -> None:
        self.app, self.models = app, app.models
        self.root = app.config.state_dir / "game-desk"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.record_lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.db = self.root / "index.sqlite3"
        with self._database() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                "event_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS metadata(name TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
        self.preference = self._get("recording_requested", None)
        self.sources = self._get("sources", {"native": {}, "runtime": {}})
        self.cursors = self._get("cursors", {})
        self.native_error = self._get("native_error", None)
        self.transition: str | None = None
        self.intent = 0
        self.message: str | None = None
        self.feedback: dict[str, Any] | None = None
        self.feedback_error: str | None = "后台未连接，没在保存"
        self.runtime: dict[str, Any] | None = None
        self._step_rows: dict[str, dict[str, Any]] = {}
        self._step_sequence = 0
        self._step_latest_runtime: dict[str, str] = {}
        self._step_inputs: dict[tuple[str, str], dict[str, Any]] = {}
        self._step_manual: dict[tuple[str, str], dict[str, Any]] = {}
        self._step_annotations: dict[str, list[dict[str, Any]]] = {}
        self.choice_context: dict[str, Any] | None = None
        self.choice_view: dict[str, Any] = {
            "catalog_key": None,
            "choices_ready": False,
            "choices_message": "打开动作列表后读取当前动作",
            "choices_model_id": None,
        }

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db, timeout=5)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def _get(self, name: str, default: Any) -> Any:
        with self._database() as db:
            row = db.execute("SELECT value FROM metadata WHERE name=?", (name,)).fetchone()
        return default if row is None else json.loads(row[0])

    def _set(self, name: str, value: Any) -> None:
        with self._database() as db:
            db.execute(
                "INSERT INTO metadata VALUES(?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (name, json.dumps(value, ensure_ascii=False)),
            )

    def _append(self, kind: str, *, event_id: str | None = None, **facts: Any) -> None:
        body = {"kind": kind, "recorded_at": now(), **facts}
        with self._database() as db:
            db.execute(
                "INSERT OR IGNORE INTO events(event_id,body) VALUES(?,?)",
                (event_id or uuid4().hex, json.dumps(body, ensure_ascii=False)),
            )

    def events(self, after: int = 0) -> list[dict[str, Any]]:
        with self._database() as db:
            rows = db.execute(
                "SELECT sequence,event_id,body FROM events WHERE sequence>? ORDER BY sequence",
                (after,),
            ).fetchall()
        return [
            {"sequence": seq, "event_id": event_id, **json.loads(body)}
            for seq, event_id, body in rows
        ]

    def start(self) -> None:
        if self.thread is not None:
            return

        def run() -> None:
            while not self.stop_event.is_set():
                try:
                    self.poll()
                except (BoundaryError, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
                    self.feedback_error = "后台索引不可用，没在确认保存；未重发游戏操作。"
                self.stop_event.wait(1)

        self.thread = threading.Thread(target=run, daemon=True, name="game-desk")
        self.thread.start()

    def close(self) -> None:
        # Closing a UI/Application observer is not a recorder or model Stop.
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def _read_stream(self, identity: str, path: Path, *, native: bool) -> None:
        if not path.is_file():
            return
        if path.is_symlink():
            reject("original_stream_symlink")
        offset = self.cursors.get(identity, 0)
        if path.stat().st_size < offset:
            reject("original_stream_truncated")
        with path.open("rb") as source:
            source.seek(offset)
            for _ in range(4096):
                before = source.tell()
                line = source.readline(2 * 1024**2 + 1)
                if not line:
                    break
                if len(line) > 2 * 1024**2:
                    reject("original_event_limit")
                if not line.endswith(b"\n"):
                    source.seek(before)
                    break
                row = json.loads(line)
                if native and row.get("kind") in {"decision_accepted", "lifecycle", "issue"}:
                    if row.get("session_id") != identity:
                        reject("native_original_session_mismatch")
                    self._append(
                        "native_event",
                        event_id=f"native:{identity}:{row['sequence']}",
                        session_id=identity,
                        original=row,
                        recording=True,
                    )
                elif not native and row.get("kind") in MODEL_KINDS:
                    self._append(
                        "runtime_event",
                        event_id=f"runtime:{identity}:{row['sequence']}",
                        run_id=identity,
                        original=row,
                        recording=(
                            self._get("manual_offer:" + identity, {}).get("recording", False)
                            if row.get("kind") == "human_directive"
                            else self.preference is True
                        ),
                    )
                offset = source.tell()
        if offset != self.cursors.get(identity):
            self.cursors[identity] = offset
            self._set("cursors", self.cursors)

    def _track_native(self, feedback: dict[str, Any]) -> None:
        session, raw = feedback.get("session_id"), feedback.get("recording_directory")
        if not session or not raw or feedback.get("profile") != P1_PROFILE:
            return
        directory = Path(raw)
        if directory.is_symlink() or not directory.is_absolute():
            reject("native_original_directory_invalid")
        manifest_path = directory / "recording-manifest.json"
        if not manifest_path.is_file():
            return  # Armed in the menu; there is no open original yet.
        manifest = json.loads(manifest_path.read_bytes())
        if (
            manifest.get("session_id") != session
            or manifest.get("capture_profile_id") != P1_PROFILE
        ):
            reject("native_original_binding_mismatch")
        if session not in self.sources["native"]:
            self.sources["native"][session] = {
                "directory": str(directory),
                "declared_origin": manifest.get("p1_native_origin"),
                "runtime_instance_id": feedback["runtime_instance_id"],
            }
            self._set("sources", self.sources)
        self._read_stream(session, directory / "p1-native-events.jsonl", native=True)

    def _attach_runtime(self) -> None:
        with self.models.lock:
            client = self.models.client
            if client is None:
                reject("model_not_loaded")
            run = client.startup["run_id"]
            if run not in self.sources["runtime"]:
                self.sources["runtime"][run] = {
                    "selection_id": self.models.state.get("selection_id"),
                    "startup": dict(client.startup),
                    "composition": self.models.composition(self.models.state["selection_id"]),
                }
                self._set("sources", self.sources)
            runs = self.models.state.setdefault("game_desk_runs", [])
            if run not in runs:
                runs.append(run)
                self.models._save()

    def _track_runtime(self) -> None:
        with self.models.lock:
            client = self.models.client
        if client is None:
            self.runtime = None
            for run in self.sources["runtime"]:
                self._read_stream(
                    run, self.models.directory / "agent-runs" / run / "events.jsonl", native=False
                )
            return
        observed = client.request("/status")["status"]
        with self.models.lock:
            if self.models.client is not client:
                return
        self.runtime = observed
        run = client.startup["run_id"]
        if run in self.sources["runtime"]:
            source = self.sources["runtime"][run]
            instance = (observed.get("environment") or {}).get("runtime_instance_id")
            if instance and source.get("runtime_instance_id") != instance:
                source["runtime_instance_id"] = instance
                self._set("sources", self.sources)
            self._read_stream(
                run, self.models.directory / "agent-runs" / run / "events.jsonl", native=False
            )

    def poll(self) -> None:
        if not self.lock.acquire(blocking=False):
            return
        try:
            try:
                feedback = self.models.native_tasks.recording_feedback(self.app.config.platform_url)
                self.feedback, self.feedback_error = feedback, None
                self._track_native(feedback)
                source = self.sources["native"].get(feedback.get("session_id"), {})
                if (
                    self.preference is None
                    and feedback.get("armed")
                    and source.get("declared_origin") == "declared_human"
                ):
                    # Only the Recorder's previously explicit Human declaration
                    # can restore this preference; AI/unknown is never promoted.
                    self.preference = True
                    self._set("recording_requested", True)
            except (BoundaryError, OSError, ValueError, TypeError, KeyError) as error:
                self.feedback_error = (
                    "后台未连接，没在保存（"
                    + getattr(error, "code", "recording_unavailable")
                    + "）"
                )
            try:
                self._track_runtime()
            except (BoundaryError, OSError, ValueError, TypeError, KeyError) as error:
                self.runtime = None
                self.message = (
                    "模型状态不可用；未重发操作（"
                    + getattr(error, "code", "runtime_unavailable")
                    + "）"
                )
            feedback = self.feedback
            can_arm = (
                self.feedback_error is None
                and feedback is not None
                and self.preference is True
                and self.transition is None
                and self.native_error is None
                and feedback["lifecycle"] in {"ready", "closed"}
                and not feedback.get("armed")
                and (
                    self.runtime is None
                    or self.runtime.get("mode") == "human"
                    and not self.runtime.get("tainted")
                    and self.runtime.get("pending_request") is None
                )
                and self.models.state.get("status") not in {"command_unknown", "recovery_required"}
                and (feedback["lifecycle"] == "ready" or feedback.get("closeout") == "closed")
                and (self.models.state.get("operation") or {}).get("status") != "pending"
            )
            if can_arm:
                try:
                    self._native_command("start_new_session")
                except (BoundaryError, OSError, ValueError) as error:
                    self.native_error = getattr(error, "code", "native_start_unconfirmed")
                    self._set("native_error", self.native_error)
        finally:
            self.lock.release()

    def _native_command(self, kind: str) -> dict[str, Any]:
        with self.record_lock:
            owner: dict[str, Any] = self.app.native_recording_status()["status"]
            if kind == "close":
                if owner["recording_lifecycle"] == "closing":
                    return owner
                if owner["recording_lifecycle"] in {"ready", "closed"}:
                    feedback = self.models.native_tasks.recording_feedback(
                        self.app.config.platform_url
                    )
                    if (
                        feedback["runtime_instance_id"] != owner["runtime_instance_id"]
                        or feedback["session_id"] != owner["recording_session_id"]
                    ):
                        reject("native_recording_context_changed")
                    if feedback["armed"] is False:
                        return owner
            if kind == "start_new_session" and owner["recording_lifecycle"] == "recording":
                return owner
            result: dict[str, Any] = self.app.control_native_recording(
                {
                    "kind": kind,
                    "runtime_instance_id": owner["runtime_instance_id"],
                    "recording_session_id": owner["recording_session_id"],
                    "source_segment_id": owner["source"]["segment_id"]
                    if owner.get("source")
                    else None,
                    "source_kind": "declared_human" if kind == "start_new_session" else None,
                    "actor_id": None,
                    "command_id": str(uuid4()),
                    **({"capture_profile_id": P1_PROFILE} if kind == "start_new_session" else {}),
                }
            )
            try:
                self.feedback = self.models.native_tasks.recording_feedback(
                    self.app.config.platform_url
                )
                self.feedback_error = None
            except (BoundaryError, OSError, ValueError):
                # The command already has an accepted owner result. A failed read
                # cannot turn it into an unknown command or authorize another POST.
                self.feedback_error = "后台状态暂不可用，没在确认保存；原命令已接受。"
            status: dict[str, Any] = result["status"]
            return status

    def _close_native(self) -> None:
        owner = self._native_command("close")
        deadline = time.monotonic() + 15
        while owner["recording_lifecycle"] == "closing" and time.monotonic() < deadline:
            self.stop_event.wait(0.1)
            owner = self.app.native_recording_status()["status"]
        if (
            owner["recording_lifecycle"] not in {"ready", "closed"}
            or owner["recording_lifecycle"] == "closed"
            and owner["closeout_status"] != "closed"
        ):
            reject("native_close_not_complete")
        self._append("native_segment_closed", session_id=owner.get("recording_session_id"))

    def record_start(self) -> dict[str, Any]:
        with self.lock:
            if self.native_error:
                reject("native_recording_recovery_required")
            self._set("recording_requested", True)
            self.preference = True
            self._append("recording_requested", enabled=True, declaration="declared_human")
        self.poll()
        if self.feedback_error:
            reject("native_recording_unavailable")
        if self.native_error:
            reject(self.native_error)
        self.message = "录制已开启；模型步始终另有原始回执。"
        return self.view()

    def record_stop(self) -> dict[str, Any]:
        with self.lock:
            self.preference = False
        try:
            self._close_native()
            self._set("recording_requested", False)
            self._append("recording_requested", enabled=False)
            self.native_error = None
            self._set("native_error", None)
            self.poll()
            self.publish_index()
        except (BoundaryError, OSError, ValueError) as error:
            self.native_error = getattr(error, "code", "native_close_unconfirmed")
            self._set("native_error", self.native_error)
            raise
        self.message = "已停录制并保存；模型运行不受影响。"
        return {**self.view(), "recovery_confirmed": True}

    def _wait_model(
        self, operation: dict[str, Any], intent: int, *, seconds: float = 40
    ) -> dict[str, Any]:
        identity = (operation.get("operation") or {}).get("id")
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with self.models.lock:
                value: dict[str, Any] = json.loads(json.dumps(self.models.state))
            if intent != self.intent:
                reject("native_model_intent_superseded")
            observed = value.get("operation") or {}
            if observed.get("id") != identity:
                reject("native_model_intent_superseded")
            if observed.get("status") != "pending":
                if observed.get("status") != "completed":
                    reject(value.get("error_code") or "model_operation_failed")
                return value
            self.stop_event.wait(0.05)
        reject("model_operation_pending_check_status")

    def _require_intent(self, intent: int) -> None:
        # LocalModelService calls this under its short admission lock. Stop
        # advances this token under that same lock without waiting for I/O.
        if self.intent != intent:
            reject("native_model_intent_superseded")

    def _current_model_selection(self, selection: str) -> str:
        """Reuse the registration owner to bind unchanged weights to this combination.

        A registration is immutable. A new adapter/core/Mod binding creates a
        new registration through that owner instead of editing historical pins.
        """
        entry = self.models.selection(selection)
        if entry["adapter"] not in GLOBAL_MODEL_ADAPTERS:
            return selection
        config = json.loads(self.models.entry_path(entry, "config").read_bytes())
        model_id = config["model_artifact_id"]
        registration = self.app.local_model_registration
        status = registration.status(model_id)
        if status.get("status") == "registered":
            candidate: str = status["selection_id"]
            candidate_entry = self.models.selection(candidate)
            manifest_path = self.models.entry_path(candidate_entry, "manifest")
            manifest = json.loads(manifest_path.read_bytes())
            capabilities = self.models.native_tasks.connector_capabilities(
                self.app.config.platform_url
            )
            producer = {k: v for k, v in capabilities["host"].items() if k != "runtime_instance_id"}

            def digest(value: Any) -> str:
                return hashlib.sha256(canonical_json(value).encode()).hexdigest()

            expected = manifest["requirements"]["environment"]
            if expected.get("producer_sha256") == digest(producer) and expected.get(
                "game_sha256"
            ) == digest(capabilities["game"]):
                try:
                    self.models._public_manifest_contract(manifest_path, candidate)
                    return candidate
                except BoundaryError as error:
                    if error.code != "native_runtime_contract_unavailable":
                        raise
        result = registration.register(model_id)
        if result.get("status") != "registered":
            reject("model_registration_not_complete")
        selected: str = result["selection_id"]
        return selected

    def _ensure_loaded(
        self,
        selection: str,
        intent: int,
        context: dict[str, Any] | None,
        authorize: Callable[[], None] | None,
    ) -> None:
        with self.models.lock:
            loaded = self.models.state.get("loaded")
            current = self.models.state.get("selection_id")
        if loaded and current == selection:
            self._attach_runtime()
            return
        if loaded:
            if (
                self.models.client is None
                or self.models.client.request("/status")["status"].get("mode") != "human"
            ):
                reject("stop_before_changing_model")
            self._wait_model(
                self.models.command("stop", intent_guard=lambda: self._require_intent(intent)),
                intent,
            )
        selection = self._current_model_selection(selection)
        self._wait_model(
            self.models.prepare_and_load(
                selection,
                "continuous",
                native_context=context,
                native_authorizer=authorize,
                intent_guard=lambda: self._require_intent(intent),
            ),
            intent,
        )

    def choices_open(
        self, context: dict[str, Any] | None = None, authorize: Callable[[], None] | None = None
    ) -> dict[str, Any]:
        with self.operation_lock:
            intent = self.intent
            with self.models.lock:
                loaded = self.models.state.get("loaded")
            if not loaded:
                selection = self._get("manual_selection", None)
                if selection is None or selection not in {
                    entry["id"] for entry in self.models.registry()["policies"]
                }:
                    selection = self.app.local_model_registration.register_global_program()[
                        "selection_id"
                    ]
                    self._set("manual_selection", selection)
                try:
                    entry = self.models.selection(selection)
                    self.models._public_manifest_contract(
                        self.models.entry_path(entry, "manifest"), selection
                    )
                except BoundaryError as error:
                    if error.code != "native_runtime_contract_unavailable":
                        raise
                    selection = self.app.local_model_registration.register_global_program()[
                        "selection_id"
                    ]
                    self._set("manual_selection", selection)
                self._ensure_loaded(selection, intent, context, authorize)
            self._attach_runtime()
        self.message = "动作列表已准备；只读取当前动作，尚未执行。"
        return self.view()

    def model_control(
        self,
        selection: str,
        mode: str,
        context: dict[str, Any] | None = None,
        authorize: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if selection not in {entry["id"] for entry in self.models_list()} or mode not in {
            "auto",
            "one_step",
        }:
            reject("unknown_local_model")
        if not self.operation_lock.acquire(blocking=False):
            reject("operation_in_progress")
        with self.lock:
            intent = self.intent
            self.transition = "model"
        try:
            # Stop/close the Human source at its own durable boundary before model control.
            self._close_native()
            self._ensure_loaded(selection, intent, context, authorize)
            self._attach_runtime()
            self._append(
                "control_requested",
                mode=mode,
                selection_id=selection,
                recording=self.preference is True,
            )
            control = control_request_context(self.models.control_context())
            self._wait_model(
                self.models.command(
                    mode,
                    expected_context=control,
                    native_context=context,
                    native_authorizer=authorize,
                    intent_guard=lambda: self._require_intent(intent),
                ),
                intent,
            )
            self.message = "模型正在连续运行" if mode == "auto" else "模型这一步已完成，已交回你。"
        finally:
            with self.lock:
                self.transition = None
            self.operation_lock.release()
            self.poll()
        return self.view()

    def model_stop(self) -> dict[str, Any]:
        with self.models.lock:
            self.intent += 1
            active = (
                self.models.client is not None
                or self.models.state.get("previous_session")
                or (self.models.thread is not None and self.models.thread.is_alive())
            )
        if active:
            observed = self.runtime or self.models.state.get("runtime") or {}
            # A tainted original is sealed, never made retryable by changing its mode.
            recovery = (
                "stop" if observed.get("tainted") or observed.get("pending_request") else "human"
            )
            operation = self.models.command(recovery)
            result = self._wait_model(operation, self.intent)
            after = result.get("runtime") or {}
            if recovery == "human" and (after.get("tainted") or after.get("pending_request")):
                self._wait_model(self.models.command("stop"), self.intent)
        self.poll()
        runtime = self.runtime or self.models.state.get("runtime")
        confirmed = (
            not active
            or runtime is None
            and self.models.state.get("status") == "stopped"
            or (
                runtime is not None
                and runtime.get("mode") == "human"
                and runtime.get("controller") == "released"
                and runtime.get("pending_request") is None
                and not runtime.get("tainted")
            )
        )
        self._append("control_stopped", recovery_confirmed=bool(confirmed))
        self.message = (
            "已停模型，现在由你操作。" if confirmed else "停止已请求；原执行结果仍需确认，未重发。"
        )
        return {**self.view(), "recovery_confirmed": bool(confirmed)}

    def rank(
        self,
        catalog_key: str,
        ranking: list[str],
        context: dict[str, Any] | None = None,
        authorize: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        with self.operation_lock:
            observed = self.choice_context
            if (
                not observed
                or observed["catalog_key"] != catalog_key
                or not ranking
                or len(set(ranking)) != len(ranking)
                or any(item not in observed["ids"] for item in ranking)
            ):
                reject("human_catalog_changed")
            intent = self.intent
            self._attach_runtime()
            self._append(
                "rank_requested",
                catalog_key=catalog_key,
                ranking=ranking,
                recording=self.preference is True,
                schema=RANK_SCHEMA,
            )
            self._set(
                "manual_offer:" + observed["context"]["runtime_run_id"],
                {
                    "recording": self.preference is True,
                    "catalog_key": catalog_key,
                    "ranking": ranking,
                },
            )
            value = self.models.command(
                "human_choice",
                expected_context=observed["context"],
                native_context=context,
                native_authorizer=authorize,
                human_selection={
                    "catalog_key": observed["runtime_catalog_key"],
                    "ranking": ranking,
                },
                intent_guard=lambda: self._require_intent(intent),
            )
            result = self._wait_model(value, intent)
            outcome = result.get("last_human_choice_result") or {}
            self.choice_context = None
            self.poll()
            if outcome.get("type") == "unknown":
                self.message = "结果未确认，没重发。先点“停，我来”处理原结果。"
            elif outcome.get("type") != "delivered":
                self.message = "局面变了或动作被拒，未重发，请重新排序。"
            else:
                self.message = "已执行第 1 个；其余未选项不作好坏评价。"
        return self.view()

    def command(
        self,
        action: str,
        body: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        authorize: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        shapes = {
            "record_start": set(),
            "record_stop": set(),
            "model_auto": {"selection_id"},
            "model_step": {"selection_id"},
            "model_stop": set(),
            "choices_open": set(),
            "rank": {"catalog_key", "ranking"},
            "mark": {"note", "step_id"},
            "mistake": {"step_id"},
            "reconnect": set(),
        }
        if action not in shapes or not isinstance(body, dict) or set(body) != shapes[action]:
            reject("invalid_desk_request")
        if action in {"model_auto", "model_step"}:
            if not isinstance(body["selection_id"], str):
                reject("invalid_desk_request")
            return self.model_control(
                body["selection_id"],
                "auto" if action == "model_auto" else "one_step",
                context,
                authorize,
            )
        if action == "rank":
            if (
                not isinstance(body["catalog_key"], str)
                or not isinstance(body["ranking"], list)
                or not 1 <= len(body["ranking"]) <= 4096
                or any(not isinstance(item, str) for item in body["ranking"])
            ):
                reject("invalid_desk_request")
            return self.rank(body["catalog_key"], body["ranking"], context, authorize)
        if action in {"mark", "mistake"}:
            if body["step_id"] is not None and not isinstance(body["step_id"], str):
                reject("invalid_desk_request")
            return self.mark(
                body.get("note", "我点错了"), body["step_id"], mistake=action == "mistake"
            )
        if action == "choices_open":
            return self.choices_open(context, authorize)
        return {
            "record_start": self.record_start,
            "record_stop": self.record_stop,
            "model_stop": self.model_stop,
            "reconnect": self.reconnect,
        }[action]()

    def models_list(self) -> list[dict[str, str]]:
        from .managed_model_target import managed_manifest

        result: dict[str, dict[str, str]] = {}
        for entry in self.models.registry()["policies"]:
            if entry["adapter"] not in GLOBAL_MODEL_ADAPTERS or entry["id"].startswith(
                "reference-binding-"
            ):
                continue
            manifest = json.loads(self.models.entry_path(entry, "manifest").read_bytes())
            if not managed_manifest(manifest):
                config = json.loads(self.models.entry_path(entry, "config").read_bytes())
                result[config.get("model_artifact_id", entry["id"])] = {
                    "id": entry["id"],
                    "label": entry["label"],
                }
        return list(result.values())

    def choices(self) -> dict[str, Any]:
        value = self.models.human_choices()
        catalog = value["catalog"]
        context = control_request_context(value["context"])
        catalog_key = key({"context": context, "catalog": value["catalog_key"]})
        self.choice_context = {
            "catalog_key": catalog_key,
            "runtime_catalog_key": value["catalog_key"],
            "context": context,
            "ids": [choice["choice_id"] for choice in catalog],
        }
        self.choice_view = {
            "catalog_key": catalog_key,
            "choices_ready": value["ready"],
            "choices_message": "当前全部合法动作；选几个，再执行第 1 个。"
            if value["ready"]
            else "先点“停，我来”"
            if value.get("reason") == "model_running"
            else "等局面稳定",
            "choices_model_id": value.get("model_choice_id"),
        }
        return {
            **self.view(),
            "choices": present_choices(
                catalog, value.get("observation"), value.get("model_choice_id")
            ),
        }

    def steps(self) -> list[dict[str, Any]]:
        with self.lock:
            return self._steps()

    def _steps(self) -> list[dict[str, Any]]:
        rows = self.events(self._step_sequence)
        steps = self._step_rows
        latest_runtime = self._step_latest_runtime
        inputs = self._step_inputs
        manual = self._step_manual
        for row in rows:
            if row["kind"] == "native_event":
                original = row["original"]
                identity = (
                    "native:" + row["session_id"] + ":" + original.get("decision_id", "issue")
                )
                if original["kind"] == "decision_accepted":
                    source = self.sources["native"].get(row["session_id"], {})
                    bound = original.get("bound_action") or original.get("native_input") or {}
                    human = (
                        source.get("declared_origin") == "declared_human"
                        and original.get("native_witness") is not None
                        and original.get("basis") is not None
                        and original.get("missing_reason") is None
                    )
                    steps[identity] = {
                        "step_id": identity,
                        "sequence": row["sequence"],
                        "actor": "human" if human else "unknown",
                        "runtime_instance_id": source.get("runtime_instance_id"),
                        "input_method": "mouse",
                        "actor_basis": "declared_origin_and_native_witness"
                        if human
                        else "unproved",
                        "human_origin_verified": False,
                        "ranking": [bound],
                        "ranking_binding": "original_native_input",
                        "action": bound,
                        "label": bound.get("label")
                        or original.get("native_action_type")
                        or "游戏内点击",
                        "receipt": original.get("native_receipt"),
                        "outcome": "pending",
                        "recording": True,
                        "saved_at": original["recorded_at"],
                        "source": {
                            "session_id": row["session_id"],
                            "decision_id": original["decision_id"],
                            "event_sequence": original["sequence"],
                        },
                        "demonstration_use": "ordinary_native_demonstration_checks"
                        if human
                        else "unknown_not_training",
                        "native_witness_present": original.get("native_witness") is not None
                        and original.get("basis") is not None
                        and original.get("missing_reason") is None,
                    }
                elif original["kind"] == "lifecycle" and identity in steps:
                    steps[identity].update(outcome=original["phase"], native_terminal=original)
            elif row["kind"] == "runtime_event":
                original, run = row["original"], row["run_id"]
                payload, kind = original["payload"], original["kind"]
                acquisition = payload.get("acquisition_id")
                if kind == "global_input":
                    inputs[(run, acquisition)] = payload
                elif kind == "human_directive":
                    manual[(run, payload["output"]["directive"]["basis_acquisition_id"])] = {
                        **payload,
                        "recording": row["recording"],
                    }
                elif kind == "global_choice":
                    identity = "runtime:" + run + ":" + acquisition
                    selected = manual.get((run, acquisition))
                    source = self.sources["runtime"].get(run, {})
                    steps[identity] = {
                        "step_id": identity,
                        "sequence": row["sequence"],
                        "actor": "human" if selected else "model",
                        "runtime_instance_id": source.get("runtime_instance_id"),
                        "input_method": "ranking" if selected else "model",
                        "actor_basis": "manual_protocol_choice_not_human_witness"
                        if selected
                        else "exact_registered_model",
                        "human_origin_verified": False,
                        "ranking": selected["ranking"] if selected else [],
                        "ranking_binding": RANK_SCHEMA if selected else None,
                        "action": payload["choice_id"],
                        "label": payload["choice_id"],
                        "receipt": [],
                        "outcome": "pending",
                        "recording": selected["recording"] if selected else row["recording"],
                        "saved_at": original["recorded_at"],
                        "model": None if selected else source.get("composition"),
                        "source": {
                            "run_id": run,
                            "acquisition_id": acquisition,
                            "event_sequence": original["sequence"],
                        },
                        "demonstration_use": "ranking_labels_separate_not_automatic_training"
                        if selected
                        else "model_trajectory_only",
                    }
                    latest_runtime[run] = identity
                    offered = inputs.get((run, acquisition))
                    if offered:
                        try:
                            content = read_artifact(
                                self.models.directory / "agent-runs" / run, offered["artifact"]
                            )
                            choice = next(
                                (
                                    c
                                    for c in content["catalog"]
                                    if c["choice_id"] == payload["choice_id"]
                                ),
                                None,
                            )
                            if choice:
                                steps[identity]["label"] = choice["label"]
                        except (BoundaryError, OSError, ValueError, KeyError):
                            pass  # Identity stays explicit; missing presentation is not fabricated.
                elif kind == "global_receipt" and run in latest_runtime:
                    steps[latest_runtime[run]]["receipt"].append(payload["receipt"])
                elif kind == "global_result":
                    identity = "runtime:" + run + ":" + acquisition
                    if identity in steps:
                        steps[identity]["outcome"] = payload["result"]["status"]
                        steps[identity]["result"] = payload["result"]
                elif (
                    kind == "global_dispatch_error"
                    and payload.get("sent")
                    and run in latest_runtime
                ):
                    steps[latest_runtime[run]]["outcome"] = "unknown"
        annotations = self._step_annotations
        for row in rows:
            if row["kind"] == "annotation" and row.get("step_id"):
                annotations.setdefault(row["step_id"], []).append(
                    {k: row[k] for k in ("event_id", "recorded_at", "annotation", "note")}
                )
        if rows:
            self._step_sequence = rows[-1]["sequence"]
        values = sorted(steps.values(), key=lambda step: step["sequence"])
        for ordinal, step in enumerate(values, 1):
            step.update(
                ordinal=ordinal,
                annotations=annotations.get(step["step_id"], []),
                score_eligible=False,
            )
        return values

    def mark(
        self, note: str, step_id: str | None = None, *, mistake: bool = False
    ) -> dict[str, Any]:
        if not isinstance(note, str) or len(note) > 2000 or not mistake and not note.strip():
            reject("invalid_problem_note")
        self.poll()
        steps = self.steps()
        if step_id is None:
            steps = self._current_steps(steps)
        targets = [
            step
            for step in steps
            if (step["actor"] == "human" and step["recording"]) or not mistake
        ]
        target = (
            next((step for step in targets if step["step_id"] == step_id), None)
            if step_id
            else next(iter(reversed(targets)), None)
        )
        if mistake and target is None:
            reject("no_recorded_human_action")
        if step_id and target is None:
            reject("annotation_step_not_found")
        self._append(
            "annotation",
            step_id=target["step_id"] if target else None,
            annotation="mistake" if mistake else "problem",
            note=note.strip(),
            automatic_negative_example=False,
        )
        self.message = f"已标在第 {target['ordinal']} 步。" if target else "已记录当前问题。"
        return self.view()

    def _current_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        instance = (self.feedback or {}).get("runtime_instance_id")
        if instance is None:
            instance = ((self.runtime or {}).get("environment") or {}).get("runtime_instance_id")
        return [step for step in steps if instance and step.get("runtime_instance_id") == instance]

    def view(self) -> dict[str, Any]:
        feedback, runtime = self.feedback or {}, self.runtime or {}
        steps = self._current_steps(self.steps())
        recorded = [step for step in steps if step["recording"] or step["actor"] == "model"]
        humans = [step for step in recorded if step["actor"] == "human" and step["recording"]]
        model = runtime.get("mode") in {"auto", "one_step", "shadow"}
        healthy = (
            self.feedback_error is None
            and feedback.get("health", {}).get("append") == "healthy"
            and feedback.get("health", {}).get("disk") == "healthy"
            and feedback.get("health", {}).get("error") is None
        )
        saving = self.preference is True and (
            bool(model)
            and not runtime.get("tainted")
            or feedback.get("lifecycle") == "recording"
            and healthy
        )
        if self.feedback_error:
            message = self.feedback_error
        elif self.native_error:
            message = "没在保存：" + self.native_error
        elif self.preference is not True:
            message = "未录制：你的操作不保存为示范；模型轨迹始终保存。"
        elif model:
            message = "录制中 · 模型轨迹按原始回执保存"
        elif not saving:
            message = "录制待命，当前没在保存" if feedback.get("armed") else "没在保存，请查看原因"
        else:
            message = "● 录制中 · 已写入本机，封存后核验完整性"
        with self.models.lock:
            session = dict(self.models.state)
        selection = session.get("selection_id")
        labels = {entry["id"]: entry["label"] for entry in self.models_list()}
        problem = self.feedback_error or (
            "录制保存异常：" + self.native_error if self.native_error else None
        )
        runtime_problem = bool(
            runtime.get("tainted")
            or session.get("status")
            in {
                "command_unknown",
                "recovery_required",
            }
            or session.get("loaded")
            and not runtime
        )
        if runtime_problem:
            problem = "原执行结果未确认；不会重发。"
        return {
            "schema": SCHEMA,
            "recording": {
                "lifecycle": "standby"
                if self.preference is True
                and feedback.get("armed")
                and feedback.get("lifecycle") in {"ready", "closed"}
                else feedback.get("lifecycle", "unavailable"),
                "requested": self.preference is True,
                "saving": bool(saving)
                and self.feedback_error is None
                and self.native_error is None,
                "count": len(recorded),
                "native_count": feedback.get("count"),
                "last_saved_at": recorded[-1]["saved_at"] if recorded else None,
                "message": message,
                "recent": [
                    {
                        k: step[k]
                        for k in (
                            "step_id",
                            "label",
                            "input_method",
                            "actor",
                            "saved_at",
                            "ordinal",
                        )
                    }
                    for step in recorded[-8:]
                ],
            },
            "model": {
                "selection_id": selection if selection in labels else None,
                "label": labels.get(selection or "", "未接模型"),
                "mode": runtime.get("mode", "unknown" if session.get("loaded") else "human"),
                "loaded": bool(session.get("loaded")),
                "busy": self.transition is not None
                or (session.get("operation") or {}).get("status") == "pending",
                "step_count": sum(step["actor"] == "model" for step in steps),
                "last_action": steps[-1]["label"] if steps else None,
            },
            "message": self.message,
            "recovery": {
                "label": "停，我来"
                if runtime_problem
                else "停录制并保存"
                if self.native_error
                else "重新连接",
                "action": "desk.model_stop"
                if runtime_problem
                else "desk.record_stop"
                if self.native_error
                else "desk.reconnect",
            }
            if problem
            else None,
            "error": problem,
            "last_human_step_id": humans[-1]["step_id"] if humans else None,
            **self.choice_view,
            "choices_ready": bool(
                self.choice_view["choices_ready"]
                and runtime.get("mode") == "human"
                and not runtime_problem
            ),
        }

    def reconnect(self) -> dict[str, Any]:
        # Reads only. Unknown commands are not retried or declared successful.
        self.poll()
        self.message = "已重新读取状态；结果未知的原请求未重发。"
        return self.view()

    def publish_index(self) -> dict[str, Any]:
        from spireagent.artifact_contracts import Manifest
        from spireagent.source import source_identity

        from .local_recording_import import _selected_store, _sync

        steps = self.steps()
        index = {
            "schema": INDEX_SCHEMA,
            "steps": steps,
            "events": self.events(),
            "sources": self.sources,
            "score_eligible": False,
            "ordering": [
                "original_lane_sequences",
                "observed_control_boundaries",
                "no_wall_clock_interpolation",
            ],
            "data_policy": {
                "human_native": "ordinary_verified_demonstration",
                "model": "model_trajectory",
                "ranking": RANK_SCHEMA,
                "unranked": "not_evaluated",
                "problem": "not_automatic_negative",
            },
        }
        labels = {
            "schema": RANK_SCHEMA,
            "labels": [
                {
                    "step_id": step["step_id"],
                    "ranking": step["ranking"],
                    "source": step["source"],
                    "recording": step["recording"],
                    "unranked": "not_evaluated",
                }
                for step in steps
                if step["input_method"] == "ranking" and step["recording"]
            ],
        }
        store, registry = _selected_store(self.app.config, require_curation=False)
        artifact = Manifest(
            "evidence",
            source_identity(ROOT),
            payloads=(
                store.put_bytes("index", json_bytes(index)),
                store.put_bytes("ranking-labels", json_bytes(labels)),
            ),
            parameters=FrozenObject.of(
                {
                    "schema": INDEX_SCHEMA,
                    "score_eligible": False,
                    "ranking_schema": RANK_SCHEMA,
                    "training_admission": "ordinary_source_checks_and_separate_ranking_labels",
                }
            ),
        )
        identity = store.publish(artifact)
        for payload in artifact.payloads:
            for _ in store.read_payload(payload):
                pass
        _sync(store, registry)
        self._set("latest_artifact_id", identity)
        events = index["events"]
        self._set("last_export_sequence", events[-1]["sequence"] if events else 0)
        return {"artifact_id": identity, "readback": "pass"}

    def read_saved(self, artifact_id: str) -> dict[str, Any]:
        from .local_recording_import import _selected_store

        store, _ = _selected_store(self.app.config, require_curation=False)
        return read_game_desk_recording(store, artifact_id)

    def finalize_after_game_exit(self) -> None:
        """Called only after Q's original native owner proves game_running=false."""
        with self.models.lock:
            client = self.models.client
            active = client is not None and client.startup["run_id"] in self.sources["runtime"]
        if active:
            self._wait_model(self.models.command("stop"), self.intent)
        with self.lock:
            for session, source in self.sources["native"].items():
                self._read_stream(
                    session, Path(source["directory"]) / "p1-native-events.jsonl", native=True
                )
            for run in self.sources["runtime"]:
                self._read_stream(
                    run, self.models.directory / "agent-runs" / run / "events.jsonl", native=False
                )
        events = self.events()
        if events and events[-1]["sequence"] != self._get("last_export_sequence", 0):
            self.publish_index()
