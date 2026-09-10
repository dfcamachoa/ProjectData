"""Canonical objects (Silver/Gold rows) -> RDF/IDO projection.

Implements medallion_rdf_ido_strategy_mapping.md §5's three correctness notes:

  1. Domain component classes are `rdfs:subClassOf` an IDO *foundational*
     class (never a bare, unconfirmed `ido:FunctionalObject`), with the real
     RDL resolution left as an explicit pending field (`vocab.P_RDL_URI_PENDING`)
     rather than invented.
  2. Every Connection is a reified node carrying `derived` — never a bare
     `isConnectedTo` triple — so inferred topology is never asserted with the
     same authority as source topology.
  3. Flow direction is materialised as `pidsys:flowsTo`, separate from the
     symmetric `pidsys:isConnectedTo`, oriented by `flow_sense` (silver
     table's four-state enum: none/forward/reverse/both).

Named-graph layout (medallion §5): `graph:masterdata`, `graph:refdata`,
`graph:oracle` (rule-invisible — see oracle_guard.py), `graph:results`.
"""
from __future__ import annotations

from typing import Iterable, Optional

from . import vocab as v
from .rdf_model import Dataset, L, U


def _resource(kind: str, obj_id: str):
    return U(v.uri(v.PIDSYS + kind.lower() + "/", obj_id))


def _assert_temporal(ds: Dataset, node, obj: dict, graph: str) -> None:
    """Asserts the four bi-temporal predicates (vocab.py's already-defined
    but, until now, never-used `P_VALID_FROM`/`P_VALID_TO`/`P_TX_FROM`/
    `P_TX_TO`) on `node` when `obj` carries them under the `_valid_from` /
    `_valid_to` / `_tx_from` / `_tx_to` keys.

    `gold_job.py::build_rdf_dataset_from_gold_objects` is the only caller
    that populates these keys today — it re-projects a `temporal.GoldRow`'s
    two time axes onto the dict shape each mapper below already expects
    (see that function's own docstring for why the identity/time fields
    have to be re-injected rather than read off `GoldRow` directly). A
    plain Silver-shaped dict, as `build_rdf_dataset`'s older, Silver-direct
    path still passes, carries none of these keys, so this is a no-op for
    that path — its RDF output is byte-for-byte unchanged by this addition.
    Only `None` is treated as "absent"; a real, still-open interval end
    (`valid_to`/`tx_to`) is legitimately `None` and correctly never
    asserted, which is what "current" means bi-temporally (temporal.py).
    """
    if obj.get("_valid_from") is not None:
        ds.add(node, U(v.P_VALID_FROM), L(obj["_valid_from"]), graph)
    if obj.get("_valid_to") is not None:
        ds.add(node, U(v.P_VALID_TO), L(obj["_valid_to"]), graph)
    if obj.get("_tx_from") is not None:
        ds.add(node, U(v.P_TX_FROM), L(obj["_tx_from"]), graph)
    if obj.get("_tx_to") is not None:
        ds.add(node, U(v.P_TX_TO), L(obj["_tx_to"]), graph)


