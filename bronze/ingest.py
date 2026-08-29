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
from .header import compute_hash, parse_header
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

    df = (
        raw.withColumn("h", header_udf(F.col("content")))
        .withColumn("bronze_id", F.expr("uuid()"))
        # content is already the authoritative binary payload
        .withColumn(
            "content_text",
            F.decode(F.col("content"), "UTF-8") if cfg.store_content_text else F.lit(None).cast("string"),
        )
        .withColumn("content_hash", F.sha2(F.col("content"), cfg.hash_bits))
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
        .withColumn("header_parse_ok", F.coalesce(F.col("h.header_parse_ok"), F.lit(False)))
        .withColumn("project_code", F.lit(cfg.project_code))
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
    spark.sql(create_table_sql(cfg.table_name, cfg.table_path, cfg.partition_by))


def ingest(spark: SparkSession, cfg: BronzeConfig, ingest_run_id: str | None = None) -> dict:
    """Run one ingestion pass. Returns a small run summary."""
    from delta.tables import DeltaTable
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
    batch.unpersist()
    return summary
