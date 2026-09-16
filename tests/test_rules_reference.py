import unittest

from gold import rules_reference as rr
from gold.rdf_model import URIRef
from tests.fixtures import build_fixture_dataset


def R(kind, obj_id):
    from gold import vocab as v
    return URIRef(v.uri(v.PIDSYS + kind.lower() + "/", obj_id))


class TestFluidClassification(unittest.TestCase):
    def setUp(self):
        self.ds = build_fixture_dataset()
        self.catalogue = rr.load_fluid_catalogue(self.ds)

    def test_flare_is_self_owning(self):
        self.assertEqual(rr.classify_fluid_category("SF", self.catalogue), "flare")
        self.assertTrue(rr.is_self_owning("SF", self.catalogue))

    def test_steam_condensate_is_self_owning_by_subcategory_substring(self):
        self.assertEqual(rr.classify_fluid_category("LS", self.catalogue), "steam_condensate")
        self.assertTrue(rr.is_self_owning("LS", self.catalogue))

    def test_process_is_not_self_owning(self):
        self.assertEqual(rr.classify_fluid_category("PG", self.catalogue), "process")
        self.assertFalse(rr.is_self_owning("PG", self.catalogue))

    def test_ordinary_utility_is_not_self_owning(self):
        self.assertEqual(rr.classify_fluid_category("N", self.catalogue), "utility")
        self.assertFalse(rr.is_self_owning("N", self.catalogue))

    def test_unknown_fluid_defaults_to_utility_not_a_crash(self):
        # data_specification.md §3.4: an unknown fluid is retained & flagged
        # elsewhere (Stage D), never fatal here.
        self.assertEqual(rr.classify_fluid_category("ZZZZ", self.catalogue), "utility")


class TestBoundaryRoles(unittest.TestCase):
    def setUp(self):
        self.ds = build_fixture_dataset()
        self.roles = rr.load_boundary_roles(self.ds)

    def test_gate_valve_is_isolation(self):
        self.assertTrue(rr.has_boundary_role("GateValve", "isolation", self.roles))

    def test_relief_classes_both_map_to_relief_role(self):
        self.assertTrue(rr.has_boundary_role("SafetyValveOrFitting", "relief", self.roles))
        self.assertTrue(rr.has_boundary_role("Reliefdevices", "relief", self.roles))

    def test_check_valve_is_never_boundary_forming(self):
        # CheckValve is absent from every role set by construction (project
        # decision, data_specification.md §3.2) — never added to BOUNDARY_ROWS.
        self.assertFalse(rr.is_boundary_forming("CheckValve", self.roles))

    def test_pipe_reducer_is_not_boundary_forming(self):
        self.assertFalse(rr.is_boundary_forming("PipeReducer", self.roles))


