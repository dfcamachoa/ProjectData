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
    from bppidsys.offpage import _parse_to_from   # shared TO/FROM label parser
    for e in dom.root.iter("PipeOffPageConnector"):
        if dom.in_catalogue(e):
            continue
        eid = e.get("ID")
        to_from_dir, to_from_text = _parse_to_from(dom.ga(e, "ToFromText"))
        recs.append({
            "eid": eid,
            "guid_self": eid[2:] if eid and eid.startswith("SP") else eid,
            "guid_mate": dom.ga(e, "SP_pairedWithID"),
            "opctag": None,          # DEXPI pairs by GUID, not OPCTag
            "home": None, "paired": dom.ga(e, "PairedDrawingNumber"),
            # --- Workstream 2 OPC feed (Step 1) -----------------------------
            # DEXPI carries BOTH a typed symbol flow role (ComponentClass:
            # FlowIn/FlowOutPipeOffPageConnector, with ComponentClassURI the
            # sandbox RDL URI, passed through verbatim -- NEVER minted as a
            # TEN_RDL/PLM class) AND the same descriptive GenericAttributes
            # PostProc uses (OPCType classification, ToFromText mate-narrative).
            # Confirmed 2026-09-18 that OPCType/ToFromText are NOT PostProc-only.
            # flow_direction is the real flow role; opc_type / to_from_* are
            # descriptive provenance and assert NO direction (ToFromText names
            # the mate, e.g. "LP ACID GAS FLARE", it is not a flow sense).
            # None of these affect pairing (match_pairs reads only guid_*/opctag).
            # on_segment is attached later from the built component's .seg_id
            # (Step 3), not read here.
            # ComponentClass / ComponentClassURI are DIRECT XML ATTRIBUTES on the
            # element (confirmed against real Project-A DEXPI 2026-09-23:
            # <PipeOffPageConnector ComponentClass="FlowOut..." ComponentClassURI=
            # "http://sandbox.dexpi.org/rdl/FlowOut..."/>), NOT <GenericAttribute>
            # children -- so they are read with e.get(), not dom.ga() (which reads
            # GenericAttribute children only and returns None for element attrs).
            # OPCType / ToFromText, by contrast, ARE GenericAttribute children, so
            # they keep dom.ga(). This split was the cause of empty flow_direction/
            # component_class_uri while opc_type/to_from_text populated correctly.
            "flow_direction": e.get("ComponentClass"),
            "class_uri": e.get("ComponentClassURI"),
            "opc_type": dom.ga(e, "OPCType"),
            "to_from_dir": to_from_dir,
            "to_from_text": to_from_text,
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
        # OPC stitching results — populated by _stitch_from (assemble() path
        # only). Defaulted here so a single-sheet from_path() graph, which never
        # stitches, still exposes them safely (empty structure, zero counts)
        # instead of AttributeError when Step-4 code or the coverage report
        # reads them. Workstream 2 (Step 2).
        self.opc_pairs = []
        self.opc_unmatched = []
        self.opc_by_id = {}
        self.opc_stitched = 0
        self.opc_offset = 0
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
        """Cross-document OPC stitching. Adds the matched pairs as undirected
        graph.und adjacency (so a system continues across sheets and walk.py's
        off-set detection works) AND — Workstream 2 (Steps 2-3) — retains the
        stitched structure, enriched with each OPC's on-segment, so the Silver
        OPC feed (Steps 4-5) can build per-OPC rows (incl. the `terminates`
        edge) and the matched-pair "Off-Page continuation" connections.

        Before this, only two integer COUNTS survived (opc_stitched/opc_offset)
        and the pairing was discarded once folded into und — leaving no source
        for a Gold OffPageConnector node or terminates edge. The counts are kept
        as integers (unchanged; the coverage report and notebook glue read them
        that way); the retained structure rides alongside as new attributes:

          opc_pairs    -- [(eid_a, eid_b), ...] the matched cross-document pairs
          opc_unmatched-- [eid, ...] placed OPCs whose mate is not in the loaded
                          set (open boundary — the system continues off-set;
                          each still gets a Gold node, but NO pair connection)
          opc_by_id    -- {eid: harvest_record} so a pair's eids resolve back to
                          the full Step-1 record (opc_type, flow_direction,
                          class_uri, to_from_dir/text, paired, home), each record
                          ALSO carrying on_segment (Step 3): the PipingSegment
                          the OPC sits on — its `terminates` target — or None
                          when the built component has no seg_id (e.g. an
                          instrument OPC not on a pipe; Step 4 branches on
                          opc_type before emitting `terminates`).

        opc_records is the concatenation assemble() built across every sheet, so
        opc_by_id spans the whole loaded set — an unmatched eid on one sheet can
        still be resolved to its record for its Gold node.
        """
        from bppidsys.offpage import match_pairs
        edges, unmatched = match_pairs(opc_records)
        # retained structure (Step 2) — new attributes, do not replace the counts
        self.opc_pairs = list(edges)
        self.opc_unmatched = list(unmatched)
        self.opc_by_id = {r["eid"]: r for r in opc_records if r.get("eid")}
        # on_segment (Step 3): the segment each OPC sits on — the model's
        # `terminates` target (map_off_page_connector). The OPC element is a
        # built component in self.by; a non-Segment component carries .seg_id
        # (the same association __init__ uses for comp_fluid, line 147-148). We
        # write it back onto the retained record here — where both opc_by_id and
        # self.by are in scope — rather than re-reading XML in the Step-1 harvest.
        # None when the component has no seg_id (e.g. an instrument OPC that does
        # not sit on a PipingSegment — Step 4 branches on opc_type before
        # emitting `terminates`; never fabricate a segment). Also cross-checks
        # the OPC-ness of the resolved component against walk._opc_ids' own
        # discriminator so a stray non-OPC eid can't silently acquire a segment.
        for eid, rec in self.opc_by_id.items():
            comp = self.by.get(eid)
            seg_id = getattr(comp, "seg_id", None) if comp is not None else None
            rec["on_segment"] = seg_id
        # existing behaviour — undirected stitch + integer counts (unchanged)
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
