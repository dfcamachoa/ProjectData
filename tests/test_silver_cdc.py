import unittest
from datetime import date, datetime

from gold.silver_cdc import SilverCdcEvent, apply_silver_cdc_events
from gold.temporal import DeltaType, current_truth


def ev(**kwargs):
    defaults = dict(
        object_kind="segment",
        anchor_id="ANCHOR-SG-1",
        delta_type=DeltaType.NEW,
        drawing_number="DWG-1",
        drawing_revision_date=date(2026, 1, 1),
        bronze_ingested_at=datetime(2026, 1, 1, 8, 0),
        attrs={"fluid": "PG"},
        anchor_hash="h(anchor)",
        content_hash_eng="h(eng)",
        content_hash_audit="h(audit)",
    )
    defaults.update(kwargs)
    return SilverCdcEvent(**defaults)


class TestSilverCdcEvent(unittest.TestCase):
    def test_rejects_unknown_object_kind(self):
        with self.assertRaises(ValueError):
            ev(object_kind="bogus")

    def test_accepts_string_delta_type(self):
        e = ev(delta_type="Modified")
        self.assertEqual(e.delta_type, DeltaType.MODIFIED)


class TestApplySilverCdcEvents(unittest.TestCase):
    def test_new_event_opens_a_current_row_keyed_on_stage_e_anchor(self):
        rows = apply_silver_cdc_events({}, [ev()])
        current = current_truth(rows["segment"])
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].anchor_id, "ANCHOR-SG-1")
        self.assertEqual(current[0].attrs["fluid"], "PG")

    def test_delete_plus_recreate_that_stage_e_already_collapsed_produces_no_gold_churn(self):
        # Stage E's own acceptance test (silver_layer_spec.md §3.5) proves a
        # delete+recreate of an unchanged object yields ZERO silver_cdc
        # deltas — so this scenario never even reaches Gold as an event. The
        # only thing to prove at this layer is that when Stage E does NOT
        # emit an event, Gold's state is simply unchanged.
        rows = apply_silver_cdc_events({}, [ev()])
        rows_after_recreate = apply_silver_cdc_events(rows, [])  # Stage E emitted nothing
        self.assertEqual(
            [r.anchor_id for r in current_truth(rows_after_recreate["segment"])],
            [r.anchor_id for r in current_truth(rows["segment"])],
        )

    def test_modified_event_ordinary_forward_closes_only_valid_axis(self):
        rows = apply_silver_cdc_events({}, [ev()])
        rows = apply_silver_cdc_events(rows, [ev(
            delta_type=DeltaType.MODIFIED,
            drawing_revision_date=date(2026, 3, 1),
            bronze_ingested_at=datetime(2026, 3, 1, 9, 0),
            attrs={"fluid": "AG"},
            content_hash_eng="h(eng)-2",
        )])
        history = rows["segment"]
        self.assertEqual(len(history), 2)
        old = next(r for r in history if r.attrs["fluid"] == "PG")
        new = next(r for r in history if r.attrs["fluid"] == "AG")
        self.assertEqual(old.valid_to, date(2026, 3, 1))
        self.assertIsNone(old.tx_to)  # ordinary supersession — see temporal.py
        self.assertTrue(new.is_current())

    def test_deleted_event_closes_both_axes_and_opens_nothing(self):
        rows = apply_silver_cdc_events({}, [ev()])
        rows = apply_silver_cdc_events(rows, [ev(
            delta_type=DeltaType.DELETED,
            drawing_revision_date=date(2026, 4, 1),
            bronze_ingested_at=datetime(2026, 4, 1, 10, 0),
            attrs={},
        )])
        history = rows["segment"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].valid_to, date(2026, 4, 1))
        self.assertEqual(history[0].tx_to, datetime(2026, 4, 1, 10, 0))
        self.assertEqual(current_truth(history), [])

    def test_events_across_object_kinds_land_in_separate_tables(self):
        rows = apply_silver_cdc_events({}, [
            ev(object_kind="segment", anchor_id="SG-1"),
            ev(object_kind="component", anchor_id="C-1", attrs={"component_class": "GateValve"}),
            ev(object_kind="equipment", anchor_id="EQ-1", attrs={"tag": "362-C0953"}),
            ev(object_kind="connection", anchor_id="CONN-1", attrs={"conn_type": "Process"}),
        ])
        for kind in ("segment", "component", "equipment", "connection"):
            self.assertEqual(len(current_truth(rows[kind])), 1)

    def test_events_applied_in_bronze_ingested_at_order_regardless_of_input_order(self):
        later = ev(
            delta_type=DeltaType.MODIFIED,
            drawing_revision_date=date(2026, 3, 1),
            bronze_ingested_at=datetime(2026, 3, 1),
            attrs={"fluid": "LATER"},
        )
        earlier_new = ev(bronze_ingested_at=datetime(2026, 1, 1))
        # Pass the Modified event BEFORE the New event; apply_silver_cdc_events
        # must still process New first (it sorts by bronze_ingested_at).
        rows = apply_silver_cdc_events({}, [later, earlier_new])
        current = current_truth(rows["segment"])
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].attrs["fluid"], "LATER")


if __name__ == "__main__":
    unittest.main()
