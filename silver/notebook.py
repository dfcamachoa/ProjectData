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


def changes(
    spark: SparkSession,
    *,
    bronze_table: str = "bronze.pid_documents",
    bronze_path: Optional[str] = None,
    silver_schema: str = "silver",
) -> dict:
    """Run Silver **Stage E** (object-grain CDC) in the given notebook session:
    diff, per drawing, the two most recent Bronze versions present in Silver and
    write the New/Modified/Deleted deltas to ``silver.silver_cdc``. Returns a
    summary. A delete+recreate of an unchanged object yields no delta (§3.5). To
    see anything, a drawing must have >= 2 Bronze versions (re-ingest a revised
    sheet), else the summary reports none.

        from silver.notebook import changes
        print(changes(spark))
        spark.table("silver.silver_cdc").show(40, False)
    """
    from .cdc_job import run_cdc                    # lazy import

    cfg = SilverConfig(bronze_table=bronze_table, bronze_path=bronze_path,
                       silver_schema=silver_schema)
    return run_cdc(cfg, spark=spark)


def assemble(
    spark: SparkSession,
    *,
    bronze_table: str = "bronze.pid_documents",
    bronze_path: Optional[str] = None,
    silver_schema: str = "silver",
) -> dict:
    """Run Silver **Stage C** (cross-document OPC assembly) in the given notebook
    session: harvest each drawing's off-page connectors, match them across sheets,
    and write the ``OffPage`` edges into ``silver.silver_connections`` plus the
    ``opc_open_boundary`` flags into ``silver.silver_quality``. Returns the
    assembly stats (matched pairs / open boundaries). Run AFTER reconstruct().

        from silver.notebook import assemble
        print(assemble(spark))
        spark.table("silver.silver_connections").where("conn_type='OffPage'").show()
    """
    from .assemble_job import run_assembly            # lazy import (keeps it light)

    cfg = SilverConfig(bronze_table=bronze_table, bronze_path=bronze_path,
                       silver_schema=silver_schema)
    return run_assembly(cfg, spark=spark)


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
