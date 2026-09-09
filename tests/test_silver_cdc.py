import unittest
from datetime import date, datetime

from gold.silver_cdc import (
    CdcAnomaly,
    SilverCdcEvent,
    aggregate_line_attrs,
    apply_silver_cdc_events,
    apply_silver_cdc_events_tolerant,
    resolve_line_seg_tag,
)
from gold.temporal import DeltaType, current_truth


def ev(**kwargs):
    defaults = dict(
        object_kind="line",
        anchor_id="LINE|DWG-1|2\"-WBF-1",
        delta_type=DeltaType.NEW,
        drawing_number="DWG-1",
        drawing_revision_date=date(2026, 1, 1),
        bronze_ingested_at=datetime(2026, 1, 1, 8, 0),
        attrs={"fluid": ("PG",)},
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

    def test_rejects_the_retired_segment_object_kind(self):
        # Line grain (2026-09-05 Silver rename, confirmed on real data
        # 2026-09-06) retired "segment" from this module's OBJECT_KINDS in
        # favour of "line" -- a caller still passing the old grain name
        # should fail loudly, not silently land in the wrong bucket.
        with self.assertRaises(ValueError):
            ev(object_kind="segment")

    def test_accepts_string_delta_type(self):
        e = ev(delta_type="Modified")
        self.assertEqual(e.delta_type, DeltaType.MODIFIED)


class TestApplySilverCdcEvents(unittest.TestCase):
    def test_new_event_opens_a_current_row_keyed_on_stage_e_anchor(self):
        rows = apply_silver_cdc_events({}, [ev()])
        current = current_truth(rows["line"])
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].anchor_id, "LINE|DWG-1|2\"-WBF-1")
        self.assertEqual(current[0].attrs["fluid"], ("PG",))

    def test_delete_plus_recreate_that_stage_e_already_collapsed_produces_no_gold_churn(self):
        # Stage E's own acceptance test (silver_layer_spec.md §3.5) proves a
        # delete+recreate of an unchanged object yields ZERO silver_cdc
        # deltas — so this scenario never even reaches Gold as an event. The
        # only thing to prove at this layer is that when Stage E does NOT
        # emit an event, Gold's state is simply unchanged.
        rows = apply_silver_cdc_events({}, [ev()])
        rows_after_recreate = apply_silver_cdc_events(rows, [])  # Stage E emitted nothing
        self.assertEqual(
            [r.anchor_id for r in current_truth(rows_after_recreate["line"])],
            [r.anchor_id for r in current_truth(rows["line"])],
        )

    def test_modified_event_ordinary_forward_closes_only_valid_axis(self):
        rows = apply_silver_cdc_events({}, [ev()])
        rows = apply_silver_cdc_events(rows, [ev(
            delta_type=DeltaType.MODIFIED,
            drawing_revision_date=date(2026, 3, 1),
            bronze_ingested_at=datetime(2026, 3, 1, 9, 0),
            attrs={"fluid": ("AG",)},
            content_hash_eng="h(eng)-2",
        )])
        history = rows["line"]
        self.assertEqual(len(history), 2)
        old = next(r for r in history if r.attrs["fluid"] == ("PG",))
        new = next(r for r in history if r.attrs["fluid"] == ("AG",))
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
        history = rows["line"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].valid_to, date(2026, 4, 1))
        self.assertEqual(history[0].tx_to, datetime(2026, 4, 1, 10, 0))
        self.assertEqual(current_truth(history), [])

    def test_events_across_object_kinds_land_in_separate_tables(self):
        rows = apply_silver_cdc_events({}, [
            ev(object_kind="line", anchor_id="LINE|DWG-1|SG-1"),
            ev(object_kind="component", anchor_id="C-1", attrs={"component_class": "GateValve"}),
            ev(object_kind="equipment", anchor_id="EQ-1", attrs={"tag": "362-C0953"}),
            ev(object_kind="connection", anchor_id="CONN-1", attrs={"conn_type": "Process"}),
        ])
        for kind in ("line", "component", "equipment", "connection"):
            self.assertEqual(len(current_truth(rows[kind])), 1)

    def test_events_applied_in_bronze_ingested_at_order_regardless_of_input_order(self):
        later = ev(
            delta_type=DeltaType.MODIFIED,
            drawing_revision_date=date(2026, 3, 1),
            bronze_ingested_at=datetime(2026, 3, 1),
            attrs={"fluid": ("LATER",)},
        )
        earlier_new = ev(bronze_ingested_at=datetime(2026, 1, 1))
        # Pass the Modified event BEFORE the New event; apply_silver_cdc_events
        # must still process New first (it sorts by bronze_ingested_at).
        rows = apply_silver_cdc_events({}, [later, earlier_new])
        current = current_truth(rows["line"])
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].attrs["fluid"], ("LATER",))


