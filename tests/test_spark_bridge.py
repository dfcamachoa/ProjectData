"""Unit tests for gold/spark_bridge.py -- the pure, Spark-free bridge between
Silver Stage E's collected rows and Gold's bi-temporal core. Every test here
works with plain dicts/lists (exactly what `Row.asDict(recursive=True)`
returns after `.collect()`), so none of this needs pyspark installed --
confirms the module stays usable by this sandbox's pyspark-less test suite.
"""
import unittest
from datetime import date, datetime

from gold.spark_bridge import (
    anomaly_to_dict,
    build_events,
    dict_to_gold_row,
    gold_row_to_dict,
    lines_for_drawing,
    parse_revision_date,
    resolve_line_attrs_for_event,
    valid_from_by_drawing,
)
from gold.silver_cdc import CdcAnomaly, SilverCdcEvent
from gold.temporal import DeltaType, GoldRow


class TestParseRevisionDate(unittest.TestCase):
    def test_parses_each_project_scoped_format(self):
        self.assertEqual(parse_revision_date("2026-01-15"), date(2026, 1, 15))
        self.assertEqual(parse_revision_date("2026/01/15"), date(2026, 1, 15))
        self.assertEqual(parse_revision_date("15Jan26"), date(2026, 1, 15))
        self.assertEqual(parse_revision_date("15-Jan-26"), date(2026, 1, 15))
        self.assertEqual(parse_revision_date("15/01/2026"), date(2026, 1, 15))
        self.assertEqual(parse_revision_date("01/15/2026"), date(2026, 1, 15))

    def test_unrecognized_format_raises_with_actionable_message(self):
        with self.assertRaises(ValueError) as ctx:
            parse_revision_date("not-a-date")
        self.assertIn("spark_bridge.DATE_FORMATS", str(ctx.exception))


class TestValidFromByDrawing(unittest.TestCase):
    def test_takes_the_latest_date_per_drawing(self):
        rows = [
            {"document_number": "DWG-1", "drawing_revision_date": "2026-01-01"},
            {"document_number": "DWG-1", "drawing_revision_date": "2026-03-01"},
            {"document_number": "DWG-2", "drawing_revision_date": "2026-02-01"},
        ]
        out = valid_from_by_drawing(rows)
        self.assertEqual(out, {"DWG-1": date(2026, 3, 1), "DWG-2": date(2026, 2, 1)})

    def test_skips_rows_missing_document_or_date(self):
        rows = [
            {"document_number": None, "drawing_revision_date": "2026-01-01"},
            {"document_number": "DWG-1", "drawing_revision_date": None},
            {"document_number": "DWG-2", "drawing_revision_date": "2026-02-01"},
        ]
        out = valid_from_by_drawing(rows)
        self.assertEqual(out, {"DWG-2": date(2026, 2, 1)})


class TestLinesForDrawing(unittest.TestCase):
    def test_falls_back_to_empty_list_when_silver_cdc_is_not_importable(self):
        # aggregate_lines_fn=None and no `silver` package on the path in this
        # gold_layer.zip checkout -> [] (the documented fallback; the caller,
        # resolve_line_attrs_for_event, then uses aggregate_line_attrs instead).
        out = lines_for_drawing("DWG-1", [], [], [], aggregate_lines_fn=None)
        self.assertIsInstance(out, list)

    def test_uses_injected_aggregate_lines_fn_and_filters_by_drawing(self):
        seen = {}

        def fake_aggregate_lines(segs, comps, conns):
            seen["segs"] = segs
            seen["comps"] = comps
            seen["conns"] = conns
            return [{"seg_tag": "L-1", "fluid_set": ("PG",)}]

        segment_rows = [
            {"segment_id": "S1", "drawing_number": "DWG-1", "seg_tag": "L-1"},
            {"segment_id": "S2", "drawing_number": "DWG-2", "seg_tag": "L-2"},
        ]
        component_rows = [
            {"component_id": "C1", "drawing_number": "DWG-1"},
        ]
        connection_rows = [
            {"drawing_number": "DWG-1"},
            {"drawing_number": "DWG-2"},
        ]
        out = lines_for_drawing("DWG-1", segment_rows, component_rows,
                                 connection_rows, aggregate_lines_fn=fake_aggregate_lines)
        self.assertEqual(out, [{"seg_tag": "L-1", "fluid_set": ("PG",)}])
        # only DWG-1 rows were passed through, and uid was set from the id column
        self.assertEqual(len(seen["segs"]), 1)
        self.assertEqual(seen["segs"][0]["uid"], "S1")
        self.assertEqual(len(seen["comps"]), 1)
        self.assertEqual(seen["comps"][0]["uid"], "C1")
        self.assertEqual(len(seen["conns"]), 1)


