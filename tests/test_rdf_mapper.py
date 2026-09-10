import unittest
from datetime import date, datetime

from gold import vocab as v
from gold.oracle_guard import OracleLeakage, assert_oracle_confined
from gold.rdf_model import Dataset, URIRef
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

    def test_component_with_no_component_class_gets_unclassified_fallback_not_a_crash(self):
        # Real Silver data finding (2026-09-10): component_class can be None
        # (or, via gold/spark_bridge.py's None-value filtering, simply
        # absent from the dict entirely) -- both must produce the honest
        # fallback class, never a KeyError.
        from gold import rdf_mapper
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        rdfs_sub = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
        for comp in (
            {"component_id": "C-NONE", "component_class": None, "tag": "T1"},
            {"component_id": "C-ABSENT", "tag": "T2"},  # key entirely missing
        ):
            ds = Dataset()
            rdf_mapper.declare_ontology_skeleton(ds)
            rdf_mapper.map_component(ds, comp)
            node = URIRef(v.uri(v.PIDSYS + "component/", comp["component_id"]))
            self.assertEqual(
                len(list(ds.triples(s=node, p=rdf_type, o=URIRef(v.C_UNCLASSIFIED_COMPONENT), graph=v.GRAPH_MASTERDATA))),
                1,
            )
            self.assertEqual(
                len(list(ds.triples(s=URIRef(v.C_UNCLASSIFIED_COMPONENT), p=rdfs_sub,
                                     o=URIRef(v.C_PIPING_COMPONENT), graph=v.GRAPH_MASTERDATA))),
                1,
            )
            # no bogus componentClass literal, and no RDL-pending flag on a
            # class that was never asserted as a real domain class
            self.assertEqual(list(ds.triples(s=node, p=URIRef(v.P_COMPONENT_CLASS), graph=v.GRAPH_MASTERDATA)), [])
            self.assertEqual(
                list(ds.triples(s=URIRef(v.C_UNCLASSIFIED_COMPONENT), p=URIRef(v.P_RDL_URI_PENDING), graph=v.GRAPH_MASTERDATA)),
                [],
            )
            # tag still asserted -- only component_class is affected
            self.assertEqual(
                [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_TAG), graph=v.GRAPH_MASTERDATA)],
                [comp["tag"]],
            )

    def test_catchall_component_class_strings_get_the_same_unclassified_fallback(self):
        # Real Project A/DEXPI finding (2026-09-11): these literal
        # ComponentClass strings are emitted by the source tool itself when
        # it couldn't resolve a specific class -- confirmed against the
        # real PCA PLM equipment ontology (specs/semantics/equipment.rdf)
        # to have no counterpart there at all, so they're treated exactly
        # like component_class=None rather than minted as bogus domain
        # classes that could never be RDL-resolved.
        from gold import rdf_mapper
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        rdfs_sub = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
        for catchall in sorted(v.CATCHALL_COMPONENT_CLASSES):
            comp = {"component_id": f"C-{catchall}", "component_class": catchall, "tag": "T1"}
            ds = Dataset()
            rdf_mapper.declare_ontology_skeleton(ds)
            rdf_mapper.map_component(ds, comp)
            node = URIRef(v.uri(v.PIDSYS + "component/", comp["component_id"]))
            self.assertEqual(
                len(list(ds.triples(s=node, p=rdf_type, o=URIRef(v.C_UNCLASSIFIED_COMPONENT), graph=v.GRAPH_MASTERDATA))),
                1,
                f"{catchall} should fall back to C_UNCLASSIFIED_COMPONENT",
            )
            # no bogus domain class minted at all for the catch-all string
            bogus_cls = URIRef(v.component_class_uri(catchall))
            self.assertEqual(list(ds.triples(s=bogus_cls, p=rdfs_sub, graph=v.GRAPH_MASTERDATA)), [])
            # no componentClass literal, no RDL-pending flag -- same as the None case
            self.assertEqual(list(ds.triples(s=node, p=URIRef(v.P_COMPONENT_CLASS), graph=v.GRAPH_MASTERDATA)), [])
            self.assertEqual(
                list(ds.triples(s=URIRef(v.C_UNCLASSIFIED_COMPONENT), p=URIRef(v.P_RDL_URI_PENDING), graph=v.GRAPH_MASTERDATA)),
                [],
            )
            # tag still asserted -- only component_class is affected
            self.assertEqual(
                [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_TAG), graph=v.GRAPH_MASTERDATA)],
                [comp["tag"]],
            )

    def test_catchall_match_is_exact_not_substring(self):
        # A real class name that merely contains "Custom" must NOT be
        # swept into the fallback -- CATCHALL_COMPONENT_CLASSES is an
        # exact-match set, not a prefix/substring rule (none seen in real
        # data so far, but the mechanism must not false-positive if one
        # ever appears).
        from gold import rdf_mapper
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds)
        comp = {"component_id": "C-LOOKALIKE", "component_class": "CustomValveXYZ", "tag": "T9"}
        rdf_mapper.map_component(ds, comp)
        node = URIRef(v.uri(v.PIDSYS + "component/", "C-LOOKALIKE"))
        domain_cls = URIRef(v.component_class_uri("CustomValveXYZ"))
        self.assertEqual(
            len(list(ds.triples(s=node, p=rdf_type, o=domain_cls, graph=v.GRAPH_MASTERDATA))), 1
        )
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_COMPONENT_CLASS), graph=v.GRAPH_MASTERDATA)],
            ["CustomValveXYZ"],
        )

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

    def test_no_temporal_predicates_when_absent_from_input(self):
        # The fixture dicts (Silver-shaped, no _valid_from/_valid_to/_tx_from/
        # _tx_to keys) must produce byte-for-byte the same RDF as before this
        # feature existed -- build_rdf_dataset_from_gold_objects is the only
        # caller that populates those keys (gold_job.py).
        for pred in (v.P_VALID_FROM, v.P_VALID_TO, v.P_TX_FROM, v.P_TX_TO):
            self.assertEqual(list(self.ds.triples(p=URIRef(pred), graph=v.GRAPH_MASTERDATA)), [])


