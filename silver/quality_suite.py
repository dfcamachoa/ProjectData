"""Silver Stage D — the expectation suite, expressed as data (silver_spec §3.4).

This is the *rules-as-data* surface the spec calls for: every data-quality check
is a declarative :class:`Expectation`, not bespoke code, so onboarding a project
is editing this list (or, for the reference-data-backed checks, pointing at that
project's ``Reference_Data.xlsx``) — never editing the evaluator.

The evaluator (:mod:`silver.quality`) reads this suite and emits the
``silver_quality`` ledger — the per-drawing / per-project **data-quality punch
list** the pre-commissioning engineer fixes at source in SmartPlant *before*
systemization runs.

Gate policy (silver_spec §3.4, decision #6) — *fail for bugs, not for data*:

    flag        record it, keep the row (the vast majority — a punch-list item)
    quarantine  record it, keep the row, mark quality_gate='quarantined'
    drop        the row was never emitted (informational ledger row only)
    fail        a STRUCTURAL / pipeline invariant broke — a code bug, never dirty
                data; the run aborts (only the two §5/§4 invariants use this)

Severity is orthogonal to the gate and drives the punch-list ordering:

    info   for-review, not necessarily a defect (e.g. missing insulation)
    warn   a real data gap the engineer should fix at source
    error  a structural-invariant breach (always paired with gate='fail')
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# gate constants
GATE_FLAG = "flag"
GATE_QUARANTINE = "quarantine"
GATE_DROP = "drop"
GATE_FAIL = "fail"

# severity constants
SEV_INFO = "info"
SEV_WARN = "warn"
SEV_ERROR = "error"

# expectation kinds understood by the evaluator
KIND_NOT_NULL = "not_null"            # column must be present/non-empty
KIND_ALL_NULL = "all_null"           # flag only when ALL `columns` are empty
KIND_IN_SET = "in_set"               # column value must be in a reference set
KIND_MATCH_REGEX = "match_regex"     # column value must match a reference pattern
KIND_PREFIX_CHAIN = "prefix_chain"   # cols[i] must start-with cols[i+1] (child..parent)
KIND_UNIQUE_ANCHOR = "unique_anchor"  # anchor_column must map to a single id_column
KIND_ROUNDTRIP = "roundtrip"         # compose(decode(tag)) == tag (best-effort)
KIND_ORPHAN_NODE = "orphan_node"     # a component present in zero connections
KIND_DERIVED_FLAGGED = "derived_flagged"   # INVARIANT: every edge carries derived (bool)
KIND_ORACLE_CONFINED = "oracle_confined"   # INVARIANT: oracle columns only on segments


@dataclass(frozen=True)
class Expectation:
    """One declarative data-quality rule (silver_spec §3.4)."""

    id: str                      # stable flag id, e.g. "segment_missing_fluid"
    table: str                   # which silver_* table this runs over
    kind: str                    # one of the KIND_* constants
    gate: str                    # flag | quarantine | drop | fail
    severity: str                # info | warn | error
    detail: str                  # human template, .format(**row)-style
    source: str                  # spec / reference-data citation

    # kind-specific parameters (only the relevant ones are set) --------------
    column: Optional[str] = None            # not_null / in_set / match_regex / roundtrip
    ref_set: Optional[str] = None           # QualityRefData attr name (a set): in_set
    pattern_ref: Optional[str] = None       # QualityRefData attr name (a regex): match_regex
    anchor_column: Optional[str] = None     # unique_anchor: the business anchor
    # unique_anchor: columns that SCOPE the anchor's uniqueness (the anchor is
    # unique only within these). Empty => global. For seg_tag the CDC anchor is
    # (drawing_number, seg_tag), so scope is ("drawing_number",) (silver_spec §3.5).
    scope_columns: Tuple[str, ...] = ()
    columns: Tuple[str, ...] = ()           # prefix_chain: child..parent order
    # optional single row filter: (column, op, value), op in {eq, neq, contains}
    where: Optional[Tuple[str, str, str]] = None
    # the row's identity column for the ledger (defaults per table if None)
    id_column: Optional[str] = None
    # if True this expectation is skipped (with an info note) when its backing
    # reference data is unavailable, rather than failing
    needs_refdata: bool = False


# id column of each object table, used for ledger object_id when id_column unset
TABLE_ID = {
    "silver_components": "component_id",
    "silver_segments": "segment_id",
    "silver_connections": "connection_id",
    "silver_equipment": "equipment_id",
}
TABLE_KIND = {
    "silver_components": "component",
    "silver_segments": "segment",
    "silver_connections": "connection",
    "silver_equipment": "equipment",
}


def default_suite() -> Tuple[Expectation, ...]:
    """The Phase-1 Stage-D suite (silver_spec §3.4). Covers every check whose
    inputs exist after Stage A+B+persist; the assembly-dependent checks
    (unmatched-OPC open boundary) arrive with Stage C."""
    return (
        # --- structural invariants — the ONLY hard fails (§4, §5) ------------
        Expectation(
            id="derived_flag_missing",
            table="silver_connections", kind=KIND_DERIVED_FLAGGED,
            gate=GATE_FAIL, severity=SEV_ERROR, column="derived",
            detail="connection {connection_id}: reified edge has no `derived` flag "
                   "(the RDF layer would assert inferred topology as source truth)",
            source="silver_spec §4 / risk #5",
        ),
        Expectation(
            id="oracle_leak",
            table="silver_segments", kind=KIND_ORACLE_CONFINED,
            gate=GATE_FAIL, severity=SEV_ERROR,
            detail="oracle column {column} found outside silver_segments — a "
                   "compute layer can now read the turnover answer key (§5); "
                   "the 97% validation would go circular",
            source="silver_spec §5 / risk #3",
        ),

        # --- segment attribute completeness (a punch-list item, §3.4) --------
        Expectation(
            id="segment_missing_fluid",
            table="silver_segments", kind=KIND_NOT_NULL, column="fluid",
            gate=GATE_FLAG, severity=SEV_WARN,
            detail="segment {seg_tag}: no fluid code (OperFluidCode missing)",
            source="MD §2.3 / silver_spec §3.4",
        ),
        Expectation(
            id="segment_missing_piping_class",
            table="silver_segments", kind=KIND_NOT_NULL, column="piping_materials_class",
            gate=GATE_FLAG, severity=SEV_WARN,
            detail="segment {seg_tag}: no piping materials class",
            source="MD §2.3 / silver_spec §3.4",
        ),
        Expectation(
            id="segment_missing_diameter",
            table="silver_segments", kind=KIND_NOT_NULL, column="diameter",
            gate=GATE_FLAG, severity=SEV_WARN,
            detail="segment {seg_tag}: no nominal diameter",
            source="MD §2.3 / silver_spec §3.4",
        ),
        # insulation: for-review, NOT a defect (uninsulated lines are legitimate).
        # The insulation PURPOSE is the seg_tag's last token (e.g. -H heat, -N none,
        # -P personnel), read from the same InsulPurpose attribute as insul_purpose —
        # so a line is only "insulation-absent" when purpose, type AND thickness are
        # ALL blank (a bare/degenerate tag), not merely when insul_type is null.
        Expectation(
            id="segment_insulation_absent",
            table="silver_segments", kind=KIND_ALL_NULL,
            columns=("insul_purpose", "insul_type", "insul_thick"),
            gate=GATE_FLAG, severity=SEV_INFO,
            detail="segment {seg_tag}: no insulation information at all — purpose, "
                   "type and thickness are all blank (the purpose is normally the "
                   "seg_tag suffix, e.g. -H); review, not a defect",
            source="MD §4.2 / silver_spec §3.4",
        ),

        # insulation VALUE check: when a purpose IS present, it must be one of the
        # project's approved insulation codes (rules-as-data, §7). Absence is the
        # separate `segment_insulation_absent` review above; this catches a wrong
        # or mistyped insulation code.
        Expectation(
            id="segment_unknown_insulation",
            table="silver_segments", kind=KIND_IN_SET, column="insul_purpose",
            ref_set="insulation_codes", needs_refdata=True,
            gate=GATE_FLAG, severity=SEV_WARN,
            detail="segment {seg_tag}: insulation code '{insul_purpose}' is not in "
                   "the project approved-insulation list",
            source="refdata Insulation / MD §4.2 / silver_spec §3.4",
        ),

        # --- reference-data-backed value checks (rules-as-data, §7) ----------
        Expectation(
            id="segment_unknown_fluid",
            table="silver_segments", kind=KIND_IN_SET, column="fluid",
            ref_set="fluid_codes", needs_refdata=True,
            gate=GATE_FLAG, severity=SEV_WARN,
            detail="segment {seg_tag}: fluid code '{fluid}' is not in the project "
                   "Fluid catalogue",
            source="refdata Fluid / ALG §3.3 / silver_spec §3.4",
        ),
        Expectation(
            id="segment_unknown_unit",
            table="silver_segments", kind=KIND_IN_SET, column="unit",
            ref_set="unit_codes", needs_refdata=True,
            gate=GATE_FLAG, severity=SEV_WARN,
            detail="segment {seg_tag}: unit '{unit}' is not in the project Unit / "
                   "UnitSUP catalogue",
            source="refdata Unit / MD §3.6 / silver_spec §3.4",
        ),
        Expectation(
            id="equipment_tag_noncompliant",
            table="silver_equipment", kind=KIND_MATCH_REGEX, column="tag",
            pattern_ref="equipment_pattern", needs_refdata=True,
            gate=GATE_FLAG, severity=SEV_WARN,
            detail="equipment tag '{tag}' does not match the project equipment "
                   "naming pattern",
            source="refdata Naming / MD §2.9 / silver_spec §3.4",
        ),
        Expectation(
            id="instrument_tag_noncompliant",
            table="silver_components", kind=KIND_MATCH_REGEX, column="tag",
            pattern_ref="instrument_pattern", needs_refdata=True,
            where=("kind", "contains", "Instrument"),
            gate=GATE_FLAG, severity=SEV_WARN,
            detail="instrument tag '{tag}' does not match the project instrument "
                   "naming pattern",
            source="refdata Naming / MD §2.5 / silver_spec §3.4",
        ),

        # --- structural / cross-column data checks ---------------------------
        Expectation(
            id="seg_tag_anchor_collision",
            table="silver_segments", kind=KIND_UNIQUE_ANCHOR,
            anchor_column="seg_tag", scope_columns=("drawing_number",),
            gate=GATE_FLAG, severity=SEV_INFO,
            detail="on drawing {drawing_number}, seg_tag '{seg_tag}' is composed by "
                   "{n} distinct segments — the (drawing, seg_tag) CDC anchor needs "
                   "a disambiguator (§3.5); review, not a source defect",
            source="silver_spec §3.5 / §4",
        ),
        Expectation(
            id="prefix_integrity",
            table="silver_segments", kind=KIND_PREFIX_CHAIN,
            columns=("seg_tag", "subline_tag", "pns_tag"),
            gate=GATE_FLAG, severity=SEV_WARN,
            detail="prefix chain broken: seg_tag '{seg_tag}' / subline "
                   "'{subline_tag}' / pns '{pns_tag}' are not nested",
            source="MD §2.2a/§2.3 §4.2 / silver_spec §3.4",
        ),

        # --- topology sanity on the RECONSTRUCTED graph (never the raw, §2) --
        Expectation(
            id="orphan_component",
            table="silver_components", kind=KIND_ORPHAN_NODE,
            gate=GATE_FLAG, severity=SEV_INFO,
            detail="component {component_id} ({component_class}) participates in no "
                   "connection — an orphan on the reconstructed graph",
            source="reconstructed und / MD §4.2 / silver_spec §3.4",
        ),
    )
