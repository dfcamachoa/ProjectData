"""Cross-validates `gold/owl_reasoning.py`'s real owlrl OWL-RL entailment
against this project's own hand-walked class-hierarchy assumption, on the
same synthetic fixture `test_rdf_mapper.py` / `test_rules_reference.py`
already use. Requires `rdflib` + `owlrl` -- unexecuted in this sandbox for
the reason `gold/rdf_model.py`'s module docstring gives (PyPI is not on
this org's egress allowlist); run this once both are installed in your
environment.
"""
import unittest

import rdflib

from gold import owl_reasoning as owl
from gold import vocab as v
from tests.fixtures import build_fixture_dataset


class TestOwlReasoning(unittest.TestCase):
    def setUp(self):
        self.ds = build_fixture_dataset()

    def test_physical_object_closure_agrees_with_owlrl(self):
        report = owl.cross_check_physical_object_closure(self.ds)
        self.assertEqual(report["asserted_directly_as_physical_object"], 0,
                          "no instance should be asserted ido:PhysicalObject "
                          "directly — only the class chain carries that "
                          "(rdf_mapper.py correctness note #1)")
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["unexpected_extra"], [])
        self.assertTrue(report["agrees_with_project_assumption"])
        # the fixture's four components (N1, C1, F1, RV1) are exactly the
        # instances expected to close to ido:PhysicalObject.
        self.assertEqual(report["expected_instances"], 4)
        self.assertEqual(report["inferred_as_physical_object"], 4)

    def test_segments_and_connections_are_not_physical_objects(self):
        # A negative check on the same closure: segments/connections are
        # typed under classes NEVER declared subClassOf ido:PhysicalObject
        # (declare_ontology_skeleton only roots PipingComponent/Equipment/
        # Nozzle there), so owlrl must NOT fold them in either — confirms
        # cross_check_physical_object_closure isn't vacuously true because
        # it over-counts every masterdata instance.
        before = owl.masterdata_graph(self.ds)
        reasoned = rdflib.Graph()
        for t in before:
            reasoned.add(t)
        owl.run_owl_rl_closure(reasoned)
        physical_after = owl.instances_of(reasoned, rdflib.URIRef(v.IDO_PHYSICAL_OBJECT))

        segment_instances = owl.instances_of(before, rdflib.URIRef(v.C_PIPING_SEGMENT))
        connection_instances = owl.instances_of(before, rdflib.URIRef(v.C_CONNECTION))
        self.assertTrue(segment_instances, "fixture should have at least one segment")
        self.assertTrue(connection_instances, "fixture should have at least one connection")
        self.assertFalse(segment_instances & physical_after)
        self.assertFalse(connection_instances & physical_after)

    def test_run_owl_rl_closure_mutates_in_place_and_returns_same_graph(self):
        g = rdflib.Graph()
        gate_valve = rdflib.URIRef(v.component_class_uri("GateValve"))
        g.add((gate_valve, rdflib.RDFS.subClassOf, rdflib.URIRef(v.C_PIPING_COMPONENT)))
        g.add((rdflib.URIRef(v.C_PIPING_COMPONENT), rdflib.RDFS.subClassOf,
               rdflib.URIRef(v.IDO_PHYSICAL_OBJECT)))
        inst = rdflib.URIRef("https://pidsys.example/ns#component/X1")
        g.add((inst, rdflib.RDF.type, gate_valve))
        before_len = len(g)

        result = owl.run_owl_rl_closure(g)
        self.assertIs(result, g)
        self.assertGreater(len(g), before_len)
        self.assertIn((inst, rdflib.RDF.type, rdflib.URIRef(v.IDO_PHYSICAL_OBJECT)), g)


if __name__ == "__main__":
    unittest.main()
