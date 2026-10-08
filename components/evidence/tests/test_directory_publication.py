from __future__ import annotations

import errno
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sts2_platform_evidence import verify_human_session_bundle
from sts2_platform_evidence import transfer as publication
from sts2_platform_evidence.store import ContentAddressedStore
from sts2_platform_evidence.transfer import DirectoryReceiver, DirectoryTransferManifest
from tests import test_evidence as fixtures


class DirectoryPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _bundle(self) -> Path:
        helper = fixtures.EvidenceTests()
        helper.root = self.root
        helper.profile_value = helper._profile_value()
        helper.profile = fixtures.load_collection_profile_from_value(helper.profile_value)
        return helper._bundle("source")

    def test_actual_platform_promotion_succeeds_and_never_replaces_empty_target(self) -> None:
        source, target = self.root / "stage", self.root / "object"
        source.mkdir()
        (source / "marker").write_bytes(b"verified bytes")
        if sys.platform not in {"darwin", "linux", "win32"}:
            with self.assertRaises(OSError) as raised:
                publication._promote_directory_no_replace(source, target)
            self.assertEqual(raised.exception.errno, errno.ENOTSUP)
            return
        publication._promote_directory_no_replace(source, target)
        self.assertEqual((target / "marker").read_bytes(), b"verified bytes")
        self.assertFalse(source.exists())
        source.mkdir()
        empty = self.root / "existing-empty"
        empty.mkdir()
        before = empty.stat().st_ino
        with self.assertRaises(FileExistsError):
            publication._promote_directory_no_replace(source, empty)
        self.assertEqual(empty.stat().st_ino, before)
        self.assertTrue(source.is_dir())

    def test_closed_dispatcher_passes_exact_mac_linux_flags_and_windows_rename(self) -> None:
        source, target = self.root / "stage", self.root / "target"
        for platform, symbol, arguments in (
            ("darwin", "renamex_np", (os.fsencode(source), os.fsencode(target), 0x4)),
            ("linux", "renameat2", (-100, os.fsencode(source), -100, os.fsencode(target), 1)),
        ):
            with self.subTest(platform=platform):
                function = Mock(return_value=0)
                library = SimpleNamespace(**{symbol: function})
                with patch.object(publication.sys, "platform", platform), \
                        patch.object(publication.ctypes, "CDLL", return_value=library), \
                        patch.object(publication.os, "replace", side_effect=AssertionError("no replace")), \
                        patch.object(publication.os, "rename", side_effect=AssertionError("no fallback")):
                    publication._promote_directory_no_replace(source, target)
                function.assert_called_once_with(*arguments)
        with patch.object(publication.sys, "platform", "win32"), \
                patch.object(publication.os, "rename") as renamed, \
                patch.object(publication.ctypes, "CDLL", side_effect=AssertionError("no libc on Windows")):
            publication._promote_directory_no_replace(source, target)
        renamed.assert_called_once_with(source, target)

    def test_unknown_platform_missing_symbols_and_unsupported_filesystem_fail_closed(self) -> None:
        source, target = self.root / "stage", self.root / "target"
        with patch.object(publication.sys, "platform", "unknown-os"), \
                patch.object(publication.os, "rename", side_effect=AssertionError("no fallback")), \
                patch.object(publication.os, "replace", side_effect=AssertionError("no replacement")):
            with self.assertRaises(OSError) as raised:
                publication._promote_directory_no_replace(source, target)
        self.assertEqual(raised.exception.errno, errno.ENOTSUP)
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform), patch.object(publication.sys, "platform", platform), \
                    patch.object(publication.ctypes, "CDLL", return_value=object()):
                with self.assertRaises(OSError) as raised:
                    publication._promote_directory_no_replace(source, target)
                self.assertEqual(raised.exception.errno, errno.ENOTSUP)
        function = Mock(return_value=-1)
        with patch.object(publication.sys, "platform", "darwin"), \
                patch.object(publication.ctypes, "CDLL", return_value=SimpleNamespace(renamex_np=function)), \
                patch.object(publication.ctypes, "get_errno", return_value=errno.ENOTSUP), \
                patch.object(publication.os, "rename", side_effect=AssertionError("no fallback")), \
                patch.object(publication.os, "replace", side_effect=AssertionError("no replacement")):
            with self.assertRaises(OSError) as raised:
                publication._promote_directory_no_replace(source, target)
        self.assertEqual(raised.exception.errno, errno.ENOTSUP)

    def test_receiver_preserves_target_created_during_typed_verification(self) -> None:
        for kind in ("empty", "valid", "corrupt", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as name:
                self.root = Path(name)
                source = self._bundle()
                bundle = verify_human_session_bundle(source).require_value()
                manifest = DirectoryTransferManifest.from_directory(
                    source, content_id=bundle.bundle_content_id, artifact_type="human-session-bundle")
                store = ContentAddressedStore(self.root / "store")
                target = store.objects / bundle.bundle_content_id
                created = []

                def verify(directory: Path, transfer: DirectoryTransferManifest) -> None:
                    value = verify_human_session_bundle(directory).require_value()
                    self.assertEqual(value.bundle_content_id, transfer.content_id)
                    if created:
                        return
                    if kind == "valid":
                        shutil.copytree(directory, target)
                    elif kind == "symlink":
                        outside = self.root / "outside"
                        shutil.copytree(directory, outside)
                        try:
                            target.symlink_to(outside, target_is_directory=True)
                        except (OSError, NotImplementedError) as error:
                            self.skipTest(f"filesystem cannot create this symlink: {error}")
                    else:
                        target.mkdir()
                        if kind == "corrupt":
                            (target / "unrelated").write_bytes(b"original existing content")
                    created.append(target.lstat().st_ino)

                receiver = DirectoryReceiver(store, promotion_verifier=verify)
                result = receiver.receive(source, manifest)
                self.assertEqual(target.lstat().st_ino, created[0])
                if kind == "valid":
                    self.assertEqual(result.status, "reused")
                    self.assertTrue(verify_human_session_bundle(target).passed)
                else:
                    self.assertIn(result.status, {"collision", "quarantined"})
                    self.assertIsNone(result.directory)
                    if kind == "empty":
                        self.assertEqual(list(target.iterdir()), [])
                    elif kind == "corrupt":
                        self.assertEqual((target / "unrelated").read_bytes(), b"original existing content")
                    else:
                        self.assertTrue(target.is_symlink())

    def test_existing_byte_identical_object_requires_typed_verification(self) -> None:
        source = self._bundle()
        value = verify_human_session_bundle(source).require_value()
        manifest = DirectoryTransferManifest.from_directory(
            source, content_id=value.bundle_content_id, artifact_type="human-session-bundle")
        store = ContentAddressedStore(self.root / "store")
        target = store.objects / value.bundle_content_id
        shutil.copytree(source, target)
        before = target.stat().st_ino

        def reject(directory: Path, transfer: DirectoryTransferManifest) -> None:
            raise ValueError("typed artifact rejection")

        result = DirectoryReceiver(store, promotion_verifier=reject).receive(source, manifest)
        self.assertEqual(result.status, "quarantined")
        self.assertEqual(target.stat().st_ino, before)

    def _link_nested_payload(self, directory: Path, *, hard: bool = False) -> Path:
        payload = directory / "export" / "decisions.jsonl"
        outside = self.root / "outside-payload"
        outside.write_bytes(payload.read_bytes())
        payload.unlink()
        try:
            if hard:
                os.link(outside, payload)
            else:
                payload.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"filesystem cannot create this link: {error}")
        return payload

    def test_existing_nested_links_cannot_be_reused(self) -> None:
        for hard in (False, True):
            with self.subTest(hard=hard), tempfile.TemporaryDirectory() as name:
                self.root = Path(name)
                source = self._bundle()
                value = verify_human_session_bundle(source).require_value()
                manifest = DirectoryTransferManifest.from_directory(
                    source, content_id=value.bundle_content_id, artifact_type="human-session-bundle")
                store = ContentAddressedStore(self.root / "store")
                target = store.objects / value.bundle_content_id
                shutil.copytree(source, target)
                payload = self._link_nested_payload(target, hard=hard)
                before = payload.lstat().st_ino
                verifier = Mock(side_effect=lambda directory, transfer:
                                verify_human_session_bundle(directory).require_value())
                result = DirectoryReceiver(store, promotion_verifier=verifier).receive(source, manifest)
                self.assertEqual(result.status, "collision")
                self.assertIsNone(result.directory)
                verifier.assert_not_called()
                self.assertEqual(payload.lstat().st_ino, before)
                self.assertEqual(payload.read_bytes(), (self.root / "outside-payload").read_bytes())

    def test_promotion_time_nested_link_target_is_preserved_and_rejected(self) -> None:
        source = self._bundle()
        value = verify_human_session_bundle(source).require_value()
        manifest = DirectoryTransferManifest.from_directory(
            source, content_id=value.bundle_content_id, artifact_type="human-session-bundle")
        store = ContentAddressedStore(self.root / "store")
        target = store.objects / value.bundle_content_id
        created = []

        def verify(directory: Path, transfer: DirectoryTransferManifest) -> None:
            verify_human_session_bundle(directory).require_value()
            self.assertFalse(created, "invalid existing target must not reach typed verifier")
            shutil.copytree(directory, target)
            payload = self._link_nested_payload(target)
            created.append(payload.lstat().st_ino)

        result = DirectoryReceiver(store, promotion_verifier=verify).receive(source, manifest)
        self.assertEqual(result.status, "collision")
        self.assertIsNone(result.directory)
        payload = target / "export" / "decisions.jsonl"
        self.assertTrue(payload.is_symlink())
        self.assertEqual(payload.lstat().st_ino, created[0])

    def test_nested_link_created_during_typed_verification_is_rejected(self) -> None:
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as name:
                self.root = Path(name)
                source = self._bundle()
                value = verify_human_session_bundle(source).require_value()
                manifest = DirectoryTransferManifest.from_directory(
                    source, content_id=value.bundle_content_id, artifact_type="human-session-bundle")
                store = ContentAddressedStore(self.root / "store")
                target = store.objects / value.bundle_content_id
                if existing:
                    shutil.copytree(source, target)

                def verify(directory: Path, transfer: DirectoryTransferManifest) -> None:
                    verify_human_session_bundle(directory).require_value()
                    self._link_nested_payload(directory)

                result = DirectoryReceiver(store, promotion_verifier=verify).receive(source, manifest)
                self.assertEqual(result.status, "quarantined")
                self.assertIsNone(result.directory)
                if existing:
                    self.assertTrue((target / "export" / "decisions.jsonl").is_symlink())
                else:
                    self.assertFalse(target.exists())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation requires POSIX")
    def test_unlisted_special_node_cannot_be_ignored_for_reuse(self) -> None:
        source = self._bundle()
        value = verify_human_session_bundle(source).require_value()
        manifest = DirectoryTransferManifest.from_directory(
            source, content_id=value.bundle_content_id, artifact_type="human-session-bundle")
        store = ContentAddressedStore(self.root / "store")
        target = store.objects / value.bundle_content_id
        shutil.copytree(source, target)
        extra = target / "export" / "unlisted-fifo"
        os.mkfifo(extra)
        before = extra.lstat().st_ino
        result = DirectoryReceiver(store).receive(source, manifest)
        self.assertEqual(result.status, "collision")
        self.assertIsNone(result.directory)
        self.assertEqual(extra.lstat().st_ino, before)

    def test_receiver_unsupported_primitive_is_not_success_or_replace(self) -> None:
        source = self._bundle()
        bundle = verify_human_session_bundle(source).require_value()
        manifest = DirectoryTransferManifest.from_directory(
            source, content_id=bundle.bundle_content_id, artifact_type="human-session-bundle")
        store = ContentAddressedStore(self.root / "store")
        with patch.object(publication, "_promote_directory_no_replace",
                          side_effect=OSError(errno.ENOTSUP, "unsupported")):
            result = DirectoryReceiver(store).receive(source, manifest)
        self.assertEqual(result.status, "quarantined")
        self.assertFalse((store.objects / bundle.bundle_content_id).exists())


if __name__ == "__main__":
    unittest.main()
