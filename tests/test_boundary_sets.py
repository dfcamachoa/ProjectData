"""Boundary reference data (data_specification.md §3.2) — the CheckValve /
empty-Role fix of 2026-09-23. The real Boundary sheet lists CheckValve with
an empty Role cell; the old mapper minted it as a `boundary_role/nan` member,
which made `is_boundary_forming("CheckValve")` True — the opposite of the
project decision. These tests pin the corrected reading."""
import unittest

from gold import rdf_mapper
from gold import rules_reference as rr
from gold import vocab as v
from gold.rdf_model import Dataset, URIRef
from tests.fixtures import BOUNDARY_ROWS, build_fixture_dataset

NAN = float("nan")


def _map(rows):
    ds = Dataset()
    report = rdf_mapper.map_boundary_sets(ds, rows)
    return ds, report


def _members(ds):
    return list(ds.triples(p=URIRef(v.P_BOUNDARY_MEMBER), graph=v.GRAPH_REFDATA))


def _forming_false(ds, cls):
    return [q.o.toPython() for q in ds.triples(s=URIRef(v.component_class_uri(cls)),
                                                p=URIRef(v.P_BOUNDARY_FORMING),
                                                graph=v.GRAPH_REFDATA)]


class TestFixtureMirrorsRealSheet(unittest.TestCase):
    def setUp(self):
        self.ds = build_fixture_dataset()
        self.roles = rr.load_boundary_roles(self.ds)

    def test_fixture_carries_the_checkvalve_row(self):
        self.assertIn("CheckValve", [r["component_class"] for r in BOUNDARY_ROWS])

    def test_checkvalve_not_boundary_forming(self):
        self.assertFalse(rr.is_boundary_forming("CheckValve", self.roles))

    def test_exclusion_recorded_as_data(self):
        self.assertEqual(_forming_false(self.ds, "CheckValve"), [False])

    def test_no_nan_role_node(self):
        subjects = {str(q.s) for q in _members(self.ds)}
        self.assertEqual({s.rsplit("/", 1)[-1] for s in subjects}, set(v.BOUNDARY_ROLES))

    def test_seven_members_as_before(self):
        self.assertEqual(len(_members(self.ds)), 7)

    def test_real_boundaries_unchanged(self):
        for cls in ("GateValve", "GlobeValve", "ButterflyValve", "PipeFlangeSpacer",
                    "SafetyValveOrFitting", "Reliefdevices", "SteamTrap"):
            self.assertTrue(rr.is_boundary_forming(cls, self.roles), cls)


class TestSheetShapes(unittest.TestCase):
    def test_empty_string_and_none_roles_are_exclusions(self):
        _, report = _map([{"component_class": "CheckValve", "role": ""},
                          {"component_class": "Flange", "role": None}])
        self.assertEqual(report, {"members": {}, "excluded": ["CheckValve", "Flange"]})

    def test_boundary_no_flag_wins_over_a_role(self):
        ds, report = _map([{"component_class": "CheckValve", "role": "isolation", "boundary": "No"}])
        self.assertEqual(report["excluded"], ["CheckValve"])
        self.assertEqual(_members(ds), [])

    def test_boundary_yes_flag_with_role(self):
        _, report = _map([{"component_class": "GateValve", "role": "Isolation", "boundary": "Yes"}])
        self.assertEqual(report["members"], {"isolation": ["GateValve"]})

    def test_nan_flag_falls_back_to_role(self):
        _, report = _map([{"component_class": "GateValve", "role": "isolation", "boundary": NAN}])
        self.assertEqual(report["members"], {"isolation": ["GateValve"]})

    def test_unknown_role_on_a_boundary_row_raises(self):
        with self.assertRaisesRegex(ValueError, "isolaton"):
            _map([{"component_class": "GateValve", "role": "isolaton"}])

    def test_bad_flag_raises(self):
        with self.assertRaisesRegex(ValueError, "maybe"):
            _map([{"component_class": "GateValve", "role": "isolation", "boundary": "maybe"}])

    def test_contradiction_raises(self):
        with self.assertRaisesRegex(ValueError, "contradicts"):
            _map([{"component_class": "CheckValve", "role": "isolation"},
                  {"component_class": "CheckValve", "role": NAN}])

    def test_blank_class_rows_ignored(self):
        _, report = _map([{"component_class": NAN, "role": NAN}])
        self.assertEqual(report, {"members": {}, "excluded": []})


class TestReaderGuard(unittest.TestCase):
    def test_stray_role_node_is_ignored(self):
        # a graph written by the OLD mapper still carries boundary_role/nan
        ds = build_fixture_dataset()
        ds.add(URIRef(v.uri(v.PIDSYS + "boundary_role/", "nan")), URIRef(v.P_BOUNDARY_MEMBER),
               URIRef(v.component_class_uri("CheckValve")), v.GRAPH_REFDATA)
        roles = rr.load_boundary_roles(ds)
        self.assertNotIn("nan", roles)
        self.assertFalse(rr.is_boundary_forming("CheckValve", roles))


if __name__ == "__main__":
    unittest.main()