def declare_ontology_skeleton(ds: Dataset) -> None:
    """The handful of class/subclass triples every projection needs: domain
    piping classes rooted under an IDO foundational class. Idempotent."""
    RDFS_SUBCLASSOF = U("http://www.w3.org/2000/01/rdf-schema#subClassOf")
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    OWL_CLASS = U("http://www.w3.org/2002/07/owl#Class")
    for cls in (
        v.C_DOCUMENT,
        v.C_STARTUP_PACKAGE,
        v.C_PROCESS_UNIT,
        v.C_PIPELINE_SYSTEM,
        v.C_SUBLINE,
        v.C_LINE,
        v.C_PIPING_SEGMENT,
        v.C_PIPING_COMPONENT,
        v.C_UNCLASSIFIED_COMPONENT,
        v.C_EQUIPMENT,
        v.C_NOZZLE,
        v.C_CONNECTION,
        v.C_FLUID,
        v.C_BOUNDARY_ROLE,
        v.C_COMMISSIONING_SYSTEM,
    ):
        ds.add(U(cls), RDF_TYPE, OWL_CLASS, v.GRAPH_MASTERDATA)
    # The one real IDO alignment this project asserts: piping components,
    # equipment, lines, and segments ARE physical objects (a foundational
    # IDO class), never more. C_PIPING_SEGMENT/C_LINE were not aligned here
    # before this addition -- an oversight fixed alongside introducing Line,
    # since a segment or a line is exactly as much a physical object as a
    # component (gold_layer_spec.md §4.3).
    ds.add(U(v.C_PIPING_COMPONENT), RDFS_SUBCLASSOF, U(v.IDO_PHYSICAL_OBJECT), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_EQUIPMENT), RDFS_SUBCLASSOF, U(v.IDO_PHYSICAL_OBJECT), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_NOZZLE), RDFS_SUBCLASSOF, U(v.IDO_PHYSICAL_OBJECT), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_PIPING_SEGMENT), RDFS_SUBCLASSOF, U(v.IDO_PHYSICAL_OBJECT), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_LINE), RDFS_SUBCLASSOF, U(v.IDO_PHYSICAL_OBJECT), v.GRAPH_MASTERDATA)
    # A component with no component_class (real Silver data: confirmed
    # 2026-09-10 -- some real components carry component_class=None) gets
    # this honest, project-defined class instead of crashing map_component --
    # still rooted under C_PIPING_COMPONENT, so it is exactly as much an IDO
    # physical object as every other component, just unclassified.
    ds.add(U(v.C_UNCLASSIFIED_COMPONENT), RDFS_SUBCLASSOF, U(v.C_PIPING_COMPONENT), v.GRAPH_MASTERDATA)


def map_component(ds: Dataset, comp: dict, rdl_uri: Optional[str] = None) -> None:
    """comp: silver_components-shaped dict — component_id, component_class,
    tag, segment_id, is_valve, drawing_number, ... (silver_layer_spec.md §4).

    `component_class` is treated as optional as of 2026-09-10: a real
    Silver `silver_components` row can carry `component_class=None` (a
    real-data finding, not a synthetic-fixture gap), and
    `gold/spark_bridge.py::build_events` additionally drops any
    `None`-valued field entirely before it reaches `gold_objects` — so a
    caller here can see either `None` or the key simply absent. Either way
    this asserts the honest `vocab.C_UNCLASSIFIED_COMPONENT` type instead
    of crashing (this project's "observe and record" discipline, not a
    silent guess at a class), and skips the `P_COMPONENT_CLASS` literal and
    the RDL-pending flag, both of which only mean something for a real
    domain class.
    """
    RDFS_SUBCLASSOF = U("http://www.w3.org/2000/01/rdf-schema#subClassOf")
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("component", comp["component_id"])
    component_class = comp.get("component_class")
    if component_class:
        domain_cls = U(v.component_class_uri(component_class))
        ds.add(domain_cls, RDFS_SUBCLASSOF, U(v.C_PIPING_COMPONENT), v.GRAPH_MASTERDATA)
    else:
        domain_cls = U(v.C_UNCLASSIFIED_COMPONENT)
    ds.add(node, RDF_TYPE, domain_cls, v.GRAPH_MASTERDATA)
    if comp.get("tag"):
        ds.add(node, U(v.P_TAG), L(comp["tag"]), v.GRAPH_MASTERDATA)
    if component_class:
        ds.add(node, U(v.P_COMPONENT_CLASS), L(component_class), v.GRAPH_MASTERDATA)
    if comp.get("segment_id"):
        ds.add(node, U(v.P_PART_OF), _resource("segment", comp["segment_id"]), v.GRAPH_MASTERDATA)
    if component_class:
        if rdl_uri:
            ds.add(domain_cls, U("http://www.w3.org/2002/07/owl#sameAs"), U(rdl_uri), v.GRAPH_MASTERDATA)
        else:
            # honest placeholder — the class awaits RDL resolution, not asserted as final
            ds.add(domain_cls, U(v.P_RDL_URI_PENDING), L(True), v.GRAPH_MASTERDATA)
    _assert_temporal(ds, node, comp, v.GRAPH_MASTERDATA)


