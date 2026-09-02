"""
reconstructed.py — build the systemization graph from pidtool's
topology-first reconstruction, instead of the raw <Connection> records.
[YOUR REPO FILE — patched for multi-format source adapters]
"""
from __future__ import annotations
from collections import defaultdict

from .master_data import stamp_master_data, equipment_is_real


def _adapter_for(path):
    """Return (Doc, Pipeline) for the file's source format.
    PostProc detected by a PipingNetworkSegment TagName (DEXPI never has one),
    so DEXPI/project-A files always route to pidtool exactly as before."""
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    from bppidsys import Doc as _BDoc
    if _BDoc.is_postproc(root):
        from bppidsys import Doc, Pipeline
    else:
        from pidtool import Doc, Pipeline
    return Doc, Pipeline


def _harvest_opcs(dom):
    """Harvest placed-OPC pairing records for whichever format `dom` is.
    Returns a list of dicts consumed by bppidsys.offpage.match_pairs."""
    # PostProc: OPCs are PipeConnectorSymbol cls=OPC, keyed by OPCTag.
    from bppidsys import Doc as _BDoc
    if isinstance(dom, _BDoc):
        from bppidsys.offpage import harvest_opcs
        return harvest_opcs(dom)
    # DEXPI: OPCs are PipeOffPageConnector, keyed by SP_pairedWithID GUID.
    recs = []
    for e in dom.root.iter("PipeOffPageConnector"):
        if dom.in_catalogue(e):
            continue
        eid = e.get("ID")
        recs.append({
            "eid": eid,
            "guid_self": eid[2:] if eid and eid.startswith("SP") else eid,
            "guid_mate": dom.ga(e, "SP_pairedWithID"),
            "opctag": None,          # DEXPI pairs by GUID, not OPCTag
            "home": None, "paired": dom.ga(e, "PairedDrawingNumber"),
        })
    return recs


# Boundary-forming component classes [MD §3.2] — loaded from the `Boundary`
# reference sheet (Class, Role, Boundary), with a built-in fallback. Changing a
# project's boundary philosophy is now a reference-data edit, not a code change.
from .refdata import load_boundary_sets as _load_boundary_sets
_BOUNDARY = _load_boundary_sets()
ISOLATION = _BOUNDARY["isolation"]
POSITIVE = _BOUNDARY["positive"]
RELIEF = _BOUNDARY["relief"]
TRAP = _BOUNDARY["trap"]
BOUNDARY_CLASSES = _BOUNDARY["all"]


