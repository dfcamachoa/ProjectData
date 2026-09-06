# Gold layer prototype

Companion code to `gold_layer_spec.md`. Pure Python, zero third-party
dependencies (see the spec's §8.1 for why: PyPI was unreachable from the
build environment).

## Run the tests

```
cd gold_layer
python3 -m unittest discover -s tests -v
```

57 tests, all green.

## Layout

```
gold/
  vocab.py             namespace + predicate constants; the IDO-alignment boundary
  rdf_model.py          dependency-free RDF quad store (rdflib-shaped)
  temporal.py            bi-temporal versioning (two time axes)
  silver_cdc.py           consumes Silver Stage E's silver_cdc table (the primary path)
  rdf_mapper.py          canonical objects -> RDF/IDO projection
  oracle_guard.py         oracle-quarantine structural invariant
  rules_reference.py      declarative classification / directional guards
  sparql_queries.py       example SPARQL + a local pattern-matcher
  fuseki_client.py        stdlib-only Fuseki Graph Store / SPARQL client
  gold_job.py             orchestration (+ an illustrative Spark sketch)
  jena_rules/
    classification.rules  Apache Jena counterpart of rules_reference.py
tests/
  fixtures.py             the walk.py scenarios reproduced in miniature
  test_*.py               one file per gold/ module (test_silver_cdc.py covers the Stage E path)
gold_layer_spec.md         the design spec this code implements
```

## Silver Stage E (object-grain CDC)

Silver's Stage E (`silver/cdc.py` / `silver/cdc_job.py` in the `ProjectData`
repo) is now built — see `gold_layer_spec.md`'s implementation-status box.
`gold/silver_cdc.py` is the primary bi-temporal path: it consumes
`silver_cdc`'s New/Modified/Deleted rows directly, keyed on Stage E's own
anchor-match identity. `gold/temporal.py::diff_snapshots` remains only as a
documented fallback for a pre-Stage-E Silver build.

## Merging into the `ProjectData` repo

This session has no GitHub write access, so the `gold/` package was built
and tested standalone. To land it alongside `bronze/` and `silver/`, copy
`gold/` and `tests/test_gold_*.py` etc. into the `ProjectData` repo at the
same level as `silver/`, and add `gold_layer_spec.md` under `claude/`
alongside `bronze_layer_spec.md` / `silver_layer_spec.md`.
