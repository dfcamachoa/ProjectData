"""Command-line entry point for Bronze ingestion.

Examples
--------
Create the table and ingest a folder (catalog table):
    python -m bronze.cli ingest \
        --source-dir /data/exports/projectB \
        --table lakehouse.bronze.bronze_pid_documents \
        --project-code B

Ingest into a path-based Delta table (no catalog):
    python -m bronze.cli ingest \
        --source-dir /data/exports/projectA \
        --table-path /lake/bronze/pid_documents \
        --project-code A

Just create the table:
    python -m bronze.cli create-table --table lakehouse.bronze.bronze_pid_documents
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
        table_name=args.table or "bronze_pid_documents",
        table_path=args.table_path,
        project_code=getattr(args, "project_code", None),
        store_content_text=not getattr(args, "no_content_text", False),
    )
    if getattr(args, "glob", None):
        cfg.path_glob = args.glob
    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bronze", description="P&ID Bronze layer ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--table", default="bronze_pid_documents", help="Catalog table name")
    common.add_argument("--table-path", default=None, help="Path-based Delta table location (alternative to --table)")

    p_create = sub.add_parser("create-table", parents=[common], help="Create the Bronze Delta table")

    p_ingest = sub.add_parser("ingest", parents=[common], help="Ingest a folder of source files")
    p_ingest.add_argument("--source-dir", required=True, help="Folder of source XML exports (scanned recursively)")
    p_ingest.add_argument("--glob", default="*.xml", help="File glob (default *.xml)")
    p_ingest.add_argument("--project-code", default=None, help="Optional project/tenant tag")
    p_ingest.add_argument("--no-content-text", action="store_true", help="Do not store the decoded text convenience column")

    args = parser.parse_args(argv)
    cfg = _build_config(args)
    spark = get_spark()

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
