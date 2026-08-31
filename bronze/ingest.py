"""The Bronze ingestion job (spec §5, §8).

Pipeline, per run:
  1. Read the source folder with Spark's binaryFile source — one row per file,
     carrying path, modificationTime, length and the raw content (binary).
  2. Per file (no cross-file state): compute the sha-256 version hash and do the
     shallow header read + format detection (bronze/header.py, via a UDF).
  3. Stamp ingestion metadata (ingested_at, ingest_run_id, project_code, ...).
  4. Idempotent, append-only MERGE into the Bronze Delta table, keyed on
     content_hash: unseen versions are inserted, known bytes are ignored, nothing
     is ever updated (delta.appendOnly = true).

Parallelism is over *files*, never a reformulation of any algorithm (strategy §2).
"""
from __future__ import annotations

import uuid

from pyspark.sql import DataFrame, Row, SparkSession
from pyspark.sql import functions as F

from .config import BronzeConfig
from .header import _algo_name, parse_header
from .schema import BRONZE_COLUMNS, HEADER_STRUCT, create_table_sql


def _make_header_udf(cfg: BronzeConfig):
    """Build the header UDF, capturing the field config by value.

    The pure logic lives in bronze/header.py; this only adapts it to Spark. The
    config is captured in a plain dict-free closure so it serialises to executors.
    """
    header_cfg = cfg.header

    def _run(content: bytearray):
        # binaryFile delivers content as bytearray/bytes on the executor.
        data = bytes(content) if content is not None else b""
        info = parse_header(data, header_cfg)
        return Row(
            originating_system=info.originating_system,
            source_format=info.source_format,
            format_detection_method=info.format_detection_method,
            client_document_number=info.client_document_number,
            document_number=info.document_number,
            drawing_revision=info.drawing_revision,
            drawing_revision_date=info.drawing_revision_date,
            project_code=info.project_code,
            project_code_source=info.project_code_source,
            header_parse_ok=info.header_parse_ok,
        )

    return F.udf(_run, HEADER_STRUCT)


def build_bronze_df(spark: SparkSession, cfg: BronzeConfig, ingest_run_id: str | None = None) -> DataFrame:
    """Read the source folder and produce Bronze rows (not yet written)."""
    ingest_run_id = ingest_run_id or str(uuid.uuid4())

    raw = (
        spark.read.format("binaryFile")
        .option("pathGlobFilter", cfg.path_glob)
        .option("recursiveFileLookup", str(cfg.recursive_lookup).lower())
        .load(cfg.source_dir)
    )
    # raw columns: path (str), modificationTime (ts), length (long), content (binary)

    header_udf = _make_header_udf(cfg)
    hash_prefix = f"{_algo_name(cfg.hash_bits)}:"  # self-describing hash (spec §5.2)

    # content_text (spec §3.3): off by default; if on, only decode files at or
    # below the size gate so a ~13 MB payload is never doubled in a retained row.
    if cfg.store_content_text:
        if cfg.content_text_max_bytes is not None:
            content_text_col = F.when(
                F.col("length") <= F.lit(cfg.content_text_max_bytes),
                F.decode(F.col("content"), "UTF-8"),
            ).otherwise(F.lit(None).cast("string"))
        else:
            content_text_col = F.decode(F.col("content"), "UTF-8")
    else:
        content_text_col = F.lit(None).cast("string")

    # project_code (spec §3.1): derived from the EPC document_number by default;
    # an explicit ingest-run value OVERRIDES and records INGEST_RUN as the source.
    if cfg.project_code:
        project_code_col = F.lit(cfg.project_code)
        project_code_source_col = F.lit("INGEST_RUN")
    else:
        project_code_col = F.col("h.project_code")
        project_code_source_col = F.coalesce(
            F.col("h.project_code_source"), F.lit("UNKNOWN")
        )

    df = (
        raw.withColumn("h", header_udf(F.col("content")))
        .withColumn("bronze_id", F.expr("uuid()"))
        .withColumn("content_text", content_text_col)
        .withColumn("content_hash", F.concat(F.lit(hash_prefix), F.sha2(F.col("content"), cfg.hash_bits)))
        .withColumn("file_size_bytes", F.col("length").cast("long"))
        .withColumn("source_path", F.col("path"))
        .withColumn("source_filename", F.element_at(F.split(F.col("path"), "/"), -1))
        .withColumn("source_last_modified", F.col("modificationTime"))
        .withColumn("ingested_at", F.current_timestamp())
        .withColumn("ingest_run_id", F.lit(ingest_run_id))
        .withColumn("originating_system", F.col("h.originating_system"))
        .withColumn("source_format", F.col("h.source_format"))
        .withColumn("format_detection_method", F.col("h.format_detection_method"))
        .withColumn("client_document_number", F.col("h.client_document_number"))
        .withColumn("document_number", F.col("h.document_number"))
        .withColumn("drawing_revision", F.col("h.drawing_revision"))
        .withColumn("drawing_revision_date", F.col("h.drawing_revision_date"))
        .withColumn("project_code", project_code_col)
        .withColumn("project_code_source", project_code_source_col)
        .withColumn("header_parse_ok", F.coalesce(F.col("h.header_parse_ok"), F.lit(False)))
        .withColumn("ingest_date", F.to_date(F.col("ingested_at")))
    )

    # De-duplicate *within* this batch on content_hash before the merge, so a file
    # presented twice in one run doesn't produce two insert candidates.
    df = df.dropDuplicates(["content_hash"])

    return df.select(*BRONZE_COLUMNS)


