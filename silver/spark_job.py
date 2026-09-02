"""Silver reconstruction Spark job (Phase-1: A + B + persist).

Reads the Bronze table, runs the vendored reconstruction once per drawing inside
a UDF (parse once, distribute over files — silver_spec §6.1), and writes the four
Silver Delta tables. Reuses bronze.spark_session.get_spark so Silver shares the
same Derby metastore / warehouse as Bronze (spec §8.4) — that is what lets
``spark.table("bronze.pid_documents")`` resolve.
"""
from __future__ import annotations

from typing import Dict

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.functions import udf

from bronze.spark_session import get_spark  # reuse Bronze's Delta+Hive session

from .config import SilverConfig
from .reconstruct import reconstruct_document
from .schema import RECON_RESULT_STRUCT, SILVER_TABLES


def _recon_udf():
    """UDF: one Bronze row -> a struct of four arrays (the four table row-sets).
    Malformed input yields empty arrays so one bad drawing never fails the batch
    (honest-partial-result, silver_spec §3.4; a parse-fail ledger row is Stage D)."""

    def _run(content, source_format, bronze_id, content_hash, document_number, project_code):
        try:
            out = reconstruct_document(
                bytes(content) if content is not None else b"",
                source_format,
                bronze_id=bronze_id,
                content_hash=content_hash,
                document_number=document_number,
                project_code=project_code,
            )
            return (out["components"], out["segments"],
                    out["connections"], out["equipment"])
        except Exception:
            return ([], [], [], [])

    return udf(_run, RECON_RESULT_STRUCT)


def run(cfg: SilverConfig, spark: SparkSession | None = None) -> Dict[str, int]:
    """Execute Silver Stage A+B and write the four Delta tables. Returns row
    counts per table."""
    own = spark is None
    if own:
        spark = get_spark(app_name="silver-pid-reconstruct", enable_hive=cfg.enable_hive)

    try:
        if cfg.enable_hive and cfg.silver_schema:
            spark.sql(f"CREATE DATABASE IF NOT EXISTS {cfg.silver_schema}")

        bronze = spark.table(cfg.resolve_bronze()) if not cfg.bronze_path \
            else spark.read.format("delta").load(cfg.bronze_path)

        # size-aware fan-out (§6.1): keep the big sheets off one core
        if cfg.repartition_by_size_buckets and "file_size_bytes" in bronze.columns:
            bronze = bronze.repartitionByRange(
                cfg.repartition_by_size_buckets, F.col("file_size_bytes"))

        recon = bronze.withColumn(
            "r",
            _recon_udf()(
                F.col("content"), F.col("source_format"), F.col("bronze_id"),
                F.col("content_hash"), F.col("document_number"), F.col("project_code"),
            ),
        ).select("r")

        recon = recon.persist()  # parse once; four explodes read the cached result

        counts: Dict[str, int] = {}
        for name in SILVER_TABLES:
            field = name.replace("silver_", "")           # components/segments/...
            rows = recon.select(F.explode(F.col(f"r.{field}")).alias("x")).select("x.*")
            writer = rows.write.format("delta").mode(cfg.write_mode)
            if cfg.partition_by:
                writer = writer.partitionBy(*cfg.partition_by)
            writer.saveAsTable(cfg.table(name))
            counts[name] = spark.table(cfg.table(name)).count()

        recon.unpersist()
        return counts
    finally:
        if own:
            spark.stop()
