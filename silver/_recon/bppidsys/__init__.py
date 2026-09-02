"""bppidsys — project-B adapter: parse an INGR ISO-15926 PostProc export
(OriginatingSystem="SPPID") and build a valve-aware graph, emitting the same
PipelineResult contract as pidtool so the unchanged pidsys core consumes it.
"""
from .model import Doc
from .pipeline import Pipeline, PipelineResult

__all__ = ["Doc", "Pipeline", "PipelineResult"]
__version__ = "0.1.0"
