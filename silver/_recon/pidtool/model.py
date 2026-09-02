"""Low-level XML access for Proteus/DEXPI adapter exports.

This module owns the DOM: parsing, the parent map, id index, catalogue
filtering, and the small accessor helpers (attributes, geometry, nodes).
Nothing here builds graphs — it only exposes the file's raw content in a
convenient form for the pipeline stages.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

XY = Tuple[float, float]

# Component element tags that carry a ComponentClass / can be graph nodes.
# Includes the element types that can appear as Connection endpoints, so the
# graph never references an id that was not extracted as a component. Extra
# tags here are harmless if absent from a given file.
COMPONENT_TAGS = {
    "PipingComponent",
    "ProcessInstrument",
    "InstrumentComponent",
    "PipeConnectorSymbol",
    "Nozzle",
    "PipeOffPageConnector",
    "SignalOffPageConnector",
    "PropertyBreak",
    "ProcessSignalGeneratingSystem",
    "ActuatingSystem",
    "ProcessInstrumentationFunction",
    "Equipment",
}
SEGMENT_TAG = "PipingNetworkSegment"


@dataclass
class Doc:
    """A parsed adapter document with indexes the pipeline needs."""

    root: ET.Element
    parent: Dict[ET.Element, ET.Element] = field(default_factory=dict)
    by_id: Dict[str, ET.Element] = field(default_factory=dict)

    # ---- construction ----
    @classmethod
    def from_bytes(cls, data: bytes) -> "Doc":
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            # Some adapter exports contain illegal numeric character references
            # (e.g. control chars) that the strict stdlib parser rejects. Retry
            # with lxml's recovering parser, then hand the cleaned tree back to
            # the stdlib API the rest of the pipeline expects.
            root = cls._recover(data)
        doc = cls(root=root)
        doc.parent = {c: p for p in root.iter() for c in p}
        doc.by_id = {e.get("ID"): e for e in root.iter() if e.get("ID")}
        return doc

    @staticmethod
    def _recover(data: bytes) -> ET.Element:
        """Parse bytes that strict XML rejects, using lxml with recover=True.
        Falls back to stripping illegal char refs if lxml is unavailable."""
        try:
            import lxml.etree as LET
            parser = LET.XMLParser(recover=True, huge_tree=True)
            ltree = LET.fromstring(data, parser=parser)
            # re-serialize the cleaned tree and re-parse with the stdlib parser,
            # so downstream code keeps working against xml.etree elements
            clean = LET.tostring(ltree)
            return ET.fromstring(clean)
        except Exception:
            import re
            # last resort: drop illegal numeric character references
            text = data.decode("utf-8", errors="replace")
            text = re.sub(r"&#x?0*[0-8bcefBCEF];", "", text)   # control chars
            return ET.fromstring(text)

    @classmethod
    def from_path(cls, path: str) -> "Doc":
        with open(path, "rb") as fh:
            return cls.from_bytes(fh.read())

    # ---- catalogue filtering ----
    def in_catalogue(self, e: ET.Element) -> bool:
        """True if e is a symbol definition under a ShapeCatalogue (not a real
        instance). Every component is duplicated: once as a catalogue symbol,
        once as a placed instance. We only ever want instances."""
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
        """First populated GenericAttribute value with this Name."""
        if e is None:
            return None
        for g in e:
            if g.tag == "GenericAttributes":
                for h in g:
                    if h.get("Name") == name and h.get("Value"):
                        return h.get("Value")
        return None

    @classmethod
    def tag(cls, e: Optional[ET.Element]) -> Optional[str]:
        return cls.ga(e, "ItemTag")

    @staticmethod
    def all_ga(e: ET.Element) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for g in e:
            if g.tag == "GenericAttributes":
                for h in g:
                    if h.get("Value"):
                        out.setdefault(h.get("Name"), h.get("Value"))
        return out

    @staticmethod
    def location(e: Optional[ET.Element]) -> Optional[XY]:
        if e is None:
            return None
        for c in e:
            if c.tag == "Position":
                for l in c:
                    if l.tag == "Location":
                        return float(l.get("X")), float(l.get("Y"))
        return None

    @staticmethod
    def extent(e: ET.Element) -> Optional[Tuple[float, float, float, float]]:
        for c in e:
            if c.tag == "Extent":
                mn = next((x for x in c if x.tag == "Min"), None)
                mx = next((x for x in c if x.tag == "Max"), None)
                if mn is not None and mx is not None:
                    return (
                        float(mn.get("X")), float(mn.get("Y")),
                        float(mx.get("X")), float(mx.get("Y")),
                    )
        return None

    @staticmethod
    def nodes(e: ET.Element) -> List[dict]:
        """Connection-point nodes: name, coordinate, nominal diameter."""
        out: List[dict] = []
        for cp in e:
            if cp.tag == "ConnectionPoints":
                for nd in cp:
                    if nd.tag != "Node":
                        continue
                    p = None
                    dia = None
                    for q in nd:
                        if q.tag == "Position":
                            for l in q:
                                if l.tag == "Location":
                                    p = (float(l.get("X")), float(l.get("Y")))
                        elif q.tag == "NominalDiameter":
                            dia = q.get("Value")
                    if p is not None:
                        out.append({"name": nd.get("Name"), "xy": p, "dia": dia})
        return out

    @staticmethod
    def centerline(seg: ET.Element) -> List[XY]:
        for c in seg:
            if c.tag == "CenterLine":
                return [
                    (float(co.get("X")), float(co.get("Y")))
                    for co in c
                    if co.tag == "Coordinate"
                ]
        return []


def arc_position(cl: List[XY], x: float, y: float) -> Optional[float]:
    """Distance along polyline `cl` of the projection of (x, y).

    Returns None if the polyline is degenerate. Used to order inline
    components along a segment. The projection is essentially exact for
    real adapter data (node coords lie on the centerline), so the returned
    ordering is reliable.
    """
    if len(cl) < 2:
        return None
    best_d = math.inf
    best_s = 0.0
    acc = 0.0
    for i in range(len(cl) - 1):
        (x1, y1), (x2, y2) = cl[i], cl[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L == 0:
            continue
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (L * L)))
        px, py = x1 + t * dx, y1 + t * dy
        d = math.hypot(x - px, y - py)
        if d < best_d:
            best_d = d
            best_s = acc + t * L
        acc += L
    return best_s
