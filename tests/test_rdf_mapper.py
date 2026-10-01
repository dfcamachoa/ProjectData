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

    def test_connection_emits_symmetric_connectedto_both_directions(self):
        # The undirected backbone is now NATIVE ido:connectedTo (P_IS_CONNECTED_TO
        # resolves to the real IDO term, not a minted pidsys: one) -- emitted in
        # both directions explicitly.
        self.assertTrue(v.P_IS_CONNECTED_TO.startswith(v.IDO),
                        "backbone connectivity must be native ido:, not pidsys:")
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

    def test_resolved_rdl_uri_asserts_sameas_and_match_type_not_pending(self):
        # §4.3.1 (risk #18): a caller that has already resolved a class to a
        # real PLM URI passes it (plus its confidence) straight through --
        # map_component itself performs no lookup, it only projects.
        from gold import rdf_mapper
        owl_same_as = URIRef("http://www.w3.org/2002/07/owl#sameAs")
        ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds)
        comp = {"component_id": "C-RESOLVED", "component_class": "GateValve", "tag": "GV-1"}
        rdf_mapper.map_component(ds, comp, rdl_uri="http://example/PLM_GATEVALVE", rdl_match_type="exact")
        domain_cls = URIRef(v.component_class_uri("GateValve"))
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=domain_cls, p=owl_same_as, graph=v.GRAPH_MASTERDATA)],
            ["http://example/PLM_GATEVALVE"],
        )
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=domain_cls, p=URIRef(v.P_RDL_MATCH_TYPE), graph=v.GRAPH_MASTERDATA)],
            ["exact"],
        )
        # a resolved class never carries the pending marker at all
        self.assertEqual(list(ds.triples(s=domain_cls, p=URIRef(v.P_RDL_URI_PENDING), graph=v.GRAPH_MASTERDATA)), [])
        self.assertEqual(list(ds.triples(s=domain_cls, p=URIRef(v.P_PENDING_REVIEW), graph=v.GRAPH_MASTERDATA)), [])

    def test_close_match_with_pending_review_flag(self):
        from gold import rdf_mapper
        ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds)
        comp = {"component_id": "C-CLOSE", "component_class": "GateValve", "tag": "GV-2"}
        rdf_mapper.map_component(
            ds, comp, rdl_uri="http://example/PLM_GATEVALVE", rdl_match_type="close", pending_review=True,
        )
        domain_cls = URIRef(v.component_class_uri("GateValve"))
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=domain_cls, p=URIRef(v.P_RDL_MATCH_TYPE), graph=v.GRAPH_MASTERDATA)],
            ["close"],
        )
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=domain_cls, p=URIRef(v.P_PENDING_REVIEW), graph=v.GRAPH_MASTERDATA)],
            [True],
        )

    def test_unresolved_class_still_gets_the_pending_marker_unchanged(self):
        # Backward-compatibility check: calling map_component with none of
        # the new kwargs must produce byte-for-byte the same RDF as before
        # this feature existed.
        from gold import rdf_mapper
        ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds)
        comp = {"component_id": "C-PENDING", "component_class": "GateValve", "tag": "GV-3"}
        rdf_mapper.map_component(ds, comp)
        domain_cls = URIRef(v.component_class_uri("GateValve"))
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=domain_cls, p=URIRef(v.P_RDL_URI_PENDING), graph=v.GRAPH_MASTERDATA)],
            [True],
        )
        self.assertEqual(list(ds.triples(s=domain_cls, p=URIRef(v.P_RDL_MATCH_TYPE), graph=v.GRAPH_MASTERDATA)), [])
        self.assertEqual(list(ds.triples(s=domain_cls, p=URIRef(v.P_PENDING_REVIEW), graph=v.GRAPH_MASTERDATA)), [])

    def test_map_rds_plm_crosswalk_asserts_skos_predicates(self):
        from gold import rdf_mapper
        ds = Dataset()
        rdf_mapper.map_rds_plm_crosswalk(ds, [
            {"rds_uri": "http://data.posccaesar.org/rdl/RDS1", "plm_uri": "http://example/PLM1", "match_type": "exact"},
            {"rds_uri": "http://data.posccaesar.org/rdl/RDS2", "plm_uri": "http://example/PLM2", "match_type": "close"},
        ])
        exact = URIRef("http://www.w3.org/2004/02/skos/core#exactMatch")
        close = URIRef("http://www.w3.org/2004/02/skos/core#closeMatch")
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=URIRef("http://data.posccaesar.org/rdl/RDS1"), p=exact, graph=v.GRAPH_REFDATA)],
            ["http://example/PLM1"],
        )
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=URIRef("http://data.posccaesar.org/rdl/RDS2"), p=close, graph=v.GRAPH_REFDATA)],
            ["http://example/PLM2"],
        )

    def test_map_componentclass_plm_aliases_round_trips_the_raw_string(self):
        from gold import rdf_mapper
        ds = Dataset()
        rdf_mapper.map_componentclass_plm_aliases(ds, [
            {"component_class": "GateValve", "plm_uri": "http://example/PLM_GATEVALVE"},
        ])
        node = URIRef(v.component_class_alias_uri("GateValve"))
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_COMPONENT_CLASS), graph=v.GRAPH_REFDATA)],
            ["GateValve"],
        )
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_ALIAS_OF), graph=v.GRAPH_REFDATA)],
            ["http://example/PLM_GATEVALVE"],
        )

    def test_domain_class_is_subclass_of_piping_component_which_is_ido_physical_artefact(self):
        # Reconciled to pidsys_extension.ttl (v4.2/FDIS): a real component
        # class is a PipingComponent, which anchors to ido:PhysicalArtefact
        # (man-made physical thing) -- the finer, correct anchor. In v4.2
        # PhysicalArtefact rdfs:subClassOf PhysicalObject directly, so a
        # component is still a PhysicalObject, just more precisely typed.
        rdfs_sub = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
        gate_valve = URIRef(v.component_class_uri("GateValve"))
        chain1 = list(self.ds.triples(s=gate_valve, p=rdfs_sub, o=URIRef(v.C_PIPING_COMPONENT), graph=v.GRAPH_MASTERDATA))
        chain2 = list(self.ds.triples(s=URIRef(v.C_PIPING_COMPONENT), p=rdfs_sub, o=URIRef(v.IDO_PHYSICAL_ARTEFACT), graph=v.GRAPH_MASTERDATA))
        self.assertEqual(len(chain1), 1)
        self.assertEqual(len(chain2), 1)
        # never asserted directly as a bare, unconfirmed IDO domain class:
        self.assertNotIn("FunctionalObject", v.IDO_PHYSICAL_ARTEFACT)

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


