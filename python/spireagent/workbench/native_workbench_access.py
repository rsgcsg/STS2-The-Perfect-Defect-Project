"""Scoped native access under the existing selected launcher's private authority.

Pair state is ephemeral authentication state, never an operation/job ledger.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from spireagent.json_boundary import BoundaryError, digest

BOOTSTRAP_SCHEMA = "spireagent/native-workbench-bootstrap-v1"
PAIR_SCHEMA = "sts2.platform/native-workbench-pair-1"
ACK_SCHEMA = "sts2.platform/native-workbench-pair-ack-1"
CURRENT_SCHEMA = "sts2.platform/native-workbench-current-1"
BINDING_FIELDS = (
    "runtime_instance_id",
    "workbench_instance_id",
    "configuration_id",
    "workbench_url",
    "pair_id",
    "expires_at",
)
ROLES = frozenset(
    {"native-register-v1", "native-register-ack-v1", "native-access-v1", "native-current-v1"}
)
MAX_PRIVATE_BYTES = 4096
MAX_PAIR_SECONDS = 600


def fail(code: str) -> BoundaryError:
    return BoundaryError("native_workbench", code)


def bounded_string(value: object, maximum: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise fail("invalid_native_string")
    return value


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def private_bytes(path: Path) -> bytes:
    if not path.is_absolute() or ".." in path.parts:
        raise fail("private_native_path_required")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise fail("private_native_path_required")
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size > MAX_PRIVATE_BYTES
        or (os.name != "nt" and (before.st_mode & 0o077 or before.st_uid != os.getuid()))
    ):
        raise fail("private_native_file_required")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_size > MAX_PRIVATE_BYTES
            or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise fail("private_native_file_changed")
        raw = os.read(descriptor, MAX_PRIVATE_BYTES + 1)
        if len(raw) > MAX_PRIVATE_BYTES:
            raise fail("private_native_file_required")
        return raw
    finally:
        os.close(descriptor)


def selected_directory() -> Path:
    if sys.platform != "darwin":
        raise fail("native_access_platform_unsupported")
    return Path.home() / "Library/Application Support/spireagent/workbench"


@dataclass(frozen=True)
class NativeBootstrap:
    config_path: str
    launcher_sha256: str
    game_mod_sha256: str
    secret: str = field(repr=False)

    @classmethod
    def read(cls, directory: Path, config_path: Path | None = None) -> NativeBootstrap:
        value = json.loads(
            private_bytes(directory / "native-access.json"), object_pairs_hook=unique_object
        )
        if (
            not isinstance(value, dict)
            or set(value)
            != {"schema", "enabled", "config_path", "launcher_sha256", "game_mod_sha256", "secret"}
            or value["schema"] != BOOTSTRAP_SCHEMA
            or value["enabled"] is not True
        ):
            raise fail("native_access_not_configured")
        path = Path(bounded_string(value["config_path"]))
        if not path.is_absolute() or (config_path is not None and path != config_path):
            raise fail("native_configuration_mismatch")
        private_bytes(path)  # Validates every ancestor, not only the leaf.
        launcher_raw = private_bytes(directory / "launcher.json")
        launcher = json.loads(launcher_raw, object_pairs_hook=unique_object)
        if (
            not isinstance(launcher, dict)
            or set(launcher)
            != {
                "schema",
                "release_directory",
                "kit_sha256",
                "source_revision",
                "workbench_sha256",
                "uv_lock_sha256",
                "config_path",
            }
            or launcher.get("schema") != "spireagent/workbench-launcher-v1"
            or launcher.get("config_path") != str(path)
        ):
            raise fail("native_launcher_mismatch")
        for key in ("launcher_sha256", "game_mod_sha256", "secret"):
            digest(value[key], "native_workbench." + key)
        if hashlib.sha256(launcher_raw).hexdigest() != value["launcher_sha256"]:
            raise fail("native_launcher_mismatch")
        return cls(
            **{
                key: value[key]
                for key in ("config_path", "launcher_sha256", "game_mod_sha256", "secret")
            }
        )


@dataclass(frozen=True)
class NativePair:
    runtime_instance_id: str
    workbench_instance_id: str
    configuration_id: str
    workbench_url: str
    pair_id: str
    expires_at: int

    def validate(self) -> None:
        bounded_string(self.runtime_instance_id, 128)
        digest(self.workbench_instance_id, "native_workbench.instance", length=32)
        digest(self.configuration_id, "native_workbench.configuration")
        digest(self.pair_id, "native_workbench.pair", length=32)
        bounded_string(self.workbench_url, 64)
        match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/", self.workbench_url)
        if not match or not 1 <= int(match[1]) <= 65535:
            raise fail("invalid_native_origin")
        if type(self.expires_at) is not int or not 0 < self.expires_at <= 9007199254740991:
            raise fail("invalid_native_expiry")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {key: getattr(self, key) for key in BINDING_FIELDS}

    @classmethod
    def from_dict(cls, value: object) -> NativePair:
        if not isinstance(value, dict) or set(value) != set(BINDING_FIELDS):
            raise fail("invalid_native_binding")
        result = cls(**value)
        result.validate()
        return result

    def sign(self, secret: str, role: str) -> str:
        self.validate()
        digest(secret, "native_workbench.secret")
        if role not in ROLES:
            raise fail("invalid_native_role")
        raw = role + "\n" + "\n".join(str(getattr(self, key)) for key in BINDING_FIELDS) + "\n"
        return hmac.new(bytes.fromhex(secret), raw.encode("utf-8"), hashlib.sha256).hexdigest()

    def headers(self, secret: str) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + self.sign(secret, "native-access-v1"),
            "X-STS2-Game-Instance-ID": self.runtime_instance_id,
            "X-SpireAgent-Workbench-Instance-ID": self.workbench_instance_id,
            "X-SpireAgent-Configuration-ID": self.configuration_id,
            "X-SpireAgent-Pair-ID": self.pair_id,
        }


class NativeWorkbenchAccess:
    def __init__(self, app: Any) -> None:
        self.app = app
        self._pair: NativePair | None = None
        self._lock = threading.RLock()
        self._peer: Any = None

    def bootstrap(self) -> NativeBootstrap:
        try:
            directory = selected_directory()
            bootstrap = NativeBootstrap.read(directory, self.app.config_path)
            launcher = json.loads(
                private_bytes(directory / "launcher.json"), object_pairs_hook=unique_object
            )
            identity = self.app.identity
            if identity.get("working_tree_clean") is not True or any(
                launcher[key] != identity.get(key)
                for key in ("source_revision", "workbench_sha256", "uv_lock_sha256")
            ):
                raise fail("native_selected_workbench_changed")
            return bootstrap
        except BoundaryError:
            raise
        except FileNotFoundError as error:
            raise fail("native_access_not_configured") from error
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise fail("native_access_recovery_required") from error

    def install(self, pair: NativePair, peer: Any) -> None:
        pair.validate()
        if pair.workbench_instance_id != self.app.instance_id:
            raise fail("native_workbench_changed")
        with self._lock:
            self._pair, self._peer = pair, peer

    def current(self) -> NativePair | None:
        with self._lock:
            return self._pair

    def revoke(self) -> None:
        with self._lock:
            self._pair, self._peer = None, None

    def _configuration_current(self, pair: NativePair) -> None:
        from spireagent.workbench.developer import ProjectConfig
        from spireagent.workbench.developer_server import configuration_id

        current = ProjectConfig.load(self.app.config_path)
        runtime = json.loads(private_bytes(self.app.config.state_dir / "runtime.json"))
        if (
            current != self.app.config
            or pair.configuration_id != configuration_id(current)
            or runtime.get("instance_id") != pair.workbench_instance_id
            or runtime.get("configuration_id") != pair.configuration_id
        ):
            raise fail("native_configuration_changed")

    @staticmethod
    def _headers_current(headers: Any, pair: NativePair, secret: str) -> None:
        if headers.get("Origin") is not None or headers.get("Cookie") is not None:
            raise fail("native_loopback_required")
        if any(
            not hmac.compare_digest(
                str(headers.get(key, "")).encode("utf-8"), value.encode("utf-8")
            )
            for key, value in pair.headers(secret).items()
        ):
            raise fail("native_pair_mismatch")

    def authenticate_recovery(self, headers: Any, request_id: object) -> NativePair:
        # This proof authorizes only recovery of the exact existing admitted model
        # intent. The dispatch rechecks it atomically with the owner's intent increment.
        context = self.app.models.native_intent_context(request_id)
        pair = NativePair.from_dict(context["binding"])
        bootstrap = self.bootstrap()
        if time.time() > pair.expires_at + 600:
            raise fail("native_recovery_grace_expired")
        self._headers_current(headers, pair, bootstrap.secret)
        self._configuration_current(pair)
        return pair

    def authorize_intent(self, context: dict[str, Any]) -> None:
        stored = self.app.models.native_intent_context(context["request_id"])
        if stored["binding"] != context["binding"]:
            raise fail("native_model_intent_superseded")
        original = NativePair.from_dict(context["binding"])
        current = self.current()
        if current is None or any(
            getattr(current, key) != getattr(original, key)
            for key in (
                "runtime_instance_id",
                "workbench_instance_id",
                "configuration_id",
                "workbench_url",
            )
        ):
            raise fail("native_model_context_changed")
        bootstrap = self.bootstrap()
        self.authenticate(current.headers(bootstrap.secret))

    def authenticate(self, headers: Any) -> NativePair:
        if headers.get("Origin") is not None or headers.get("Cookie") is not None:
            raise fail("native_loopback_required")
        bootstrap = self.bootstrap()
        with self._lock:
            pair, peer = self._pair, self._peer
        if pair is None or peer is None or pair.expires_at <= time.time():
            raise fail("native_pair_expired_or_missing")
        self._headers_current(headers, pair, bootstrap.secret)
        self._configuration_current(pair)
        # Read-only peer confirmation precedes dispatch. It cannot settle any job.
        try:
            response = peer._request(
                "GET", "/v1/workbench/native-status", headers=pair.headers(bootstrap.secret)
            )
        except (HTTPError, URLError, OSError, ValueError, TypeError) as error:
            raise fail("native_pair_unavailable") from error
        if (
            not isinstance(response, dict)
            or set(response) != {"schema", "signature", *BINDING_FIELDS}
            or response["schema"] != CURRENT_SCHEMA
            or {key: response[key] for key in BINDING_FIELDS} != pair.to_dict()
            or not isinstance(response["signature"], str)
            or not hmac.compare_digest(
                response["signature"], pair.sign(bootstrap.secret, "native-current-v1")
            )
        ):
            raise fail("native_game_pair_changed")
        return pair