class TestRdlResolution(unittest.TestCase):
    """§4.3.1's resolver (gold_layer_spec.md risk #18). The crosswalk/alias
    rows here are small synthetic examples for test purposes only — the
    real crosswalk assets are Workstream 1.5 and don't exist yet."""

    def setUp(self):
        from gold import rdf_mapper
        from gold.rdf_model import Dataset
        self.ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(self.ds)
        # Only GateValve is boundary-forming in this fixture; PressureVessel
        # (used below as the non-boundary example) never gets a role row.
        rdf_mapper.map_boundary_sets(self.ds, [{"component_class": "GateValve", "role": "isolation"}])
        rdf_mapper.map_rds_plm_crosswalk(self.ds, [
            {"rds_uri": "http://data.posccaesar.org/rdl/RDS427229",
             "plm_uri": "http://rds.posccaesar.org/ontology/plm/rdl/PCA_100005976",
             "match_type": "close"},
            {"rds_uri": "http://data.posccaesar.org/rdl/RDS999999",
             "plm_uri": "http://rds.posccaesar.org/ontology/plm/rdl/PCA_100000001",
             "match_type": "exact"},
        ])
        rdf_mapper.map_componentclass_plm_aliases(self.ds, [
            {"component_class": "GateValve", "plm_uri": "http://rds.posccaesar.org/ontology/plm/rdl/PCA_GATEVALVE"},
        ])
        self.crosswalk = rr.load_rds_plm_crosswalk(self.ds)
        self.aliases = rr.load_componentclass_plm_aliases(self.ds)
        self.boundary_roles = rr.load_boundary_roles(self.ds)

    def test_uri_bridge_close_match(self):
        result = rr.resolve_rdl_uri(
            "PressureVessel", "http://data.posccaesar.org/rdl/RDS427229",
            self.crosswalk, self.aliases, self.boundary_roles,
        )
        self.assertEqual(result, {
            "rdl_uri": "http://rds.posccaesar.org/ontology/plm/rdl/PCA_100005976",
            "rdl_match_type": "close",
            "pending_review": False,  # PressureVessel is not a boundary-forming class in this fixture
        })

    def test_uri_bridge_exact_match_never_needs_review_even_on_a_boundary_class(self):
        # Not a real scenario (RDS999999 isn't GateValve's real RDS code --
        # synthetic test data) but exercises the rule directly: exact never
        # sets pending_review, regardless of boundary role.
        result = rr.resolve_rdl_uri(
            "GateValve", "http://data.posccaesar.org/rdl/RDS999999",
            self.crosswalk, self.aliases, self.boundary_roles,
        )
        self.assertEqual(result["rdl_match_type"], "exact")
        self.assertFalse(result["pending_review"])

    def test_label_bridge_used_when_no_uri_present(self):
        # PostProc shape: component_class_uri is None (never exported).
        result = rr.resolve_rdl_uri("GateValve", None, self.crosswalk, self.aliases, self.boundary_roles)
        self.assertEqual(result["rdl_uri"], "http://rds.posccaesar.org/ontology/plm/rdl/PCA_GATEVALVE")
        self.assertEqual(result["rdl_match_type"], "label")

    def test_label_bridge_on_a_boundary_forming_class_requires_review(self):
        result = rr.resolve_rdl_uri("GateValve", None, self.crosswalk, self.aliases, self.boundary_roles)
        self.assertTrue(result["pending_review"])  # label + boundary-forming (isolation) -> review gate

    def test_uri_bridge_preferred_over_label_bridge_when_both_present(self):
        # GateValve has both a (synthetic) RDS URI match and a label alias;
        # the URI bridge must win as the higher-confidence signal.
        result = rr.resolve_rdl_uri(
            "GateValve", "http://data.posccaesar.org/rdl/RDS427229",
            self.crosswalk, self.aliases, self.boundary_roles,
        )
        self.assertEqual(result["rdl_match_type"], "close")

    def test_no_match_anywhere_leaves_rdl_uri_none(self):
        result = rr.resolve_rdl_uri("CustomPipingComponent", None, self.crosswalk, self.aliases, self.boundary_roles)
        self.assertEqual(result, {"rdl_uri": None, "rdl_match_type": None, "pending_review": False})

    def test_no_signal_at_all_is_not_a_crash(self):
        result = rr.resolve_rdl_uri(None, None, self.crosswalk, self.aliases, self.boundary_roles)
        self.assertEqual(result, {"rdl_uri": None, "rdl_match_type": None, "pending_review": False})


