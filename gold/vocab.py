"""Vocabulary / namespace constants for the Gold RDF projection.

[medallion_rdf_ido_strategy_mapping.md §5, §9 risk #4] IDO (ISO 23726-3) is a
foundational upper ontology; it ships no `validFrom`/`validTo`, and it ships no
`FunctionalObject`/`Valve`/`GateValve` domain classes either. Both must be
project-defined or resolved to a domain reference-data library aligned to IDO
(the POSC Caesar RDL / ISO 15926-4 lineage the sibling IDO prototype already
reaches). This module names that boundary explicitly instead of quietly
asserting `ido:` terms that do not exist:

  - `PIDSYS`  — this project's own namespace. All predicates and validity
    timestamps this product invents (flowsTo, derived, validFrom, validTo,
    srcTurnoverSystem, ...) live here, never under `ido:`.
  - `IDO`     — the real upper-ontology namespace (ISO/IEC 21838-2 aligned
    IDO). Referenced ONLY for the handful of foundational classes IDO does
    ship (a physical object / process / information-content-entity split);
    never for domain classes.
  - `RDL`     — placeholder for the domain reference-data library a real
    deployment resolves component classes into (POSC Caesar RDL URIs, e.g.
    `http://data.posccaesar.org/rdl/RDS...`). `rdf_mapper.py` subclasses every
    domain class under an IDO physical-object class *and* records an
    `rdl_uri` pending-resolution field rather than inventing an RDL URI.
"""
from __future__ import annotations

PIDSYS = "https://pidsys.example/ns#"
IDO = "https://www.omg.org/spec/Commons/IndustrialData/"  # foundational classes only
RDL = "http://data.posccaesar.org/rdl/"  # pending resolution — see module docstring
PROV = "http://www.w3.org/ns/prov#"
XSD = "http://www.w3.org/2001/XMLSchema#"

# --- Foundational IDO alignment (the only IDO terms this project asserts) ---
# The v4.2 (FDIS) core carries a finer set of foundational anchors than the
# single PhysicalObject this project first aligned to. The reasoner-verified
# pidsys_extension.ttl anchors each pidsys: class to the MOST SPECIFIC
# foundational class that is correct, not to bare PhysicalObject:
#   - PhysicalArtefact  — man-made physical things (components, equipment).
#     In v4.2, PhysicalArtefact rdfs:subClassOf PhysicalObject DIRECTLY
#     (there is no intervening InanimatePhysicalObject in this core), so a
#     PhysicalArtefact is still a PhysicalObject — just more precisely typed.
#   - Feature           — a dependent part/locus of a physical object
#     (nozzles, connection nodes) — not a free-standing physical object.
#   - System            — a functional grouping (piping segments, lines,
#     sublines, process units, start-up packages, commissioning systems,
#     instrumentation loops). NOTE this makes segments/lines FUNCTIONAL, not
#     physical — see the C_PIPING_SEGMENT / C_LINE comments below.
#   - InformationObject — an information artefact (documents, and the reified
#     Connection edge / OffPageConnector, which are ABOUT physical things,
#     not physical things themselves).
#   - Function / Activity — the realization pair that bridges a physical
#     object to its functional role (hasFunction / realizedIn), replacing the
#     v2021 `installedAs` bridge (absent from v4.2 and never the right term).
IDO_PHYSICAL_OBJECT = IDO + "PhysicalObject"
IDO_PHYSICAL_ARTEFACT = IDO + "PhysicalArtefact"
IDO_FEATURE = IDO + "Feature"
IDO_SYSTEM = IDO + "System"
IDO_INFORMATION_OBJECT = IDO + "InformationObject"
IDO_FUNCTION = IDO + "Function"
IDO_ACTIVITY = IDO + "Activity"
IDO_PROCESS = IDO + "Process"
IDO_INFORMATION_CONTENT_ENTITY = IDO + "InformationContentEntity"

# --- pidsys: classes (each anchored to the foundational IDO class named in
#     the trailing comment; see declare_ontology_skeleton in rdf_mapper.py for
#     the actual rdfs:subClassOf axioms, and pidsys_extension.ttl for the
#     reasoner-verified model these mirror) ---
C_DOCUMENT = PIDSYS + "Document"               # -> IDO_INFORMATION_OBJECT
C_STARTUP_PACKAGE = PIDSYS + "StartUpPackage"  # -> IDO_SYSTEM
C_PROCESS_UNIT = PIDSYS + "ProcessUnit"        # -> IDO_SYSTEM
C_PIPELINE_SYSTEM = PIDSYS + "PipelineSystem"  # -> IDO_SYSTEM
C_SUBLINE = PIDSYS + "Subline"                 # -> IDO_SYSTEM
C_LINE = PIDSYS + "Line"               # -> IDO_SYSTEM (FUNCTIONAL, not physical). Gold's own line-grain
                                        # aggregation unit (silver_cdc.py OBJECT_KINDS "line"). Per the
                                        # reasoner-verified model, a Line is a functional grouping with
                                        # functional (not material) continuity: its identity persists
                                        # across revisions even as its physical pieces' ids/split-points
                                        # churn -- which is exactly why Silver moved piping CDC to line
                                        # grain (silver_layer_spec.md §3.5). The as-drawn physical pieces
                                        # are SEPARATE PhysicalArtefact individuals; the Line groups them
                                        # via hasFunctionalPart, it is not itself one of them.
