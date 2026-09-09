"""Consumption of Silver Stage E's real object-grain CDC output.

Silver Stage E — object-grain change data capture — is now BUILT (see
`silver_layer_spec.md`'s implementation-status box, 2026-09-02): `silver/cdc.py`
(a pure, Spark-free core, 10 unit tests) diffs, per drawing, the two most
recent Bronze versions present in Silver, and writes the `silver_cdc` table —
New/Modified/Deleted deltas keyed on the §3.5 anchor-match (equipment tag /
`(drawing, seg_tag)` / `(segment-anchor, class)` bucket), never the volatile
source UID, with three separated hashes (`anchor_hash`, `content_hash_eng`,
`content_hash_audit`). Its own docstring calls these deltas out by name:
*"the New/Modified/Deleted deltas that are Gold's interval open/close
events."*

This module is what changed as a result: `gold/temporal.py::diff_snapshots`
was always documented as a fallback for exactly this gap
("Gold's own anchor-diffing logic... until Silver Stage E ships"). It now
has ships, so this is the real consumption path — `apply_delta` calls driven
directly by Silver's own delta classification, not a heuristic Gold
recomputes from a snapshot comparison. `diff_snapshots` is kept (and still
tested) as the documented fallback for a Silver build that has not yet run
Stage E, or for a one-off backfill where only snapshots are available; it is
no longer the primary path.

One thing does NOT change with Stage E's arrival: Gold still decides
ordinary-forward-supersession vs. correction vs. retroactive purely from
comparing `valid_from` values (`temporal.py::apply_delta`) — that bi-temporal
interval judgement is Gold's job, not Silver's. Silver's CDC tells Gold
*that* something changed (and, via `content_hash_eng`, that it is a genuine
engineering change, oracle-fields already excluded per the compute-only
firewall — silver_layer_spec.md §5); Gold decides *which interval-closing
shape* that change takes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

from .temporal import DeltaType, GoldRow, RetroactiveCorrection, apply_delta

# --- Line grain (2026-09-05 Silver rename, confirmed on real data 2026-09-06):
# Silver's Stage E now versions piping at LINE grain, not per physical segment
# -- `"segment"` is retired from this module's OBJECT_KINDS in favour of
# `"line"`, matching what a real `silver_cdc` batch's `grain` column actually
# contains. See `aggregate_line_attrs` below for the attrs-resolution side of
# this change (that lives in the caller, not here, but the grain rename is
# what makes `"line"` the object_kind SilverCdcEvent now has to accept).
OBJECT_KINDS = ("component", "line", "equipment", "connection")

# --- Real-data finding (2026-09-04, Project B Rev C/D narrative): silver_cdc's
# `anchor` is a BUCKET key for components -- silver_layer_spec.md §3.5 pairs
# multiple same-class siblings on one line within a shared
# `(line anchor, component_class)` bucket, not a per-instance string. So a
# single CDC batch CAN legitimately carry two simultaneous New/Modified/
# Deleted events for one `anchor_id` (two GateValves added to the same line in
# one revision, say) -- the exact same class of non-uniqueness
# `silver_layer_spec.md` §3f already documents for `seg_tag`, one layer up.
# `apply_silver_cdc_events` (below) still raises on this, by design -- one
# current row per anchor is the documented Gold contract. Prefer
# `apply_silver_cdc_events_tolerant` for a real Stage-E feed, which reports
# such collisions as anomalies (this project's "flag, don't silently fail"
# discipline -- Silver Stage D's `silver_quality` precedent) instead of
# aborting the whole run on the first one.
#
# NOTE: `anchor` (and `anchor_id` below) is always treated as an OPAQUE
# string by this module -- Gold never parses it. So whether a real
# `grain='component'` anchor is formatted `CMP|SEG|...` or `CMP|LINE|...`
# post-rename makes no functional difference here; only `old_uid`/`new_uid`
# at LINE grain needed parsing (a real, confirmed format change -- see the
# caller's `resolve_line_seg_tag`).


@dataclass
class SilverCdcEvent:
    """One `silver_cdc` row (silver_layer_spec.md, Stage E implementation
    note). `anchor_id` is the anchor-match identity (never a source UID) —
    Gold uses it verbatim as the bi-temporal row's anchor, so a
    delete+recreate that Stage E's own acceptance test proves collapses to
    zero deltas never reaches Gold as churn either.
    """
    object_kind: str                 # 'component' | 'line' | 'equipment' | 'connection'
    anchor_id: str                   # Stage E's anchor-match identity (e.g. anchor_hash)
    delta_type: DeltaType
    drawing_number: str
    drawing_revision_date: date      # valid-time source (Bronze, carried through Silver/this event)
    bronze_ingested_at: datetime     # transaction-time source
    attrs: dict = field(default_factory=dict)   # engineering attributes; empty for Deleted
    anchor_hash: Optional[str] = None            # audit/debug lineage, not used for Gold identity beyond anchor_id
    content_hash_eng: Optional[str] = None       # audit/debug lineage — Silver already used this to classify the delta
    content_hash_audit: Optional[str] = None     # audit/debug lineage — carries the UID + quarantined oracle, per Silver §5

    def __post_init__(self) -> None:
        if self.object_kind not in OBJECT_KINDS:
            raise ValueError(f"unknown object_kind {self.object_kind!r}")
        if isinstance(self.delta_type, str):
            self.delta_type = DeltaType(self.delta_type)


def apply_silver_cdc_events(
    rows_by_kind: dict,
    events: Iterable[SilverCdcEvent],
) -> dict:
    """Applies Silver Stage E's own New/Modified/Deleted classification
    directly, one `apply_delta` call per event, grouped by `object_kind`.
    `rows_by_kind` is {object_kind: [GoldRow, ...]} (the previous Gold
    state; pass {} on first load) and is mutated in place as well as
    returned, mirroring `temporal.apply_delta`'s own convention.

    Events are applied in `bronze_ingested_at` order so that, when two
    events land in the same call for the same anchor (a backfill spanning
    several Bronze versions at once), the transaction-time ordering the
    two-axis model depends on (temporal.py §3.3) is respected regardless of
    the order the caller happened to collect them in.
    """
    out = {kind: list(rows_by_kind.get(kind, [])) for kind in OBJECT_KINDS}
    for event in sorted(events, key=lambda e: e.bronze_ingested_at):
        apply_delta(
            out[event.object_kind],
            object_kind=event.object_kind,
            anchor_id=event.anchor_id,
            delta=event.delta_type,
            attrs=dict(event.attrs),
            valid_from=event.drawing_revision_date,
            tx_from=event.bronze_ingested_at,
        )
    return out


@dataclass
class CdcAnomaly:
    """One `apply_delta` failure `apply_silver_cdc_events_tolerant` caught
    instead of aborting the batch on. `reason` is `'anchor_collision'` (a NEW
    landed on an anchor that already has a current row — see the module-level
    real-data-finding note above) or `'retroactive_correction'`
    (`RetroactiveCorrection` — a valid_from earlier than the current row's
    own, which this package deliberately does not interval-split; see
    `temporal.py`'s module docstring)."""
    event: SilverCdcEvent
    reason: str
    detail: str


def apply_silver_cdc_events_tolerant(
    rows_by_kind: dict,
    events: Iterable[SilverCdcEvent],
) -> "tuple[dict, list[CdcAnomaly]]":
    """Same contract as `apply_silver_cdc_events`, except one bad event never
    aborts the whole batch: an anchor collision or a retroactive correction
    is collected as a `CdcAnomaly` and the remaining events still apply.
    This is the project's own "observe-and-record, hard-fail only on
    structural bugs" discipline (Silver Stage D's `silver_quality` gate is
    the precedent this mirrors) — an anchor-bucket collision is real-world
    dirty data (or a genuinely ambiguous multi-sibling change), not a
    pipeline bug, so it belongs on a punch list a person reviews, not a
    crashed notebook.

    Prefer this over the strict function for any real Stage E feed; the
    strict function stays as the documented, simpler contract the unit tests
    pin down (one event, one outcome, no silent partial-batch behaviour).
    """
    out = {kind: list(rows_by_kind.get(kind, [])) for kind in OBJECT_KINDS}
    anomalies: list[CdcAnomaly] = []
    for event in sorted(events, key=lambda e: e.bronze_ingested_at):
        try:
            apply_delta(
                out[event.object_kind],
                object_kind=event.object_kind,
                anchor_id=event.anchor_id,
                delta=event.delta_type,
                attrs=dict(event.attrs),
                valid_from=event.drawing_revision_date,
                tx_from=event.bronze_ingested_at,
            )
        except RetroactiveCorrection as e:
            anomalies.append(CdcAnomaly(event=event, reason="retroactive_correction", detail=str(e)))
        except ValueError as e:
            anomalies.append(CdcAnomaly(event=event, reason="anchor_collision", detail=str(e)))
    return out, anomalies


# ---------------------------------------------------------------------------
# Line-grain attrs resolution (2026-09-06 real-data finding)
#
# A real `grain='line'` silver_cdc row's `old_uid`/`new_uid` is literally
# `f"{drawing_number}|{seg_tag}"` -- e.g. `new_uid` =
# `216097C-A22-PID-0021-0015-001|2"-WBF-2215101-B242A-H"` next to `anchor` =
# `LINE|` + that same string. It is NOT a `silver_segments` row id: a line's
# `(drawing_number, seg_tag)` maps to MULTIPLE physical `silver_segments`
# rows -- the very ~78% of seg_tags shared by 2+ pieces that forced the line
# grain in the first place (silver_layer_spec.md's real-data finding). So a
# caller resolving line attrs cannot `.loc[new_uid]` into one row the way it
# still can for component/equipment/connection grain; it must group by
# `(drawing_number, seg_tag)` and reduce, matching Silver's own line-grain
# `content_hash_eng` semantics (silver_layer_spec.md: each attribute as the
# distinct-value SET across the line's pieces).
#
# RESOLVED 2026-09-06: `silver.cdc.aggregate_lines` IS a real, importable,
# pure-Python function (confirmed once `/silver/` was added to this
# project's sync) -- `(segments, components, connections) -> list[dict]`,
# one dict per composable line, keyed by `"seg_tag"`, with each engineering
# attribute as `"<field>_set"`, plus `"inconsistent"` (list of disagreeing
# field names), `"neighbour_lines"`, and `"piece_uids"`. The notebook glue
# (`append_gold_cells.py`'s `cdc_to_gold_events`) now calls it directly when
# `silver/` is importable, per this project's "re-house, don't re-derive"
# discipline (silver_layer_spec.md §0) -- see that function's own comment
# for the exact row-reshaping it needs (`segment_id`/`component_id` renamed
# to `"uid"`, matching `silver/cdc_job.py`'s own `_obj_seg`/`_obj_cmp`).
# `aggregate_line_attrs` below remains the documented, unit-tested fallback
# for a `gold_layer.zip` used standalone, without `silver/` on the path --
# built from the spec's stated semantics, not a guess, but coarser than the
# real function in one respect: it has no routing (`neighbour_lines` always
# comes back empty here, since that needs the whole drawing's components/
# connections, which this function's narrower signature doesn't take).

LINE_ENGINEERING_ATTR_FIELDS = (
    "fluid", "unit", "diameter", "piping_materials_class",
    "insul_type", "insul_purpose", "insul_thick",
)


def resolve_line_seg_tag(uid: str, drawing_number: str) -> str:
    """Recovers a line's `seg_tag` from Stage E's `old_uid`/`new_uid` at line
    grain (`f"{drawing_number}|{seg_tag}"`), stripping the known
    `drawing_number` prefix rather than a bare `.split("|")` -- a `seg_tag`
    can itself contain characters a naive split would mis-parse."""
    prefix = f"{drawing_number}|"
    if not uid.startswith(prefix):
        raise ValueError(
            f"line uid {uid!r} does not start with the expected "
            f"drawing_number prefix {prefix!r}"
        )
    return uid[len(prefix):]


def _is_present(value) -> bool:
    """Dependency-free "has a real value" check -- `gold/` stays zero
    third-party dependencies (README), so this cannot lean on
    `pandas.isna`. Catches None, blank strings, and NaN (`value != value`
    is True only for NaN, floats included -- how a caller's
    `DataFrame.to_dict("records")` represents a missing numeric cell)."""
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    if isinstance(value, float) and value != value:
        return False
    return True


def aggregate_line_attrs(segment_pieces: "list[dict]") -> dict:
    """Re-derives a line's engineering attrs from its raw `silver_segments`
    pieces (see the module note above). Matches `silver_layer_spec.md`'s
    line-grain `content_hash_eng` semantics: each of
    `LINE_ENGINEERING_ATTR_FIELDS` becomes the SORTED TUPLE of its distinct
    present values across the pieces -- size 1 in the ordinary case; size >1
    is exactly the `line_attr_inconsistent` condition (a within-line spec
    break Silver flags WARN/quarantine rather than silently averaging away),
    so this function surfaces the set rather than picking a value.

    `piece_count` and `segment_ids` ride along for audit/traceability -- the
    informational multiplicity report the old `seg_tag_anchor_collision`
    expectation became at INFO severity once the grain became the line
    (silver_layer_spec.md §3.5): expected, not a defect.

    Output keys match the shape a caller gets back from the real
    `silver.cdc.aggregate_lines` path (`gold_job.py`'s notebook glue prefers
    that function when `silver/` is importable; this is its fallback), with
    one honest gap: `neighbour_lines` always comes back empty here, since
    routing needs the whole drawing's components/connections, which this
    function's narrower per-line signature doesn't take.

    Raises `ValueError` on an empty list -- a caller should never invoke
    this for a line with zero matching pieces; that is itself a data-quality
    signal worth surfacing at the call site, not inside this reduction.
    """
    if not segment_pieces:
        raise ValueError("aggregate_line_attrs called with no segment pieces")

    out: dict = {}
    for attr in LINE_ENGINEERING_ATTR_FIELDS:
        values = sorted({p[attr] for p in segment_pieces if _is_present(p.get(attr))})
        out[attr] = tuple(values)
    inconsistent_fields = tuple(
        attr for attr in LINE_ENGINEERING_ATTR_FIELDS if len(out[attr]) > 1
    )
    out["line_attr_inconsistent"] = bool(inconsistent_fields)
    out["inconsistent_fields"] = inconsistent_fields
    out["neighbour_lines"] = ()  # see docstring -- not computable from pieces alone
    out["piece_count"] = len(segment_pieces)
    out["segment_ids"] = tuple(sorted(
        p["segment_id"] for p in segment_pieces if _is_present(p.get("segment_id"))
    ))
    return out
