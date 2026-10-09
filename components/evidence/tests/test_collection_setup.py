import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from sts2_platform_evidence.collection_tool import (
    SOURCE_V3_SUPPORT, CollectionTool, CollectionFailure, canonical, digest,
)
from sts2_platform_evidence.transfer import _inventory


class CollectionSetupTests(unittest.TestCase):
    def fixture(self, root, *, source3=False):
        entry = "setup/apps/game-mod/collection-setup.mjs"
        for name, content in {entry: "fixture", "sts2-human-annotator.dll": "fixture", "platform-bom.json": "{}",
                              "game-mod/build-provenance.json": "{}"}.items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
        identity = {"worktree": "clean", "source_revision": "c" * 40,
                    "entrypoint": "sts2-human-annotator.dll", "collection_setup_entrypoint": entry,
                    "supported_recording_schema": "sts2.human-annotator/recording-manifest-2",
                    "files": [{"path": p, "bytes": n, "sha256": h} for p, n, h in _inventory(root)]}
        if source3:
            identity["source_v3_support"] = dict(SOURCE_V3_SUPPORT)
        release_id = digest(identity)
        (root / "collection-tool.json").write_bytes(canonical({"schema": "sts2.evidence/collection-tool-1",
                                                               "release_id": release_id, "identity": identity}))
        return CollectionTool(root, release_id)

    def test_old_fixed_tool_is_legacy_valid_but_cannot_attempt_source3(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = self.fixture(root)
            self.assertFalse(tool.supports_source_v3())
            with patch("sts2_platform_evidence.collection_tool.subprocess.run") as run:
                with self.assertRaisesRegex(CollectionFailure, "unavailable"):
                    tool.pack_source_v3(root / "session", root / "bundle", "worker", "campaign")
                run.assert_not_called()

    def test_authenticated_source3_executes_only_its_fixed_command_without_attestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = self.fixture(root, source3=True)
            self.assertTrue(tool.supports_source_v3())
            with patch("sts2_platform_evidence.collection_tool.subprocess.run",
                       return_value=SimpleNamespace(returncode=0)) as run:
                tool.pack_source_v3(root / "session", root / "bundle", "worker", "campaign")
                self.assertEqual(run.call_args.args[0], [
                    "dotnet", str(root / "sts2-human-annotator.dll"), "pack-source-v3",
                    str(root / "session"), "worker", "campaign", str(root / "bundle"), "c" * 40,
                ])
                self.assertNotIn("human_origin_attested", run.call_args.args[0])
                (root / "sts2-human-annotator.dll").write_text("changed")
                with self.assertRaisesRegex(ValueError, "bytes differ"):
                    tool.pack_source_v3(root / "session", root / "bundle", "worker", "campaign")
                self.assertEqual(run.call_count, 1)

    def test_source3_descriptor_tamper_rejects_both_old_pin_and_rehashed_wrong_capability(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = self.fixture(root, source3=True)
            manifest = json.loads((root / "collection-tool.json").read_bytes())
            manifest["identity"]["source_v3_support"]["command"] = "pack-session"
            (root / "collection-tool.json").write_bytes(canonical(manifest))
            with self.assertRaisesRegex(ValueError, "release ID mismatch"):
                tool.supports_source_v3()
            release = digest(manifest["identity"])
            manifest["release_id"] = release
            (root / "collection-tool.json").write_bytes(canonical(manifest))
            with self.assertRaisesRegex(ValueError, "unsupported Source3"):
                CollectionTool(root, release)

    def test_fixed_owner_commands_pin_provenance_and_keep_status_distinct_from_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = self.fixture(root)
            response = {"schema": "sts2.platform/collection-setup-1", "status": "configured", "bound": False}
            with patch("sts2_platform_evidence.collection_tool.subprocess.run",
                       return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response))) as run:
                self.assertEqual(tool.setup_status(recordings_root=root / "records", game_directory=root / "game"), response)
                self.assertIn("status", run.call_args.args[0])
                self.assertIn(str(root / "game-mod/build-provenance.json"), run.call_args.args[0])
                self.assertEqual(tool.bind_recording_root(recordings_root=root / "records"), response)
                self.assertIn("bind", run.call_args.args[0])
                with self.assertRaises(CollectionFailure):
                    tool.setup_status(recordings_root=root / "records", mod_provenance=root.parent / "untrusted.json")
                self.assertEqual(run.call_count, 2)

    def test_setup_reverifies_all_owner_bytes_before_each_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = self.fixture(root)
            (root / "game-mod/build-provenance.json").write_text("changed")
            with patch("sts2_platform_evidence.collection_tool.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "bytes differ"):
                    tool.setup_status(recordings_root=root / "records")
                run.assert_not_called()

    def test_mixed_case_publisher_inventory_verifies_on_every_platform(self):
        """CollectionTool must match the publisher's POSIX path ordering.

        Windows Path ordering is case-insensitive, while the JavaScript
        publisher's relative POSIX path sort is ordinal.  Building the
        manifest in explicit publisher order makes this an actual filesystem
        verification on Windows as well as a portable regression elsewhere.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, content in {
                "STS2HumanAnnotator.Core.dll": b"core",
                "platform-bom.json": b"{}",
                "sts2-human-annotator.dll": b"entrypoint",
            }.items():
                destination = root / name
                destination.write_bytes(content)
            discovered = [{"path": p, "bytes": n, "sha256": h} for p, n, h in _inventory(root)]
            published = sorted(discovered, key=lambda row: row["path"])
            identity = {
                "worktree": "clean",
                "source_revision": "c" * 40,
                "entrypoint": "sts2-human-annotator.dll",
                "supported_recording_schema": "sts2.human-annotator/recording-manifest-2",
                "files": published,
            }
            release_id = digest(identity)
            (root / "collection-tool.json").write_bytes(canonical({
                "schema": "sts2.evidence/collection-tool-1",
                "release_id": release_id,
                "identity": identity,
            }))

            verified = CollectionTool(root, release_id)
            self.assertEqual(verified.manifest["identity"]["files"], published)

            # Sorting the actual rows must not weaken the declared manifest
            # order check: a separately re-pinned but unsorted declaration is
            # still a release-byte mismatch.
            identity["files"] = list(reversed(published))
            release_id = digest(identity)
            (root / "collection-tool.json").write_bytes(canonical({
                "schema": "sts2.evidence/collection-tool-1",
                "release_id": release_id,
                "identity": identity,
            }))
            with self.assertRaisesRegex(ValueError, "bytes differ"):
                CollectionTool(root, release_id)

    def test_mixed_case_windows_inventory_order_is_normalized_portably(self):
        """The verification boundary remains correct for Windows-like input."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                {"path": "STS2HumanAnnotator.Core.dll", "bytes": 4, "sha256": "a" * 64},
                {"path": "platform-bom.json", "bytes": 2, "sha256": "b" * 64},
                {"path": "sts2-human-annotator.dll", "bytes": 10, "sha256": "c" * 64},
            ]
            for row in rows:
                (root / row["path"]).write_bytes(b"x" * row["bytes"])
            identity = {
                "worktree": "clean",
                "source_revision": "c" * 40,
                "entrypoint": "sts2-human-annotator.dll",
                "supported_recording_schema": "sts2.human-annotator/recording-manifest-2",
                "files": sorted(rows, key=lambda row: row["path"]),
            }
            release_id = digest(identity)
            (root / "collection-tool.json").write_bytes(canonical({
                "schema": "sts2.evidence/collection-tool-1",
                "release_id": release_id,
                "identity": identity,
            }))
            with patch(
                "sts2_platform_evidence.collection_tool._inventory",
                return_value=[
                    ("platform-bom.json", 2, "b" * 64),
                    ("sts2-human-annotator.dll", 10, "c" * 64),
                    ("STS2HumanAnnotator.Core.dll", 4, "a" * 64),
                    ("collection-tool.json", 0, "d" * 64),
                ],
            ):
                CollectionTool(root, release_id)
