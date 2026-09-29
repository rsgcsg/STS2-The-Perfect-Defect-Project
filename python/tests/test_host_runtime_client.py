from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from spireagent.package_identity import PackageIdentityError, directory_sha256
from stpd.host_runtime_client import activate_host_runtime_client


def _package_fixture(root: Path, *, version: str = "1.1.0-rc.7",
                     fail_import: bool = False) -> dict[str, str]:
    package = root / "consumers" / "python" / "sts2_headless"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "raise RuntimeError('fixture import failed')\n" if fail_import
        else "from .client import ManagedPlayerEnvironment\n", encoding="utf-8"
    )
    (package / "client.py").write_text("class ManagedPlayerEnvironment: pass\n", encoding="utf-8")
    tools = root / "tools"
    tools.mkdir()
    for name in ("managed-pe-driver.mjs", "reference-pe-driver.mjs"):
        (tools / name).write_text("", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps({"name": "@rsgcsg/sts2-host-runtime", "version": version}),
        encoding="utf-8",
    )
    return {
        "package": "@rsgcsg/sts2-host-runtime",
        "version": "1.1.0-rc.7",
        "source_revision": "a" * 40,
        "component_tree_revision": "b" * 40,
        "release_asset_sha256": "c" * 64,
        "package_content_sha256": directory_sha256(root),
    }


def _run_activation(
    root: Path, expected: dict[str, str], script: str
) -> subprocess.CompletedProcess[str]:
    python_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [sys.executable, "-c", script, str(root), json.dumps(expected)],
        cwd=python_root, env={**os.environ, "PYTHONPATH": str(python_root)},
        capture_output=True, text=True, check=False,
    )


class HeadlessClientTest(unittest.TestCase):
    def test_requires_the_exact_public_host_runtime_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(PackageIdentityError):
                activate_host_runtime_client(root, {})

            expected = _package_fixture(root)
            result = _run_activation(root, expected, """
import json, pathlib, sys
from stpd.host_runtime_client import activate_host_runtime_client
root = pathlib.Path(sys.argv[1]); expected = json.loads(sys.argv[2])
actual = activate_host_runtime_client(root, expected)
from sts2_headless import ManagedPlayerEnvironment
assert ManagedPlayerEnvironment.__name__ == 'ManagedPlayerEnvironment'
assert actual == root.resolve() / 'consumers' / 'python'
""")
            self.assertEqual(result.returncode, 0, result.stderr)

            (root / "tools" / "managed-pe-driver.mjs").write_text(
                "// tampered\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(PackageIdentityError, "content differs"):
                activate_host_runtime_client(root, expected)

    def test_rejects_host_runtime_package_version_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = _package_fixture(root, version="9.9.9")
            with self.assertRaisesRegex(PackageIdentityError, "version differs"):
                activate_host_runtime_client(root, expected)

    def test_public_import_preserves_exact_package_hash_on_repeat_activation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = _package_fixture(root)
            result = _run_activation(root, expected, """
import json, pathlib, sys
from spireagent.package_identity import directory_sha256
from stpd.host_runtime_client import activate_host_runtime_client
root = pathlib.Path(sys.argv[1]); expected = json.loads(sys.argv[2])
original_flag = sys.dont_write_bytecode
for _ in range(2):
    activate_host_runtime_client(root, expected)
    from sts2_headless import ManagedPlayerEnvironment
    assert ManagedPlayerEnvironment.__name__ == 'ManagedPlayerEnvironment'
    assert directory_sha256(root) == expected['package_content_sha256']
    assert sys.dont_write_bytecode is original_flag
assert not list(root.rglob('__pycache__'))
""")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(directory_sha256(root), expected["package_content_sha256"])

    def test_failed_import_restores_flag_and_allows_clean_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = _package_fixture(root, fail_import=True)
            result = _run_activation(root, expected, """
import json, pathlib, sys
from spireagent.package_identity import directory_sha256
from stpd.host_runtime_client import activate_host_runtime_client
root = pathlib.Path(sys.argv[1]); expected = json.loads(sys.argv[2])
original_flag = sys.dont_write_bytecode
try:
    activate_host_runtime_client(root, expected)
    raise AssertionError('bad fixture unexpectedly loaded')
except RuntimeError as error:
    assert str(error) == 'fixture import failed'
assert sys.dont_write_bytecode is original_flag
assert 'sts2_headless' not in sys.modules
assert str(root / 'consumers' / 'python') not in sys.path
assert directory_sha256(root) == expected['package_content_sha256']
package = root / 'consumers' / 'python' / 'sts2_headless' / '__init__.py'
package.write_text('from .client import ManagedPlayerEnvironment\\n', encoding='utf-8')
expected['package_content_sha256'] = directory_sha256(root)
activate_host_runtime_client(root, expected)
from sts2_headless import ManagedPlayerEnvironment
assert ManagedPlayerEnvironment.__name__ == 'ManagedPlayerEnvironment'
assert sys.dont_write_bytecode is original_flag
assert directory_sha256(root) == expected['package_content_sha256']
""")
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
