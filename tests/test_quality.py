"""Unit tests for Silver Stage D — the pure quality evaluator (silver_spec §3.4).

Plain Python — no PySpark. Exercises every expectation kind, the quality_gate
rollup, both hard-fail structural invariants, the anchor-collision, the
reference-data skip behaviour, and the run-level mass-failure warning.
"""
from __future__ import annotations

from silver.quality import QualityRefData, evaluate
from silver.quality_suite import Expectation, default_suite


def _seg(**kw):
    base = dict(
        segment_id=None, fluid="PG", unit="14", diameter='2"',
        piping_materials_class="1B6AS", insul_type="H",
        seg_tag='2"-PG-1415101-B242A-H', subline_tag="PG-1415101", pns_tag="PG-14151",
        src_turnover="1000", src_subsystem="10",
        drawing_number="D1", project_code="215777C", source_format="DEXPI",
    )
    base.update(kw)
    return base


def _conn(**kw):
    base = dict(connection_id=None, from_id="a", to_id="b", derived=True,
                flow_sense="forward", drawing_number="D1", project_code="215777C",
                source_format="DEXPI")
    base.update(kw)
    return base


def _comp(**kw):
    base = dict(component_id=None, component_class="GateValve", tag=None,
                kind="PipingComponent", drawing_number="D1", project_code="215777C",
                source_format="DEXPI")
    base.update(kw)
    return base


def _tables(segments=None, connections=None, components=None, equipment=None):
    return {
        "silver_segments": segments or [],
        "silver_connections": connections or [],
        "silver_components": components or [],
        "silver_equipment": equipment or [],
    }


def _flags(res):
    return {r["flag"] for r in res.ledger}


# --- completeness (not_null) ------------------------------------------------

def test_missing_fluid_is_flagged_and_gates_the_segment():
    segs = [_seg(segment_id="SG1", fluid=None)]
    res = evaluate(_tables(segments=segs))
    assert "segment_missing_fluid" in _flags(res)
    assert res.gates[("silver_segments", "SG1")] == "flagged"


def test_complete_segment_raises_no_completeness_flags():
    segs = [_seg(segment_id="SG1")]
    res = evaluate(_tables(segments=segs))
    for f in ("segment_missing_fluid", "segment_missing_piping_class",
              "segment_missing_diameter"):
        assert f not in _flags(res)


def test_insulation_present_via_purpose_is_not_flagged():
    # the seg_tag's -H suffix IS the insulation purpose; insul_type (the separate
    # material attr) being null must NOT read as insulation-absent
    segs = [_seg(segment_id="SG1", insul_type=None, insul_purpose="H",
                 insul_thick=None)]
    res = evaluate(_tables(segments=segs))
    assert not any(r["flag"] == "segment_insulation_absent" for r in res.ledger)


def test_insulation_absent_only_when_all_three_blank_and_is_info():
    segs = [_seg(segment_id="SG2", insul_type=None, insul_purpose=None,
                 insul_thick=None)]
    res = evaluate(_tables(segments=segs))
    row = next(r for r in res.ledger if r["flag"] == "segment_insulation_absent")
    assert row["severity"] == "info"
    # info still gates as flagged (a punch-list line), but never quarantines
    assert res.gates[("silver_segments", "SG2")] == "flagged"


# --- reference-data-backed checks (in_set / regex) --------------------------

def test_unknown_fluid_flagged_only_when_catalogue_present():
    segs = [_seg(segment_id="SG1", fluid="ZZ")]
    # no catalogue -> skipped
    res = evaluate(_tables(segments=segs), refdata=QualityRefData())
    assert "segment_unknown_fluid" not in _flags(res)
    assert any("segment_unknown_fluid" in s for s in res.skipped)
    # catalogue present, ZZ not in it -> flagged
    rd = QualityRefData(fluid_codes=frozenset({"PG", "ST"}))
    res2 = evaluate(_tables(segments=segs), refdata=rd)
    assert "segment_unknown_fluid" in _flags(res2)


def test_known_fluid_passes_in_set():
    segs = [_seg(segment_id="SG1", fluid="PG")]
    rd = QualityRefData(fluid_codes=frozenset({"PG"}))
    res = evaluate(_tables(segments=segs), refdata=rd)
    assert "segment_unknown_fluid" not in _flags(res)


