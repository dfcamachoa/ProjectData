"""Canonical objects (Silver/Gold rows) -> RDF/IDO projection.

Implements medallion_rdf_ido_strategy_mapping.md §5's three correctness notes:

  1. Domain component classes are `rdfs:subClassOf` an IDO *foundational*
     class (never a bare, unconfirmed `ido:FunctionalObject`), with the real
     RDL resolution left as an explicit pending field (`vocab.P_RDL_URI_PENDING`)
     rather than invented.
  2. Every Connection is a reified node (a `pidsys:Connection`, itself an
     `ido:InformationObject`) carrying `derived` — never a bare connectivity
     triple — so inferred topology is never asserted with the same authority
     as source topology.
  3. Flow direction is materialised as the directed `pidsys:flowsTo`, separate
     from the symmetric backbone `ido:connectedTo` (native IDO, not a minted
     term), oriented by `flow_sense` (silver table's four-state enum:
     none/forward/reverse/both).

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
        v.C_OFF_PAGE_CONNECTOR,
        v.C_FLUID,
        v.C_FUNCTION,
        v.C_BOUNDARY_ROLE,
        v.C_COMMISSIONING_SYSTEM,
    ):
        ds.add(U(cls), RDF_TYPE, OWL_CLASS, v.GRAPH_MASTERDATA)
    # IDO alignment, reconciled to the reasoner-verified pidsys_extension.ttl
    # (HermiT-consistent over the v4.2/FDIS core, 2026-09-16). Each class is
    # anchored to the MOST SPECIFIC correct foundational class, not to bare
    # PhysicalObject:
    #
    #   Physical artefacts (man-made physical things):
    ds.add(U(v.C_PIPING_COMPONENT), RDFS_SUBCLASSOF, U(v.IDO_PHYSICAL_ARTEFACT), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_EQUIPMENT), RDFS_SUBCLASSOF, U(v.IDO_PHYSICAL_ARTEFACT), v.GRAPH_MASTERDATA)
    #   Features (dependent parts/loci of physical objects, not free-standing):
    ds.add(U(v.C_NOZZLE), RDFS_SUBCLASSOF, U(v.IDO_FEATURE), v.GRAPH_MASTERDATA)
    #   FUNCTIONAL groupings (ido:System) -- the load-bearing correction:
    #   a segment/line has functional (not material) continuity, so it is a
    #   System, NOT a PhysicalObject. Its physical pieces are separate
    #   PhysicalArtefact individuals it groups via hasFunctionalPart. v4.2
    #   asserts no System|PhysicalObject disjointness, so the old dual
    #   typing was satisfiable but wrong; the correct single typing is enforced
    #   here in projection, per the model.
    ds.add(U(v.C_PIPING_SEGMENT), RDFS_SUBCLASSOF, U(v.IDO_SYSTEM), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_LINE), RDFS_SUBCLASSOF, U(v.IDO_SYSTEM), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_SUBLINE), RDFS_SUBCLASSOF, U(v.IDO_SYSTEM), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_PIPELINE_SYSTEM), RDFS_SUBCLASSOF, U(v.IDO_SYSTEM), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_PROCESS_UNIT), RDFS_SUBCLASSOF, U(v.IDO_SYSTEM), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_STARTUP_PACKAGE), RDFS_SUBCLASSOF, U(v.IDO_SYSTEM), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_COMMISSIONING_SYSTEM), RDFS_SUBCLASSOF, U(v.IDO_SYSTEM), v.GRAPH_MASTERDATA)
    #   Information objects (artefacts ABOUT physical things, not physical):
    ds.add(U(v.C_DOCUMENT), RDFS_SUBCLASSOF, U(v.IDO_INFORMATION_OBJECT), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_CONNECTION), RDFS_SUBCLASSOF, U(v.IDO_INFORMATION_OBJECT), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_OFF_PAGE_CONNECTOR), RDFS_SUBCLASSOF, U(v.IDO_INFORMATION_OBJECT), v.GRAPH_MASTERDATA)
    #   Function individuals (the realization target of hasMember):
    ds.add(U(v.C_FUNCTION), RDFS_SUBCLASSOF, U(v.IDO_FUNCTION), v.GRAPH_MASTERDATA)
    #   P_MEMBER specialises the native functional-part property, so a
    #   CommissioningSystem's members are functional individuals by construction.
    ds.add(U(v.P_MEMBER), RDFS_SUBCLASSOF, U(v.P_HAS_FUNCTIONAL_PART), v.GRAPH_MASTERDATA)
    ds.add(U(v.P_BOUNDARY_MEMBER), RDFS_SUBCLASSOF, U(v.P_HAS_FUNCTIONAL_PART), v.GRAPH_MASTERDATA)
    # A component with no component_class (real Silver data: confirmed
    # 2026-09-10 -- some real components carry component_class=None) gets
    # this honest, project-defined class instead of crashing map_component --
    # still rooted under C_PIPING_COMPONENT, so it is exactly as much an IDO
    # physical artefact as every other component, just unclassified.
    ds.add(U(v.C_UNCLASSIFIED_COMPONENT), RDFS_SUBCLASSOF, U(v.C_PIPING_COMPONENT), v.GRAPH_MASTERDATA)


def map_component(
    ds: Dataset,
    comp: dict,
    rdl_uri: Optional[str] = None,
    rdl_match_type: Optional[str] = None,
    pending_review: bool = False,
) -> None:
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

    As of 2026-09-11, the same treatment is extended to
    `vocab.CATCHALL_COMPONENT_CLASSES` — literal strings the source tool
    itself emits (`CustomPipingComponent`, `GenericComponent`, ...) when it
    could not resolve a specific class. Confirmed against the real PCA PLM
    equipment reference ontology (`specs/semantics/equipment.rdf`, 246
    classes) that none of these strings, nor any catch-all/placeholder
    class of any name, exists in the reference vocabulary — so minting a
    domain class for one is not "pending RDL resolution", it is a
    classification that can never resolve. Both cases collapse onto the
    same `component_class = None`-shaped code path below.

    As of 2026-09-11 (cont.), `rdl_uri` is joined by two more optional
    resolution-metadata fields (§4.3.1, gold_layer_spec.md risk #18):
    `rdl_match_type` ("exact"/"close"/"label" — how `rdl_uri` was reached,
    asserted alongside the existing `owl:sameAs`) and `pending_review`
    (True for a close/label match on a boundary-forming class, per §4.5's
    review-gate discipline — never set for an exact match, and meaningless
    when `rdl_uri` is absent). The caller (`gold_job.py`, via
    `rules_reference.resolve_rdl_uri`) resolves these from the
    `graph:refdata` crosswalks before calling this function — `map_component`
    itself performs no lookup and stays a pure projection, exactly as
    `rdl_uri` already worked before this addition.
    """
    RDFS_SUBCLASSOF = U("http://www.w3.org/2000/01/rdf-schema#subClassOf")
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("component", comp["component_id"])
    component_class = comp.get("component_class")
    if component_class in v.CATCHALL_COMPONENT_CLASSES:
        component_class = None
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
            if rdl_match_type:
                ds.add(domain_cls, U(v.P_RDL_MATCH_TYPE), L(rdl_match_type), v.GRAPH_MASTERDATA)
            if pending_review:
                ds.add(domain_cls, U(v.P_PENDING_REVIEW), L(True), v.GRAPH_MASTERDATA)
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
    # Off-page continuation: Silver Stage C writes conn_type "OffPage" (its own
    # string); the model/spec also uses "Off-Page continuation". Recognise both.
    # Its endpoints are OffPageConnector nodes, not components — Stage C's OffPage
    # row carries only from_id/to_id (no *_kind), so default those to
    # "off_page_connector" here rather than the usual "component".
    is_off_page = conn.get("conn_type") in ("OffPage", "Off-Page continuation")
    default_kind = "off_page_connector" if is_off_page else "component"
    frm = _resource(conn.get("from_kind", default_kind), conn["from_id"])
    to = _resource(conn.get("to_kind", default_kind), conn["to_id"])

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

    # symmetric undirected backbone — always emitted, as NATIVE ido:connectedTo
    # (P_IS_CONNECTED_TO now resolves to the real IDO term, not a minted
    # pidsys: one). Emitted in both directions explicitly rather than relying on
    # a reasoner to materialise the symmetry, so the raw graph is complete
    # pre-reasoning.
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


