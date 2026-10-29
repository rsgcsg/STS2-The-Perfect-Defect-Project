"""Generic S3 conditional-write adapter; no vendor branch enters research semantics."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from spireagent.json_boundary import text
from spireagent.storage.blobs import MAX_BLOB_BYTES, StoreError, bounded, safe_key


class S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class S3Config:
    bucket: str
    endpoint: str | None = None
    prefix: str = "stpd"
    region: str = "us-east-1"

    def __post_init__(self) -> None:
        text(self.bucket, "s3.bucket", maximum=255)
        if any(c in self.bucket for c in "/\\:@?# "):
            raise StoreError("invalid_bucket")
        text(self.region, "s3.region", maximum=128)
        if self.prefix:
            safe_key(self.prefix)
        if self.endpoint is not None:
            parsed = urlsplit(self.endpoint)
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.scheme not in {"https", "http"}
                or (parsed.scheme == "http" and not local)
            ):
                raise StoreError("unsafe_endpoint_configuration")


def client_for(config: S3Config) -> S3Client:
    # Credentials use the SDK's scoped environment/profile/instance-role provider chain.
    # Never copy them into manifests, generated launch packets, or logs.
    import boto3
    from botocore.config import Config

    return cast(
        S3Client,
        boto3.client(
            "s3",
            endpoint_url=config.endpoint,
            region_name=config.region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=15,
                read_timeout=60,
                retries={"mode": "standard", "max_attempts": 3},
            ),
        ),
    )


def _error_code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        detail = response.get("Error", {})
        if isinstance(detail, dict) and isinstance(detail.get("Code"), str):
            return str(detail["Code"])
    return "transport_failure"


class S3ReadError(StoreError):
    """A failed S3 read with safe transport facts, outside the generic blob port."""

    def __init__(self, code: str, diagnostic: dict[str, object]) -> None:
        super().__init__(code)
        self.diagnostic = diagnostic


def _read_failure(operation: str, key: str, error: Exception) -> S3ReadError:
    """Keep safe SDK facts, never its message, URL, headers or request arguments."""
    from botocore.exceptions import (
        ConnectionClosedError,
        ConnectTimeoutError,
        EndpointConnectionError,
        IncompleteReadError,
        ReadTimeoutError,
        ResponseStreamingError,
    )

    response = getattr(error, "response", {})
    metadata = response.get("ResponseMetadata", {}) if isinstance(response, dict) else {}
    status = metadata.get("HTTPStatusCode")
    code = _error_code(error)
    transient = (isinstance(error, (ConnectionClosedError, ConnectTimeoutError,
        EndpointConnectionError, IncompleteReadError, ReadTimeoutError, ResponseStreamingError))
        or status in {408, 429, 500, 502, 503, 504}
        or code in {"SlowDown", "RequestTimeout", "InternalError", "ServiceUnavailable"})
    return S3ReadError(operation, {"operation": operation, "prefix_or_key": key,
        "sdk_error_type": type(error).__name__, "service_code": code,
        "http_status": status, "retryable": transient})


class S3BlobStore:
    def __init__(self, config: S3Config, client: S3Client | None = None) -> None:
        self.config = config
        self.client = client if client is not None else client_for(config)

    def _key(self, key: str) -> str:
        safe_key(key)
        return f"{self.config.prefix}/{key}" if self.config.prefix else key

    def put_if_absent(self, key: str, data: bytes) -> bool:
        bounded(data)
        for _ in range(3):
            try:
                self.client.put_object(
                    Bucket=self.config.bucket,
                    Key=self._key(key),
                    Body=data,
                    ContentLength=len(data),
                    IfNoneMatch="*",
                    ContentMD5=base64.b64encode(
                        hashlib.md5(data, usedforsecurity=False).digest()
                    ).decode("ascii"),
                    Metadata={"sha256": hashlib.sha256(data).hexdigest()},
                )
                return True
            except Exception as error:
                code = _error_code(error)
                if code in {"PreconditionFailed", "412"}:
                    if self.get(key) != data:
                        raise StoreError("immutable_key_collision") from None
                    return False
                if code in {"ConditionalRequestConflict", "409"}:
                    continue
                if code in {"NotImplemented", "InvalidRequest", "InvalidArgument"}:
                    raise StoreError("conditional_writes_not_supported") from None
                raise StoreError("s3_put_failed") from None
        raise StoreError("conditional_write_conflict_exhausted")

    def get(self, key: str) -> bytes:
        from botocore.exceptions import (
            ConnectionClosedError,
            IncompleteReadError,
            ReadTimeoutError,
            ResponseStreamingError,
        )

        try:
            response = self.client.get_object(Bucket=self.config.bucket, Key=self._key(key))
        except Exception as error:
            if _error_code(error) in {"NoSuchKey", "NotFound", "404"}:
                raise StoreError("object_not_found") from None
            raise _read_failure("s3_get_failed", key, error) from None
        body = response.get("Body")
        if body is None or not callable(getattr(body, "read", None)):
            raise StoreError("invalid_s3_body")
        try:
            length = response.get("ContentLength")
            if type(length) is not int or not 0 <= length <= MAX_BLOB_BYTES:
                raise StoreError("s3_body_length_mismatch")
            buffer = bytearray()
            while len(buffer) <= length:
                part = body.read(length + 1 - len(buffer))
                if not isinstance(part, bytes):
                    raise StoreError("invalid_s3_body")
                if not part:
                    break
                buffer.extend(part)
            data = bytes(buffer)
        except (ConnectionClosedError, IncompleteReadError, ReadTimeoutError,
                ResponseStreamingError) as error:
            # StreamingBody failures happen after get_object; keep them classified
            # without exposing the SDK's endpoint/credential-bearing message.
            raise _read_failure("s3_stream_read_failed", key, error) from None
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        bounded(data)
        length = response.get("ContentLength")
        if type(length) is not int or length != len(data):
            raise StoreError("s3_body_length_mismatch")
        return data

    def keys(self, prefix: str) -> tuple[str, ...]:
        safe_key(prefix, prefix=True)
        full_prefix = f"{self.config.prefix}/{prefix}" if self.config.prefix else prefix
        token: str | None = None
        seen_tokens: set[str] = set()
        found: set[str] = set()
        while True:
            arguments: dict[str, Any] = {"Bucket": self.config.bucket, "Prefix": full_prefix}
            if token is not None:
                arguments["ContinuationToken"] = token
            try:
                page = self.client.list_objects_v2(**arguments)
            except Exception as error:
                raise _read_failure("s3_list_failed", prefix, error) from None
            entries = page.get("Contents", [])
            if not isinstance(entries, list):
                raise StoreError("invalid_s3_listing")
            for entry in entries:
                key = entry.get("Key") if isinstance(entry, dict) else None
                if not isinstance(key, str) or not key.startswith(full_prefix):
                    raise StoreError("invalid_s3_listing_key")
                relative = key[len(self.config.prefix) + 1 :] if self.config.prefix else key
                found.add(safe_key(relative))
            if page.get("IsTruncated", False) is False:
                return tuple(sorted(found))
            token = page.get("NextContinuationToken")
            if not isinstance(token, str) or not token or token in seen_tokens:
                raise StoreError("invalid_s3_pagination")
            seen_tokens.add(token)
