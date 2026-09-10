import unittest
from datetime import date, datetime

from gold import vocab as v
from gold.gold_job import (
    GoldInputs,
    build_bitemporal_tables,
    build_rdf_dataset,
    build_rdf_dataset_from_gold_objects,
)
from gold.oracle_guard import assert_oracle_confined
from gold.rdf_model import URIRef
from gold.temporal import DeltaType, GoldRow, current_truth
from tests.fixtures import (
    BOUNDARY_ROWS,
    COMPONENTS,
    CONNECTIONS,
    FLUID_CATALOGUE_ROWS,
    SEGMENTS,
)


def make_inputs():
    equipment = [{"equipment_id": "EQ-1", "tag": "362-C0953", "nozzle_ids": ["N1"]}]
    lineage = {"DWG-1": {"ingested_at": datetime(2026, 9, 1, 6, 0), "drawing_revision_date": date(2026, 8, 15)}}
    return GoldInputs(
        components=COMPONENTS,
        segments=SEGMENTS,
        equipment=equipment,
        connections=CONNECTIONS,
        fluid_catalogue=FLUID_CATALOGUE_ROWS,
        boundary_rows=BOUNDARY_ROWS,
        drawing_lineage=lineage,
    )


class TestGoldJob(unittest.TestCase):
    def test_build_rdf_dataset_is_oracle_clean_end_to_end(self):
        ds = build_rdf_dataset(make_inputs())
        assert_oracle_confined(ds)  # re-assert; build_rdf_dataset already does this internally
        self.assertGreater(len(ds), 0)
        self.assertIn("https://pidsys.example/ns#graph/masterdata", ds.graphs())
        self.assertIn("https://pidsys.example/ns#graph/refdata", ds.graphs())

    def test_build_rdf_dataset_harvests_equipment_class_from_duplicate_component_row(self):
        # Same real-data finding (2026-09-11) as build_rdf_dataset_from_
        # gold_objects's equivalent test, exercised on this legacy
        # Silver-direct path too: the Equipment-kind component row this
        # function skips can carry a real component_class that has nowhere
        # else to land, since inputs.equipment's own equipment_class is
        # always None here (make_inputs() never sets it).
        equipment = [{"equipment_id": "EQ-1", "tag": "362-C0953", "nozzle_ids": ["N1"]}]
        components = COMPONENTS + [
            {"component_id": "EQ-1", "component_class": "VerticalDrums", "tag": "362-C0953",
             "segment_id": None, "kind": "Equipment"},
        ]
        lineage = {"DWG-1": {"ingested_at": datetime(2026, 9, 1, 6, 0), "drawing_revision_date": date(2026, 8, 15)}}
        inputs = GoldInputs(
            components=components, segments=SEGMENTS, equipment=equipment, connections=CONNECTIONS,
            fluid_catalogue=FLUID_CATALOGUE_ROWS, boundary_rows=BOUNDARY_ROWS, drawing_lineage=lineage,
        )
        ds = build_rdf_dataset(inputs)
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        equip_node = URIRef(v.uri(v.PIDSYS + "equipment/", "EQ-1"))
        domain_cls = URIRef(v.component_class_uri("VerticalDrums"))
        types = set(q.o for q in ds.triples(s=equip_node, p=rdf_type, graph=v.GRAPH_MASTERDATA))
        self.assertEqual(types, {URIRef(v.C_EQUIPMENT), domain_cls})
        # the duplicate component-kind node itself is still never mapped
        comp_node = URIRef(v.uri(v.PIDSYS + "component/", "EQ-1"))
        self.assertEqual(list(ds.triples(s=comp_node, p=rdf_type, graph=v.GRAPH_MASTERDATA)), [])

    def test_build_bitemporal_tables_first_load_opens_current_rows_for_every_object(self):
        inputs = make_inputs()
        tables = build_bitemporal_tables(inputs, previous_gold_rows={}, run_tx_from=datetime(2026, 9, 1, 6, 0))
        for kind, expected_ids in (
            ("component", {c["component_id"] for c in COMPONENTS}),
            ("segment", {s["segment_id"] for s in SEGMENTS}),
            ("equipment", {"EQ-1"}),
            ("connection", {c["connection_id"] for c in CONNECTIONS}),
        ):
            current = current_truth(tables[kind])
            self.assertEqual({r.anchor_id for r in current}, expected_ids)
            for r in current:
                self.assertEqual(r.valid_from, date(2026, 8, 15))
                self.assertIsNone(r.valid_to)
                self.assertIsNone(r.tx_to)

    def test_second_run_with_no_changes_does_not_bump_versions(self):
        inputs = make_inputs()
        first = build_bitemporal_tables(inputs, previous_gold_rows={}, run_tx_from=datetime(2026, 9, 1, 6, 0))
        second = build_bitemporal_tables(inputs, previous_gold_rows=first, run_tx_from=datetime(2026, 9, 2, 6, 0))
        for kind in first:
            self.assertEqual(len(first[kind]), len(second[kind]))


