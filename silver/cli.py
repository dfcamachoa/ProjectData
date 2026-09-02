"""Silver CLI — mirror of bronze.cli.

    python -m silver.cli reconstruct --bronze-table bronze.pid_documents

Runs Stage A+B and writes silver.silver_components / _segments / _connections /
_equipment. Must run in the SAME working directory as the Bronze run so it
shares the Derby metastore + spark-warehouse (spec §8.4).
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import SilverConfig
from .spark_job import run


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="silver", description="Silver reconstruction (Phase-1).")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("reconstruct", help="parse + reconstruct Bronze into Silver tables")
    r.add_argument("--bronze-table", default="bronze.pid_documents",
                   help="Bronze catalog table to read (default: bronze.pid_documents)")
    r.add_argument("--bronze-path", default=None,
                   help="read Bronze from a Delta path instead of a catalog name")
    r.add_argument("--silver-schema", default="silver",
                   help="output database for the Silver tables (default: silver)")
    r.add_argument("--refdata", default=None,
                   help="path to Reference_Data.xlsx (tag composition); optional")
    r.add_argument("--write-mode", default="overwrite", choices=["overwrite", "append"])
    r.add_argument("--size-buckets", type=int, default=0,
                   help="repartition Bronze into N ranges by file_size_bytes (§6.1)")
    r.add_argument("--no-hive", action="store_true",
                   help="path-based only; do not use the Derby metastore")

    args = p.parse_args(argv)

    if args.command == "reconstruct":
        cfg = SilverConfig(
            bronze_table=args.bronze_table,
            bronze_path=args.bronze_path,
            silver_schema=args.silver_schema,
            refdata_path=args.refdata,
            write_mode=args.write_mode,
            enable_hive=not args.no_hive,
            repartition_by_size_buckets=args.size_buckets,
        )
        counts = run(cfg)
        print(json.dumps(counts, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
