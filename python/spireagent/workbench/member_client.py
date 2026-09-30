"""Scoped account BFF and verified project downloads; no storage credential reaches JS."""

from __future__ import annotations

import hashlib
import re
import shutil
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.request import Request

from spireagent.json_boundary import BoundaryError, digest, json_bytes
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.identity import LocalIdentity

DOWNLOAD_TIMEOUT = 30


class MemberClient:
    def __init__(self, account: LocalIdentity) -> None:
        self.account = account
        self.lock = threading.RLock()
        self.operation: dict[str, Any] = {"status": "idle"}
        self.thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.owner = ""

    def token(self) -> str:
        session = self.account.session()
        if not session:
            raise BoundaryError("identity", "sign_in_required")
        return str(session["session_token"])

    def request(self, route: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        path, _, query = route.partition("?")
        allowed = (
            r"collection-settings|campaigns(?:/[a-f0-9]{64}/enroll|/enrollments(?:/[a-f0-9]{32})?)?"
            r"|collections/[a-f0-9]{32}/decisions"
            r"|artifacts/visibility"
            r"|exports(?:/[a-f0-9]{64})?|datasets(?:/archived|/visibility|/[a-f0-9]{32}(?:/(?:retry|cancel))?)?|games"
        )
        if re.fullmatch(allowed, path) is None:
            raise BoundaryError("member", "invalid_member_route")
        values = parse_qs(query, strict_parsing=True, max_num_fields=2)
        if (
            set(values) - {"limit", "offset"}
            or any(len(value) != 1 for value in values.values())
            or (body is not None and query)
        ):
            raise BoundaryError("member", "invalid_member_query")
        writable = re.fullmatch(
            r"exports|quality-annotations|artifacts/visibility|datasets(?:/visibility|/materialize|/[a-f0-9]{32}/(?:retry|cancel))?"
            r"|campaigns/[a-f0-9]{64}/enroll", path
        )
        if body is not None and writable is None:
            raise BoundaryError("member", "member_route_read_only")
        return self.account.request("/v1/identity/member/" + route, body=body, token=self.token())

    def prepare_campaign(self, enrollment_id: str) -> dict[str, Any]:
        from sts2_platform_evidence.delivery_config import DeliveryConfig

        from spireagent.workbench.campaign_prepare import prepare_campaign

        if not re.fullmatch(r"[a-f0-9]{32}", enrollment_id):
            raise BoundaryError("member", "invalid_enrollment_id")
        # Fetch again: a browser-supplied enrollment cannot become local authority.
        enrollment = self.request("campaigns/enrollments/" + enrollment_id)
        current = self.account.config.delivery_config
        if current is None:
            from spireagent.workbench.collection_tool_registration import (
                current_collection_tool,
                registered_collection_tool,
            )

            release = enrollment["template"].get("tool_release_id")
            tool_directory = (
                registered_collection_tool(self.account.config, release)
                if release
                else current_collection_tool(self.account.config)[0]
            )
        else:
            tool_directory = DeliveryConfig.load(current).tool_directory
        return prepare_campaign(self.account.config, enrollment, tool_directory)

    def download_status(self) -> dict[str, Any]:
        # Download progress is account-private even on the same local Workbench.
        token = self.token()
        with self.lock:
            if self.owner and self.owner != hashlib.sha256(token.encode()).hexdigest():
                return {"status": "idle"}
            return dict(self.operation)

    def download(self, export_id: str) -> dict[str, Any]:
        identity = digest(export_id, "member.export_id")
        token = self.token()
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise BoundaryError("member", "download_in_progress")
            self.operation = {
                "status": "pending",
                "export_id": identity,
                "verified_files": 0,
                "verified_bytes": 0,
            }
            self.owner = hashlib.sha256(token.encode()).hexdigest()
            self.cancel.clear()
            self.thread = threading.Thread(
                target=self._download, args=(identity, token), daemon=True
            )
            self.thread.start()
            return dict(self.operation)

    def _update(self, **values: Any) -> None:
        with self.lock:
            self.operation.update(values)

    @staticmethod
    def _directory(path: Path) -> None:
        if path.is_symlink():
            raise BoundaryError("member", "download_symlink_rejected")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.is_dir():
            raise BoundaryError("member", "download_directory_required")

    def _download(self, export_id: str, token: str) -> None:
        temporary: Path | None = None
        temporary_owned = False
        try:
            # This runs in the transfer worker, not an interactive catalog read.
            # Inventory authorization may inspect a multi-source immutable lineage.
            value = self.account.request(
                "/v1/identity/member/exports/" + export_id, token=token,
                timeout=DOWNLOAD_TIMEOUT,
            )
            files = value.get("files")
            content = {
                key: item for key, item in value.items() if key not in {"export_id", "created_at"}
            }
            if (
                value.get("schema") != "stpd/project-export-v1"
                or value.get("export_id") != export_id
                or not isinstance(files, list)
                or not 1 <= len(files) <= 1000
                or hashlib.sha256(json_bytes(content)).hexdigest() != export_id
            ):
                raise BoundaryError("member", "invalid_export_inventory")
            total = 0
            identities: set[str] = set()
            for item in files:
                file_id = digest(item["file_id"], "export.file_id")
                digest(item["sha256"], "export.sha256")
                if file_id in identities or type(item["size"]) is not int or item["size"] < 0:
                    raise BoundaryError("member", "invalid_export_inventory")
                identities.add(file_id)
                total += item["size"]
            if total != value.get("total_bytes") or total > 20 * 1024**3:
                raise BoundaryError("member", "export_size_limit")
            root = self.account.config.state_dir
            for part in (root, root / "downloads", root / "downloads" / export_id):
                self._directory(part)
            destination = root / "downloads" / export_id
            if shutil.disk_usage(destination).free < total + 16 * 1024**2:
                raise BoundaryError("member", "insufficient_download_space")
            self._update(status="downloading", total_files=len(files), total_bytes=total)
            verified = 0
            for index, item in enumerate(files):
                # Logout, expiry or app close stop further bytes; Hub rechecks membership/grant.
                if self.cancel.is_set() or self.token() != token:
                    raise BoundaryError("member", "download_session_changed")
                target = destination / item["file_id"]
                temporary = destination / (item["file_id"] + ".part")
                temporary_owned = False
                if temporary.exists() or temporary.is_symlink() or target.is_symlink():
                    raise BoundaryError("member", "download_file_conflict")
                route = "/v1/identity/member/exports/" + export_id + "/files/" + item["file_id"]
                request = Request(
                    self.account.config.hub_url + route,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Accept": "application/octet-stream",
                    },
                )
                observed, count = hashlib.sha256(), 0
                with (
                    self.account.opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response,
                    temporary.open("xb") as handle,
                ):
                    temporary_owned = True
                    temporary.chmod(0o600)
                    while chunk := response.read(1024 * 1024):
                        count += len(chunk)
                        if count > item["size"] or self.cancel.is_set() or self.token() != token:
                            raise BoundaryError("member", "download_interrupted_or_oversize")
                        observed.update(chunk)
                        handle.write(chunk)
                if count != item["size"] or observed.hexdigest() != item["sha256"]:
                    raise BoundaryError("member", "download_checksum_mismatch")
                if self.cancel.is_set() or self.token() != token:
                    raise BoundaryError("member", "download_session_changed")
                if target.exists():
                    with target.open("rb") as handle:
                        old = hashlib.file_digest(handle, "sha256").hexdigest()
                    if old != item["sha256"]:
                        raise BoundaryError("member", "download_file_conflict")
                    temporary.unlink()
                else:
                    temporary.replace(target)
                temporary = None
                verified += count
                self._update(verified_files=index + 1, verified_bytes=verified)
            if self.cancel.is_set() or self.token() != token:
                raise BoundaryError("member", "download_session_changed")
            atomic_json(destination / "inventory.json", value)
            self._update(status="verified", directory=str(destination), training_admitted=False)
            atomic_json(destination / "download.json", self.operation)
        except (BoundaryError, OSError, ValueError, KeyError, TypeError) as error:
            self._update(
                status="failed",
                error=error.code if isinstance(error, BoundaryError) else "download_failed",
            )
        finally:
            if (
                temporary_owned
                and temporary is not None
                and temporary.is_file()
                and not temporary.is_symlink()
            ):
                temporary.unlink(missing_ok=True)

    def close(self) -> None:
        self.cancel.set()
