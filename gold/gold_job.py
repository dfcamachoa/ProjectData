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
from .temporal import GoldRow, current_truth, diff_snapshots


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
    # Harvest real classification off the Equipment-kind component duplicate
    # BEFORE skipping it, below: silver/reconstruct.py's silver_equipment row
    # always carries equipment_class=None ("class enrichment: later"), but
    # real data confirmed 2026-09-11 that this "classless" component-kind
    # sibling frequently carries a genuine class (e.g. HeatExchangers,
    # VerticalDrums) that would otherwise have nowhere to land. Matched by
    # equipment TAG, not raw id -- the same anchor identity the dedup below
    # already relies on ("the same physical item").
    equipment_class_by_tag = {
        comp["tag"]: comp["component_class"]
        for comp in inputs.components
        if comp.get("kind") == "Equipment" and comp.get("tag") and comp.get("component_class")
    }
    for comp in inputs.components:
        if comp.get("kind") == "Equipment":
            # silver/reconstruct.py's component loop only special-cases
            # kind=="Segment" -- every real Equipment element also lands in
            # silver_components, duplicating the SAME physical item
            # map_equipment already maps correctly below from
            # inputs.equipment (its real classification, when this row
            # carries one, is harvested above rather than lost). Confirmed
            # 2026-09-10 against the Rev C/D narrative: every
            # component_class=None row was kind=="Equipment", and every one
            # of those ids also appears in inputs.equipment. This legacy
            # Silver-direct path predates the "report, don't silently skip"
            # discipline `build_rdf_dataset_from_gold_objects` below now
            # follows for this same case -- prefer that function for a real
            # orchestration run.
            continue
        rdf_mapper.map_component(ds, comp)
    for seg in inputs.segments:
        rdf_mapper.map_segment(ds, seg)
    for eq in inputs.equipment:
        merged_eq = dict(eq)
        if not merged_eq.get("equipment_class"):
            merged_eq["equipment_class"] = equipment_class_by_tag.get(eq.get("tag"))
        rdf_mapper.map_equipment(ds, merged_eq)
    for conn in inputs.connections:
        rdf_mapper.map_connection(ds, conn)
    assert_oracle_confined(ds)
    return ds


# Anchor-id -> the identity key each rdf_mapper.py mapper function expects to
# find in its input dict (map_component reads comp["component_id"], etc.) —
# `temporal.GoldRow` stores that identity separately, as `anchor_id`, from
# the engineering attributes in `attrs` (temporal.py), so
# `_gold_row_to_mapper_dict` below re-injects it under the right name per
# object_kind before calling a mapper.
#
# "line" is handled separately, below, via `rdf_mapper.map_line` rather than
# through this table: Gold's bi-temporal grain for piping is "line"
# (silver_cdc.py's `OBJECT_KINDS`, an aggregated, multi-piece line), not
# `rdf_mapper.map_segment`'s "segment" (one physical `silver_segments`
# piece), and a line's OWN attrs are distinct-value SETS across its pieces
# (`spark_bridge.aggregate_line_attrs`) — not a single segment's scalar
# fields — so a `"line"` GoldRow cannot be handed to `map_component`-style
# one-mapper-one-node handling the way component/equipment/connection are.
# `map_line` asserts the Line node from those aggregated fields AND, from
# the `"pieces"` each line's attrs now carry (`spark_bridge.py::
# _segment_piece_attrs`), a real child `map_segment` node per physical
# piece with its own un-aggregated attributes — this is what closes
# `medallion_rdf_ido_strategy_mapping.md` / `gold_layer_spec.md` risk #10
# for real, rather than the explicit-skip this function used before.
_GOLD_KIND_TO_MAPPER = {
    "component": (rdf_mapper.map_component, "component_id"),
    "equipment": (rdf_mapper.map_equipment, "equipment_id"),
    "connection": (rdf_mapper.map_connection, "connection_id"),
}


def _gold_row_to_mapper_dict(row: GoldRow, id_key: str) -> dict:
    """Reconstitutes the dict shape `rdf_mapper.py`'s per-kind mappers
    expect from one bi-temporal `GoldRow`: `row.attrs` plus the object's
    identity re-injected under the mapper's own key name, plus the two time
    axes threaded through under the `_valid_from`/`_valid_to`/`_tx_from`/
    `_tx_to` keys `rdf_mapper._assert_temporal` reads (see that function)."""
    obj = dict(row.attrs)
    obj[id_key] = row.anchor_id
    obj["_valid_from"] = row.valid_from
    obj["_valid_to"] = row.valid_to
    obj["_tx_from"] = row.tx_from
    obj["_tx_to"] = row.tx_to
    return obj


