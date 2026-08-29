# Spark Concepts — as used in the Bronze ingestion layer

**Context:** This reference explains the Apache Spark / PySpark concepts the project
actually uses, anchored to the **Bronze ingestion** code (`bronze_ingestion/`, which
implements `bronze_layer_spec.md`). Every concept below points at a real line in that
job rather than an abstract example, so it doubles as a walkthrough of how the Bronze
layer runs.

**Scope note:** these are the concepts exercised by Bronze ingestion specifically
(reading a folder of files, per-file processing, an idempotent Delta write). Silver
(reconstruction UDFs, joins/shuffles, Great Expectations) will add a few more —
notably shuffles and wide transformations — which are flagged where relevant but not
covered in depth here.

---

## The five layers to hold in your head

1. **Runtime model** — who runs the work (driver, executors, local mode).
2. **Data model** — what you operate on (DataFrames, partitions, schema).
3. **Execution model** — when work actually happens (laziness, transformations vs actions, the DAG, cache).
4. **Compute building blocks** — how you express work (Column expressions, functions, UDFs).
5. **Delta layer** — what makes the table durable (transaction log, MERGE, catalog).

---

## 1. Runtime model — driver, executors, local mode

A Spark application always has one **driver** and some number of **executors**.

- **Driver** — the process running *your* Python. It builds the plan and hands work out. In the Bronze job, the driver is what runs `ingest()` and `build_bronze_df()`.
- **Executors** — JVM processes that actually chew through the data in parallel. Each executor runs **tasks**; a task is one unit of work over one slice (partition) of the data.

On a real cluster the driver and executors are on different machines. In **local mode** (`local[*]`) they are all threads inside a *single* JVM, and `[*]` means "use as many worker threads as there are CPU cores." Local mode is a fully real Spark — just everyone in one process — so the same code runs unchanged on a 100-node cluster; only the `master` URL changes.

**Where in the code:** `bronze/spark_session.py` → `get_spark()`. The
`SparkSession.builder … getOrCreate()` call launches the driver and (in local mode)
the in-process executor. The `spark.sql.extensions` + `DeltaCatalog` config lines are
what teach the session to understand Delta tables.

---

## 2. Data model — DataFrames, partitions, schema

The **DataFrame** is the one abstraction everything flows through: a distributed
table, its rows split into chunks called **partitions**, each partition living on and
processed by one executor.

In the Bronze job, `spark.read.format("binaryFile").load(...)` produces a DataFrame
where **each source file is one row**, with columns `path`, `modificationTime`,
`length`, and `content` (raw bytes). Because the files are independent rows, Spark
hands different files to different executors and processes them **in parallel** — this
is the spec's *"parallelism is over files, not a reformulation of the algorithm"*
point. Per-file work (hashing, header read, and later the topology reconstruction)
runs side by side.

Two properties matter:

- **Immutable.** Every `withColumn` does not modify a DataFrame; it produces a *new*
  one meaning "the old one, plus this column." The long `.withColumn(...).withColumn(...)`
  chain in `build_bronze_df` is building a new description at each step, not mutating.
- **Schema-first.** Spark always knows column names and types up front. That is why
  `bronze/schema.py` declares `BRONZE_SCHEMA` and `HEADER_STRUCT` as `StructType`
  objects — Spark validates and optimizes against them before running anything.

---

## 3. Execution model — laziness, transformations vs actions, the DAG

This is the concept that most shapes the job. Spark operations are of two kinds:

- **Transformations are lazy.** They compute nothing; they record intent.
  `withColumn`, `select`, `dropDuplicates`, `decode`, `to_date` all just extend the
  plan. When `build_bronze_df(...)` returns, **not a single file has been read yet** —
  you have built a recipe, not a meal.
- **Actions trigger execution.** The moment you call one, Spark takes the whole chain
  of transformations, builds an optimized execution plan — a **DAG** (directed acyclic
  graph) of stages and tasks — and runs it across the executors. In the Bronze job the
  actions are `batch.count()`, the `.write.save()` / `.saveAsTable()`, the Delta
  `.merge(...).execute()`, and (in the smoke test) `.show()` and `.distinct().count()`.

So: transformations build a lazy plan → the first action makes Spark plan-and-run it →
results come back. Laziness is *why* Spark is fast: it sees the whole pipeline before
executing and can reorder, combine, and prune steps (the **Catalyst optimizer**)
rather than naively materializing every intermediate step.

**Direct consequence in the code — why `cache()` is there.** The plan re-runs from
scratch on *every* action. The Bronze job hits the `batch` DataFrame with **two**
actions (`.count()` then the `MERGE`), which would read and parse every file twice.
`cache()` prevents that:

```python
batch = build_bronze_df(spark, cfg, ingest_run_id).cache()
batch_count = batch.count()       # first action: computes the batch AND caches it
...merge(batch.alias("s"), ...)   # reuses the cached result instead of recomputing
```

`cache()` is itself lazy — the caching happens during that first `count()` action —
and `batch.unpersist()` at the end releases the memory.

> **Coming in Silver:** transformations that need to move rows between partitions
> (joins, `groupBy`, `distinct` across partitions) cause a **shuffle** — the expensive,
> network-heavy step. Bronze's `dropDuplicates(["content_hash"])` is the first mild
> taste of this; the reconstruction joins in Silver are where shuffles matter.

---

## 4. Compute building blocks — Columns, functions, UDFs

When you write `F.col("content")` or `F.sha2(F.col("content"), 256)`, you are not
computing anything in Python. You are building a **Column expression** — a description
of a computation that Spark compiles and runs **inside the JVM executors**, across all
partitions, with no per-row Python. `F.sha2`, `F.to_date`, `F.decode`,
`F.element_at`, `F.lit`, `F.coalesce` in `build_bronze_df` are all these built-ins.
They are fast because they run natively and the optimizer understands them.

