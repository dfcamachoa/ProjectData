"""Example SPARQL query surface (medallion §10 step 3: "master data +
reference data become queryable by SPARQL"), plus two ways to answer them
without a running Fuseki: `run_sparql` (real SPARQL 1.1 execution, via
rdflib's own query engine, now that `Dataset` is genuinely `rdflib`-backed
— see `rdf_model.py`'s docstring) and `run_local_pattern` (a plain
triple-pattern match, kept as the lighter-weight option when a full SPARQL
string is overkill for a simple lookup).

The strings in `EXAMPLE_QUERIES` are real SPARQL 1.1, meant to be sent
verbatim to `fuseki_client.sparql_query` once triples are loaded into a
real Fuseki, or to `run_sparql` below against the in-memory `Dataset` —
both take the identical query text, so a query proven against the
in-memory graph via `run_sparql` needs no rewriting when it later moves to
a real Fuseki deployment.
"""
from __future__ import annotations

from typing import Optional

from . import vocab as v
from .rdf_model import Dataset, Term


def run_sparql(ds: Dataset, query: str):
    """Real SPARQL 1.1 execution against the in-memory Dataset, via
    rdflib's own query engine — no Fuseki required. Returns rdflib's
    `Result` object (iterate it for rows; `.vars` for the projected
    variable names) exactly as `fuseki_client.sparql_query` would return
    parsed JSON bindings from a live Fuseki, so a caller comparing the two
    is comparing the same query against two execution engines, not two
    different query languages."""
    return ds.rdflib_dataset().query(query)

PREFIXES = f"""
PREFIX pidsys: <{v.PIDSYS}>
PREFIX prov: <{v.PROV}>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""

EXAMPLE_QUERIES = {
    # Members and boundary of every computed system — graph:results only,
    # never graph:oracle (the cross-check join happens in a *separate*
    # query, below, that a person runs deliberately for validation).
    "systems_and_members": PREFIXES + f"""
SELECT ?system ?name (COUNT(?member) AS ?memberCount)
FROM <{v.GRAPH_RESULTS}>
WHERE {{
  ?system a pidsys:CommissioningSystem ;
          pidsys:tag ?name ;
          pidsys:member ?member .
}}
GROUP BY ?system ?name
ORDER BY DESC(?memberCount)
""",

    # Every Derived connection — the provenance query the strategy names as
    # the point of reifying Connection at all (medallion §5 note 2).
    "derived_connections": PREFIXES + f"""
SELECT ?connection ?fromObj ?toObj ?connType
FROM <{v.GRAPH_MASTERDATA}>
WHERE {{
  ?connection a pidsys:Connection ;
              pidsys:derived true ;
              pidsys:fromObject ?fromObj ;
              pidsys:toObject ?toObj ;
              pidsys:connType ?connType .
}}
""",

    # The validation cross-check — the ONLY query allowed to join
    # graph:results against graph:oracle, and it is read-only reporting,
    # never an input to any other query or rule (silver_layer_spec.md §5).
    "oracle_cross_check": PREFIXES + f"""
SELECT ?segment ?srcTurnover ?computedSystem
FROM <{v.GRAPH_ORACLE}>
FROM <{v.GRAPH_RESULTS}>
WHERE {{
  GRAPH <{v.GRAPH_ORACLE}>  {{ ?segment pidsys:srcTurnoverSystem ?srcTurnover . }}
  GRAPH <{v.GRAPH_RESULTS}> {{ ?computedSystem pidsys:member ?segment . }}
}}
""",

    # Boundary-role membership as data (data_specification.md §3.2) —
    # "onboarding a project is editing this sheet, not the rule."
    "boundary_roles": PREFIXES + f"""
SELECT ?role ?componentClass
FROM <{v.GRAPH_REFDATA}>
WHERE {{
  ?role pidsys:boundaryMember ?componentClass .
}}
""",
}


def run_local_pattern(
    ds: Dataset,
    s: Optional[Term] = None,
    p: Optional[Term] = None,
    o: Optional[Term] = None,
    graph: Optional[str] = None,
):
    """Local stand-in for the queries above, for use without a SPARQL
    engine — a plain triple-pattern match over the in-memory Dataset."""
    return list(ds.triples(s=s, p=p, o=o, graph=graph))
