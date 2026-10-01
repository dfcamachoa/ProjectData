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
    TimestampType,
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

# name -> (struct, natural key) for the four Stage A+B reconstruction output
# tables. This drives spark_job.run()'s explode of the reconstruction UDF's
# result struct (RECON_RESULT_STRUCT), which has exactly these four array fields
# — so silver_off_page_connectors MUST NOT be added here: it is a Stage C
# assembly output (written directly by assemble_job.py via OPC_ENTITY_STRUCT /
# OPC_ENTITY_TABLE below), not a reconstruction output, and there is no
# `off_page_connectors` field on the reconstruction struct to explode.
SILVER_TABLES = {
    "silver_components": (COMPONENT_STRUCT, "component_id"),
    "silver_segments": (SEGMENT_STRUCT, "segment_id"),
    "silver_connections": (CONNECTION_STRUCT, "connection_id"),
    "silver_equipment": (EQUIPMENT_STRUCT, "equipment_id"),
}

# --- silver_quality: the Stage-D verdict ledger (§3.4, §4) ------------------ #
# One row per flag occurrence — the per-drawing / per-project data-quality punch
# list the pre-commissioning engineer fixes at source before systemization runs.
QUALITY_STRUCT = StructType([
    StructField("object_id", StringType()),        # the flagged object (or anchor)
    StructField("object_kind", StringType()),      # component|segment|connection|equipment|run
    StructField("flag", StringType(), nullable=False),   # expectation id
    StructField("severity", StringType()),         # info|warn|error
    StructField("gate", StringType()),             # drop|quarantine|flag|fail
    StructField("stage", StringType()),            # "D"
    StructField("detail", StringType()),           # human-readable punch-list line
    StructField("drawing_number", StringType()),
    StructField("project_code", StringType()),
    StructField("source_format", StringType()),
    StructField("transaction_ts", TimestampType()),
])

QUALITY_TABLE = "silver_quality"

# columns of QUALITY_STRUCT in declared order (drives the pure core -> Row build)
QUALITY_COLUMNS = [f.name for f in QUALITY_STRUCT.fields]

# --- Stage C: harvested OPC record (silver_spec §3.3) ---------------------- #
# One placed off-page connector per row, in a format-independent shape the
# matcher reads (OPCTag for PostProc, GUID for DEXPI), plus Bronze lineage.
OPC_STRUCT = StructType([
    StructField("eid", StringType()),          # OPC element id (the stitch endpoint)
    StructField("home", StringType()),         # this sheet's DrawingNumber (PostProc)
    StructField("paired", StringType()),       # PairedDrawingNumber back-reference
    StructField("opctag", StringType()),       # the cross-sheet key (PostProc)
    StructField("guid_self", StringType()),    # DEXPI: this OPC's GUID
    StructField("guid_mate", StringType()),    # DEXPI: SP_pairedWithID
    *_LINEAGE,
])

OPC_COLUMNS = [f.name for f in OPC_STRUCT.fields]

# --- Stage C: harvest UDF boundary shape (Workstream 2) --------------------- #
# The UDF that harvests OPCs per drawing must carry BOTH the matcher keys AND
# the descriptive entity fields to the driver, or the entity fields are dropped
# at the Spark boundary (the lean OPC_STRUCT above would project them away).
# This is the harvest struct assemble_job._harvest_udf returns; assemble_opcs
# reads these dicts and match_pairs still uses only the matcher subset. Distinct
# from OPC_ENTITY_STRUCT (the persisted table): this is a transport shape and
# has no on_segment (that is joined from silver_components in the job, not
# harvested). class_uri here is the harvest record's key; it lands in the entity
# table's component_class_uri column.
OPC_HARVEST_STRUCT = StructType([
    StructField("eid", StringType()),
    StructField("home", StringType()),
    StructField("paired", StringType()),
    StructField("opctag", StringType()),
    StructField("guid_self", StringType()),
    StructField("guid_mate", StringType()),
    StructField("opc_type", StringType()),         # entity field (Step 1 harvest)
    StructField("flow_direction", StringType()),   # entity field
    StructField("class_uri", StringType()),        # entity field -> component_class_uri
    StructField("to_from_dir", StringType()),      # entity field
    StructField("to_from_text", StringType()),     # entity field
    *_LINEAGE,
])

