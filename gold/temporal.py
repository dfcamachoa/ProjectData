"""Bi-temporal versioning of the Silver object grain.

[medallion_rdf_ido_strategy_mapping.md §4] names two independent time axes and
says "decide and document it, don't assume the term exists" for both the axes
and the vocabulary:

  - **Valid time** (engineering reality) — when the plant configuration a
    record describes was true. Driven by the drawing revision (Bronze
    `drawing_revision_date`, format-normalised here — Bronze stores it
    verbatim per bronze_layer_spec.md §6).
  - **Transaction time** (audit / system time) — when the platform *learned*
    the fact. Driven by ingestion (Bronze `ingested_at`).

Grain: the object grain Silver's CDC (Stage E) is designed around — component /
segment / equipment / connection, keyed on a stable **anchor id**
(silver_layer_spec.md §3.5), never the volatile source UID.

Interval discipline: "never delete, close the interval" — a row's `valid_to`
and `transaction_to` are None while current; a superseding fact closes both
axes on the prior row and opens a new one. A **retroactive correction** (a
newly-learned fact whose valid time precedes the current row's valid_from) is
the one edge case full SQL:2011 bi-temporal tables handle by splitting the
valid-time interval; this prototype does not implement interval splitting —
it closes the transaction axis (the platform did just learn something) and
raises `RetroactiveCorrection` so it is queued for a person to reconcile,
rather than silently asserting an interval shape nobody has validated. This is
the same "flag, don't silently resolve" discipline the specs use everywhere
else (algorithm_spec.md §11).

Silver's object-grain CDC (Stage E) is now BUILT (`silver/cdc.py` +
`silver/cdc_job.py`, per `silver_layer_spec.md`'s implementation-status box —
"Silver is complete... Next layer: Gold"). `gold/silver_cdc.py` is the real
consumption path: it calls `apply_delta` directly, driven by Stage E's own
New/Modified/Deleted classification and its anchor-match identity.
`diff_snapshots` below remains as the documented fallback for a Silver build
predating Stage E, or a snapshot-only backfill — it is no longer the primary
path, but every function it calls (`apply_delta`, `current_truth`) is the
same one Stage E's events drive, so nothing here changed shape when Stage E
shipped; only which caller is preferred did (see `gold_job.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Iterable, Optional


class DeltaType(str, Enum):
    NEW = "New"
    MODIFIED = "Modified"
    DELETED = "Deleted"


class RetroactiveCorrection(Exception):
    """Raised when a new fact's valid_from precedes the current row's
    valid_from for the same anchor — a correction whose valid-time interval
    would need splitting. See module docstring."""


@dataclass
class GoldRow:
    object_kind: str          # 'component' | 'segment' | 'equipment' | 'connection'
    anchor_id: str            # stable business anchor (silver_layer_spec.md §3.5), never a source UID
    attrs: dict
    valid_from: date
    valid_to: Optional[date]
    tx_from: datetime
    tx_to: Optional[datetime]
    superseded_by_delta: Optional[DeltaType] = None  # None while current

    def is_current(self) -> bool:
        return self.valid_to is None and self.tx_to is None

    def content_key(self, exclude: frozenset = frozenset()) -> tuple:
        """Order-independent fingerprint of the engineering attributes, for
        detecting a no-op re-ingest (identical content re-landed) vs a real
        Modify. `exclude` is how a caller keeps oracle fields (src_turnover /
        src_subsystem) out of the change-detection surface — mirroring
        Silver's compute-only firewall (silver_layer_spec.md §5): the oracle
        may ride along in `attrs`, but it must never be what *triggers* a
        Gold version bump, or a source-turnover correction would masquerade
        as an engineering change.
        """
        return tuple(sorted((k, v) for k, v in self.attrs.items() if k not in exclude))


ORACLE_ATTR_KEYS = frozenset({"src_turnover", "src_subsystem"})


def _find_current(rows: list[GoldRow], anchor_id: str) -> Optional[GoldRow]:
    for r in rows:
        if r.anchor_id == anchor_id and r.is_current():
            return r
    return None


def apply_delta(
    rows: list[GoldRow],
    object_kind: str,
    anchor_id: str,
    delta: DeltaType,
    attrs: dict,
    valid_from: date,
    tx_from: datetime,
) -> list[GoldRow]:
    """Apply one CDC event (from Silver Stage E, or the snapshot-diff
    fallback below) to a Gold row history. Returns the updated list
    (mutated in place and also returned for convenience).
    """
    current = _find_current(rows, anchor_id)

    if delta is DeltaType.NEW:
        if current is not None:
            raise ValueError(f"NEW delta for anchor already current: {anchor_id}")
        rows.append(
            GoldRow(
                object_kind=object_kind,
                anchor_id=anchor_id,
                attrs=dict(attrs),
                valid_from=valid_from,
                valid_to=None,
                tx_from=tx_from,
                tx_to=None,
            )
        )
        return rows

    if current is None:
        # Modify/Delete with no current row is itself a data-quality signal
        # upstream (an anchor CDC did not expect); treat it as a NEW here so
        # Gold never silently drops an object, and let the caller's own
        # quality ledger flag the anomaly.
        return apply_delta(rows, object_kind, anchor_id, DeltaType.NEW, attrs, valid_from, tx_from)

    if delta is DeltaType.MODIFIED:
        eng_changed = current.content_key(ORACLE_ATTR_KEYS) != GoldRow(
            object_kind, anchor_id, attrs, valid_from, None, tx_from, None
        ).content_key(ORACLE_ATTR_KEYS)
        if not eng_changed:
            # No-op re-ingest of identical content (or an oracle-only change,
            # which per the compute-only firewall must never trigger a
            # version bump). Leave the current row untouched.
            return rows

        if valid_from < current.valid_from:
            # A fact whose validity precedes the current row's own start —
            # would require splitting an EARLIER interval we may not even
            # hold. See module docstring: not implemented, route to a person.
            raise RetroactiveCorrection(
                f"{object_kind} {anchor_id}: new valid_from {valid_from} precedes "
                f"current row's valid_from {current.valid_from}; interval splitting "
                "is not implemented — route to manual reconciliation."
            )

        if valid_from == current.valid_from:
            # A CORRECTION: the same engineering-validity period, but we now
            # know different content was true throughout it (e.g. a
            # re-ingest of the same revision with corrected attributes).
            # Only the transaction axis moves — we did not just learn the
            # fact stopped being valid, we learned we recorded it wrong.
            # The valid interval itself (valid_to) carries over unchanged.
            current.tx_to = tx_from
            current.superseded_by_delta = DeltaType.MODIFIED
            rows.append(
                GoldRow(
                    object_kind=object_kind,
                    anchor_id=anchor_id,
                    attrs=dict(attrs),
                    valid_from=valid_from,
                    valid_to=current.valid_to,
                    tx_from=tx_from,
                    tx_to=None,
                )
            )
            return rows

        # Ordinary forward supersession (valid_from > current.valid_from):
        # a later revision naturally takes over from here. Only the valid
        # axis closes — the OLD row remains a permanently-held, currently
        # trusted record of what was true during ITS interval; transaction
        # time is not retroactively rewritten just because time moved on
        # and something new arrived (that would make every past interval
        # look "corrected" on every subsequent revision, which it was not).
        current.valid_to = valid_from
        current.superseded_by_delta = DeltaType.MODIFIED
        rows.append(
            GoldRow(
                object_kind=object_kind,
                anchor_id=anchor_id,
                attrs=dict(attrs),
                valid_from=valid_from,
                valid_to=None,
                tx_from=tx_from,
                tx_to=None,
            )
        )
        return rows

    if delta is DeltaType.DELETED:
        current.valid_to = valid_from
        current.tx_to = tx_from
        current.superseded_by_delta = DeltaType.DELETED
        return rows

    raise AssertionError(f"unhandled delta type {delta!r}")


def current_truth(
    rows: Iterable[GoldRow],
    as_of_valid: Optional[date] = None,
    as_of_tx: Optional[datetime] = None,
) -> list[GoldRow]:
    """The 'transaction_time = now AND valid_time = latest' view
    (medallion_rdf_ido_strategy_mapping.md §4b) when both args are None;
    otherwise a point-in-time query on either or both axes — 'what did we
    believe as of transaction time T' (as_of_tx) vs 'what was true at Rev B'
    (as_of_valid).

    An axis left unspecified is UNCONSTRAINED, not defaulted to "latest" —
    except when BOTH are unspecified, which is the one case with a real
    default ("give me current truth"). Defaulting an unspecified axis to
    "latest/open" whenever the OTHER axis is pinned would wrongly exclude,
    e.g., a historical valid-time row from an as_of_tx-only query merely
    because a later revision has since closed its valid interval — the
    query "what rows existed in the system as of T" is about the
    transaction axis alone; it should not also silently demand that row be
    today's latest engineering fact.
    """
    both_unspecified = as_of_valid is None and as_of_tx is None
    out = []
    for r in rows:
        if as_of_valid is not None:
            valid_ok = r.valid_from <= as_of_valid and (r.valid_to is None or as_of_valid < r.valid_to)
        elif both_unspecified:
            valid_ok = r.valid_to is None
        else:
            valid_ok = True

        if as_of_tx is not None:
            tx_ok = r.tx_from <= as_of_tx and (r.tx_to is None or as_of_tx < r.tx_to)
        elif both_unspecified:
            tx_ok = r.tx_to is None
        else:
            tx_ok = True

        if valid_ok and tx_ok:
            out.append(r)
    return out


def diff_snapshots(
    current_rows: dict,
    previous_gold_rows: list[GoldRow],
    object_kind: str,
    valid_from: date,
    tx_from: datetime,
) -> list[GoldRow]:
    """Snapshot-diff fallback for when Silver Stage E (object-grain CDC) has
    not yet run. `current_rows` is {anchor_id: attrs} for this ingest.
    Produces New/Modified/Deleted classification by comparing against the
    previous Gold current rows for `object_kind`, then applies each delta.
    This is deliberately coarser than Stage E's anchor-hierarchy matching
    (silver_layer_spec.md §3.5) — it has no within-bucket member-pairing, so
    a delete+recreate of an unchanged item can misclassify as Delete+Add
    here. That is exactly the false-churn risk Stage E exists to avoid;
    this fallback trades that precision for being usable today, and the
    docstring above says so."""
    rows = list(previous_gold_rows)
    prev_current = {r.anchor_id: r for r in rows if r.object_kind == object_kind and r.is_current()}

    for anchor_id, attrs in current_rows.items():
        if anchor_id not in prev_current:
            apply_delta(rows, object_kind, anchor_id, DeltaType.NEW, attrs, valid_from, tx_from)
        else:
            apply_delta(rows, object_kind, anchor_id, DeltaType.MODIFIED, attrs, valid_from, tx_from)

    for anchor_id in prev_current:
        if anchor_id not in current_rows:
            apply_delta(rows, object_kind, anchor_id, DeltaType.DELETED, {}, valid_from, tx_from)

    return rows
