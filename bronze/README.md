# Bronze Layer — PySpark / Delta Ingestion

> Part of the `ProjectData` medallion pipeline — see the [repo-root README](../README.md)
> for how Bronze fits with Silver and Gold, and the shared environment setup
> (`requirements.txt`'s pinned `pyspark`/`delta-spark` pair in particular).

Runnable implementation of the Bronze ingestion layer for the P&ID
pre-commissioning systemization data product. It lands raw DEXPI/Proteus (project A)
and INGR ISO-15926 PostProc/SPPID (project B) exports **as-is**, one immutable row
per distinct file-version, into an append-only Delta table with the ingestion
metadata the specs reserve.

This code implements [`specs/bronze_spec.md`](../specs/bronze_spec.md)
(**Draft v0.2** — see that file's own header for its dated post-v0.2 corrections).
Section references below (§) point into that spec.

## What it does (and deliberately does not)

Bronze does exactly one interpretive act — a **shallow header read** for identity,
versioning and routing (§1.2, §4). It does **not** parse the network model,
reconstruct topology, run quality gates, do CDC, or model bi-temporal intervals —
those are Silver and Gold. Keeping that boundary is the whole point (§1.2).

Per file it captures: the raw bytes, a **self-describing `sha256:<hex>` version hash**
(§5.2), file/source lineage, the detected source format (`DEXPI` / `POSTPROC` /
unknown) and how it was detected, the EPC and client **drawing numbers**, the current
**revision and its issue date** (the seed for Gold's valid-time axis, §6), the derived
**`project_code`** with its authority (§3.1), and a `header_parse_ok` flag. It
**flags, never rejects** (§7): a malformed or partial file still lands.

### v0.2 changes baked in

- **Self-describing hash** `sha256:<hex>` (§5.2), no pre-hash normalisation.
- **`content_text` off by default** — drawings reach ~13 MB, so the derivable UTF-8
  decode is not stored (decode-on-read instead); a 256 KB size gate applies if turned
  on (§3.3).
- **`project_code` derived** from the EPC document number's leading token, with an
  ingest-run override and a run-level mismatch count in the summary (§3.1).
- **Revision from the revision-history** — DEXPI `RevRow{N}No/Date` (current = highest
  N); PostProc `Drawing/@Revision` + the matching revision `Label`'s `TP_RevisionData`;
  dates stored **verbatim** (§6).
- **Per-format document mapping** — DEXPI EPC = `OperationCenterDocNo`, client =
  `DrawingNumber`; PostProc both = `Drawing/@Name` (§3.1).
- **Embedded Derby metastore** (`enableHiveSupport`) for named `bronze.pid_documents`
  on local WSL; path-based tables skip it (§8.4).

## Layout

```
bronze/
  config.py         # BronzeConfig + HeaderFieldConfig — all per-project knobs are data
  header.py         # PURE core: shallow header read, format-detection ladder, hashing (no Spark)
  schema.py         # Bronze Delta table schema + CREATE TABLE DDL (appendOnly)
  spark_session.py  # Spark session wired for Delta — shared by Silver and Gold too
  ingest.py         # the job: binaryFile read -> header UDF -> metadata -> idempotent MERGE
  notebook.py        # notebook-friendly wrapper around the ingest job
  cli.py            # `python -m bronze.cli ingest|create-table ...`
```

Tests, sample data, and repo-wide scripts now live one level up (they're shared
with Silver and Gold): `tests/test_header.py`, `sample_data/`, `smoke_local.py`,
`run_tests.py`, and the single, repo-root `requirements.txt`.

The pure core (`header.py`, `config.py`) imports **only the standard library**, so it
is testable and reusable without PySpark. The Spark job imports are lazy, so
`import bronze; bronze.parse_header(...)` works with no Spark present.

## Format-detection ladder (§4)

1. **`ORIGINATING_SYSTEM`** — `OriginatingSystem` contains an `SPPID` marker →
   `POSTPROC`; any other originator → `DEXPI`. (Cheap, header-only, preferred.)
2. **`SEGMENT_TAGNAME`** — no usable originator: a `PipingNetworkSegment` carrying a
   `TagName` → `POSTPROC`, else `DEXPI`. Mirrors `reconstructed._adapter_for`.
3. **`UNKNOWN`** — neither resolves; the file is still landed.

Bronze **records** the classification; Silver **acts** on it (§4).

## Run it

Install (matched Spark/Delta pair — see the repo-root `requirements.txt`, which
now pins this for the whole repo, not just Bronze):

```bash
cd ..                                # repo root
pip install -r requirements.txt      # not on Databricks — the runtime provides these
```

Unit tests (pure core, no Spark, no pytest needed):

```bash
cd ..
python run_tests.py                  # or: pytest tests/ -q
```

End-to-end local smoke test (needs pyspark + delta-spark + a JDK):

```bash
cd ..
python smoke_local.py                # ingests sample_data twice, asserts idempotency
```

Ingest a real folder into a **path-based** Delta table (simplest — no catalog
needed; recommended on standalone/local Spark and WSL):

```bash
python -m bronze.cli ingest \
    --source-dir /data/exports/projectA \
    --table-path ~/lake/bronze/pid_documents
```

> `--project-code` is **optional** — `project_code` now derives from each file's EPC
> document number (§3.1), so one run can carry several projects. Pass it only to
> override; a mismatch between the override and the derived code is reported in the
> run summary.

…or into a **named managed table**. On standalone/local Spark the built-in
`spark_catalog` accepts a **one- or two-part** name only (`table` or
`schema.table`); the job auto-creates the schema for a two-part name:

```bash
python -m bronze.cli ingest \
    --source-dir /data/exports/projectB \
    --table bronze.pid_documents
```

> **Three-part names** (`catalog.schema.table`) require a multi-catalog backend
> such as Databricks **Unity Catalog**. On plain Spark they fail with
> `REQUIRES_SINGLE_PART_NAMESPACE` — use a one/two-part name or `--table-path`.

On a cluster, run via `spark-submit` with the matching Delta package, e.g.:

```bash
spark-submit --packages io.delta:delta-spark_2.12:3.2.0 \
    -m bronze.cli ingest --source-dir ... --table ...
```

## Idempotency & immutability (§5)

- The **`content_hash` (`sha256:<hex>` over raw bytes)** is the version key.
  Byte-identical re-ingest is a no-op; any byte difference lands as a **new** version
  row. Stored self-describing so a future algorithm change can't silently break dedup.
- The write is an **insert-only `MERGE`** on `content_hash` into a table with
  `delta.appendOnly = true` — unseen versions insert, known bytes are ignored,
  existing rows are never mutated. Replay any downstream layer from Bronze alone.
- Whole-file hashing is correct **here** (Bronze versions files). Object-grain change
  detection to separate engineering change from re-export churn is a **Silver/CDC**
  concern and is intentionally not done here (§5.3).

## Validation status

- **Pure core**: 70/70 unit tests pass (`run_tests.py`, no Spark / no pytest needed) —
  Bronze header parsing (DEXPI EPC-doc-number + `RevRow` revision history, PostProc
  `Drawing/@Revision` + matching revision-Label date, `project_code` derivation, the
  segment-tagname fallback, namespaced XML, malformed-tolerance, empty/non-XML input,
  the self-describing hash), Stage-C OPC assembly, the Stage-D quality suite (including
  un-composable seg_tag and within-line inconsistency), and Stage-E CDC — object-grain
  **and line-grain** (a pure re-split of a line yields zero deltas; a real
  change / re-route / within-line inconsistency yields exactly one).
- **Spark job**: all modules byte-compile; run `smoke_local.py` in a Spark+Delta
  environment (verified working on WSL Ubuntu) for the end-to-end + idempotency check.
- These counts predate Gold's own test suite (`tests/test_fuseki_*.py`,
  `test_gold_job.py`, `test_owl_reasoning.py`, `test_rdf_mapper.py`,
  `test_rules_reference.py`, `test_silver_cdc.py`, `test_spark_bridge.py`,
  `test_temporal.py`), which now shares this same `tests/` directory — see
  `gold/README.md` and `specs/gold_layer_spec.md` for those.

## Confirm at onboarding (the config is data, not code)

Most spec §9 open decisions are now **resolved** and implemented (self-describing
hash, `content_text` off, `project_code` derivation, revision-history capture, PoC
Derby metastore). What still needs checking against **real** export files — a config
edit in `HeaderFieldConfig`, never a code change:

1. **Header element/attribute names.** The confirmed mappings are encoded as defaults
   (DEXPI `OperationCenterDocNo`, `RevRow{N}No/Date`; PostProc `Drawing/@Name`,
   `Drawing/@Revision`, `Revision.TP_RevisionData` Labels). The synthetic samples model
   these shapes but are illustrative — verify the exact names/nesting on your exports
   and extend the candidate lists if a dialect differs.
2. **Revision scheme & date format** (token ordering, `DDMMMYY` vs `YYYY/MM/DD`) — Bronze
   captures verbatim; the *scheme* is consumed downstream by Silver/Gold and belongs in
   Project Reference Data (spec §6).
3. **Org hash standard** — sha-256 clears FIPS; swap only if a standard names another
   (`BronzeConfig.hash_bits`, and the stored prefix updates automatically).
