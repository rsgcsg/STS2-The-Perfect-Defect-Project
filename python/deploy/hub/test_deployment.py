"""Portable source checks only; Docker/TLS/storage runtime qualification is separate."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import preflight
from test_support import PosixPermissionFixture


class PreflightTests(PosixPermissionFixture):
    def setUp(self) -> None:
        super().setUp()
        # Existing portable membership fixtures mock file UID10001; exercise the
        # already-running-as-owner branch without requiring privileged CI.
        uid = patch.object(preflight.os, "geteuid", return_value=10001, create=True)
        uid.start()
        self.addCleanup(uid.stop)

    @staticmethod
    def membership_database(root: Path, *, schema: int = 4, active: bool = True) -> Path:
        path = root / "operations.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(f"PRAGMA user_version={schema}")
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("INSERT INTO settings VALUES('membership_initialized','1')")
            db.execute("CREATE TABLE identity_members(id TEXT PRIMARY KEY,email TEXT,issuer TEXT,"
                       "subject TEXT,access_subject TEXT,role TEXT,status TEXT)")
            db.execute("CREATE TABLE identity_users(subject TEXT)")
            db.execute("CREATE TABLE identity_sessions(subject TEXT)")
            db.execute("CREATE TABLE devices(owner_subject TEXT)")
            db.execute("INSERT INTO identity_members VALUES(?,?,?,?,?,'admin',?)",
                       ("admin", "admin@example.invalid", "https://team.cloudflareaccess.com",
                        "subject" if active else None, "access" if active else None,
                        "active" if active else "invited"))
        path.chmod(0o600)
        return path

    @staticmethod
    def browser_environment() -> dict[str, str]:
        return {"STPD_ACCESS_ISSUER": "https://team.cloudflareaccess.com",
                "STPD_ACCESS_AUDIENCE": "a" * 64}

    def test_browser_uses_private_hub_membership_without_legacy_runtime_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.assertEqual(preflight.check_console({}, root), {"browser_console": "disabled"})
            self.assertEqual(list(root.iterdir()), [])
            fields = self.browser_environment()
            with self.assertRaisesRegex(preflight.PreflightError, "incomplete"):
                preflight.check_console({"STPD_ACCESS_ISSUER": fields["STPD_ACCESS_ISSUER"]}, root)
            for legacy in ("", "/DO_NOT_READ/private-access.json"):
                with self.assertRaisesRegex(preflight.PreflightError, "migration_required"):
                    preflight.check_console({**fields, "STPD_ACCESS_ALLOWLIST": legacy}, root)
            with self.assertRaisesRegex(preflight.PreflightError, "explicit_bootstrap"):
                preflight.check_console(fields, root)
            self.assertFalse((root / "operations.sqlite").exists())
            path = self.membership_database(root)
            before = path.read_bytes()
            with patch.object(preflight, "owner_uid", return_value=10001):
                report = preflight.check_console(fields, root)
                self.assertEqual(report, {"browser_console": "configured_not_live_qualified",
                                          "membership": "hub_operations_active_admin"})
                self.assertNotIn("admin@example", json.dumps(report))
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(list(root.iterdir()), [path])
                path.chmod(0o644)
                with self.assertRaisesRegex(preflight.PreflightError, "private_uid"):
                    preflight.check_console(fields, root)
                path.chmod(0o600)
            with (patch.object(preflight, "owner_uid", return_value=0),
                  self.assertRaisesRegex(preflight.PreflightError, "private_uid")):
                preflight.check_console(fields, root)

    def test_browser_requires_matching_schema_initialization_and_bound_current_admin(self) -> None:
        alterations = [
            ("PRAGMA user_version=3", "schema4_migration"),
            ("PRAGMA user_version=5", "schema4_migration"),
            ("DELETE FROM settings", "explicit_bootstrap"),
            ("UPDATE identity_members SET status='disabled'", "active_admin_required"),
            ("UPDATE identity_members SET role='member'", "active_admin_required"),
            ("UPDATE identity_members SET subject=NULL", "active_admin_required"),
            ("UPDATE identity_members SET access_subject=''", "active_admin_required"),
            ("UPDATE identity_members SET issuer='https://other.cloudflareaccess.com'",
             "active_admin_required"),
            ("DROP TABLE identity_members", "unreadable_or_incompatible"),
        ]
        for sql, code in alterations:
            with self.subTest(sql=sql), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                path = self.membership_database(root)
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(sql)
                before = path.read_bytes()
                with (patch.object(preflight, "owner_uid", return_value=10001),
                      self.assertRaisesRegex(preflight.PreflightError, code)):
                    preflight.check_console(self.browser_environment(), root)
                self.assertEqual(path.read_bytes(), before)

    def test_first_admin_requires_owner_bootstrap_marker_and_empty_identity_state(self) -> None:
        mutations = [
            "DELETE FROM settings WHERE key='membership_bootstrap_pending_admin'",
            "UPDATE settings SET value='other' WHERE key='membership_bootstrap_pending_admin'",
            "UPDATE identity_members SET issuer='https://other.cloudflareaccess.com'",
            "UPDATE identity_members SET email='malformed'",
            "UPDATE identity_members SET subject='already-bound'",
            "UPDATE identity_members SET access_subject='already-bound'",
            "INSERT INTO identity_members VALUES('two','other@example.invalid',"
            "'https://team.cloudflareaccess.com',NULL,NULL,'admin','invited')",
            "INSERT INTO identity_members VALUES('two','other@example.invalid',"
            "'https://team.cloudflareaccess.com','member','member','member','active')",
            "INSERT INTO identity_users VALUES('previous-user')",
            "INSERT INTO devices VALUES('previous-owner')",
            "INSERT INTO identity_sessions VALUES('previous-session')",
        ]
        for mutation in [None, *mutations]:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                path = self.membership_database(root, active=False)
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(
                        "INSERT INTO settings VALUES('membership_bootstrap_pending_admin','admin')",
                    )
                    if mutation:
                        db.execute(mutation)
                with patch.object(preflight, "owner_uid", return_value=10001):
                    if mutation:
                        with self.assertRaisesRegex(preflight.PreflightError,
                                                    "active_admin_required"):
                            preflight.check_console(self.browser_environment(), root)
                    else:
                        observed = preflight.check_console(self.browser_environment(), root)
                        self.assertEqual(observed, {
                            "browser_console": "bootstrap_pending",
                            "membership": "hub_operations_first_admin_invited",
                            "warning": "FIRST_ADMIN_LOGIN_REQUIRED",
                        })

    def test_browser_reads_committed_wal_not_stale_main_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = self.membership_database(root)
            with closing(sqlite3.connect(path)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("UPDATE identity_members SET status='disabled'")
                writer.commit()
                for sidecar in root.iterdir():
                    sidecar.chmod(0o600)
                inventory = set(root.iterdir())
                # SQLite maintains SHM reader marks even for a mode=ro connection. Durable
                # database/WAL bytes and directory inventory must remain unchanged.
                before = {p.name: p.read_bytes() for p in inventory if not p.name.endswith("-shm")}
                with (patch.object(preflight, "owner_uid", return_value=10001),
                      self.assertRaisesRegex(preflight.PreflightError, "active_admin_required")):
                    preflight.check_console(self.browser_environment(), root)
                self.assertEqual(set(root.iterdir()), inventory)
                self.assertEqual({p.name: p.read_bytes() for p in inventory
                                  if not p.name.endswith("-shm")}, before)

    @unittest.skipIf(os.name == "nt", "real symlink creation requires Windows privileges")
    def test_browser_rejects_database_and_wal_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = self.membership_database(root)
            other = root / "other.sqlite"
            path.rename(other)
            path.symlink_to(other)
            with self.assertRaisesRegex(preflight.PreflightError, "symlinks_forbidden"):
                preflight.check_console(self.browser_environment(), root)
            path.unlink()
            other.rename(path)
            Path(str(path) + "-wal").symlink_to(path)
            with (patch.object(preflight, "owner_uid", return_value=10001),
                  self.assertRaisesRegex(preflight.PreflightError, "symlinks_forbidden")):
                preflight.check_console(self.browser_environment(), root)

    def test_named_modal_environment_is_accepted_without_enabling_compute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "runtime.env"
            env_file.write_text("MODAL_ENVIRONMENT=spireagent-b\n")
            secrets = preflight.read_env(env_file, preflight.SECRET_KEYS)
            values = {"STPD_HUB_BUDGET_UNITS": "0"}
            self.assertEqual(preflight.check_compute(
                values, secrets, root, allow_compute=False,
            ), {"compute_budget": 0, "compute": "disabled"})
            for invalid in ("", "wrong/environment", "environment with spaces"):
                with self.assertRaisesRegex(preflight.PreflightError, "invalid_modal_environment"):
                    preflight.check_compute(
                        values, {"MODAL_ENVIRONMENT": invalid}, root, allow_compute=False,
                    )

    def test_configuration_is_read_only_and_never_reports_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("state/work", "state/backups", "tls/data", "tls/config"):
                (root / name).mkdir(parents=True, exist_ok=True)
            for path in root.rglob("*"):
                if path.is_dir():
                    path.chmod(0o700)
            secrets = root / "runtime.env"
            secrets.write_text(
                "STPD_HUB_ADMIN_TOKEN=" + "private-not-output-" * 3 + "\n"
                "AWS_ACCESS_KEY_ID=private-key\nAWS_SECRET_ACCESS_KEY=private-secret\n"
                "STPD_S3_ENDPOINT=https://account.r2.cloudflarestorage.com\n"
                "STPD_S3_BUCKET=artifacts\nSTPD_INGRESS_BUCKET=ingress\n"
            )
            secrets.chmod(0o600)
            fields = {
                "STPD_WORKER_IMAGE": "registry.example/stpd@sha256:" + "a" * 64,
                "STPD_CADDY_IMAGE": "docker.io/library/caddy@sha256:" + "b" * 64,
                "STPD_HUB_DOMAIN": "hub.example.org", "STPD_ACME_EMAIL": "ops@example.org",
                "STPD_HUB_STATE_DIR": str(root / "state"),
                "STPD_HUB_CADDY_DIR": str(root / "tls"),
                "STPD_HUB_SECRET_FILE": str(secrets), "STPD_HUB_BUDGET_UNITS": "0",
            }
            config = root / "deployment.env"
            config.write_text("".join(f"{key}={value}\n" for key, value in fields.items()))
            before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in (config, secrets)}
            with patch.object(preflight, "owner_uid", return_value=10001):
                report = preflight.check_configuration(config)
                self.assertEqual(report["configuration"], "PASS")
                self.assertNotIn("private-", json.dumps(report))
                (root / "state").chmod(0o755)
                with self.assertRaisesRegex(preflight.PreflightError, "must_be_private"):
                    preflight.check_configuration(config)
                (root / "state").chmod(0o700)
                secrets.chmod(0o644)
                with self.assertRaisesRegex(preflight.PreflightError, "mode_0600"):
                    preflight.check_configuration(config)
                secrets.chmod(0o600)
                config.write_text(config.read_text().replace("BUDGET_UNITS=0", "BUDGET_UNITS=1"))
                with self.assertRaisesRegex(preflight.PreflightError, "disable_compute_budget"):
                    preflight.check_configuration(config)
                config.write_text(config.read_text().replace("BUDGET_UNITS=1", "BUDGET_UNITS=0"))
            self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path in (config, secrets)})

    def test_backup_reader_preserves_bytes_and_rejects_unpaused_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "operations.sqlite"
            with closing(sqlite3.connect(backup)) as db, db:
                db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
                db.execute("INSERT INTO settings VALUES('paused','1')")
            original = backup.read_bytes()
            self.assertEqual(preflight.check_backup(backup)["restore_mode"], "paused")
            self.assertEqual(backup.read_bytes(), original)
            with closing(sqlite3.connect(backup)) as db, db:
                db.execute("UPDATE settings SET value='0'")
            with self.assertRaisesRegex(preflight.PreflightError, "restore_paused"):
                preflight.check_backup(backup)

    def test_env_rejects_duplicates_and_shell_style_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "bad.env"
            for value in ("UNKNOWN=value", "export STPD_HUB_DOMAIN=x", "X=1\nX=2"):
                config.write_text(value)
                with self.assertRaises(preflight.PreflightError):
                    preflight.read_env(config, {"X"})

    def test_optional_compute_is_exact_mounted_and_explicitly_budgeted(self) -> None:
        producer = {"repository": "rsgcsg/STS2-The-Perfect-Defect", "source_revision": "a" * 40,
                    "uv_lock_sha256": "b" * 64}
        image = "registry.example/stpd@sha256:" + "c" * 64
        values = {"STPD_WORKER_IMAGE": image, "STPD_HUB_BUDGET_UNITS": "0"}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory).resolve()
            target = state / "modal-target.json"
            target.write_text(json.dumps({"schema": "stpd/modal-target-v2", "producer": producer,
                                          "image": image, "gpu": "L4", "timeout_seconds": 300,
                                          "cpu_cores": 2, "memory_mib": 8192,
                                          "storage_secret_name": "storage",
                                          "qwen_volume_name": None}))
            target.chmod(0o600)
            secrets = {"STPD_MODAL_TARGET": "/var/lib/stpd/modal-target.json",
                       "MODAL_TOKEN_ID": "private-modal-id", "MODAL_TOKEN_SECRET": "private-secret"}
            original = target.read_bytes()
            with (patch.object(preflight, "owner_uid", return_value=10001),
                  patch.object(preflight, "checkout_identity", return_value=producer)):
                report = preflight.check_compute(values, secrets, state, allow_compute=False)
                self.assertEqual(report["compute"], "configured_budget_zero")
                self.assertNotIn("private-", json.dumps(report))
                self.assertEqual(target.read_bytes(), original)
                old_schema = json.loads(original)
                old_schema["schema"] = "stpd/modal-target-v1"
                target.write_text(json.dumps(old_schema))
                with self.assertRaisesRegex(preflight.PreflightError, "current_source_lock"):
                    preflight.check_compute(values, secrets, state, allow_compute=False)
                target.write_bytes(original)
                values["STPD_HUB_BUDGET_UNITS"] = "1"
                with self.assertRaisesRegex(preflight.PreflightError, "disable_compute_budget"):
                    preflight.check_compute(values, secrets, state, allow_compute=False)
                self.assertEqual(preflight.check_compute(
                    values, secrets, state, allow_compute=True,
                )["compute"], "explicitly_enabled")
                with self.assertRaisesRegex(preflight.PreflightError, "requires_exact_modal"):
                    preflight.check_compute(values, {}, state, allow_compute=True)
                values["STPD_HUB_BUDGET_UNITS"] = "0"
                incomplete = {"MODAL_TOKEN_ID": "private-modal-id"}
                with self.assertRaisesRegex(preflight.PreflightError, "environment_incomplete"):
                    preflight.check_compute(values, incomplete, state, allow_compute=False)
                for bad_path in ("/etc/target.json", "/var/lib/stpd/../target.json"):
                    secrets["STPD_MODAL_TARGET"] = bad_path
                    with self.assertRaisesRegex(preflight.PreflightError, "inside_mounted_state"):
                        preflight.check_compute(values, secrets, state, allow_compute=False)
                secrets["STPD_MODAL_TARGET"] = "/var/lib/stpd/modal-target.json"
                target.write_text(original.decode().replace("a" * 40, "d" * 40))
                with self.assertRaisesRegex(preflight.PreflightError, "source_lock_image_mismatch"):
                    preflight.check_compute(values, secrets, state, allow_compute=False)

    @unittest.skipIf(os.name == "nt", "Linux symlink guard requires POSIX fixture rights")
    def test_modal_target_symlinks_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory).resolve()
            target = state / "real.json"
            target.write_text("{}")
            (state / "linked.json").symlink_to(target)
            values = {"STPD_HUB_BUDGET_UNITS": "0"}
            secrets = {"STPD_MODAL_TARGET": "/var/lib/stpd/linked.json",
                       "MODAL_TOKEN_ID": "synthetic", "MODAL_TOKEN_SECRET": "synthetic"}
            with self.assertRaisesRegex(preflight.PreflightError, "symlinks_forbidden"):
                preflight.check_compute(values, secrets, state, allow_compute=False)

    def test_proxy_preserves_current_32_mib_upload_intent_contract(self) -> None:
        config = (Path(__file__).parent / "Caddyfile").read_text()
        self.assertIn("max_size 33554432", config)

    def test_ingress_does_not_expire_unverified_evidence(self) -> None:
        value = json.loads((Path(__file__).parent / "ingress-lifecycle.json").read_text())
        self.assertEqual(len(value["Rules"]), 1)
        rule = value["Rules"][0]
        self.assertEqual(rule["Filter"], {"Prefix": "incoming/"})
        self.assertEqual(rule["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"], 1)
        self.assertNotIn("Expiration", rule)


if __name__ == "__main__":
    unittest.main()
