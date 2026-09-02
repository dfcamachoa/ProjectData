# Silver layer — Phase-1 (parse + reconstruct + persist)

Reads the Bronze table and reconstructs each drawing into four Delta tables:
`silver_components`, `silver_segments`, `silver_connections`, `silver_equipment`.
Implements Stage A (parse/shred) + Stage B (topology reconstruction) of
`specs/silver_spec.md`. Stages C (assembly), D (Great Expectations) and E (CDC)
are specified but not built in this phase.

## Layout

```
silver/
├── config.py        SilverConfig (bronze table, output schema, options)
├── schema.py        the four Silver table schemas + UDF return struct (§4)
├── reconstruct.py   PURE core: bytes + source_format -> row dicts (Spark-free, testable)
├── spark_job.py     Spark job: read Bronze -> UDF -> explode -> write 4 Delta tables (§6.1)
├── cli.py           python -m silver.cli reconstruct ...
├── smoke_local.py   Spark-free smoke test over sample_data (both formats)
└── _recon/          VENDORED reconstruction (pidtool, bppidsys, pidsys subset) — unchanged
```

The reconstruction algorithm is the validated `pidtool` / `bppidsys` / `pidsys`
code, vendored under `_recon/` and **re-housed, not re-derived** (silver_spec §2).
Only master data + connectivity are vendored — not `walk.py` / `validate.py`
(systemization): Silver is use-case-neutral (§1.2, §5).

## Run

### In a notebook — use the in-session helper (recommended)

Embedded Derby is **single-session** (spec §8.4), so a `!python -m silver.cli`
**subprocess** writes into a *different* metastore than a live notebook Spark
session — the tables get written but the notebook can't resolve them by name.
Run Silver inside the notebook's own session:

```python
from bronze.spark_session import get_spark
spark = get_spark()                       # one Delta+Hive session for the notebook

from bronze.notebook import ingest_folder  # (once) land the samples into Bronze
ingest_folder(spark, source_dir="sample_data")

from silver.notebook import reconstruct    # Bronze -> Silver, same session
print(reconstruct(spark))
spark.table("silver.silver_segments").show()   # visible immediately
```

### From the shell (CLI)

The CLI creates its own session, so run it **only** when no notebook session is
live, from the same working directory as the Bronze ingest (so the metastore +
warehouse match):

```bash
python -m bronze.cli ingest --source-dir sample_data --table bronze.pid_documents
python -m silver.cli reconstruct --bronze-table bronze.pid_documents --silver-schema silver
```

Output is JSON row counts per table. Inspect:

```python
spark.table("silver.silver_components").show()
spark.table("silver.silver_connections").groupBy("flow_sense","derived").count().show()
spark.table("silver.silver_segments").select("seg_tag","fluid","src_turnover").show()  # oracle carried, quarantined
```

Options: `--bronze-path <delta path>` (read Bronze by path instead of catalog),
`--write-mode append|overwrite` (default overwrite — idempotent rebuild, no CDC
yet), `--size-buckets N` (repartition Bronze by `file_size_bytes` for the big
sheets, §6.1), `--refdata Reference_Data.xlsx` (tag composition; falls back to
the built-in preset when absent), `--no-hive` (path-based, no metastore).

## Test without Spark

`reconstruct.py` is Spark-independent, so the algorithmic core is testable
anywhere:

```bash
python -m silver.smoke_local                          # over sample_data (both formats)
python -m silver.smoke_local path/to/real_sheet.xml DEXPI   # one file
```

## What Phase-1 does and doesn't do

- **Does:** pick the adapter from Bronze's `source_format`, build the DOM from
  the `content` bytes (no re-sniffing, no temp files), run the reconstruction,
  stamp master-data tags, emit the four tables with Bronze lineage, carry the
  oracle (`src_turnover`/`src_subsystem`) as **quarantined** segment columns,
  emit reified connections with `derived` + `flow_sense`.
- **Doesn't yet:** OPC cross-document assembly (Stage C), the Great-Expectations
  punch-list ledger (`silver_quality`, Stage D), object-grain CDC (Stage E). The
  `quality_gate` column is present and defaults to `clean`; Stage D populates it.
  `connection_id` is keyed on element ids for this single-version build; the
  anchor-based identity for CDC (§3.5) layers on in Stage E without changing the
  row shape.
