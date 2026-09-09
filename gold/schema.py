"""Gold Delta table schemas (mirrors `bronze/schema.py` / `silver/schema.py`'s
own conventions: one place the Spark job and the row-building bridge agree
on column names/types). Requires `pyspark` — imported only by
`gold/spark_job.py`, never by the pure core, so `gold_layer`'s Spark-free
tests are unaffected by whether pyspark is installed.
"""
from __future__ import annotations

from pyspark.sql.types import (
    BooleanType,
    DateType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

GOLD_OBJECTS_TABLE = "gold_objects"
GOLD_ANOMALIES_TABLE = "gold_anomalies"

# One row per GoldRow (temporal.py) -- "never delete, close the interval":
# valid_to/tx_to are NULL while current, and every superseded row is kept,
# never overwritten. `attrs_json` carries the object's engineering
# attributes (heterogeneous across component/line/equipment, hence JSON
# rather than a fixed set of columns); `current` is a query-convenience
# projection of `GoldRow.is_current()`, not a separate source of truth.
GOLD_OBJECTS_SCHEMA = StructType([
    StructField("object_kind", StringType(), nullable=False),
    StructField("anchor_id", StringType(), nullable=False),
    StructField("drawing_number", StringType(), nullable=True),
    StructField("attrs_json", StringType(), nullable=False),
    StructField("valid_from", DateType(), nullable=False),
    StructField("valid_to", DateType(), nullable=True),
    StructField("tx_from", TimestampType(), nullable=False),
    StructField("tx_to", TimestampType(), nullable=True),
    StructField("superseded_by_delta", StringType(), nullable=True),
    StructField("current", BooleanType(), nullable=False),
])
GOLD_OBJECTS_COLUMNS = [f.name for f in GOLD_OBJECTS_SCHEMA.fields]

# This run's punch list (silver_cdc.py::CdcAnomaly) -- an anchor-bucket
# collision or a retroactive correction, "observe and record" rather than a
# crash (Stage D's silver_quality precedent).
GOLD_ANOMALIES_SCHEMA = StructType([
    StructField("object_kind", StringType(), nullable=False),
    StructField("anchor_id", StringType(), nullable=False),
    StructField("drawing_number", StringType(), nullable=True),
    StructField("reason", StringType(), nullable=False),
    StructField("detail", StringType(), nullable=False),
    StructField("transaction_ts", TimestampType(), nullable=False),
])
GOLD_ANOMALIES_COLUMNS = [f.name for f in GOLD_ANOMALIES_SCHEMA.fields]
