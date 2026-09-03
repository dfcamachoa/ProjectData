"""Silver Stage C — cross-document assembly, as a Spark job (silver_spec §3.3, §6).

The medallion shape of `ReconstructedGraph.assemble`: a per-drawing **harvest**
map (parse each sheet, collect its OPC records) followed by a plant-level
**reduce** (`match_pairs`) done in the driver over the small OPC set. It then
writes, idempotently:

    * `OffPage` edges into `silver_connections` (append, after clearing any prior
      OffPage rows) — the cross-document continuations (§3.3);
    * `opc_open_boundary` rows into `silver_quality` — the unmatched OPCs (§3.4).

OPC records are tiny (a handful per sheet), so only they are collected to the
driver — never the 13 MB payloads. Run AFTER Stage A+B has materialised
`silver_connections` (Stage C appends to it).
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

from pyspark.sql import DataFrame, SparkSession, functions as F
from pyspark.sql.functions import udf

from bronze.spark_session import get_spark

from .assemble import assemble_opcs, harvest_opcs_document
from .config import SilverConfig
from .schema import (
    CONNECTION_STRUCT,
    OPC_COLUMNS,
    OPC_STRUCT,
    QUALITY_COLUMNS,
    QUALITY_STRUCT,
    QUALITY_TABLE,
)
from pyspark.sql.types import ArrayType

_CONN_COLUMNS = [f.name for f in CONNECTION_STRUCT.fields]


def _harvest_udf():
    """One Bronze row -> an array of OPC structs (this sheet's placed OPCs)."""
    def _run(content, source_format, bronze_id, content_hash, document_number, project_code):
        try:
            recs = harvest_opcs_document(
                bytes(content) if content is not None else b"",
                source_format, bronze_id=bronze_id, content_hash=content_hash,
                document_number=document_number, project_code=project_code)
            return [tuple(r.get(c) for c in OPC_COLUMNS) for r in recs]
        except Exception:
            return []
    return udf(_run, ArrayType(OPC_STRUCT))


def run_assembly(cfg: SilverConfig, spark: Optional[SparkSession] = None) -> dict:
    """Execute Stage C: harvest OPCs, match across sheets, and write the OffPage
    edges + open-boundary flags. Returns the assembly stats."""
    own = spark is None
    if own:
        spark = get_spark(app_name="silver-pid-assemble", enable_hive=cfg.enable_hive)
    try:
        bronze = spark.table(cfg.resolve_bronze()) if not cfg.bronze_path \
            else spark.read.format("delta").load(cfg.bronze_path)

        # 1) harvest per drawing, keep only the tiny OPC rows (not the payloads)
        opc_rows = (bronze.withColumn(
            "o", _harvest_udf()(
                F.col("content"), F.col("source_format"), F.col("bronze_id"),
                F.col("content_hash"), F.col("document_number"), F.col("project_code")))
            .select(F.explode("o").alias("x")).select("x.*"))
        records = [r.asDict(recursive=True) for r in opc_rows.collect()]

        # 2) reduce: match every OPC (pure, testable core)
        run_ts = _dt.datetime.now()
        result = assemble_opcs(records, run_ts=run_ts)

        conn_tbl = cfg.table("silver_connections")
        qual_tbl = cfg.table(QUALITY_TABLE)

        # 3) OffPage edges -> silver_connections (idempotent: clear then append)
        spark.sql(f"DELETE FROM {conn_tbl} WHERE conn_type = 'OffPage'")
        if result["offpage_connections"]:
            rows = [tuple(rec.get(c) for c in _CONN_COLUMNS)
                    for rec in result["offpage_connections"]]
            (spark.createDataFrame(rows, schema=CONNECTION_STRUCT)
                 .write.format("delta").mode("append").saveAsTable(conn_tbl))

        # 4) open boundaries -> silver_quality (idempotent for this flag)
        try:
            spark.sql(f"DELETE FROM {qual_tbl} WHERE flag = 'opc_open_boundary'")
        except Exception:
            pass                                # ledger may not exist yet (Stage D not run)
        if result["open_boundaries"]:
            if cfg.enable_hive and cfg.silver_schema:
                spark.sql(f"CREATE DATABASE IF NOT EXISTS {cfg.silver_schema}")
            qrows = [tuple(rec.get(c) for c in QUALITY_COLUMNS)
                     for rec in result["open_boundaries"]]
            (spark.createDataFrame(qrows, schema=QUALITY_STRUCT)
                 .write.format("delta").mode("append").saveAsTable(qual_tbl))

        stats = dict(result["stats"])
        stats["silver_connections"] = conn_tbl
        return stats
    finally:
        if own:
            spark.stop()
