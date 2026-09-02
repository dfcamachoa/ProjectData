"""Silver Stage D — the Great-Expectations gate, as a Spark job (silver_spec §3.4).

Reads the four Silver tables that Stage A+B persisted, runs the pure declarative
evaluator (:mod:`silver.quality`) over them, and:

    1. writes the ``silver_quality`` ledger (the engineer's punch list, §4);
    2. denormalises the per-object ``quality_gate`` rollup back onto each object
       table, so a strict consumer can ``WHERE quality_gate <> 'quarantined'``
       without joining the ledger (§4);
    3. **raises** on any structural-invariant breach (oracle leak / unflagged
       derived edge) — a code bug fails the whole run, while dirty data only
       flags and flows (§3.4, decision #6).

The suite runs *observe-and-record*: results are written, GX's abort-on-fail is
off for every data-quality expectation. The evaluation itself is done in the
driver over collected rows — fine for the PoC's tens-to-low-hundreds of sheets,
and the simple kinds (not-null / in-set / regex) push down trivially if this ever
needs to scale (silver_spec §6).
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, Optional

from pyspark.sql import DataFrame, SparkSession, functions as F

from bronze.spark_session import get_spark

from .config import SilverConfig
from .quality import QualityResult, evaluate
from .quality_refdata import load_quality_refdata
from .quality_suite import TABLE_ID, default_suite
from .schema import QUALITY_COLUMNS, QUALITY_STRUCT, QUALITY_TABLE, SILVER_TABLES


class SilverQualityError(RuntimeError):
    """A Stage-D structural-invariant breach — a pipeline bug, not dirty data."""


def _rows_as_dicts(df: DataFrame) -> list:
    return [r.asDict(recursive=True) for r in df.collect()]


def _ledger_df(spark: SparkSession, result: QualityResult, run_ts) -> DataFrame:
    """Ledger rows + run-level warnings, as a DataFrame in QUALITY_STRUCT shape."""
    records = list(result.ledger)
    for w in result.warnings:                       # surface warnings in the ledger
        records.append({
            "object_id": None, "object_kind": "run", "flag": "run_warning",
            "severity": "warn", "gate": "flag", "stage": "D", "detail": w,
            "drawing_number": None, "project_code": None, "source_format": None,
            "transaction_ts": run_ts,
        })
    rows = [tuple(rec.get(c) for c in QUALITY_COLUMNS) for rec in records]
    return spark.createDataFrame(rows, schema=QUALITY_STRUCT)


def _apply_gate_rollup(spark: SparkSession, cfg: SilverConfig,
                       table: str, gates_for_table: Dict[str, str]) -> None:
    """Set the object table's ``quality_gate`` column from the rollup map, **in
    place** via a Delta UPDATE + MERGE — never a table overwrite. Overwriting a
    managed Delta table re-runs Delta's catalog-sync hook, which the embedded Hive
    metastore rejects with a noisy (but non-fatal) ``alter_table`` error; an
    in-place update touches only the changed rows and leaves the catalog alone.
    Every run first resets all rows to 'clean' (idempotent re-runs), then stamps
    the flagged / quarantined ids."""
    from delta.tables import DeltaTable

    fq = cfg.table(table)
    df = spark.table(fq)
    if "quality_gate" not in df.columns:
        return
    id_col = TABLE_ID[table]

    # reset first so a re-run never leaves a stale gate on a now-clean row
    spark.sql(f"UPDATE {fq} SET quality_gate = 'clean'")
    if not gates_for_table:
        return

    items = [(k, v) for k, v in gates_for_table.items()]
    gmap = spark.createDataFrame(items, schema=[id_col, "__gate"])
    (DeltaTable.forName(spark, fq).alias("t")
        .merge(gmap.alias("g"), f"t.`{id_col}` = g.`{id_col}`")
        .whenMatchedUpdate(set={"quality_gate": "g.__gate"})
        .execute())


def run_quality(
    cfg: SilverConfig,
    spark: Optional[SparkSession] = None,
    *,
    raise_on_fail: bool = True,
    equipment_pattern: Optional[str] = None,
    instrument_pattern: Optional[str] = None,
) -> dict:
    """Execute Stage D and write ``silver_quality`` + the gate rollup.

    Returns the evaluator summary plus ``warnings`` / ``skipped`` /
    ``hard_failures``. Raises :class:`SilverQualityError` on a structural-invariant
    breach when ``raise_on_fail`` (default) — but only *after* writing the ledger,
    so the breach is auditable.
    """
    own = spark is None
    if own:
        spark = get_spark(app_name="silver-pid-quality", enable_hive=cfg.enable_hive)

    try:
        # 1. collect the Silver tables the suite reads
        tables = {name: _rows_as_dicts(spark.table(cfg.table(name)))
                  for name in SILVER_TABLES}

        # 2. reference data (rules-as-data) + evaluate (pure, testable core)
        refdata = load_quality_refdata(
            cfg.refdata_path,
            equipment_pattern=equipment_pattern,
            instrument_pattern=instrument_pattern,
        )
        run_ts = _dt.datetime.now()
        result = evaluate(tables, default_suite(), refdata, run_ts=run_ts)

        # 3. write the ledger (always — even a hard fail is recorded)
        if cfg.enable_hive and cfg.silver_schema:
            spark.sql(f"CREATE DATABASE IF NOT EXISTS {cfg.silver_schema}")
        (_ledger_df(spark, result, run_ts)
            .write.format("delta").mode(cfg.write_mode)
            .saveAsTable(cfg.table(QUALITY_TABLE)))

        # 4. denormalise the quality_gate rollup onto each object table
        per_table: Dict[str, Dict[str, str]] = {t: {} for t in SILVER_TABLES}
        for (tbl, oid), gate in result.gates.items():
            if tbl in per_table and oid is not None:
                per_table[tbl][oid] = gate
        for table in SILVER_TABLES:
            _apply_gate_rollup(spark, cfg, table, per_table[table])

        summary = dict(result.summary)
        summary["warnings"] = list(result.warnings)
        summary["skipped"] = list(result.skipped)
        summary["quality_table"] = cfg.table(QUALITY_TABLE)

        if result.hard_failures and raise_on_fail:
            flags = sorted({h["flag"] for h in result.hard_failures})
            raise SilverQualityError(
                f"Stage-D structural invariant(s) breached: {', '.join(flags)} "
                f"({len(result.hard_failures)} occurrence(s)). This is a pipeline "
                f"bug, not dirty data — see {cfg.table(QUALITY_TABLE)} "
                f"(severity='error'). Run aborted (silver_spec §3.4/§4/§5).")
        return summary
    finally:
        if own:
            spark.stop()