A **UDF** (user-defined function) is the escape hatch for when no built-in does the
job — here, reading the XML header. `_make_header_udf` wraps the pure-Python
`parse_header` so Spark can call it as a column expression:

```python
header_udf = F.udf(_run, HEADER_STRUCT)
raw.withColumn("h", header_udf(F.col("content")))
```

Two things to understand about UDFs:

- **They cross a language boundary.** For a Python UDF, Spark ships each row's bytes
  from the JVM to a separate Python worker process on the executor, runs the function,
  and ships the result back. That serialization makes Python UDFs slower than
  built-ins — which is exactly why the design does the cheap, universal work (hashing,
  dates) with built-in functions and reserves the UDF for the one genuinely custom
  step (parsing XML).
- **The function is serialized to the executors.** Everything the UDF closes over must
  be pickle-able, which is why `_make_header_udf` captures a plain config object and
  the pure logic lives in `bronze/header.py` with **no Spark imports**. The UDF returns
  a `StructType` (`HEADER_STRUCT`), so one call yields a whole group of columns
  (`h.source_format`, `h.document_number`, …) that are then broken out with
  `withColumn`.

**Design payoff:** because the substantive logic is a pure function, it is unit-tested
in plain Python (`tests/test_header.py`, run via `run_tests.py`) with no Spark at all —
Spark is just the parallel harness wrapped around it.

---

## 5. Delta layer — transaction log, MERGE, catalog

Delta Lake sits *on top of* Spark and adds what plain files lack: a transaction log
(the `_delta_log` folder) giving **ACID transactions, time travel, and property
enforcement**. A Delta table is a folder of Parquet data files plus that log recording
every version — precisely the immutable audit trail Bronze wants.

**MERGE** is the Delta operation doing the idempotency (spec §5):

```python
target.alias("t").merge(batch.alias("s"), "t.content_hash = s.content_hash") \
      .whenNotMatchedInsertAll().execute()
```

Read it as: "for each incoming row, if no existing row shares its `content_hash`,
insert it; otherwise do nothing." It is atomic — all-or-nothing — and because it only
inserts, it respects `delta.appendOnly = true`. That property is Delta **enforcing**
immutability at the storage layer, not merely by convention.

**Catalog and namespaces** (the naming that caused `REQUIRES_SINGLE_PART_NAMESPACE`):

- A `SparkSession` has a **catalog** — the registry of named tables and databases.
- Local/standalone Spark uses the built-in **`spark_catalog`**, which allows at most a
  **two-part** name: `database.table` (one "namespace" part).
- A **three-part** `catalog.database.table` name assumes a *second* catalog beside
  `spark_catalog` — what Databricks **Unity Catalog** provides — so on plain Spark it
  errors. Use a one- or two-part name locally; the same three-part name becomes correct
  once the code runs on Unity Catalog.
- A **path-based table** (`--table-path`) sidesteps the catalog entirely: the table
  *is* a folder on disk, identified by its path, nothing registered by name. Simplest
  choice for a local PoC.

**Where in the code:** `bronze/ingest.py` → `create_bronze_table` (auto-creates the
schema for a two-part name; rejects three-part names with a clear message) and the
`whenNotMatchedInsertAll` MERGE in `ingest`. `bronze/schema.py` sets
`delta.appendOnly = true` in the table DDL.

---

## The whole Bronze flow in one sentence

`get_spark()` starts the **driver**; `read.binaryFile` defines a **DataFrame** of
one-row-per-file across **partitions**; a chain of lazy **transformations** (built-in
**Column expressions** plus the header **UDF**) describes the work; the first **action**
(`count`) triggers the **DAG** to run across **executors** and, thanks to **cache**,
keeps the result; and the **Delta MERGE** commits it atomically into an append-only
table that the **catalog** (or a path) points at.

---

## Quick glossary

| Term | One-line meaning | In Bronze |
|---|---|---|
| Driver | Runs your Python, builds the plan | `get_spark()`, `ingest()` |
| Executor / task | JVM worker / one unit of work over one partition | processes files in parallel |
| Local mode `local[*]` | Driver + executors in one JVM, one thread per core | how you run it on WSL |
| DataFrame | Distributed, immutable, schema-typed table | the `raw` / `batch` frames |
| Partition | A slice of a DataFrame processed by one task | one-row-per-file from `binaryFile` |
| Transformation (lazy) | Records intent, computes nothing | `withColumn`, `select`, `dropDuplicates` |
| Action (eager) | Triggers the DAG to run | `count`, `save`, `merge().execute()`, `show` |
| DAG / Catalyst | The optimized plan Spark builds then runs | built at the first action |
| Column expression | A computation compiled to run in the JVM | `F.sha2`, `F.to_date`, `F.decode` |
| UDF | Custom Python called per row (slower, serialized) | `header_udf` wrapping `parse_header` |
| StructType / schema | Declared column names + types | `BRONZE_SCHEMA`, `HEADER_STRUCT` |
| cache / persist | Keep a computed DataFrame to avoid recompute | `batch.cache()` before two actions |
| Shuffle | Moving rows between partitions (joins/groupBy) | mild in Bronze; central in Silver |
| Delta table / `_delta_log` | Parquet + transaction log = ACID + time travel | the Bronze table |
| MERGE | Atomic conditional upsert | idempotent insert on `content_hash` |
| appendOnly | Storage-enforced immutability | `delta.appendOnly = true` |
| Catalog / namespace | Registry of named tables; 1–2 parts on `spark_catalog` | `--table` vs `--table-path` |