"""Configuration for the Gold bi-temporal layer.

Mirrors `bronze/config.py` / `silver/config.py`: everything that varies
between projects/environments lives here as data, and a run reads Bronze +
Silver by name (or path) and writes Gold Delta tables by name, exactly the
same way Silver reads Bronze.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class GoldConfig:
    """Top-level configuration for a Gold bi-temporal run."""

    # Bronze input (align to bronze/config.py BronzeConfig.table_name) — Gold
    # reads Bronze directly for drawing_revision_date (valid-time source),
    # exactly as append_gold_cells.py's Bridge 1 already did.
    bronze_table: str = "bronze.pid_documents"
    bronze_path: Optional[str] = None

    # Silver input schema (align to silver/config.py SilverConfig.silver_schema)
    # — Gold reads silver_cdc plus, for line grain, silver_segments /
    # silver_components / silver_connections.
    silver_schema: str = "silver"

    # Output schema/database for the Gold tables (Hive/Derby, spec §8.4).
    gold_schema: str = "gold"

    # Register named tables in the embedded Derby Hive metastore (spec §8.4).
    # MUST match the Bronze/Silver run's metastore/warehouse so bronze_table
    # and silver_schema resolve.
    enable_hive: bool = True

    def resolve_bronze(self) -> str:
        """Identifier used to read Bronze (path-based or catalog name)."""
        if self.bronze_path:
            return f"delta.`{self.bronze_path}`"
        return self.bronze_table

    def silver_table(self, name: str) -> str:
        """Fully-qualified Silver table name, e.g. silver.silver_cdc."""
        return f"{self.silver_schema}.{name}" if self.silver_schema else name

    def table(self, name: str) -> str:
        """Fully-qualified Gold table name, e.g. gold.gold_objects."""
        return f"{self.gold_schema}.{name}" if self.gold_schema else name
