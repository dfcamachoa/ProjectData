"""Silver Stage D — the pure, Spark-free quality evaluator (silver_spec §3.4).

Mirrors :mod:`silver.reconstruct`: this is the testable core. It takes the four
Silver table row-sets (as lists of dicts), a declarative expectation suite
(:mod:`silver.quality_suite`) and a :class:`QualityRefData` bundle, and returns:

    * ledger      — the ``silver_quality`` rows (the engineer's punch list)
    * gates       — {(table, object_id): quality_gate} rollup for the object rows
    * hard_failures — structural-invariant breaches (gate='fail'); a code bug,
                      so the Spark job raises on any (silver_spec §3.4, §4, §5)
    * warnings    — run-level notes (e.g. a mass-failure config smell)

The Spark job (:mod:`silver.quality_job`) collects the needed columns, calls
this, writes the ledger, and denormalises the gate rollup back onto each object
table. Keeping the logic here means the whole gate is unit-tested without Spark.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .quality_suite import (
    Expectation,
    GATE_FAIL,
    GATE_QUARANTINE,
    KIND_ALL_NULL,
    KIND_DERIVED_FLAGGED,
    KIND_IN_SET,
    KIND_MATCH_REGEX,
    KIND_NOT_NULL,
    KIND_ORACLE_CONFINED,
    KIND_ORPHAN_NODE,
    KIND_PREFIX_CHAIN,
    KIND_ROUNDTRIP,
    KIND_UNCOMPOSABLE_TAG,
    KIND_LINE_ATTR_INCONSISTENT,
    KIND_UNIQUE_ANCHOR,
    SEV_INFO,
    TABLE_ID,
    TABLE_KIND,
    default_suite,
)

# columns that must live ONLY on silver_segments (the quarantined oracle, §5)
ORACLE_COLUMNS = ("src_turnover", "src_subsystem")

# quality_gate rollup ranks (higher wins)
_GATE_RANK = {"clean": 0, "flag": 1, "flagged": 1, "quarantine": 2, "quarantined": 2}
_GATE_TO_ROLLUP = {"flag": "flagged", "quarantine": "quarantined"}


@dataclass
class QualityRefData:
    """Reference data the reference-backed expectations read (rules-as-data, §7).
    Any field left ``None`` makes its expectation *skip* (with an info note in the
    summary) rather than fail — an un-onboarded project must not fail the run."""
    fluid_codes: Optional[frozenset] = None
    unit_codes: Optional[frozenset] = None
    insulation_codes: Optional[frozenset] = None
    equipment_pattern: Optional[str] = None
    instrument_pattern: Optional[str] = None
    convention: object = None            # TaggingConvention, for the round-trip check

    def get_set(self, name: Optional[str]) -> Optional[frozenset]:
        return getattr(self, name) if name else None

    def get_pattern(self, name: Optional[str]) -> Optional[str]:
        return getattr(self, name) if name else None


@dataclass
class QualityResult:
    ledger: List[dict] = field(default_factory=list)
    gates: Dict[Tuple[str, str], str] = field(default_factory=dict)
    hard_failures: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)     # expectation ids skipped
    summary: Dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
#  small helpers                                                              #
# --------------------------------------------------------------------------- #
def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def is_uncomposable_seg_tag(v) -> bool:
    """True when a ``seg_tag`` is NOT a real composable line tag — a connector /
    off-page pseudo-tag or a placeholder description rather than the reconstructed
    ``[dia-]fluid-lineCore[-class][-insul]`` line number (silver_spec §3.5). Real
    Project-B examples that must flag: ``Conn to process/supply-``, ``Pneumatic-``,
    ``PG-Utility, Secondary-``. The three symptoms, any of which disqualifies it:

      * empty / absent (can't anchor a line at all);
      * contains whitespace or a description separator (real tags are code strings);
      * ends with a separator ``- , /`` (composition left a trailing empty field);
      * has no numeric line core (no run of >=3 digits: no unit+sequence).

    Exposed as a shared predicate so line-grain CDC can exclude exactly the same
    rows Stage D quarantines — one rule, two consumers.
    """
    s = "" if v is None else str(v).strip()
    if not s:
        return True
    if any(ch.isspace() for ch in s):
        return True
    if s.endswith(("-", ",", "/")):
        return True
    if re.search(r"\d{3,}", s) is None:
        return True
    return False


def _passes_where(row: dict, where: Optional[Tuple[str, str, str]]) -> bool:
    if not where:
        return True
    col, op, val = where
    cur = row.get(col)
    s = "" if cur is None else str(cur)
    if op == "eq":
        return s == val
    if op == "neq":
        return s != val
    if op == "contains":
        return val in s
    return True


def _fmt(template: str, row: dict, **extra) -> str:
    """Safe .format — missing keys render as '?' rather than raising."""
    class _D(dict):
        def __missing__(self, k):  # noqa: D401
            return "?"
    data = _D(row)
    data.update({k: v for k, v in extra.items()})
    try:
        return template.format_map(data)
    except Exception:
        return template


def _lineage(row: dict) -> dict:
    return {
        "drawing_number": row.get("drawing_number"),
        "project_code": row.get("project_code"),
        "source_format": row.get("source_format"),
    }


def _ledger_row(exp: Expectation, *, object_id, object_kind, detail, row=None,
                run_ts=None) -> dict:
    base = _lineage(row or {})
    base.update({
        "object_id": object_id,
        "object_kind": object_kind,
        "flag": exp.id,
        "severity": exp.severity,
        "gate": exp.gate,
        "stage": "D",
        "detail": detail,
        "transaction_ts": run_ts,
    })
    return base


# --------------------------------------------------------------------------- #
#  per-kind evaluation                                                         #
# --------------------------------------------------------------------------- #
def _eval_rowwise(exp, rows, refdata, run_ts, predicate_fails):
    """Shared driver for the row-at-a-time kinds: emit a ledger row wherever
    ``predicate_fails(row)`` is truthy, honouring the optional where-filter."""
    id_col = exp.id_column or TABLE_ID.get(exp.table, "object_id")
    okind = TABLE_KIND.get(exp.table, exp.table)
    hits, evaluated = [], 0
    for row in rows:
        if not _passes_where(row, exp.where):
            continue
        evaluated += 1
        if predicate_fails(row):
            hits.append(_ledger_row(
                exp, object_id=row.get(id_col), object_kind=okind,
                detail=_fmt(exp.detail, row), row=row, run_ts=run_ts))
    return hits, evaluated


def _kind_not_null(exp, rows, refdata, run_ts):
    return _eval_rowwise(exp, rows, refdata, run_ts,
                         lambda r: _empty(r.get(exp.column)))


def _kind_all_null(exp, rows, refdata, run_ts):
    """Flag a row only when EVERY column in ``exp.columns`` is empty (e.g. a
    segment with no insulation signal at all — purpose, type and thickness all
    blank). Distinct from not_null, which flags on a single missing column."""
    cols = exp.columns
    return _eval_rowwise(exp, rows, refdata, run_ts,
                         lambda r: all(_empty(r.get(c)) for c in cols))


def _kind_uncomposable_tag(exp, rows, refdata, run_ts):
    """Flag rows whose ``exp.column`` is not a composable line tag (§3.5)."""
    return _eval_rowwise(exp, rows, refdata, run_ts,
                         lambda r: is_uncomposable_seg_tag(r.get(exp.column)))


def _kind_in_set(exp, rows, refdata, run_ts):
    ref = refdata.get_set(exp.ref_set)
    if ref is None:
        return None, 0                       # skip: reference data unavailable
    return _eval_rowwise(
        exp, rows, refdata, run_ts,
        lambda r: (not _empty(r.get(exp.column))) and str(r.get(exp.column)) not in ref)


def _kind_match_regex(exp, rows, refdata, run_ts):
    pat = refdata.get_pattern(exp.pattern_ref)
    if not pat:
        return None, 0                       # skip: no pattern configured
    rx = re.compile(pat)
    return _eval_rowwise(
        exp, rows, refdata, run_ts,
        lambda r: (not _empty(r.get(exp.column))) and rx.fullmatch(str(r.get(exp.column))) is None)


def _kind_prefix_chain(exp, rows, refdata, run_ts):
    """Tag nesting (`cols` ordered child..parent): each parent tag must be
    *contained in* its child, NOT a literal left-anchored prefix of it. The
    composed segment business tag is decorated with a diameter prefix and
    class/insulation suffixes (e.g. seg `36"-PG-1417205-D24P1HD-H` embeds subline
    `PG-1417205`), so a strict `startswith` false-flags every real segment.
    Containment still catches a genuine mismatch — a segment whose fluid/unit
    identity disagrees with its subline / pipeline-system (MD §2.2a/§2.3, §4.2)."""
    cols = exp.columns

    def fails(r):
        vals = [r.get(c) for c in cols]
        if any(_empty(v) for v in vals):     # completeness handled elsewhere
            return False
        return any(str(vals[i + 1]) not in str(vals[i])
                   for i in range(len(vals) - 1))

    return _eval_rowwise(exp, rows, refdata, run_ts, fails)


def _kind_roundtrip(exp, rows, refdata, run_ts):
    conv = refdata.convention
    if conv is None:
        return None, 0
    id_col = exp.id_column or TABLE_ID.get(exp.table, "object_id")
    okind = TABLE_KIND.get(exp.table, exp.table)
    hits, evaluated = [], 0
    for row in rows:
        if not _passes_where(row, exp.where):
            continue
        tag = row.get(exp.column)
        if _empty(tag):
            continue
        evaluated += 1
        try:
            ident = conv.decode(tag, row.get("fluid"), None)
            ok = ident is not None
        except Exception:
            ok = False
        if not ok:
            hits.append(_ledger_row(
                exp, object_id=row.get(id_col), object_kind=okind,
                detail=_fmt(exp.detail, row), row=row, run_ts=run_ts))
    return hits, evaluated


def _kind_unique_anchor(exp, rows, refdata, run_ts):
    """Anchor collision (§3.5): within the anchor's SCOPE (``scope_columns``, e.g.
    the drawing), one anchor value composed by >1 distinct object. Scoping by
    drawing is what keeps the *same line drawn on many sheets* from reading as a
    collision — only a genuine within-scope duplicate is flagged. One ledger row
    per colliding (scope, anchor); every participating object is gated."""
    id_col = exp.id_column or TABLE_ID.get(exp.table, "object_id")
    okind = TABLE_KIND.get(exp.table, exp.table)
    scope = exp.scope_columns or ()
    groups: Dict[tuple, List[dict]] = {}
    for row in rows:
        if not _passes_where(row, exp.where):
            continue
        anchor = row.get(exp.anchor_column)
        if _empty(anchor):
            continue
        # a seg_tag anchor that isn't a real line (connector/placeholder) is
        # reported once by `seg_tag_uncomposable`; don't double-report it here
        if exp.anchor_column == "seg_tag" and is_uncomposable_seg_tag(anchor):
            continue
        key = tuple(row.get(c) for c in scope) + (str(anchor),)
        groups.setdefault(key, []).append(row)

    hits, evaluated = [], len(rows)
    extra_gates: List[Tuple[str, str]] = []      # (table, object_id) to gate too
    for key, members in groups.items():
        ids = {m.get(id_col) for m in members}
        if len(ids) > 1:
            rep = members[0]
            hits.append(_ledger_row(
                exp, object_id=str(rep.get(exp.anchor_column)), object_kind=okind,
                detail=_fmt(exp.detail, rep, n=len(ids)), row=rep, run_ts=run_ts))
            for m in members:                    # gate every colliding object
                extra_gates.append((exp.table, m.get(id_col)))
    return hits, evaluated, extra_gates


def _kind_line_attr_inconsistent(exp, rows, refdata, run_ts):
    """Within-line consistency (§3.5): group the PipingNetworkSegment pieces by the
    CDC line anchor ``(scope_columns, seg_tag)`` — the same grouping line-grain CDC
    aggregates on — and flag any line whose pieces carry more than one distinct
    non-null value for a line-defining attribute (``columns``). This is the check
    that keeps set-reduction in the aggregation from *hiding* a genuine spec break:
    one ledger row per inconsistent line, every participating piece gated."""
    id_col = exp.id_column or TABLE_ID.get(exp.table, "object_id")
    okind = TABLE_KIND.get(exp.table, exp.table)
    scope = exp.scope_columns or ()
    groups: Dict[tuple, List[dict]] = {}
    for row in rows:
        if not _passes_where(row, exp.where):
            continue
        anchor = row.get(exp.anchor_column)
        if _empty(anchor) or is_uncomposable_seg_tag(anchor):
            continue                              # un-composable is reported elsewhere
        key = tuple(row.get(c) for c in scope) + (str(anchor),)
        groups.setdefault(key, []).append(row)

    hits, evaluated = [], len(rows)
    extra_gates: List[Tuple[str, str]] = []
    for members in groups.values():
        bad = [c for c in exp.columns
               if len({str(m.get(c)) for m in members if not _empty(m.get(c))}) > 1]
        if bad:
            rep = members[0]
            hits.append(_ledger_row(
                exp, object_id=str(rep.get(exp.anchor_column)), object_kind=okind,
                detail=_fmt(exp.detail, rep, attrs=", ".join(bad)),
                row=rep, run_ts=run_ts))
            for m in members:
                extra_gates.append((exp.table, m.get(id_col)))
    return hits, evaluated, extra_gates


def _kind_orphan_node(exp, comp_rows, conn_rows, run_ts):
    """A component present in zero connections, on the RECONSTRUCTED graph (never
    the raw <Connection> set — that would false-alarm every inline valve, §2)."""
    id_col = exp.id_column or TABLE_ID.get(exp.table, "component_id")
    endpoints = set()
    for c in conn_rows:
        endpoints.add(c.get("from_id"))
        endpoints.add(c.get("to_id"))
    hits, evaluated = [], 0
    for row in comp_rows:
        if not _passes_where(row, exp.where):
            continue
        if (row.get("kind") or "") == "Nozzle":  # nozzles wire via equipment
            continue
        evaluated += 1
        if row.get(id_col) not in endpoints:
            hits.append(_ledger_row(
                exp, object_id=row.get(id_col), object_kind="component",
                detail=_fmt(exp.detail, row), row=row, run_ts=run_ts))
    return hits, evaluated


def _kind_derived_flagged(exp, conn_rows, run_ts):
    """INVARIANT (§4): every reified edge must carry a boolean `derived`. A NULL
    means a pipeline bug, not dirty data → hard fail."""
    hits = []
    for row in conn_rows:
        if not isinstance(row.get(exp.column), bool):
            hits.append(_ledger_row(
                exp, object_id=row.get("connection_id"), object_kind="connection",
                detail=_fmt(exp.detail, row), row=row, run_ts=run_ts))
    return hits, len(conn_rows)


def _kind_oracle_confined(exp, tables, run_ts):
    """INVARIANT (§5): the oracle columns may appear ONLY on silver_segments. If a
    compute table carries a populated oracle value, a rule could read the answer
    key and the 97% validation goes circular → hard fail."""
    hits = []
    for tname, rows in tables.items():
        if tname == "silver_segments":
            continue
        for row in rows:
            for oc in ORACLE_COLUMNS:
                if oc in row and not _empty(row.get(oc)):
                    hits.append(_ledger_row(
                        exp, object_id=row.get(TABLE_ID.get(tname, "object_id")),
                        object_kind=TABLE_KIND.get(tname, tname),
                        detail=_fmt(exp.detail, row, column=oc), row=row, run_ts=run_ts))
                    break
    return hits, 1


# --------------------------------------------------------------------------- #
#  the evaluator                                                              #
# --------------------------------------------------------------------------- #
def evaluate(
    tables: Dict[str, Sequence[dict]],
    suite: Optional[Sequence[Expectation]] = None,
    refdata: Optional[QualityRefData] = None,
    *,
    run_ts=None,
    mass_fail_threshold: float = 0.5,
    min_rows_for_mass_fail: int = 20,
) -> QualityResult:
    """Run the suite over the Silver row-sets and produce the quality result.

    ``tables`` maps ``silver_components`` / ``silver_segments`` /
    ``silver_connections`` / ``silver_equipment`` to lists of row dicts.
    """
    suite = list(suite if suite is not None else default_suite())
    refdata = refdata or QualityRefData()
    res = QualityResult()
    conn_rows = list(tables.get("silver_connections", []))
    comp_rows = list(tables.get("silver_components", []))

    def _apply_gates(rows_with_gate):
        for lr in rows_with_gate:
            key = (_TABLE_FOR_KIND.get(lr["object_kind"]), lr["object_id"])
            if key[0] is None or key[1] is None:
                continue
            rollup = _GATE_TO_ROLLUP.get(lr["gate"])
            if not rollup:
                continue
            cur = res.gates.get(key, "clean")
            if _GATE_RANK[rollup] > _GATE_RANK.get(cur, 0):
                res.gates[key] = rollup

    for exp in suite:
        rows = list(tables.get(exp.table, []))
        hits = None
        evaluated = 0
        extra_gates: List[Tuple[str, str]] = []

        if exp.kind == KIND_NOT_NULL:
            hits, evaluated = _kind_not_null(exp, rows, refdata, run_ts)
        elif exp.kind == KIND_ALL_NULL:
            hits, evaluated = _kind_all_null(exp, rows, refdata, run_ts)
        elif exp.kind == KIND_UNCOMPOSABLE_TAG:
            hits, evaluated = _kind_uncomposable_tag(exp, rows, refdata, run_ts)
        elif exp.kind == KIND_IN_SET:
            hits, evaluated = _kind_in_set(exp, rows, refdata, run_ts)
        elif exp.kind == KIND_MATCH_REGEX:
            hits, evaluated = _kind_match_regex(exp, rows, refdata, run_ts)
        elif exp.kind == KIND_PREFIX_CHAIN:
            hits, evaluated = _kind_prefix_chain(exp, rows, refdata, run_ts)
        elif exp.kind == KIND_ROUNDTRIP:
            hits, evaluated = _kind_roundtrip(exp, rows, refdata, run_ts)
        elif exp.kind == KIND_UNIQUE_ANCHOR:
            hits, evaluated, extra_gates = _kind_unique_anchor(exp, rows, refdata, run_ts)
        elif exp.kind == KIND_LINE_ATTR_INCONSISTENT:
            hits, evaluated, extra_gates = _kind_line_attr_inconsistent(
                exp, rows, refdata, run_ts)
        elif exp.kind == KIND_ORPHAN_NODE:
            hits, evaluated = _kind_orphan_node(exp, comp_rows, conn_rows, run_ts)
        elif exp.kind == KIND_DERIVED_FLAGGED:
            hits, evaluated = _kind_derived_flagged(exp, conn_rows, run_ts)
        elif exp.kind == KIND_ORACLE_CONFINED:
            hits, evaluated = _kind_oracle_confined(exp, tables, run_ts)
        else:                                   # unknown kind — skip, note it
            res.skipped.append(f"{exp.id} (unknown kind {exp.kind})")
            continue

        if hits is None:                        # skipped for missing refdata
            res.skipped.append(f"{exp.id} (reference data unavailable)")
            continue

        res.ledger.extend(hits)

        if exp.gate == GATE_FAIL:
            res.hard_failures.extend(hits)
        else:
            _apply_gates(hits)
            # anchor-collision gates every participating object, not just the rep
            for tbl, oid in extra_gates:
                key = (tbl, oid)
                rollup = _GATE_TO_ROLLUP.get(exp.gate)
                if oid and rollup and _GATE_RANK[rollup] > _GATE_RANK.get(res.gates.get(key, "clean"), 0):
                    res.gates[key] = rollup

        # run-level mass-failure warning (config smell, not an abort — §3.4).
        # Only for normally-rare checks (warn/error): an `info` check like
        # insulation-absent is *expected* to be common on real plants, so a high
        # rate there is not a mis-config signal and must not raise a warning.
        if (exp.gate != GATE_FAIL and exp.severity != SEV_INFO
                and evaluated >= min_rows_for_mass_fail
                and len(hits) / evaluated > mass_fail_threshold):
            res.warnings.append(
                f"{exp.id}: {len(hits)}/{evaluated} rows failed "
                f"({100*len(hits)//evaluated}%) — check the project reference data "
                f"/ adapter (possible mis-config), partial result still produced")

    res.summary = {
        "expectations_run": len(suite) - len(res.skipped),
        "expectations_skipped": len(res.skipped),
        "ledger_rows": len(res.ledger),
        "hard_failures": len(res.hard_failures),
        "objects_flagged": sum(1 for g in res.gates.values() if g == "flagged"),
        "objects_quarantined": sum(1 for g in res.gates.values() if g == "quarantined"),
        "run_warnings": len(res.warnings),
    }
    return res


# object_kind -> the table it rolls up onto (for gate denormalisation)
_TABLE_FOR_KIND = {v: k for k, v in TABLE_KIND.items()}
