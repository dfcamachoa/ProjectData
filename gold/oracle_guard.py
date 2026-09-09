"""The oracle-quarantine structural invariant, enforced at the RDF layer.

silver_layer_spec.md §5: "The walk reads only connectivity, class, and fluid —
never the source Z_TurnOverSystemNumber / SubsystemNo." Silver enforces this
with a hard-fail GX invariant (`oracle_confined`). This module is that same
invariant, restated for Gold's RDF projection: `graph:oracle` is the ONLY
named graph the oracle predicates (`pidsys:srcTurnoverSystem`,
`pidsys:srcSubsystem`) may appear in. A rule engine — Jena or
`rules_reference.py` — that reads `graph:oracle` at all is a bug, not a
config choice; this guard is what makes that a hard failure instead of a
silent circularity in the ~97% agreement figure.
"""
from __future__ import annotations

from . import vocab as v
from .rdf_model import Dataset


class OracleLeakage(Exception):
    """Raised when an oracle predicate is asserted outside graph:oracle."""


def assert_oracle_confined(ds: Dataset) -> None:
    # q.p / q.g are real rdflib URIRefs (str subclasses) as of the 2026-09-09
    # rdflib graduation — compare/format with str(), not the old hand-rolled
    # term's `.value` attribute, which rdflib's URIRef doesn't have.
    for q in ds:
        if str(q.p) in v.ORACLE_PREDICATES and str(q.g) != v.GRAPH_ORACLE:
            raise OracleLeakage(
                f"oracle predicate {q.p} asserted in graph {q.g} "
                f"(subject {q.s}) — must be confined to {v.GRAPH_ORACLE}"
            )


def assert_rule_engine_did_not_read_oracle(graphs_read: set) -> None:
    """Call this from any rule-engine entry point (rules_reference.py
    functions all thread a `readable_graphs` set for exactly this check)."""
    if v.GRAPH_ORACLE in graphs_read:
        raise OracleLeakage(
            "a classification/partition rule attempted to read graph:oracle — "
            "the oracle is a validation answer key only, never a rule input "
            "(silver_layer_spec.md §5; medallion_rdf_ido_strategy_mapping.md §9 risk #6)"
        )
