"""Audit a fixed allocation before training models for the real game entry point."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from spireagent.source import source_identity
from spireagent.storage.config import open_store
from stpd.fullrun.live_input_audit import audit_allocation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    parser.add_argument("--allocation", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; keep historical receipts")
    producer = source_identity(Path(__file__).resolve().parents[1])
    report = {"status": "running", "producer": producer.to_dict(), "allocation_id": args.allocation}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def write() -> None:
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2))
        temporary.replace(args.output)

    started = time.perf_counter()
    write()
    try:
        print("Verifying allocation and replaying original public snapshots", flush=True)
        report.update(audit_allocation(open_store(args.store), args.allocation))
        report["status"] = "completed"
        print(json.dumps({k: report[k] for k in ("counts", "exclusions", "by_split")}), flush=True)
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__,
                      error_code=getattr(error, "code", None))
        raise
    finally:
        report["elapsed_seconds"] = time.perf_counter() - started
        write()
        print("Audit status: " + report["status"], flush=True)


if __name__ == "__main__":
    main()
