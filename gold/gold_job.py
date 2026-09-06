"""Gold job orchestration.

Wires together temporal.py (bi-temporal versioning), rdf_mapper.py (RDF/IDO
projection), rules_reference.py (declarative classification), and
oracle_guard.py (the structural invariant) into the shape a Spark driver
would call once per run, mirroring how Silver's Stage C/D jobs are written
(silver_layer_spec.md §3.3, §3.4: driver-side over the collected tables —
"right for the PoC's tens-to-low-hundreds of sheets").

This module is deliberately Spark-free and unit-tested as such
(tests/test_gold_job.py) — the same "pure, Spark-free core wrapped in a thin
Spark job" split Silver's quality/assemble stages already use. The Spark
wrapper below is a sketch (illustrative, not exercised — no Spark/Delta is
installed in this sandbox), exactly as bronze_layer_spec.md §8.2 and
silver_layer_spec.md §6 present their own Spark sketches.
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
    one segment can share it, the same non-uniqueness §3f already documents
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
# Spark wrapper sketch (illustrative — not exercised in this sandbox; no
# Spark/Delta available here). Mirrors bronze_layer_spec.md §8.2 /
# silver_layer_spec.md §6's own sketches.
# --------------------------------------------------------------------------
SPARK_SKETCH = '''
# gold_job_spark.py — driver-side orchestration, Spark used for I/O only
# (reading Silver Delta tables, writing Gold Delta tables + Turtle/N-Quads
# to object storage), never for the RDF mapping or rule logic itself —
# same "Spark owns orchestration and persistence, Python owns the algorithm"
# discipline as Bronze/Silver (medallion §2, §6).

def run(spark, silver_db="silver", gold_db="gold", fuseki_cfg=None):
    components = spark.table(f"{silver_db}.silver_components").toPandas().to_dict("records")
    segments   = spark.table(f"{silver_db}.silver_segments").toPandas().to_dict("records")
    equipment  = spark.table(f"{silver_db}.silver_equipment").toPandas().to_dict("records")
    connections = spark.table(f"{silver_db}.silver_connections").toPandas().to_dict("records")
    fluid_catalogue = load_reference_sheet("Fluid")
    boundary_rows = load_reference_sheet("Boundary")
    drawing_lineage = load_bronze_lineage(spark)

    inputs = GoldInputs(components, segments, equipment, connections,
                         fluid_catalogue, boundary_rows, drawing_lineage)

    ds = build_rdf_dataset(inputs)                       # driver-side; dataset sizes are
                                                            # tens-to-low-hundreds of sheets (PoC scale)

    # Bi-temporal versioning now consumes Silver Stage E's silver_cdc table
    # directly (silver_cdc.py) — the primary path, not the snapshot-diff
    # fallback (build_bitemporal_tables), now that Stage E is built.
    cdc_events = load_silver_cdc_events(spark, silver_db)  # -> list[SilverCdcEvent]
    previous = read_previous_gold_rows(spark, gold_db)     # {} on first run
    bitemporal, anomalies = build_bitemporal_tables_from_cdc(previous, cdc_events)

    write_gold_delta_tables(spark, gold_db, bitemporal)    # one table per object kind, append-only,
                                                            # "never delete, close the interval"
    if anomalies:
        write_gold_anomalies_table(spark, gold_db, anomalies)  # this run's punch list — Stage D's
                                                                # silver_quality precedent, not a crash
    if fuseki_cfg is not None:
        for graph_uri in (GRAPH_MASTERDATA, GRAPH_REFDATA, GRAPH_ORACLE, GRAPH_RESULTS):
            push_named_graph(fuseki_cfg, graph_uri, ds.to_turtle(graph_uri))
    else:
        write_nquads_to_object_storage(ds.to_nquads())      # PoC fallback: no Fuseki reachable locally
'''
