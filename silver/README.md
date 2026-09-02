# Silver layer — Phase-1 (parse + reconstruct + persist) + Stage D (quality gate)

Reads the Bronze table and reconstructs each drawing into four Delta tables:
`silver_components`, `silver_segments`, `silver_connections`, `silver_equipment`.
Implements Stage A (parse/shred) + Stage B (topology reconstruction) + **Stage D
(the Great-Expectations quality gate → `silver_quality`)** of
`specs/silver_spec.md`. Stages C (assembly) and E (CDC) are specified but not
built in this phase.

## Layout

```
silver/
├── config.py         SilverConfig (bronze table, output schema, options)
├── schema.py         the four Silver table schemas + UDF return struct + silver_quality (§4)
├── reconstruct.py    PURE core: bytes + source_format -> row dicts (Spark-free, testable)
├── spark_job.py      Spark job: read Bronze -> UDF -> explode -> write 4 Delta tables (§6.1)
├── quality_suite.py  Stage D: the expectation suite AS DATA (rules-as-data, §3.4)
├── quality.py        Stage D PURE core: evaluate(tables, suite, refdata) -> ledger + gates
├── quality_refdata.py Stage D: load fluid/unit sets + naming patterns from Reference_Data.xlsx
├── quality_job.py    Stage D Spark job: evaluate -> write silver_quality + quality_gate rollup
├── cli.py            python -m silver.cli reconstruct | quality ...
├── smoke_local.py    Spark-free smoke test over sample_data (both formats)
└── _recon/           VENDORED reconstruction (pidtool, bppidsys, pidsys subset) — unchanged
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

from silver.notebook import quality        # Stage D: the quality gate, same session
print(quality(spark, refdata_path="Reference_Data.xlsx"))   # -> silver.silver_quality
spark.table("silver.silver_quality").orderBy("severity").show(40, False)  # the punch list
```

### From the shell (CLI)

The CLI creates its own session, so run it **only** when no notebook session is
live, from the same working directory as the Bronze ingest (so the metastore +
warehouse match):

```bash
python -m bronze.cli ingest --source-dir sample_data --table bronze.pid_documents
python -m silver.cli reconstruct --bronze-table bronze.pid_documents --silver-schema silver
python -m silver.cli quality --silver-schema silver --refdata Reference_Data.xlsx
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

## Stage D — the quality gate (`silver_quality`)

Stage D promotes the specs' advisory flags to a **declarative expectation suite**
(`quality_suite.py`) evaluated by a **pure, Spark-free core** (`quality.py`) and
writes `silver_quality` — the per-drawing / per-project **data-quality punch
list** the pre-commissioning engineer fixes at source before systemization runs.
It also denormalises a `quality_gate` enum (`clean`/`flagged`/`quarantined`) back
onto each object row so a strict consumer can filter without joining the ledger.

Gate policy — **fail for bugs, not for data** (§3.4, decision #6): every
data-quality expectation *flags and flows*; only two **structural invariants**
hard-fail (aborting the run) — an **oracle leak** into a compute table (§5) and an
**unflagged `derived` edge** (§4), both of which are pipeline bugs, not dirty
data. The reference-backed checks (unknown fluid/unit, approved-insulation,
equipment/instrument naming) read the project's `Reference_Data.xlsx` (`Fluid` /
`Unit` / `Insulation` / a `Naming` sheet); when absent they *skip cleanly* rather
than fail — rules-as-data (§7).

The suite (all `flag`/`info`, retained, unless noted): segment completeness
(fluid, piping-class, diameter, insulation-signal-as-review), unknown fluid,
unknown unit, off-list insulation code, equipment-tag naming, instrument-tag
naming, `seg_tag` anchor-collision (§3.5, scoped by drawing, `info`),
prefix-integrity (containment), orphan component — plus the two hard-fail
invariants.

## Test without Spark

`reconstruct.py` and `quality.py` are both Spark-independent, so the algorithmic
core **and** the whole quality gate are testable anywhere:

```bash
python -m silver.smoke_local                          # over sample_data (both formats)
python -m silver.smoke_local path/to/real_sheet.xml DEXPI   # one file
pytest tests/test_quality.py                          # 16 pure Stage-D unit tests
```

## What this build does and doesn't do

- **Does:** pick the adapter from Bronze's `source_format`, build the DOM from
  the `content` bytes (no re-sniffing, no temp files), run the reconstruction,
  stamp master-data tags, emit the four tables with Bronze lineage, carry the
  oracle (`src_turnover`/`src_subsystem`) as **quarantined** segment columns,
  emit reified connections with `derived` + `flow_sense`, and run the **Stage D
  quality gate** → `silver_quality` + the `quality_gate` rollup.
- **Doesn't yet:** OPC cross-document assembly (Stage C) — so the *unmatched-OPC
  open-boundary* expectation is deferred until an assembled graph exists — and
  object-grain CDC (Stage E). `connection_id` is keyed on element ids for this
  single-version build; the anchor-based identity for CDC (§3.5) layers on in
  Stage E without changing the row shape.
