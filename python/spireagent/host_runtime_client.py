"""Load the strategy-free Host Runtime client from one exact public package."""

from __future__ import annotations

import importlib
import json
import sys
import threading
from pathlib import Path
from types import ModuleType

from spireagent.package_identity import validate_installed_package

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST_RUNTIME = ROOT / "node_modules" / "@rsgcsg" / "sts2-host-runtime"
DEFAULT_HOST_RUNTIME_PIN = ROOT / "configs" / "v0" / "platform-host-runtime-v1.json"
_ACTIVATION_LOCK = threading.RLock()


def load_host_runtime_pin(path: Path = DEFAULT_HOST_RUNTIME_PIN) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def activate_host_runtime_client(
    host_runtime: Path = DEFAULT_HOST_RUNTIME,
    expected_identity: object | None = None,
) -> Path:
    host_runtime = host_runtime.resolve()
    identity = expected_identity if expected_identity is not None else load_host_runtime_pin()
    with _ACTIVATION_LOCK:
        validate_installed_package(
            host_runtime,
            identity,
            required_paths=(
                "consumers/python/sts2_headless/__init__.py",
                "tools/managed-pe-driver.mjs",
                "tools/reference-pe-driver.mjs",
            ),
        )
        package_root = host_runtime / "consumers" / "python"

        loaded: ModuleType | None = sys.modules.get("sts2_headless")
        if loaded is not None:
            loaded_file = getattr(loaded, "__file__", None)
            if loaded_file is None or package_root not in Path(loaded_file).resolve().parents:
                raise RuntimeError("sts2_headless was already imported from a different package.")

        rendered = str(package_root)
        inserted = rendered not in sys.path
        if inserted:
            sys.path.insert(0, rendered)
        # Import the public package (whose __init__ eagerly imports client)
        # while bytecode writes are suppressed. Otherwise the first use adds
        # __pycache__ inside an immutable installed package and breaks its pin.
        previous = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            importlib.import_module("sts2_headless")
        except Exception:
            if inserted:
                sys.path.remove(rendered)
            if loaded is None:
                for name, module in tuple(sys.modules.items()):
                    if name == "sts2_headless" or name.startswith("sts2_headless."):
                        module_file = getattr(module, "__file__", None)
                        if (
                            module_file is not None
                            and package_root in Path(module_file).resolve().parents
                        ):
                            sys.modules.pop(name, None)
            raise
        finally:
            sys.dont_write_bytecode = previous
        return package_root
