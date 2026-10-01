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


# --- Workstream 2 OPC feed (Step 1) -------------------------------------
# OPCs carry two descriptive GenericAttributes in BOTH source formats
# (confirmed 2026-09-18 -- these are NOT PostProc-only; DEXPI files carry them
# too, alongside DEXPI's own typed ComponentClass role). NEITHER is a flow role
# for the symbol, and this harvest asserts no flow direction from them:
#
#   OPCType    -- a CLASSIFICATION of the connector. Real values seen:
#                 "Off Drawing Piping Connector", "Off Unit Piping Connector",
#                 "Off Drawing Instrument Connector", "Utility Connector".
#                 The connector's subtype (piping vs instrument vs utility,
#                 on-unit vs off-unit); belongs on the Gold OPC node as such.
#   ToFromText -- a human continuation label naming the MATE, e.g.
#                 "TO FLARE HEADER" / "FROM 11FY0009 C" / "LP ACID GAS FLARE".
#                 A TO/FROM keyword (when present) describes what the OTHER
#                 sheet is; it is a narrative pointer to the mate, NOT the
#                 process flow sense THROUGH this symbol. Many values carry no
#                 keyword at all (e.g. "LP ACID GAS FLARE") -- pure mate names.
#
# Flow role is format-specific and separate from these: DEXPI has a real typed
# role in ComponentClass (FlowIn/FlowOutPipeOffPageConnector); PostProc has
# none, so PostProc's flow_direction is always None. `_parse_to_from` below is
# shared by both harvests (reconstructed.py imports it) and returns the TO/FROM
# keyword ONLY as a label plus the mate text -- it never emits a flow enum.
#
# IMPORTANT: an earlier version mapped TO/FROM onto DEXPI's FlowIn/FlowOut enum.
# That was wrong -- it read a mate-narrative as a flow role and conflated a
# descriptive string with DEXPI's own typed symbol class, which would let a
# reasoner infer a flow sense the data never asserts.
_POSTPROC_TOFROM_ATTR = "ToFromText"
_POSTPROC_OPCTYPE_ATTR = "OPCType"


def _parse_to_from(text: Optional[str]) -> "tuple[Optional[str], Optional[str]]":
    """(to_from_dir, to_from_text) from a PostProc ToFromText value -- BOTH are
    provenance, neither is a flow role.

    to_from_dir is the bare narrative keyword ("TO" | "FROM" | None) describing
    the mate, kept verbatim as a label; to_from_text is the destination/source
    the label names, with the keyword stripped. A label with no TO/FROM keyword
    keeps its whole text as to_from_text (nothing is silently dropped). Returns
    (None, None) for an empty/missing/whitespace label. This function does NOT
    assert flow direction -- see the module note above."""
    if not text:
        return None, None
    raw = str(text).strip()
    if not raw:                   # whitespace-only label
        return None, None
    upper = raw.upper()
    if upper.startswith("TO ") or upper == "TO":
        return "TO", raw[3:].strip() or None
    if upper.startswith("FROM ") or upper == "FROM":
        return "FROM", raw[5:].strip() or None
    return None, raw          # text present but no TO/FROM keyword -- keep verbatim


def harvest_opcs(dom: Doc) -> List[dict]:
    """One record per placed OPC on this sheet, carrying its business composite
    and element id (for stitching). Home drawing is resolved once per file.

    Record shape is kept UNIFORM with the DEXPI harvest
    (reconstructed.py::_harvest_opcs) so the shared match_pairs and the Silver
    OPC-row builder (Step 4) see one shape regardless of source format. The
    Workstream-2 fields:
      * opc_type       -- the OPCType classification string (piping/instrument/
        utility, on/off-unit); the connector's subtype for the Gold node.
      * flow_direction -- None for PostProc (no flow role is asserted; see the
        module note). DEXPI supplies a real FlowIn/FlowOut role instead.
      * to_from_dir    -- the bare "TO"/"FROM" narrative keyword (label only).
      * to_from_text   -- the mate destination/source the label names (e.g.
        "FLARE HEADER", "11FY0009 C"); provenance on the Gold OPC node.
      * class_uri      -- always None: PostProc carries no RDL URIs at all
        (ido_semantic_mapping_spec.md §4.1). DEXPI supplies the sandbox URI.
    None of these affect pairing (match_pairs reads only opctag here)."""
    home = home_drawing(dom)
    out: List[dict] = []
    for e in dom.root.iter("PipeConnectorSymbol"):
        if dom.cc(e) != "OPC" or dom.in_catalogue(e):
            continue
        to_from_dir, to_from_text = _parse_to_from(dom.ga(e, _POSTPROC_TOFROM_ATTR))
        out.append({
            "eid": e.get("ID"),
            "home": home,
            "paired": dom.ga(e, "PairedDrawingNumber"),
            "opctag": dom.ga(e, "OPCTag"),
            "opc_type": dom.ga(e, _POSTPROC_OPCTYPE_ATTR),   # connector subtype
            "flow_direction": None,   # PostProc asserts no flow role (see note)
            "to_from_dir": to_from_dir,     # "TO"/"FROM" narrative keyword, label only
            "to_from_text": to_from_text,   # mate destination/source, provenance
            "class_uri": None,       # PostProc has no RDL URIs (spec §4.1)
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