def _gold_row(object_kind, anchor_id, attrs, valid_from, valid_to=None, tx_from=None, tx_to=None):
    return GoldRow(
        object_kind=object_kind, anchor_id=anchor_id, attrs=attrs,
        valid_from=valid_from, valid_to=valid_to,
        tx_from=tx_from or datetime(2026, 9, 1, 6, 0), tx_to=tx_to,
    )


def make_gold_rows_by_kind():
    """A tiny gold_objects-shaped fixture: one current + one superseded
    component (so current_truth's filtering is actually exercised), one
    current equipment, one current connection, one current 'line' row WITH
    real per-piece detail (`attrs["pieces"]`, as `spark_bridge.py::
    resolve_line_attrs_for_event` now attaches), and a second 'line' row
    with none (the "written before this capability existed, or the
    caller's segment_rows didn't cover it" case §`build_rdf_dataset_from_
    gold_objects` reports rather than silently guessing at) — deliberately
    not reusing tests/fixtures.py's Silver-shaped dicts, since GoldRow.attrs
    holds only engineering attributes, never the identity key (gold_job.py's
    own re-injection wrinkle this fixture is built to exercise)."""
    return {
        "component": [
            _gold_row("component", "C1", {"component_class": "PipeReducer", "tag": None},
                      valid_from=date(2026, 7, 1), valid_to=date(2026, 8, 15),
                      tx_from=datetime(2026, 7, 1, 6, 0), tx_to=datetime(2026, 9, 1, 6, 0)),
            _gold_row("component", "C1", {"component_class": "PipeReducer", "tag": "C1-NEW"},
                      valid_from=date(2026, 8, 15), tx_from=datetime(2026, 9, 1, 6, 0)),
            # a real-data finding (2026-09-10): silver/reconstruct.py also
            # emits Equipment elements as component-kind rows -- the SAME
            # physical item "equipment" below already maps correctly. Must
            # be skipped and reported, not mapped as a second RDF node for
            # the one physical pump. (component_class=None here is the
            # genuinely-unclassified case; test_harvests_equipment_class_
            # from_the_skipped_duplicate below covers the more common real
            # case -- confirmed 2026-09-11 -- where this row DOES carry a
            # real class that map_equipment must not lose.)
            _gold_row("component", "EQ-1", {"kind": "Equipment", "component_class": None, "tag": "362-C0953"},
                      valid_from=date(2026, 8, 15), tx_from=datetime(2026, 9, 1, 6, 0)),
        ],
        "equipment": [
            _gold_row("equipment", "EQ-1", {"tag": "362-C0953", "nozzle_ids": ["N1"]},
                      valid_from=date(2026, 8, 15), tx_from=datetime(2026, 9, 1, 6, 0)),
        ],
        "connection": [
            _gold_row("connection", "CONN-C1-F1",
                      {"from_id": "C1", "to_id": "F1", "conn_type": "Process",
                       "derived": True, "flow_sense": "forward"},
                      valid_from=date(2026, 8, 15), tx_from=datetime(2026, 9, 1, 6, 0)),
        ],
        "line": [
            _gold_row("line", "DWG-1|PG-000001", {
                "fluid": ("PG",), "unit": ("BAR",), "diameter": ('2"',),
                "piece_count": 2, "line_attr_inconsistent": False,
                "pieces": [
                    {"segment_id": "S1", "seg_tag": "PG-000001", "fluid": "PG", "diameter": '2"',
                     "src_turnover": "362-09", "src_subsystem": "01"},
                    {"segment_id": "S2", "seg_tag": "PG-000001", "fluid": "PG", "diameter": '2"'},
                ],
            }, valid_from=date(2026, 8, 15), tx_from=datetime(2026, 9, 1, 6, 0)),
            _gold_row("line", "DWG-1|PG-000002", {"fluid": ("N",), "piece_count": 1},
                      valid_from=date(2026, 8, 15), tx_from=datetime(2026, 9, 1, 6, 0)),
        ],
    }


