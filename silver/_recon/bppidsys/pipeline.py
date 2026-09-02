"""
pipeline.py — extraction + topology-first valve-aware reconstruction for
INGR ISO-15926 PostProc exports.

Project-B counterpart to pidtool/pipeline.py. Emits the SAME PipelineResult /
Component contract the unchanged pidsys core consumes:

    PipelineResult.components : list[Component]  (each with id, kind, cls,
                               seg_id, attrs{OperFluidCode, PipingMaterialsClass,
                               SubsystemNo, Z_TurnOverSystemNumber, ...})
    PipelineResult.und/adj/radj : undirected + directed adjacency dicts
    PipelineResult.by_id()      : id -> Component

Stages (mirroring pidtool):
    1 parse / index            (model.Doc)
    2 extract objects          one record per component / segment
    3 raw topology             read <Connection> endpoints
    4 inline reconstruction    order inline valves along the segment centerline
    5 valve-aware graph         skeleton from shared segment endpoints, valves
                               inserted into each segment's chain; directed by
                               FlowDirection
    6 stats
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .model import COMPONENT_TAGS, SEGMENT_TAG, Doc, arc_position

# Single definition — the valve-class set lives in the base adapter (pidtool) and
# is shared here, so the two adapters can never drift apart. [de-duplicated]
from pidtool.pipeline import VALVE_CLASSES

# Attributes the pidsys core reads off each segment (via the owning segment for
# a component). Carried verbatim into Component.attrs so reconstructed.py finds
# them unchanged. Z_TurnOverSystemNumber / SubsystemNo are absent in PostProc
# and resolve to None — validation is cohesion-only there.
SEGMENT_ATTRS = (
    "OperFluidCode",
    "PipingMaterialsClass",
    "SubsystemNo",
    "Z_TurnOverSystemNumber",
    "FlowDirection",
    "NominalDiameter",
    "UnitCode",
)


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
    seg_id: Optional[str] = None
    in_raw_graph: bool = False
    inline_index: Optional[int] = None
    inline_count: Optional[int] = None


@dataclass
class PipelineResult:
    components: List[Component]
    edges: List[Tuple[str, str, str]]
    adj: Dict[str, List[str]]
    radj: Dict[str, List[str]]
    und: Dict[str, List[str]]
    valves: Set[str]
    stats: Dict[str, object]
    # SVG drawing payload (same shape pidtool emits, so overlay_ui.render_system_svg
    # consumes it unchanged): sheet size in metres, faint background geometry, and
    # text labels. Populated by Pipeline._build_geometry from the PostProc file.
    sheet: Tuple[float, float] = (0.841, 0.594)
    bg: List[dict] = field(default_factory=list)
    texts: List[dict] = field(default_factory=list)

    def by_id(self) -> Dict[str, Component]:
        return {c.id: c for c in self.components}


class Pipeline:
    def __init__(self, doc: Doc):
        self.doc = doc

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
        stats = self._stats(comps, raw_nodes, edges, und, valves)
        sheet, bg, texts = self._build_geometry()
        return PipelineResult(
            components=comps, edges=edges, adj=adj, radj=radj, und=und,
            valves=valves, stats=stats, sheet=sheet, bg=bg, texts=texts,
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

            is_seg = e.tag == SEGMENT_TAG
            loc = d.location(e)
            cl = d.centerline(e) if is_seg else []
            if loc is None and cl:
                loc = cl[len(cl) // 2]
            ext = d.extent(e)
            if loc is None and ext:
                loc = ((ext[0] + ext[2]) / 2, (ext[1] + ext[3]) / 2)

            parent = d.parent.get(e)
            seg_owner = parent if (parent is not None and parent.tag == SEGMENT_TAG) else None
            seg_tag = d.tag(seg_owner) if seg_owner is not None else (d.tag(e) if is_seg else None)
            seg_id = (seg_owner.get("ID") if seg_owner is not None
                      else (eid if is_seg else None))

            attrs: Dict[str, str] = {}
            if is_seg:
                for a in SEGMENT_ATTRS:
                    v = d.ga(e, a)
                    if v is not None:
                        attrs[a] = v
                # keep the line class too, for reference
                if e.get("TagName"):
                    attrs["TagName"] = e.get("TagName")

            out.append(Component(
                id=eid,
                kind="Segment" if is_seg else e.tag,
                cls=(None if is_seg else d.cc(e)),
                tag=d.tag(e),
                x=(loc[0] if loc else None),
                y=(loc[1] if loc else None),
                extent=ext,
                centerline=cl,
                nodes=(d.nodes(e) if not is_seg else []),
                attrs=attrs,
                seg_tag=seg_tag,
                seg_id=seg_id,
            ))
        return out

    # ---- stage 3: raw topology ----
    def _raw_topology(self) -> Set[str]:
        ids: Set[str] = set()
        for c in self.doc.root.iter("Connection"):
            for k in ("FromID", "ToID"):
                v = c.get(k)
                if v:
                    ids.add(v)
        return ids

    # ---- stage 4: inline ordering along the centerline ----
    def _reconstruct_inline(self, comps: List[Component]) -> List[dict]:
        """For each segment, build terminal -> inline... -> terminal chain.

        The skeleton comes from the <Connection> endpoints inside the segment
        (topology-first, deterministic). Inline valves that the raw graph
        misses are ordered by projecting their location onto the centerline.
        """
        d = self.doc
        by_id = {c.id: c for c in comps}

        # segment id -> list of its inline component ids (piping comps under it)
        seg_members: Dict[str, List[str]] = defaultdict(list)
        for c in comps:
            if c.kind != "Segment" and c.seg_id:
                seg_members[c.seg_id].append(c.id)

        skeleton: List[dict] = []
        for seg in d.root.iter(SEGMENT_TAG):
            if d.in_catalogue(seg):
                continue
            sid = seg.get("ID")
            cl = d.centerline(seg)
            fd = d.ga(seg, "FlowDirection")
            seg_tag = d.tag(seg)

            # endpoints from the segment's own <Connection> records
            endpoints: List[str] = []
            for conn in seg.findall("Connection"):
                for k in ("FromID", "ToID"):
                    v = conn.get(k)
                    if v:
                        endpoints.append(v)

            members = seg_members.get(sid, [])
            # terminals = the two extreme endpoints of the segment chain; the
            # in-between + all inline members get ordered by arc position.
            def rep_arc(cid: str) -> float:
                e = d.by_id.get(cid)
                if e is None or not cl:
                    return 0.0
                ns = d.nodes(e)
                pts = [n["xy"] for n in ns if n.get("xy")]
                if not pts:
                    loc = d.location(e)
                    pts = [loc] if loc else []
                vals = [arc_position(cl, x, y) for (x, y) in pts]
                vals = [v for v in vals if v is not None]
                return sum(vals) / len(vals) if vals else 0.0

            chain_ids = list(dict.fromkeys(endpoints + members))  # de-dup, keep order
            chain_ids = [c for c in chain_ids if c in by_id]
            ordered = sorted(chain_ids, key=rep_arc)

            skeleton.append({
                "seg_id": sid, "seg_tag": seg_tag, "fd": fd,
                "chain": ordered,
            })
        return skeleton

    # ---- stage 5: build the graph ----
    def _build_graph(self, skeleton: List[dict], known_ids: Set[str]):
        edges: List[Tuple[str, str, str]] = []
        node_ids: Set[str] = set()
        for s in skeleton:
            chain = [n for n in s["chain"] if n and n in known_ids]
            if s["fd"] and "downstream" in (s["fd"] or ""):
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

    # ---- geometry payload for the SVG overlay ----
    def _build_geometry(self):
        """Extract the sheet size, faint background geometry, and text labels the
        SVG renderer (overlay_ui.render_system_svg) paints under the coloured
        system dots. Same shapes pidtool emits:

            sheet = (W, H) in metres, from Drawing/Extent/Max (real project-B
                    sheet size, not the hardcoded DEXPI A1 frame)
            bg    = [{"t":"p","pts":[(x,y)...],"c":hex,"w":width}, ...] polylines
                    + {"t":"c","x","y","r"} circles
            texts = [{"x","y","h","j","a","s"}, ...]

        Background is capped so a dense drawing can't produce a multi-megabyte
        SVG; the cap keeps the schematic readable without every hairline.
        """
        d = self.doc
        root = d.root

        # --- sheet size from the Drawing extent ---
        sheet = (0.841, 0.594)
        dr = root.find(".//Drawing")
        if dr is not None:
            ext = dr.find("Extent")
            if ext is not None:
                mx = ext.find("Max")
                if mx is not None and mx.get("X") is not None:
                    try:
                        sheet = (float(mx.get("X")), float(mx.get("Y")))
                    except (TypeError, ValueError):
                        pass

        def _pres_color(el):
            p = el.find("Presentation")
            if p is None:
                return "#666666"
            try:
                r = int(p.get("R", "80")); g = int(p.get("G", "80")); b = int(p.get("B", "80"))
                return "#%02x%02x%02x" % (r, g, b)
            except (TypeError, ValueError):
                return "#666666"

        def _pres_weight(el):
            p = el.find("Presentation")
            try:
                return float(p.get("LineWeight")) if p is not None else 0.0002
            except (TypeError, ValueError):
                return 0.0002

        def _coords(el):
            pts = []
            for co in el.findall("Coordinate"):
                try:
                    pts.append((float(co.get("X")), float(co.get("Y"))))
                except (TypeError, ValueError):
                    continue
            return pts

        bg: List[dict] = []
        MAX_BG = 6000          # cap total background primitives
        # polylines and straight lines (the bulk of the drawing)
        for tag in ("PolyLine", "Line"):
            for el in root.iter(tag):
                if len(bg) >= MAX_BG:
                    break
                pts = _coords(el)
                if len(pts) >= 2:
                    bg.append({"t": "p", "pts": pts,
                               "c": _pres_color(el), "w": _pres_weight(el)})
        # circles (symbols/labels) — position from Position/Location, radius attr
        for el in root.iter("Circle"):
            if len(bg) >= MAX_BG:
                break
            loc = d.location(el)
            try:
                rad = float(el.get("Radius"))
            except (TypeError, ValueError):
                rad = None
            if loc and rad:
                bg.append({"t": "c", "x": loc[0], "y": loc[1], "r": rad})

        # --- text labels ---
        texts: List[dict] = []
        MAX_TX = 4000
        for el in root.iter("Text"):
            if len(texts) >= MAX_TX:
                break
            s = el.get("String")
            if not s:
                continue
            loc = d.location(el)
            if not loc:
                continue
            try:
                h = float(el.get("Height", "0.0025"))
            except (TypeError, ValueError):
                h = 0.0025
            try:
                a = float(el.get("TextAngle", "0"))
            except (TypeError, ValueError):
                a = 0.0
            texts.append({"x": loc[0], "y": loc[1], "h": h,
                          "j": el.get("Justification", "LeftCenter"),
                          "a": a, "s": s})

        return sheet, bg, texts

    # ---- stats ----
    def _stats(self, comps, raw_nodes, edges, und, valves):
        comp_only = [c for c in comps if c.kind != "Segment"]
        cls_counts = Counter(c.cls for c in comp_only)
        all_valves = [c for c in comp_only if c.cls in VALVE_CLASSES]
        raw_valves = sum(1 for c in all_valves if c.id in raw_nodes)
        graph_valves = sum(1 for v in valves if v in und)

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
            "components": len(comp_only),
            "segments": sum(1 for c in comps if c.kind == "Segment"),
            "classes": dict(cls_counts.most_common()),
            "total_valves": len(all_valves),
            "valves_in_raw_graph": raw_valves,
            "valves_in_rebuilt_graph": graph_valves,
            "graph_nodes": len(und),
            "graph_edges": len(edges),
            "connected_components": ncc,
        }