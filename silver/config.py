"""Configuration for the Silver reconstruction layer.

Mirrors bronze/config.py: everything that varies between projects / environments
lives here as data. See specs/silver_spec.md (Phase-1: parse + reconstruct +
persist). Stages C (assembly), D (Great Expectations) and E (CDC) are not built
in this phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SilverConfig:
    """Top-level configuration for a Silver reconstruction run."""

    # Bronze input (align to bronze/config.py BronzeConfig.table_name).
    bronze_table: str = "bronze.pid_documents"
    bronze_path: Optional[str] = None            # delta path alternative to a catalog name

    # Output schema/database for the Silver tables (Hive/Derby, spec §8.4).
    silver_schema: str = "silver"

    # Optional project reference data (Reference_Data.xlsx) for tag composition.
    # When absent, master_data falls back to the built-in PROJECT_B preset.
    refdata_path: Optional[str] = None

    # Write mode for this phase. "overwrite" rebuilds the Silver tables from
    # Bronze each run (idempotent, no CDC yet); "append" accumulates.
    write_mode: str = "overwrite"

    # Register named tables in the embedded Derby Hive metastore (spec §8.4).
    # MUST match the Bronze run's metastore/warehouse so bronze_table resolves.
    enable_hive: bool = True

    # Size-aware fan-out (spec §6.1): repartition Bronze by file_size_bytes so a
    # single core doesn't choke on the multi-MB sheets. 0 disables (keep input
    # partitioning). For local mode set to a small multiple of your cores.
    repartition_by_size_buckets: int = 0

    partition_by: List[str] = field(default_factory=list)

    def resolve_bronze(self) -> str:
        """Identifier used to read Bronze (path-based or catalog name)."""
        if self.bronze_path:
            return f"delta.`{self.bronze_path}`"
        return self.bronze_table

    def table(self, name: str) -> str:
        """Fully-qualified Silver table name, e.g. silver.silver_components."""
        return f"{self.silver_schema}.{name}" if self.silver_schema else name
