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
IDO_PHYSICAL_OBJECT = IDO + "PhysicalObject"
IDO_PROCESS = IDO + "Process"
IDO_INFORMATION_CONTENT_ENTITY = IDO + "InformationContentEntity"

# --- pidsys: classes (subclassed under an IDO foundational class, never bare) ---
C_DOCUMENT = PIDSYS + "Document"
C_STARTUP_PACKAGE = PIDSYS + "StartUpPackage"
C_PROCESS_UNIT = PIDSYS + "ProcessUnit"
C_PIPELINE_SYSTEM = PIDSYS + "PipelineSystem"
C_SUBLINE = PIDSYS + "Subline"
C_LINE = PIDSYS + "Line"               # Gold's own line-grain aggregation unit (silver_cdc.py OBJECT_KINDS
                                        # "line") -- the physical line as CDC/bi-temporally versions it;
                                        # a PipingSegment (below) is one as-drawn piece of it (gold_layer_spec.md §3.2/§4.4)
C_PIPING_SEGMENT = PIDSYS + "PipingSegment"
C_PIPING_COMPONENT = PIDSYS + "PipingComponent"
C_UNCLASSIFIED_COMPONENT = PIDSYS + "UnclassifiedComponent"  # comp["component_class"] missing/None in
                                                              # real Silver data -- map_component's honest
                                                              # fallback instead of crashing on a KeyError
C_EQUIPMENT = PIDSYS + "Equipment"
C_NOZZLE = PIDSYS + "Nozzle"
C_CONNECTION = PIDSYS + "Connection"
C_FLUID = PIDSYS + "Fluid"
C_BOUNDARY_ROLE = PIDSYS + "BoundaryRole"
C_COMMISSIONING_SYSTEM = PIDSYS + "CommissioningSystem"

# domain component classes are minted on demand as PIDSYS + component_class
# (e.g. PIDSYS#GateValve) and declared rdfs:subClassOf C_PIPING_COMPONENT,
# which is itself rdfs:subClassOf IDO_PHYSICAL_OBJECT — see rdf_mapper.py.


def component_class_uri(component_class: str) -> str:
    """Domain class URI for a component class string, e.g. 'GateValve'."""
    safe = component_class.replace(" ", "_")
    return PIDSYS + safe


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
P_IS_CONNECTED_TO = PIDSYS + "isConnectedTo"          # symmetric, undirected
P_FLOWS_TO = PIDSYS + "flowsTo"                        # directed overlay
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
P_MEMBER = PIDSYS + "member"
P_BOUNDARY_MEMBER = PIDSYS + "boundaryMember"

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
