"""Gold job orchestration.

Wires together temporal.py (bi-temporal versioning), rdf_mapper.py (RDF/IDO
projection), rules_reference.py (declarative classification), and
oracle_guard.py (the structural invariant) into the shape a Spark driver
would call once per run, mirroring how Silver's Stage C/D jobs are written
(silver_layer_spec.md §3.3, §3.4: driver-side over the collected tables —
"right for the PoC's tens-to-low-hundreds of sheets").

This module is Spark-free, but as of 2026-09-09 it is no longer
dependency-free: `build_rdf_dataset` calls into `rdf_mapper.py`, which
builds on `rdf_model.py`'s now-real `rdflib`-backed `Dataset` (see that
module's docstring for why). `build_bitemporal_tables_from_cdc` /
`build_bitemporal_tables` stay pure — they only touch `temporal.py` /
`silver_cdc.py`, neither of which import `rdf_model`. `tests/test_gold_job.py`
needs `rdflib` installed to run as a result; the same "pure, Spark-free
core wrapped in a thin Spark job" split Silver's quality/assemble stages
use still holds, it's just that "pure" here means "no Spark", not
"no dependencies at all" anymore for the RDF half.

The Spark wrapper is no longer a sketch: `gold/spark_job.py::run_gold` is a
real, runnable job — `gold/config.py::GoldConfig` for what varies,
`gold/spark_bridge.py` for the pure dict-in/dict-out resolution logic (its
own unit-tested module), `gold/schema.py` for the Delta table shapes — built
2026-09-06 to mirror `bronze/ingest.py` / `silver/cdc_job.py` line for line,
per the user's own environment: Bronze and Silver already run on real Spark
there, and Gold now follows the identical `get_spark()` / `.collect()` /
`.write.format("delta")` discipline instead of the pandas-based notebook
glue (`append_gold_cells.py`'s Bridge 1/2) an earlier prototype used. It is
untested AS A SPARK JOB in this sandbox (no pyspark/Delta installed here,
so `gold/schema.py` and `gold/spark_job.py` cannot even import — confirmed
they fail with a plain `ModuleNotFoundError`, not a broken import chain,
and `gold/__init__.py` does not import them eagerly, so the rest of this
package and its test suite are unaffected either way); it should be
exercised in the user's own Spark environment before being trusted as
Bronze/Silver's real jobs already are there.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

from . import rdf_mapper
from .oracle_guard import assert_oracle_confined
from .rdf_model import Dataset
from .silver_cdc import CdcAnomaly, SilverCdcEvent, apply_silver_cdc_events_tolerant
from .temporal import GoldRow, diff_snapshots


@dataclass
class GoldInputs:
    """Shape of what a Gold run consumes — one Silver snapshot for a run,
    plus the refdata sheets and Bronze lineage Gold needs for the two time
    axes (medallion §4)."""
    components: list      # silver_components rows
    segments: list         # silver_segments rows
    equipment: list        # silver_equipment rows
    connections: list      # silver_connections rows
    fluid_catalogue: list  # refdata Fluid sheet rows
    boundary_rows: list    # refdata Boundary sheet rows
    drawing_lineage: dict  # {drawing_number: {"ingested_at":..., "drawing_revision_date":...}}


def build_rdf_dataset(inputs: GoldInputs) -> Dataset:
    """Pure Stage-3 projection [strategy §10 step 3]: Silver's canonical
    objects -> RDF/IDO across the four named graphs. Raises OracleLeakage
    (via assert_oracle_confined) if any mapper accidentally routes an oracle
    field outside graph:oracle — the Gold-layer twin of Silver's
    `oracle_confined` invariant."""
    ds = Dataset()
    rdf_mapper.declare_ontology_skeleton(ds)
    rdf_mapper.map_fluid_catalogue(ds, inputs.fluid_catalogue)
    rdf_mapper.map_boundary_sets(ds, inputs.boundary_rows)
    for comp in inputs.components:
        rdf_mapper.map_component(ds, comp)
    for seg in inputs.segments:
        rdf_mapper.map_segment(ds, seg)
    for eq in inputs.equipment:
        rdf_mapper.map_equipment(ds, eq)
    for conn in inputs.connections:
        rdf_mapper.map_connection(ds, conn)
    assert_oracle_confined(ds)
    return ds


def build_bitemporal_tables_from_cdc(
    previous_gold_rows: dict,          # {object_kind: [GoldRow, ...]} from the prior run, {} on first load
    cdc_events: list,                   # SilverCdcEvent list — the silver_cdc table for this run
) -> "tuple[dict, list[CdcAnomaly]]":
    """The PRIMARY Gold path now that Silver Stage E is built (see
    `silver_cdc.py`'s module docstring). One `apply_delta` call per
    `silver_cdc` row, driven by Silver's own New/Modified/Deleted
    classification and its anchor-match identity — never a source UID, and
    never Gold re-deriving whether something changed from a snapshot diff.

    Uses the *tolerant* application (`apply_silver_cdc_events_tolerant`), not
    the strict one: a real Stage E feed's `anchor` is a bucket key for
    components (silver_layer_spec.md §3.5 — multiple same-class siblings on
    one line can share it, the same non-uniqueness §3f already documents
    for `seg_tag`), so one batch legitimately CAN carry two simultaneous
    events for one anchor. Returns `(bitemporal_tables, anomalies)` — the
    second element is empty in the common case and, when not, is this run's
    punch list (silver_cdc.py's `CdcAnomaly`), not a crash.
    """
    return apply_silver_cdc_events_tolerant(previous_gold_rows, cdc_events)


def build_bitemporal_tables(
    inputs: GoldInputs,
    previous_gold_rows: dict,   # {object_kind: [GoldRow, ...]} from the prior run, {} on first load
    run_tx_from: datetime,
) -> dict:
    """DEPRECATED primary path, kept as the documented fallback for a Silver
    build that has not run Stage E (or a one-off backfill with only
    snapshots to compare) — see `silver_cdc.py`'s module docstring. Prefer
    `build_bitemporal_tables_from_cdc` now that Silver's `silver_cdc` table
    exists; this function's anchor id is still whatever id the Silver
    *snapshot* row carries (component_id / segment_id / ...), not Stage E's
    true anchor-match identity, so it remains coarser than the CDC path
    (silver_layer_spec.md §3.5: a delete+recreate can misclassify here in a
    way Stage E's own acceptance test proves it does not).
    """
    object_rows = {
        "component": {c["component_id"]: c for c in inputs.components},
        "segment": {s["segment_id"]: s for s in inputs.segments},
        "equipment": {e["equipment_id"]: e for e in inputs.equipment},
        "connection": {c["connection_id"]: c for c in inputs.connections},
    }
    out = {}
    for kind, rows_by_id in object_rows.items():
        valid_from = _valid_from_for_kind(kind, rows_by_id, inputs.drawing_lineage)
        out[kind] = diff_snapshots(
            current_rows=rows_by_id,
            previous_gold_rows=previous_gold_rows.get(kind, []),
            object_kind=kind,
            valid_from=valid_from,
            tx_from=run_tx_from,
        )
    return out


def _valid_from_for_kind(kind: str, rows_by_id: dict, drawing_lineage: dict) -> date:
    """All objects in one Gold run share one valid-time origin per drawing in
    the common case (a whole drawing revision lands together); this
    prototype takes the run's latest known drawing_revision_date across the
    batch as a conservative single valid_from. A production Gold job keys
    this per-object off each object's own drawing_number instead — the
    lineage dict already carries that mapping — but the single-drawing
    fixtures this package tests against make either choice equivalent."""
    dates = [v["drawing_revision_date"] for v in drawing_lineage.values() if v.get("drawing_revision_date")]
    if not dates:
        raise ValueError("drawing_lineage has no drawing_revision_date to seed valid_from")
    return max(dates)


# --------------------------------------------------------------------------
# The real Spark wrapper for the bi-temporal side of Gold (this module's
# build_rdf_dataset / build_bitemporal_tables_from_cdc above) now lives in
# gold/spark_job.py::run_gold — see that module's docstring for the exact
# read/apply/write sequence, and gold/config.py::GoldConfig for what varies
# between environments. Usage from a notebook or driver script, once pyspark
# is available (it is not in this sandbox):
#
#     from gold.config import GoldConfig
#     from gold.spark_job import run_gold
#     summary = run_gold(GoldConfig(bronze_table="bronze.pid_documents",
#                                    silver_schema="silver", gold_schema="gold"))
#
# `run_gold` reads Bronze (for drawing_revision_date), the current
# silver_cdc batch plus whichever per-grain Silver tables that batch
# touches, and Gold's OWN previous gold_objects table (its accumulated
# bi-temporal history — silver_cdc itself is overwritten each Silver run,
# not accumulated), then writes gold_objects (full rewrite — a lossless
# re-serialization of every row-version, open and closed) and, when this
# run produced any, gold_anomalies (appended).
#
# The RDF/IDO projection (build_rdf_dataset) and the Fuseki push are a
# separate concern from the bi-temporal Delta tables above and do not yet
# have their own Spark wrapper — pushing named graphs to Fuseki or writing
# N-Quads to object storage is orchestration a caller adds around
# build_rdf_dataset's already-real, already-tested output, following the
# same "Spark owns I/O, Python owns the algorithm" split as run_gold.
# --------------------------------------------------------------------------
