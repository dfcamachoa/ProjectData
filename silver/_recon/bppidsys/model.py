"""
model.py — low-level XML access for INGR ISO-15926 PostProc exports
(OriginatingSystem="SPPID").

Project-B counterpart to pidtool/model.py. It owns the DOM: parsing, the
parent map, the id index, catalogue filtering, and the small accessor
helpers. Nothing here builds graphs — it only exposes the file's raw
content in the shape the pipeline expects, IDENTICAL to pidtool.Doc, so
the unchanged pidsys core can consume it as a drop-in.

Differences from the DEXPI/Proteus reader, each confirmed against a real
PostProc file (216097C-A14-...):

  1. Segments carry BOTH TagName (line class, e.g. "100-FA-Utility,
     Secondary") and ItemTag (line number, e.g. "FA-3510001").
     Format is detected by the presence of segment TagName attributes;
     tag() prefers ItemTag (the line number) for a segment.
  2. GenericAttribute name/value pairs live under an element's own
     <GenericAttributes> child. A segment and its inline components each
     have their own block, and .iter() would bleed a child's attributes
     into the parent — so ga() reads ONLY direct-child GenericAttributes.
  3. No Z_TurnOverSystemNumber / SubsystemNo on segments (PostProc carries
     no turnover ground truth) — accessors simply return None; validation
     is cohesion/structural, not source-agreement.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

XY = Tuple[float, float]

# Component element tags that carry a ComponentClass / can be graph nodes.
# Mirrors pidtool.COMPONENT_TAGS; extra tags are harmless if absent.
COMPONENT_TAGS = {
    "PipingComponent",
    "ProcessInstrument",
    "InstrumentComponent",
    "PipeConnectorSymbol",
    "Nozzle",
    "PropertyBreak",
    "Equipment",
}
SEGMENT_TAG = "PipingNetworkSegment"


@dataclass
class Doc:
    """A parsed PostProc document with the indexes the pipeline needs."""

    root: ET.Element
    parent: Dict[ET.Element, ET.Element] = field(default_factory=dict)
    by_id: Dict[str, ET.Element] = field(default_factory=dict)

    # ---- construction ----
    @classmethod
    def from_bytes(cls, data: bytes) -> "Doc":
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            root = cls._recover(data)
        doc = cls(root=root)
        doc.parent = {c: p for p in root.iter() for c in p}
        doc.by_id = {e.get("ID"): e for e in root.iter() if e.get("ID")}
        return doc

    @staticmethod
    def _recover(data: bytes) -> ET.Element:
        try:
            import lxml.etree as LET
            parser = LET.XMLParser(recover=True, huge_tree=True)
            ltree = LET.fromstring(data, parser=parser)
            return ET.fromstring(LET.tostring(ltree))
        except Exception:
            import re
            text = data.decode("utf-8", errors="replace")
            text = re.sub(r"&#x?0*[0-8bcefBCEF];", "", text)
            return ET.fromstring(text)

    @classmethod
    def from_path(cls, path: str) -> "Doc":
        with open(path, "rb") as fh:
            return cls.from_bytes(fh.read())

    # ---- format detection ----
    @classmethod
    def is_postproc(cls, root: ET.Element) -> bool:
        """PostProc iff any PipingNetworkSegment carries a TagName attribute.
        (Keys ONLY on TagName presence — requiring ItemTag absent would break
        on files that carry both, which real PostProc files do.)"""
        for seg in root.iter(SEGMENT_TAG):
            if seg.get("TagName") is not None:
                return True
        return False

    # ---- catalogue filtering ----
    def in_catalogue(self, e: ET.Element) -> bool:
        """True if e is a symbol definition under a ShapeCatalogue (not a
        placed instance). PostProc files may not carry a ShapeCatalogue at
        all; then nothing is filtered here."""
        x = e
        while x in self.parent:
            x = self.parent[x]
            if x.tag == "ShapeCatalogue":
                return True
        return False

    def owner_segment(self, e: ET.Element) -> Optional[ET.Element]:
        p = self.parent.get(e)
        return p if p is not None and p.tag == SEGMENT_TAG else None

    # ---- accessors ----
    @staticmethod
    def cc(e: Optional[ET.Element]) -> Optional[str]:
        return e.get("ComponentClass") if e is not None else None

    @staticmethod
    def ga(e: Optional[ET.Element], name: str) -> Optional[str]:
        """First populated GenericAttribute value with this Name, read ONLY
        from the element's own direct <GenericAttributes> child(ren) — never
        from a nested component's block (which .iter() would wrongly reach)."""
        if e is None:
            return None
        for gas in e:
            if gas.tag != "GenericAttributes":
                continue
            for g in gas:
                if g.tag == "GenericAttribute" and g.get("Name") == name:
                    v = g.get("Value")
                    if v not in (None, ""):
                        return v
        return None

    def tag(self, e: Optional[ET.Element]) -> Optional[str]:
        """Business tag. For a segment, prefer ItemTag (the specific line
        number) over TagName (the line class); for everything else, ItemTag."""
        if e is None:
            return None
        it = self.ga(e, "ItemTag")
        if it:
            return it
        if e.tag == SEGMENT_TAG:
            return e.get("TagName")   # fall back to the line class
        return None

    # ---- geometry ----
    def location(self, e: Optional[ET.Element]) -> Optional[XY]:
        """(x, y) of an element from its Position/Location, if present."""
        if e is None:
            return None
        pos = e.find("Position")
        if pos is not None:
            loc = pos.find("Location")
            if loc is not None and loc.get("X") is not None:
                try:
                    return (float(loc.get("X")), float(loc.get("Y")))
                except (TypeError, ValueError):
                    pass
        return None

    def extent(self, e: Optional[ET.Element]) -> Optional[Tuple[float, float, float, float]]:
        if e is None:
            return None
        ext = e.find("Extent")
        if ext is None:
            return None
        mn, mx = ext.find("Min"), ext.find("Max")
        if mn is None or mx is None:
            return None
        try:
            return (float(mn.get("X")), float(mn.get("Y")),
                    float(mx.get("X")), float(mx.get("Y")))
        except (TypeError, ValueError):
            return None

    def centerline(self, seg: Optional[ET.Element]) -> List[XY]:
        """Ordered centerline coordinates of a segment."""
        if seg is None:
            return []
        cl = seg.find("CenterLine")
        if cl is None:
            return []
        pts: List[XY] = []
        for co in cl.findall("Coordinate"):
            try:
                pts.append((float(co.get("X")), float(co.get("Y"))))
            except (TypeError, ValueError):
                continue
        return pts

    def nodes(self, e: Optional[ET.Element]) -> List[dict]:
        """Connection-point nodes of a component, with any per-node location."""
        if e is None:
            return []
        cp = e.find("ConnectionPoints")
        if cp is None:
            return []
        out: List[dict] = []
        for n in cp.findall("Node"):
            xy = None
            pos = n.find("Position")
            if pos is not None:
                loc = pos.find("Location")
                if loc is not None and loc.get("X") is not None:
                    try:
                        xy = (float(loc.get("X")), float(loc.get("Y")))
                    except (TypeError, ValueError):
                        xy = None
            out.append({"name": n.get("Name"), "xy": xy})
        return out


def arc_position(centerline: List[XY], x: float, y: float) -> Optional[float]:
    """Arc-length position of the point on `centerline` nearest to (x, y).
    Used to order inline components along a segment. Identical semantics to
    pidtool.arc_position."""
    if not centerline or len(centerline) < 2:
        return None
    best_d = None
    best_s = 0.0
    acc = 0.0
    for (x0, y0), (x1, y1) in zip(centerline, centerline[1:]):
        dx, dy = x1 - x0, y1 - y0
        seg_len = math.hypot(dx, dy)
        if seg_len == 0:
            continue
        t = ((x - x0) * dx + (y - y0) * dy) / (seg_len * seg_len)
        t = max(0.0, min(1.0, t))
        px, py = x0 + t * dx, y0 + t * dy
        d = math.hypot(x - px, y - py)
        s = acc + t * seg_len
        if best_d is None or d < best_d:
            best_d, best_s = d, s
        acc += seg_len
    return best_s
