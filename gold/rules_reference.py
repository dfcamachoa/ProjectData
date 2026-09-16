"""Reference implementation of the declarative classification / local-rule
layer the strategy assigns to Jena.

[medallion_rdf_ido_strategy_mapping.md §6] "Put classification and local,
node-local rules in Jena/OWL/SHACL, read from graph:refdata: ... Keep the
global reachability / connected-fragment partition where it already works
and is validated — the Python reconstruction/walk." This module is exactly
the first half of that hybrid: local, node-local rules that read only
graph:refdata and graph:masterdata (never graph:oracle — see
oracle_guard.py), reproduced faithfully from `pidsys/walk.py`'s documented
behaviour and `data_specification.md` §3.4 / §3.2, and validated in
`tests/test_rules_reference.py` against small fixtures that reproduce the
exact scenarios walk.py's own comments describe (nitrogen teeing into a
process header; a process fragment discharging to flare; a relief valve on a
protected process line).

It does NOT reimplement the global connected-fragment partition (walk.py's
`_fragments`, the C1/C2 consumer trace, or fragment-merge) — that stays in
Silver/Python per the hybrid boundary above. `jena_rules/classification.rules`
is the one-to-one Jena-syntax counterpart of the functions below, so the two
can be diffed for drift and both validated against the same fixtures once a
real Fuseki + Jena rule engine is stood up.
"""
from __future__ import annotations

from typing import Optional

from . import vocab as v
from .oracle_guard import assert_rule_engine_did_not_read_oracle
from .rdf_model import Dataset, URIRef


# --------------------------------------------------------------------------
# Fluid classification [data_specification.md §3.4]
# --------------------------------------------------------------------------

def load_fluid_catalogue(ds: Dataset) -> dict:
    """graph:refdata Fluid rows -> {fluid_code: {"category":..., "subcategory":...}}."""
    assert_rule_engine_did_not_read_oracle({v.GRAPH_REFDATA})
    catalogue: dict = {}
    for q in ds.triples(p=URIRef(v.P_FLUID_CODE), graph=v.GRAPH_REFDATA):
        code = str(q.o)
        cat = next(ds.triples(s=q.s, p=URIRef(v.P_CATEGORY), graph=v.GRAPH_REFDATA), None)
        sub = next(ds.triples(s=q.s, p=URIRef(v.P_SUBCATEGORY), graph=v.GRAPH_REFDATA), None)
        catalogue[code] = {
            "category": str(cat.o) if cat else None,
            "subcategory": str(sub.o) if sub else "",
        }
    return catalogue


def classify_fluid_category(fluid_code: str, catalogue: dict) -> str:
    """Returns one of 'flare' | 'steam_condensate' | 'process' | 'utility'.

    [data_specification.md §3.4]: Category=Flare -> its own flare collection
    system; Category=Utility with Subcategory containing 'Steam' or
    'Condensate' (substring test, accepts a combined 'Steam / Condensate'
    spelling) -> steam/condensate self-owning; Category=Process -> named as
    a process system; anything else -> ordinary utility (traced to a
    consumer).
    """
    row = catalogue.get(fluid_code)
    if row is None:
        return "utility"  # unknown fluid: flagged elsewhere (Stage D), traced as an ordinary utility here
    category = row.get("category")
    subcategory = row.get("subcategory") or ""
    if category == "Flare":
        return "flare"
    if category == "Utility" and ("Steam" in subcategory or "Condensate" in subcategory):
        return "steam_condensate"
    if category == "Process":
        return "process"
    return "utility"


def is_self_owning(fluid_code: str, catalogue: dict) -> bool:
    """[algorithm_spec.md §7.1a, §7.2] Flare and Steam/Condensate fluids form
    their own commissioning systems and are never traced to a consumer —
    "the single highest-value grouping fix" the walk validated against
    ~97% source agreement. This MUST be decided before any consumer trace
    runs, or a naive engine walks a steam header straight into whatever
    equipment it terminates at (medallion §6, item 3)."""
    return classify_fluid_category(fluid_code, catalogue) in ("flare", "steam_condensate")