class TestFunctionalMembership(unittest.TestCase):
    """Reconciliation to pidsys_extension.ttl: a CommissioningSystem's members
    are FUNCTIONAL individuals reached via the hasFunction/realizedIn pattern,
    never the physical components directly (P_MEMBER subPropertyOf
    ido:hasFunctionalPart, whose range is functional)."""

    def _system_ds(self):
        from gold import rdf_mapper
        ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(ds)
        rdf_mapper.map_system_result(ds, {
            "system_id": "SYS-1", "display_name": "Test System",
            "members": ["C1", "C2"], "boundaries": ["B1"],
        }, rule_name="test-rule")
        return ds

    def test_member_predicate_points_at_a_function_not_the_physical_component(self):
        ds = self._system_ds()
        sys_node = URIRef(v.uri(v.PIDSYS + "system/", "SYS-1"))
        member_objs = {q.o for q in ds.triples(s=sys_node, p=URIRef(v.P_MEMBER), graph=v.GRAPH_RESULTS)}
        # members are the function individuals, NOT the component individuals
        self.assertIn(URIRef(v.uri(v.PIDSYS + "function/", "C1")), member_objs)
        self.assertIn(URIRef(v.uri(v.PIDSYS + "function/", "C2")), member_objs)
        self.assertNotIn(URIRef(v.uri(v.PIDSYS + "component/", "C1")), member_objs)

    def test_member_predicate_is_declared_a_functional_part_subproperty(self):
        ds = self._system_ds()
        rdfs_sub = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
        # declare_ontology_skeleton asserts P_MEMBER/P_BOUNDARY_MEMBER
        # rdfs:subClassOf (used here as sub-property axiom form) hasFunctionalPart
        self.assertEqual(
            len(list(ds.triples(s=URIRef(v.P_MEMBER), p=rdfs_sub,
                                o=URIRef(v.P_HAS_FUNCTIONAL_PART), graph=v.GRAPH_MASTERDATA))), 1)
        self.assertEqual(
            len(list(ds.triples(s=URIRef(v.P_BOUNDARY_MEMBER), p=rdfs_sub,
                                o=URIRef(v.P_HAS_FUNCTIONAL_PART), graph=v.GRAPH_MASTERDATA))), 1)

    def test_realization_bridge_component_hasFunction_realizedIn_activity(self):
        ds = self._system_ds()
        comp = URIRef(v.uri(v.PIDSYS + "component/", "C1"))
        func = URIRef(v.uri(v.PIDSYS + "function/", "C1"))
        activity = URIRef(v.uri(v.PIDSYS + "activity/", "SYS-1"))
        # component --hasFunction--> function
        self.assertEqual(
            len(list(ds.triples(s=comp, p=URIRef(v.P_HAS_FUNCTION), o=func, graph=v.GRAPH_RESULTS))), 1)
        # function --realizedIn--> activity  (range Activity, NEVER the component)
        self.assertEqual(
            len(list(ds.triples(s=func, p=URIRef(v.P_REALIZED_IN), o=activity, graph=v.GRAPH_RESULTS))), 1)
        # realizedIn must NOT point at the physical component
        self.assertEqual(
            list(ds.triples(s=func, p=URIRef(v.P_REALIZED_IN), o=comp, graph=v.GRAPH_RESULTS)), [])

    def test_function_and_activity_are_typed(self):
        ds = self._system_ds()
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        func = URIRef(v.uri(v.PIDSYS + "function/", "C1"))
        activity = URIRef(v.uri(v.PIDSYS + "activity/", "SYS-1"))
        self.assertEqual(
            len(list(ds.triples(s=func, p=rdf_type, o=URIRef(v.C_FUNCTION), graph=v.GRAPH_RESULTS))), 1)
        self.assertEqual(
            len(list(ds.triples(s=activity, p=rdf_type, o=URIRef(v.IDO_ACTIVITY), graph=v.GRAPH_RESULTS))), 1)

    def test_boundary_member_also_uses_functional_membership(self):
        ds = self._system_ds()
        sys_node = URIRef(v.uri(v.PIDSYS + "system/", "SYS-1"))
        bnd_objs = {q.o for q in ds.triples(s=sys_node, p=URIRef(v.P_BOUNDARY_MEMBER), graph=v.GRAPH_RESULTS)}
        self.assertIn(URIRef(v.uri(v.PIDSYS + "function/", "B1")), bnd_objs)
        self.assertNotIn(URIRef(v.uri(v.PIDSYS + "component/", "B1")), bnd_objs)