C_PIPING_SEGMENT = PIDSYS + "PipingSegment"  # -> IDO_SYSTEM (FUNCTIONAL, not physical). A functional
                                              # grouping of the physical pieces that make up one drawn
                                              # segment; slot != filler (4D identity). NOT a PhysicalObject
                                              # -- v4.2 asserts no System|PhysicalObject disjointness, so a
                                              # dual-typed segment is satisfiable-but-WRONG (HermiT-checked);
                                              # this discipline lives in projection code, not an axiom.
C_PIPING_COMPONENT = PIDSYS + "PipingComponent"  # -> IDO_PHYSICAL_ARTEFACT
C_UNCLASSIFIED_COMPONENT = PIDSYS + "UnclassifiedComponent"  # -> C_PIPING_COMPONENT. comp["component_class"]
                                                              # missing/None in real Silver data -- map_component's
                                                              # honest fallback instead of crashing on a KeyError
C_EQUIPMENT = PIDSYS + "Equipment"     # -> IDO_PHYSICAL_ARTEFACT
C_NOZZLE = PIDSYS + "Nozzle"           # -> IDO_FEATURE (a dependent part of equipment, not free-standing)
C_CONNECTION = PIDSYS + "Connection"   # -> IDO_INFORMATION_OBJECT (the reified edge is ABOUT two physical
                                        # objects; it is not itself physical). Endpoints (fromObject/
                                        # toObject) range over PhysicalObject UNION OffPageConnector.
C_OFF_PAGE_CONNECTOR = PIDSYS + "OffPageConnector"  # -> IDO_INFORMATION_OBJECT (sibling of Connection, NOT a
                                        # subclass of it): a drawing-continuation artefact, the THING a
                                        # cross-document Connection points at, not the edge. Carries the OPC
                                        # tag, flow-direction value (FlowIn/FlowOut...), and a pass-through
                                        # DEXPI sandbox component_class_uri. `terminates` (below) is a DIRECT
                                        # edge to the PipingSegment it ends -- deliberately not reified, per
                                        # data_specification.md §2.11-2.13 (only the mating pair is a Connection).
C_FLUID = PIDSYS + "Fluid"
C_FUNCTION = PIDSYS + "Function"       # -> IDO_FUNCTION. The functional individual a physical object realizes;
                                        # the target of hasMember for a CommissioningSystem (members are
                                        # FUNCTIONAL individuals, reached via the hasFunction/realizedIn
                                        # realization pattern -- see map_system_result in rdf_mapper.py).
C_BOUNDARY_ROLE = PIDSYS + "BoundaryRole"
C_COMMISSIONING_SYSTEM = PIDSYS + "CommissioningSystem"  # -> IDO_SYSTEM

# domain component classes are minted on demand as PIDSYS + component_class
# (e.g. PIDSYS#GateValve) and declared rdfs:subClassOf C_PIPING_COMPONENT,
# which is itself rdfs:subClassOf IDO_PHYSICAL_OBJECT — see rdf_mapper.py.


def component_class_uri(component_class: str) -> str:
    """Domain class URI for a component class string, e.g. 'GateValve'."""
    safe = component_class.replace(" ", "_")
    return PIDSYS + safe


def component_class_alias_uri(component_class: str) -> str:
    """§4.3.1's label-bridge alias node for a ComponentClass STRING --
    distinct from component_class_uri's domain-class node (never asserted
    as an rdf:type or subclassed under anything; it names "the string
    'GateValve' as an alias lookup key", not the domain class itself).
    The original string is also asserted as a P_COMPONENT_CLASS literal on
    this node (rdf_mapper.map_componentclass_plm_aliases) rather than
    reconstructed from this URI-safe encoding, so a class containing a
    literal underscore still round-trips correctly."""
    safe = component_class.replace(" ", "_")
    return PIDSYS + "componentclass_alias/" + safe