def test_unknown_insulation_flagged_against_approved_list():
    segs = [_seg(segment_id="SG1", insul_purpose="H"),   # approved
            _seg(segment_id="SG2", insul_purpose="ZZ")]  # not approved
    # no catalogue -> skipped entirely
    res0 = evaluate(_tables(segments=segs), refdata=QualityRefData())
    assert "segment_unknown_insulation" not in _flags(res0)
    assert any("segment_unknown_insulation" in s for s in res0.skipped)
    # with the approved set, only the off-list code flags
    rd = QualityRefData(insulation_codes=frozenset({"H", "N", "W", "P", "ET"}))
    res = evaluate(_tables(segments=segs), refdata=rd)
    bad = [r for r in res.ledger if r["flag"] == "segment_unknown_insulation"]
    assert {r["object_id"] for r in bad} == {"SG2"}


def test_absent_insulation_not_flagged_as_unknown():
    # a blank purpose is the `segment_insulation_absent` review, NOT unknown-value
    segs = [_seg(segment_id="SG1", insul_purpose=None)]
    rd = QualityRefData(insulation_codes=frozenset({"H"}))
    res = evaluate(_tables(segments=segs), refdata=rd)
    assert "segment_unknown_insulation" not in _flags(res)


def test_equipment_naming_regex_fullmatch():
    eq = [dict(equipment_id="E1", tag="362-C0953", drawing_number="D1",
               project_code="215777C", source_format="DEXPI"),
          dict(equipment_id="E2", tag="junk", drawing_number="D1",
               project_code="215777C", source_format="DEXPI")]
    rd = QualityRefData(equipment_pattern=r"\d{3}-[A-Z]\d{4}")
    res = evaluate(_tables(equipment=eq), refdata=rd)
    bad = [r for r in res.ledger if r["flag"] == "equipment_tag_noncompliant"]
    assert {r["object_id"] for r in bad} == {"E2"}


def test_instrument_regex_respects_where_filter():
    comps = [_comp(component_id="I1", kind="ProcessInstrument", tag="BAD"),
             _comp(component_id="V1", kind="PipingComponent", tag="whatever")]
    rd = QualityRefData(instrument_pattern=r"\d{3}[A-Z]{2}\d{6}")
    res = evaluate(_tables(components=comps), refdata=rd)
    hit = [r for r in res.ledger if r["flag"] == "instrument_tag_noncompliant"]
    # only the instrument row is evaluated (where kind contains 'Instrument')
    assert {r["object_id"] for r in hit} == {"I1"}


# --- anchor collision (§3.5) ------------------------------------------------

def test_seg_tag_anchor_collision_flags_once_gates_all():
    # keep prefixes self-consistent so only the anchor-collision check fires
    def coll(sid, tag):
        return _seg(segment_id=sid, seg_tag=tag, subline_tag=tag, pns_tag=tag)
    C, U = '2"-SV-3620901-1B6AS-N', '4"-SV-3620999-1B6AS-N'   # composable line tags
    segs = [coll("SG1", C), coll("SG2", C), coll("SG3", C), coll("SG9", U)]
    res = evaluate(_tables(segments=segs))
    coll = [r for r in res.ledger if r["flag"] == "seg_tag_anchor_collision"]
    assert len(coll) == 1                       # one ledger line per colliding tag
    assert res.gates[("silver_segments", "SG1")] == "flagged"
    assert res.gates[("silver_segments", "SG3")] == "flagged"
    assert ("silver_segments", "SG9") not in res.gates


def test_anchor_collision_is_scoped_by_drawing():
    # the SAME seg_tag on DIFFERENT drawings is the same line drawn on two sheets
    # — NOT a collision, because the CDC anchor is (drawing, seg_tag) (§3.5)
    def s(sid, dwg):
        t = '2"-SV-3620901-1B6AS-N'
        return _seg(segment_id=sid, seg_tag=t, subline_tag=t, pns_tag=t,
                    drawing_number=dwg)
    res = evaluate(_tables(segments=[s("SG1", "D1"), s("SG2", "D2")]))
    assert not any(r["flag"] == "seg_tag_anchor_collision" for r in res.ledger)
    # but two on the SAME drawing still collide
    res2 = evaluate(_tables(segments=[s("SG1", "D1"), s("SG2", "D1")]))
    assert any(r["flag"] == "seg_tag_anchor_collision" for r in res2.ledger)


# --- prefix integrity -------------------------------------------------------

def test_prefix_chain_violation_flagged():
    segs = [_seg(segment_id="SG1", seg_tag='2"-PG-1415101-B242A-H',
                 subline_tag="PG-1415101", pns_tag="PG-14151"),          # nested
            _seg(segment_id="SG2", seg_tag='2"-PG-1415102-B242A-H',
                 subline_tag="XY-9999999", pns_tag="XY-99999")]          # broken
    res = evaluate(_tables(segments=segs))
    bad = [r for r in res.ledger if r["flag"] == "prefix_integrity"]
    assert {r["object_id"] for r in bad} == {"SG2"}


