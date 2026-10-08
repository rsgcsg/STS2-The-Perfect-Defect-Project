"""Bounded native presentation and fixed actions over existing Application owners."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode

from spireagent.json_boundary import BoundaryError, digest
from spireagent.workbench.native_workbench_access import NativePair, fail

if TYPE_CHECKING:
    from spireagent.workbench.developer_server import Application

PREFIX = "/api/native-workbench/v1"
VIEW_SCHEMA = "spireagent/native-workbench-view-v1"
COMMAND_SCHEMA = "spireagent/native-workbench-command-v1"
RESULT_SCHEMA = "spireagent/native-workbench-command-result-v1"
MAX_RESPONSE_BYTES = 262144
MAX_COMMAND_BYTES = 32768
PAGES = frozenset({"play", "data", "training", "models", "settings"})
ACTIONS = frozenset(
    {
        "workspace.create",
        "curation.prepare",
        "recordings.refresh",
        "recordings.import",
        "datasets.preview",
        "datasets.human-preview",
        "datasets.publish",
        "training.start",
        "training.pause",
        "training.cancel",
        "training.reconcile",
        "training.resume",
        "evaluation.start",
        "models.export",
        "models.register",
        "models.download",
        "models.load",
        "models.takeover",
        "models.human",
        "models.stop",
        "models.reconcile",
        "identity.login",
        "identity.poll",
        "identity.logout",
        "collection.consent",
        "collection.prepare",
        "collection.upload",
        "downloads.start",
    }
)
_PRECONDITION_CODES = frozenset(
    {
        "invalid_training_request",
        "unsupported_training_recipe",
        "unsupported_resource_limits",
        "unsupported_placement",
        "invalid_wall_seconds",
        "invalid_scratch_bytes",
        "invalid_recipe_config",
        "fixed_recipe_config_required",
        "invalid_checkpoint_cadence",
        "recipe_limits_not_supported",
        "wall_limit_required",
        "stale_operation_attempt",
        "cumulative_limits_must_be_preserved",
        "cumulative_budget_exhausted",
        "previous_training_outcome_unknown",
        "prior_writer_terminal_required",
        "writer_still_running",
        "writer_reconciliation_required",
        "operation_in_progress",
        "model_not_loaded",
        "runtime_already_running",
        "unknown_local_model",
        "new_experiment_precondition_failed",
        "native_model_intent_superseded",
        "native_pending_request_required",
        "native_pending_request_changed",
    }
)
_PRIVATE_KEYS = frozenset(
    {
        "csrf_token",
        "cookie",
        "secret",
        "client_secret",
        "session_token",
        "device_token",
        "control_token",
        "token",
        "authorization",
        "headers",
        "payloads",
        "workspace",
        "curation_owner",
        "binding_root",
    }
)


def public(value: Any, depth: int = 0) -> Any:
    """Explicit native projection strips credentials/paths and bounds display data."""
    if depth > 16:
        raise fail("native_projection_limit")
    if isinstance(value, dict):
        return {
            key: public(item, depth + 1)
            for key, item in value.items()
            if isinstance(key, str)
            and key not in _PRIVATE_KEYS
            and not key.startswith("_")
            and not key.endswith(("_path", "_dir", "directory"))
        }
    if isinstance(value, (list, tuple)):
        if len(value) > 512:
            raise fail("native_projection_limit")
        return [public(item, depth + 1) for item in value]
    if isinstance(value, str):
        if value.startswith("/"):
            return "[private path]"
        if len(value.encode("utf-8")) > 4096:
            raise fail("native_projection_limit")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise fail("native_projection_type")


def bounded_response(value: dict[str, Any]) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise fail("native_response_limit")
    return raw


def exact(body: dict[str, Any], fields: set[str]) -> None:
    if set(body) != fields:
        raise fail("invalid_native_payload")


def field(
    name: str,
    label: str,
    kind: str = "string",
    default: Any = "",
    options: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "type": kind,
        "default": default,
        "options": options or [],
    }


def action(
    action_id: str,
    label: str,
    fields: list[dict[str, Any]] | None = None,
    *,
    enabled: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "label": label,
        "fields": fields or [],
        "enabled": enabled,
        "reason": reason,
    }


class NativeWorkbenchApi:
    def __init__(self, app: Application) -> None:
        self.app = app
        self._recordings: dict[str, Any] | None = None

    @staticmethod
    def card(owner: str, load: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            data = public(load())
            return {
                "owner": owner,
                "schema": data.get("schema"),
                "observed_at": time.time(),
                "data": data,
            }
        except (BoundaryError, OSError, ValueError, TypeError, KeyError) as error:
            return {
                "owner": owner,
                "schema": None,
                "observed_at": time.time(),
                "data": {
                    "availability": "unavailable",
                    "reason": error.code
                    if isinstance(error, BoundaryError)
                    else "owner_unavailable",
                },
            }

    def view(self, query: str, pair: NativePair) -> dict[str, Any]:
        parsed = parse_qs(query, strict_parsing=True, keep_blank_values=True, max_num_fields=4)
        if set(parsed) - {"page", "limit", "offset", "id"} or any(
            len(values) != 1 for values in parsed.values()
        ):
            raise fail("invalid_native_query")
        page = parsed.get("page", ["play"])[0]
        limit = int(parsed.get("limit", ["25"])[0])
        offset = int(parsed.get("offset", ["0"])[0])
        context_id = parsed.get("id", [None])[0]
        if page not in PAGES or not 1 <= limit <= 50 or not 0 <= offset <= 10000:
            raise fail("invalid_native_query")
        if context_id is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", context_id):
            raise fail("invalid_native_context")
        cards, items, controls = [], [], []
        total = 0
        app = self.app
        training = app.local_training.capabilities()
        if page == "play":
            catalog = self.card("models", app.models.catalog)
            cards.append(catalog)
            items = catalog["data"].get("policies", [])
            total, items = len(items), items[offset : offset + limit]
            session = catalog["data"].get("session", {})
            loading = (session.get("operation") or {}).get("status") == "pending"
            can_load = (
                catalog.get("schema") is not None
                and not session.get("loaded")
                and not loading
                and session.get("status") not in {"command_unknown", "recovery_required"}
            )
            runtime = session.get("runtime")
            pending = runtime.get("pending_request") if isinstance(runtime, dict) else None
            request_id = pending.get("request_id") if isinstance(pending, dict) else None
            can_reconcile = (
                session.get("loaded") is True
                and isinstance(runtime, dict)
                and isinstance(pending, dict)
                and runtime.get("schema") == "sts2.policy-runtime/agent-session-status-1"
                and isinstance(request_id, str)
                and bool(request_id)
                and pending.get("run_id") == runtime.get("run_id")
            )

            controls.extend(
                [
                    action(
                        "models.load",
                        "加载模型（Human 模式）",
                        [
                            field("selection_id", "已注册模型", "selection", context_id or ""),
                            field(
                                "run_profile",
                                "运行范围",
                                "enum",
                                "short",
                                [
                                    {"value": "short", "label": "短时检查"},
                                    {"value": "extended", "label": "较长有界尝试"},
                                ],
                            ),
                        ],
                        enabled=can_load,
                        reason=None if can_load else "owner_load_not_available",
                    ),
                    action(
                        "models.takeover",
                        "加载并接管",
                        [
                            field("selection_id", "已注册模型", "selection", context_id or ""),
                            field(
                                "run_profile",
                                "运行范围",
                                "enum",
                                "short",
                                [
                                    {"value": "short", "label": "短时检查"},
                                    {"value": "extended", "label": "较长有界尝试"},
                                ],
                            ),
                        ],
                        enabled=can_load,
                        reason=None if can_load else "owner_load_not_available",
                    ),
                    action("models.human", "暂停并交还 Human"),
                    action("models.stop", "结束模型运行"),
                    action(
                        "models.reconcile",
                        "明确查询原始待定请求",
                        [
                            field(
                                "request_id",
                                "原始 Runtime 请求",
                                "enum",
                                request_id or "",
                                [{"value": request_id, "label": request_id}]
                                if can_reconcile
                                else [],
                            ),
                        ],
                        enabled=can_reconcile,
                        reason=None if can_reconcile else "native_pending_request_required",
                    ),
                ]
            )
            if context_id:
                cards.append(self.card("readiness", lambda: app.models.readiness(context_id)))
        elif page in {"data", "training", "models"}:
            workspace = app.local_research_workspace()
            if workspace is not None:
                inventory = workspace.inventory(
                    category="models" if page == "models" else "all", limit=limit, offset=offset
                )
                items = public(inventory["items"])
                total = inventory["total"]
                if context_id and re.fullmatch(r"[a-f0-9]{64}", context_id):
                    cards.append(self.card("artifact", lambda: workspace.artifact(context_id)))
            else:
                cards.append(self.card("workspace", app.managed_local_workspace))
                controls.append(action("workspace.create", "建立本机资料空间"))
            if page == "data":
                cards.extend(
                    [
                        self.card("curation", app.local_curation_preparation.status),
                        self.card("import", app.local_recording_import.status),
                        self.card("datasets", app.local_datasets.status),
                    ]
                )
                dataset_operation = cards[-1]["data"].get("operation", {})
                preview_id = dataset_operation.get("preview_id", "")
                if self._recordings is not None:
                    listing = {
                        **self._recordings,
                        "candidates": self._recordings.get("candidates", [])[
                            offset : offset + limit
                        ],
                    }
                    cards.append(
                        {
                            "owner": "recordings",
                            "schema": listing.get("schema"),
                            "observed_at": listing.get("observed_at"),
                            "data": listing,
                        }
                    )
                controls.extend(
                    [
                        action("recordings.refresh", "刷新已结束的录制"),
                        action(
                            "recordings.import",
                            "核对声明并导入 Human 录制",
                            [
                                field("candidate_id", "已结束录制编号"),
                                field(
                                    "human_origin_attested",
                                    "我声明此来源实际由 Human 操作",
                                    "boolean",
                                    False,
                                ),
                            ],
                        ),
                        action(
                            "datasets.preview",
                            "检查数据用途与预览",
                            [
                                field("artifact_id", "来源对象", "artifact", context_id or ""),
                                field(
                                    "purpose",
                                    "用途",
                                    "enum",
                                    "training",
                                    [
                                        {"value": "training", "label": "训练"},
                                        {"value": "test", "label": "测试"},
                                        {"value": "gold", "label": "Gold"},
                                    ],
                                ),
                                field(
                                    "paired_training", "配对训练集（可留空）", "optional-artifact"
                                ),
                            ],
                        ),
                        action(
                            "datasets.human-preview",
                            "准备 Human 操作标签输入",
                            [
                                field(
                                    "artifact_ids",
                                    "已验证 Human 来源编号（可在列表多选）",
                                    "artifact-list",
                                    context_id or "",
                                )
                            ],
                        ),
                        action(
                            "datasets.publish",
                            "发布已核对预览",
                            [field("preview_id", "预览编号", default=preview_id)],
                            enabled=bool(preview_id),
                            reason=None if preview_id else "verified_preview_required",
                        ),
                        action("curation.prepare", "准备既有资料的用途索引"),
                    ]
                )
            if page == "training":
                cards.append(self.card("training", app.local_training.status))
                operation = cards[-1]["data"].get("operation", {})
                can_start = cards[-1]["data"].get("availability") == "ready" and (
                    operation.get("status") in {"idle", "completed"}
                    or (operation.get("status") == "failed" and not operation.get("run_id"))
                )
                controls.append(
                    action(
                        "training.start",
                        "明确开始训练",
                        [field("source_id", "训练来源", "artifact", context_id or "")],
                        enabled=can_start,
                        reason=None if can_start else "owner_start_not_available",
                    )
                )
                operation = cards[-1]["data"].get("operation", {})
                for mode, label in [
                    ("pause", "请求暂停"),
                    ("cancel", "请求取消"),
                    ("reconcile", "核对原 attempt"),
                    ("resume", "明确恢复 checkpoint"),
                ]:
                    supported = mode in operation.get("supported_actions", [])
                    if operation.get("status") == "pending" and (
                        operation.get("requested_action") == mode
                        or operation.get("requested_action") == "cancel"
                    ):
                        supported = False
                    if mode == "resume":
                        supported = bool(
                            supported
                            and operation.get("worker_state") == "terminal"
                            and operation.get("status") != "interrupted_unknown"
                            and operation.get("checkpoint_id")
                            and operation.get("limits")
                        )
                    controls.append(
                        action(
                            "training." + mode,
                            label,
                            enabled=supported,
                            reason=None if supported else "owner_action_not_available",
                        )
                    )
            if page == "models":
                cards.append(
                    self.card(
                        "team_exports",
                        lambda: app.members.request(
                            "exports?" + urlencode({"limit": limit, "offset": offset})
                        ),
                    )
                )
                cards.extend(
                    [
                        self.card("export", app.local_model_export.status),
                        self.card("evaluation", app.local_memory_evaluation.status),
                    ]
                )
                controls.extend(
                    [
                        action(
                            "models.export",
                            "导出模型",
                            [field("model_id", "模型产物", "artifact", context_id or "")],
                        ),
                        action(
                            "models.register",
                            "登记到本机模型目录",
                            [field("model_id", "模型产物", "artifact", context_id or "")],
                        ),
                        action(
                            "evaluation.start",
                            "开始独立开发集评估",
                            [
                                field("model_id", "模型产物", "artifact", context_id or ""),
                                field("source_id", "另选开发来源", "artifact"),
                            ],
                        ),
                        action(
                            "models.download",
                            "下载授权模型",
                            [field("artifact_id", "模型产物", "artifact")],
                        ),
                        action(
                            "downloads.start",
                            "下载授权团队导出",
                            [field("export_id", "导出产物", "artifact")],
                        ),
                    ]
                )
                if context_id and re.fullmatch(r"[a-f0-9]{64}", context_id):
                    cards.append(
                        self.card(
                            "registration", lambda: app.local_model_registration.status(context_id)
                        )
                    )
        else:
            identity = self.card("identity", app.account.status)
            cards.extend(
                [
                    identity,
                    self.card("collection", app.collection_flow.status),
                    {
                        "owner": "configuration",
                        "schema": "spireagent/native-settings-v1",
                        "observed_at": time.time(),
                        "data": {
                            "team_configured": bool(app.config.hub_url),
                            "trusted_hub_url": app.config.hub_url,
                            "platform_local": app.config.platform_url
                            in {"http://127.0.0.1:15526", "http://localhost:15526"},
                            "pairing_scope": "local_application_only",
                        },
                    },
                ]
            )
            controls.extend(
                [
                    action(
                        "identity.login",
                        "连接团队账号",
                        [field("device_name", "本机名称", default="我的电脑")],
                    ),
                    action("identity.poll", "检查账号连接结果"),
                    action("identity.logout", "退出团队账号"),
                    action(
                        "collection.consent",
                        "接受已显示的采集声明",
                        [
                            field("template_id", "采集模板", "artifact"),
                            field("accepted", "我接受当前显示的采集条款", "boolean", False),
                        ],
                    ),
                    action("collection.prepare", "准备已授权采集配置"),
                    action(
                        "collection.upload",
                        "设置独立上传状态",
                        [field("enabled", "启用上传", "boolean", False)],
                    ),
                ]
            )
        external = {
            "view": "local-models"
            if page == "play"
            else "local-workspace"
            if page in {"data", "training", "models"}
            else "devices"
        }
        if (
            context_id
            and re.fullmatch(r"[a-f0-9]{64}", context_id)
            and external["view"] == "local-workspace"
        ):
            external["id"] = context_id
        return {
            "schema": VIEW_SCHEMA,
            "binding": pair.to_dict(),
            "page": page,
            "observed_at": time.time(),
            "availability": "ready",
            "reason": None,
            "capabilities": {
                "actions": controls,
                "training": public(training),
                "source_aware_prepare": {
                    "enabled": False,
                    "reason": "source_owner_application_adapter_required",
                },
                "remote_execution": {
                    "enabled": False,
                    "reason": "compatible_remote_owner_required",
                },
            },
            "cards": cards,
            "items": items,
            "pagination": {"limit": limit, "offset": offset, "total": total},
            "context": {
                "id": context_id,
                "external": external,
                "external_url": pair.workbench_url + "?" + urlencode(external),
            },
        }

    def command(self, action_id: str, body: dict[str, Any], pair: NativePair) -> dict[str, Any]:
        exact(body, {"schema", "request_id", "payload"})
        if body["schema"] != COMMAND_SCHEMA or not isinstance(body["payload"], dict):
            raise fail("invalid_native_command")
        request_id = digest(body["request_id"], "native_workbench.request", length=32)
        if action_id not in ACTIONS:
            raise fail("native_action_not_supported")
        payload = body["payload"]
        app = self.app
        try:
            value = self._dispatch(action_id, payload, pair=pair, request_id=request_id)
            response = {
                "schema": RESULT_SCHEMA,
                "binding": pair.to_dict(),
                "request_id": request_id,
                "status": "accepted",
                "owner_response": public(value),
                "error": None,
            }
        except (BoundaryError, OSError, ValueError, TypeError, KeyError) as error:
            code = error.code if isinstance(error, BoundaryError) else "owner_command_unconfirmed"
            precondition = code in _PRECONDITION_CODES or (
                isinstance(error, BoundaryError)
                and error.stage == "native_workbench"
                and code == "invalid_native_payload"
            )
            response = {
                "schema": RESULT_SCHEMA,
                "binding": pair.to_dict(),
                "request_id": request_id,
                "status": "rejected" if precondition else "unconfirmed",
                "owner_response": None,
                "error": {"code": code, "automatic_retry": False},
            }
        # Keep approved login URLs scoped to the configured identity owner.
        if action_id == "identity.login" and response["status"] == "accepted":
            approval = (response["owner_response"] or {}).get("approval_url")
            expected = app.config.hub_url + "/app/"
            if not isinstance(approval, str) or not approval.startswith(expected):
                raise fail("native_login_target_invalid")
        return response

    def _dispatch(
        self, action_id: str, body: dict[str, Any], *, pair: NativePair, request_id: str
    ) -> dict[str, Any]:
        app = self.app
        if action_id == "workspace.create":
            exact(body, set())
            return app.create_managed_local_workspace()
        if action_id == "curation.prepare":
            exact(body, set())
            return app.prepare_local_curation()
        if action_id == "recordings.refresh":
            exact(body, set())
            observed = app.local_recordings.read()
            self._recordings = public(observed)
            return {**observed, "candidates": observed.get("candidates", [])[:50]}
        if action_id == "recordings.import":
            exact(body, {"candidate_id", "human_origin_attested"})
            return app.start_local_recording_import(**body)
        if action_id == "datasets.preview":
            exact(body, {"artifact_id", "purpose", "paired_training"})
            return app.start_local_dataset_preview(**body)
        if action_id == "datasets.human-preview":
            exact(body, {"artifact_ids"})
            return app.start_local_human_dataset_preview(body["artifact_ids"])
        if action_id == "datasets.publish":
            exact(body, {"preview_id"})
            return app.start_local_dataset_publish(body["preview_id"])
        if action_id == "training.start":
            from spireagent.workbench.recipe_contracts import TrainingRequest

            return app.start_local_training(TrainingRequest.from_dict(body))
        if action_id.startswith("training."):
            fields = {"operation_id", "expected_attempt_id"}
            if action_id == "training.resume":
                fields |= {"checkpoint_id", "intent_id", "limits"}
            exact(body, fields)
            return app.control_local_training(action_id.split(".")[1], body)
        if action_id == "evaluation.start":
            if set(body) not in (
                {"model_id", "source_id"},
                {"model_id", "source_id", "max_settling_events"},
            ):
                raise fail("invalid_native_payload")
            return app.start_local_memory_evaluation(**body)
        if action_id == "models.export":
            exact(body, {"model_id"})
            return app.start_local_model_export(body["model_id"])
        if action_id == "models.register":
            exact(body, {"model_id"})
            return app.register_local_model(body["model_id"])
        if action_id == "models.download":
            exact(body, {"artifact_id"})
            return app.models.prepare(body["artifact_id"])
        if action_id in {"models.load", "models.takeover"}:
            exact(body, {"selection_id", "run_profile"})
            context = {"request_id": request_id, "binding": pair.to_dict()}

            def authorize() -> None:
                app.native_access.authorize_intent(context)

            if action_id == "models.load":
                return app.models.prepare_and_load(
                    body["selection_id"],
                    body["run_profile"],
                    native_context=context,
                    native_authorizer=authorize,
                )
            return app.models.prepare_and_takeover(
                body["selection_id"],
                body["run_profile"],
                native_context=context,
                native_authorizer=authorize,
            )
        if action_id in {"models.human", "models.stop"}:
            mode = "human" if action_id == "models.human" else "stop"
            if set(body) == {"native_request_id"}:
                return app.models.recover_native_intent(body["native_request_id"], mode)
            exact(body, set())
            return app.models.command(mode)
        if action_id == "models.reconcile":
            exact(body, {"request_id"})
            return app.models.command("reconcile", request_id=body["request_id"])
        if action_id == "identity.login":
            exact(body, {"device_name"})
            return app.account.begin(body["device_name"])
        if action_id == "identity.poll":
            exact(body, set())
            return app.account.poll()
        if action_id == "identity.logout":
            exact(body, set())
            app.members.close()
            value = app.account.logout()
            with app.console.cloud_cache.lock:
                app.console.cloud_cache.values.clear()
            return value
        if action_id == "collection.consent":
            exact(body, {"template_id", "accepted"})
            return app.collection_flow.consent(body)
        if action_id == "collection.prepare":
            exact(body, set())
            return app.collection_flow.prepare(body)
        if action_id == "collection.upload":
            exact(body, {"enabled"})
            return app.collection_flow.set_upload(body)
        if action_id == "downloads.start":
            exact(body, {"export_id"})
            return app.members.download(body["export_id"])
        raise fail("native_action_not_supported")