# --- Confirmed catch-all component_class strings (Cause A, ido_semantic_
# mapping_spec.md §4.4) ------------------------------------------------
# Real Project A/DEXPI data confirms these four literal ComponentClass
# strings are emitted by the source tool itself when it could not resolve
# a specific class -- not missing data, and not a real classification.
# Verified 2026-09-10 against the actual reference ontology
# (ProjectData:specs/semantics/equipment.rdf, the PCA PLM equipment
# library, 246 classes): none of these four strings, nor any "Custom"/
# "Generic"/"Unclassified"-named class, exists anywhere in that library.
# Every real class there is a specific, named equipment/component type
# (Gate Valve, Pump, Heat Exchanger, ...) -- there is no catch-all class
# to map these onto, in this version or any future one, since the
# ontology's own design has no placeholder tier. So these strings are
# structurally equivalent to component_class being absent/None, and
# map_component treats them identically: routed to
# C_UNCLASSIFIED_COMPONENT instead of minting a domain class that could
# never be RDL-resolved. An exact-match set, not a prefix/substring rule,
# so a real class that merely contains "Custom" as a substring (none seen
# in real data so far) is never misclassified.
CATCHALL_COMPONENT_CLASSES = frozenset({
    "CustomComponent",
    "CustomPipingComponent",
    "CustomPipeFitting",
    "GenericComponent",
})


# --- pidsys: predicates ---
P_HAS_PART = PIDSYS + "hasPart"
P_PART_OF = PIDSYS + "partOf"
P_HAS_START_UP_PACKAGE = PIDSYS + "hasStartUpPackage"
P_TAG = PIDSYS + "tag"
P_COMPONENT_CLASS = PIDSYS + "componentClass"
P_EQUIPMENT_CLASS = PIDSYS + "equipmentClass"          # added 2026-09-11: silver_equipment's own
                                                        # equipment_class is always None ("class
                                                        # enrichment: later" -- silver/reconstruct.py);
                                                        # real data confirmed the ONLY place that
                                                        # classification actually lives is the
                                                        # Equipment-kind silver_components duplicate
                                                        # risk #15 excludes from projection -- see
                                                        # rdf_mapper.map_equipment / gold_job.py
P_FLUID_CODE = PIDSYS + "fluidCode"
P_UNIT = PIDSYS + "unit"                                   # a segment/line's own engineering attrs --
P_DIAMETER = PIDSYS + "diameter"                            # never asserted before this addition (map_segment
P_PIPING_MATERIALS_CLASS = PIDSYS + "pipingMaterialsClass"  # previously only emitted tag/fluidCode/partOf)
P_INSULATION_TYPE = PIDSYS + "insulationType"
P_INSULATION_PURPOSE = PIDSYS + "insulationPurpose"
P_INSULATION_THICKNESS = PIDSYS + "insulationThickness"
P_PIECE_COUNT = PIDSYS + "pieceCount"                       # a Line's own aggregate metadata (spark_bridge.py)
P_LINE_ATTR_INCONSISTENT = PIDSYS + "lineAttrInconsistent"  # True iff pieces disagree on a field -- flagged, not resolved
P_CATEGORY = PIDSYS + "category"
P_SUBCATEGORY = PIDSYS + "subcategory"
# Symmetric undirected backbone is NATIVE IDO, not a minted pidsys: term:
# v4.2 ships ido:connectedTo (symmetric, PhysicalObject-domained), so minting
# pidsys:isConnectedTo alongside it was redundant. The model deletes the minted
# term and uses ido:connectedTo directly. flowsTo STAYS a pidsys: term: it is
# directed, and cannot be a subproperty of the symmetric connectedTo without
# inheriting symmetry -- and v4.2 has no directional connectivity companion.
P_IS_CONNECTED_TO = IDO + "connectedTo"               # symmetric, undirected -- NATIVE ido:, not pidsys:
P_FLOWS_TO = PIDSYS + "flowsTo"                        # directed overlay -- pidsys: (no IDO equivalent)
# Function-realization bridge (v4.2) -- how a physical object reaches the
# functional individual that a System groups. Replaces the v2021 `installedAs`
# path (absent from v4.2). hasFunction: PhysicalObject -> Function; realizedIn:
# a Potential (the Function) -> the Activity it is realized in. NEVER assert
# `Function realizedIn physicalObject` -- realizedIn's range is Activity.
P_HAS_FUNCTION = IDO + "hasFunction"                  # PhysicalObject -> Function (native ido:)
P_REALIZED_IN = IDO + "realizedIn"                    # Function(Potential) -> Activity (native ido:)
P_HAS_FUNCTIONAL_PART = IDO + "hasFunctionalPart"     # System -> FunctionalObject (native ido:) -- the
                                                       # super-property P_MEMBER specialises (see below)
# Off-Page Connector edges (Workstream 2)
P_TERMINATES = PIDSYS + "terminates"                  # OffPageConnector -> PipingSegment (direct, not reified)
P_COMPONENT_CLASS_URI = PIDSYS + "componentClassUri"  # pass-through source-native class URI (e.g. the DEXPI
                                                       # sandbox RDL URI on an OPC) -- traceability, not a class