class TestBuildRdfDatasetFromGoldObjects(unittest.TestCase):
    def test_projects_only_current_truth_rows(self):
        ds, _skipped, _equip_dupes = build_rdf_dataset_from_gold_objects(
            make_gold_rows_by_kind(), FLUID_CATALOGUE_ROWS, BOUNDARY_ROWS,
        )
        assert_oracle_confined(ds)  # re-assert; the function already does this internally
        node = URIRef(v.uri(v.PIDSYS + "component/", "C1"))
        tags = [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_TAG), graph=v.GRAPH_MASTERDATA)]
        self.assertEqual(tags, ["C1-NEW"])  # the superseded row's tag must not appear

    def test_asserts_bitemporal_predicates_from_the_gold_row(self):
        ds, _skipped, _equip_dupes = build_rdf_dataset_from_gold_objects(
            make_gold_rows_by_kind(), FLUID_CATALOGUE_ROWS, BOUNDARY_ROWS,
        )
        node = URIRef(v.uri(v.PIDSYS + "component/", "C1"))
        valid_from = [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_VALID_FROM), graph=v.GRAPH_MASTERDATA)]
        self.assertEqual(valid_from, [date(2026, 8, 15)])
        # the current row's valid_to/tx_to are None -> no triple asserted
        self.assertEqual(list(ds.triples(s=node, p=URIRef(v.P_VALID_TO), graph=v.GRAPH_MASTERDATA)), [])

    def test_equipment_kind_component_rows_are_skipped_and_reported_not_double_mapped(self):
        ds, _skipped, equipment_dupes = build_rdf_dataset_from_gold_objects(
            make_gold_rows_by_kind(), FLUID_CATALOGUE_ROWS, BOUNDARY_ROWS,
        )
        self.assertEqual(equipment_dupes, ["EQ-1"])
        # no pidsys:PipingComponent-family node for EQ-1 ...
        comp_node = URIRef(v.uri(v.PIDSYS + "component/", "EQ-1"))
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        self.assertEqual(list(ds.triples(s=comp_node, p=rdf_type, graph=v.GRAPH_MASTERDATA)), [])
        # ... only the real pidsys:Equipment node, from the "equipment" kind loop
        equip_node = URIRef(v.uri(v.PIDSYS + "equipment/", "EQ-1"))
        self.assertEqual(
            len(list(ds.triples(s=equip_node, p=rdf_type, o=URIRef(v.C_EQUIPMENT), graph=v.GRAPH_MASTERDATA))), 1
        )

    def test_harvests_equipment_class_from_the_skipped_duplicate(self):
        # Real data finding (2026-09-11): the "component"-kind Equipment
        # duplicate frequently carries a real component_class (silver_
        # equipment's own equipment_class field is always None -- "class
        # enrichment: later"). That value must reach the real pidsys:
        # Equipment node as `equipment_class`, matched by tag, rather than
        # being discarded along with the skipped duplicate row.
        rows = make_gold_rows_by_kind()
        rows["component"][-1].attrs["component_class"] = "HeatExchangers"
        ds, _reported, equipment_dupes = build_rdf_dataset_from_gold_objects(
            rows, FLUID_CATALOGUE_ROWS, BOUNDARY_ROWS,
        )
        self.assertEqual(equipment_dupes, ["EQ-1"])  # still correctly skipped/reported, not double-mapped
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        equip_node = URIRef(v.uri(v.PIDSYS + "equipment/", "EQ-1"))
        domain_cls = URIRef(v.component_class_uri("HeatExchangers"))
        types = set(q.o for q in ds.triples(s=equip_node, p=rdf_type, graph=v.GRAPH_MASTERDATA))
        self.assertEqual(types, {URIRef(v.C_EQUIPMENT), domain_cls})
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=equip_node, p=URIRef(v.P_EQUIPMENT_CLASS), graph=v.GRAPH_MASTERDATA)],
            ["HeatExchangers"],
        )
        # the skipped component-kind node itself still gets no RDF type at all
        comp_node = URIRef(v.uri(v.PIDSYS + "component/", "EQ-1"))
        self.assertEqual(list(ds.triples(s=comp_node, p=rdf_type, graph=v.GRAPH_MASTERDATA)), [])

    def test_line_is_projected_with_real_child_segment_nodes(self):
        ds, _reported, _equip_dupes = build_rdf_dataset_from_gold_objects(
            make_gold_rows_by_kind(), FLUID_CATALOGUE_ROWS, BOUNDARY_ROWS,
        )
        assert_oracle_confined(ds)  # the nested map_segment calls must still route src_turnover/src_subsystem correctly
        line_node = URIRef(v.uri(v.PIDSYS + "line/", "DWG-1|PG-000001"))
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=line_node, p=URIRef(v.P_PIECE_COUNT), graph=v.GRAPH_MASTERDATA)],
            [2],
        )
        s1 = URIRef(v.uri(v.PIDSYS + "segment/", "S1"))
        s2 = URIRef(v.uri(v.PIDSYS + "segment/", "S2"))
        # each real physical piece is its own node, carrying its own scalar attrs
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=s1, p=URIRef(v.P_DIAMETER), graph=v.GRAPH_MASTERDATA)],
            ['2"'],
        )
        # partOf the Line, alongside whatever physical containment map_segment already asserts
        self.assertEqual(len(list(ds.triples(s=s1, p=URIRef(v.P_PART_OF), o=line_node, graph=v.GRAPH_MASTERDATA))), 1)
        self.assertEqual(len(list(ds.triples(s=s2, p=URIRef(v.P_PART_OF), o=line_node, graph=v.GRAPH_MASTERDATA))), 1)
        # a piece inherits its parent Line's own bi-temporal interval, not one of its own
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=s1, p=URIRef(v.P_VALID_FROM), graph=v.GRAPH_MASTERDATA)],
            [date(2026, 8, 15)],
        )
        # the oracle field on S1 must still land only in graph:oracle, never graph:masterdata
        self.assertEqual(list(ds.triples(s=s1, p=URIRef(v.P_SRC_TURNOVER_SYSTEM), graph=v.GRAPH_MASTERDATA)), [])
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=s1, p=URIRef(v.P_SRC_TURNOVER_SYSTEM), graph=v.GRAPH_ORACLE)],
            ["362-09"],
        )

    def test_lines_with_no_piece_detail_are_still_projected_and_reported(self):
        ds, reported, _equip_dupes = build_rdf_dataset_from_gold_objects(
            make_gold_rows_by_kind(), FLUID_CATALOGUE_ROWS, BOUNDARY_ROWS,
        )
        self.assertEqual(reported, ["DWG-1|PG-000002"])
        line_node = URIRef(v.uri(v.PIDSYS + "line/", "DWG-1|PG-000002"))
        # the Line node itself is still real and correctly asserted...
        rdf_type = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        self.assertEqual(
            len(list(ds.triples(s=line_node, p=rdf_type, o=URIRef(v.C_LINE), graph=v.GRAPH_MASTERDATA))), 1
        )
        self.assertEqual(
            [q.o.toPython() for q in ds.triples(s=line_node, p=URIRef(v.P_FLUID_CODE), graph=v.GRAPH_MASTERDATA)],
            ["N"],
        )
        # ...it just has zero children, since no "pieces" were supplied
        segments_of_this_line = list(ds.triples(p=URIRef(v.P_PART_OF), o=line_node, graph=v.GRAPH_MASTERDATA))
        self.assertEqual(segments_of_this_line, [])

    def test_as_of_valid_projects_a_past_revision(self):
        ds, _skipped, _equip_dupes = build_rdf_dataset_from_gold_objects(
            make_gold_rows_by_kind(), FLUID_CATALOGUE_ROWS, BOUNDARY_ROWS,
            as_of_valid=date(2026, 7, 15),
        )
        node = URIRef(v.uri(v.PIDSYS + "component/", "C1"))
        tags = [q.o.toPython() for q in ds.triples(s=node, p=URIRef(v.P_TAG), graph=v.GRAPH_MASTERDATA)]
        self.assertEqual(tags, [])  # the pre-revision row had tag=None
        component_class = list(ds.triples(s=node, p=URIRef(v.P_COMPONENT_CLASS), graph=v.GRAPH_MASTERDATA))
        self.assertEqual(len(component_class), 1)  # but the row itself is still projected


if __name__ == "__main__":
    unittest.main()
