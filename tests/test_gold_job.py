import unittest
from datetime import date, datetime

from gold.gold_job import GoldInputs, build_bitemporal_tables, build_rdf_dataset
from gold.oracle_guard import assert_oracle_confined
from gold.temporal import current_truth
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


if __name__ == "__main__":
    unittest.main()
