"""The extraction pipeline.

Stages, in order:
    1. parse / index          (model.Doc)
    2. extract objects        semantic + geometric record per component
    3. raw topology           read <Connection> endpoints
    4. inline reconstruction  order valves within each segment by arc length
    5. valve-aware graph       topology-first: skeleton from shared endpoints,
                               valves inserted into each segment's chain
    6. render payload          flatten geometry + graph for the UI

The graph construction is topology-first: the network skeleton comes from
shared endpoint components (a branch that bounds two segments joins them),
which is deterministic and independent of geometry. Geometry is used only
to order the inline valves *inside* a segment.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import xml.etree.ElementTree as ET

from .model import COMPONENT_TAGS, SEGMENT_TAG, Doc, arc_position

# Canonical valve-class set for both adapters — bppidsys imports this rather than
# re-declaring it, so the two format readers stay in lock-step. [single definition]
VALVE_CLASSES = {"GateValve", "CheckValve", "BallValve", "GlobeValve", "ButterflyValve"}
# End-of-line devices whose isolation we precompute.
DEVICE_CLASSES = {
    "HoseRackStation", "SteamTrapAssembly", "SteamTraceHeader",
    "CondensateRecoveryHeader", "Off-lineinstruments",
}


@dataclass
class Component:
    id: str
    kind: str                # element tag or 'Segment'
    cls: Optional[str]
    tag: Optional[str]
    x: Optional[float]
    y: Optional[float]
    extent: Optional[Tuple[float, float, float, float]]
    centerline: List[Tuple[float, float]]
    nodes: List[dict]
    attrs: Dict[str, str]
    seg_tag: Optional[str]
    seg_id: Optional[str] = None      # ID of the owning PipingNetworkSegment
    in_raw_graph: bool = False
    inline_index: Optional[int] = None
    inline_count: Optional[int] = None


@dataclass
class PipelineResult:
    components: List[Component]
    edges: List[Tuple[str, str, str]]           # (u, v, segment_tag) directed
    adj: Dict[str, List[str]]
    radj: Dict[str, List[str]]
    und: Dict[str, List[str]]
    valves: Set[str]
    isolation: Dict[str, dict]                   # component id -> {valves, region}
    bg: List[dict]                               # background geometry
    texts: List[dict]
    sheet: Tuple[float, float]                   # width, height (m)
    stats: Dict[str, object]

    def by_id(self) -> Dict[str, Component]:
        return {c.id: c for c in self.components}


class Pipeline:
    def __init__(self, doc: Doc):
        self.doc = doc

    # ---- public entrypoints ----
    @classmethod
    def from_bytes(cls, data: bytes) -> "Pipeline":
        return cls(Doc.from_bytes(data))

    @classmethod
    def from_path(cls, path: str) -> "Pipeline":
        return cls(Doc.from_path(path))

    def run(self) -> PipelineResult:
        comps = self._extract_objects()
        raw_nodes = self._raw_topology()
        for c in comps:
            c.in_raw_graph = c.id in raw_nodes
        skeleton = self._reconstruct_inline(comps)
        known_ids = {c.id for c in comps}
        edges, adj, radj, und, valves = self._build_graph(skeleton, known_ids)
        isolation = self._precompute_isolation(und, valves)
        bg, texts, sheet = self._render_payload()
        stats = self._stats(comps, raw_nodes, edges, und, valves, isolation)
        return PipelineResult(
            components=comps, edges=edges, adj=adj, radj=radj, und=und,
            valves=valves, isolation=isolation, bg=bg, texts=texts,
            sheet=sheet, stats=stats,
        )

    # ---- stage 2: extract objects ----
    def _extract_objects(self) -> List[Component]:
        d = self.doc
        out: List[Component] = []
        seen: Set[str] = set()
        for e in d.root.iter():
            if e.tag not in COMPONENT_TAGS and e.tag != SEGMENT_TAG:
                continue
            if d.in_catalogue(e):
                continue
            eid = e.get("ID")
            if not eid or eid in seen:
                continue
            seen.add(eid)

            loc = d.location(e)
            cl = d.centerline(e) if e.tag == SEGMENT_TAG else []
            if loc is None and cl:
                loc = cl[len(cl) // 2]
            ext = d.extent(e)
            if loc is None and ext:
                loc = ((ext[0] + ext[2]) / 2, (ext[1] + ext[3]) / 2)

            parent = d.parent.get(e)
            is_seg = e.tag == SEGMENT_TAG
            seg_tag = (
                d.tag(parent) if parent is not None and parent.tag == SEGMENT_TAG
                else (d.tag(e) if is_seg else None)
            )
            # ID of the owning segment: the parent's ID for a member, or the
            # element's own ID for a segment. Unique even when line tags repeat.
            seg_id = (
                parent.get("ID") if parent is not None and parent.tag == SEGMENT_TAG
                else (eid if is_seg else None)
            )
            out.append(Component(
                id=eid,
                kind="Segment" if is_seg else e.tag,
                cls=d.cc(e),
                tag=d.tag(e),
                x=loc[0] if loc else None,
                y=loc[1] if loc else None,
                extent=ext,
                centerline=cl,
                nodes=d.nodes(e),
                attrs=d.all_ga(e),
                seg_tag=seg_tag,
                seg_id=seg_id,
            ))
        return out

    # ---- stage 3: raw topology ----
    def _raw_topology(self) -> Set[str]:
        nodes: Set[str] = set()
        for c in self.doc.root.iter():
            if c.tag == "Connection":
                if c.get("FromID"):
                    nodes.add(c.get("FromID"))
                if c.get("ToID"):
                    nodes.add(c.get("ToID"))
        return nodes

    # ---- stage 4 + 5: reconstruct inline order, per segment ----
    def _reconstruct_inline(self, comps: List[Component]) -> List[dict]:
        """Return one skeleton edge per segment: its two terminal components
        plus the inline components ordered A->B along the centerline."""
        d = self.doc
        by_id = {c.id: c for c in comps}
        skeleton: List[dict] = []
        segs = [
            e for e in d.root.iter()
            if e.tag == SEGMENT_TAG and not d.in_catalogue(e)
        ]
        for seg in segs:
            conns = [c for c in seg if c.tag == "Connection"]
            if not conns:
                continue
            c0 = conns[0]  # exactly one per segment in adapter exports
            a, b = c0.get("FromID"), c0.get("ToID")
            inline = [
                ch.get("ID") for ch in seg
                if ch.tag in COMPONENT_TAGS and ch.get("ID") not in (a, b)
            ]
            cl = d.centerline(seg)
            ordered = self._order_along(cl, a, b, inline)
            skeleton.append({
                "a": a, "b": b, "seg": seg.get("ID"),
                "seg_tag": d.tag(seg), "inline": ordered,
                "fd": d.ga(seg, "FlowDirection"),
            })
            # annotate inline index on components
            if ordered:
                for i, cid in enumerate(ordered):
                    if cid in by_id:
                        by_id[cid].inline_index = i + 1
                        by_id[cid].inline_count = len(ordered)
        return skeleton

    def _order_along(self, cl, a, b, inline) -> List[str]:
        """Order inline ids by arc position so the chain runs from terminal a
        to terminal b."""
        d = self.doc

        def rep_arc(cid: str) -> float:
            e = d.by_id.get(cid)
            if e is None or not cl:
                return 0.0
            ns = d.nodes(e)
            pts = [n["xy"] for n in ns] if ns else (
                [d.location(e)] if d.location(e) else []
            )
            vals = [arc_position(cl, x, y) for (x, y) in pts]
            vals = [v for v in vals if v is not None]
            return sum(vals) / len(vals) if vals else 0.0

        if not inline or not cl:
            return list(inline)
        ordered = sorted(inline, key=rep_arc)
        if rep_arc(a) > rep_arc(b):   # ensure order runs a -> b
            ordered = ordered[::-1]
        return ordered

    def _build_graph(self, skeleton: List[dict], known_ids: Set[str]):
        edges: List[Tuple[str, str, str]] = []
        node_ids: Set[str] = set()
        for s in skeleton:
            # A segment terminal can be None (open connection) or reference an
            # element type not extracted as a component. Drop both from the
            # chain so no null or orphan node reaches the graph; the inline
            # components still chain to whichever valid terminals remain.
            chain = [
                n for n in ([s["a"]] + s["inline"] + [s["b"]])
                if n and n in known_ids
            ]
            if s["fd"] and "downstream" in s["fd"]:
                chain = chain[::-1]
            for u, v in zip(chain, chain[1:]):
                if u == v:
                    continue
                edges.append((u, v, s["seg_tag"]))
                node_ids.add(u)
                node_ids.add(v)

        adj: Dict[str, Set[str]] = defaultdict(set)
        radj: Dict[str, Set[str]] = defaultdict(set)
        und: Dict[str, Set[str]] = defaultdict(set)
        for u, v, _ in edges:
            adj[u].add(v)
            radj[v].add(u)
            und[u].add(v)
            und[v].add(u)

        valves = {
            i for i in node_ids
            if self.doc.cc(self.doc.by_id.get(i)) in VALVE_CLASSES
        }
        return (
            edges,
            {k: list(v) for k, v in adj.items()},
            {k: list(v) for k, v in radj.items()},
            {k: list(v) for k, v in und.items()},
            valves,
        )

    # ---- stage 5b: isolation sets ----
    def _precompute_isolation(self, und, valves, cap: int = 8) -> Dict[str, dict]:
        d = self.doc
        und_sets = {k: set(v) for k, v in und.items()}

        def isolate(target: str):
            seen = {target}
            stack = [target]
            boundary: Set[str] = set()
            while stack:
                x = stack.pop()
                for y in und_sets.get(x, ()):
                    if y in valves:
                        boundary.add(y)
                        seen.add(y)
                        continue
                    if y not in seen:
                        seen.add(y)
                        stack.append(y)
            return boundary, seen

        out: Dict[str, dict] = {}
        for cid in und_sets:
            cls = d.cc(d.by_id.get(cid))
            if cls in VALVE_CLASSES or cls == "PipingNetworkBranch":
                continue
            b, region = isolate(cid)
            if 0 < len(b) <= cap:
                out[cid] = {"valves": list(b), "region": list(region)}
        return out

    # ---- stage 6: render payload ----
    def _render_payload(self):
        d = self.doc
        W, H = 0.841, 0.594  # A1 landscape default; overwritten below if drawing size present

        def pres_of(e):
            x = e
            while x is not None:
                for c in x:
                    if c.tag == "Presentation":
                        return c.attrib
                x = d.parent.get(x)
            return {}

        def rgb(a):
            try:
                return "#%02x%02x%02x" % tuple(
                    int(float(a.get(k, 0)) * 255) for k in "RGB"
                )
            except Exception:
                return "#222"

        def owner_id(e):
            x = d.parent.get(e)
            while x is not None:
                if x.tag in COMPONENT_TAGS or x.tag == SEGMENT_TAG:
                    return x.get("ID")
                x = d.parent.get(x)
            return None

        bg: List[dict] = []
        for e in d.root.iter():
            if e.tag in ("Line", "PolyLine", "CenterLine"):
                pts = [
                    (float(c.get("X")), float(c.get("Y")))
                    for c in e if c.tag == "Coordinate"
                ]
                if len(pts) >= 2:
                    a = pres_of(e)
                    bg.append({
                        "t": "p", "pts": pts, "c": rgb(a),
                        "w": a.get("LineWeight") or "0.00025",
                        "o": owner_id(e),
                    })
            elif e.tag == "Circle":
                p = d.location(e)
                rad = e.get("Radius")
                if p and rad:
                    bg.append({
                        "t": "c", "x": p[0], "y": p[1], "r": float(rad),
                        "c": rgb(pres_of(e)), "o": owner_id(e),
                    })

        texts: List[dict] = []
        for e in d.root.iter():
            if e.tag == "Text" and e.get("String"):
                p = d.location(e)
                if not p:
                    continue
                texts.append({
                    "s": e.get("String").strip(),
                    "x": p[0], "y": p[1],
                    "h": float(e.get("Height") or 0.002),
                    "a": float(e.get("TextAngle") or 0),
                    "j": e.get("Justification", "LeftCenter"),
                })

        # sheet size from Drawing extent if present
        for dr in d.root.iter("Drawing"):
            ext = d.extent(dr)
            if ext:
                W, H = max(ext[2], W), max(ext[3], H)
                break
        return bg, texts, (W, H)

    # ---- reporting ----
    def _stats(self, comps, raw_nodes, edges, und, valves, isolation):
        d = self.doc
        comp_only = [c for c in comps if c.kind != "Segment"]
        cls_counts = Counter(c.cls for c in comp_only)
        raw_valves = sum(
            1 for c in comp_only
            if c.cls in VALVE_CLASSES and c.id in raw_nodes
        )
        graph_valves = sum(1 for v in valves if v in und)
        # components
        n_comp = len(comp_only)
        n_seg = sum(1 for c in comps if c.kind == "Segment")
        # connected components
        und_sets = {k: set(v) for k, v in und.items()}
        seen: Set[str] = set()
        ncc = 0
        for n in und_sets:
            if n in seen:
                continue
            ncc += 1
            stack = [n]
            while stack:
                x = stack.pop()
                if x in seen:
                    continue
                seen.add(x)
                stack.extend(und_sets.get(x, ()))
        return {
            "components": n_comp,
            "segments": n_seg,
            "classes": dict(cls_counts.most_common()),
            "total_valves": len(valves),
            "valves_in_raw_graph": raw_valves,
            "valves_in_rebuilt_graph": graph_valves,
            "graph_nodes": len(und),
            "graph_edges": len(edges),
            "connected_components": ncc,
            "isolation_sets": len(isolation),
        }