def _is_piping_opc(opc) -> bool:
    """Whether an OPC sits on a PipingSegment (so a `terminates` edge — whose
    model range is PipingSegment — is valid).

    Signal is the DEXPI-typed flow ROLE, `flow_direction` / ComponentClass
    (`FlowIn`/`FlowOutPipeOffPageConnector`), NOT the `opc_type` business label.
    Confirmed against real Project-A data (2026-09-23): an element can be
    `OPCType="Utility Connector"` yet `ComponentClass="FlowOutPipeOffPageConnector"`
    with symbol file `Utility-Off Drawing Piping Connector` — a utility LINE
    continuation that does sit on a pipe and must terminate its segment. So the
    business label does NOT decide this; the flow role does.

    Rule: a `PipeOffPageConnector` (any `...PipeOffPageConnector` flow role) sits
    on a pipe. `SignalOffPageConnector`s are the ones that don't — and those are
    never harvested (reconstructed.py::_harvest_opcs iterates PipeOffPageConnector
    only), so in practice every harvested OPC is a piping OPC. This guard stays as
    defence-in-depth: emit `terminates` unless the flow role is explicitly a
    non-pipe (Signal) or the opc_type is explicitly an instrument connector.

    Accepts the whole opc dict (not just opc_type) so it can read flow_direction.
    """
    flow = (opc.get("flow_direction") or "")
    otype = (opc.get("opc_type") or "")
    if "Signal" in flow:            # explicit non-pipe flow role
        return False
    if "Instrument" in otype:       # explicit instrument business type
        return False
    return True   # default allow; caller still requires on_segment to be present


