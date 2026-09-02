"""
offpage.py — cross-sheet OPC pairing for PostProc exports.

Project-B counterpart to the OPC-stitching logic in pidsys/reconstructed.py,
adapted to the one hard-won PostProc discovery:

    GUID pairing (SP_pairedWithID / MatingOPCPath / CrossPageConnection's
    LinkedPersistentID) does NOT reciprocate across sheets — the mate GUID
    points at an element on another drawing, so within a single file it never
    resolves. The durable cross-sheet key is the BUSINESS composite a human
    reads off the OPC balloon:

        (home DrawingNumber, PairedDrawingNumber, OPCTag)

    Two OPCs are mates when, from each side, the other's home drawing equals
    this side's paired drawing AND the OPCTag matches. Stitching the two OPC
    element ids joins the sheets' graphs so a system continues across the sheet.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .model import Doc


def home_drawing(dom: Doc) -> Optional[str]:
    dr = dom.root.find(".//Drawing")
    return dom.ga(dr, "DrawingNumber") if dr is not None else None


def harvest_opcs(dom: Doc) -> List[dict]:
    """One record per placed OPC on this sheet, carrying its business composite
    and element id (for stitching). Home drawing is resolved once per file."""
    home = home_drawing(dom)
    out: List[dict] = []
    for e in dom.root.iter("PipeConnectorSymbol"):
        if dom.cc(e) != "OPC" or dom.in_catalogue(e):
            continue
        out.append({
            "eid": e.get("ID"),
            "home": home,
            "paired": dom.ga(e, "PairedDrawingNumber"),
            "opctag": dom.ga(e, "OPCTag"),
        })
    return out


def pair_key(opc: dict) -> Optional[str]:
    """Mate key = OPCTag.

    Discovered on real project-B data: OPCTag is what reciprocates across
    sheets — sheet 1's OPC to sheet 2 and sheet 2's mate both carry OPCTag
    4782. The PairedDrawingNumber back-reference is NOT reliable in mixed
    exports (the plain .xml sheet had lost it, only the _Post.xml sheet kept
    it), and the GUID fields point at other-sheet ids that never resolve
    within one file. So the durable, reciprocating key is the OPCTag; the
    drawing pair is used only as a cross-check when both ends carry it
    (see match_pairs)."""
    tag = opc.get("opctag")
    return tag if tag else None


def _drawings_consistent(a: dict, b: dict) -> bool:
    """When both OPCs carry a PairedDrawingNumber, require the pairing to be
    reciprocal (a.home==b.paired and b.home==a.paired). When either side is
    missing its back-reference (real in mixed exports), don't block the match
    — the OPCTag identity already establishes the mate."""
    ah, ap = a.get("home"), a.get("paired")
    bh, bp = b.get("home"), b.get("paired")
    if ap and bp:
        return (ah == bp) and (bh == ap)
    if ap:                       # only a has a back-ref; it must point at b's home
        return ap == bh
    if bp:
        return bp == ah
    return True                  # neither has a back-ref — OPCTag alone decides


def _match_by_guid(opcs: List[dict]) -> Tuple[List[Tuple[str, str]], set]:
    """DEXPI pairing: an OPC's guid_mate equals its mate's guid_self (element
    id is 'SP'+GUID; SP_pairedWithID is the bare GUID). Returns (edges, matched)."""
    present = {o.get("guid_self"): o["eid"] for o in opcs if o.get("guid_self")}
    edges: List[Tuple[str, str]] = []
    matched: set = set()
    for o in opcs:
        mate = o.get("guid_mate")
        if not mate:
            continue
        mate_eid = present.get(mate) or present.get(
            mate[2:] if mate.startswith("SP") else "SP" + mate)
        if mate_eid and o["eid"] not in matched and mate_eid not in matched:
            edges.append((o["eid"], mate_eid))
            matched.add(o["eid"])
            matched.add(mate_eid)
    return edges, matched


def match_pairs(opcs: List[dict]) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Given OPC records harvested across the loaded set (either format), return
    (mate_edges, unmatched):
        mate_edges — [(eid_a, eid_b), ...] element-id pairs to stitch
        unmatched  — [eid, ...] OPCs whose mate is not in the loaded set
                     (open boundary — the system continues off-set, not an error)

    Two-path matcher so one call serves both source formats:
      * PostProc records carry an OPCTag -> matched by OPCTag, with a reciprocal
        drawing-pair cross-check where both ends carry PairedDrawingNumber.
      * DEXPI records carry no OPCTag but a guid_mate/guid_self -> matched by GUID.
    A key that appears once = mate on an unloaded sheet (open boundary); a key on
    >2 OPCs is ambiguous and left unmatched.
    """
    tag_recs = [o for o in opcs if o.get("opctag")]
    guid_recs = [o for o in opcs if not o.get("opctag")]

    edges: List[Tuple[str, str]] = []
    matched: set = set()

    # --- PostProc: OPCTag ---
    buckets: Dict[str, List[dict]] = {}
    for o in tag_recs:
        buckets.setdefault(o["opctag"], []).append(o)
    for tag, group in buckets.items():
        if len(group) == 2 and _drawings_consistent(group[0], group[1]):
            a, b = group[0]["eid"], group[1]["eid"]
            edges.append((a, b))
            matched.add(a)
            matched.add(b)

    # --- DEXPI: GUID ---
    g_edges, g_matched = _match_by_guid(guid_recs)
    edges.extend(g_edges)
    matched |= g_matched

    unmatched = [o["eid"] for o in opcs if o["eid"] not in matched]
    return edges, unmatched