def test_prefix_chain_tolerates_decorated_segment_tag():
    # real Project B: the composed seg tag has a diameter prefix + class/insul
    # suffix, so the subline is EMBEDDED, not a literal prefix. Must NOT flag.
    segs = [_seg(segment_id="SG1", seg_tag='36"-PG-1417205-D24P1HD-H',
                 subline_tag="PG-1417205", pns_tag="PG-14172")]
    res = evaluate(_tables(segments=segs))
    assert not any(r["flag"] == "prefix_integrity" for r in res.ledger)


def test_prefix_chain_still_flags_a_real_identity_mismatch():
    # seg tag's fluid/line identity disagrees with its subline/pns -> a real defect
    segs = [_seg(segment_id="SG1", seg_tag='2"-SC-2203801-F242S-W',
                 subline_tag="PG-9999999", pns_tag="PG-99999")]
    res = evaluate(_tables(segments=segs))
    assert any(r["flag"] == "prefix_integrity" for r in res.ledger)


# --- un-composable seg_tag (§3.5) -------------------------------------------

def test_uncomposable_predicate_matches_the_real_pseudo_tags():
    from silver.quality import is_uncomposable_seg_tag
    for t in ["Conn to process/supply-", "PG-Utility, Secondary-", "Pneumatic-",
              "", None, "OPC"]:
        assert is_uncomposable_seg_tag(t) is True, t
    for t in ['36"-PG-1417205-D24P1HD-H', '2"-SC-2203801-F242S-W',
              "SM-1404001-F242S-H", "PG-1415101"]:
        assert is_uncomposable_seg_tag(t) is False, t


def test_uncomposable_seg_tag_is_quarantined_from_cdc():
    segs = [_seg(segment_id="SG1", seg_tag="Conn to process/supply-",
                 subline_tag=None, pns_tag=None)]
    res = evaluate(_tables(segments=segs))
    assert "seg_tag_uncomposable" in _flags(res)
    assert res.gates[("silver_segments", "SG1")] == "quarantined"


def test_real_line_tag_is_not_uncomposable():
    res = evaluate(_tables(segments=[_seg(segment_id="SG1")]))
    assert "seg_tag_uncomposable" not in _flags(res)


def test_uncomposable_tag_is_not_double_reported_as_anchor_collision():
    # several segments sharing a connector pseudo-tag -> uncomposable, NOT collision
    segs = [_seg(segment_id=f"SG{i}", seg_tag="Conn to process/supply-",
                 subline_tag=None, pns_tag=None) for i in range(4)]
    res = evaluate(_tables(segments=segs))
    assert "seg_tag_uncomposable" in _flags(res)
    assert "seg_tag_anchor_collision" not in _flags(res)


# --- within-line attribute consistency (§3.5) -------------------------------

def _line(sid, tag, **kw):
    # a self-consistent piece of one line; override an attr to break consistency
    return _seg(segment_id=sid, seg_tag=tag, subline_tag=tag, pns_tag=tag, **kw)


def test_consistent_line_pieces_raise_no_inconsistency():
    tag = '2"-PG-1415101-B242A-H'
    segs = [_line("SG1", tag), _line("SG2", tag), _line("SG3", tag)]
    res = evaluate(_tables(segments=segs))
    assert "line_attr_inconsistent" not in _flags(res)


def test_line_pieces_disagreeing_on_class_is_flagged_and_gated():
    tag = '2"-PG-1415101-B242A-H'
    segs = [_line("SG1", tag, piping_materials_class="1B6AS"),
            _line("SG2", tag, piping_materials_class="B2B"),   # spec break
            _line("SG3", tag, piping_materials_class="1B6AS")]
    res = evaluate(_tables(segments=segs))
    bad = [r for r in res.ledger if r["flag"] == "line_attr_inconsistent"]
    assert len(bad) == 1                                # one row per inconsistent line
    assert "piping_materials_class" in bad[0]["detail"]
    for sid in ("SG1", "SG2", "SG3"):                   # every piece quarantined
        assert res.gates[("silver_segments", sid)] == "quarantined"


def test_line_inconsistency_scoped_by_drawing():
    # the same tag on two drawings is two different lines — no inconsistency
    tag = '2"-PG-1415101-B242A-H'
    segs = [_line("SG1", tag, drawing_number="D1", diameter='2"'),
            _line("SG2", tag, drawing_number="D2", diameter='4"')]
    res = evaluate(_tables(segments=segs))
    assert "line_attr_inconsistent" not in _flags(res)