class TestMapEquipment(unittest.TestCase):
    # Real data finding (2026-09-11): silver_equipment's own equipment_class
    # is always None ("class enrichment: later" -- silver/reconstruct.py),
    # but the item's actual classification is frequently sitting on its
    # Equipment-kind silver_components duplicate instead (gold_job.py now
    # harvests it by tag and passes it in here). map_equipment must handle
    # both shapes -- present and absent -- without a caller needing to know
    # where the value came from.
    def test_equipment_with_no_class_is_unaffected_by_this_addition(self):
        from gold import rdf_mapper
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds)
        rdf_mapper.map_equipment(ds, {"equipment_id": "EQ-1", "tag": "362-C0953", "nozzle_ids": []})
        node = URIRef(v.uri(v.PIDSYS + "equipment/", "EQ-1"))
        types = [q.o for q in ds.triples(s=node, p=rdf_type, graph=v.GRAPH_MASTERDATA)]
        self.assertEqual(types, [URIRef(v.C_EQUIPMENT)])  # no extra domain-class type asserted
        self.assertEqual(list(ds.triples(s=node, p=URIRef(v.P_EQUIPMENT_CLASS), graph=v.GRAPH_MASTERDATA)), [])

    def test_equipment_with_a_real_class_gets_a_domain_subclass_under_equipment(self):
        from gold import rdf_mapper
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        rdfs_sub = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
        ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds)
        rdf_mapper.map_equipment(ds, {
            "equipment_id": "EQ-2", "tag": "V-2201", "nozzle_ids": [],
            "equipment_class": "VerticalDrums",
        })
        node = URIRef(v.uri(v.PIDSYS + "equipment/", "EQ-2"))
        domain_cls = URIRef(v.component_class_uri("VerticalDrums"))
        # both the bare Equipment type (unchanged) and the more specific
        # domain class are asserted -- RDFS subclass transitivity already
        # gives an inferencer pidsys:Equipment from the domain type alone,
        # but the explicit bare type keeps this byte-for-byte compatible
        # with every existing map_equipment caller/test.
        types = set(q.o for q in ds.triples(s=node, p=rdf_type, graph=v.GRAPH_MASTERDATA))
        self.assertEqual(types, {URIRef(v.C_EQUIPMENT), domain_cls})
        self.assertEqual(
            len(list(ds.triples(s=domain_cls, p=rdfs_sub, o=URIRef(v.C_EQUIPMENT), graph=v.GRAPH_MASTERDATA))), 1,
        )
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_EQUIPMENT_CLASS), graph=v.GRAPH_MASTERDATA)],
            ["VerticalDrums"],
        )
        # honest RDL-pending marker, same discipline as map_component
        self.assertEqual(
            len(list(ds.triples(s=domain_cls, p=URIRef(v.P_RDL_URI_PENDING), graph=v.GRAPH_MASTERDATA))), 1,
        )

    def test_temporal_predicates_asserted_when_present_on_component(self):
        from gold import rdf_mapper
        ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds)
        rdf_mapper.map_component(ds, {
            "component_id": "C-TEMPORAL", "component_class": "GateValve", "tag": "GV-99",
            "_valid_from": date(2026, 8, 15), "_valid_to": None,
            "_tx_from": datetime(2026, 9, 1, 6, 0), "_tx_to": None,
        })
        node = URIRef(v.uri(v.PIDSYS + "component/", "C-TEMPORAL"))
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_VALID_FROM), graph=v.GRAPH_MASTERDATA)],
            [date(2026, 8, 15)],
        )
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_TX_FROM), graph=v.GRAPH_MASTERDATA)],
            [datetime(2026, 9, 1, 6, 0)],
        )
        # still-open intervals (valid_to/tx_to None) assert nothing -- that
        # IS "current" bi-temporally (temporal.py::GoldRow.is_current).
        self.assertEqual(list(ds.triples(s=node, p=URIRef(v.P_VALID_TO), graph=v.GRAPH_MASTERDATA)), [])
        self.assertEqual(list(ds.triples(s=node, p=URIRef(v.P_TX_TO), graph=v.GRAPH_MASTERDATA)), [])

    def test_temporal_predicates_asserted_on_a_closed_interval(self):
        from gold import rdf_mapper
        ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds)
        rdf_mapper.map_connection(ds, {
            "connection_id": "CONN-TEMPORAL", "from_id": "C1", "to_id": "F1",
            "conn_type": "Process", "derived": True, "flow_sense": "forward",
            "_valid_from": date(2026, 8, 15), "_valid_to": date(2026, 9, 1),
            "_tx_from": datetime(2026, 9, 1, 6, 0), "_tx_to": datetime(2026, 9, 2, 6, 0),
        })
        node = URIRef(v.uri(v.PIDSYS + "connection/", "CONN-TEMPORAL"))
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_VALID_TO), graph=v.GRAPH_MASTERDATA)],
            [date(2026, 9, 1)],
        )
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_TX_TO), graph=v.GRAPH_MASTERDATA)],
            [datetime(2026, 9, 2, 6, 0)],
        )


if __name__ == "__main__":
    unittest.main()
