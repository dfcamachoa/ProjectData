"""A DEMO-SCOPED stand-in for `silver/reconstruct.py`'s pure core -- NOT a
copy of it, and not meant to be one.

The real `reconstruct_document()` calls the vendored, validated `pidtool` /
`bppidsys` / `pidsys` reconstruction engine (topology-from-geometry, business
tag composition via `pidsys.master_data.TaggingConvention`/`TagTemplate`,
ghost-filtered equipment, ...) -- hundreds of lines this project's own
discipline says to *re-house, not re-derive*. Reproducing that engine from
search snippets of a RAG index would be exactly the re-derivation this
project's spec repeatedly warns against, with no way to validate the result
byte-for-byte against the real code.

What this module does instead: `demo_cdc.write_narrative`'s synthetic XML is
deliberately simple -- a `<PipingNetworkSegment>` per line with inline
`<PipingComponent>`/`<ProcessInstrument>` children, one `<Equipment>` per
item with N flat `<Nozzle>` children, straight-line geometry, and (by
design) no `<Connection>` elements at all. For exactly this shape, the
segment/component/equipment ROW SHAPE Stage E's `cdc.py` needs can be read
straight off the XML structure without any topology inference -- so this is
a minimal, direct extractor for THIS narrative's XML, not a general P&ID
reconstruction.

The one piece of real business logic reproduced here is the Project-B
segment business-tag composition, confirmed against a REAL observed anchor
string (`README.md`'s 2026-09-06 finding: a real `silver_cdc` batch's
component anchor was `CMP|SEG|216097C-A22-PID-0021-0015-001|2"-WBF-2215101-
B242A-H|GateValve`) and against `silver/_recon/pidsys/master_data.py`'s
`B_SEG_TMPL` template (groups: diameter, fluid+subline_core, piping_class,
insul_purpose, joined by "-", empty groups dropped). In this narrative's
data, `ItemTag` is always `"{fluid}-{7 digits}"` and the decode+recompose of
that digit string is a lossless round trip, so `fluid + "-" + subline_core`
reduces to the literal `ItemTag` -- which is exactly the shape the confirmed
real anchor above shows (`WBF-2215101` appears unchanged inside the seg_tag).

Connections are intentionally left empty (`[]`): this narrative's XML has no
`<Connection>` elements and no derivable topology, and the real platform's
own run of this exact narrative also produced 0 connection-grain CDC events
-- so this scoping matches observed real behaviour, not just convenience.
One narrative row this therefore cannot reproduce: the "component Modified
-- connectivity" case for the retained gate valve whose neighbour (the
removed check valve) was deleted (`NARRATIVE.md`'s connectivity note) --
named here as an explicit, out-of-scope gap rather than silently missing.
"""
from __future__ import annotations

from typing import Dict, List, Optional
from xml.etree import ElementTree as ET


def _ga(el, name: str) -> Optional[str]:
    for gas in el:
        if gas.tag != "GenericAttributes":
            continue
        for g in gas:
            if g.tag == "GenericAttribute" and g.get("Name") == name:
                v = g.get("Value")
                if v not in (None, ""):
                    return v
    return None


def compose_segment_business_tag(item_tag: Optional[str], diameter: Optional[str],
                                  piping_class: Optional[str],
                                  insul_purpose: Optional[str]) -> Optional[str]:
    """Project-B segment business tag: diameter - itemtag - class - insul
    purpose, empty groups dropped (mirrors `pidsys.master_data.B_SEG_TMPL`;
    see module docstring for the confirmation this matches a real anchor)."""
    if not item_tag:
        return None
    parts = [p for p in (diameter, item_tag, piping_class, insul_purpose) if p]
    return "-".join(parts)


def reconstruct_document(content: bytes, *, bronze_id: str, content_hash: str,
                          document_number: str, project_code: str,
                          revision: str) -> Dict[str, List[dict]]:
    """Parse one narrative drawing XML into {segments, components, equipment,
    connections} row dicts, in the shape `silver/cdc.py` expects (see module
    docstring for what is and is not reproduced from the real engine)."""
    root = ET.fromstring(content)
    lineage = dict(project_code=project_code, source_format="POSTPROC",
                   drawing_number=document_number)

    segments: List[dict] = []
    components: List[dict] = []
    for seg_el in root.iter("PipingNetworkSegment"):
        seg_id = seg_el.get("ID")
        item_tag = _ga(seg_el, "ItemTag")
        diameter = _ga(seg_el, "NominalDiameter")
        piping_class = _ga(seg_el, "PipingMaterialsClass")
        insul_purpose = _ga(seg_el, "InsulPurpose")
        seg_tag = compose_segment_business_tag(item_tag, diameter, piping_class, insul_purpose)
        segments.append({
            "uid": seg_id,
            "fluid": _ga(seg_el, "OperFluidCode"),
            "unit": None,
            "diameter": diameter,
            "piping_materials_class": piping_class,
            "insul_purpose": insul_purpose,
            "insul_type": _ga(seg_el, "InsulType"),
            "insul_thick": _ga(seg_el, "InsulThick"),
            "seg_tag": seg_tag,
            "src_turnover": None,
            "src_subsystem": None,
            "version": content_hash,
            "revision": revision,
            **lineage,
        })
        for idx, child in enumerate(seg_el):
            if child.tag not in ("PipingComponent", "ProcessInstrument"):
                continue
            components.append({
                "uid": child.get("ID"),
                "component_class": child.get("ComponentClass"),
                "segment_id": seg_id,
                "inline_index": idx,
                "version": content_hash,
                "revision": revision,
                **lineage,
            })

    equipment: List[dict] = []
    for eq_el in root.iter("Equipment"):
        nozzle_ids = [nz.get("ID") for nz in eq_el.findall("Nozzle")]
        equipment.append({
            "uid": eq_el.get("ID"),
            "tag": _ga(eq_el, "ItemTag"),
            "equipment_class": None,
            "nozzle_tags": nozzle_ids,
            "version": content_hash,
            "revision": revision,
            **lineage,
        })

    return {"segments": segments, "components": components, "equipment": equipment,
            "connections": []}
