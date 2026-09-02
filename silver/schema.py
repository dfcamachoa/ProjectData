"""Silver table schemas (silver_spec §4).

One Delta table per object kind, all carrying Bronze lineage so every Silver row
traces to the exact source bytes. Defined once so the reconstruct core, the UDF
return type, and the table DDL agree on names and types.

Lineage columns present on every row: bronze_id, content_hash, source_format,
project_code, drawing_number.
"""
from __future__ import annotations

from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)

_LINEAGE = [
    StructField("bronze_id", StringType()),
    StructField("content_hash", StringType()),
    StructField("source_format", StringType()),
    StructField("project_code", StringType()),
    StructField("drawing_number", StringType()),
]

# --- silver_components: one Piping Component -------------------------------- #
COMPONENT_STRUCT = StructType([
    StructField("component_id", StringType(), nullable=False),
    StructField("component_class", StringType()),
    StructField("component_name", StringType()),
    StructField("tag", StringType()),
    StructField("segment_id", StringType()),
    StructField("kind", StringType()),
    StructField("inline_index", IntegerType()),
    StructField("inline_count", IntegerType()),
    StructField("is_valve", BooleanType()),
    StructField("quality_gate", StringType()),   # clean|flagged|quarantined (Stage D)
    *_LINEAGE,
])

# --- silver_segments: one Piping Segment (carries QUARANTINED oracle, §5) --- #
SEGMENT_STRUCT = StructType([
    StructField("segment_id", StringType(), nullable=False),
    StructField("fluid", StringType()),
    StructField("unit", StringType()),
    StructField("diameter", StringType()),
    StructField("piping_materials_class", StringType()),
    StructField("insul_type", StringType()),
    StructField("insul_purpose", StringType()),
    StructField("insul_thick", StringType()),
    StructField("item_tag", StringType()),
    StructField("seg_tag", StringType()),
    StructField("pns_tag", StringType()),
    StructField("subline_tag", StringType()),
    StructField("src_turnover", StringType()),    # Z_TurnOverSystemNumber — QUARANTINED
    StructField("src_subsystem", StringType()),   # SubsystemNo — QUARANTINED
    StructField("quality_gate", StringType()),
    *_LINEAGE,
])

# --- silver_connections: one undirected reified edge (§4) ------------------- #
CONNECTION_STRUCT = StructType([
    StructField("connection_id", StringType(), nullable=False),
    StructField("from_id", StringType()),
    StructField("to_id", StringType()),
    StructField("conn_type", StringType()),       # Process|Nozzle|Signal|OffPage
    StructField("derived", BooleanType()),        # Source (stated) vs Derived (reconstructed)
    StructField("flow_sense", StringType()),      # none|forward|reverse|both (vs sorted order)
    *_LINEAGE,
])

# --- silver_equipment: one real (ghost-filtered) Equipment ----------------- #
EQUIPMENT_STRUCT = StructType([
    StructField("equipment_id", StringType(), nullable=False),
    StructField("tag", StringType()),
    StructField("equipment_class", StringType()),
    StructField("nozzle_ids", ArrayType(StringType())),
    *_LINEAGE,
])

# The struct one UDF call returns per drawing — four arrays exploded downstream.
RECON_RESULT_STRUCT = StructType([
    StructField("components", ArrayType(COMPONENT_STRUCT)),
    StructField("segments", ArrayType(SEGMENT_STRUCT)),
    StructField("connections", ArrayType(CONNECTION_STRUCT)),
    StructField("equipment", ArrayType(EQUIPMENT_STRUCT)),
])

# name -> (struct, natural key) for the four output tables
SILVER_TABLES = {
    "silver_components": (COMPONENT_STRUCT, "component_id"),
    "silver_segments": (SEGMENT_STRUCT, "segment_id"),
    "silver_connections": (CONNECTION_STRUCT, "connection_id"),
    "silver_equipment": (EQUIPMENT_STRUCT, "equipment_id"),
}