# --------------------------------------------------------------------------
# Boundary roles [data_specification.md §3.2; refdata.load_boundary_sets]
# --------------------------------------------------------------------------

def load_boundary_roles(ds: Dataset) -> dict:
    """graph:refdata Boundary rows -> {role: {component_class_uri, ...}}."""
    assert_rule_engine_did_not_read_oracle({v.GRAPH_REFDATA})
    roles: dict = {}
    for q in ds.triples(p=URIRef(v.P_BOUNDARY_MEMBER), graph=v.GRAPH_REFDATA):
        role = str(q.s).rsplit("/", 1)[-1]
        roles.setdefault(role, set()).add(str(q.o))
    return roles


def has_boundary_role(component_class: str, role: str, boundary_roles: dict) -> bool:
    return v.component_class_uri(component_class) in boundary_roles.get(role, set())


def is_boundary_forming(component_class: str, boundary_roles: dict) -> bool:
    """Any of isolation/positive/relief/trap — CheckValve is deliberately
    excluded by never appearing in any role set (project decision,
    data_specification.md §3.2)."""
    return any(has_boundary_role(component_class, role, boundary_roles) for role in boundary_roles)


# --------------------------------------------------------------------------
# RDL/PLM resolution bridges [gold_layer_spec.md §4.3.1, risk #18]
# --------------------------------------------------------------------------

def load_rds_plm_crosswalk(ds: Dataset) -> dict:
    """graph:refdata RDS→PLM SKOS mapping triples -> {rds_uri: (plm_uri,
    match_type)} (§4.3.1's DEXPI URI bridge; `rdf_mapper.
    map_rds_plm_crosswalk` is the writer). `exact` is checked after `close`
    so an `exactMatch` triple wins if a real crosswalk ever asserted both
    for the same RDS code — should not happen (the PLM library publishes
    one match per code), but exact is the higher-confidence answer if it
    somehow does."""
    assert_rule_engine_did_not_read_oracle({v.GRAPH_REFDATA})
    result: dict = {}
    for match_type, pred in (("close", v.SKOS_CLOSE_MATCH), ("exact", v.SKOS_EXACT_MATCH)):
        for q in ds.triples(p=URIRef(pred), graph=v.GRAPH_REFDATA):
            result[str(q.s)] = (str(q.o), match_type)
    return result


def load_componentclass_plm_aliases(ds: Dataset) -> dict:
    """graph:refdata ComponentClass-string alias triples ->
    {component_class: plm_uri} (§4.3.1's PostProc label bridge; `rdf_mapper.
    map_componentclass_plm_aliases` is the writer). Reads the raw string
    off the alias node's own `P_COMPONENT_CLASS` literal, not off the
    node's URI-safe encoding, so a class containing a literal underscore
    still resolves correctly."""
    assert_rule_engine_did_not_read_oracle({v.GRAPH_REFDATA})
    result: dict = {}
    for q in ds.triples(p=URIRef(v.P_ALIAS_OF), graph=v.GRAPH_REFDATA):
        labels = list(ds.triples(s=q.s, p=URIRef(v.P_COMPONENT_CLASS), graph=v.GRAPH_REFDATA))
        if labels:
            result[labels[0].o.toPython()] = str(q.o)
    return result