def map_segment(ds: Dataset, seg: dict) -> None:
    """seg: silver_segments-shaped dict. The two oracle columns
    (src_turnover / src_subsystem) are routed to graph:oracle ONLY — see
    silver_layer_spec.md §5, and never touch graph:masterdata here.

    A segment's own engineering attributes (unit, diameter, piping
    materials class, the insulation triple) are asserted when present —
    each one optional, since not every caller's dict carries every field
    (a segment nested under a Line via `map_line` below carries whatever
    `spark_bridge.py::_segment_piece_attrs` captured for that piece; the
    plain Silver-direct path passes a full `silver_segments` row). These
    were never asserted before this addition — an omission surfaced when
    Gold's Line/Segment RDF projection was built, not something specific
    to that feature: a segment genuinely has these attributes regardless
    of whether a Line node exists at all.
    """
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("segment", seg["segment_id"])
    ds.add(node, RDF_TYPE, U(v.C_PIPING_SEGMENT), v.GRAPH_MASTERDATA)
    ds.add(node, U(v.P_TAG), L(seg.get("seg_tag", "")), v.GRAPH_MASTERDATA)
    ds.add(node, U(v.P_FLUID_CODE), L(seg["fluid"]), v.GRAPH_MASTERDATA)
    if seg.get("subline_tag"):
        ds.add(node, U(v.P_PART_OF), _resource("subline", seg["subline_tag"]), v.GRAPH_MASTERDATA)
    elif seg.get("pns_tag"):
        ds.add(node, U(v.P_PART_OF), _resource("pipeline_system", seg["pns_tag"]), v.GRAPH_MASTERDATA)
    if seg.get("_line_id"):
        # This piece's Line, when mapped via map_line below — a SEPARATE
        # partOf edge from the physical subline/pipeline-system containment
        # above; a segment is simultaneously part of the physical hierarchy
        # and part of Gold's own line-grain versioning unit (gold_layer_spec.md §4.4).
        ds.add(node, U(v.P_PART_OF), _resource("line", seg["_line_id"]), v.GRAPH_MASTERDATA)

    for attr_key, pred in (
        ("unit", v.P_UNIT),
        ("diameter", v.P_DIAMETER),
        ("piping_materials_class", v.P_PIPING_MATERIALS_CLASS),
        ("insul_type", v.P_INSULATION_TYPE),
        ("insul_purpose", v.P_INSULATION_PURPOSE),
        ("insul_thick", v.P_INSULATION_THICKNESS),
    ):
        if seg.get(attr_key) is not None:
            ds.add(node, U(pred), L(seg[attr_key]), v.GRAPH_MASTERDATA)

    if seg.get("src_turnover") is not None:
        ds.add(node, U(v.P_SRC_TURNOVER_SYSTEM), L(seg["src_turnover"]), v.GRAPH_ORACLE)
    if seg.get("src_subsystem") is not None:
        ds.add(node, U(v.P_SRC_SUBSYSTEM), L(seg["src_subsystem"]), v.GRAPH_ORACLE)
    _assert_temporal(ds, node, seg, v.GRAPH_MASTERDATA)