class TestApplySilverCdcEventsTolerant(unittest.TestCase):
    def test_two_simultaneous_new_events_sharing_one_anchor_bucket_is_flagged_not_raised(self):
        # Real-data finding (2026-09-04): silver_cdc's anchor is a bucket key
        # for components (silver_layer_spec.md §3.5) -- two same-class
        # siblings on one line can legitimately share one anchor string.
        # Anchor format confirmed on real data (2026-09-06, project 216097C):
        # still "CMP|SEG|..." post line-grain-rename, NOT "CMP|LINE|..." --
        # see TestRealComponentGrainBatch below for the un-modified real
        # sample this confirms.
        rows, anomalies = apply_silver_cdc_events_tolerant({}, [
            ev(object_kind="component", anchor_id="CMP|SEG|DWG-1|3/4\"-WBF-1|GateValve", attrs={"component_class": "GateValve"}),
            ev(object_kind="component", anchor_id="CMP|SEG|DWG-1|3/4\"-WBF-1|GateValve",
               attrs={"component_class": "GateValve", "tag": "GV-002"}),
        ])
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].reason, "anchor_collision")
        # the first event still applied -- one bad event doesn't lose the rest
        self.assertEqual(len(current_truth(rows["component"])), 1)

    def test_unrelated_events_after_an_anomaly_still_apply(self):
        rows, anomalies = apply_silver_cdc_events_tolerant({}, [
            ev(anchor_id="ANCHOR-A", bronze_ingested_at=datetime(2026, 1, 1)),
            ev(anchor_id="ANCHOR-A", bronze_ingested_at=datetime(2026, 1, 1, 0, 0, 1)),  # collides with A
            ev(anchor_id="ANCHOR-B", bronze_ingested_at=datetime(2026, 1, 2)),
        ])
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(
            {r.anchor_id for r in current_truth(rows["line"])},
            {"ANCHOR-A", "ANCHOR-B"},
        )

    def test_retroactive_correction_is_flagged_not_raised(self):
        rows = {}
        rows, first_anomalies = apply_silver_cdc_events_tolerant(rows, [ev(
            drawing_revision_date=date(2026, 6, 1), bronze_ingested_at=datetime(2026, 6, 1),
        )])
        self.assertEqual(first_anomalies, [])
        rows, anomalies = apply_silver_cdc_events_tolerant(rows, [ev(
            delta_type=DeltaType.MODIFIED,
            drawing_revision_date=date(2026, 1, 1),   # precedes the current row's valid_from
            bronze_ingested_at=datetime(2026, 6, 2),
            attrs={"fluid": ("AG",)},
        )])
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].reason, "retroactive_correction")


class TestResolveLineSegTag(unittest.TestCase):
    def test_strips_the_drawing_number_prefix(self):
        # Real pasted sample (2026-09-06): new_uid ==
        # "216097C-A22-PID-0021-0015-001|2\"-WBF-2215101-B242A-H"
        uid = "216097C-A22-PID-0021-0015-001|2\"-WBF-2215101-B242A-H"
        self.assertEqual(
            resolve_line_seg_tag(uid, "216097C-A22-PID-0021-0015-001"),
            "2\"-WBF-2215101-B242A-H",
        )

    def test_seg_tag_containing_a_pipe_character_is_not_mis_split(self):
        uid = "DWG-1|2\"-AB|CD-1"
        self.assertEqual(resolve_line_seg_tag(uid, "DWG-1"), "2\"-AB|CD-1")

    def test_raises_when_uid_does_not_start_with_the_drawing_number(self):
        with self.assertRaises(ValueError):
            resolve_line_seg_tag("OTHER-DWG|SG-1", "DWG-1")


