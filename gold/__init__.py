"""Gold layer prototype package.

Two halves, with different dependency stories:

  - **Pure, zero-dependency, unit-tested in any sandbox:** bi-temporal
    versioning of the Silver object grain (temporal.py), Stage E `silver_cdc`
    consumption (silver_cdc.py), the pure Spark-bridge resolution logic
    (spark_bridge.py), and orchestration config (config.py).
  - **Real-dependency, as of 2026-09-09:** the RDF/IDO projection
    (rdf_model.py -- genuinely `rdflib`-backed now, not hand-rolled;
    rdf_mapper.py; oracle_guard.py; rules_reference.py, validated against
    `jena_rules/classification.rules`'s one-to-one Jena counterpart;
    sparql_queries.py, real SPARQL via rdflib or a local pattern match; a
    first OWL/RDFS entailment cross-check via `owlrl` (owl_reasoning.py) --
    and the Gold bi-temporal Spark job (schema.py, spark_job.py, which need
    `pyspark`/`delta-spark`). `fuseki_client.py` stays stdlib-only either
    way (no `rdflib`/`requests` needed to build or send an HTTP request).

Through 2026-09-06, `rdflib`/`pyspark`/`delta-spark`/`owlrl` could not be
installed in the build sandbox (PyPI is not on this org's egress
allowlist), so the RDF layer was hand-rolled to the minimum needed to prove
the design, and the Spark jobs were illustrative sketches. The user's own
local environment carries all four now, so the RDF layer and the Gold Spark
job are real, runnable code — see `rdf_model.py`, `spark_job.py`, and
`owl_reasoning.py`'s module docstrings for exactly what changed and why the
call surfaces were kept stable. Both remain unexecuted IN THIS SANDBOX (the
same PyPI constraint still applies here); `gold_layer_spec.md` documents
the graduation and its test-count consequences in full.
"""
