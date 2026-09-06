"""Example SPARQL query surface (medallion §10 step 3: "master data +
reference data become queryable by SPARQL"), plus a tiny local
triple-pattern runner so the same questions are answerable against the
in-memory `Dataset` without a running Fuseki.

The strings in `EXAMPLE_QUERIES` are real SPARQL 1.1, meant to be sent
verbatim to `fuseki_client.sparql_query` once triples are loaded into
Fuseki (or to `rdflib.Graph.query` on graduation — see rdf_model.py's
docstring on why this PoC does not depend on rdflib). `run_local_pattern`
below is not a SPARQL engine; it demonstrates that the same questions are
answerable over the Dataset object this package already builds, for
environments (like this sandbox) where no SPARQL engine is reachable.
"""
from __future__ import annotations

from typing import Optional

from . import vocab as v
from .rdf_model import Dataset, Term

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
