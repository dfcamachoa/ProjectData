"""In-session helper for notebooks — ingest INTO your notebook's SparkSession so
the Bronze table lands in the metastore you query (spec §8.4).

Embedded Derby is single-session: don't mix a live notebook session with a
``!python -m bronze.cli`` subprocess against one metastore. Drive ingestion
in-session instead:

    from bronze.spark_session import get_spark
    spark = get_spark()                                  # Delta + Hive/Derby session

    from bronze.notebook import ingest_folder
    summary = ingest_folder(spark, source_dir="sample_data")
    spark.table("bronze.pid_documents").count()
"""
from __future__ import annotations

from typing import Optional

from pyspark.sql import SparkSession

from .config import BronzeConfig
from .ingest import ingest


def ingest_folder(
    spark: SparkSession,
    source_dir: str,
    *,
    table_name: str = "bronze.pid_documents",
    table_path: Optional[str] = None,
    project_code: Optional[str] = None,
    store_content_text: bool = False,
) -> dict:
    """Ingest a folder of source XML into Bronze in the given (notebook)
    SparkSession. Returns the ingest summary. Does **not** stop ``spark``."""
    cfg = BronzeConfig(
        source_dir=source_dir,
        table_name=table_name,
        table_path=table_path,
        project_code=project_code,
        store_content_text=store_content_text,
    )
    return ingest(spark, cfg)