def map_line(ds: Dataset, line: dict) -> None:
    """line: a `gold_objects` `"line"`-kind `GoldRow` reconstituted into a
    dict (`gold_job.py::_gold_row_to_mapper_dict`) — `line_id` (Gold's own
    anchor_id, `f"{drawing_number}|{seg_tag}"` per `silver_cdc.py`), the
    aggregated per-field distinct-value tuples (`fluid`/`unit`/`diameter`/...,
    each a tuple since a line's pieces can legitimately disagree —
    `spark_bridge.py::aggregate_line_attrs`), `piece_count`,
    `line_attr_inconsistent`, and — the reason this function can build real
    child nodes instead of a placeholder — `"pieces"`: the list of each
    physical segment's OWN, un-reduced scalar attributes as recorded at
    this Line version's CDC-aggregation time
    (`spark_bridge.py::_segment_piece_attrs`).

    Individual physical segments are NOT independently bi-temporally
    tracked in `gold_objects` — their own ids/split-points churn across
    revisions, which is the very reason Silver moved piping CDC to line
    grain in the first place (`silver_layer_spec.md` §3.5,
    `temporal.py`/`gold_layer_spec.md` §3.2). So a piece's identity and
    attributes are only ever known "as of" whichever Line version currently
    holds them — which is why they ride along inside the Line's own
    `gold_objects` row (as `attrs["pieces"]`) rather than getting their own
    `gold_objects` entry, and why each mapped Segment inherits the SAME
    four bi-temporal timestamps as its parent Line (see below) rather than
    carrying independent ones of its own.

    `pieces` can legitimately be `[]` — a `gold_objects` row written before
    this feature existed carries no such key, and a caller whose
    `segment_rows` doesn't cover this drawing produces the aggregated
    fields with an empty piece list. Either way this asserts the Line node
    correctly and simply emits zero child Segment nodes; the caller
    (`gold_job.py::build_rdf_dataset_from_gold_objects`) reports which
    lines had no piece detail rather than treating it as an error.
    """
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("line", line["line_id"])
    ds.add(node, RDF_TYPE, U(v.C_LINE), v.GRAPH_MASTERDATA)
    # The Line's own aggregated fields (spark_bridge.LINE_ENGINEERING_ATTR_FIELDS)
    # are each a tuple of distinct values across its pieces — asserted as
    # zero-or-more triples per predicate rather than picking one, so an
    # inconsistent line (line_attr_inconsistent=True below) is visible on
    # the Line node itself, not only recoverable by walking its children.
    for attr_key, pred in (
        ("fluid", v.P_FLUID_CODE),
        ("unit", v.P_UNIT),
        ("diameter", v.P_DIAMETER),
        ("piping_materials_class", v.P_PIPING_MATERIALS_CLASS),
        ("insul_type", v.P_INSULATION_TYPE),
        ("insul_purpose", v.P_INSULATION_PURPOSE),
        ("insul_thick", v.P_INSULATION_THICKNESS),
    ):
        for value in line.get(attr_key) or ():
            ds.add(node, U(pred), L(value), v.GRAPH_MASTERDATA)
    if line.get("piece_count") is not None:
        ds.add(node, U(v.P_PIECE_COUNT), L(int(line["piece_count"])), v.GRAPH_MASTERDATA)
    if line.get("line_attr_inconsistent") is not None:
        ds.add(node, U(v.P_LINE_ATTR_INCONSISTENT), L(bool(line["line_attr_inconsistent"])), v.GRAPH_MASTERDATA)
    _assert_temporal(ds, node, line, v.GRAPH_MASTERDATA)

    for piece in line.get("pieces", []):
        seg = dict(piece)
        seg["_line_id"] = line["line_id"]
        # A piece has no independent bi-temporal identity (see docstring) —
        # it inherits the Line version's own interval verbatim.
        seg["_valid_from"] = line.get("_valid_from")
        seg["_valid_to"] = line.get("_valid_to")
        seg["_tx_from"] = line.get("_tx_from")
        seg["_tx_to"] = line.get("_tx_to")
        map_segment(ds, seg)


