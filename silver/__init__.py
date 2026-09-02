"""Silver layer — parse + reconstruct Bronze P&ID documents into typed Delta
tables (specs/silver_spec.md).

Phase-1 (this build): Stage A (parse/shred) + Stage B (topology reconstruction)
+ persist to silver_components / silver_segments / silver_connections /
silver_equipment. Stages C (assembly), D (Great Expectations) and E (CDC) are
specified but not yet built.

The reconstruction algorithm is the validated pidtool / bppidsys / pidsys code,
vendored unchanged under ``silver/_recon/`` and re-housed, not re-derived.
"""
from .config import SilverConfig
from .reconstruct import reconstruct_document

__all__ = ["SilverConfig", "reconstruct_document"]