class TestFunctionalOnlySegments(unittest.TestCase):
    """Reconciliation: PipingSegment and Line are ido:System (functional
    groupings), NEVER physical objects/artefacts. The reasoner confirmed a
    dual-typed segment is satisfiable-but-wrong; the single correct typing is
    enforced in projection."""

    def setUp(self):
        from gold import rdf_mapper
        self.ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(self.ds)

    def _anchor(self, cls):
        rdfs_sub = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
        return {q.o for q in self.ds.triples(s=URIRef(cls), p=rdfs_sub, graph=v.GRAPH_MASTERDATA)}

    def test_segment_anchored_to_system_not_physical(self):
        anchors = self._anchor(v.C_PIPING_SEGMENT)
        self.assertIn(URIRef(v.IDO_SYSTEM), anchors)
        self.assertNotIn(URIRef(v.IDO_PHYSICAL_OBJECT), anchors)
        self.assertNotIn(URIRef(v.IDO_PHYSICAL_ARTEFACT), anchors)

    def test_line_anchored_to_system_not_physical(self):
        anchors = self._anchor(v.C_LINE)
        self.assertIn(URIRef(v.IDO_SYSTEM), anchors)
        self.assertNotIn(URIRef(v.IDO_PHYSICAL_OBJECT), anchors)
        self.assertNotIn(URIRef(v.IDO_PHYSICAL_ARTEFACT), anchors)

    def test_all_grouping_levels_are_systems(self):
        for cls in (v.C_SUBLINE, v.C_PIPELINE_SYSTEM, v.C_PROCESS_UNIT,
                    v.C_STARTUP_PACKAGE, v.C_COMMISSIONING_SYSTEM):
            self.assertIn(URIRef(v.IDO_SYSTEM), self._anchor(cls), f"{cls} should anchor to ido:System")

    def test_components_and_equipment_are_physical_artefacts(self):
        self.assertIn(URIRef(v.IDO_PHYSICAL_ARTEFACT), self._anchor(v.C_PIPING_COMPONENT))
        self.assertIn(URIRef(v.IDO_PHYSICAL_ARTEFACT), self._anchor(v.C_EQUIPMENT))

    def test_nozzle_is_a_feature(self):
        self.assertIn(URIRef(v.IDO_FEATURE), self._anchor(v.C_NOZZLE))

    def test_connection_and_opc_are_information_objects(self):
        self.assertIn(URIRef(v.IDO_INFORMATION_OBJECT), self._anchor(v.C_CONNECTION))
        self.assertIn(URIRef(v.IDO_INFORMATION_OBJECT), self._anchor(v.C_OFF_PAGE_CONNECTOR))