def resolve_rdl_uri(
    component_class: Optional[str],
    component_class_uri: Optional[str],
    rds_plm_crosswalk: dict,
    componentclass_aliases: dict,
    boundary_roles: dict,
) -> dict:
    """§4.3.1's resolver: given one component's raw classification
    signal(s), returns `{"rdl_uri", "rdl_match_type", "pending_review"}`
    for `rdf_mapper.map_component`'s matching keyword arguments.

    Format-scoped by construction, not by an explicit format flag — a
    DEXPI component carries `component_class_uri` and takes the URI
    bridge; a PostProc component never does (verified: zero RDS/posccaesar
    URIs across a full real PostProc drawing, `ido_semantic_mapping_spec.md`
    §4.1) and falls through to the label bridge automatically. The URI
    bridge is tried first when both signals happen to be present — it is
    the higher-confidence match.

    `pending_review` is True only for a close/label match on a
    boundary-forming class (§4.3.1, §4.5's review-gate discipline already
    used for the oracle and for CV output) — an exact match, or a match on
    a non-boundary class, never needs one. No lookup happens here for a
    component with neither signal, or with no crosswalk/alias entry for
    what it does carry — `rdl_uri` stays `None` and the caller's existing
    `rdlUriPending` marker fires exactly as it did before this function
    existed."""
    rdl_uri = None
    match_type = None
    if component_class_uri and component_class_uri in rds_plm_crosswalk:
        rdl_uri, match_type = rds_plm_crosswalk[component_class_uri]
    elif component_class and component_class in componentclass_aliases:
        rdl_uri = componentclass_aliases[component_class]
        match_type = "label"
    pending_review = bool(
        rdl_uri
        and match_type in ("close", "label")
        and component_class
        and is_boundary_forming(component_class, boundary_roles)
    )
    return {"rdl_uri": rdl_uri, "rdl_match_type": match_type, "pending_review": pending_review}


# --------------------------------------------------------------------------
# Graph reads over graph:masterdata (component fluid, class, neighbours, flow)
# --------------------------------------------------------------------------

def _resource(kind: str, obj_id: str) -> URIRef:
    return URIRef(v.uri(v.PIDSYS + kind.lower() + "/", obj_id))


def component_class_of(ds: Dataset, comp: URIRef) -> Optional[str]:
    q = next(ds.triples(s=comp, p=URIRef(v.P_COMPONENT_CLASS), graph=v.GRAPH_MASTERDATA), None)
    return str(q.o) if q else None


def component_fluid(ds: Dataset, comp: URIRef) -> Optional[str]:
    """Follows partOf (component -> segment) then reads the segment's fluid
    code — components carry no fluid of their own (data_specification.md §2.4);
    the fluid is the owning Piping Segment's."""
    seg = next(ds.triples(s=comp, p=URIRef(v.P_PART_OF), graph=v.GRAPH_MASTERDATA), None)
    if seg is None:
        return None
    fq = next(ds.triples(s=seg.o, p=URIRef(v.P_FLUID_CODE), graph=v.GRAPH_MASTERDATA), None)
    return str(fq.o) if fq else None


def neighbors(ds: Dataset, comp: URIRef) -> set:
    return {q.o for q in ds.triples(s=comp, p=URIRef(v.P_IS_CONNECTED_TO), graph=v.GRAPH_MASTERDATA)}


def flows_into(ds: Dataset, a: URIRef, b: URIRef) -> bool:
    """True iff reconstructed flow runs a -> b (pidsys:flowsTo), i.e. 'a
    flows into b'. Absent for flow_sense='none'; both directions present for
    'both' (rdf_mapper.map_connection)."""
    return next(ds.triples(s=a, p=URIRef(v.P_FLOWS_TO), o=b, graph=v.GRAPH_MASTERDATA), None) is not None


# --------------------------------------------------------------------------
# The three directional guards [walk.py; systemization_spec.md §4.1, §5.9]
# --------------------------------------------------------------------------

def flare_guard(ds: Dataset, fragment_component: URIRef, neighbour: URIRef, catalogue: dict) -> bool:
    """Returns True if `neighbour` should be SKIPPED as a consumer signal for
    `fragment_component` because it is a flare discharge sink, not a
    consumer. [algorithm_spec.md §7.2] "if a process fragment reaches a
    flare neighbour and reconstructed flow runs into the flare
    (process -> flare), the flare is a discharge sink — not a consumer."
    Fires only when direction is known and one-way into the flare; a
    reversed or absent direction falls through (returns False) to ordinary
    consumer detection — a safe fallback, never a wrong attach."""
    nb_fluid = component_fluid(ds, neighbour)
    if nb_fluid is None:
        return False
    if classify_fluid_category(nb_fluid, catalogue) != "flare":
        return False
    return flows_into(ds, fragment_component, neighbour) and not flows_into(ds, neighbour, fragment_component)


