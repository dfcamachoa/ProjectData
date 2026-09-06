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

OBJECT_KINDS = ("component", "segment", "equipment", "connection")

# --- Real-data finding (2026-09-04, Project B Rev C/D narrative): silver_cdc's
# `anchor` is a BUCKET key for components -- silver_layer_spec.md §3.5 pairs
# multiple same-class siblings on one segment within a shared
# `(segment, component_class)` bucket, not a per-instance string. So a single
# CDC batch CAN legitimately carry two simultaneous New/Modified/Deleted
# events for one `anchor_id` (two GateValves added to the same segment in one
# revision, say) -- the exact same class of non-uniqueness
# `silver_layer_spec.md` §3f already documents for `seg_tag`, one layer up.
# `apply_silver_cdc_events` (below) still raises on this, by design -- one
# current row per anchor is the documented Gold contract. Prefer
# `apply_silver_cdc_events_tolerant` for a real Stage-E feed, which reports
# such collisions as anomalies (this project's "flag, don't silently fail"
# discipline -- Silver Stage D's `silver_quality` precedent) instead of
# aborting the whole run on the first one.


@dataclass
class SilverCdcEvent:
    """One `silver_cdc` row (silver_layer_spec.md, Stage E implementation
    note). `anchor_id` is the anchor-match identity (never a source UID) —
    Gold uses it verbatim as the bi-temporal row's anchor, so a
    delete+recreate that Stage E's own acceptance test proves collapses to
    zero deltas never reaches Gold as churn either.
    """
    object_kind: str                 # 'component' | 'segment' | 'equipment' | 'connection'
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
