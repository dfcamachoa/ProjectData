"""End-to-end local smoke test against the synthetic sample_data.

Runs a real Spark + Delta ingestion into a path-based Delta table under ./_smoke,
then re-runs it to prove idempotency (no duplicate rows on a second pass).

Requires pyspark + delta-spark installed (see requirements.txt) and a JDK.
Run:  python smoke_local.py
"""
from __future__ import annotations

import pathlib
import shutil

from bronze.config import BronzeConfig
from bronze.ingest import ingest
from bronze.spark_session import get_spark

ROOT = pathlib.Path(__file__).resolve().parent
TABLE_PATH = str(ROOT / "_smoke" / "bronze_pid_documents")


def main() -> None:
    shutil.rmtree(ROOT / "_smoke", ignore_errors=True)
    spark = get_spark(app_name="bronze-smoke", enable_hive=False)  # path-based, no metastore
    try:
        cfg = BronzeConfig(
            source_dir=str(ROOT / "sample_data"),
            table_path=TABLE_PATH,
            # no project_code override — let it derive from each file's EPC
            # document number so the demo shows 215777C / 216097C / A22 (§3.1).
        )

        print("First ingest:")
        print(ingest(spark, cfg))

        print("\nSecond ingest (must insert 0 — idempotent):")
        print(ingest(spark, cfg))

        df = spark.read.format("delta").load(TABLE_PATH)
        print("\nLanded rows:", df.count())
        df.select(
            "source_filename",
            "source_format",
            "document_number",
            "project_code",
            "drawing_revision",
            "drawing_revision_date",
            "header_parse_ok",
            "content_hash",
        ).show(truncate=False)

        # Immutability / audit-trail check: every row is a distinct version.
        assert df.count() == df.select("content_hash").distinct().count(), (
            "content_hash must be unique per landed version"
        )
        print("Idempotency + uniqueness checks passed.")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
