"""Vendored subset of pidsys for Silver Stage A/B (reconstruction only).

Only master data + connectivity reconstruction is vendored — NOT walk.py /
validate.py (systemization). Silver is use-case-neutral: it computes master
data and connectivity, never commissioning systems (silver_spec §1.2, §5).
"""
from .reconstructed import ReconstructedGraph, BOUNDARY_CLASSES
__all__ = ["ReconstructedGraph", "BOUNDARY_CLASSES"]