def map_equipment(ds: Dataset, equip: dict) -> None:
    """equip: silver_equipment-shaped dict — equipment_id, tag, nozzle_ids,
    and, as of 2026-09-11, an optional `equipment_class`.

    `silver/reconstruct.py` stamps every real Equipment's own
    `silver_equipment` row with `equipment_class=None` ("class enrichment:
    later") — the only place that classification actually lives is its
    classless-in-name `silver_components` duplicate (`kind=="Equipment"`),
    which `gold_job.py`'s Equipment-dedup logic (risk #15) excludes from RDF
    projection entirely, to avoid asserting one physical item as two RDF
    individuals. Confirmed against real data 2026-09-11: those "duplicate"
    rows are frequently NOT classless at all (e.g. `HeatExchangers`,
    `VerticalDrums`, `Generalequipmentcomponents`) — so `gold_job.py` now
    harvests that value (matched by equipment tag, the same anchor identity
    the dedup itself relies on) and passes it in here as `equipment_class`,
    rather than silently discarding it. This function stays agnostic to
    where the value came from: any caller with a real `equipment_class` on
    hand can pass it directly. When absent (the harvest found nothing, or a
    genuinely unclassified equipment item), behaviour is unchanged from
    before this addition — only the bare `pidsys:Equipment` type is
    asserted, exactly as `map_component`'s `UnclassifiedComponent` fallback
    leaves a component honest about a real classification gap rather than
    inventing one.
    """
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    RDFS_SUBCLASSOF = U("http://www.w3.org/2000/01/rdf-schema#subClassOf")
    node = _resource("equipment", equip["equipment_id"])
    ds.add(node, RDF_TYPE, U(v.C_EQUIPMENT), v.GRAPH_MASTERDATA)
    ds.add(node, U(v.P_TAG), L(equip["tag"]), v.GRAPH_MASTERDATA)
    equipment_class = equip.get("equipment_class")
    if equipment_class:
        # Same domain-class-minting scheme map_component uses for piping
        # component classes (v.component_class_uri), rooted here under
        # C_EQUIPMENT instead of C_PIPING_COMPONENT -- an equipment
        # sub-classification is a different domain-class hierarchy, not a
        # kind of piping component.
        domain_cls = U(v.component_class_uri(equipment_class))
        ds.add(domain_cls, RDFS_SUBCLASSOF, U(v.C_EQUIPMENT), v.GRAPH_MASTERDATA)
        ds.add(node, RDF_TYPE, domain_cls, v.GRAPH_MASTERDATA)
        ds.add(node, U(v.P_EQUIPMENT_CLASS), L(equipment_class), v.GRAPH_MASTERDATA)
        # honest placeholder, mirroring map_component -- this class awaits
        # RDL resolution (against the PLM equipment library
        # ido_semantic_mapping_spec.md §3-4 documents), not asserted as final
        ds.add(domain_cls, U(v.P_RDL_URI_PENDING), L(True), v.GRAPH_MASTERDATA)
    for nid in equip.get("nozzle_ids", []):
        n = _resource("nozzle", nid)
        ds.add(n, RDF_TYPE, U(v.C_NOZZLE), v.GRAPH_MASTERDATA)
        ds.add(n, U(v.P_PART_OF), node, v.GRAPH_MASTERDATA)
    _assert_temporal(ds, node, equip, v.GRAPH_MASTERDATA)


