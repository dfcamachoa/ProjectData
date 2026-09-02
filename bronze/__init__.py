"""Bronze ingestion layer for the P&ID pre-commissioning data product.

Public surface:
    parse_header, compute_hash, HeaderInfo   — pure, Spark-independent core
    BronzeConfig, HeaderFieldConfig          — configuration
    build_bronze_df, ingest, create_bronze_table  — the Spark/Delta job

The Spark job imports are lazy so that the pure core can be imported and tested
without PySpark installed.
"""
from __future__ import annotations

from .config import BronzeConfig, HeaderFieldConfig
from .header import (
    FORMAT_DEXPI,
    FORMAT_POSTPROC,
    METHOD_APPLICATION,
    METHOD_SEGMENT_TAGNAME,
    METHOD_UNKNOWN,
    HeaderInfo,
    compute_hash,
    parse_header,
)

__all__ = [
    "BronzeConfig",
    "HeaderFieldConfig",
    "HeaderInfo",
    "parse_header",
    "compute_hash",
    "FORMAT_DEXPI",
    "FORMAT_POSTPROC",
    "METHOD_APPLICATION",
    "METHOD_SEGMENT_TAGNAME",
    "METHOD_UNKNOWN",
]


def __getattr__(name):
    # Lazy access to the Spark-dependent job so `import bronze` works without pyspark.
    if name in {"build_bronze_df", "ingest", "create_bronze_table"}:
        from . import ingest as _ingest

        return getattr(_ingest, name)
    raise AttributeError(f"module 'bronze' has no attribute {name!r}")