class TestResolveLineAttrsForEvent(unittest.TestCase):
    def test_prefers_the_real_aggregate_lines_result_when_present(self):
        lines = [{"seg_tag": "L-1", "fluid_set": ("PG",), "unit_set": ("BAR",),
                  "diameter_set": ('2"',), "piping_materials_class_set": ("WBF",),
                  "insul_type_set": (), "insul_purpose_set": (), "insul_thick_set": (),
                  "inconsistent": [], "neighbour_lines": ["L-2"], "piece_uids": ["S1", "S2"]}]
        attrs = resolve_line_attrs_for_event("L-1", "DWG-1", lines, [])
        self.assertEqual(attrs["fluid"], ("PG",))
        self.assertEqual(attrs["neighbour_lines"], ("L-2",))
        self.assertEqual(attrs["piece_count"], 2)
        self.assertFalse(attrs["line_attr_inconsistent"])
        self.assertEqual(attrs["pieces"], [])  # no segment_rows passed -> honestly empty, not an error

    def test_pieces_attached_from_segment_rows_even_in_the_real_aggregate_branch(self):
        # The aggregated fields come from `lines` (silver.cdc.aggregate_lines'
        # own reduced output, which carries no per-piece dicts); `pieces`
        # must still be resolved from `segment_rows` separately in this
        # branch, not left empty just because a real `lines` match existed.
        lines = [{"seg_tag": "L-1", "fluid_set": ("PG",), "unit_set": ("BAR",),
                  "diameter_set": ('2"',), "piping_materials_class_set": ("WBF",),
                  "insul_type_set": (), "insul_purpose_set": (), "insul_thick_set": (),
                  "inconsistent": [], "neighbour_lines": [], "piece_uids": ["S1", "S2"]}]
        segment_rows = [
            {"drawing_number": "DWG-1", "seg_tag": "L-1", "segment_id": "S1", "fluid": "PG", "diameter": '2"'},
            {"drawing_number": "DWG-1", "seg_tag": "L-1", "segment_id": "S2", "fluid": "PG", "diameter": '2"'},
            {"drawing_number": "DWG-1", "seg_tag": "L-2", "segment_id": "S3", "fluid": "N"},  # a different line
        ]
        attrs = resolve_line_attrs_for_event("L-1", "DWG-1", lines, segment_rows)
        self.assertEqual({p["segment_id"] for p in attrs["pieces"]}, {"S1", "S2"})
        self.assertEqual(attrs["pieces"][0]["fluid"], "PG")

    def test_falls_back_to_aggregate_line_attrs_when_no_lines_match(self):
        segment_rows = [
            {"drawing_number": "DWG-1", "seg_tag": "L-1", "segment_id": "S1", "fluid": "PG"},
            {"drawing_number": "DWG-1", "seg_tag": "L-1", "segment_id": "S2", "fluid": "PG"},
        ]
        attrs = resolve_line_attrs_for_event("L-1", "DWG-1", [], segment_rows)
        self.assertIsNotNone(attrs)
        self.assertEqual(attrs["fluid"], ("PG",))
        self.assertEqual(attrs["piece_count"], 2)
        self.assertEqual({p["segment_id"] for p in attrs["pieces"]}, {"S1", "S2"})

    def test_returns_none_when_neither_source_has_a_match(self):
        attrs = resolve_line_attrs_for_event("L-missing", "DWG-1", [], [])
        self.assertIsNone(attrs)