def map_off_page_connector(ds: Dataset, opc: dict) -> None:
    """opc: an off-page-connector dict from Silver's `silver_off_page_connectors`
    table (Stage C, Workstream 2). Fields (see silver/schema.OPC_ENTITY_STRUCT):
    `opc_id`, `tag`, `opc_type` (OPCType classification), `flow_direction`
    (DEXPI `FlowIn/FlowOutPipeOffPageConnector`; None for PostProc),
    `component_class_uri` (DEXPI sandbox RDL URI — passed through, NOT minted),
    `to_from_dir`/`to_from_text` (mate narrative, provenance), `on_segment`
    (the PipingSegment it terminates; None for instrument OPCs / no seg),
    `paired_drawing`, `matched`.

    Per data_specification.md §2.11–§2.13 and
    `claude/off_page_connector_representation.md`, the OPC NODE is its own
    class (`C_OFF_PAGE_CONNECTOR`, an `ido:InformationObject`, a SIBLING of
    Connection, never a subclass of it), and its `terminates` link to the
    segment is a DIRECT edge, not a reified Connection. `terminates` is emitted
    ONLY for a piping-type OPC with an `on_segment` — an instrument/utility OPC
    (or one with no segment) gets a node with no `terminates`, since the model's
    `terminates` range is PipingSegment and instrumentation is deferred (§6.4).

    The cross-document mating pair, by contrast, IS a Connection — asserted by
    an ordinary `map_connection` call with `conn_type` `"OffPage"` (Silver Stage
    C's own string) or `"Off-Page continuation"`, both recognised there, and
    `derived=True`. An unmatched OPC gets a node here but NO Connection — a
    connectivity data-quality flag (Silver's `opc_open_boundary`), not an edge.
    """
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("off_page_connector", opc["opc_id"])
    ds.add(node, RDF_TYPE, U(v.C_OFF_PAGE_CONNECTOR), v.GRAPH_MASTERDATA)
    if opc.get("tag"):
        ds.add(node, U(v.P_TAG), L(opc["tag"]), v.GRAPH_MASTERDATA)
    if opc.get("opc_type"):
        # OPCType classification (piping/instrument/utility, on/off-unit).
        ds.add(node, U(v.P_COMPONENT_CLASS), L(opc["opc_type"]), v.GRAPH_MASTERDATA)
    if opc.get("flow_direction"):
        # DEXPI's typed flow role (FlowIn/FlowOut). Metadata on the OPC, not a
        # class or crosswalk entry. PostProc has none (stays absent).
        ds.add(node, U(v.P_FLOW_SENSE), L(opc["flow_direction"]), v.GRAPH_MASTERDATA)
    if opc.get("to_from_text"):
        # Mate narrative (e.g. "LP ACID GAS FLARE"); provenance, asserts no flow.
        ds.add(node, U(v.P_CATEGORY), L(opc["to_from_text"]), v.GRAPH_MASTERDATA)
    if opc.get("component_class_uri"):
        # Pass-through of DEXPI's own sandbox RDL URI for traceability — a
        # direct reference to a URI DEXPI already publishes, never a TEN_RDL
        # or PLM class assertion.
        ds.add(node, U(v.P_COMPONENT_CLASS_URI), U(opc["component_class_uri"]), v.GRAPH_MASTERDATA)
    if opc.get("on_segment") and _is_piping_opc(opc):
        # Direct terminates edge OPC -> PipingSegment (not reified). Piping-type
        # OPCs only — an instrument/utility OPC does not terminate a pipe.
        ds.add(node, U(v.P_TERMINATES), _resource("segment", opc["on_segment"]), v.GRAPH_MASTERDATA)
    _assert_temporal(ds, node, opc, v.GRAPH_MASTERDATA)


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