class TestMapOffPageConnector(unittest.TestCase):
    """Reconciliation: the OPC node is its own class (an ido:InformationObject,
    sibling of Connection), with a DIRECT terminates edge to its segment. The
    mating pair, separately, is an ordinary Connection of type
    'Off-Page continuation'."""

    def setUp(self):
        from gold import rdf_mapper
        self.rdf_mapper = rdf_mapper
        self.ds = Dataset()
        rdf_mapper.declare_ontology_skeleton(self.ds)

    def test_opc_node_typed_and_terminates_its_segment(self):
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        self.rdf_mapper.map_off_page_connector(self.ds, {
            "opc_id": "OPC-1", "tag": "OPC-A-001", "on_segment": "SG-1",
            "opc_type": "Off Drawing Piping Connector",
            "flow_direction": "FlowOutPipeOffPageConnector",
            "component_class_uri": "http://sandbox.dexpi.org/rdl/FlowOutPipeOffPageConnector",
        })
        node = URIRef(v.uri(v.PIDSYS + "off_page_connector/", "OPC-1"))
        self.assertEqual(
            len(list(self.ds.triples(s=node, p=rdf_type, o=URIRef(v.C_OFF_PAGE_CONNECTOR), graph=v.GRAPH_MASTERDATA))), 1)
        # direct terminates edge -> the segment (NOT a reified connection)
        seg = URIRef(v.uri(v.PIDSYS + "segment/", "SG-1"))
        self.assertEqual(
            len(list(self.ds.triples(s=node, p=URIRef(v.P_TERMINATES), o=seg, graph=v.GRAPH_MASTERDATA))), 1)

    def test_instrument_opc_gets_node_but_no_terminates(self):
        # An instrument OPC (opc_type explicitly "...Instrument...") does not sit
        # on a PipingSegment; even with an on_segment present, terminates must NOT
        # be emitted (model range is PipingSegment; instrumentation deferred, §6.4).
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        self.rdf_mapper.map_off_page_connector(self.ds, {
            "opc_id": "OPC-INST", "on_segment": "SG-9",
            "opc_type": "Off Drawing Instrument Connector",
        })
        node = URIRef(v.uri(v.PIDSYS + "off_page_connector/", "OPC-INST"))
        self.assertEqual(
            len(list(self.ds.triples(s=node, p=rdf_type, o=URIRef(v.C_OFF_PAGE_CONNECTOR), graph=v.GRAPH_MASTERDATA))), 1)
        self.assertEqual(
            list(self.ds.triples(s=node, p=URIRef(v.P_TERMINATES), graph=v.GRAPH_MASTERDATA)), [])

    def test_signal_flow_role_no_terminates(self):
        # A Signal flow role does not sit on a pipe -> no terminates (defence in
        # depth; signal OPCs aren't harvested, but if one arrives it's node-only).
        self.rdf_mapper.map_off_page_connector(self.ds, {
            "opc_id": "OPC-SIG", "on_segment": "SG-8",
            "flow_direction": "SignalOffPageConnector",
        })
        node = URIRef(v.uri(v.PIDSYS + "off_page_connector/", "OPC-SIG"))
        self.assertEqual(
            list(self.ds.triples(s=node, p=URIRef(v.P_TERMINATES), graph=v.GRAPH_MASTERDATA)), [])

    def test_utility_connector_on_a_pipe_DOES_terminate(self):
        # Real Project-A data (2026-09-23): opc_type "Utility Connector" but a
        # piping flow role (ComponentClass FlowOutPipeOffPageConnector) — a utility
        # LINE continuation that sits on a pipe. The business label must NOT
        # suppress terminates; the flow role decides. So this DOES get a terminates.
        self.rdf_mapper.map_off_page_connector(self.ds, {
            "opc_id": "OPC-UTIL", "on_segment": "SG-7", "opc_type": "Utility Connector",
            "flow_direction": "FlowOutPipeOffPageConnector",
        })
        node = URIRef(v.uri(v.PIDSYS + "off_page_connector/", "OPC-UTIL"))
        seg = URIRef(v.uri(v.PIDSYS + "segment/", "SG-7"))
        self.assertEqual(
            len(list(self.ds.triples(s=node, p=URIRef(v.P_TERMINATES), o=seg, graph=v.GRAPH_MASTERDATA))), 1)

    def test_opc_passes_through_dexpi_sandbox_uri_as_a_uri_not_a_class(self):
        self.rdf_mapper.map_off_page_connector(self.ds, {
            "opc_id": "OPC-2", "on_segment": "SG-2", "opc_type": "Off Drawing Piping Connector",
            "component_class_uri": "http://sandbox.dexpi.org/rdl/FlowInPipeOffPageConnector",
        })
        node = URIRef(v.uri(v.PIDSYS + "off_page_connector/", "OPC-2"))
        objs = [q.o for q in self.ds.triples(s=node, p=URIRef(v.P_COMPONENT_CLASS_URI), graph=v.GRAPH_MASTERDATA)]
        self.assertEqual(objs, [URIRef("http://sandbox.dexpi.org/rdl/FlowInPipeOffPageConnector")])
        # it is a URIRef (traceability reference), never subclassed as a domain class
        rdfs_sub = URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf")
        self.assertEqual(
            list(self.ds.triples(s=objs[0], p=rdfs_sub, graph=v.GRAPH_MASTERDATA)), [])

    def test_matching_pair_offpage_string_resolves_opc_endpoints(self):
        # Silver Stage C writes conn_type "OffPage" with only from_id/to_id (no
        # *_kind). map_connection must recognise it AND resolve endpoints to
        # off_page_connector nodes, not components.
        self.rdf_mapper.map_connection(self.ds, {
            "connection_id": "OPC-PAIR-1", "from_id": "OPC-1", "to_id": "OPC-2",
            "conn_type": "OffPage", "derived": True, "flow_sense": "none",
        })
        node = URIRef(v.uri(v.PIDSYS + "connection/", "OPC-PAIR-1"))
        frm = [q.o for q in self.ds.triples(s=node, p=URIRef(v.P_FROM_OBJECT), graph=v.GRAPH_MASTERDATA)]
        to = [q.o for q in self.ds.triples(s=node, p=URIRef(v.P_TO_OBJECT), graph=v.GRAPH_MASTERDATA)]
        # endpoints resolve to OPC nodes (not component nodes) despite no *_kind
        self.assertEqual(frm, [URIRef(v.uri(v.PIDSYS + "off_page_connector/", "OPC-1"))])
        self.assertEqual(to, [URIRef(v.uri(v.PIDSYS + "off_page_connector/", "OPC-2"))])
        derived = [q.o.toPython() for q in self.ds.triples(s=node, p=URIRef(v.P_DERIVED), graph=v.GRAPH_MASTERDATA)]
        self.assertEqual(derived, [True])
        conn_type = [q.o.toPython() for q in self.ds.triples(s=node, p=URIRef(v.P_CONN_TYPE), graph=v.GRAPH_MASTERDATA)]
        self.assertEqual(conn_type, ["OffPage"])

    def test_matching_pair_long_string_still_works(self):
        # The model's "Off-Page continuation" string is recognised too.
        self.rdf_mapper.map_connection(self.ds, {
            "connection_id": "OPC-PAIR-2", "from_id": "OPC-3", "to_id": "OPC-4",
            "conn_type": "Off-Page continuation", "derived": True, "flow_sense": "forward",
        })
        node = URIRef(v.uri(v.PIDSYS + "connection/", "OPC-PAIR-2"))
        frm = [q.o for q in self.ds.triples(s=node, p=URIRef(v.P_FROM_OBJECT), graph=v.GRAPH_MASTERDATA)]
        self.assertEqual(frm, [URIRef(v.uri(v.PIDSYS + "off_page_connector/", "OPC-3"))])


if __name__ == "__main__":
    unittest.main()