def _drawing_number(dom, path):
    """The P&ID Document number for a sheet — the DrawingNumber attribute the
    file carries (same value the overlay's document_stem targets). Falls back to
    the filename stem (minus a _Dexpi/_Post format suffix) if the attribute is
    absent, so a component is never left without a drawing label."""
    try:
        dr = dom.root.find(".//Drawing")
        num = dom.ga(dr, "DrawingNumber") if dr is not None else None
        if num:
            return num
    except Exception:
        pass
    import os
    stem = os.path.basename(path)
    stem = stem[:-4] if stem.lower().endswith(".xml") else stem
    for suffix in ("_Dexpi", "_Post"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _stamp_drawing(graph, dom, path):
    """Tag every component from this sheet with its Document number, so the
    assembled graph knows which P&ID each component sits on (SS §6.1 Spanned
    Documents). Sets .drawing on each Component of this sheet's result."""
    number = _drawing_number(dom, path)
    for c in graph.res.components:
        # only stamp components that don't already carry a drawing (in assemble,
        # each sheet's g is fresh, so this tags exactly that sheet's components)
        if getattr(c, "drawing", None) is None:
            try:
                c.drawing = number
            except Exception:
                pass
    graph.drawing_number = number


class ReconstructedGraph:
    def __init__(self, res, dom=None):
        self.res = res
        self._dom = dom
        self.by = res.by_id()
        self.und = {k: set(v) for k, v in res.und.items()}
        self.adj = {k: set(v) for k, v in res.adj.items()}
        self.radj = {k: set(v) for k, v in res.radj.items()}
        self.seg_fluid = {}; self.seg_mc = {}; self.seg_sub = {}; self.seg_sys = {}
        for c in res.components:
            if c.kind == "Segment":
                self.seg_fluid[c.id] = c.attrs.get("OperFluidCode")
                self.seg_mc[c.id] = c.attrs.get("PipingMaterialsClass")
                self.seg_sub[c.id] = c.attrs.get("SubsystemNo")
                self.seg_sys[c.id] = c.attrs.get("Z_TurnOverSystemNumber")
        self.comp_fluid = {}; self.comp_class = {}; self.nozzles = set(); self.real_equipment = {}
        for c in res.components:
            self.comp_class[c.id] = c.cls
            if c.kind != "Segment" and c.seg_id:
                self.comp_fluid[c.id] = self.seg_fluid.get(c.seg_id)
            if c.kind == "Nozzle":
                self.nozzles.add(c.id)
        self.equipment = {}; self.eq_nozzles = {}
        self._wire_equipment(res)

    def _wire_equipment(self, res):
        dom = self._dom
        if dom is None:
            return
        for eq in dom.root.iter("Equipment"):
            tag = dom.tag(eq)
            noz = [n.get("ID") for n in eq.iter("Nozzle") if n.get("ID")]
            if not equipment_is_real(tag, noz):
                continue
            eid = eq.get("ID")
            self.equipment[eid] = tag
            self.eq_nozzles[eid] = set(noz)
            for nz in noz:
                self.und.setdefault(eid, set()).add(nz)
                self.und.setdefault(nz, set()).add(eid)

    @classmethod
    def from_path(cls, path):
        Doc, Pipeline = _adapter_for(path)
        dom = Doc.from_path(path)
        res = Pipeline(dom).run()
        g = cls(res, dom=dom)
        _stamp_drawing(g, dom, path)          # record each component's P&ID
        stamp_master_data(g, dom)            # PNS/segment business tags + ComponentName
        return g

    @classmethod
    def assemble(cls, paths, progress=None):
        merged = None
        opc_records = []
        n = len(paths)
        for i, p in enumerate(paths):
            name = __import__("os").path.basename(p)
            try:
                Doc, Pipeline = _adapter_for(p)
                dom = Doc.from_path(p)
                g = cls(Pipeline(dom).run(), dom=dom)
                _stamp_drawing(g, dom, p)          # record each component's P&ID
                stamp_master_data(g, dom)            # PNS/segment business tags + ComponentName
                opc_records.extend(_harvest_opcs(dom))
                g._dom = None
                if merged is None:
                    merged = g
                else:
                    merged._absorb(g)
                if progress:
                    progress(i + 1, n, name, "ok")
            except Exception as exc:
                if progress:
                    progress(i + 1, n, name, f"ERROR: {exc}")
                continue
        if merged is None:
            raise RuntimeError("no drawings could be parsed")
        merged._stitch_from(opc_records)
        return merged

    def _absorb(self, other):
        for k, v in other.und.items(): self.und.setdefault(k, set()).update(v)
        for k, v in other.adj.items(): self.adj.setdefault(k, set()).update(v)
        for k, v in other.radj.items(): self.radj.setdefault(k, set()).update(v)
        self.comp_fluid.update(other.comp_fluid); self.comp_class.update(other.comp_class)
        self.nozzles |= other.nozzles; self.equipment.update(other.equipment)
        self.seg_fluid.update(other.seg_fluid); self.seg_sub.update(other.seg_sub)
        self.seg_sys.update(other.seg_sys); self.seg_mc.update(other.seg_mc)
        self.by.update(other.by)
        self.res.components = list(self.res.components) + list(other.res.components)

    def _stitch_from(self, opc_records):
        from bppidsys.offpage import match_pairs
        edges, unmatched = match_pairs(opc_records)
        self.opc_stitched = 0
        for a, b in edges:
            self.und.setdefault(a, set()).add(b)
            self.und.setdefault(b, set()).add(a)
            self.opc_stitched += 1
        self.opc_offset = len(unmatched)

    def is_boundary(self, cid): return self.comp_class.get(cid) in BOUNDARY_CLASSES
    def flows_into(self, u, v): return v in self.adj.get(u, ())
    def flow_between(self, u, v):
        f = v in self.adj.get(u, ()); r = u in self.adj.get(v, ())
        if f and r: return "both"
        if f: return "u->v"
        if r: return "v->u"
        return None

    def classify_fluids(self):
        from .walk import FLARE_FLUIDS
        fluid_comps = defaultdict(list)
        for cid, fl in self.comp_fluid.items():
            if fl: fluid_comps[fl].append(cid)
        result, spread, segspan = {}, {}, {}
        for fl, comps in fluid_comps.items():
            syss = set()
            for cid in comps:
                seg = self._seg_of(cid)
                if seg and self.seg_sys.get(seg): syss.add(self.seg_sys[seg])
            spread[fl] = len(syss); segspan[fl] = len(comps)
            result[fl] = "flare" if fl in FLARE_FLUIDS else "distributed"
        return result, segspan, spread

    def _seg_of(self, cid):
        c = self.by.get(cid)
        return c.seg_id if c else None
