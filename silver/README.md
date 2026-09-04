# Silver layer — COMPLETE (Stages A · B · C · D · E)

Reads the Bronze table and reconstructs each drawing into four Delta tables:
`silver_components`, `silver_segments`, `silver_connections`, `silver_equipment`.
Implements **all five** sub-stages of `specs/silver_spec.md`: Stage A (parse/shred)
+ Stage B (topology reconstruction) + **Stage C (cross-document OPC assembly)** +
**Stage D (the Great-Expectations quality gate → `silver_quality`)** + **Stage E
(object-grain CDC → `silver_cdc`)**.

## Layout

```
silver/
├── config.py         SilverConfig (bronze table, output schema, options)
├── schema.py         the four Silver table schemas + UDF return struct + silver_quality (§4)
├── reconstruct.py    PURE core: bytes + source_format -> row dicts (Spark-free, testable)
├── spark_job.py      Spark job: read Bronze -> UDF -> explode -> write 4 Delta tables (§6.1)
├── assemble.py       Stage C PURE core: harvest OPCs + match_pairs -> OffPage rows + open boundaries
├── assemble_job.py   Stage C Spark job: harvest per drawing -> match -> write OffPage + opc_open_boundary
├── cdc.py            Stage E PURE core: anchor-match + 3 hashes + diff(old,new,grain) -> deltas
├── cdc_job.py        Stage E Spark job: diff two latest Bronze versions per drawing -> write silver_cdc
├── quality_suite.py  Stage D: the expectation suite AS DATA (rules-as-data, §3.4)
├── quality.py        Stage D PURE core: evaluate(tables, suite, refdata) -> ledger + gates
├── quality_refdata.py Stage D: load fluid/unit/insulation sets + naming patterns from Reference_Data.xlsx
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

from silver.notebook import assemble       # Stage C: join the P&IDs via OPCs
print(assemble(spark))                          # -> OffPage edges + open boundaries
spark.table("silver.silver_connections").where("conn_type='OffPage'").show()

from silver.notebook import quality        # Stage D: the quality gate, same session
print(quality(spark, refdata_path="Reference_Data.xlsx"))   # -> silver.silver_quality
spark.table("silver.silver_quality").orderBy("severity").show(40, False)  # the punch list

from silver.notebook import changes        # Stage E: object-grain CDC, same session
print(changes(spark))                           # -> silver.silver_cdc (needs >=2 versions/drawing)
spark.table("silver.silver_cdc").show(40, False)
```

### From the shell (CLI)

The CLI creates its own session, so run it **only** when no notebook session is
live, from the same working directory as the Bronze ingest (so the metastore +
warehouse match):

```bash
python -m bronze.cli ingest --source-dir sample_data --table bronze.pid_documents
python -m silver.cli reconstruct --bronze-table bronze.pid_documents --silver-schema silver
python -m silver.cli assemble --bronze-table bronze.pid_documents --silver-schema silver
python -m silver.cli quality --silver-schema silver --refdata Reference_Data.xlsx
python -m silver.cli cdc --bronze-table bronze.pid_documents --silver-schema silver
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

## Stage C — cross-document assembly (`OffPage` edges)

Stage B reconstructs each drawing on its own; Stage C joins them into one plant by
matching **off-page connectors** across sheets — by `OPCTag` for PostProc, by GUID
for DEXPI (`bppidsys.offpage.match_pairs`, re-housed via `pidsys.reconstructed`).
It runs as a cheap per-drawing **harvest** (parse + collect each sheet's OPC
records — never the 13 MB payload) feeding a plant-level **reduce** in the driver,
then writes:

- one undirected, always-`derived` **`OffPage`** row into `silver_connections`
  per matched OPC pair (it spans two drawings); and
- one `opc_open_boundary` flag into `silver_quality` per **unmatched** OPC — an
  open boundary (the system continues off-sheet), never an error or a dropped row.

Both writes are idempotent (clear this stage's rows, then append), so re-running
after loading more sheets resolves open boundaries into `OffPage` edges. Run it
after `reconstruct` and (if you want the open boundaries in the ledger) before or
after `quality` — they touch disjoint rows.

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

## Stage E — object-grain CDC (`silver_cdc`)

SmartPlant re-exports the whole drawing XML for one symbol move and re-mints an
element's UID on delete+recreate, so a file hash (or the UID) marks everything
changed. Stage E separates **engineering change** from **re-export churn** with an
*anchor-match* identity that survives delete+recreate — equipment tag /
`(drawing, seg_tag)` / `(segment-anchor, class)` bucket — and three separated
hashes: `anchor_hash` (matching, no UID), `content_hash_eng` (engineering attrs +
neighbour **anchor** sets — the Modify trigger), `content_hash_audit` (adds UID +
the quarantined oracle, so a recreate is *visible* but stays *inert* for
engineering CDC). It diffs, per drawing, the **two most recent Bronze versions**
(ordered by `ingested_at`) and writes New/Modified/Deleted deltas to `silver_cdc` —
Gold's interval open/close events. A delete+recreate of an unchanged object yields
**zero** deltas. To see deltas a drawing needs **≥2 Bronze versions** (re-ingest a
revised sheet, re-run reconstruct, then cdc).

## Test without Spark

The algorithmic cores are all Spark-independent, so they're testable anywhere:

```bash
python -m silver.smoke_local                          # over sample_data (both formats)
python -m silver.smoke_local path/to/real_sheet.xml DEXPI   # one file
pytest tests/test_quality.py tests/test_assemble.py tests/test_cdc.py tests/test_reconstruct_insulation.py   # 42 tests
```

## What this build does (Silver is complete)

- **Does:** pick the adapter from Bronze's `source_format`, build the DOM from
  the `content` bytes (no re-sniffing, no temp files), run the reconstruction,
  stamp master-data tags, emit the four tables with Bronze lineage, carry the
  oracle (`src_turnover`/`src_subsystem`) as **quarantined** segment columns,
  emit reified connections with `derived` + `flow_sense`, and run the **Stage D
  quality gate** → `silver_quality` + the `quality_gate` rollup.
- **Stage C** joins the sheets (OPC `OffPage` edges + open boundaries); **Stage D**
  gates quality (`silver_quality` + `quality_gate`); **Stage E** captures
  object-grain change (`silver_cdc`, delete+recreate-safe).
- **One §3.5 optimisation is left for later:** recompute-scoping (re-running the
  reconstruction only for changed drawings + their OPC neighbours). The current
  build reconstructs everything each run; CDC *detection* is complete. `connection_id`
  is element-id-keyed within a version; cross-version identity is the anchor-match
  Stage E implements. Next layer: **Gold** (bi-temporal intervals over `silver_cdc`).
