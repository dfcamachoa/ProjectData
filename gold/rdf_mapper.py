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
        v.C_PIPING_SEGMENT,
        v.C_PIPING_COMPONENT,
        v.C_EQUIPMENT,
        v.C_NOZZLE,
        v.C_CONNECTION,
        v.C_FLUID,
        v.C_BOUNDARY_ROLE,
        v.C_COMMISSIONING_SYSTEM,
    ):
        ds.add(U(cls), RDF_TYPE, OWL_CLASS, v.GRAPH_MASTERDATA)
    # The one real IDO alignment this project asserts: piping components and
    # equipment ARE physical objects (a foundational IDO class), never more.
    ds.add(U(v.C_PIPING_COMPONENT), RDFS_SUBCLASSOF, U(v.IDO_PHYSICAL_OBJECT), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_EQUIPMENT), RDFS_SUBCLASSOF, U(v.IDO_PHYSICAL_OBJECT), v.GRAPH_MASTERDATA)
    ds.add(U(v.C_NOZZLE), RDFS_SUBCLASSOF, U(v.IDO_PHYSICAL_OBJECT), v.GRAPH_MASTERDATA)


def map_component(ds: Dataset, comp: dict, rdl_uri: Optional[str] = None) -> None:
    """comp: silver_components-shaped dict — component_id, component_class,
    tag, segment_id, is_valve, drawing_number, ... (silver_layer_spec.md §4)."""
    RDFS_SUBCLASSOF = U("http://www.w3.org/2000/01/rdf-schema#subClassOf")
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("component", comp["component_id"])
    domain_cls = U(v.component_class_uri(comp["component_class"]))
    ds.add(domain_cls, RDFS_SUBCLASSOF, U(v.C_PIPING_COMPONENT), v.GRAPH_MASTERDATA)
    ds.add(node, RDF_TYPE, domain_cls, v.GRAPH_MASTERDATA)
    if comp.get("tag"):
        ds.add(node, U(v.P_TAG), L(comp["tag"]), v.GRAPH_MASTERDATA)
    ds.add(node, U(v.P_COMPONENT_CLASS), L(comp["component_class"]), v.GRAPH_MASTERDATA)
    if comp.get("segment_id"):
        ds.add(node, U(v.P_PART_OF), _resource("segment", comp["segment_id"]), v.GRAPH_MASTERDATA)
    if rdl_uri:
        ds.add(domain_cls, U("http://www.w3.org/2002/07/owl#sameAs"), U(rdl_uri), v.GRAPH_MASTERDATA)
    else:
        # honest placeholder — the class awaits RDL resolution, not asserted as final
        ds.add(domain_cls, U(v.P_RDL_URI_PENDING), L(True), v.GRAPH_MASTERDATA)


def map_segment(ds: Dataset, seg: dict) -> None:
    """seg: silver_segments-shaped dict. The two oracle columns
    (src_turnover / src_subsystem) are routed to graph:oracle ONLY — see
    silver_layer_spec.md §5, and never touch graph:masterdata here."""
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("segment", seg["segment_id"])
    ds.add(node, RDF_TYPE, U(v.C_PIPING_SEGMENT), v.GRAPH_MASTERDATA)
    ds.add(node, U(v.P_TAG), L(seg.get("seg_tag", "")), v.GRAPH_MASTERDATA)
    ds.add(node, U(v.P_FLUID_CODE), L(seg["fluid"]), v.GRAPH_MASTERDATA)
    if seg.get("subline_tag"):
        ds.add(node, U(v.P_PART_OF), _resource("subline", seg["subline_tag"]), v.GRAPH_MASTERDATA)
    elif seg.get("pns_tag"):
        ds.add(node, U(v.P_PART_OF), _resource("pipeline_system", seg["pns_tag"]), v.GRAPH_MASTERDATA)

    if seg.get("src_turnover") is not None:
        ds.add(node, U(v.P_SRC_TURNOVER_SYSTEM), L(seg["src_turnover"]), v.GRAPH_ORACLE)
    if seg.get("src_subsystem") is not None:
        ds.add(node, U(v.P_SRC_SUBSYSTEM), L(seg["src_subsystem"]), v.GRAPH_ORACLE)


def map_equipment(ds: Dataset, equip: dict) -> None:
    RDF_TYPE = U("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    node = _resource("equipment", equip["equipment_id"])
    ds.add(node, RDF_TYPE, U(v.C_EQUIPMENT), v.GRAPH_MASTERDATA)
    ds.add(node, U(v.P_TAG), L(equip["tag"]), v.GRAPH_MASTERDATA)
    for nid in equip.get("nozzle_ids", []):
        n = _resource("nozzle", nid)
        ds.add(n, RDF_TYPE, U(v.C_NOZZLE), v.GRAPH_MASTERDATA)
        ds.add(n, U(v.P_PART_OF), node, v.GRAPH_MASTERDATA)


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
