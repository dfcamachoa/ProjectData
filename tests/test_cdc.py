"""Unit tests for Silver Stage E — object-grain CDC (silver_spec §3.5).

The centre of gravity is the §3.5 acceptance test: a delete+recreate of an
unchanged object (new UID, same anchor + same engineering) must produce ZERO
false deltas, while a genuine add / remove / modify produces exactly one. Also
proves neighbour instance-churn immunity (adjacency keyed on ANCHOR, not UID) and
the oracle firewall (turnover change is audit-visible but inert for eng CDC).
"""
from __future__ import annotations

from silver.cdc import (
    anchor_key,
    content_hash_audit,
    content_hash_eng,
    diff,
    enrich_neighbors,
    summarize,
)


def comp(uid, cls="GateValve", seg="D1|2-PG-1", inline=0, und=(), flow=(), **kw):
    d = dict(uid=uid, component_class=cls, segment_anchor=seg, inline_index=inline,
             neighbor_anchors=list(und), flow_neighbor_anchors=list(flow),
             drawing_number="D1", project_code="216097C", source_format="POSTPROC",
             version="v2", src_turnover=None, src_subsystem=None)
    d.update(kw)
    return d


def seg(uid, seg_tag="2-PG-1", diameter='2"', fluid="PG", **kw):
    d = dict(uid=uid, seg_tag=seg_tag, drawing_number="D1", fluid=fluid,
             unit="14", diameter=diameter, piping_materials_class="D341H",
             insul_purpose="H", insul_type=None, insul_thick=None,
             neighbor_anchors=[], flow_neighbor_anchors=[], version="v2",
             project_code="216097C", source_format="POSTPROC",
             src_turnover=None, src_subsystem=None)
    d.update(kw)
    return d


# --- THE acceptance test ---------------------------------------------------

def test_delete_recreate_of_unchanged_valve_yields_zero_deltas():
    # same anchor bucket, same engineering — only the UID was re-minted
    old = [comp("SP-OLD")]
    new = [comp("SP-NEW")]           # different UID, everything else identical
    assert diff(old, new, "component") == []


def test_real_added_valve_is_exactly_one_new():
    old = [comp("SP1", und=())]
    new = [comp("SP2", und=()),                 # the recreated (unchanged) one
           comp("SP3", inline=1, und=("N1",))]  # a genuinely new valve
    d = diff(old, new, "component")
    assert summarize(d) == {"New": 1, "Modified": 0, "Deleted": 0, "total": 1}
    assert d[0]["new_uid"] == "SP3"


def test_real_removed_valve_is_exactly_one_deleted():
    old = [comp("SP1", und=()), comp("SP2", inline=1, und=("N1",))]
    new = [comp("SP3", und=())]                 # only the unchanged one survived
    d = diff(old, new, "component")
    assert summarize(d) == {"New": 0, "Modified": 0, "Deleted": 1, "total": 1}
    assert d[0]["old_uid"] == "SP2"


def test_real_attribute_change_is_one_modified():
    d = diff([seg("SG1", diameter='2"')], [seg("SG2", diameter='3"')], "segment")
    assert summarize(d)["Modified"] == 1 and summarize(d)["total"] == 1
    assert "diameter" in d[0]["detail"]
    assert d[0]["old_uid"] == "SG1" and d[0]["new_uid"] == "SG2"


# --- neighbour instance-churn immunity -------------------------------------

def test_neighbour_delete_recreate_does_not_ripple_a_false_modify():
    # V's adjacency is keyed on the neighbour's ANCHOR ("A"), so even though the
    # neighbour object was itself deleted+recreated, V's signature is unchanged.
    old = [comp("V-OLD", und=("A",))]
    new = [comp("V-NEW", und=("A",))]
    assert diff(old, new, "component") == []
    # but a REAL connectivity change (different neighbour anchor) IS a Modify
    d = diff([comp("V1", und=("A",))], [comp("V2", und=("B",))], "component")
    assert summarize(d)["Modified"] == 1
    assert "connectivity" in d[0]["detail"]


# --- oracle firewall (§5) --------------------------------------------------

def test_oracle_change_is_inert_for_eng_cdc_but_visible_in_audit():
    o = seg("SG1", src_turnover="1000")
    n = seg("SG2", src_turnover="2000")          # only the oracle field changed
    assert diff([o], [n], "segment") == []        # no engineering delta
    assert content_hash_eng(o, "segment") == content_hash_eng(n, "segment")
    assert content_hash_audit(o, "segment") != content_hash_audit(n, "segment")


# --- identity mechanics ----------------------------------------------------

def test_eng_hash_excludes_uid():
    a = comp("X"); b = comp("Y")
    assert content_hash_eng(a, "component") == content_hash_eng(b, "component")


def test_segment_anchor_is_drawing_plus_seg_tag():
    assert anchor_key(seg("SG1", seg_tag="2-PG-1"), "segment") == \
        ("SEG", "D1", "2-PG-1")
    # same seg_tag on a different drawing is a DIFFERENT object (no match)
    d = diff([seg("SG1", seg_tag="2-PG-1", drawing_number="D1")],
             [seg("SG2", seg_tag="2-PG-1", drawing_number="D2")], "segment")
    assert summarize(d) == {"New": 1, "Modified": 0, "Deleted": 1, "total": 2}


def test_enrich_keys_neighbours_on_anchor_so_uid_churn_is_invisible():
    # build one drawing-version: component wired to a segment; enrich sets the
    # component's neighbour anchor to the SEGMENT anchor (drawing|seg_tag)
    def build(cid, sid):
        segs = [seg(sid, seg_tag="2-PG-1")]
        comps = [comp(cid, segment_id=sid)]
        conns = [{"from_id": cid, "to_id": sid, "flow_sense": "forward"}]
        enrich_neighbors(segs, comps, [], conns)
        return comps
    v1 = build("C-OLD", "S-OLD")
    v2 = build("C-NEW", "S-NEW")           # BOTH the component and its segment recreated
    # neighbour anchor is the segment's (unchanged) anchor -> no false modify
    assert v1[0]["neighbor_anchors"] == ["SEG|D1|2-PG-1"]
    assert diff(v1, v2, "component") == []


def test_equipment_matches_on_tag_across_recreate():
    def eq(uid):
        return dict(uid=uid, tag="14-C-0953", equipment_class="Vessel",
                    nozzle_tags=["N1", "N2"], neighbor_anchors=[],
                    flow_neighbor_anchors=[], drawing_number="D1", version="v2")
    assert diff([eq("E-OLD")], [eq("E-NEW")], "equipment") == []