def map_connection(ds: Dataset, conn: dict) -> None:
    """conn: silver_connections-shaped dict — connection_id, from_id, to_id,
    from_node, to_node, conn_type, derived, flow_sense
    (silver_layer_spec.md §4, decision #4: one row, direction as an overlay).

    Reifies the edge (correctness note 2), emits `isConnectedTo`
    unconditionally (symmetric, both directions) and `flowsTo` only when
    `flow_sense` orients it (correctness note 3): forward -> from->to,
    reverse -> to->from, both -> both directions, none -> no flowsTo triple.
    """
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("connection", conn["connection_id"])
    frm = _resource(conn.get("from_kind", "component"), conn["from_id"])
    to = _resource(conn.get("to_kind", "component"), conn["to_id"])

    ds.add(node, RDF_TYPE, U(v.C_CONNECTION), v.GRAPH_MASTERDATA)
    ds.add(node, U(v.P_FROM_OBJECT), frm, v.GRAPH_MASTERDATA)
    ds.add(node, U(v.P_TO_OBJECT), to, v.GRAPH_MASTERDATA)
    if conn.get("from_node") is not None:
        ds.add(node, U(v.P_FROM_NODE), L(conn["from_node"]), v.GRAPH_MASTERDATA)
    if conn.get("to_node") is not None:
        ds.add(node, U(v.P_TO_NODE), L(conn["to_node"]), v.GRAPH_MASTERDATA)
    ds.add(node, U(v.P_CONN_TYPE), L(conn["conn_type"]), v.GRAPH_MASTERDATA)
    if "derived" not in conn:
        raise ValueError(f"connection {conn['connection_id']}: 'derived' is mandatory — "
                          "structural invariant (silver_layer_spec.md §3.4 'derived_flagged')")
    ds.add(node, U(v.P_DERIVED), L(bool(conn["derived"])), v.GRAPH_MASTERDATA)
    flow_sense = conn.get("flow_sense", "none")
    ds.add(node, U(v.P_FLOW_SENSE), L(flow_sense), v.GRAPH_MASTERDATA)

    # symmetric undirected backbone — always emitted
    ds.add(frm, U(v.P_IS_CONNECTED_TO), to, v.GRAPH_MASTERDATA)
    ds.add(to, U(v.P_IS_CONNECTED_TO), frm, v.GRAPH_MASTERDATA)

    # directed overlay
    if flow_sense == "forward":
        ds.add(frm, U(v.P_FLOWS_TO), to, v.GRAPH_MASTERDATA)
    elif flow_sense == "reverse":
        ds.add(to, U(v.P_FLOWS_TO), frm, v.GRAPH_MASTERDATA)
    elif flow_sense == "both":
        ds.add(frm, U(v.P_FLOWS_TO), to, v.GRAPH_MASTERDATA)
        ds.add(to, U(v.P_FLOWS_TO), frm, v.GRAPH_MASTERDATA)
    # 'none' -> no flowsTo triple at all; direction genuinely unknown

    _assert_temporal(ds, node, conn, v.GRAPH_MASTERDATA)


def map_fluid_catalogue(ds: Dataset, fluids: Iterable[dict]) -> None:
    """refdata Fluid sheet rows -> graph:refdata SKOS-ish concepts
    (data_specification.md §3.4). This IS the rules-as-data surface
    rules_reference.classify_fluid reads."""
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    for row in fluids:
        node = _resource("fluid", row["fluid_code"])
        ds.add(node, RDF_TYPE, U(v.C_FLUID), v.GRAPH_REFDATA)
        ds.add(node, U(v.P_FLUID_CODE), L(row["fluid_code"]), v.GRAPH_REFDATA)
        ds.add(node, U(v.P_CATEGORY), L(row["category"]), v.GRAPH_REFDATA)
        ds.add(node, U(v.P_SUBCATEGORY), L(row.get("subcategory", "")), v.GRAPH_REFDATA)


def map_boundary_sets(ds: Dataset, boundary_rows: Iterable[dict]) -> None:
    """refdata Boundary sheet rows -> graph:refdata role membership
    (data_specification.md §3.2; refdata.load_boundary_sets)."""
    for row in boundary_rows:
        cls = U(v.component_class_uri(row["component_class"]))
        role = U(v.uri(v.PIDSYS + "boundary_role/", row["role"]))
        ds.add(role, U(v.P_BOUNDARY_MEMBER), cls, v.GRAPH_REFDATA)


def map_system_result(ds: Dataset, system: dict, rule_name: str) -> None:
    """A computed commissioning system -> graph:results with PROV
    (medallion §5, "materialise the result... back into graph:results as
    PROV"). Never reads or writes graph:oracle."""
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("system", system["system_id"])
    ds.add(node, RDF_TYPE, U(v.C_COMMISSIONING_SYSTEM), v.GRAPH_RESULTS)
    ds.add(node, U(v.P_TAG), L(system.get("display_name", system["system_id"])), v.GRAPH_RESULTS)
    ds.add(node, U(v.P_RULE), L(rule_name), v.GRAPH_RESULTS)
    for member_id in system.get("members", []):
        ds.add(node, U(v.P_MEMBER), _resource("component", member_id), v.GRAPH_RESULTS)
    for boundary_id in system.get("boundaries", []):
        ds.add(node, U(v.P_BOUNDARY_MEMBER), _resource("component", boundary_id), v.GRAPH_RESULTS)