OPC_HARVEST_COLUMNS = [f.name for f in OPC_HARVEST_STRUCT.fields]

# --- silver_off_page_connectors: Workstream 2 per-OPC ENTITY (Gold feed) ---- #
# One row per PLACED off-page connector (matched AND unmatched — both get a Gold
# OffPageConnector node; only matched also get the OffPage edge above). Distinct
# from OPC_STRUCT: that is the lean, matcher-only HARVEST shape; this is the
# persisted ENTITY the Gold C_OFF_PAGE_CONNECTOR node projects from. Sources:
#   (a) descriptive fields — from the (widened) Stage C harvest, straight off the
#       XML element; opc_type drives Gold's `terminates` piping-vs-instrument
#       branch; flow_direction/component_class_uri are DEXPI-only.
#   (b) on_segment — NOT from the harvest (parse-only, no Pipeline.run()) but a
#       Spark join in assemble_job.py: opc_id -> silver_components.component_id,
#       take its segment_id (an OPC lands in silver_components with kind
#       containing "OffPageConnector"). None for instrument OPCs not on a pipe.
# See silver_opc_feed_change_spec.md Step 4. JOIN-KEY: opc_id (eid) must equal
# silver_components.component_id — confirm reconstruct.py doesn't _cid()-transform it.
OPC_ENTITY_STRUCT = StructType([
    StructField("opc_id", StringType(), nullable=False),   # = eid; join key to silver_components
    StructField("tag", StringType()),                      # opctag (often None for DEXPI)
    StructField("opc_type", StringType()),                 # OPCType classification (both formats)
    StructField("flow_direction", StringType()),           # DEXPI FlowIn/FlowOut; None for PostProc
    StructField("component_class_uri", StringType()),      # DEXPI sandbox URI pass-through; None PostProc
    StructField("to_from_dir", StringType()),              # "TO"/"FROM" narrative label (not a flow role)
    StructField("to_from_text", StringType()),             # mate name, provenance
    StructField("on_segment", StringType()),               # terminates target (join); None if not on a pipe
    StructField("paired_drawing", StringType()),           # paired back-reference (reference/QA)
    StructField("matched", BooleanType()),                 # in a matched pair -> has an OffPage edge
    *_LINEAGE,
])

OPC_ENTITY_TABLE = "silver_off_page_connectors"
OPC_ENTITY_COLUMNS = [f.name for f in OPC_ENTITY_STRUCT.fields]
# NOTE: deliberately NOT added to SILVER_TABLES — that dict drives Stage A+B's
# reconstruction explode (four fields only). assemble_job.py writes this table
# directly via OPC_ENTITY_TABLE / OPC_ENTITY_STRUCT / OPC_ENTITY_COLUMNS, and
# gold reads it via spark_bridge.GRAIN_TABLE, so no registry entry is needed.

# --- silver_cdc: Stage E object-grain deltas (silver_spec §3.5) ------------- #
# One row per changed object across two Bronze versions of a drawing. These are
# exactly the New/Modified/Deleted interval open/close events Gold consumes.
CDC_STRUCT = StructType([
    StructField("cdc_id", StringType(), nullable=False),
    StructField("grain", StringType()),            # segment|component|equipment|off_page_connector
    StructField("drawing_number", StringType()),
    StructField("anchor", StringType()),           # the UID-free identity anchor
    StructField("change_type", StringType()),      # New|Modified|Deleted
    StructField("old_uid", StringType()),          # audit only (may be re-minted)
    StructField("new_uid", StringType()),
    StructField("old_content_hash_eng", StringType()),
    StructField("new_content_hash_eng", StringType()),
    StructField("old_content_hash_audit", StringType()),
    StructField("new_content_hash_audit", StringType()),
    StructField("old_version", StringType()),       # content_hash of the older Bronze version
    StructField("new_version", StringType()),
    StructField("old_revision", StringType()),      # drawing_revision label (e.g. F)
    StructField("new_revision", StringType()),
    StructField("detail", StringType()),
    StructField("project_code", StringType()),
    StructField("source_format", StringType()),
    StructField("transaction_ts", TimestampType()),
])

CDC_TABLE = "silver_cdc"
CDC_COLUMNS = [f.name for f in CDC_STRUCT.fields]