class TestBuildEvents(unittest.TestCase):
    def _base_cdc_row(self, **kwargs):
        row = {
            "cdc_id": "cdc-1",
            "grain": "component",
            "drawing_number": "DWG-1",
            "anchor": "CMP|SEG|DWG-1|2\"-WBF-1|GateValve",
            "change_type": "New",
            "new_uid": "C1",
            "old_uid": None,
            "transaction_ts": datetime(2026, 1, 1, 8, 0),
            "new_content_hash_eng": "h(eng)",
            "old_content_hash_eng": None,
            "new_content_hash_audit": "h(audit)",
            "old_content_hash_audit": None,
        }
        row.update(kwargs)
        return row

    def test_skips_rows_with_no_bronze_valid_from(self):
        rows = [self._base_cdc_row(drawing_number="DWG-UNKNOWN")]
        events, skipped = build_events(rows, {}, {}, {}, [])
        self.assertEqual(events, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("no Bronze drawing_revision_date", skipped[0]["reason"])

    def test_deleted_event_gets_empty_attrs_without_needing_a_silver_source(self):
        rows = [self._base_cdc_row(change_type="Deleted", new_uid=None, old_uid="C1")]
        valid_from = {"DWG-1": date(2026, 1, 1)}
        events, skipped = build_events(rows, valid_from, {}, {}, [])
        self.assertEqual(skipped, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].delta_type, DeltaType.DELETED)
        self.assertEqual(events[0].attrs, {})

    def test_component_event_resolves_attrs_from_attrs_by_grain_and_skips_hash_columns(self):
        rows = [self._base_cdc_row()]
        valid_from = {"DWG-1": date(2026, 1, 1)}
        attrs_by_grain = {"component": {"C1": {
            "component_id": "C1", "drawing_number": "DWG-1", "component_class": "GateValve",
            "content_hash": "irrelevant", "bronze_id": "b1", "segment_id": "S1",
            "project_code": "P1", "source_format": "DEXPI", "size": "2in",
        }}}
        events, skipped = build_events(rows, valid_from, attrs_by_grain, {}, [])
        self.assertEqual(skipped, [])
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e.object_kind, "component")
        self.assertEqual(e.anchor_id, rows[0]["anchor"])
        self.assertEqual(e.attrs, {"component_class": "GateValve", "size": "2in"})
        self.assertEqual(e.content_hash_eng, "h(eng)")
        self.assertEqual(e.content_hash_audit, "h(audit)")

    def test_component_event_skipped_when_uid_not_found_in_silver_table(self):
        rows = [self._base_cdc_row()]
        valid_from = {"DWG-1": date(2026, 1, 1)}
        events, skipped = build_events(rows, valid_from, {"component": {}}, {}, [])
        self.assertEqual(events, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("not found in", skipped[0]["reason"])

    def test_line_event_resolves_attrs_via_seg_tag_and_lines_by_drawing(self):
        rows = [self._base_cdc_row(
            grain="line", anchor="LINE|DWG-1|2\"-WBF-1", new_uid="DWG-1|2\"-WBF-1")]
        valid_from = {"DWG-1": date(2026, 1, 1)}
        lines_by_drawing = {"DWG-1": [{
            "seg_tag": "2\"-WBF-1", "fluid_set": ("PG",), "unit_set": (), "diameter_set": (),
            "piping_materials_class_set": (), "insul_type_set": (), "insul_purpose_set": (),
            "insul_thick_set": (), "inconsistent": [], "neighbour_lines": [], "piece_uids": ["S1"],
        }]}
        events, skipped = build_events(rows, valid_from, {}, lines_by_drawing, [])
        self.assertEqual(skipped, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].object_kind, "line")
        self.assertEqual(events[0].attrs["fluid"], ("PG",))

    def test_line_event_skipped_when_no_match_anywhere(self):
        rows = [self._base_cdc_row(
            grain="line", anchor="LINE|DWG-1|2\"-WBF-1", new_uid="DWG-1|2\"-WBF-1")]
        valid_from = {"DWG-1": date(2026, 1, 1)}
        events, skipped = build_events(rows, valid_from, {}, {}, [])
        self.assertEqual(events, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("no silver_segments pieces", skipped[0]["reason"])


class TestGoldRowDictRoundTrip(unittest.TestCase):
    def test_gold_row_to_dict_and_back(self):
        row = GoldRow(
            object_kind="component", anchor_id="CMP|A", attrs={"x": 1},
            valid_from=date(2026, 1, 1), valid_to=None,
            tx_from=datetime(2026, 1, 1, 8, 0), tx_to=None,
            superseded_by_delta=None,
        )
        d = gold_row_to_dict(row, "DWG-1", '{"x": 1}')
        self.assertEqual(d["object_kind"], "component")
        self.assertEqual(d["drawing_number"], "DWG-1")
        self.assertTrue(d["current"])
        self.assertIsNone(d["superseded_by_delta"])

        rebuilt = dict_to_gold_row(d, {"x": 1})
        self.assertEqual(rebuilt.object_kind, row.object_kind)
        self.assertEqual(rebuilt.anchor_id, row.anchor_id)
        self.assertEqual(rebuilt.attrs, row.attrs)
        self.assertEqual(rebuilt.valid_from, row.valid_from)
        self.assertEqual(rebuilt.tx_from, row.tx_from)
        self.assertIsNone(rebuilt.superseded_by_delta)
        self.assertTrue(rebuilt.is_current())

    def test_superseded_row_round_trips_its_delta_type(self):
        row = GoldRow(
            object_kind="component", anchor_id="CMP|A", attrs={},
            valid_from=date(2026, 1, 1), valid_to=date(2026, 2, 1),
            tx_from=datetime(2026, 1, 1, 8, 0), tx_to=datetime(2026, 2, 1, 8, 0),
            superseded_by_delta=DeltaType.MODIFIED,
        )
        d = gold_row_to_dict(row, "DWG-1", "{}")
        self.assertEqual(d["superseded_by_delta"], "Modified")
        self.assertFalse(d["current"])
        rebuilt = dict_to_gold_row(d, {})
        self.assertEqual(rebuilt.superseded_by_delta, DeltaType.MODIFIED)
        self.assertFalse(rebuilt.is_current())


class TestAnomalyToDict(unittest.TestCase):
    def test_flattens_the_anomaly_and_its_event(self):
        event = SilverCdcEvent(
            object_kind="component", anchor_id="CMP|A", delta_type=DeltaType.NEW,
            drawing_number="DWG-1", drawing_revision_date=date(2026, 1, 1),
            bronze_ingested_at=datetime(2026, 1, 1, 8, 0), attrs={},
        )
        anomaly = CdcAnomaly(event=event, reason="anchor_collision", detail="NEW on a current anchor")
        run_ts = datetime(2026, 9, 6, 12, 0)
        d = anomaly_to_dict(anomaly, run_ts)
        self.assertEqual(d, {
            "object_kind": "component",
            "anchor_id": "CMP|A",
            "drawing_number": "DWG-1",
            "reason": "anchor_collision",
            "detail": "NEW on a current anchor",
            "transaction_ts": run_ts,
        })


if __name__ == "__main__":
    unittest.main()
