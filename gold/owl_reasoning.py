"""Real OWL/RDFS entailment over the Gold ontology skeleton, via `owlrl` --
the first concrete step of this project's own next phase ("explore the use
of an owl Semantic approach... where business rules must be applied to
industrial plant data based on base ontologies like IDO").

**Deliberately narrow scope.** This module cross-validates the ONE thing
real OWL/RDFS entailment is actually the right tool for here: class-hierarchy
subsumption. `rdf_mapper.declare_ontology_skeleton` asserts every domain
component class as `rdfs:subClassOf` an IDO foundational class
(`rdf_mapper.py`'s correctness note #1 — "never a bare, unconfirmed
`ido:FunctionalObject`"), and every instance is typed under its domain class
only (e.g. a valve is asserted `rdf:type pidsys:GateValve`, never also
`rdf:type ido:PhysicalObject` directly). The project's own rules
(`rules_reference.py`, `oracle_guard.py`) and this package's tests all
*assume* that any consumer asking "is this a physical object" gets the
right answer for free from `rdfs:subClassOf` transitivity, without Gold
having to assert or query the transitive closure itself. This module is
where that assumption gets checked against a real, standards-conformant
reasoner instead of staying an assumption.

**What this module deliberately does NOT do:** re-express the fluid/flow
business rules (`rules_reference.py` — flare/consumer/relief-attribution
guards, boundary roles, fluid classification) as OWL axioms or SWRL rules.
Those are node-local, directional, and data-driven (a per-connection
`flow_sense` read, not a class-membership inference) in a way that doesn't
map onto OWL-RL's entailment rules at all — which is exactly why the
project already assigned them to Jena's *generic rule* engine
(`jena_rules/classification.rules`) rather than to OWL/RDFS semantics
(medallion_rdf_ido_strategy_mapping.md §6). This module is where that
boundary sits, concretely: OWL/owlrl answers "is this instance really an
`ido:PhysicalObject`" (a real entailment question); it is not asked to
answer "should this valve be skipped as a consumer" (a business rule, not
an entailment — that stays `rules_reference.py` / Jena's territory).

Requires `rdflib` + `owlrl`, same as `rdf_model.py`'s 2026-09-09 graduation
note — unexecuted in this sandbox (neither is installable here; PyPI is not
on this org's egress allowlist). Confirm with
`python3 -m unittest tests.test_owl_reasoning -v` once both are installed
in your environment.
"""
from __future__ import annotations

from typing import Set

import owlrl
import rdflib

from . import vocab as v
from .rdf_model import Dataset

RDF_TYPE = rdflib.RDF.type
RDFS_SUBCLASSOF = rdflib.RDFS.subClassOf


def masterdata_graph(ds: Dataset) -> "rdflib.Graph":
    """The masterdata graph alone, copied out as a plain `rdflib.Graph` --
    owlrl's `DeductiveClosure` reasons over one graph, not a named-graph
    `Dataset`. Deliberately masterdata only: `graph:oracle` must never reach
    a rule engine at all (`oracle_guard.assert_rule_engine_did_not_read_oracle`),
    and `graph:refdata` / `graph:results` aren't needed for a class-hierarchy
    check."""
    g = rdflib.Graph()
    for q in ds.triples(graph=v.GRAPH_MASTERDATA):
        g.add((q.s, q.p, q.o))
    return g


def run_owl_rl_closure(graph: "rdflib.Graph") -> "rdflib.Graph":
    """Materialises the OWL-RL entailment closure IN PLACE (owlrl's
    `DeductiveClosure.expand` mutates the graph it's given, adding every
    inferred triple) and returns the same graph for chaining. Callers that
    need the pre-reasoning graph too (as `cross_check_physical_object_closure`
    does) must pass a copy, not the original."""
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(graph)
    return graph


def instances_of(graph: "rdflib.Graph", cls) -> "Set[rdflib.URIRef]":
    cls_uri = cls if isinstance(cls, rdflib.URIRef) else rdflib.URIRef(cls)
    return set(graph.subjects(RDF_TYPE, cls_uri))


def _asserted_subclass_closure(graph: "rdflib.Graph", root: "rdflib.URIRef") -> "Set[rdflib.URIRef]":
    """`root` plus every class that is (transitively) `rdfs:subClassOf` it,
    walked directly over the ASSERTED triples -- no reasoning involved. This
    is the independent, pure-Python "expected answer" this module
    cross-checks owlrl's real OWL-RL entailment against; if the two
    disagree, this walk is the one to doubt first (it's a plain BFS, no
    RDFS semantics beyond the one edge it follows), but a real disagreement
    still means one of them is wrong, not that it can be shrugged off."""
    closure = {root}
    frontier = {root}
    while frontier:
        cls = frontier.pop()
        for sub in graph.subjects(RDFS_SUBCLASSOF, cls):
            if sub not in closure:
                closure.add(sub)
                frontier.add(sub)
    return closure


def cross_check_physical_object_closure(ds: Dataset) -> dict:
    """The concrete cross-validation described in the module docstring.

    Computes the "expected" set of physical-object instances two ways and
    compares them: (1) `_asserted_subclass_closure` walks the asserted
    `rdfs:subClassOf` edges by hand to find every class rooted at
    `ido:PhysicalObject`, then collects every instance typed under one of
    those classes -- this is Gold's own assumption, made explicit and
    computable without a reasoner; (2) `run_owl_rl_closure` runs owlrl's
    real OWL-RL entailment on a COPY of the graph and reads off which
    instances it independently concludes are `ido:PhysicalObject`. Also
    confirms `rdf_mapper.py`'s correctness note #1 directly: no instance
    should be asserted `rdf:type ido:PhysicalObject` before any reasoning
    runs (only the class chain carries that assertion, never the instance).

    Anything in `missing` (expected by the hand-walked closure, but owlrl
    didn't infer it) or `unexpected_extra` (owlrl inferred it, but the
    hand-walked closure didn't expect it) is a real disagreement between
    this project's own assumption and a real reasoner — either the ontology
    skeleton's subclass chain is broken, or the hand-walked closure above
    is wrong, or owlrl behaves differently than expected. Not something to
    paper over either way.

    Returns a report dict rather than asserting, so a caller (or a test) can
    inspect exactly which instances passed or failed instead of getting one
    opaque boolean.
    """
    before = masterdata_graph(ds)
    physical_object = rdflib.URIRef(v.IDO_PHYSICAL_OBJECT)
    physical_before = instances_of(before, physical_object)

    expected_classes = _asserted_subclass_closure(before, physical_object)
    expected_instances: "Set[rdflib.URIRef]" = set()
    for cls in expected_classes:
        expected_instances |= instances_of(before, cls)

    reasoned = rdflib.Graph()
    for t in before:
        reasoned.add(t)
    run_owl_rl_closure(reasoned)
    physical_after = instances_of(reasoned, physical_object)

    missing = expected_instances - physical_after
    unexpected_extra = physical_after - expected_instances

    return {
        "asserted_directly_as_physical_object": len(physical_before),  # must be 0 (correctness note #1)
        "expected_instances": len(expected_instances),
        "inferred_as_physical_object": len(physical_after),
        "missing": sorted(str(s) for s in missing),                    # non-empty -> a real gap
        "unexpected_extra": sorted(str(s) for s in unexpected_extra),  # non-empty -> a real disagreement
        "agrees_with_project_assumption": not missing and not unexpected_extra and not physical_before,
    }
