import unittest
from datetime import date, datetime

from gold.temporal import (
    DeltaType,
    RetroactiveCorrection,
    apply_delta,
    current_truth,
    diff_snapshots,
)


class TestApplyDelta(unittest.TestCase):
    def test_new_opens_an_open_interval(self):
        rows = []
        apply_delta(rows, "segment", "SG-1", DeltaType.NEW,
                    {"fluid": "PG"}, date(2026, 1, 1), datetime(2026, 1, 1, 8, 0))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertIsNone(r.valid_to)
        self.assertIsNone(r.tx_to)
        self.assertTrue(r.is_current())

    def test_new_on_existing_current_anchor_raises(self):
        rows = []
        apply_delta(rows, "segment", "SG-1", DeltaType.NEW,
                    {"fluid": "PG"}, date(2026, 1, 1), datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            apply_delta(rows, "segment", "SG-1", DeltaType.NEW,
                        {"fluid": "PG"}, date(2026, 2, 1), datetime(2026, 2, 1))

    def test_modified_closes_old_row_and_opens_new_one(self):
        rows = []
        apply_delta(rows, "segment", "SG-1", DeltaType.NEW,
                    {"fluid": "PG", "diameter": "6in"}, date(2026, 1, 1), datetime(2026, 1, 1, 8))
        apply_delta(rows, "segment", "SG-1", DeltaType.MODIFIED,
                    {"fluid": "PG", "diameter": "8in"}, date(2026, 3, 1), datetime(2026, 3, 1, 9))
        self.assertEqual(len(rows), 2)
        old, new = rows
        self.assertEqual(old.valid_to, date(2026, 3, 1))
        # Ordinary forward supersession does NOT retroactively close the old
        # row's transaction interval — we still hold it as a trusted record
        # of what was true during its own valid window; see temporal.py's
        # MODIFIED-branch docstring for why closing tx_to here would be wrong.
        self.assertIsNone(old.tx_to)
        self.assertTrue(new.is_current())
        self.assertEqual(new.attrs["diameter"], "8in")

    def test_correction_same_valid_from_closes_only_the_transaction_axis(self):
        rows = []
        apply_delta(rows, "segment", "SG-1", DeltaType.NEW,
                    {"fluid": "PG", "diameter": "6in"}, date(2026, 1, 1), datetime(2026, 1, 1, 8))
        # We discover, on 2026-02-01, that what was actually true from
        # 2026-01-01 onward was 8in all along (a correction, not a new
        # engineering fact) — same valid_from, different content.
        apply_delta(rows, "segment", "SG-1", DeltaType.MODIFIED,
                    {"fluid": "PG", "diameter": "8in"}, date(2026, 1, 1), datetime(2026, 2, 1, 9))
        self.assertEqual(len(rows), 2)
        old, new = rows
        self.assertEqual(old.valid_to, None)  # valid interval untouched by a correction
        self.assertEqual(old.tx_to, datetime(2026, 2, 1, 9))  # only tx axis closes
        self.assertEqual(new.valid_from, date(2026, 1, 1))
        self.assertTrue(new.is_current())

    def test_modified_with_identical_content_is_a_noop(self):
        rows = []
        apply_delta(rows, "segment", "SG-1", DeltaType.NEW,
                    {"fluid": "PG"}, date(2026, 1, 1), datetime(2026, 1, 1))
        apply_delta(rows, "segment", "SG-1", DeltaType.MODIFIED,
                    {"fluid": "PG"}, date(2026, 3, 1), datetime(2026, 3, 1))
        self.assertEqual(len(rows), 1)  # no version bump on identical content

    def test_oracle_only_change_never_triggers_a_version_bump(self):
        # Silver's compute-only firewall, restated at Gold: a source-turnover
        # correction alone must never look like an engineering Modify.
        rows = []
        apply_delta(rows, "segment", "SG-1", DeltaType.NEW,
                    {"fluid": "PG", "src_turnover": "362-09"}, date(2026, 1, 1), datetime(2026, 1, 1))
        apply_delta(rows, "segment", "SG-1", DeltaType.MODIFIED,
                    {"fluid": "PG", "src_turnover": "362-14"}, date(2026, 3, 1), datetime(2026, 3, 1))
        self.assertEqual(len(rows), 1)

    def test_deleted_closes_without_opening_a_new_row(self):
        rows = []
        apply_delta(rows, "component", "C-1", DeltaType.NEW,
                    {"component_class": "GateValve"}, date(2026, 1, 1), datetime(2026, 1, 1))
        apply_delta(rows, "component", "C-1", DeltaType.DELETED,
                    {}, date(2026, 4, 1), datetime(2026, 4, 1, 12))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r.valid_to, date(2026, 4, 1))
        self.assertEqual(r.tx_to, datetime(2026, 4, 1, 12))
        self.assertFalse(r.is_current())

    def test_retroactive_correction_raises_instead_of_guessing(self):
        rows = []
        apply_delta(rows, "segment", "SG-1", DeltaType.NEW,
                    {"fluid": "PG"}, date(2026, 3, 1), datetime(2026, 3, 1))
        with self.assertRaises(RetroactiveCorrection):
            apply_delta(rows, "segment", "SG-1", DeltaType.MODIFIED,
                        {"fluid": "AG"}, date(2026, 1, 1), datetime(2026, 3, 15))

    def test_modify_or_delete_with_no_current_row_becomes_new_never_silently_dropped(self):
        rows = []
        apply_delta(rows, "component", "C-9", DeltaType.MODIFIED,
                    {"component_class": "GlobeValve"}, date(2026, 1, 1), datetime(2026, 1, 1))
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_current())


