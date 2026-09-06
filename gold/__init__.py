"""Gold layer prototype package.

Implements, in pure Python (no Spark / no JVM required to run or test):

  - bi-temporal versioning of the Silver object grain (temporal.py)
  - a dependency-free RDF triple/quad model with named-graph support (rdf_model.py)
  - the canonical-objects -> RDF/IDO projection (rdf_mapper.py)
  - the oracle-quarantine structural invariant, enforced at the RDF layer (oracle_guard.py)
  - the declarative classification / local-directional rules that the strategy
    assigns to Jena (rules_reference.py), plus the authoritative Jena rule file
    (jena_rules/classification.rules) they are validated against
  - an example SPARQL query surface, runnable locally or against a real Fuseki
    (sparql_queries.py)
  - a stdlib-only Fuseki loader (fuseki_client.py)
  - an orchestration sketch (gold_job.py) showing how these compose into the
    medallion Gold stage

No third-party packages (rdflib, pyspark, delta-spark, owlrl, ...) are
available in this sandbox (PyPI is not reachable), so the RDF and query
surfaces are hand-rolled to the minimum needed to prove the design is
correct and unit-testable. `gold_layer_spec.md` (the companion doc) states
explicitly where a graduation to rdflib + real Jena/Fuseki plugs in without
changing the triple shapes this package already produces.
"""