class TestDirectionalGuards(unittest.TestCase):
    """Reproduces walk.py's own documented scenarios exactly (see
    tests/fixtures.py docstring)."""

    def setUp(self):
        self.ds = build_fixture_dataset()
        self.catalogue = rr.load_fluid_catalogue(self.ds)
        self.C1 = R("component", "C1")
        self.N1 = R("component", "N1")
        self.F1 = R("component", "F1")
        self.RV1 = R("component", "RV1")

    def test_flare_guard_skips_flare_neighbour_when_flow_discharges_into_it(self):
        # C1 -> F1 forward: process fragment discharges INTO the flare.
        self.assertTrue(rr.flare_guard(self.ds, self.C1, self.F1, self.catalogue))

    def test_flare_guard_does_not_fire_when_direction_unknown(self):
        # Swap in a 'none' flow_sense connection and confirm the guard falls
        # through (returns False) rather than guessing.
        from gold import rdf_mapper
        ds2 = build_fixture_dataset()
        rdf_mapper.map_connection(ds2, {
            "connection_id": "CONN-C1-F1-none", "from_id": "C1", "to_id": "F1",
            "conn_type": "Process", "derived": True, "flow_sense": "none",
        })
        # The 'forward' edge from the base fixture is still present too, so
        # this test instead builds an isolated dataset with only the 'none' edge.
        ds3 = build_fixture_dataset()
        # Remove is not supported by the minimal Dataset; instead verify on a
        # fresh, isolated fixture that never asserts flowsTo in either direction.
        from gold.rdf_model import Dataset
        from tests.fixtures import FLUID_CATALOGUE_ROWS, BOUNDARY_ROWS, SEGMENTS, COMPONENTS
        ds_iso = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds_iso)
        rdf_mapper.map_fluid_catalogue(ds_iso, FLUID_CATALOGUE_ROWS)
        rdf_mapper.map_boundary_sets(ds_iso, BOUNDARY_ROWS)
        for seg in SEGMENTS:
            rdf_mapper.map_segment(ds_iso, seg)
        for comp in COMPONENTS:
            rdf_mapper.map_component(ds_iso, comp)
        rdf_mapper.map_connection(ds_iso, {
            "connection_id": "CONN-C1-F1-none2", "from_id": "C1", "to_id": "F1",
            "conn_type": "Process", "derived": True, "flow_sense": "none",
        })
        cat = rr.load_fluid_catalogue(ds_iso)
        self.assertFalse(rr.flare_guard(ds_iso, self.C1, self.F1, cat))

    def test_directional_consumer_guard_skips_supply_tie_in(self):
        # N1 -> C1 forward: nitrogen ties INTO the process fragment; N1 must
        # not be treated as C1's consumer.
        self.assertTrue(rr.directional_consumer_guard(self.ds, self.C1, self.N1))

    def test_directional_consumer_guard_does_not_skip_a_true_consumer(self):
        # From N1's point of view, C1 is downstream (N1 flows into C1, not
        # the reverse) — C1 IS a legitimate consumer signal for N1's fragment.
        self.assertFalse(rr.directional_consumer_guard(self.ds, self.N1, self.C1))

    def test_relief_attribution_returns_protected_side(self):
        self.assertEqual(rr.relief_attribution(self.ds, self.RV1), self.C1)

    def test_relief_attribution_none_when_direction_unknown(self):
        from gold import rdf_mapper
        from gold.rdf_model import Dataset
        from tests.fixtures import FLUID_CATALOGUE_ROWS, BOUNDARY_ROWS, SEGMENTS, COMPONENTS
        ds_iso = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds_iso)
        rdf_mapper.map_fluid_catalogue(ds_iso, FLUID_CATALOGUE_ROWS)
        rdf_mapper.map_boundary_sets(ds_iso, BOUNDARY_ROWS)
        for seg in SEGMENTS:
            rdf_mapper.map_segment(ds_iso, seg)
        for comp in COMPONENTS:
            rdf_mapper.map_component(ds_iso, comp)
        rdf_mapper.map_connection(ds_iso, {
            "connection_id": "CONN-C1-RV1-none", "from_id": "C1", "to_id": "RV1",
            "conn_type": "Process", "derived": False, "flow_sense": "none",
        })
        self.assertIsNone(rr.relief_attribution(ds_iso, self.RV1))


class TestAllocationRules(unittest.TestCase):
    def test_boundary_valve_goes_to_higher_priority_side(self):
        self.assertEqual(rr.allocate_boundary_valve_or_blind(1, 2), "side_a")
        self.assertEqual(rr.allocate_boundary_valve_or_blind(3, 2), "side_b")
        self.assertEqual(rr.allocate_boundary_valve_or_blind(2, 2), "side_a")  # tie -> side_a

    def test_steam_trap_always_steam_side(self):
        self.assertEqual(rr.allocate_steam_trap(), "steam_side")

    def test_sampling_connection_is_upstream(self):
        self.assertEqual(rr.allocate_sampling_connection(), "upstream")

    def test_interface_valve_allocation(self):
        self.assertEqual(rr.allocate_interface_valve("flare"), "flare")
        self.assertEqual(rr.allocate_interface_valve("drain"), "drain")
        with self.assertRaises(ValueError):
            rr.allocate_interface_valve("bogus")

    def test_vessel_instrument_goes_to_equipment_system(self):
        self.assertEqual(rr.allocate_vessel_column_instrument(), "equipment_system")

    def test_fragment_merge_key_utility_vs_process(self):
        self.assertEqual(rr.fragment_merge_key("utility", "SUP07", fluid="N"), ("SUP07", "utility", "N"))
        self.assertEqual(
            rr.fragment_merge_key("process", "SUP07", anchor_equipment="362-C0953"),
            ("SUP07", "process", "362-C0953"),
        )
        with self.assertRaises(ValueError):
            rr.fragment_merge_key("utility", "SUP07")  # missing fluid


if __name__ == "__main__":
    unittest.main()
