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

    a = sub.add_parser("assemble", help="Stage C — cross-document OPC assembly")
    a.add_argument("--bronze-table", default="bronze.pid_documents",
                   help="Bronze catalog table to read (default: bronze.pid_documents)")
    a.add_argument("--bronze-path", default=None,
                   help="read Bronze from a Delta path instead of a catalog name")
    a.add_argument("--silver-schema", default="silver",
                   help="schema holding the Silver tables (default: silver)")

    e = sub.add_parser("cdc", help="Stage E — object-grain CDC, write silver_cdc")
    e.add_argument("--bronze-table", default="bronze.pid_documents",
                   help="Bronze catalog table to read (default: bronze.pid_documents)")
    e.add_argument("--bronze-path", default=None,
                   help="read Bronze from a Delta path instead of a catalog name")
    e.add_argument("--silver-schema", default="silver",
                   help="schema holding the Silver tables (default: silver)")

    q = sub.add_parser("quality", help="Stage D — run the quality gate, write silver_quality")
    q.add_argument("--silver-schema", default="silver",
                   help="schema holding the Silver tables (default: silver)")
    q.add_argument("--refdata", default=None,
                   help="path to Reference_Data.xlsx (fluid/unit/naming); optional")
    q.add_argument("--write-mode", default="overwrite", choices=["overwrite", "append"])
    q.add_argument("--equipment-pattern", default=None,
                   help="override the equipment naming regex (else the Naming sheet)")
    q.add_argument("--instrument-pattern", default=None,
                   help="override the instrument naming regex (else the Naming sheet)")
    q.add_argument("--no-raise", action="store_true",
                   help="record structural-invariant breaches but do not abort")

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

    if args.command == "assemble":
        from .assemble_job import run_assembly
        cfg = SilverConfig(
            bronze_table=args.bronze_table,
            bronze_path=args.bronze_path,
            silver_schema=args.silver_schema,
        )
        print(json.dumps(run_assembly(cfg), indent=2, default=str))
        return 0

    if args.command == "cdc":
        from .cdc_job import run_cdc
        cfg = SilverConfig(
            bronze_table=args.bronze_table,
            bronze_path=args.bronze_path,
            silver_schema=args.silver_schema,
        )
        print(json.dumps(run_cdc(cfg), indent=2, default=str))
        return 0

    if args.command == "quality":
        from .quality_job import run_quality, SilverQualityError
        cfg = SilverConfig(
            silver_schema=args.silver_schema,
            refdata_path=args.refdata,
            write_mode=args.write_mode,
        )
        try:
            summary = run_quality(
                cfg, raise_on_fail=not args.no_raise,
                equipment_pattern=args.equipment_pattern,
                instrument_pattern=args.instrument_pattern,
            )
            print(json.dumps(summary, indent=2, default=str))
            return 0
        except SilverQualityError as exc:
            print(json.dumps({"error": str(exc)}, indent=2))
            return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
