"""Read-only Hub deployment checks. Never starts services or changes application data."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sqlite3
import stat
import subprocess
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

PUBLIC_KEYS = {
    "STPD_WORKER_IMAGE",
    "STPD_CADDY_IMAGE",
    "STPD_HUB_DOMAIN",
    "STPD_ACME_EMAIL",
    "STPD_HUB_STATE_DIR",
    "STPD_HUB_CADDY_DIR",
    "STPD_HUB_SECRET_FILE",
    "STPD_HUB_BUDGET_UNITS",
}
SECRET_KEYS = {
    "STPD_HUB_ADMIN_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "STPD_S3_ENDPOINT",
    "STPD_S3_REGION",
    "STPD_S3_BUCKET",
    "STPD_S3_PREFIX",
    "STPD_INGRESS_BUCKET",
    "STPD_MODAL_TARGET",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "MODAL_ENVIRONMENT",
    "STPD_ACCESS_ISSUER",
    "STPD_ACCESS_AUDIENCE",
    "STPD_ACCESS_ALLOWLIST",
    "STPD_HUB_BACKUP_STATUS",
}
COMPUTE_KEYS = {"STPD_MODAL_TARGET", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"}
REQUIRED_SECRET_KEYS = SECRET_KEYS - {
    "AWS_SESSION_TOKEN",
    "STPD_S3_REGION",
    "STPD_S3_PREFIX",
    "MODAL_ENVIRONMENT",
    *COMPUTE_KEYS,
    "STPD_ACCESS_ISSUER",
    "STPD_ACCESS_AUDIENCE",
    "STPD_ACCESS_ALLOWLIST",
    "STPD_HUB_BACKUP_STATUS",
}
IMAGE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}"
SAFE_STATUS_DIRECTORY = Path("/var/lib/stpd-maintenance/safe-status")


class PreflightError(ValueError):
    pass


def operational_owner(name: str) -> ModuleType:
    # This command runs under host Python without numpy/Torch or an installed STPD.
    # Load the one stdlib-only owner from this exact checkout, not a second policy.
    if name not in {"capacity", "backup_status"}:
        raise PreflightError("unknown_operational_owner")
    path = Path(__file__).resolve().parents[2] / "spireagent/hub" / (name + ".py")
    spec = importlib.util.spec_from_file_location("stpd_host_" + name, path)
    if spec is None or spec.loader is None:
        raise PreflightError("capacity_owner_unavailable")
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    return owner


def capacity_owner() -> ModuleType:
    return operational_owner("capacity")


def check_capacity(
    config: Path,
    image_store: Path,
    *,
    additional_bytes: int,
    additional_inodes: int,
) -> dict[str, Any]:
    values = read_env(config, PUBLIC_KEYS)
    state = Path(values.get("STPD_HUB_STATE_DIR", ""))
    if not state.is_absolute() or not image_store.is_absolute():
        raise PreflightError("capacity_requires_absolute_state_and_image_store")
    return dict(
        capacity_owner().host_capacity(
            state,
            image_store,
            additional_bytes=additional_bytes,
            additional_inodes=additional_inodes,
        )
    )


def read_env(path: Path, allowed: set[str], *, private: bool = False) -> dict[str, str]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
        raise PreflightError("env_requires_bounded_regular_file")
    if private and info.st_mode & 0o077:
        raise PreflightError("secret_file_requires_mode_0600")
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in allowed or key in result:
            raise PreflightError("env_unknown_or_duplicate_key")
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise PreflightError("env_invalid_raw_value")
        result[key] = value
    return result


def owner_uid(path: Path) -> int:
    return path.stat().st_uid


def checkout_identity() -> dict[str, str]:
    """Read this deployment checkout; Hub still owns the runtime Producer guard."""
    root = Path(__file__).resolve().parents[2]
    command = ["git", "-c", f"safe.directory={root.parent}"]

    def read(*args: str) -> bytes:
        return subprocess.check_output(
            command + list(args),
            cwd=root,
            stderr=subprocess.PIPE,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )

    if read("status", "--porcelain"):
        raise PreflightError("compute_requires_exact_clean_deployment_checkout")
    revision = read("rev-parse", "HEAD").decode().strip()
    lock = (root / "uv.lock").read_bytes()
    if (
        re.fullmatch(r"[0-9a-f]{40}", revision) is None
        or read("show", "HEAD:" + read("rev-parse", "--show-prefix").decode().strip() + "uv.lock")
        != lock
    ):
        raise PreflightError("compute_checkout_identity_mismatch")
    return {
        "repository": "rsgcsg/STS2-The-Perfect-Defect-Project",
        "source_revision": revision,
        "uv_lock_sha256": hashlib.sha256(lock).hexdigest(),
    }


def check_compute(
    values: dict[str, str],
    secrets: dict[str, str],
    state: Path,
    *,
    allow_compute: bool,
) -> dict[str, Any]:
    raw_budget = values["STPD_HUB_BUDGET_UNITS"]
    if re.fullmatch(r"0|[1-9][0-9]{0,18}", raw_budget) is None:
        raise PreflightError("compute_budget_must_be_nonnegative_integer")
    budget = int(raw_budget)
    if budget and not allow_compute:
        raise PreflightError("initial_deployment_must_disable_compute_budget")
    if (
        "MODAL_ENVIRONMENT" in secrets
        and re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}",
            secrets["MODAL_ENVIRONMENT"],
        )
        is None
    ):
        raise PreflightError("invalid_modal_environment")
    configured = any(key in secrets for key in COMPUTE_KEYS)
    if not configured:
        if budget:
            raise PreflightError("positive_budget_requires_exact_modal_target")
        return {"compute_budget": 0, "compute": "disabled"}
    if any(not secrets.get(key) for key in COMPUTE_KEYS):
        raise PreflightError("optional_compute_environment_incomplete")
    container_path = Path(secrets["STPD_MODAL_TARGET"])
    mount = Path("/var/lib/stpd")
    if not container_path.is_relative_to(mount) or ".." in container_path.parts:
        raise PreflightError("modal_target_must_be_inside_mounted_state")
    target_path = state / container_path.relative_to(mount)
    if any(part.is_symlink() for part in (target_path, *target_path.parents)):
        raise PreflightError("modal_target_symlinks_forbidden")
    info = target_path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
        raise PreflightError("modal_target_requires_bounded_regular_file")
    if info.st_mode & 0o077 or owner_uid(target_path) != 10001:
        raise PreflightError("modal_target_requires_private_uid_10001_file")
    target = json.loads(target_path.read_bytes())
    if (
        not isinstance(target, dict)
        or target.get("schema") != "stpd/modal-target-v2"
        or target.get("producer") != checkout_identity()
        or target.get("image") != values["STPD_WORKER_IMAGE"]
    ):
        raise PreflightError("modal_target_current_source_lock_image_mismatch")
    return {
        "compute_budget": budget,
        "compute": "configured_budget_zero" if not budget else "explicitly_enabled",
    }


@contextmanager
def membership_reader_owner(path: Path) -> Iterator[None]:
    """Scope the single-threaded Linux host reader to the verified database owner.

    SQLite's read-only WAL connection can create -wal/-shm. Those files must belong
    to the Hub, and closing the connection must happen before operator identity returns.
    This host-only context must not be used by a threaded server or library worker.
    """
    if platform.system() != "Linux":
        yield
        return
    if owner_uid(path) != 10001:
        raise PreflightError("membership_database_requires_private_uid_10001_file")
    original_uid = os.geteuid()
    if original_uid == 10001:
        yield
        return
    if original_uid != 0:
        raise PreflightError("membership_reader_requires_root_or_database_owner")
    import threading

    if threading.active_count() != 1:
        raise PreflightError("membership_reader_requires_single_threaded_host")
    original_gid, original_groups = os.getegid(), os.getgroups()
    try:
        os.setgroups([])
        os.setegid(path.stat().st_gid)
        os.seteuid(10001)
        yield
    finally:
        # This finally starts before the first switch: partial failures restore too.
        os.seteuid(original_uid)
        os.setegid(original_gid)
        os.setgroups(original_groups)


def check_console(secrets: dict[str, str], state: Path) -> dict[str, str]:
    # Recognize the retired key only to explain the required explicit migration.
    if "STPD_ACCESS_ALLOWLIST" in secrets:
        raise PreflightError("legacy_runtime_allowlist_membership_migration_required")
    names = ("STPD_ACCESS_ISSUER", "STPD_ACCESS_AUDIENCE")
    if not any(key in secrets for key in names):
        return {"browser_console": "disabled"}
    if not all(secrets.get(key) for key in names):
        raise PreflightError("optional_browser_environment_incomplete")
    if re.fullmatch(r"https://[a-z0-9-]+\.cloudflareaccess\.com", secrets[names[0]]) is None:
        raise PreflightError("invalid_access_issuer")
    if re.fullmatch(r"[a-f0-9]{64}", secrets[names[1]]) is None:
        raise PreflightError("invalid_access_audience")
    path = state / "operations.sqlite"
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise PreflightError("membership_database_symlinks_forbidden")
    if not path.is_file():
        raise PreflightError("membership_database_explicit_bootstrap_required")
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.is_symlink():
            raise PreflightError("membership_database_symlinks_forbidden")
        if candidate.exists():
            info = candidate.stat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or owner_uid(candidate) != 10001
            ):
                raise PreflightError("membership_database_requires_private_uid_10001_file")
    try:
        # Do not instantiate Operations: it owns migrations and writes. A read transaction
        # includes committed WAL state; immutable=1 would silently ignore current members.
        with (
            membership_reader_owner(path),
            closing(
                sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2),
            ) as db,
        ):
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            if db.execute("PRAGMA user_version").fetchone() != (4,):
                raise PreflightError("membership_schema4_migration_required")
            initialized = db.execute(
                "SELECT value FROM settings WHERE key='membership_initialized'",
            ).fetchone()
            if initialized != ("1",):
                raise PreflightError("membership_explicit_bootstrap_required")
            admins = db.execute(
                "SELECT id,email,issuer,subject,access_subject FROM identity_members "
                "WHERE role='admin' AND status='active'",
            ).fetchall()
            if any(row[2] == secrets[names[0]] and row[3] and row[4] for row in admins):
                return {
                    "browser_console": "configured_not_live_qualified",
                    "membership": "hub_operations_active_admin",
                }
            pending = db.execute(
                "SELECT value FROM settings WHERE key='membership_bootstrap_pending_admin'",
            ).fetchone()
            invited = db.execute(
                "SELECT id,email,issuer,subject,access_subject FROM identity_members "
                "WHERE role='admin' AND status='invited'",
            ).fetchall()
            if (
                not admins
                and pending
                and len(invited) == 1
                and pending == (invited[0][0],)
                and invited[0][2] == secrets[names[0]]
                and isinstance(invited[0][1], str)
                and re.fullmatch(r"[^\s@]+@[^\s@]+", invited[0][1])
                and len(invited[0][1]) <= 254
                and invited[0][3] is None
                and invited[0][4] is None
                and not db.execute(
                    "SELECT 1 FROM identity_members WHERE status='active'",
                ).fetchone()
                and not db.execute("SELECT 1 FROM identity_users").fetchone()
                and not db.execute(
                    "SELECT 1 FROM devices WHERE owner_subject IS NOT NULL",
                ).fetchone()
                and not db.execute("SELECT 1 FROM identity_sessions").fetchone()
            ):
                return {
                    "browser_console": "bootstrap_pending",
                    "membership": "hub_operations_first_admin_invited",
                    "warning": "FIRST_ADMIN_LOGIN_REQUIRED",
                }
            raise PreflightError("membership_active_admin_required")
    except sqlite3.Error:
        raise PreflightError("membership_database_unreadable_or_incompatible") from None


def check_configuration(config: Path, *, allow_compute: bool = False) -> dict[str, Any]:
    values = read_env(config, PUBLIC_KEYS)
    if set(values) != PUBLIC_KEYS or any(not value for value in values.values()):
        raise PreflightError("deployment_fields_missing")
    for name in ("STPD_WORKER_IMAGE", "STPD_CADDY_IMAGE"):
        if re.fullmatch(IMAGE_PATTERN, values[name]) is None:
            raise PreflightError("immutable_image_digests_required")
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", values["STPD_HUB_DOMAIN"]) is None:
        raise PreflightError("public_dns_hostname_required")
    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", values["STPD_ACME_EMAIL"]) is None:
        raise PreflightError("acme_contact_required")
    paths = {}
    for name in ("STPD_HUB_STATE_DIR", "STPD_HUB_CADDY_DIR", "STPD_HUB_SECRET_FILE"):
        path = Path(values[name])
        if not path.is_absolute() or path.is_symlink():
            raise PreflightError("absolute_non_symlink_runtime_paths_required")
        if any((ancestor / ".git").exists() for ancestor in (path, *path.parents)):
            raise PreflightError("runtime_files_must_be_outside_git_checkout")
        paths[name] = path
    state, tls = paths["STPD_HUB_STATE_DIR"], paths["STPD_HUB_CADDY_DIR"]
    if state == tls or state in tls.parents or tls in state.parents:
        raise PreflightError("separate_application_and_tls_state_required")
    for path in (state, state / "work", state / "backups", tls, tls / "data", tls / "config"):
        if not path.is_dir() or path.is_symlink():
            raise PreflightError("persistent_directories_not_prepared")
        if path.stat().st_mode & 0o077:
            raise PreflightError("persistent_directories_must_be_private")
    if any(owner_uid(path) != 10001 for path in (state, state / "work", state / "backups")):
        raise PreflightError("application_state_owner_must_be_uid_10001")
    secrets = read_env(paths["STPD_HUB_SECRET_FILE"], SECRET_KEYS, private=True)
    if any(not secrets.get(name) for name in REQUIRED_SECRET_KEYS):
        raise PreflightError("runtime_environment_incomplete")
    if len(secrets["STPD_HUB_ADMIN_TOKEN"]) < 32:
        raise PreflightError("admin_credential_too_short")
    endpoint = urlsplit(secrets["STPD_S3_ENDPOINT"])
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise PreflightError("private_store_https_endpoint_required")
    if secrets["STPD_S3_BUCKET"] == secrets["STPD_INGRESS_BUCKET"]:
        raise PreflightError("separate_ingress_and_artifact_buckets_required")
    return {
        "configuration": "PASS",
        "credential_values": "not_reported",
        **check_compute(values, secrets, state, allow_compute=allow_compute),
        **check_console(secrets, state),
        "checks_not_performed": [
            "DNS ownership",
            "TLS issuance",
            "bucket privacy",
            "cloud IAM",
            "loaded OCI identity",
            "real upload",
            "real GPU",
        ],
    }


def check_backup(path: Path) -> dict[str, str]:
    """Open an already produced consistent backup without touching its bytes."""
    if path.is_symlink() or not path.is_file():
        raise PreflightError("backup_regular_file_required")
    if Path(str(path) + "-wal").exists() or Path(str(path) + "-shm").exists():
        raise PreflightError("backup_must_be_closed_without_wal_or_shm")
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise PreflightError("backup_integrity_failure")
        row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        if row != ("1",):
            raise PreflightError("backup_must_restore_paused")
    return {"backup_integrity": "PASS", "restore_mode": "paused"}


def host_checks() -> dict[str, Any]:
    if platform.system() != "Linux":
        raise PreflightError("deployment_requires_linux_host")
    if platform.machine() not in {"x86_64", "amd64"}:
        raise PreflightError("deployment_requires_qualified_amd64_image_host")
    if not shutil.which("docker"):
        raise PreflightError("docker_engine_and_compose_required")
    safe_status = SAFE_STATUS_DIRECTORY
    private = safe_status.parent
    if (
        not private.is_dir()
        or private.is_symlink()
        or private.stat().st_mode & 0o777 != 0o700
        or owner_uid(private) != 0
    ):
        raise PreflightError("private_backup_status_directory_requires_root_mode_0700")
    if (
        not safe_status.is_dir()
        or safe_status.is_symlink()
        or safe_status.stat().st_mode & 0o777 != 0o755
        or owner_uid(safe_status) != 0
    ):
        raise PreflightError("safe_backup_status_directory_requires_preparation_mode_0755")
    result = subprocess.run(
        ["docker", "compose", "version", "--short"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
    if result.returncode or match is None or tuple(map(int, match.groups())) < (2, 30, 0):
        raise PreflightError("compose_2_30_or_newer_required")
    memory = int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    return {
        "host": "PASS",
        "physical_memory_mib": memory // 1024**2,
        "capacity_note": "4_GiB_recommended_measure_before_raising_limits",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--host", action="store_true")
    parser.add_argument(
        "--capacity",
        action="store_true",
        help="capacity-only check; run normal configuration preflight separately",
    )
    parser.add_argument(
        "--image-store",
        type=Path,
        help="actual host filesystem containing Docker/containerd image data",
    )
    parser.add_argument(
        "--additional-bytes",
        type=int,
        help="reviewed peak extra bytes; explicit 0 for already present images",
    )
    parser.add_argument(
        "--additional-inodes",
        type=int,
        help="reviewed peak extra inode allocation; unknown is not zero",
    )
    parser.add_argument(
        "--allow-compute",
        action="store_true",
        help="validate an explicitly authorized nonzero compute budget",
    )
    args = parser.parse_args(argv)
    try:
        if not (args.config or args.backup or args.host or args.capacity):
            raise PreflightError("select_config_backup_or_host_checks")
        report: dict[str, Any] = {"schema": "stpd/hub-preflight-v1", "read_only": True}
        if args.capacity:
            if not args.config or not args.image_store:
                raise PreflightError("capacity_requires_config_and_image_store")
            if (
                args.additional_bytes is None
                or args.additional_inodes is None
                or args.additional_bytes < 0
                or args.additional_inodes < 0
            ):
                raise PreflightError("explicit_nonnegative_additional_capacity_required")
            capacity = check_capacity(
                args.config,
                args.image_store,
                additional_bytes=args.additional_bytes,
                additional_inodes=args.additional_inodes,
            )
            report["capacity"] = capacity
            report["checks_not_performed"] = [
                "configuration validity",
                "OCI identity",
                "capacity reservation",
                "image cleanup",
            ]
            if capacity["status"] != "ok":
                report["error"] = "capacity_attention_required"
                print(json.dumps(report))
                return 2
        if args.config and not args.capacity:
            report.update(check_configuration(args.config, allow_compute=args.allow_compute))
        if args.backup:
            report.update(check_backup(args.backup))
        if args.host:
            report.update(host_checks())
        print(json.dumps(report))
        return 0
    except Exception as error:
        # Do not emit paths, SDK messages or any parsed configuration values.
        code = str(error) if isinstance(error, PreflightError) else type(error).__name__
        print(json.dumps({"schema": "stpd/hub-preflight-v1", "read_only": True, "error": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
