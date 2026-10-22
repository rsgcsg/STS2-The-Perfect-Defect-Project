"""Open the final TEST comparison for four exact frozen result IDs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from spireagent.json_boundary import BoundaryError
from spireagent.storage.config import open_peer
from spireagent.storage.store import ArtifactStore
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.local_model_export import LocalModelExport
from stpd.p1.comparison import AGENTS, compare_test_results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    for agent in AGENTS:
        parser.add_argument("--" + agent + "-result", required=True)
    parser.add_argument("--open-test", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        config = ProjectConfig.load(args.config)
        store: ArtifactStore
        if config.remote_compute is not None:
            store = open_peer(config.remote_compute.compute_store)
        else:
            store = LocalModelExport(config)._workspace().store
        value = compare_test_results(store, {a: getattr(args, a + "_result") for a in AGENTS},
                                     open_test=args.open_test)
        print(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, BoundaryError) as error:
        code = error.code if isinstance(error, BoundaryError) else type(error).__name__
        print(json.dumps({"status": "blocked", "error_code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