def _clean_cell(value) -> str:
    """'' for None / NaN / 'nan' / 'none' / blank — what an empty spreadsheet
    cell looks like after pandas reads it (NaN is the only value != itself)."""
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "null") else text


_BOUNDARY_YES = {"y", "yes", "true", "1"}
_BOUNDARY_NO = {"n", "no", "false", "0"}


def map_boundary_sets(ds: Dataset, boundary_rows: Iterable[dict]) -> dict:
    """refdata Boundary sheet rows -> graph:refdata (data_specification.md §3.2).

    Each row: {"component_class", "role", optional "boundary"}. Same reading
    as Silver's `refdata.boundary_from_rows`: a row is boundary-forming when
    its Boundary flag is yes, or — with no flag — when it names a role.

    - boundary-forming  -> `<boundary_role/ROLE> pidsys:boundaryMember <class>`;
      ROLE must be one of vocab.BOUNDARY_ROLES, else ValueError (a typo must
      not silently drop a boundary).
    - explicitly not    -> `<class> pidsys:boundaryForming false`, so the
      project decision (e.g. CheckValve) is recorded as data, not as an
      absence. Found real 2026-09-23: the sheet's CheckValve row has an empty
      Role, which the old mapper minted as a `boundary_role/nan` member —
      making CheckValve boundary-forming, the opposite of the decision.
    - a class that is both -> ValueError (the sheet contradicts itself).

    Returns {"members": {role: [classes]}, "excluded": [classes]}.
    """
    members: dict = {}
    excluded: set = set()
    for row in boundary_rows:
        cls = _clean_cell(row.get("component_class"))
        if not cls:
            continue
        role = _clean_cell(row.get("role")).lower()
        flag = _clean_cell(row.get("boundary")).lower()
        if flag and flag not in _BOUNDARY_YES | _BOUNDARY_NO:
            raise ValueError(f"Boundary sheet: {cls!r} has Boundary={flag!r}; expected yes/no")
        is_boundary = (flag in _BOUNDARY_YES) if flag else bool(role)
        if not is_boundary:
            excluded.add(cls)
            continue
        if role not in v.BOUNDARY_ROLES:
            raise ValueError(f"Boundary sheet: {cls!r} is boundary-forming but its role {role!r} "
                             f"is not one of {v.BOUNDARY_ROLES}")
        members.setdefault(role, set()).add(cls)
    in_a_role = set().union(*members.values()) if members else set()
    conflict = sorted(excluded & in_a_role)
    if conflict:
        raise ValueError(f"Boundary sheet contradicts itself: {conflict} are both boundary-forming "
                         "and explicitly not")
    for role, classes in members.items():
        role_node = U(v.uri(v.PIDSYS + "boundary_role/", role))
        for cls in classes:
            ds.add(role_node, U(v.P_BOUNDARY_MEMBER), U(v.component_class_uri(cls)), v.GRAPH_REFDATA)
    for cls in excluded:
        ds.add(U(v.component_class_uri(cls)), U(v.P_BOUNDARY_FORMING), L(False), v.GRAPH_REFDATA)
    return {"members": {r: sorted(c) for r, c in sorted(members.items())},
            "excluded": sorted(excluded)}


def map_rds_plm_crosswalk(ds: Dataset, crosswalk_rows: Iterable[dict]) -> None:
    """§4.3.1's DEXPI URI bridge: RDS→PLM crosswalk rows -> graph:refdata
    SKOS mapping triples. Each row: {"rds_uri", "plm_uri", "match_type"}
    with `match_type` one of "exact"/"close" — mirrors the real PLM
    library's own published `skos:exactMatch`/`closeMatch` predicates
    (`ido_semantic_mapping_spec.md` §4.2: 345 closeMatch, 22 relatedMatch,
    1 exactMatch across 208 RDS codes) rather than inventing new
    vocabulary. `rules_reference.load_rds_plm_crosswalk` is the reader."""
    for row in crosswalk_rows:
        pred = v.SKOS_EXACT_MATCH if row["match_type"] == "exact" else v.SKOS_CLOSE_MATCH
        ds.add(U(row["rds_uri"]), U(pred), U(row["plm_uri"]), v.GRAPH_REFDATA)


