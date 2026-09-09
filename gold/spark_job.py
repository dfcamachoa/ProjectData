"""Gold's real Spark job -- reads Bronze + Silver Delta tables, applies
Stage E's `silver_cdc` batch onto Gold's own persisted bi-temporal state,
and writes the result back as Delta tables. Mirrors
`bronze/ingest.py` / `silver/cdc_job.py` exactly: `bronze.spark_session.get_spark`
for the session, a `GoldConfig` dataclass for everything that varies, driver-
side `.collect()` over the small per-run tables (this project's own "PoC's
tens-to-low-hundreds of sheets, driver-side over the collected tables"
discipline -- the same one `silver/cdc_job.py::run_cdc` already uses), and
`spark.createDataFrame(...).write.format("delta")` for the write. All of the
actual logic (attrs resolution, the bi-temporal state machine) lives in
`spark_bridge.py` / `silver_cdc.py` / `temporal.py` -- none of it pyspark-
aware -- so this module is a thin orchestration layer, unit-tested only at
the boundary (it isn't unit-tested itself; there is no Spark/Delta available
in the sandbox this package was built in, mirroring bronze_layer_spec.md
§8.2 / silver_layer_spec.md §6's own Spark sketches -- but every function
this module calls into IS unit-tested, and this module's own shape now
matches `silver/cdc_job.py` line for line rather than being a notebook
sketch).

Run this once per orchestration cycle, right after Silver's Stage E
(`silver.cdc_job.run_cdc`) -- Gold consumes exactly the `silver_cdc` batch
that run wrote (that table is overwritten each run, not accumulated;
Gold's OWN table is what accumulates history across runs, read back here as
`previous`).
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Dict, List, Optional

from pyspark.sql import Row, SparkSession

from bronze.spark_session import get_spark

from .config import GoldConfig
from .schema import (
    GOLD_ANOMALIES_COLUMNS,
    GOLD_ANOMALIES_SCHEMA,
    GOLD_ANOMALIES_TABLE,
    GOLD_OBJECTS_COLUMNS,
    GOLD_OBJECTS_SCHEMA,
    GOLD_OBJECTS_TABLE,
)
from .silver_cdc import OBJECT_KINDS, apply_silver_cdc_events_tolerant
from .spark_bridge import (
    GRAIN_ID_COL,
    GRAIN_TABLE,
    build_events,
    dict_to_gold_row,
    gold_row_to_dict,
    anomaly_to_dict,
    lines_for_drawing,
    valid_from_by_drawing,
)


def _collect(df) -> "List[dict]":
    return [r.asDict(recursive=True) for r in df.collect()]


def _read_previous(spark: SparkSession, cfg: GoldConfig) -> "tuple[dict, dict]":
    """Gold's own table from the prior run -> ({object_kind: [GoldRow,...]},
    {(object_kind, anchor_id): drawing_number}). Returns ({}, {}) on a first
    run (table doesn't exist yet) -- `apply_silver_cdc_events_tolerant`'s own
    documented contract for that case."""
    table = cfg.table(GOLD_OBJECTS_TABLE)
    if not spark.catalog.tableExists(table):
        return {}, {}
    rows_by_kind: Dict[str, list] = {kind: [] for kind in OBJECT_KINDS}
    drawing_by_anchor: Dict[tuple, Optional[str]] = {}
    for r in _collect(spark.table(table)):
        attrs = json.loads(r["attrs_json"]) if r.get("attrs_json") else {}
        row = dict_to_gold_row(r, attrs)
        rows_by_kind.setdefault(row.object_kind, []).append(row)
        drawing_by_anchor[(row.object_kind, row.anchor_id)] = r.get("drawing_number")
    return rows_by_kind, drawing_by_anchor


def run_gold(cfg: GoldConfig, spark: Optional[SparkSession] = None) -> dict:
    """Execute one Gold run and write `gold_objects` (+ `gold_anomalies` if
    this batch produced any). Returns a summary."""
    own = spark is None
    if own:
        spark = get_spark(app_name="gold-pid-bitemporal", enable_hive=cfg.enable_hive)
    try:
        if cfg.enable_hive and cfg.gold_schema:
            spark.sql(f"CREATE DATABASE IF NOT EXISTS {cfg.gold_schema}")

        cdc_table = cfg.silver_table("silver_cdc")
        if not spark.catalog.tableExists(cdc_table):
            return {"events_applied": 0, "note": f"{cdc_table} does not exist yet"}
        cdc_rows = _collect(spark.table(cdc_table))
        if not cdc_rows:
            return {"events_applied": 0, "note": f"{cdc_table} is empty for this run"}

        # --- 1) valid_from per drawing, from Bronze (bi-temporal §4) --------
        bronze = (spark.table(cfg.resolve_bronze()) if not cfg.bronze_path
                  else spark.read.format("delta").load(cfg.bronze_path))
        bronze_rows = _collect(
            bronze.select("document_number", "drawing_revision_date")
                  .where("drawing_revision_date is not null").distinct()
        )
        valid_from_by_doc = valid_from_by_drawing(bronze_rows)

        # --- 2) per-grain attrs sources, only what this batch needs ---------
        grains_present = {r["grain"] for r in cdc_rows}
        attrs_by_grain: Dict[str, dict] = {}
        for grain, table_name in GRAIN_TABLE.items():
            if grain == "line" or grain not in grains_present:
                continue
            id_col = GRAIN_ID_COL[grain]
            rows = _collect(spark.table(cfg.silver_table(table_name)))
            attrs_by_grain[grain] = {r[id_col]: r for r in rows}

        segment_rows: List[dict] = []
        lines_by_drawing: Dict[str, list] = {}
        if "line" in grains_present:
            segment_rows = _collect(spark.table(cfg.silver_table("silver_segments")))
            component_rows = _collect(spark.table(cfg.silver_table("silver_components")))
            connection_rows = _collect(spark.table(cfg.silver_table("silver_connections")))
            drawings = {r["drawing_number"] for r in cdc_rows if r["grain"] == "line"}
            for dwg in drawings:
                lines_by_drawing[dwg] = lines_for_drawing(
                    dwg, segment_rows, component_rows, connection_rows)

        # --- 3) build events, apply onto Gold's own persisted state --------
        events, skipped = build_events(
            cdc_rows, valid_from_by_doc, attrs_by_grain, lines_by_drawing, segment_rows)
        for s in skipped:
            print(f"skip {s['cdc_id']}: {s['reason']}")

        previous_rows, drawing_by_anchor = _read_previous(spark, cfg)
        for e in events:
            drawing_by_anchor[(e.object_kind, e.anchor_id)] = e.drawing_number

        gold_rows, anomalies = apply_silver_cdc_events_tolerant(previous_rows, events)

        # --- 4) write gold_objects (full rewrite -- matches silver_cdc's own
        # mode("overwrite") full-rewrite at this PoC scale; the table already
        # holds every row-version, open and closed, so this is a lossless
        # re-serialization, not a truncate-and-lose-history) --------------
        out_rows = []
        for kind, rows in gold_rows.items():
            for row in rows:
                dwg = drawing_by_anchor.get((kind, row.anchor_id))
                out_rows.append(gold_row_to_dict(row, dwg, json.dumps(row.attrs, default=str)))

        (spark.createDataFrame([Row(**r) for r in out_rows], schema=GOLD_OBJECTS_SCHEMA)
              .write.format("delta").mode("overwrite").option("overwriteSchema", "true")
              .saveAsTable(cfg.table(GOLD_OBJECTS_TABLE)))

        if anomalies:
            run_ts = _dt.datetime.now()
            anomaly_rows = [anomaly_to_dict(a, run_ts) for a in anomalies]
            writer = (spark.createDataFrame([Row(**r) for r in anomaly_rows], schema=GOLD_ANOMALIES_SCHEMA)
                      .write.format("delta"))
            mode = "append" if spark.catalog.tableExists(cfg.table(GOLD_ANOMALIES_TABLE)) else "overwrite"
            writer.mode(mode).saveAsTable(cfg.table(GOLD_ANOMALIES_TABLE))

        return {
            "events_applied": len(events),
            "events_skipped": len(skipped),
            "row_versions_written": len(out_rows),
            "anomalies": len(anomalies),
            "gold_objects": cfg.table(GOLD_OBJECTS_TABLE),
            "gold_anomalies": cfg.table(GOLD_ANOMALIES_TABLE) if anomalies else None,
        }
    finally:
        if own:
            spark.stop()
