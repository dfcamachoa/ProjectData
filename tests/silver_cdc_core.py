"""Re-housed verbatim from the ProjectData repo's `silver/cdc.py` (read via
the project's widened GitHub sync, 2026-09-06) -- NOT re-derived. Stage E's
own docstring, unchanged:

Silver Stage E -- object-grain change data capture (silver_spec Sec3.5), pure core.

The one Silver sub-stage with **no PoC predecessor**. Its whole job is to
separate *engineering change* from *re-export churn*: SmartPlant re-exports
the entire drawing XML for a single symbol move, and -- worse -- **re-mints
the element UID when an object is deleted and recreated** (a routine SPPID
rework action). So the UID cannot be the identity key; it is demoted to an
audit field.

Identity is therefore a **hierarchical anchor-match** that survives
delete+recreate and reordering (Sec3.5):

    equipment  -> its tag
    segment    -> (drawing_number, composed seg business tag)
    component  -> the bucket (owning-segment anchor, component_class), with
                  members paired inside the bucket

and three cleanly separated hashes:

    anchor_hash         matching -- the business/structural anchor. NO UID.
    content_hash_eng    Modify + recompute trigger -- engineering attributes
                        PLUS one-hop neighbour sets keyed on each neighbour's
                        ANCHOR identity (never its UID), undirected and
                        directed hashed separately.
    content_hash_audit  content_hash_eng + the UID + the quarantined oracle
                        fields (src_turnover / src_subsystem).

`diff(old, new, grain)` classifies each object New / Modified / Deleted; an
unchanged object that was merely deleted+recreated (same anchor, same
`content_hash_eng`) produces **zero** deltas -- the acceptance test of Sec3.5.

Spark-free and unit-tested. The Spark job (`cdc_job.py`) assembles the two
versions and the neighbour-anchor sets and calls `diff`. This copy omits
nothing from the pure core except the module-level docstring's cross-file
references; every function body below is unchanged.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Dict, List, Optional

from .silver_quality_stub import is_uncomposable_seg_tag, _empty  # noqa: F401  (shared rule, kept for parity)

# a line's engineering attributes -- reduced across its pieces to distinct-value
# SETS (lossless, piece-count-independent), so a re-split churns nothing but any
# real value change -- or a within-line inconsistency -- is captured (silver_spec Sec3.5)
_LINE_ATTR_FIELDS = ("fluid", "unit", "diameter", "piping_materials_class",
                     "insul_purpose", "insul_type", "insul_thick")


def _norm(v) -> str:
    return "" if v is None else str(v)


# --------------------------------------------------------------------------- #
#  identity + content signatures                                              #
# --------------------------------------------------------------------------- #
def anchor_key(o: dict, grain: str) -> tuple:
    """The delete+recreate-safe identity anchor (silver_spec Sec3.5). NO UID."""
    if grain == "line":
        # a LINE = (drawing, composed seg_tag); unique by construction, so the
        # many PipingNetworkSegment pieces of one line collapse to one object
        return ("LINE", _norm(o.get("drawing_number")), _norm(o.get("seg_tag")))
    if grain == "segment":
        return ("SEG", _norm(o.get("drawing_number")), _norm(o.get("seg_tag")))
    if grain == "equipment":
        return ("EQ", _norm(o.get("tag")))
    if grain == "component":
        # bucket: owning-segment anchor + component class (ItemTag is a line tag,
        # not per-object, so a component has no business tag of its own)
        return ("CMP", _norm(o.get("segment_anchor")), _norm(o.get("component_class")))
    return ("OBJ", _norm(o.get("uid")))


# engineering attributes per grain -- the change-meaningful fields, no UID/oracle
_ENG_FIELDS = {
    "segment": ("fluid", "unit", "diameter", "piping_materials_class",
                "insul_purpose", "insul_type", "insul_thick"),
    "component": ("component_class",),
    "equipment": ("equipment_class",),
}


def eng_signature(o: dict, grain: str) -> dict:
    """The engineering-meaningful projection: typed attrs + one-hop neighbour
    ANCHOR sets (undirected + directed), never UIDs (silver_spec Sec3.5)."""
    if grain == "line":
        # attrs as distinct-value SETS across the pieces (so a re-split is inert,
        # and a within-line inconsistency is present, not averaged away); plus the
        # set of neighbour LINE tags (routing) -- components are their own grain
        return {"attrs": {f: sorted(o.get(f + "_set") or []) for f in _LINE_ATTR_FIELDS},
                "routing": sorted(o.get("neighbour_lines") or [])}
    attrs = [_norm(o.get(f)) for f in _ENG_FIELDS.get(grain, ())]
    if grain == "equipment":
        # nozzle_ids are element UIDs (re-minted on re-export), so hashing them
        # would leak instance churn -- use the nozzle COUNT, a UID-free structural
        # signal (a nozzle added/removed => Modified; a pure re-draw => no delta).
        attrs.append("nozzles=%d" % len(o.get("nozzle_tags") or []))
    und = sorted(_norm(a) for a in (o.get("neighbor_anchors") or []))
    flow = sorted(_norm(a) for a in (o.get("flow_neighbor_anchors") or []))
    return {"attrs": attrs, "und": und, "flow": flow}


def _sha(obj) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def anchor_hash(o: dict, grain: str) -> str:
    return _sha(list(anchor_key(o, grain)))


def content_hash_eng(o: dict, grain: str) -> str:
    """Modify + recompute trigger. Excludes the UID and the oracle (Sec5)."""
    return _sha(eng_signature(o, grain))


def content_hash_audit(o: dict, grain: str) -> str:
    """Audit visibility: adds the UID and the quarantined oracle -- a
    delete+recreate or a turnover reassignment shows here but never in
    `content_hash_eng`."""
    base = eng_signature(o, grain)
    if grain == "line":
        base["pieces"] = sorted(o.get("piece_uids") or [])          # the segment UIDs
        base["oracle"] = [sorted(o.get("src_turnover_set") or []),
                          sorted(o.get("src_subsystem_set") or [])]
    else:
        base["uid"] = _norm(o.get("uid"))
        base["oracle"] = [_norm(o.get("src_turnover")), _norm(o.get("src_subsystem"))]
    return _sha(base)


# --------------------------------------------------------------------------- #
#  the diff                                                                   #
# --------------------------------------------------------------------------- #
def _inline(o: dict):
    v = o.get("inline_index")
    return (v if v is not None else 1_000_000, _norm(o.get("uid")))


def _eng_fields_changed(o: dict, n: dict, grain: str) -> List[str]:
    if grain == "line":
        changed = [f for f in _LINE_ATTR_FIELDS
                   if sorted(o.get(f + "_set") or []) != sorted(n.get(f + "_set") or [])]
        if sorted(o.get("neighbour_lines") or []) != sorted(n.get("neighbour_lines") or []):
            changed.append("routing")
        return changed
    changed = [f for f in _ENG_FIELDS.get(grain, ())
               if _norm(o.get(f)) != _norm(n.get(f))]
    if grain == "equipment" and \
       len(o.get("nozzle_tags") or []) != len(n.get("nozzle_tags") or []):
        changed.append("nozzle_count")
    if sorted(_norm(a) for a in (o.get("neighbor_anchors") or [])) != \
       sorted(_norm(a) for a in (n.get("neighbor_anchors") or [])):
        changed.append("connectivity")
    if sorted(_norm(a) for a in (o.get("flow_neighbor_anchors") or [])) != \
       sorted(_norm(a) for a in (n.get("flow_neighbor_anchors") or [])):
        changed.append("flow_direction")
    return changed


def _delta(change: str, grain: str, anchor: tuple,
           o: Optional[dict], n: Optional[dict]) -> dict:
    ref = n or o
    detail = ""
    if change == "Modified":
        fields = _eng_fields_changed(o, n, grain)
        detail = "changed: " + ", ".join(fields) if fields else "engineering change"
    elif change == "New":
        detail = "added"
    elif change == "Deleted":
        detail = "removed"
    if grain == "line":                        # surface a within-line inconsistency
        inc = ref.get("inconsistent") or [
            f for f in _LINE_ATTR_FIELDS if len(ref.get(f + "_set") or []) > 1]
        if inc:
            detail = (detail + " | " if detail else "") + \
                "INCONSISTENT within line: " + ", ".join(inc)
    return {
        "grain": grain,
        "drawing_number": ref.get("drawing_number"),
        "anchor": "|".join(str(x) for x in anchor),
        "change_type": change,
        "old_uid": o.get("uid") if o else None,
        "new_uid": n.get("uid") if n else None,
        "old_content_hash_eng": content_hash_eng(o, grain) if o else None,
        "new_content_hash_eng": content_hash_eng(n, grain) if n else None,
        "old_content_hash_audit": content_hash_audit(o, grain) if o else None,
        "new_content_hash_audit": content_hash_audit(n, grain) if n else None,
        "old_version": o.get("version") if o else None,
        "new_version": n.get("version") if n else None,
        "old_revision": o.get("revision") if o else None,
        "new_revision": n.get("revision") if n else None,
        "project_code": ref.get("project_code"),
        "source_format": ref.get("source_format"),
        "detail": detail,
    }


def _match_bucket(olds: List[dict], news: List[dict], grain: str, anchor: tuple) -> List[dict]:
    """Pair the N old against N new members inside one anchor bucket (Sec3.5):
      1. absorb members with identical `content_hash_eng` (a recreate of an
         unchanged object -- same anchor + same engineering -- pairs silently);
      2. pair what remains by inline order -> Modified;
      3. the unmatched remainder is the genuine add / remove.
    """
    out: List[dict] = []
    n_by_hash: Dict[str, list] = defaultdict(list)
    for n in news:
        n_by_hash[content_hash_eng(n, grain)].append(n)

    still_old: List[dict] = []
    for o in olds:
        h = content_hash_eng(o, grain)
        if n_by_hash.get(h):
            n_by_hash[h].pop()                 # unchanged pair -- no delta emitted
        else:
            still_old.append(o)
    still_new = [n for lst in n_by_hash.values() for n in lst]

    still_old.sort(key=_inline)
    still_new.sort(key=_inline)
    k = min(len(still_old), len(still_new))
    for i in range(k):
        out.append(_delta("Modified", grain, anchor, still_old[i], still_new[i]))
    for o in still_old[k:]:
        out.append(_delta("Deleted", grain, anchor, o, None))
    for n in still_new[k:]:
        out.append(_delta("New", grain, anchor, None, n))
    return out


def diff(old_objs: List[dict], new_objs: List[dict], grain: str) -> List[dict]:
    """Object-grain deltas between two versions of one drawing's objects of one
    grain. Emits New / Modified / Deleted (not Unchanged) -- the interval
    open/close events Gold consumes. Delete+recreate of an unchanged object
    yields nothing."""
    o_by: Dict[tuple, list] = defaultdict(list)
    n_by: Dict[tuple, list] = defaultdict(list)
    for o in old_objs:
        o_by[anchor_key(o, grain)].append(o)
    for n in new_objs:
        n_by[anchor_key(n, grain)].append(n)

    deltas: List[dict] = []
    for a in set(o_by) | set(n_by):
        deltas.extend(_match_bucket(o_by.get(a, []), n_by.get(a, []), grain, a))
    return deltas


def enrich_neighbors(segments: List[dict], components: List[dict],
                     equipment: List[dict], connections: List[dict]) -> None:
    """Set `segment_anchor` on components and `neighbor_anchors` /
    `flow_neighbor_anchors` on every object of ONE drawing-version, keyed on
    each neighbour's ANCHOR (never its UID) -- the property that makes CDC
    immune to a neighbour's delete+recreate (silver_spec Sec3.5). Mutates the
    dicts in place. `connections` are that version's `silver_connections`
    rows."""
    seg_by_id = {s["uid"]: s for s in segments}
    for c in components:                       # component anchor needs its owner's
        owner = seg_by_id.get(c.get("segment_id"))
        c["segment_anchor"] = ("|".join(map(str, anchor_key(owner, "segment")))
                               if owner else _norm(c.get("segment_id")))

    anchor_of: Dict[str, str] = {}
    for s in segments:
        anchor_of[s["uid"]] = "|".join(map(str, anchor_key(s, "segment")))
    for e in equipment:
        anchor_of[e["uid"]] = "|".join(map(str, anchor_key(e, "equipment")))
    for c in components:
        anchor_of[c["uid"]] = "|".join(map(str, anchor_key(c, "component")))

    und: Dict[str, set] = defaultdict(set)
    flow: Dict[str, set] = defaultdict(set)    # outgoing directed neighbours
    for e in connections:
        a, b, sense = e.get("from_id"), e.get("to_id"), e.get("flow_sense")
        if a is None or b is None:
            continue
        und[a].add(b)
        und[b].add(a)
        if sense in ("forward", "both"):
            flow[a].add(b)
        if sense in ("reverse", "both"):
            flow[b].add(a)

    for o in list(segments) + list(components) + list(equipment):
        uid = o["uid"]
        o["neighbor_anchors"] = sorted(anchor_of[n] for n in und.get(uid, ())
                                       if n in anchor_of)
        o["flow_neighbor_anchors"] = sorted(anchor_of[n] for n in flow.get(uid, ())
                                            if n in anchor_of)


def aggregate_lines(segments: List[dict], components: List[dict],
                    connections: List[dict]) -> List[dict]:
    """Collapse the PipingNetworkSegment pieces of ONE drawing-version into
    LINE objects keyed on `(drawing, seg_tag)` (silver_spec Sec3.5). The
    physical piece is a drawing artifact with no stable identity; the LINE is
    what engineers version.

    Un-composable seg_tags (connectors / placeholders) are excluded -- Stage D
    quarantines them separately. Each engineering attribute becomes the
    sorted SET of its distinct non-null values across the pieces (a re-split
    is inert; a real change or a within-line inconsistency is captured).
    ``neighbour_lines`` is the set of OTHER lines this line connects to
    (routing), from the pieces' component connections. ``piece_uids`` and the
    oracle sets are carried for audit only.
    """
    seg_tag_of = {s["uid"]: s.get("seg_tag") for s in segments}
    comp_line = {}
    for c in components:
        st = seg_tag_of.get(c.get("segment_id"))
        if st:
            comp_line[c["uid"]] = st
    adj: Dict[str, set] = defaultdict(set)
    for e in connections:
        la, lb = comp_line.get(e.get("from_id")), comp_line.get(e.get("to_id"))
        if la and lb and la != lb:
            adj[la].add(lb); adj[lb].add(la)
    lines: Dict[tuple, list] = defaultdict(list)
    for s in segments:
        st = s.get("seg_tag")
        if is_uncomposable_seg_tag(st):
            continue
        lines[(s.get("drawing_number"), st)].append(s)
    out: List[dict] = []
    for (dwg, st), pieces in lines.items():
        rep = pieces[0]
        obj = {
            "uid": f"{dwg}|{st}", "drawing_number": dwg, "seg_tag": st,
            "version": rep.get("version"), "revision": rep.get("revision"),
            "project_code": rep.get("project_code"), "source_format": rep.get("source_format"),
            "piece_uids": sorted(p["uid"] for p in pieces if p.get("uid")),
            "neighbour_lines": sorted(a for a in adj.get(st, ()) if a != st),
        }
        for f in _LINE_ATTR_FIELDS:
            obj[f + "_set"] = sorted({_norm(p.get(f)) for p in pieces if not _empty(p.get(f))})
        obj["src_turnover_set"] = sorted({_norm(p.get("src_turnover")) for p in pieces if not _empty(p.get("src_turnover"))})
        obj["src_subsystem_set"] = sorted({_norm(p.get("src_subsystem")) for p in pieces if not _empty(p.get("src_subsystem"))})
        obj["inconsistent"] = [f for f in _LINE_ATTR_FIELDS if len(obj[f + "_set"]) > 1]
        out.append(obj)
    return out