def map_componentclass_plm_aliases(ds: Dataset, alias_rows: Iterable[dict]) -> None:
    """§4.3.1's PostProc label bridge: PostProc carries no RDL/RDS URIs at
    all (verified — zero across a full real drawing, `ido_semantic_mapping_
    spec.md` §4.1), so there is no URI to crosswalk; a curated
    ComponentClass string -> PLM URI alias is the only bridge. Each row:
    {"component_class", "plm_uri"}. The raw string is asserted as a
    `P_COMPONENT_CLASS` literal on the alias node (not reconstructed from
    the node's URI-safe encoding) so a class containing a literal
    underscore still round-trips correctly. `rules_reference.
    load_componentclass_plm_aliases` is the reader."""
    for row in alias_rows:
        node = U(v.component_class_alias_uri(row["component_class"]))
        ds.add(node, U(v.P_COMPONENT_CLASS), L(row["component_class"]), v.GRAPH_REFDATA)
        ds.add(node, U(v.P_ALIAS_OF), U(row["plm_uri"]), v.GRAPH_REFDATA)


def _assert_functional_membership(ds: Dataset, system_node, member_pred: str,
                                  component_id: str, activity_node) -> None:
    """Assert that a physical component is a member of a commissioning system
    THROUGH the v4.2 function-realization pattern, rather than by pointing the
    membership predicate at the physical component directly.

    P_MEMBER / P_BOUNDARY_MEMBER are subproperties of ido:hasFunctionalPart
    (declare_ontology_skeleton), whose range is a functional object — so a
    system's member must be a FUNCTIONAL individual, not the physical component.
    The bridge (reasoner-verified model, pidsys_extension.ttl):

        component  --ido:hasFunction-->  function        (physical -> its role)
        function   --ido:realizedIn -->  systemActivity  (Potential -> Activity)
        system     --pidsys:member  -->  function        (functional membership)

    NOTE realizedIn's range is Activity, so it is the FUNCTION that is
    realizedIn the commissioning Activity — never `function realizedIn
    component`. The Function individual is minted deterministically from the
    component id (one function per component per system) so re-projection is
    idempotent and the physical component is never itself asserted as a
    functional part of a System.
    """
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    component_node = _resource("component", component_id)
    function_node = _resource("function", component_id)
    ds.add(function_node, RDF_TYPE, U(v.C_FUNCTION), v.GRAPH_RESULTS)
    ds.add(component_node, U(v.P_HAS_FUNCTION), function_node, v.GRAPH_RESULTS)
    ds.add(function_node, U(v.P_REALIZED_IN), activity_node, v.GRAPH_RESULTS)
    ds.add(system_node, U(member_pred), function_node, v.GRAPH_RESULTS)


def map_system_result(ds: Dataset, system: dict, rule_name: str) -> None:
    """A computed commissioning system -> graph:results with PROV
    (medallion §5, "materialise the result... back into graph:results as
    PROV"). Never reads or writes graph:oracle.

    Membership uses the function-realization pattern (see
    `_assert_functional_membership`): the system's members are the FUNCTIONAL
    individuals the physical components realize, not the physical components
    themselves — because P_MEMBER specialises ido:hasFunctionalPart, whose
    range is functional. Each system carries one commissioning Activity node
    that the members' functions are realizedIn.
    """
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("system", system["system_id"])
    ds.add(node, RDF_TYPE, U(v.C_COMMISSIONING_SYSTEM), v.GRAPH_RESULTS)
    ds.add(node, U(v.P_TAG), L(system.get("display_name", system["system_id"])), v.GRAPH_RESULTS)
    ds.add(node, U(v.P_RULE), L(rule_name), v.GRAPH_RESULTS)
    # The commissioning Activity this system's member functions are realized in
    # (realizedIn's range). One per system, minted from the system id.
    activity_node = _resource("activity", system["system_id"])
    ds.add(activity_node, RDF_TYPE, U(v.IDO_ACTIVITY), v.GRAPH_RESULTS)
    for member_id in system.get("members", []):
        _assert_functional_membership(ds, node, v.P_MEMBER, member_id, activity_node)
    for boundary_id in system.get("boundaries", []):
        _assert_functional_membership(ds, node, v.P_BOUNDARY_MEMBER, boundary_id, activity_node)
