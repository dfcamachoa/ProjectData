"""Command-line entry point for Bronze ingestion.

Examples
--------
Ingest into a path-based Delta table (simplest; no catalog needed):
    python -m bronze.cli ingest \
        --source-dir /data/exports/projectA \
        --table-path ~/lake/bronze/pid_documents

Ingest into a named managed table (one- or two-part name on local Spark;
three-part catalog.schema.table needs Unity Catalog):
    python -m bronze.cli ingest \
        --source-dir /data/exports/projectB \
        --table bronze.pid_documents

project_code derives from each file's EPC document number, so one run can carry
several projects; pass --project-code only to override (§3.1).

Just create the table:
    python -m bronze.cli create-table --table bronze.pid_documents
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import BronzeConfig
from .ingest import create_bronze_table, ingest
from .spark_session import get_spark


def _build_config(args: argparse.Namespace) -> BronzeConfig:
    cfg = BronzeConfig(
        source_dir=getattr(args, "source_dir", "") or "",
        table_name=args.table or "bronze.pid_documents",
        table_path=args.table_path,
        project_code=getattr(args, "project_code", None),
        store_content_text=getattr(args, "store_content_text", False),
    )
    if getattr(args, "glob", None):
        cfg.path_glob = args.glob
    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bronze", description="P&ID Bronze layer ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--table", default="bronze.pid_documents", help="Catalog table name")
    common.add_argument("--table-path", default=None, help="Path-based Delta table location (alternative to --table)")

    p_create = sub.add_parser("create-table", parents=[common], help="Create the Bronze Delta table")

    p_ingest = sub.add_parser("ingest", parents=[common], help="Ingest a folder of source files")
    p_ingest.add_argument("--source-dir", required=True, help="Folder of source XML exports (scanned recursively)")
    p_ingest.add_argument("--glob", default="*.xml", help="File glob (default *.xml)")
    p_ingest.add_argument("--project-code", default=None, help="Override the derived project_code (normally omit; §3.1)")
    p_ingest.add_argument("--store-content-text", action="store_true", help="Materialise the decoded-text column (off by default; size-gated, §3.3)")

    args = parser.parse_args(argv)
    cfg = _build_config(args)
    # Named tables need the Hive metastore; path-based tables do not.
    spark = get_spark(enable_hive=cfg.enable_hive and cfg.table_path is None)

    try:
        if args.command == "create-table":
            create_bronze_table(spark, cfg)
            print(f"Bronze table ready: {cfg.resolve_table()}")
            return 0
        if args.command == "ingest":
            summary = ingest(spark, cfg)
            print(json.dumps(summary, indent=2, default=str))
            return 0
    finally:
        spark.stop()

    return 1


if __name__ == "__main__":
    sys.exit(main())
