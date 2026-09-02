"""Silver Stage A + B — parse + reconstruct one Bronze document into typed rows.

This is the **pure, Spark-independent core** (testable without Spark). It
*re-houses* the validated `pidtool` / `bppidsys` reconstruction (vendored under
`silver/_recon/`) — it never re-derives it (silver_spec §2, §6.1). Given one
Bronze row's raw XML bytes + its `source_format`, it returns row dicts for the
four Silver tables:

    components  — one Piping Component (valves, fittings, nozzles, …)
    segments    — one Piping Segment (carries the QUARANTINED oracle, §5)
    connections — one undirected reified edge (+ derived flag + flow_sense, §4)
    equipment   — one real (ghost-filtered) Equipment

The Spark job (`silver/spark_job.py`) calls this once per drawing inside a UDF
and explodes the four arrays into the four Delta tables — parse once, distribute
over files (silver_spec §6.1).

Discipline preserved here (silver_spec §5): the reconstructed graph carries the
source turnover assignment as `seg_sys` / `seg_sub`; this core copies those onto
the segment row as **quarantined lineage** (`src_turnover` / `src_subsystem`)
and computes *nothing* from them.
"""
from __future__ import annotations

import hashlib
import os
import sys
from typing import Dict, List, Optional

# --- vendored reconstruction on the import path ---------------------------- #
_RECON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_recon")
if _RECON not in sys.path:
    sys.path.insert(0, _RECON)

import pidtool  # noqa: E402
import bppidsys  # noqa: E402
from pidsys.reconstructed import ReconstructedGraph  # noqa: E402
from pidsys.master_data import stamp_master_data  # noqa: E402


def _adapter(source_format: Optional[str]):
    """Pick the reconstruction adapter from the Bronze `source_format` column
    (silver_spec §3.1) — no DOM re-sniffing. POSTPROC → bppidsys, else pidtool."""
    fmt = (source_format or "").upper()
    if fmt.startswith("POST") or fmt == "POSTPROC" or fmt == "SPPID":
        return bppidsys.Doc, bppidsys.Pipeline
    return pidtool.Doc, pidtool.Pipeline


def _cid(a: str, b: str, conn_type: str) -> str:
    """Deterministic, self-describing connection id (silver_spec §4).

    PHASE-1 NOTE: keyed on element ids for a single-version build. The
    anchor-based identity that survives delete+recreate (silver_spec §3.5) is a
    Stage-E/CDC concern and layers on later without changing this row shape.
    """
    return "sha256:" + hashlib.sha256(f"{a}|{b}|{conn_type}".encode()).hexdigest()


def _raw_connection_pairs(dom) -> set:
    """The endpoint pairs stated directly in the source `<Connection>` records —
    used to flag each reconstructed edge Source (stated) vs Derived (§4)."""
    pairs = set()
    for c in dom.root.iter("Connection"):
        f, t = c.get("FromID"), c.get("ToID")
        if f and t:
            pairs.add((f, t))
    return pairs


def reconstruct_document(
    content: bytes,
    source_format: Optional[str],
    *,
    bronze_id: Optional[str] = None,
    content_hash: Optional[str] = None,
    document_number: Optional[str] = None,
    project_code: Optional[str] = None,
    refdata_path: Optional[str] = None,
) -> Dict[str, List[dict]]:
    """Parse + reconstruct one drawing; return {components, segments,
    connections, equipment, stats}. Pure — no Spark, no I/O beyond the bytes."""
    if isinstance(content, bytearray):
        content = bytes(content)

    Doc, Pipeline = _adapter(source_format)
    dom = Doc.from_bytes(content)
    res = Pipeline(dom).run()                      # the crown-jewel reconstruction
    g = ReconstructedGraph(res, dom=dom)           # wires real equipment (ghost-filter)
    stamp_master_data(g, dom, refdata_path=refdata_path)  # business tags / names

    lineage = dict(
        bronze_id=bronze_id,
        content_hash=content_hash,
        source_format=source_format,
        project_code=project_code,
        drawing_number=document_number,
    )

    valves = set(res.valves)
    components: List[dict] = []
    segments: List[dict] = []

    for c in res.components:
        if c.kind == "Segment":
            a = c.attrs or {}
            segments.append({
                "segment_id": c.id,
                "fluid": a.get("OperFluidCode"),
                "unit": getattr(c, "unit", None),
                "diameter": a.get("NominalDiameter"),
                "piping_materials_class": a.get("PipingMaterialsClass"),
                "insul_type": a.get("InsulType"),
                "insul_purpose": a.get("InsulPurpose"),
                "insul_thick": a.get("InsulThick"),
                "item_tag": a.get("ItemTag"),
                "seg_tag": getattr(c, "seg_tag", None),
                "pns_tag": getattr(c, "pns", None),
                "subline_tag": getattr(c, "subline", None),
                # QUARANTINED oracle (silver_spec §5): carried, never computed on.
                "src_turnover": g.seg_sys.get(c.id),
                "src_subsystem": g.seg_sub.get(c.id),
                "quality_gate": "clean",           # GX populates in Stage D
                **lineage,
            })
        else:
            components.append({
                "component_id": c.id,
                "component_class": c.cls,
                "component_name": getattr(c, "component_name", None),
                "tag": c.tag,
                "segment_id": c.seg_id,
                "kind": c.kind,
                "inline_index": c.inline_index,
                "inline_count": c.inline_count,
                "is_valve": c.id in valves,
                "quality_gate": "clean",
                **lineage,
            })

    # --- connections: undirected entity + flow_sense overlay + derived flag --- #
    raw = _raw_connection_pairs(dom)
    seen = set()
    connections: List[dict] = []
    for u, nbrs in g.und.items():
        for v in nbrs:
            a, b = (u, v) if u <= v else (v, u)    # canonical undirected order
            if (a, b) in seen:
                continue
            seen.add((a, b))
            fwd = b in g.adj.get(a, ())            # a -> b known?
            rev = a in g.adj.get(b, ())            # b -> a known?
            flow_sense = ("both" if (fwd and rev)
                          else "forward" if fwd
                          else "reverse" if rev
                          else "none")
            derived = not ((a, b) in raw or (b, a) in raw)
            ctype = ("Nozzle" if (a in g.equipment or b in g.equipment
                                  or a in g.nozzles or b in g.nozzles)
                     else "Process")
            connections.append({
                "connection_id": _cid(a, b, ctype),
                "from_id": a,
                "to_id": b,
                "conn_type": ctype,
                "derived": derived,
                "flow_sense": flow_sense,
                **lineage,
            })

    # --- equipment: real (ghost-filtered) items and their nozzles ------------ #
    equipment: List[dict] = []
    for eid, tag in g.equipment.items():
        equipment.append({
            "equipment_id": eid,
            "tag": tag,
            "equipment_class": None,               # class enrichment: later
            "nozzle_ids": sorted(g.eq_nozzles.get(eid, [])),
            **lineage,
        })

    return {
        "components": components,
        "segments": segments,
        "connections": connections,
        "equipment": equipment,
        "stats": dict(res.stats),
    }
