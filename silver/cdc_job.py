"""Silver Stage E — object-grain CDC, as a Spark job (silver_spec §3.5).

Diffs, per drawing, the **two most recent Bronze versions** present in Silver
(ordered by Bronze `ingested_at`, with `drawing_revision` carried as a label),
and writes `silver_cdc` — the New / Modified / Deleted deltas that are Gold's
interval open/close events. A drawing with a single version has no prior to diff
and is skipped.

The heavy lifting (anchor-match identity, the three hashes, delete+recreate
immunity, neighbour-anchor keying) lives in the pure `cdc` core; this job only
assembles the two versions and their neighbour graph and calls `diff`.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from collections import defaultdict
from typing import Dict, List, Optional

from pyspark.sql import SparkSession, functions as F

from bronze.spark_session import get_spark

from . import cdc
from .config import SilverConfig
from .schema import CDC_COLUMNS, CDC_STRUCT, CDC_TABLE

# columns to pull per grain (uid alias + the fields the hashes read)
_SEG_COLS = ["segment_id", "seg_tag", "drawing_number", "fluid", "unit", "diameter",
             "piping_materials_class", "insul_purpose", "insul_type", "insul_thick",
             "src_turnover", "src_subsystem", "project_code", "source_format",
             "content_hash"]
_CMP_COLS = ["component_id", "component_class", "segment_id", "inline_index",
             "kind", "drawing_number", "project_code", "source_format", "content_hash"]
# reconstruction kinds that are NOT piping components — diffed in other grains
_NON_COMPONENT_KINDS = {"Segment", "Nozzle", "Equipment"}
_EQ_COLS = ["equipment_id", "tag", "equipment_class", "nozzle_ids",
            "drawing_number", "project_code", "source_format", "content_hash"]
_CONN_COLS = ["from_id", "to_id", "flow_sense", "drawing_number", "content_hash"]


def _cdc_id(d: dict) -> str:
    key = "|".join(_n(d.get(k)) for k in
                   ("grain", "drawing_number", "anchor", "change_type",
                    "old_version", "new_version", "old_uid", "new_uid"))
    return "sha256:" + hashlib.sha256(key.encode()).hexdigest()


def _n(v) -> str:
    return "" if v is None else str(v)


def _obj_seg(r: dict, revision) -> dict:
    return {"uid": r["segment_id"], "seg_tag": r.get("seg_tag"),
            "drawing_number": r.get("drawing_number"), "fluid": r.get("fluid"),
            "unit": r.get("unit"), "diameter": r.get("diameter"),
            "piping_materials_class": r.get("piping_materials_class"),
            "insul_purpose": r.get("insul_purpose"), "insul_type": r.get("insul_type"),
            "insul_thick": r.get("insul_thick"), "src_turnover": r.get("src_turnover"),
            "src_subsystem": r.get("src_subsystem"), "version": r.get("content_hash"),
            "revision": revision, "project_code": r.get("project_code"),
            "source_format": r.get("source_format")}


def _obj_cmp(r: dict, revision) -> dict:
    return {"uid": r["component_id"], "component_class": r.get("component_class"),
            "segment_id": r.get("segment_id"), "inline_index": r.get("inline_index"),
            "drawing_number": r.get("drawing_number"), "version": r.get("content_hash"),
            "revision": revision, "project_code": r.get("project_code"),
            "source_format": r.get("source_format")}


def _obj_eq(r: dict, revision) -> dict:
    return {"uid": r["equipment_id"], "tag": r.get("tag"),
            "equipment_class": r.get("equipment_class"),
            "nozzle_tags": list(r.get("nozzle_ids") or []),
            "drawing_number": r.get("drawing_number"), "version": r.get("content_hash"),
            "revision": revision, "project_code": r.get("project_code"),
            "source_format": r.get("source_format")}


def run_cdc(cfg: SilverConfig, spark: Optional[SparkSession] = None) -> dict:
    """Execute Stage E and write `silver_cdc`. Returns a summary."""
    own = spark is None
    if own:
        spark = get_spark(app_name="silver-pid-cdc", enable_hive=cfg.enable_hive)
    try:
        bronze = spark.table(cfg.resolve_bronze()) if not cfg.bronze_path \
            else spark.read.format("delta").load(cfg.bronze_path)

        # --- 1) version order per drawing: rank content_hash by ingested_at ----
        ver = (bronze.select("document_number", "content_hash", "ingested_at",
                             "drawing_revision")
               .where("document_number is not null").distinct().collect())
        by_drawing: Dict[str, list] = defaultdict(list)
        rev_of: Dict[str, str] = {}
        for r in ver:
            by_drawing[r["document_number"]].append((r["ingested_at"], r["content_hash"]))
            rev_of[r["content_hash"]] = r["drawing_revision"]

        pairs: Dict[str, Dict[str, str]] = {}      # drawing -> {"new":ch, "old":ch}
        for dwg, vs in by_drawing.items():
            vs = sorted(vs, key=lambda t: (t[0] is not None, t[0]))  # oldest..newest
            if len(vs) >= 2:
                pairs[dwg] = {"new": vs[-1][1], "old": vs[-2][1]}
        wanted = {ch for p in pairs.values() for ch in p.values()}

        if not pairs:
            _write_empty(spark, cfg)
            return {"drawings_with_two_versions": 0, "deltas": 0,
                    "note": "no drawing has >= 2 Bronze versions to diff",
                    "silver_cdc": cfg.table(CDC_TABLE)}

        # --- 2) collect only the rows for the versions of interest ------------
        def grab(table, cols):
            df = spark.table(cfg.table(table)).select(*cols)
            df = df.where(F.col("content_hash").isin(list(wanted)))
            return [r.asDict(recursive=True) for r in df.collect()]

        segs = grab("silver_segments", _SEG_COLS)
        comps = [r for r in grab("silver_components", _CMP_COLS)
                 if r.get("kind") not in _NON_COMPONENT_KINDS]
        equips = grab("silver_equipment", _EQ_COLS)
        conns = grab("silver_connections", _CONN_COLS)

        def bucket(rows, ch, dwg):
            return [r for r in rows if r.get("content_hash") == ch
                    and r.get("drawing_number") == dwg]

        # --- 3) per drawing, enrich each version and diff each grain ----------
        # The piping topology is versioned at LINE grain, not at the physical
        # PipingNetworkSegment: one line is drawn as many pieces whose UIDs and
        # split points churn between revisions (silver_spec §3.5). `aggregate_lines`
        # collapses the pieces of `(drawing, seg_tag)` into one line object so a pure
        # re-split is inert; components and equipment keep their own grain.
        deltas: List[dict] = []
        for dwg, p in pairs.items():
            side: Dict[str, dict] = {}
            for tag, ch in (("old", p["old"]), ("new", p["new"])):
                s = [_obj_seg(r, rev_of.get(ch)) for r in bucket(segs, ch, dwg)]
                c = [_obj_cmp(r, rev_of.get(ch)) for r in bucket(comps, ch, dwg)]
                e = [_obj_eq(r, rev_of.get(ch)) for r in bucket(equips, ch, dwg)]
                cn = bucket(conns, ch, dwg)
                cdc.enrich_neighbors(s, c, e, cn)      # component/equipment neighbours
                side[tag] = {"line": cdc.aggregate_lines(s, c, cn),
                             "component": c, "equipment": e}
            for grain in ("line", "component", "equipment"):
                deltas.extend(cdc.diff(side["old"][grain], side["new"][grain], grain))

        # --- 4) write silver_cdc ---------------------------------------------
        run_ts = _dt.datetime.now()
        for d in deltas:
            d["cdc_id"] = _cdc_id(d)
            d["transaction_ts"] = run_ts
        _write(spark, cfg, deltas)

        return {"drawings_with_two_versions": len(pairs),
                "deltas": len(deltas), **cdc.summarize(deltas),
                "silver_cdc": cfg.table(CDC_TABLE)}
    finally:
        if own:
            spark.stop()


def _rows(deltas):
    return [tuple(d.get(c) for c in CDC_COLUMNS) for d in deltas]


def _write(spark, cfg, deltas):
    if cfg.enable_hive and cfg.silver_schema:
        spark.sql(f"CREATE DATABASE IF NOT EXISTS {cfg.silver_schema}")
    (spark.createDataFrame(_rows(deltas), schema=CDC_STRUCT)
        .write.format("delta").mode("overwrite").option("overwriteSchema", "true")
        .saveAsTable(cfg.table(CDC_TABLE)))


def _write_empty(spark, cfg):
    _write(spark, cfg, [])