def create_bronze_table(spark: SparkSession, cfg: BronzeConfig) -> None:
    """Create the append-only Bronze Delta table if it does not exist (spec §5.1)."""
    if cfg.table_path:
        # Path-based table: creating on first write is fine, but we still set the
        # append-only property explicitly for path tables via DataFrame writer.
        return

    parts = cfg.table_name.split(".")
    if len(parts) == 2:
        # A two-part schema.table name needs its schema (database) to exist first.
        spark.sql(f"CREATE DATABASE IF NOT EXISTS {parts[0]}")
    elif len(parts) >= 3:
        # A three-part catalog.schema.table name requires a multi-catalog backend
        # (e.g. Databricks Unity Catalog). The built-in spark_catalog rejects it
        # with REQUIRES_SINGLE_PART_NAMESPACE. Fail early with a clear message.
        raise ValueError(
            f"Three-part table name {cfg.table_name!r} needs a multi-catalog "
            "backend such as Unity Catalog. On a standalone/local Spark use a "
            "one- or two-part name (e.g. 'bronze.pid_documents'), or a "
            "path-based table via --table-path."
        )
    spark.sql(create_table_sql(cfg.table_name, cfg.table_path, cfg.partition_by))


def ingest(spark: SparkSession, cfg: BronzeConfig, ingest_run_id: str | None = None) -> dict:
    """Run one ingestion pass. Returns a small run summary."""
    from delta.tables import DeltaTable

    # Resolve the run id here so the same value is stamped on the rows AND
    # reported in the summary (otherwise the summary shows None).
    ingest_run_id = ingest_run_id or str(uuid.uuid4())

    create_bronze_table(spark, cfg)
    batch = build_bronze_df(spark, cfg, ingest_run_id).cache()
    batch_count = batch.count()

    target_exists = (
        DeltaTable.isDeltaTable(spark, cfg.table_path)
        if cfg.table_path
        else spark.catalog.tableExists(cfg.table_name)
    )

    if not target_exists:
        # First-ever write: create the table from the batch with append-only set.
        writer = (
            batch.write.format("delta")
            .option("delta.appendOnly", "true")
            .partitionBy(*cfg.partition_by)
        )
        if cfg.table_path:
            writer.save(cfg.table_path)
        else:
            writer.saveAsTable(cfg.table_name)
        inserted = batch_count
    else:
        target = (
            DeltaTable.forPath(spark, cfg.table_path)
            if cfg.table_path
            else DeltaTable.forName(spark, cfg.table_name)
        )
        before = target.toDF().count()
        # Insert-only MERGE on the version key: unseen bytes land, known bytes are
        # ignored, existing rows are never modified (idempotent + append-only).
        (
            target.alias("t")
            .merge(batch.alias("s"), "t.content_hash = s.content_hash")
            .whenNotMatchedInsertAll()
            .execute()
        )
        after = target.toDF().count()
        inserted = after - before

    summary = {
        "ingest_run_id": ingest_run_id,
        "files_in_batch": batch_count,
        "rows_inserted": inserted,
        "rows_skipped_already_present": batch_count - inserted,
    }

    # project-code cross-check (spec §3.1): when an ingest-run code overrode the
    # derived one, surface how many rows disagree with the code the EPC document
    # number implies — flagged, never silently reconciled.
    if cfg.project_code:
        delim = cfg.header.project_code_delimiter
        idx = cfg.header.project_code_token_index
        derived_token = F.element_at(F.split(F.col("document_number"), delim), idx + 1)
        mismatches = batch.filter(
            F.col("document_number").isNotNull()
            & (derived_token != F.lit(cfg.project_code))
        ).count()
        summary["project_code_override"] = cfg.project_code
        summary["project_code_mismatches"] = mismatches

    batch.unpersist()
    return summary
