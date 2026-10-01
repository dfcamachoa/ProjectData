"""Silver Stage C — cross-document assembly (OPC stitch), the pure core.

Stage B reconstructs each drawing independently; Stage C joins those per-drawing
graphs into one plant by matching **off-page connectors** (OPCs) across sheets
(silver_spec §3.3). It re-houses the validated matcher — `bppidsys.offpage`
(OPCTag for PostProc, GUID for DEXPI) via `pidsys.reconstructed._harvest_opcs` —
exactly as `ReconstructedGraph.assemble` does, but in the medallion's shape: a
cheap per-drawing **harvest** (parse + collect each sheet's OPC records) feeds a
plant-level **reduce** (`match_pairs`) that emits:

    * `OffPage` rows into `silver_connections` — one undirected, always-`derived`
      edge per matched OPC pair, spanning two drawings (ConnType OFFPAGE, §3.3);
    * `opc_open_boundary` rows into `silver_quality` — one per **unmatched** OPC,
      whose mate is not in the loaded set (an OPEN BOUNDARY: the system continues
      off-set — a flag, never an error or a dropped row, §3.3/§3.4);
    * `silver_off_page_connectors` rows — one per placed OPC (matched AND
      unmatched), the per-OPC ENTITY the Gold C_OFF_PAGE_CONNECTOR node projects
      from (Workstream 2). Descriptive fields (opc_type, flow_direction,
      to_from_text, ...) come from the widened harvest; `on_segment` is filled by
      the Spark job's join to silver_components (this pure core leaves it None).

This module is Spark-free and unit-tested; the Spark job (`assemble_job.py`) does
the harvest fan-out and the idempotent writes.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

from .reconstruct import _adapter, _cid  # adapter pick + deterministic edge id

# lineage keys carried on every harvested OPC record and emitted row
_LINEAGE_KEYS = ("bronze_id", "content_hash", "source_format", "project_code",
                 "drawing_number")

# the unified OPC-record fields the matcher reads (format-independent)
_OPC_MATCH_KEYS = ("eid", "home", "paired", "opctag", "guid_self", "guid_mate")

# Workstream 2: descriptive OPC ENTITY fields (Step 1 harvest additions) that
# ride ALONGSIDE the matcher keys, for the silver_off_page_connectors entity
# table (schema.OPC_ENTITY_STRUCT). They do NOT affect matching -- match_pairs
# reads only _OPC_MATCH_KEYS -- but the earlier narrow projection dropped them,
# so the Gold OffPageConnector node had no source. on_segment is NOT here: the
# harvest is parse-only (no Pipeline.run()), so the segment comes from a join
# to silver_components in assemble_job.py, not from this record.
_OPC_ENTITY_KEYS = ("opc_type", "flow_direction", "class_uri",
                    "to_from_dir", "to_from_text")


def harvest_opcs_document(
    content: bytes,
    source_format: Optional[str],
    *,
    bronze_id: Optional[str] = None,
    content_hash: Optional[str] = None,
    document_number: Optional[str] = None,
    project_code: Optional[str] = None,
) -> List[dict]:
    """Parse one drawing and return its placed OPCs as unified records
    (`eid/home/paired/opctag/guid_self/guid_mate` + lineage). Cheap: it parses
    and scans for OPC elements only — no `Pipeline.run()` / stamping. Malformed
    input yields no records (honest-partial-result)."""
    if isinstance(content, bytearray):
        content = bytes(content)
    try:
        Doc, _Pipeline = _adapter(source_format)
        dom = Doc.from_bytes(content)
        from pidsys.reconstructed import _harvest_opcs
        raw = _harvest_opcs(dom)
    except Exception:
        return []

    lineage = dict(bronze_id=bronze_id, content_hash=content_hash,
                   source_format=source_format, project_code=project_code,
                   drawing_number=document_number)
    out: List[dict] = []
    for r in raw:
        # matcher keys + Workstream-2 entity fields (the latter ride along for
        # the silver_off_page_connectors table; they don't affect match_pairs)
        rec = {k: r.get(k) for k in _OPC_MATCH_KEYS}
        rec.update({k: r.get(k) for k in _OPC_ENTITY_KEYS})
        rec.update(lineage)
        out.append(rec)
    return out


def _offpage_connection_row(a: str, b: str, rec_a: dict) -> dict:
    """One undirected, always-derived OffPage edge (silver_spec §3.3, §4). Lineage
    is the home side's (`a`); the mate side is reachable via `to_id`."""
    lo, hi = (a, b) if a <= b else (b, a)
    return {
        "connection_id": _cid(lo, hi, "OffPage"),
        "from_id": lo,
        "to_id": hi,
        "conn_type": "OffPage",
        "derived": True,                       # OPC continuation is always derived
        "flow_sense": "none",                  # the stitch is undirected (adds to `und`)
        "bronze_id": rec_a.get("bronze_id"),
        "content_hash": rec_a.get("content_hash"),
        "source_format": rec_a.get("source_format"),
        "project_code": rec_a.get("project_code"),
        "drawing_number": rec_a.get("drawing_number"),
    }


