"""Read-only Hub access and explicitly scoped, verified result downloads."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from spireagent.artifact_contracts import Manifest
from spireagent.hub.access import RESULT_KINDS
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes
from spireagent.workbench.developer import atomic_json, endpoint

JSON_LIMIT = 8 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # A redirect must never move the Hub credential to another endpoint.
        return None


class HubClient:
    def __init__(
        self, url: str, *, timeout: float = 10, download_timeout: float = 30,
        token: Callable[[], str] | None = None,
    ) -> None:
        self.url = endpoint(url)
        self.timeout = timeout
        self.download_timeout = download_timeout
        self.token = token or (lambda: os.environ.get("STPD_HUB_TOKEN", ""))
        self.opener = build_opener(NoRedirect())

    def _request(
        self, route: str, *, limit: int | None = None, offset: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        token = self.token()
        if not token:
            raise BoundaryError("hub", "credential_not_configured")
        if not route.startswith("/v1/") or any(c in route for c in ("?", "#", "\\")):
            raise BoundaryError("hub", "invalid_route")
        query = {}
        if limit is not None:
            if type(limit) is not int or not 1 <= limit <= 100:
                raise BoundaryError("hub", "invalid_pagination")
            query["limit"] = limit
        if offset is not None:
            if type(offset) is not int or offset < 0:
                raise BoundaryError("hub", "invalid_pagination")
            query["offset"] = offset
        suffix = "?" + urlencode(query) if query else ""
        request = Request(self.url + route + suffix, headers={"Authorization": "Bearer " + token})
        try:
            return self.opener.open(request, timeout=self.timeout if timeout is None else timeout)
        except HTTPError as error:
            raise BoundaryError("hub", "http_" + str(error.code)) from None
        except (URLError, OSError, ValueError):
            raise BoundaryError("hub", "unavailable") from None

    def get(
        self, route: str, *, limit: int | None = None, offset: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        with self._request(route, limit=limit, offset=offset, timeout=timeout) as response:
            raw = response.read(JSON_LIMIT + 1)
        if len(raw) > JSON_LIMIT:
            raise BoundaryError("hub", "response_too_large")
        value = decode_json(raw)
        if not isinstance(value, dict):
            raise BoundaryError("hub", "invalid_response")
        return value

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in ("status", "uploads", "jobs", "incidents"):
            try:
                result[name] = {"status": "available", "value": self.get("/v1/" + name)}
            except (BoundaryError, OSError, ValueError) as error:
                result[name] = {
                    "status": "unavailable",
                    "code": error.code if isinstance(error, BoundaryError) else "invalid_response",
                }
        return result

    def download(
        self, artifact_id: str, destination: Path, roles: tuple[str, ...] | None = None
    ) -> dict[str, Any]:
        """Materialize only selected own payloads; provenance references remain intact.

        This result cache is deliberately not an ArtifactStore with a falsely complete
        lineage. No Dataset, evidence or other parent bytes are fetched implicitly.
        """
        identity = digest(artifact_id, "download.artifact")
        manifest = Manifest.from_bytes(
            json_bytes(self.get("/v1/artifacts/" + identity, timeout=self.download_timeout)),
            identity,
        )
        if manifest.kind not in RESULT_KINDS:
            raise BoundaryError("download", "not_a_result_artifact")
        selected = manifest.payloads if roles is None else tuple(manifest.payload(r) for r in roles)
        if roles is not None and len(set(roles)) != len(roles):
            raise BoundaryError("download", "duplicate_payload_role")
        directory = destination.expanduser().resolve() / identity
        if directory.is_symlink():
            raise BoundaryError("download", "symlink_destination")
        directory.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink():
            raise BoundaryError("download", "symlink_destination")
        files = []
        for payload in selected:
            filename = payload.sha256 + ".bin"
            target = directory / filename
            if target.is_symlink():
                raise BoundaryError("download", "symlink_destination")
            if target.exists():
                with target.open("rb") as source:
                    valid = (
                        target.stat().st_size == payload.size
                        and hashlib.file_digest(source, "sha256").hexdigest() == payload.sha256
                    )
                if not valid:
                    raise BoundaryError("download", "existing_payload_mismatch")
            else:
                descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=directory)
                temporary = Path(name)
                try:
                    size, checksum = 0, hashlib.sha256()
                    route = (
                        "/v1/artifacts/" + identity + "/payloads/" + quote(payload.role, safe="")
                    )
                    with os.fdopen(descriptor, "wb") as out, self._request(
                        route, timeout=self.download_timeout
                    ) as response:
                        while chunk := response.read(1024 * 1024):
                            size += len(chunk)
                            if size > payload.size:
                                raise BoundaryError("download", "payload_size_mismatch")
                            checksum.update(chunk)
                            out.write(chunk)
                        out.flush()
                        os.fsync(out.fileno())
                    if size != payload.size or checksum.hexdigest() != payload.sha256:
                        raise BoundaryError("download", "payload_integrity_failure")
                    try:
                        os.link(temporary, target)
                    except FileExistsError:
                        if target.is_symlink():
                            raise BoundaryError(
                                "download", "concurrent_payload_collision"
                            ) from None
                        with target.open("rb") as existing:
                            same = (
                                target.stat().st_size == payload.size
                                and hashlib.file_digest(existing, "sha256").hexdigest()
                                == payload.sha256
                            )
                        if not same:
                            raise BoundaryError(
                                "download", "concurrent_payload_collision"
                            ) from None
                finally:
                    temporary.unlink(missing_ok=True)
            files.append(
                {
                    "role": payload.role,
                    "sha256": payload.sha256,
                    "size": payload.size,
                    "file": filename,
                }
            )
        # Publish the original canonical manifest only after every selected payload.
        descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(manifest.to_bytes())
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, manifest_path)
            except FileExistsError:
                if manifest_path.is_symlink() or manifest_path.read_bytes() != manifest.to_bytes():
                    raise BoundaryError("download", "manifest_cache_collision") from None
        finally:
            temporary.unlink(missing_ok=True)
        receipt = {
            "schema": "stpd/result-download-v1",
            "artifact_id": identity,
            "kind": manifest.kind,
            "payloads": files,
            "parents_downloaded": False,
            "model_load_validated": False,
        }
        atomic_json(directory / "download.json", receipt)
        return receipt