def test_uncomposable_line_not_checked_for_inconsistency():
    # pseudo-tags are quarantined by seg_tag_uncomposable, not double-reported here
    segs = [_seg(segment_id="SG1", seg_tag="Pneumatic-", subline_tag=None,
                 pns_tag=None, diameter='2"'),
            _seg(segment_id="SG2", seg_tag="Pneumatic-", subline_tag=None,
                 pns_tag=None, diameter='4"')]
    res = evaluate(_tables(segments=segs))
    assert "line_attr_inconsistent" not in _flags(res)


# --- orphan node on the reconstructed graph ---------------------------------

def test_orphan_component_when_absent_from_connections():
    comps = [_comp(component_id="C1"), _comp(component_id="C2")]
    conns = [_conn(connection_id="X", from_id="C1", to_id="other")]
    res = evaluate(_tables(components=comps, connections=conns))
    orphans = [r for r in res.ledger if r["flag"] == "orphan_component"]
    assert {r["object_id"] for r in orphans} == {"C2"}


# --- STRUCTURAL INVARIANTS (hard fail) --------------------------------------

def test_unflagged_derived_edge_is_a_hard_failure():
    conns = [_conn(connection_id="OK", derived=True),
             _conn(connection_id="BAD", derived=None)]
    res = evaluate(_tables(connections=conns))
    assert res.hard_failures
    assert {h["object_id"] for h in res.hard_failures} == {"BAD"}
    assert all(h["severity"] == "error" and h["gate"] == "fail"
               for h in res.hard_failures)


def test_oracle_leak_into_a_compute_table_is_a_hard_failure():
    # a component row carrying a populated oracle column -> leak
    comps = [_comp(component_id="C1", src_turnover="1000")]
    res = evaluate(_tables(components=comps))
    leaks = [h for h in res.hard_failures if h["flag"] == "oracle_leak"]
    assert leaks and leaks[0]["object_kind"] == "component"


def test_oracle_on_segments_is_fine():
    segs = [_seg(segment_id="SG1")]              # oracle lives here legitimately
    res = evaluate(_tables(segments=segs))
    assert not any(h["flag"] == "oracle_leak" for h in res.hard_failures)


# --- run-level mass-failure warning -----------------------------------------

def test_mass_failure_raises_run_warning_but_still_produces_result():
    segs = [_seg(segment_id=f"SG{i}", fluid=None,
                 seg_tag=f'2"-PG-1415{i:03d}-B242A-H',
                 subline_tag=f"PG-1415{i:03d}", pns_tag=f"PG-1415{i:03d}")
            for i in range(30)]
    res = evaluate(_tables(segments=segs))
    assert res.warnings
    assert any("segment_missing_fluid" in w for w in res.warnings)
    # partial result still produced (all 30 flagged)
    assert res.summary["ledger_rows"] >= 30


def test_info_severity_mass_absence_does_not_warn():
    # insulation-absent is info ("review, not a defect") — common on real plants,
    # so a high rate must NOT raise a config-smell warning (only warn/error do)
    segs = [_seg(segment_id=f"SG{i}", insul_type=None) for i in range(30)]
    res = evaluate(_tables(segments=segs))
    assert not any("insulation" in w for w in res.warnings)


# --- gate rollup: quarantine outranks flag ----------------------------------

def test_gate_rollup_takes_the_max_severity():
    # craft a custom suite: one flag + one quarantine on the same object
    suite = (
        Expectation(id="f", table="silver_segments", kind="not_null",
                    column="fluid", gate="flag", severity="warn",
                    detail="x", source="t"),
        Expectation(id="q", table="silver_segments", kind="not_null",
                    column="diameter", gate="quarantine", severity="warn",
                    detail="x", source="t"),
    )
    segs = [_seg(segment_id="SG1", fluid=None, diameter=None)]
    res = evaluate(_tables(segments=segs), suite=suite)
    assert res.gates[("silver_segments", "SG1")] == "quarantined"


# --- default suite smoke ----------------------------------------------------

def test_default_suite_runs_clean_on_healthy_data():
    segs = [_seg(segment_id="SG1")]              # default is a composable, nested tag
    comps = [_comp(component_id="C1")]
    conns = [_conn(connection_id="X", from_id="C1", to_id="C1")]
    res = evaluate(_tables(segments=segs, components=comps, connections=conns))
    assert not res.hard_failures
    # C1 participates in a connection -> not orphan; nothing flagged
    assert res.gates == {}
