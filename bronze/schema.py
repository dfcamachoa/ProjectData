"""Bronze table schema and DDL (spec §3.1, §8.1).

One Delta table, one row per distinct landed file-version. Kept in one place so
the Spark job, the table DDL, and the header UDF agree on column names and types.
"""
from __future__ import annotations

from pyspark.sql.types import (
    BinaryType,
    BooleanType,
    DateType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# The struct returned by the header UDF (bronze/header.py -> HeaderInfo).
# Column order here must match the order the UDF packs values (see ingest.py).
HEADER_STRUCT = StructType(
    [
        StructField("originating_system", StringType()),
        StructField("source_format", StringType()),
        StructField("format_detection_method", StringType()),
        StructField("client_document_number", StringType()),
        StructField("document_number", StringType()),
        StructField("drawing_revision", StringType()),
        StructField("drawing_revision_date", StringType()),
        StructField("project_code", StringType()),
        StructField("project_code_source", StringType()),
        StructField("header_parse_ok", BooleanType()),
    ]
)

# The full Bronze row.
BRONZE_SCHEMA = StructType(
    [
        StructField("bronze_id", StringType(), nullable=False),
        StructField("content", BinaryType(), nullable=False),
        StructField("content_text", StringType(), nullable=True),  # off by default (spec §3.3)
        StructField("content_hash", StringType(), nullable=False),  # self-describing "sha256:<hex>" (§5.2)
        StructField("file_size_bytes", LongType(), nullable=False),
        StructField("source_path", StringType(), nullable=False),
        StructField("source_filename", StringType(), nullable=True),
        StructField("source_last_modified", TimestampType(), nullable=True),
        StructField("ingested_at", TimestampType(), nullable=False),
        StructField("ingest_run_id", StringType(), nullable=False),
        StructField("originating_system", StringType(), nullable=True),
        StructField("source_format", StringType(), nullable=True),
        StructField("format_detection_method", StringType(), nullable=True),
        StructField("client_document_number", StringType(), nullable=True),
        StructField("document_number", StringType(), nullable=True),
        StructField("drawing_revision", StringType(), nullable=True),
        StructField("drawing_revision_date", StringType(), nullable=True),  # verbatim (spec §6)
        StructField("project_code", StringType(), nullable=True),
        StructField("project_code_source", StringType(), nullable=True),  # authority (spec §3.1)
        StructField("header_parse_ok", BooleanType(), nullable=False),
        StructField("ingest_date", DateType(), nullable=False),  # partition col: to_date(ingested_at)
    ]
)

# Column list in table order, for the final select in the job.
BRONZE_COLUMNS = [f.name for f in BRONZE_SCHEMA.fields]


def create_table_sql(table: str, location: str | None, partition_by: list[str]) -> str:
    """DDL for the Bronze Delta table, with the append-only guarantee (spec §5.1).

    `table` is a catalog name (e.g. schema.pid_documents). `location` sets
    an external storage path if given. Partitioning defaults to ingest_date.
    """
    cols = ",\n    ".join(f"{f.name} {_sql_type(f)}" for f in BRONZE_SCHEMA.fields)
    part = ""
    if partition_by:
        part = f"\nPARTITIONED BY ({', '.join(partition_by)})"
    loc = f"\nLOCATION '{location}'" if location else ""
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n    {cols}\n)\n"
        f"USING DELTA{part}{loc}\n"
        "TBLPROPERTIES (\n"
        "    'delta.appendOnly' = 'true',\n"
        "    'delta.minReaderVersion' = '1',\n"
        "    'delta.minWriterVersion' = '2'\n"
        ")"
    )


def _sql_type(field: StructField) -> str:
    mapping = {
        "StringType": "STRING",
        "BinaryType": "BINARY",
        "LongType": "BIGINT",
        "TimestampType": "TIMESTAMP",
        "DateType": "DATE",
        "BooleanType": "BOOLEAN",
    }
    return mapping[type(field.dataType).__name__]