def directional_consumer_guard(ds: Dataset, fragment_component: URIRef, neighbour: URIRef) -> bool:
    """Returns True if `neighbour` should be SKIPPED as a consumer signal
    because it is a supply tying INTO the fragment, not a consumer the
    fragment feeds. [medallion §6 item 4] "a different-fluid neighbour is a
    consumer only if the fragment flows toward it; if flow runs
    neighbour -> fragment, the neighbour is a supply tying in." Generalises
    the flare guard to every fluid tie-in (algorithm_spec.md §7.2a). Fires
    only when flow is known and strictly one-way into the fragment;
    unknown/both falls through to ordinary consumer detection."""
    return flows_into(ds, neighbour, fragment_component) and not flows_into(ds, fragment_component, neighbour)


def relief_attribution(ds: Dataset, relief_component: URIRef) -> Optional[URIRef]:
    """[algorithm_spec.md §7.4a] A relief device is attributed to the system
    it PROTECTS (flow into the valve), not the discharge it vents to (flow
    out). Returns the protected-side neighbour, or None if direction is not
    directionally identifiable (falls through to the ordinary walk — this
    function only ever *adds* a correct assignment, never forces a wrong
    one)."""
    for nb in neighbors(ds, relief_component):
        if flows_into(ds, nb, relief_component) and not flows_into(ds, relief_component, nb):
            return nb
    return None


# --------------------------------------------------------------------------
# Allocation & identity rules [algorithm_spec.md §9.1, §9.3, §9.5]
# --------------------------------------------------------------------------

def allocate_boundary_valve_or_blind(priority_a: int, priority_b: int) -> str:
    """[SS §5.1, ALG §9.1] Boundary valve/blind -> the higher-priority
    (earlier-commissioned) system. Lower numeric priority commissions first."""
    return "side_a" if priority_a <= priority_b else "side_b"


def allocate_steam_trap() -> str:
    """[SS §5.7, ALG §9.1] Always the steam side; the boundary is the flange
    downstream of the trap."""
    return "steam_side"


def allocate_sampling_connection() -> str:
    """[SS §5.6, ALG §9.1] The upstream commissioning system feeding the sample point."""
    return "upstream"


def allocate_interface_valve(interface: str) -> str:
    """[SS §5.6/§5.9/§5.10, ALG §9.1] Flare/drain interface valves belong to
    the flare/drain side. `interface` is 'flare' or 'drain'."""
    if interface not in ("flare", "drain"):
        raise ValueError(f"unknown interface {interface!r}; expected 'flare' or 'drain'")
    return interface


def allocate_vessel_column_instrument() -> str:
    """[SS §5.15, ALG §9.1] Vessel/column instruments -> the equipment system."""
    return "equipment_system"


def fragment_merge_key(system_kind: str, sup: str, fluid: Optional[str] = None,
                        anchor_equipment: Optional[str] = None) -> tuple:
    """[algorithm_spec.md §9.5, §10] The stable identity a system's many
    connected fragments must be unioned on, or a query surface reports dozens
    of fragment-systems instead of the real handful: (SUP, fluid) for
    utilities, (SUP, anchor-equipment) for process systems."""
    if system_kind == "utility":
        if fluid is None:
            raise ValueError("utility fragment_merge_key requires fluid")
        return (sup, "utility", fluid)
    if system_kind == "process":
        if anchor_equipment is None:
            raise ValueError("process fragment_merge_key requires anchor_equipment")
        return (sup, "process", anchor_equipment)
    raise ValueError(f"unknown system_kind {system_kind!r}")
