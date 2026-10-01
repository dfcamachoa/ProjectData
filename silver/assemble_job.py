"""Silver Stage C — cross-document assembly, as a Spark job (silver_spec §3.3, §6).

The medallion shape of `ReconstructedGraph.assemble`: a per-drawing **harvest**
map (parse each sheet, collect its OPC records) followed by a plant-level
**reduce** (`match_pairs`) done in the driver over the small OPC set. It then
writes, idempotently:

    * `OffPage` edges into `silver_connections` (append, after clearing any prior
      OffPage rows) — the cross-document continuations (§3.3);
    * `opc_open_boundary` rows into `silver_quality` — the unmatched OPCs (§3.4);
    * `silver_off_page_connectors` — one per placed OPC (matched AND unmatched),
      the per-OPC entity the Gold OffPageConnector node projects from
      (Workstream 2); its `on_segment` is resolved by a left join to
      `silver_components.segment_id`, so this must run AFTER Stage A+B.

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
    OPC_HARVEST_COLUMNS,
    OPC_HARVEST_STRUCT,
    OPC_ENTITY_COLUMNS,
    OPC_ENTITY_STRUCT,
    OPC_ENTITY_TABLE,
    QUALITY_COLUMNS,
    QUALITY_STRUCT,
    QUALITY_TABLE,
)
from pyspark.sql.types import ArrayType

_CONN_COLUMNS = [f.name for f in CONNECTION_STRUCT.fields]


def _harvest_udf():
    """One Bronze row -> an array of OPC structs (this sheet's placed OPCs),
    carrying matcher keys AND the descriptive entity fields (Workstream 2), so
    the per-OPC entity table has a source. match_pairs still reads only the
    matcher subset; the entity fields ride along to the driver."""
    def _run(content, source_format, bronze_id, content_hash, document_number, project_code):
        try:
            recs = harvest_opcs_document(
                bytes(content) if content is not None else b"",
                source_format, bronze_id=bronze_id, content_hash=content_hash,
                document_number=document_number, project_code=project_code)
            return [tuple(r.get(c) for c in OPC_HARVEST_COLUMNS) for r in recs]
        except Exception:
            return []
    return udf(_run, ArrayType(OPC_HARVEST_STRUCT))


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

        # 5) per-OPC entity rows -> silver_off_page_connectors (Workstream 2).
        # on_segment is filled by joining each OPC to its silver_components row
        # (an OPC lands there with kind containing "OffPageConnector"), taking
        # that row's segment_id — Stage B already computed the linkage, so this
        # reuses it rather than re-running the pipeline. JOIN KEY: opc_id == the
        # component's component_id. If reconstruct.py derives component_id via a
        # transform of the raw element ID, apply the same transform to opc_id
        # here before the join.
        opc_tbl = cfg.table(OPC_ENTITY_TABLE)
        spark.sql(f"DROP TABLE IF EXISTS {opc_tbl}")
        if result["off_page_connectors"]:
            if cfg.enable_hive and cfg.silver_schema:
                spark.sql(f"CREATE DATABASE IF NOT EXISTS {cfg.silver_schema}")
            erows = [tuple(rec.get(c) for c in OPC_ENTITY_COLUMNS)
                     for rec in result["off_page_connectors"]]
            ent_df = spark.createDataFrame(erows, schema=OPC_ENTITY_STRUCT)
            # resolve on_segment from silver_components.segment_id (left join, so
            # an OPC with no matching component row keeps on_segment = None)
            comp_tbl = cfg.table("silver_components")
            seg_lookup = (spark.table(comp_tbl)
                          .select(F.col("component_id").alias("_cid"),
                                  F.col("segment_id").alias("_seg")))
            ent_df = (ent_df.join(seg_lookup,
                                  ent_df["opc_id"] == seg_lookup["_cid"], "left")
                            .withColumn("on_segment", F.coalesce(F.col("_seg"),
                                                                 F.col("on_segment")))
                            .drop("_cid", "_seg"))
            # re-project to the declared column order before writing
            ent_df = ent_df.select(*OPC_ENTITY_COLUMNS)
            (ent_df.write.format("delta").mode("overwrite")
                   .option("overwriteSchema", "true").saveAsTable(opc_tbl))

        stats = dict(result["stats"])
        stats["silver_connections"] = conn_tbl
        stats["silver_off_page_connectors"] = opc_tbl
        return stats
    finally:
        if own:
            spark.stop()
