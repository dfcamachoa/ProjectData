"""In-session helper for notebooks — avoid the subprocess/metastore trap (§8.4).

Embedded Derby is single-session, so a ``!python -m silver.cli`` subprocess
writes into a *different* metastore than a live notebook SparkSession: the tables
exist but the notebook can't resolve them by name. Run Silver INSIDE the
notebook's own session instead, and the tables land in the metastore you query:

    from bronze.spark_session import get_spark
    spark = get_spark()                          # Delta + Hive/Derby session

    from silver.notebook import reconstruct
    counts = reconstruct(spark)                   # bronze.pid_documents -> silver.*
    spark.table("silver.silver_segments").show()
"""
from __future__ import annotations

from typing import Optional

from pyspark.sql import SparkSession

from .config import SilverConfig
from .spark_job import run


def quality(
    spark: SparkSession,
    *,
    silver_schema: str = "silver",
    refdata_path: Optional[str] = None,
    write_mode: str = "overwrite",
    equipment_pattern: Optional[str] = None,
    instrument_pattern: Optional[str] = None,
    raise_on_fail: bool = True,
) -> dict:
    """Run Silver **Stage D** (the Great-Expectations gate) in the given notebook
    session: evaluate the declarative suite over the four Silver tables, write the
    ``silver_quality`` punch-list ledger, and denormalise ``quality_gate`` back
    onto each object table. Returns the evaluator summary (+ warnings / skipped).
    Raises ``SilverQualityError`` on a structural-invariant breach unless
    ``raise_on_fail=False``. Does **not** stop ``spark``.

        from silver.notebook import quality
        summary = quality(spark)                       # -> silver.silver_quality
        spark.table("silver.silver_quality").show(50, False)
    """
    from .quality_job import run_quality               # lazy: keep import light

    cfg = SilverConfig(
        silver_schema=silver_schema,
        refdata_path=refdata_path,
        write_mode=write_mode,
    )
    return run_quality(
        cfg, spark=spark, raise_on_fail=raise_on_fail,
        equipment_pattern=equipment_pattern,
        instrument_pattern=instrument_pattern,
    )


def reconstruct(
    spark: SparkSession,
    *,
    bronze_table: str = "bronze.pid_documents",
    bronze_path: Optional[str] = None,
    silver_schema: str = "silver",
    refdata_path: Optional[str] = None,
    write_mode: str = "overwrite",
    size_buckets: int = 0,
) -> dict:
    """Run Silver Stage A+B in the given (notebook) SparkSession and write the
    four Silver tables. Returns row counts per table. Does **not** stop
    ``spark`` (it is your session, not ours)."""
    cfg = SilverConfig(
        bronze_table=bronze_table,
        bronze_path=bronze_path,
        silver_schema=silver_schema,
        refdata_path=refdata_path,
        write_mode=write_mode,
        repartition_by_size_buckets=size_buckets,
    )
    return run(cfg, spark=spark)
