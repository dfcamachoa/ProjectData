"""Pure, Spark-free bridge between Silver Stage E's real (collected) rows and
Gold's bi-temporal core (`temporal.py` / `silver_cdc.py`) -- the same
"pure core, thin Spark job" split `bronze/header.py` vs `bronze/ingest.py`
and `silver/cdc.py` vs `silver/cdc_job.py` already use throughout this
project. Every function here takes and returns plain dicts/lists -- exactly
what `Row.asDict(recursive=True)` gives after `.collect()` -- so it is
unit-tested without a `SparkSession`, the same way this project's other
pure cores are. `gold/spark_job.py` is the thin wrapper that does the actual
`spark.table(...)` / `.collect()` / `.write.format("delta")` calls around
these functions; nothing in THIS module imports pyspark.

This replaces the `.toPandas()`-based notebook glue
(`append_gold_cells.py`'s Bridge 1/2) that the earlier prototype used —
`.collect()` into plain dicts is what Silver's own `cdc_job.py` does at this
same PoC scale (driver-side over the collected tables), so Gold now follows
the identical discipline instead of routing through pandas.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from .silver_cdc import (
    LINE_ENGINEERING_ATTR_FIELDS,
    CdcAnomaly,
    SilverCdcEvent,
    aggregate_line_attrs,
)
from .temporal import DeltaType, GoldRow

# Grain -> Silver table / id column (mirrors silver/cdc_job.py's own naming).
# "line" is deliberately absent from GRAIN_ID_COL -- Stage E's line-grain
# old_uid/new_uid is f"{drawing_number}|{seg_tag}" (a real 2026-09-06
# finding), not a *_id row id, so there is no single row to .loc[]/index
# into for that grain -- see `resolve_line_seg_tag` in silver_cdc.py.
GRAIN_TABLE = {"component": "silver_components", "line": "silver_segments",
               "equipment": "silver_equipment", "connection": "silver_connections"}
GRAIN_ID_COL = {"component": "component_id", "equipment": "equipment_id",
                "connection": "connection_id"}

# bronze_layer_spec.md §6: dates are stored verbatim, project-scoped format.
# DDMMMYY is DEXPI/project A's; YYYY/MM/DD is PostProc/project B's -- add a
# project's own format here if it uses neither.
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d%b%y", "%d-%b-%y", "%d/%m/%Y", "%m/%d/%Y")


def parse_revision_date(raw) -> date:
    """Verbatim `drawing_revision_date` -> a `date`, trying each
    project-scoped format in turn."""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"drawing_revision_date {raw!r} doesn't match any of {DATE_FORMATS} -- "
        "add this project's format to spark_bridge.DATE_FORMATS."
    )


def valid_from_by_drawing(bronze_rows: "list[dict]") -> "dict[str, date]":
    """{document_number: latest known drawing_revision_date} from
    already-collected Bronze rows (each a dict with at least
    `document_number` / `drawing_revision_date`) -- the run's single
    valid_from per drawing (gold_layer_spec.md: one date, not per-row)."""
    out: Dict[str, date] = {}
    for row in bronze_rows:
        raw = row.get("drawing_revision_date")
        doc = row.get("document_number")
        if not raw or not doc:
            continue
        d = parse_revision_date(raw)
        if doc not in out or d > out[doc]:
            out[doc] = d
    return out


def lines_for_drawing(dwg: str, segment_rows: "list[dict]", component_rows: "list[dict]",
                       connection_rows: "list[dict]", aggregate_lines_fn=None) -> "list[dict]":
    """One drawing-version's `silver.cdc.aggregate_lines` call, reshaped
    exactly as `silver/cdc_job.py`'s own `_obj_seg`/`_obj_cmp` do
    (`segment_id`/`component_id` renamed to `"uid"` -- `aggregate_lines`'
    own internal key). `aggregate_lines_fn` defaults to
    `silver.cdc.aggregate_lines` when importable (this project's
    "re-house, don't re-derive" discipline, silver_layer_spec.md §0); pass
    it explicitly in a test, or accept the `[]` fallback below for a
    `gold_layer.zip` checkout without `silver/` on the path (the caller,
    `resolve_line_attrs_for_event`, then falls back to `aggregate_line_attrs`
    per-line instead)."""
    if aggregate_lines_fn is None:
        try:
            from silver.cdc import aggregate_lines as aggregate_lines_fn
        except ImportError:
            return []
    segs = [{**r, "uid": r["segment_id"]} for r in segment_rows if r.get("drawing_number") == dwg]
    comps = [{**r, "uid": r["component_id"]} for r in component_rows if r.get("drawing_number") == dwg]
    conns = [r for r in connection_rows if r.get("drawing_number") == dwg]
    return aggregate_lines_fn(segs, comps, conns)


def _line_attrs_from_aggregate(line_obj: dict) -> dict:
    """`silver.cdc.aggregate_lines`'s per-line dict -> a Gold `attrs` payload
    (matches `LINE_ENGINEERING_ATTR_FIELDS` shape)."""
    attrs = {f: tuple(line_obj[f + "_set"]) for f in LINE_ENGINEERING_ATTR_FIELDS}
    attrs["inconsistent_fields"] = tuple(line_obj.get("inconsistent") or [])
    attrs["line_attr_inconsistent"] = bool(line_obj.get("inconsistent"))
    attrs["neighbour_lines"] = tuple(line_obj.get("neighbour_lines") or [])
    attrs["piece_count"] = len(line_obj.get("piece_uids") or [])
    attrs["segment_ids"] = tuple(line_obj.get("piece_uids") or [])
    return attrs


# Per-physical-segment scalar fields kept verbatim (never reduced to a
# distinct-value set) for `map_line`'s child Segment nodes — the user's own
# correction that a segment carries its own real engineering attributes,
# not just a placeholder identity nested under its Line. `segment_id` and
# `seg_tag` identify the piece; the rest mirror `LINE_ENGINEERING_ATTR_FIELDS`
# plus the containment/oracle fields `rdf_mapper.map_segment` already reads.
_SEGMENT_PIECE_FIELDS = (
    "segment_id", "seg_tag", "fluid", "unit", "diameter", "piping_materials_class",
    "insul_type", "insul_purpose", "insul_thick",
    "subline_tag", "pns_tag", "src_turnover", "src_subsystem",
)


def _segment_piece_attrs(row: dict) -> dict:
    """One physical `silver_segments` row's own scalar attributes, kept
    exactly as recorded (never aggregated into a set) — the shape
    `rdf_mapper.map_line` hands to `map_segment` per piece."""
    return {k: row[k] for k in _SEGMENT_PIECE_FIELDS if row.get(k) is not None}


def resolve_line_attrs_for_event(seg_tag: str, dwg: str, lines: "list[dict]",
                                  segment_rows: "list[dict]") -> "Optional[dict]":
    """A line-grain event's attrs: prefer the real `aggregate_lines` result
    already computed for this drawing (`lines`); fall back to
    `aggregate_line_attrs` over the matching raw `silver_segments` pieces
    when `lines` is empty (no `silver/` on the path). Returns `None` when
    neither source has a match, so the caller can skip and count it.

    Either way, the returned attrs also carry `"pieces"`: each matching raw
    `silver_segments` row's own un-reduced scalar attributes
    (`_segment_piece_attrs`), regardless of which branch computed the
    aggregated fields — `rdf_mapper.map_line` uses this to emit real child
    Segment nodes rather than a placeholder. `pieces` can legitimately come
    back `[]` even when the aggregated fields are non-empty, if `segment_rows`
    doesn't cover this drawing (a caller passing a narrower `segment_rows`
    than what produced `lines`) — an honest reflection of the caller's own
    inputs, not a bug in this function.
    """
    match = next((l for l in lines if l.get("seg_tag") == seg_tag), None)
    pieces = [r for r in segment_rows
              if r.get("drawing_number") == dwg and r.get("seg_tag") == seg_tag]
    if match is not None:
        attrs = _line_attrs_from_aggregate(match)
    elif pieces:
        attrs = aggregate_line_attrs(pieces)
    else:
        return None
    attrs["pieces"] = [_segment_piece_attrs(p) for p in pieces]
    return attrs


def build_events(
    cdc_rows: "list[dict]",
    valid_from_by_doc: "dict[str, date]",
    attrs_by_grain: "dict[str, dict]",          # {grain: {uid: row_dict}} for component/equipment/connection
    lines_by_drawing: "dict[str, list]",        # {drawing_number: [aggregate_lines() result, ...]}
    segment_rows: "list[dict]",                  # raw silver_segments rows -- the aggregate_line_attrs fallback
) -> "Tuple[List[SilverCdcEvent], List[dict]]":
    """`silver_cdc` rows (already `.collect()`ed to dicts) -> `SilverCdcEvent`s,
    resolving each event's engineering attrs from the appropriate Silver
    source per grain. Returns `(events, skipped)` -- `skipped` is a list of
    `{cdc_id, reason}` dicts (never a silent drop), matching this project's
    "observe and record" discipline (Stage D's `silver_quality` precedent).
    """
    events: List[SilverCdcEvent] = []
    skipped: List[dict] = []
    for row in cdc_rows:
        dwg = row.get("drawing_number")
        valid_from = valid_from_by_doc.get(dwg)
        if valid_from is None:
            skipped.append({"cdc_id": row.get("cdc_id"),
                             "reason": f"no Bronze drawing_revision_date for drawing {dwg!r}"})
            continue

        grain = row["grain"]
        uid = row.get("new_uid") or row.get("old_uid")

        if row["change_type"] == "Deleted":
            attrs: Optional[dict] = {}
        elif grain == "line":
            seg_tag = uid.split("|", 1)[1] if uid and "|" in uid else uid
            attrs = resolve_line_attrs_for_event(
                seg_tag, dwg, lines_by_drawing.get(dwg, []), segment_rows)
            if attrs is None:
                skipped.append({"cdc_id": row.get("cdc_id"),
                                 "reason": f"no silver_segments pieces for line "
                                           f"(drawing_number={dwg!r}, seg_tag={seg_tag!r})"})
                continue
        else:
            table = attrs_by_grain.get(grain, {})
            src = table.get(uid)
            if src is None:
                skipped.append({"cdc_id": row.get("cdc_id"),
                                 "reason": f"new_uid {uid!r} not found in "
                                           f"{GRAIN_TABLE.get(grain)}"})
                continue
            skip_keys = {GRAIN_ID_COL.get(grain), "segment_id", "project_code",
                         "source_format", "drawing_number", "content_hash",
                         "bronze_id"}
            attrs = {k: v for k, v in src.items() if k not in skip_keys and v is not None}

        tx = row.get("transaction_ts")
        events.append(SilverCdcEvent(
            object_kind=grain, anchor_id=row["anchor"], delta_type=DeltaType(row["change_type"]),
            drawing_number=dwg, drawing_revision_date=valid_from, bronze_ingested_at=tx,
            attrs=attrs or {},
            content_hash_eng=row.get("new_content_hash_eng") or row.get("old_content_hash_eng"),
            content_hash_audit=row.get("new_content_hash_audit") or row.get("old_content_hash_audit"),
        ))
    return events, skipped


# --------------------------------------------------------------------------- #
#  GoldRow <-> plain dict (the Gold Delta table's row shape)                  #
# --------------------------------------------------------------------------- #
def gold_row_to_dict(row: GoldRow, drawing_number: Optional[str], attrs_json: str) -> dict:
    """`GoldRow` -> the flat dict `gold/schema.py::GOLD_OBJECTS_STRUCT`
    expects, ready for `spark.createDataFrame`. `attrs_json` is passed in
    (rather than computed here) so this module never needs to pick a JSON
    encoder for the caller's Spark session — `gold/spark_job.py` does that
    with the stdlib `json` module before calling this."""
    return {
        "object_kind": row.object_kind,
        "anchor_id": row.anchor_id,
        "drawing_number": drawing_number,
        "attrs_json": attrs_json,
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "tx_from": row.tx_from,
        "tx_to": row.tx_to,
        "superseded_by_delta": row.superseded_by_delta.value if row.superseded_by_delta else None,
        "current": row.is_current(),
    }


def dict_to_gold_row(d: dict, attrs: dict) -> GoldRow:
    """The inverse of `gold_row_to_dict` (minus JSON decoding, which
    `gold/spark_job.py` does before calling this, for the same reason as
    above) -- rebuilds a `GoldRow` from a previously-written Gold table row,
    so `apply_silver_cdc_events_tolerant` can extend the prior run's history
    rather than starting over."""
    return GoldRow(
        object_kind=d["object_kind"], anchor_id=d["anchor_id"], attrs=attrs,
        valid_from=d["valid_from"], valid_to=d.get("valid_to"),
        tx_from=d["tx_from"], tx_to=d.get("tx_to"),
        superseded_by_delta=(DeltaType(d["superseded_by_delta"])
                             if d.get("superseded_by_delta") else None),
    )


def anomaly_to_dict(a: CdcAnomaly, run_ts: datetime) -> dict:
    """`CdcAnomaly` -> the flat dict `gold/schema.py::GOLD_ANOMALIES_STRUCT`
    expects."""
    return {
        "object_kind": a.event.object_kind,
        "anchor_id": a.event.anchor_id,
        "drawing_number": a.event.drawing_number,
        "reason": a.reason,
        "detail": a.detail,
        "transaction_ts": run_ts,
    }
