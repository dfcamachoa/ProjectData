"""Small synthetic fixtures reproducing, in miniature, the exact scenarios
`pidsys/walk.py`'s own comments describe. Real Project A/B drawings are not
available in this sandbox (out-of-repo, confidential — bronze_layer_spec.md
§8.7), so these fixtures play the role the "known delete+recreate revision
pair" and the oracle drawings play in the Silver/Bronze specs: small,
reproducible, and checked against the documented expected behaviour rather
than against real data this environment cannot see.

Scenario built:

    N1 --[flows_into: N1->C1]--> C1 --[flows_into: C1->F1]--> F1
    (nitrogen)                  (process)                  (flare, SF)
                                   |
                                   RV1 (SafetyValveOrFitting, relief)
                                   ^ flows_into: C1 -> RV1 -> (discharges to ATM, direction unknown)

  - N1 is Nitrogen (Utility, ordinary) tying INTO the process fragment at C1
    -> directional_consumer_guard must skip N1 as a consumer of C1's fragment.
  - C1 (process, fluid PG) flows into F1 (flare, fluid SF)
    -> flare_guard must skip F1 as a consumer of C1's fragment.
  - RV1 is a relief valve; C1 flows into RV1 (C1 is the protected side)
    -> relief_attribution(RV1) must return C1.
"""
from __future__ import annotations

from gold import rdf_mapper
from gold.rdf_model import Dataset

FLUID_CATALOGUE_ROWS = [
    {"fluid_code": "PG", "category": "Process", "subcategory": "Process General"},
    {"fluid_code": "N", "category": "Utility", "subcategory": "Nitrogen"},
    {"fluid_code": "SF", "category": "Flare", "subcategory": "Flare"},
    {"fluid_code": "LS", "category": "Utility", "subcategory": "Steam / Condensate"},
]

BOUNDARY_ROWS = [
    {"component_class": "GateValve", "role": "isolation"},
    {"component_class": "GlobeValve", "role": "isolation"},
    {"component_class": "ButterflyValve", "role": "isolation"},
    {"component_class": "PipeFlangeSpacer", "role": "positive"},
    {"component_class": "SafetyValveOrFitting", "role": "relief"},
    {"component_class": "Reliefdevices", "role": "relief"},
    {"component_class": "SteamTrap", "role": "trap"},
    # CheckValve deliberately absent from every role set (project decision).
]

SEGMENTS = [
    {"segment_id": "SG-N1", "fluid": "N", "seg_tag": "N-000001"},
    {"segment_id": "SG-PG1", "fluid": "PG", "seg_tag": "PG-000001"},
    {"segment_id": "SG-SF1", "fluid": "SF", "seg_tag": "SF-000001"},
]

COMPONENTS = [
    {"component_id": "N1", "component_class": "GateValve", "tag": "N-GV-01", "segment_id": "SG-N1"},
    {"component_id": "C1", "component_class": "PipeReducer", "tag": None, "segment_id": "SG-PG1"},
    {"component_id": "F1", "component_class": "PipeFlangeSpacer", "tag": None, "segment_id": "SG-SF1"},
    {"component_id": "RV1", "component_class": "SafetyValveOrFitting", "tag": "PSV-01", "segment_id": "SG-PG1"},
]

CONNECTIONS = [
    {
        "connection_id": "CONN-N1-C1", "from_id": "N1", "to_id": "C1",
        "from_node": 1, "to_node": 1, "conn_type": "Process",
        "derived": True, "flow_sense": "forward",  # N1 -> C1: nitrogen flows INTO the process fragment
    },
    {
        "connection_id": "CONN-C1-F1", "from_id": "C1", "to_id": "F1",
        "from_node": 2, "to_node": 1, "conn_type": "Process",
        "derived": True, "flow_sense": "forward",  # C1 -> F1: process discharges INTO the flare
    },
    {
        "connection_id": "CONN-C1-RV1", "from_id": "C1", "to_id": "RV1",
        "from_node": 3, "to_node": 1, "conn_type": "Process",
        "derived": False, "flow_sense": "forward",  # C1 -> RV1: the protected line flows into the relief valve
    },
]


def build_fixture_dataset() -> Dataset:
    ds = Dataset()
    rdf_mapper.declare_ontology_skeleton(ds)
    rdf_mapper.map_fluid_catalogue(ds, FLUID_CATALOGUE_ROWS)
    rdf_mapper.map_boundary_sets(ds, BOUNDARY_ROWS)
    for seg in SEGMENTS:
        rdf_mapper.map_segment(ds, seg)
    for comp in COMPONENTS:
        rdf_mapper.map_component(ds, comp)
    for conn in CONNECTIONS:
        rdf_mapper.map_connection(ds, conn)
    return ds