class TestCurrentTruth(unittest.TestCase):
    def setUp(self):
        self.rows = []
        apply_delta(self.rows, "segment", "SG-1", DeltaType.NEW,
                    {"diameter": "6in"}, date(2026, 1, 1), datetime(2026, 1, 1, 8))
        apply_delta(self.rows, "segment", "SG-1", DeltaType.MODIFIED,
                    {"diameter": "8in"}, date(2026, 3, 1), datetime(2026, 3, 1, 9))

    def test_default_view_is_latest_valid_and_now_transaction(self):
        current = current_truth(self.rows)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].attrs["diameter"], "8in")

    def test_as_of_valid_before_the_revision_returns_the_prior_fact(self):
        past = current_truth(self.rows, as_of_valid=date(2026, 2, 1))
        self.assertEqual(len(past), 1)
        self.assertEqual(past[0].attrs["diameter"], "6in")

    def test_as_of_tx_before_ingestion_returns_what_we_believed_then(self):
        past = current_truth(self.rows, as_of_tx=datetime(2026, 2, 1))
        self.assertEqual(len(past), 1)
        self.assertEqual(past[0].attrs["diameter"], "6in")


class TestDiffSnapshots(unittest.TestCase):
    def test_full_new_modify_delete_cycle(self):
        rows = diff_snapshots(
            current_rows={"C-1": {"component_class": "GateValve"}, "C-2": {"component_class": "CheckValve"}},
            previous_gold_rows=[],
            object_kind="component",
            valid_from=date(2026, 1, 1),
            tx_from=datetime(2026, 1, 1),
        )
        self.assertEqual({r.anchor_id for r in current_truth(rows)}, {"C-1", "C-2"})

        rows2 = diff_snapshots(
            current_rows={"C-1": {"component_class": "GateValve"}, "C-3": {"component_class": "GlobeValve"}},
            previous_gold_rows=rows,
            object_kind="component",
            valid_from=date(2026, 3, 1),
            tx_from=datetime(2026, 3, 1),
        )
        current_ids = {r.anchor_id for r in current_truth(rows2)}
        self.assertEqual(current_ids, {"C-1", "C-3"})  # C-2 deleted, C-3 added, C-1 unchanged (no-op)
        c1_rows = [r for r in rows2 if r.anchor_id == "C-1"]
        self.assertEqual(len(c1_rows), 1)  # identical content -> no version bump


if __name__ == "__main__":
    unittest.main()