class TestAggregateLineAttrs(unittest.TestCase):
    def test_uniform_pieces_collapse_to_single_value_tuples(self):
        pieces = [
            {"segment_id": "SG-1", "fluid": "PG", "unit": "10", "diameter": "6",
             "piping_materials_class": "A1A", "insul_type": "CS", "insul_purpose": "H",
             "insul_thick": "50"},
            {"segment_id": "SG-2", "fluid": "PG", "unit": "10", "diameter": "6",
             "piping_materials_class": "A1A", "insul_type": "CS", "insul_purpose": "H",
             "insul_thick": "50"},
        ]
        attrs = aggregate_line_attrs(pieces)
        self.assertEqual(attrs["fluid"], ("PG",))
        self.assertEqual(attrs["piece_count"], 2)
        self.assertEqual(attrs["segment_ids"], ("SG-1", "SG-2"))
        self.assertFalse(attrs["line_attr_inconsistent"])

    def test_divergent_pieces_surface_as_a_multi_value_tuple_and_flag_inconsistency(self):
        # silver_layer_spec.md: a within-line disagreement (e.g. two pieces
        # of one line carrying different materials classes) is a spec break
        # line CDC flags (line_attr_inconsistent) rather than averages away.
        pieces = [
            {"segment_id": "SG-1", "fluid": "PG", "piping_materials_class": "A1A"},
            {"segment_id": "SG-2", "fluid": "PG", "piping_materials_class": "B2B"},
        ]
        attrs = aggregate_line_attrs(pieces)
        self.assertEqual(attrs["piping_materials_class"], ("A1A", "B2B"))
        self.assertTrue(attrs["line_attr_inconsistent"])

    def test_missing_and_nan_values_are_excluded_not_counted_as_a_distinct_value(self):
        pieces = [
            {"segment_id": "SG-1", "fluid": "PG", "diameter": float("nan")},
            {"segment_id": "SG-2", "fluid": "", "diameter": "6"},
            {"segment_id": "SG-3", "fluid": None, "diameter": "6"},
        ]
        attrs = aggregate_line_attrs(pieces)
        self.assertEqual(attrs["fluid"], ("PG",))
        self.assertEqual(attrs["diameter"], ("6",))

    def test_raises_on_empty_piece_list(self):
        with self.assertRaises(ValueError):
            aggregate_line_attrs([])

    def test_output_shape_matches_the_real_silver_cdc_aggregate_lines_path(self):
        # cdc_to_gold_events (append_gold_cells.py) builds one attrs dict
        # regardless of which path resolved it -- the real silver.cdc.aggregate_lines
        # (via its `<field>_set` / `inconsistent` list / `neighbour_lines` shape)
        # or this re-derivation. Both must land on the same key set so
        # SilverCdcEvent.attrs is uniform either way.
        pieces = [
            {"segment_id": "SG-1", "fluid": "PG", "piping_materials_class": "A1A"},
            {"segment_id": "SG-2", "fluid": "PG", "piping_materials_class": "B2B"},
        ]
        attrs = aggregate_line_attrs(pieces)
        self.assertIn("inconsistent_fields", attrs)
        self.assertEqual(attrs["inconsistent_fields"], ("piping_materials_class",))
        self.assertIn("neighbour_lines", attrs)
        # Documented gap: routing needs the whole drawing's components/
        # connections, which this narrower per-line signature doesn't take.
        self.assertEqual(attrs["neighbour_lines"], ())
        self.assertIsInstance(attrs["line_attr_inconsistent"], bool)

    def test_output_is_usable_as_a_silver_cdc_event_attrs_payload(self):
        # Integration-shaped: aggregate_line_attrs' output must round-trip
        # through apply_delta's content_key() (sorts (k, v) pairs), which it
        # only can if every value is hashable/sortable -- tuples, bools, ints.
        pieces = [{"segment_id": "SG-1", "fluid": "PG", "diameter": "6"}]
        attrs = aggregate_line_attrs(pieces)
        rows = apply_silver_cdc_events({}, [ev(attrs=attrs)])
        current = current_truth(rows["line"])
        self.assertEqual(current[0].attrs["fluid"], ("PG",))
        self.assertEqual(current[0].attrs["piece_count"], 1)