P_FROM_OBJECT = PIDSYS + "fromObject"
P_TO_OBJECT = PIDSYS + "toObject"
P_FROM_NODE = PIDSYS + "fromNode"
P_TO_NODE = PIDSYS + "toNode"
P_CONN_TYPE = PIDSYS + "connType"
P_DERIVED = PIDSYS + "derived"                         # boolean, mandatory on every Connection
P_FLOW_SENSE = PIDSYS + "flowSense"                    # none|forward|reverse|both
P_VALID_FROM = PIDSYS + "validFrom"                    # project predicate, NOT ido:validFrom
P_VALID_TO = PIDSYS + "validTo"
P_TX_FROM = PIDSYS + "transactionFrom"
P_TX_TO = PIDSYS + "transactionTo"
P_SRC_TURNOVER_SYSTEM = PIDSYS + "srcTurnoverSystem"   # ORACLE — graph:oracle only, see oracle_guard.py
P_SRC_SUBSYSTEM = PIDSYS + "srcSubsystem"              # ORACLE — graph:oracle only
P_HAS_BOUNDARY_ROLE = PIDSYS + "hasBoundaryRole"
P_RDL_URI_PENDING = PIDSYS + "rdlUriPending"           # records the unresolved RDL mapping, see IDO note above
P_MEMBER = PIDSYS + "member"          # rdfs:subPropertyOf ido:hasFunctionalPart (P_HAS_FUNCTIONAL_PART).
                                       # A CommissioningSystem's members are FUNCTIONAL individuals
                                       # (Functions), never the physical components directly -- the physical
                                       # component reaches its Function via hasFunction, and the Function is
                                       # realizedIn the commissioning Activity. See map_system_result.
P_BOUNDARY_MEMBER = PIDSYS + "boundaryMember"
P_BOUNDARY_FORMING = PIDSYS + "boundaryForming"   # xsd:boolean on a component-class node in
                                                  # graph:refdata; `false` records an explicit
                                                  # project decision (e.g. CheckValve, [MD §3.2])
BOUNDARY_ROLES = ("isolation", "positive", "relief", "trap")   # the only valid role names  # same functional-membership semantics, boundary-forming role

# --- §4.3.1 RDL/PLM resolution bridges (gold_layer_spec.md risk #18) ---
# Discharges P_RDL_URI_PENDING via a graph:refdata crosswalk lookup, never
# a hardcoded rdf_mapper.py mapping. Two format-scoped bridges converge on
# the same domain_cls owl:sameAs assertion map_component already makes:
#   DEXPI   -- component_class_uri (an RDS... URI) -> SKOS_EXACT/CLOSE_MATCH
#              -> a PLM URI, via the crosswalk map_rds_plm_crosswalk loads.
#   PostProc -- component_class (a string; PostProc carries no RDL URIs at
#              all, verified -- ido_semantic_mapping_spec.md §4.1) -> a
#              curated alias -> a PLM URI, via map_componentclass_plm_aliases.
SKOS = "http://www.w3.org/2004/02/skos/core#"
SKOS_EXACT_MATCH = SKOS + "exactMatch"
SKOS_CLOSE_MATCH = SKOS + "closeMatch"

P_RDL_MATCH_TYPE = PIDSYS + "rdlMatchType"  # "exact" | "close" | "label" -- how a resolved rdlUri was reached
P_PENDING_REVIEW = PIDSYS + "pendingReview"  # a close/label match on a boundary-forming class (§4.5's review-gate
                                              # discipline) -- must clear human review before a trusted
                                              # systemization run reads it; exact matches never set this
P_ALIAS_OF = PIDSYS + "aliasOf"              # a componentclass_alias_uri() node -> its curated PLM target URI

# --- prov: predicates (results graph) ---
P_WAS_DERIVED_FROM = PROV + "wasDerivedFrom"
P_WAS_GENERATED_BY = PROV + "wasGeneratedBy"
P_RULE = PIDSYS + "firedRule"

# --- Oracle predicates: the set oracle_guard.py enforces is confined to graph:oracle ---
ORACLE_PREDICATES = frozenset({P_SRC_TURNOVER_SYSTEM, P_SRC_SUBSYSTEM})

# --- Named graphs [strategy §5] ---
GRAPH_MASTERDATA = PIDSYS + "graph/masterdata"
GRAPH_REFDATA = PIDSYS + "graph/refdata"
GRAPH_ORACLE = PIDSYS + "graph/oracle"       # rule-invisible — see oracle_guard.py
GRAPH_RESULTS = PIDSYS + "graph/results"


def uri(prefix: str, local: str) -> str:
    return prefix + str(local).replace(" ", "_")
