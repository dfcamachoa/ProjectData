# ProjectData — Automatic Pre-Commissioning Systemization (P&ID)

Data product implementing the **Master Data Spec**, **Systemization Spec**, and
**Algorithm Spec** (`specs/`) for pre-commissioning system-boundary detection from
DEXPI (project A) and ISO-15926/PostProc (project B) P&ID exports — a
medallion-architecture PoC (**Bronze → Silver → Gold**) on local Spark/Delta,
feeding an RDF/IDO semantic layer.

## The three layers

| Layer | What it does | Code | Docs |
|---|---|---|---|
| **Bronze** | Lands raw DEXPI/PostProc exports as-is, one immutable row per file-version, with a shallow header read for identity/versioning/routing. | `bronze/` | [`bronze/README.md`](bronze/README.md), [`specs/bronze_spec.md`](specs/bronze_spec.md) |
| **Silver** | Quality gates, per-format reconstruction (DEXPI/PostProc adapters), object-grain **and** line-grain CDC. | `silver/` | [`silver/README.md`](silver/README.md), [`specs/silver_spec.md`](specs/silver_spec.md) |
| **Gold** | Bi-temporal Delta tables, RDF/IDO projection, SPARQL surface, a real Fuseki push, an OWL-RL cross-check of the class hierarchy. | `gold/` | [`gold/README.md`](gold/README.md), [`specs/gold_layer_spec.md`](specs/gold_layer_spec.md) |

Each layer only does its own job — Bronze never parses the network model, Silver
never touches bi-temporal intervals, Gold never re-derives what Silver already
decided. `specs/strategy.md` is where that boundary is argued for; the per-layer
specs are where it's made precise.

## Specs

Authoritative specs live in `specs/`:

- `strategy.md` — the overall medallion + RDF/IDO strategy and why the layer
  boundaries sit where they do
- `bronze_spec.md`, `silver_spec.md`, `gold_layer_spec.md` — per-layer design,
  each with a running, **dated** implementation-status log at the top; read that
  log before assuming a gap is still open — most of what earlier drafts flagged
  as future work has a dated "Resolved" entry by now
- `systemization/data_spec.md`, `systemization/algorithm_spec.md`,
  `systemization/systemization_spec.md`, `systemization/architecture_note.md`
- `spark/spark_concepts_bronze.md`

## Environment setup

- See `WINDOWS_WSL_SETUP.md` for the local WSL dev environment this PoC targets.
- `requirements.txt` at the repo root is the **single, shared** dependency list
  for all three layers: `rdflib`/`owlrl` (Gold's RDF/IDO layer) plus a
  **deliberately pinned** `pyspark==3.5.1` / `delta-spark==3.2.0` pair (Bronze,
  Silver, and Gold all share one Spark session builder,
  `bronze/spark_session.py::get_spark()`). Don't loosen that pin casually — an
  earlier unpinned `delta-spark` pulled a version requiring a newer `pyspark`
  than was installed, and threw `NoSuchMethodError`/`NoClassDefFoundError` out
  of plain `SparkSession` init before Delta even loaded. It turned out to have
  a second cause layered under it — a corrupted, mixed-version `pyspark/jars/`
  directory left behind by a botched `pip` upgrade — fixed only by a clean
  `pip uninstall` + manual jar-directory removal + reinstall, not the pin
  alone. See `specs/gold_layer_spec.md`'s 2026-09-10 entries for the full,
  dated account before touching this again.
- `Reference_Data.xlsx` / `Reference_Data_CFI.xlsx` — Project Reference Data
  (fluid codes and the like) that the specs deliberately externalise as
  project-scoped configuration, not code.

## The notebook

`medallion_concepts.ipynb` at the repo root is the single, current walkthrough:
a real Bronze ingest → real Silver CDC → Gold's real Spark bi-temporal job →
RDF/IDO projection → SPARQL queries → a real push into a running Fuseki
instance. It supersedes any older per-layer notebook copies — if an older one
turns up elsewhere in the tree, it predates the Gold sections and should be
retired once you've confirmed this one covers everything it did.

## Running the tests

All three layers' unit tests live together under `tests/`:

```bash
python -m unittest discover -s tests -v
```

or, without a discovery runner:

```bash
python run_tests.py
```

Bronze's and Silver's pure-core tests need no third-party dependencies. Gold's
`rdflib`/`owlrl`-backed modules (`test_rdf_mapper.py`, `test_rules_reference.py`,
`test_gold_job.py`, `test_owl_reasoning.py`, `test_fuseki_bootstrap.py`) need
`requirements.txt` installed first — until then they fail cleanly with
`ModuleNotFoundError`, which is expected, not a bug.

## Status

Each layer's own README/spec carries the authoritative, current validation
status — check there rather than here, so numbers never drift out of sync in
two places:

- Bronze: `bronze/README.md` → "Validation status"
- Silver: `silver/README.md`
- Gold: `gold/README.md`, and `specs/gold_layer_spec.md`'s dated
  implementation-status log — the most detailed and most current of the three,
  logging every real-environment finding this PoC has hit (the Fuseki
  Basic-Auth fix, the Spark/Delta jar-mismatch incident, the first real
  project-scale Fuseki push) with a date and a resolution.