class TestRealComponentGrainBatch(unittest.TestCase):
    """A real `grain='component'` silver_cdc batch (project 216097C, Rev C ->
    Rev D, pasted 2026-09-06) -- the same POSTPROC drawings the earlier
    grain='line' sample came from. Two things this confirms on real data,
    not just the spec's prose:

    1. Component-grain `old_uid`/`new_uid` ARE plain single-instance ids
       (`PC-0010`, `PC-10014`, ...) -- unlike line grain, the original
       single-row lookup in the notebook's `cdc_to_gold_events` needed no
       change here.
    2. The component anchor format is STILL `CMP|SEG|...` post the
       2026-09-05 line-grain rename -- not `CMP|LINE|...`. Confirms the
       module docstring's claim that this was never going to matter
       functionally (anchor is opaque to Gold either way), but settles the
       open question with real data rather than an assumption.

    The batch also contains a genuine anchor-bucket collision "in the
    wild": anchor `...2"-WBF-2215101-B242A-H|GateValve` carries BOTH a
    Modified event (PC-0010 -> PC-10014, an existing valve's connectivity/
    flow_direction changed) and a New event (-> PC-10015, a second GateValve
    added to the same segment/bucket) in the same transaction. This is
    exactly the multi-sibling-bucket scenario `apply_silver_cdc_events_tolerant`
    was built for (2026-09-04) -- validated here against a second real
    batch, not just the first one that surfaced it.
    """

    REV_D = date(2026, 9, 6)
    TX = datetime(2026, 9, 6, 11, 42, 10, 679573)
    DWG = "216097C-A22-PID-0021-0015-001"

    def _events(self):
        rows = [
            ('CMP|SEG|216097C-A22-PID-0021-0015-001|2"-WBF-2215101-B242A-H|GateValve',
             "Modified", "PC-10014", self.DWG),
            ('CMP|SEG|216097C-A22-PID-0021-0015-001|3/4"-WBF-2215102-D341H-N|PressureTransmitter',
             "New", "PC-10018", self.DWG),
            ('CMP|SEG|216097C-A22-PID-0021-0016-001|2"-SM-2203910-G400S-N|SafetyValveOrFitting',
             "New", "PC-10033", "216097C-A22-PID-0021-0016-001"),
            ('CMP|SEG|216097C-A22-PID-0021-0015-001|3/4"-WBF-2215102-D341H-N|ControlValve',
             "New", "PC-10017", self.DWG),
            ('CMP|SEG|216097C-A22-PID-0021-0015-001|3/4"-WBF-2215106-D341H-N|GlobeValve',
             "New", "PC-10022", self.DWG),
            ('CMP|SEG|216097C-A22-PID-0021-0015-001|2"-WBF-2215101-B242A-H|GateValve',
             "New", "PC-10015", self.DWG),
        ]
        return [
            SilverCdcEvent(
                object_kind="component", anchor_id=anchor,
                delta_type=DeltaType(change_type),
                drawing_number=dwg, drawing_revision_date=self.REV_D,
                bronze_ingested_at=self.TX, attrs={"new_uid": new_uid},
            )
            for anchor, change_type, new_uid, dwg in rows
        ]

    def test_anchor_format_is_still_cmp_seg_not_cmp_line(self):
        # A plain acceptance check: none of these real anchors were rejected
        # by SilverCdcEvent, and OBJECT_KINDS didn't need a "CMP|LINE|" case.
        for event in self._events():
            self.assertTrue(event.anchor_id.startswith("CMP|SEG|"))

    def test_real_multi_sibling_collision_is_flagged_not_raised(self):
        rows, anomalies = apply_silver_cdc_events_tolerant({}, self._events())
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].reason, "anchor_collision")
        self.assertIn("GateValve", anomalies[0].event.anchor_id)
        # the colliding pair's FIRST event (the Modified, sorted first since
        # both share one transaction_ts and Modified is listed first) still
        # applied -- only the second New on that anchor is the anomaly.
        current = current_truth(rows["component"])
        self.assertEqual(len(current), 5)  # 6 events in, 1 collision -> 5 land

    def test_non_colliding_anchors_all_resolve_with_their_new_uid(self):
        rows, anomalies = apply_silver_cdc_events_tolerant({}, self._events())
        by_anchor = {r.anchor_id: r for r in current_truth(rows["component"])}
        self.assertEqual(
            by_anchor['CMP|SEG|216097C-A22-PID-0021-0015-001|3/4"-WBF-2215102-D341H-N|PressureTransmitter'].attrs["new_uid"],
            "PC-10018",
        )
        self.assertEqual(
            by_anchor['CMP|SEG|216097C-A22-PID-0021-0016-001|2"-SM-2203910-G400S-N|SafetyValveOrFitting'].attrs["new_uid"],
            "PC-10033",
        )


if __name__ == "__main__":
    unittest.main()
