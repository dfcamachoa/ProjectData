"""Bronze header resolution for this demo -- a documented SIMPLIFICATION of
the real `bronze/header.py` (read via the project's widened GitHub sync,
2026-09-06), not a re-derivation of its resolution algorithm.

`bronze/header.py`'s pure core (`_scan`) does one bounded, streaming
`iterparse` pass with an element budget -- necessary for real, possibly
large, drawing exports where Bronze must stay "shallow" and cheap. These
synthetic narrative files are a few KB each, so this module parses each one
fully with `xml.etree.ElementTree.fromstring` instead of streaming, then
applies the REAL project-B resolution rules verbatim:

  * format: a `PipingNetworkSegment` carrying a `TagName` -> POSTPROC
    (`bronze/header.py`'s METHOD_SEGMENT_TAGNAME path -- the only path this
    narrative ever exercises, since `demo_cdc.py` only emits PostProc XML).
  * document_number / client_document_number: both = `Drawing/@Name` (project
    B; `_resolve_documents`'s POSTPROC branch).
  * drawing_revision / drawing_revision_date: the REAL PostProc rule from
    `_resolve_revision` -- take the LATEST logged revision as a consistent
    PAIR (the `Revision.RevisionNumber` that is the companion of the latest
    `Revision.TP_RevisionData`), not `Drawing/@Revision` directly.
  * project_code: leading token of `document_number` split on "-"
    (`_derive_project_code`; project B's 216097C prefix).
  * compute_hash: identical to the real `bronze/header.py::compute_hash`
    (sha256 of the exact raw bytes).

What this module does NOT reproduce: the streaming/budget machinery, DEXPI
support (unused here), and the malformed-XML tolerance the real scan has
(these files are always well-formed, since `demo_cdc.py` builds them).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree as ET


@dataclass
class HeaderInfo:
    originating_system: Optional[str] = None
    source_format: Optional[str] = None
    document_number: Optional[str] = None
    client_document_number: Optional[str] = None
    drawing_revision: Optional[str] = None
    drawing_revision_date: Optional[str] = None
    project_code: Optional[str] = None
    header_parse_ok: bool = False


def compute_hash(content: bytes, hash_bits: int = 256) -> str:
    """Verbatim `bronze/header.py::compute_hash` -- self-describing content
    fingerprint over the exact raw bytes."""
    name = {224: "sha224", 256: "sha256", 384: "sha384", 512: "sha512"}[hash_bits]
    return f"{name}:{hashlib.new(name, content).hexdigest()}"


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


def parse_header(content: bytes) -> HeaderInfo:
    info = HeaderInfo()
    if not content:
        return info
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return info

    plant_info = root.find("PlantInformation")
    info.originating_system = plant_info.get("OriginatingSystem") if plant_info is not None else None

    # format: PipingNetworkSegment carrying TagName -> POSTPROC (the real
    # METHOD_SEGMENT_TAGNAME rule; this narrative never emits DEXPI).
    segment_tagname_seen = any(
        seg.get("TagName") is not None for seg in root.iter("PipingNetworkSegment")
    )
    info.source_format = "POSTPROC" if segment_tagname_seen else None

    drawing = root.find("Drawing")
    if drawing is not None and info.source_format == "POSTPROC":
        name = drawing.get("Name")
        info.document_number = name
        info.client_document_number = name

    # revision: the LATEST logged revision as a consistent (number, date) pair
    # -- the real PostProc rule (_resolve_revision), not Drawing/@Revision.
    best_date, best_num = None, None
    for label in root.iter("Label"):
        d = _ga(label, "Revision.TP_RevisionData")
        if d is None:
            continue
        if best_date is None or d > best_date:
            best_date = d
            best_num = _ga(label, "Revision.RevisionNumber")
    if best_date is not None:
        info.drawing_revision, info.drawing_revision_date = best_num, best_date
    elif drawing is not None:
        info.drawing_revision = drawing.get("Revision")

    if info.document_number:
        info.project_code = info.document_number.split("-", 1)[0] or None

    info.header_parse_ok = (
        info.document_number is not None
        and info.drawing_revision is not None
        and info.source_format is not None
    )
    return info
