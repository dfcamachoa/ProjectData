# Bronze Layer — PySpark / Delta Ingestion

Runnable implementation of the Bronze ingestion layer for the P&ID
pre-commissioning systemization data product. It lands raw DEXPI/Proteus (project A)
and INGR ISO-15926 PostProc/SPPID (project B) exports **as-is**, one immutable row
per distinct file-version, into an append-only Delta table with the ingestion
metadata the specs reserve.

This code implements `bronze_layer_spec.md`. Section references below (§) point into
that spec.

## What it does (and deliberately does not)

Bronze does exactly one interpretive act — a **shallow header read** for identity,
versioning and routing (§1.2, §4). It does **not** parse the network model,
reconstruct topology, run quality gates, do CDC, or model bi-temporal intervals —
those are Silver and Gold. Keeping that boundary is the whole point (§1.2).

Per file it captures: the raw bytes, a sha-256 version hash, file/source lineage,
the detected source format (`DEXPI` / `POSTPROC` / unknown) and how it was detected,
the drawing numbers and **revision** (the seed for Gold's valid-time axis, §6), and a
`header_parse_ok` flag. It **flags, never rejects** (§7): a malformed or partial file
still lands.

## Layout

```
bronze/
  config.py         # BronzeConfig + HeaderFieldConfig — all per-project knobs are data
  header.py         # PURE core: shallow header read, format-detection ladder, hashing (no Spark)
  schema.py         # Bronze Delta table schema + CREATE TABLE DDL (appendOnly)
  spark_session.py  # Spark session wired for Delta
  ingest.py         # the job: binaryFile read -> header UDF -> metadata -> idempotent MERGE
  cli.py            # `python -m bronze.cli ingest|create-table ...`
tests/test_header.py # unit tests for the pure core (format detection, malformed, hashing)
sample_data/        # synthetic DEXPI / PostProc / ambiguous / malformed exports
smoke_local.py      # real end-to-end Spark+Delta run over sample_data (+ idempotency check)
run_tests.py        # runs the unit tests without pytest installed
requirements.txt
```

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

Install (matched Spark/Delta pair — see `requirements.txt`):

```bash
pip install -r requirements.txt      # not on Databricks — the runtime provides these
```

Unit tests (pure core, no Spark, no pytest needed):

```bash
python run_tests.py                  # or: pytest tests/ -q
```

End-to-end local smoke test (needs pyspark + delta-spark + a JDK):

```bash
python smoke_local.py                # ingests sample_data twice, asserts idempotency
```

Ingest a real folder into a catalog table:

```bash
python -m bronze.cli ingest \
    --source-dir /data/exports/projectB \
    --table lakehouse.bronze.bronze_pid_documents \
    --project-code B
```

…or into a path-based Delta table (no catalog):

```bash
python -m bronze.cli ingest \
    --source-dir /data/exports/projectA \
    --table-path /lake/bronze/pid_documents \
    --project-code A
```

On a cluster, run via `spark-submit` with the matching Delta package, e.g.:

```bash
spark-submit --packages io.delta:delta-spark_2.12:3.2.0 \
    -m bronze.cli ingest --source-dir ... --table ...
```

## Idempotency & immutability (§5)

- The **`content_hash` (sha-256 over raw bytes)** is the version key. Byte-identical
  re-ingest is a no-op; any byte difference lands as a **new** version row.
- The write is an **insert-only `MERGE`** on `content_hash` into a table with
  `delta.appendOnly = true` — unseen versions insert, known bytes are ignored,
  existing rows are never mutated. Replay any downstream layer from Bronze alone.
- Whole-file hashing is correct **here** (Bronze versions files). Object-grain change
  detection to separate engineering change from re-export churn is a **Silver/CDC**
  concern and is intentionally not done here (§5.3).

## Validation status

- **Pure core**: 11/11 unit tests pass (`run_tests.py`) — DEXPI/PostProc/ambiguous
  detection, the segment-tagname fallback, namespaced XML, malformed-tolerance,
  empty/non-XML input, and hashing.
- **Spark job**: all modules byte-compile; run `smoke_local.py` in a Spark+Delta
  environment for the end-to-end + idempotency check (it could not be executed in the
  authoring sandbox, which had no Spark).

## Open decisions carried from the spec (§9)

Confirm at onboarding — none blocks running the code:

1. **Revision issue date** — do the DEXPI/PostProc title blocks reliably expose a
   revision *date*? `HeaderFieldConfig.revision_date_attrs` reads it when present;
   Gold falls back to revision order + `source_last_modified` otherwise.
2. **Header field names** — `HeaderFieldConfig` lists *candidate* element/attribute
   names for the drawing numbers and revision. Confirm/extend them against real
   exports; the synthetic samples are illustrative, not authoritative.
3. **`content_text`** on/off (`store_content_text`), hash algorithm, retention/VACUUM,
   `project_code` authority, and table/catalog naming — see spec §9.