def _open_boundary_row(rec: dict, run_ts) -> dict:
    """One `opc_open_boundary` ledger row for an unmatched OPC (silver_spec §3.4)."""
    tag = rec.get("opctag") or rec.get("guid_self") or rec.get("eid")
    paired = rec.get("paired")
    where = f" (paired drawing {paired})" if paired else ""
    return {
        "object_id": rec.get("eid"),
        "object_kind": "connection",
        "flag": "opc_open_boundary",
        "severity": "info",
        "gate": "flag",
        "stage": "C",
        "detail": (f"OPC '{tag}' on drawing {rec.get('drawing_number')}{where} has "
                   f"no mate in the loaded set — open boundary (the system "
                   f"continues off-set; not an error)"),
        "drawing_number": rec.get("drawing_number"),
        "project_code": rec.get("project_code"),
        "source_format": rec.get("source_format"),
        "transaction_ts": run_ts,
    }


def _opc_entity_row(rec: dict, *, matched: bool) -> dict:
    """One silver_off_page_connectors row (schema.OPC_ENTITY_STRUCT) for a placed
    OPC — matched or unmatched. Descriptive fields come from the widened harvest
    record; `on_segment` is left None here and filled by assemble_job.py's join
    to silver_components (the pure core has no Spark tables). `matched` records
    whether this OPC also has an OffPage edge."""
    return {
        "opc_id": rec.get("eid"),
        "tag": rec.get("opctag"),
        "opc_type": rec.get("opc_type"),
        "flow_direction": rec.get("flow_direction"),
        "component_class_uri": rec.get("class_uri"),
        "to_from_dir": rec.get("to_from_dir"),
        "to_from_text": rec.get("to_from_text"),
        "on_segment": None,                    # filled by the job's silver_components join
        "paired_drawing": rec.get("paired"),
        "matched": matched,
        "bronze_id": rec.get("bronze_id"),
        "content_hash": rec.get("content_hash"),
        "source_format": rec.get("source_format"),
        "project_code": rec.get("project_code"),
        "drawing_number": rec.get("drawing_number"),
    }


def assemble_opcs(opc_records: List[dict], *, run_ts=None) -> dict:
    """The plant-level reduce: match every harvested OPC and produce the OffPage
    connection rows + open-boundary ledger rows + the per-OPC entity rows.

    Returns ``{offpage_connections, open_boundaries, off_page_connectors, stats}``
    where stats mirrors the PoC's ``opc_stitched`` (matched pairs) / ``opc_offset``
    (unmatched). ``off_page_connectors`` is one entity row per placed OPC (matched
    AND unmatched) for the silver_off_page_connectors table; its ``on_segment`` is
    None here and filled by assemble_job.py's join to silver_components.
    """
    from bppidsys.offpage import match_pairs

    records = list(opc_records)
    by_eid: Dict[str, dict] = {r["eid"]: r for r in records if r.get("eid")}
    edges, unmatched = match_pairs(records)

    offpage: List[dict] = []
    seen = set()
    for a, b in edges:
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        rec_a = by_eid.get(a, {})
        offpage.append(_offpage_connection_row(a, b, rec_a))

    boundaries = [_open_boundary_row(by_eid.get(eid, {"eid": eid}), run_ts)
                  for eid in unmatched]

    # per-OPC entity rows — every placed OPC, matched flag set from the pairs
    matched_eids = {e for pair in edges for e in pair}
    entities = [_opc_entity_row(rec, matched=(eid in matched_eids))
                for eid, rec in by_eid.items()]

    return {
        "offpage_connections": offpage,
        "open_boundaries": boundaries,
        "off_page_connectors": entities,
        "stats": {
            "opc_records": len(records),
            "opc_stitched": len(offpage),     # matched cross-sheet pairs
            "opc_offset": len(unmatched),     # open boundaries
        },
    }
