import unittest

from gold import vocab as v
from gold.oracle_guard import OracleLeakage, assert_oracle_confined
from gold.rdf_model import URIRef
from tests.fixtures import build_fixture_dataset


class TestRdfMapper(unittest.TestCase):
    def setUp(self):
        self.ds = build_fixture_dataset()

    def test_oracle_fields_confined_by_default(self):
        # The fixture segments carry no src_turnover/src_subsystem at all;
        # confirm the guard passes cleanly on a dataset with none present.
        assert_oracle_confined(self.ds)  # should not raise

    def test_oracle_leakage_is_detected(self):
        self.ds.add(
            URIRef(v.uri(v.PIDSYS + "segment/", "SG-PG1")),
            URIRef(v.P_SRC_TURNOVER_SYSTEM),
            URIRef("http://example/leaked"),
            v.GRAPH_MASTERDATA,  # wrong graph — this must be caught
        )
        with self.assertRaises(OracleLeakage):
            assert_oracle_confined(self.ds)

    def test_oracle_field_in_the_oracle_graph_is_fine(self):
        from gold import rdf_mapper
        seg = {"segment_id": "SG-PG1", "fluid": "PG", "seg_tag": "PG-000001",
               "src_turnover": "362-09", "src_subsystem": "01"}
        rdf_mapper.map_segment(self.ds, seg)
        assert_oracle_confined(self.ds)  # should not raise: it's in graph:oracle

    def test_connection_requires_derived_flag(self):
        from gold import rdf_mapper
        with self.assertRaises(ValueError):
            rdf_mapper.map_connection(self.ds, {
                "connection_id": "CONN-BAD", "from_id": "C1", "to_id": "F1",
                "conn_type": "Process", "flow_sense": "forward",
                # no 'derived' key — structural invariant violation
            })

    def test_connection_emits_symmetric_isconnectedto_both_directions(self):
        c1 = URIRef(v.uri(v.PIDSYS + "component/", "C1"))
        f1 = URIRef(v.uri(v.PIDSYS + "component/", "F1"))
        forward = list(self.ds.triples(s=c1, p=URIRef(v.P_IS_CONNECTED_TO), o=f1, graph=v.GRAPH_MASTERDATA))
        backward = list(self.ds.triples(s=f1, p=URIRef(v.P_IS_CONNECTED_TO), o=c1, graph=v.GRAPH_MASTERDATA))
        self.assertEqual(len(forward), 1)
        self.assertEqual(len(backward), 1)

    def test_flows_to_oriented_by_flow_sense_forward_only(self):
        c1 = URIRef(v.uri(v.PIDSYS + "component/", "C1"))
        f1 = URIRef(v.uri(v.PIDSYS + "component/", "F1"))
        fwd = list(self.ds.triples(s=c1, p=URIRef(v.P_FLOWS_TO), o=f1, graph=v.GRAPH_MASTERDATA))
        rev = list(self.ds.triples(s=f1, p=URIRef(v.P_FLOWS_TO), o=c1, graph=v.GRAPH_MASTERDATA))
        self.assertEqual(len(fwd), 1)
        self.assertEqual(len(rev), 0)

    def test_none_flow_sense_emits_no_flows_to_triple(self):
        from gold import rdf_mapper
        from gold.rdf_model import Dataset
        ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds)
        rdf_mapper.map_connection(ds, {
            "connection_id": "CONN-XY", "from_id": "X", "to_id": "Y",
            "conn_type": "Process", "derived": True, "flow_sense": "none",
        })
        flows = list(ds.triples(p=URIRef(v.P_FLOWS_TO), graph=v.GRAPH_MASTERDATA))
        self.assertEqual(len(flows), 0)

    def test_domain_class_is_subclass_of_piping_component_which_is_ido_physical_object(self):
        rdfs_sub = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
        gate_valve = URIRef(v.component_class_uri("GateValve"))
        chain1 = list(self.ds.triples(s=gate_valve, p=rdfs_sub, o=URIRef(v.C_PIPING_COMPONENT), graph=v.GRAPH_MASTERDATA))
        chain2 = list(self.ds.triples(s=URIRef(v.C_PIPING_COMPONENT), p=rdfs_sub, o=URIRef(v.IDO_PHYSICAL_OBJECT), graph=v.GRAPH_MASTERDATA))
        self.assertEqual(len(chain1), 1)
        self.assertEqual(len(chain2), 1)
        # never asserted directly as a bare, unconfirmed IDO domain class:
        self.assertNotIn("FunctionalObject", v.IDO_PHYSICAL_OBJECT)

    def test_rdl_uri_pending_when_not_resolved(self):
        pending = list(self.ds.triples(p=URIRef(v.P_RDL_URI_PENDING), graph=v.GRAPH_MASTERDATA))
        self.assertTrue(len(pending) >= 1)


if __name__ == "__main__":
    unittest.main()