def build_rdf_dataset_from_gold_objects(
    gold_rows_by_kind: dict,   # {object_kind: [GoldRow, ...]} -- gold_objects' full accumulated history
    fluid_catalogue: list,      # refdata Fluid sheet rows
    boundary_rows: list,        # refdata Boundary sheet rows
    as_of_valid: Optional[date] = None,
    as_of_tx: Optional[datetime] = None,
) -> "tuple[Dataset, list[str], list[str]]":
    """Stage-3 projection sourced from the bi-temporal `gold_objects` table
    (via `temporal.current_truth`), not raw Silver rows — the design
    decided and logged in `gold_layer_spec.md`'s "Decided ... (cont.)"
    entry: the RDF/IDO graph pushed to Fuseki takes `gold_objects` as its
    input, as the second of two sequential orchestration tasks (`run_gold`
    writes `gold_objects`; a caller then passes that same run's
    `gold_rows_by_kind` — or a fresh read of the table — into this function
    and pushes the result to Fuseki), never an independent consumer of
    Silver. That sequencing means a Gold RDF snapshot and a Gold Delta-table
    snapshot can never diverge onto two different bi-temporal views of the
    same run.

    Unlike `build_rdf_dataset` (still Silver-sourced, kept for the fixtures/
    tests that predate `gold_objects`), every mapped component/equipment/
    connection node here also carries the four bi-temporal predicates
    (`vocab.P_VALID_FROM`/`P_VALID_TO`/`P_TX_FROM`/`P_TX_TO` — defined since
    the module's first cut, never asserted until now) sourced from that
    object's own `GoldRow` — see `rdf_mapper.py::_assert_temporal`.

    `as_of_valid`/`as_of_tx` pass straight through to `current_truth` (both
    `None` -> ordinary current truth, the common case; either pinned ->
    a point-in-time projection, e.g. rebuilding the graph as Fuseki should
    have looked as of a past revision or a past run).

    Returns `(dataset, lines_without_piece_detail, equipment_duplicate_components)`.

    Every current-truth `"line"`-kind `GoldRow` IS projected — as a
    `pidsys:Line` node plus one `pidsys:PipingSegment` child per physical
    piece its `attrs["pieces"]` carries (`rdf_mapper.map_line`). The second
    return element is the anchor ids of lines that had NO piece detail to
    project (an empty or missing `"pieces"` key — e.g. a `gold_objects` row
    written before this capability existed, or a caller whose Silver
    segment rows didn't cover that drawing): the Line node itself is still
    asserted correctly, just with zero children, and this list is how a
    caller notices that rather than the graph silently looking sparser than
    it should, following this project's "observe and record" discipline
    (the `silver_quality` / `gold_anomalies` precedent).

    The third return element is the anchor ids of `"component"`-kind
    `GoldRow`s that were NOT mapped because `attrs["kind"] == "Equipment"` —
    `silver/reconstruct.py` puts every real Equipment element into
    `silver_components` too, duplicating the exact same physical item the
    `"equipment"` kind loop below already maps correctly via
    `map_equipment`. Mapping it again here as a second component node would
    assert one physical item as two different RDF individuals; skipping it —
    and reporting the skip, rather than silently dropping it — is the fix.
    (Earlier documentation here claimed this duplicate row is always
    classless, since `ComponentClass` is supposedly never set on an
    Equipment tag — confirmed 2026-09-10 against the synthetic Rev C/D
    narrative fixture only. Real production data checked 2026-09-11
    contradicts that as a universal claim: real Equipment-kind component
    rows frequently DO carry a genuine `component_class`, e.g.
    `HeatExchangers`, `VerticalDrums`. That value is not lost — it is
    harvested by equipment tag, above, and passed into `map_equipment` as
    `equipment_class` before this row is skipped, since `silver_equipment`'s
    own `equipment_class` field is always `None` otherwise.) `vocab.
    C_UNCLASSIFIED_COMPONENT` (`rdf_mapper.map_component`'s own fallback) is
    reserved for a genuinely unclassified NON-Equipment component, should
    real data turn one up.
    """
    ds = Dataset()
    rdf_mapper.declare_ontology_skeleton(ds)
    rdf_mapper.map_fluid_catalogue(ds, fluid_catalogue)
    rdf_mapper.map_boundary_sets(ds, boundary_rows)

    # Current-truth rows per kind, computed once up front (rather than inline
    # in the loop below) so the Equipment-classification harvest below can
    # see the "component" kind's rows before that kind is (mostly) skipped.
    current_by_kind = {
        kind: current_truth(gold_rows_by_kind.get(kind, []), as_of_valid=as_of_valid, as_of_tx=as_of_tx)
        for kind in _GOLD_KIND_TO_MAPPER
    }

    # Harvest real equipment classification off the "component"-kind
    # Equipment-duplicate rows this loop is about to skip, below -- see
    # rdf_mapper.map_equipment's docstring and gold_layer_spec.md's
    # 2026-09-11 entry: silver_equipment's own equipment_class is always
    # None, and real data confirmed this "classless" sibling frequently
    # carries the item's genuine classification (e.g. HeatExchangers).
    # Matched by equipment tag -- the same anchor identity the dedup itself
    # already relies on ("the same physical item").
    equipment_class_by_tag = {
        row.attrs.get("tag"): row.attrs.get("component_class")
        for row in current_by_kind["component"]
        if row.attrs.get("kind") == "Equipment" and row.attrs.get("tag") and row.attrs.get("component_class")
    }

    equipment_duplicate_components = []
    for kind, (mapper_fn, id_key) in _GOLD_KIND_TO_MAPPER.items():
        for row in current_by_kind[kind]:
            if kind == "component" and row.attrs.get("kind") == "Equipment":
                equipment_duplicate_components.append(row.anchor_id)
                continue
            mapper_dict = _gold_row_to_mapper_dict(row, id_key)
            if kind == "equipment" and not mapper_dict.get("equipment_class"):
                harvested = equipment_class_by_tag.get(mapper_dict.get("tag"))
                if harvested:
                    mapper_dict["equipment_class"] = harvested
            mapper_fn(ds, mapper_dict)

    lines_without_piece_detail = []
    line_rows = current_truth(gold_rows_by_kind.get("line", []), as_of_valid=as_of_valid, as_of_tx=as_of_tx)
    for row in line_rows:
        line_dict = _gold_row_to_mapper_dict(row, "line_id")
        rdf_mapper.map_line(ds, line_dict)
        if not line_dict.get("pieces"):
            lines_without_piece_detail.append(row.anchor_id)

    assert_oracle_confined(ds)
    return ds, lines_without_piece_detail, equipment_duplicate_components


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
# The RDF/IDO projection and the Fuseki push are a separate concern from the
# bi-temporal Delta tables above and do not yet have their own Spark
# wrapper — pushing named graphs to Fuseki or writing N-Quads to object
# storage is orchestration a caller adds around this module's already-real,
# already-tested output, following the same "Spark owns I/O, Python owns
# the algorithm" split as run_gold.
#
# As of 2026-09-09 (orchestration_layer_spec.md's revised DAG,
# gold_layer_spec.md's "Decided ... (cont.)" entry) that projection is
# `build_rdf_dataset_from_gold_objects`, not `build_rdf_dataset` — it
# consumes `gold_objects`' own bi-temporal current truth (via
# `temporal.current_truth`), so it runs as a SECOND, sequential
# orchestration task after `run_gold`, reading the very `gold_objects` table
# that task just wrote:
#
#     from gold.spark_job import run_gold
#     from gold.gold_job import build_rdf_dataset_from_gold_objects
#     from gold.spark_bridge import dict_to_gold_row
#     import json
#
#     summary = run_gold(cfg, spark=spark)  # task 1: writes gold.gold_objects
#
#     rows = spark.table(f"{cfg.gold_schema}.gold_objects").collect()
#     gold_rows_by_kind: dict = {}
#     for r in rows:
#         d = r.asDict(recursive=True)
#         gold_rows_by_kind.setdefault(d["object_kind"], []).append(
#             dict_to_gold_row(d, attrs=json.loads(d["attrs_json"]))
#         )
#     ds, lines_without_piece_detail, equipment_dupes = build_rdf_dataset_from_gold_objects(  # task 2
#         gold_rows_by_kind, fluid_catalogue_rows, boundary_rows,
#     )
#     # ... push ds's named graphs to Fuseki (fuseki_client.py) ...
#
# `build_rdf_dataset` (Silver-direct, no temporal predicates) stays as-is
# for the fixtures/tests that predate `gold_objects` — it is not the path a
# real orchestration run takes any more.
# --------------------------------------------------------------------------
