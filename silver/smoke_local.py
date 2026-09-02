"""Spark-free smoke test for the Silver reconstruction core.

Runs `reconstruct_document` over the repo sample_data (both formats) and prints
per-drawing row counts for the four Silver tables. This exercises Stage A+B —
the risky, algorithmic part — without needing Spark, so it runs anywhere.

    python -m silver.smoke_local
    python -m silver.smoke_local /path/to/a/real_sheet.xml DEXPI

The full Spark job (silver/spark_job.py) writes the actual Delta tables; run it
in the WSL/Spark environment with `python -m silver.cli reconstruct`.
"""
from __future__ import annotations

import os
import sys

from .reconstruct import reconstruct_document

# (path, source_format) — the committed synthetic fixtures.
SAMPLES = [
    ("sample_data/projectA_dexpi_01010.xml", "DEXPI"),
    ("sample_data/projectA_dexpi_02231.xml", "DEXPI"),
    ("sample_data/projectB_postproc_0001.xml", "POSTPROC"),
    ("sample_data/projectB_postproc_0012.xml", "POSTPROC"),
]


def _run(path: str, fmt: str) -> None:
    with open(path, "rb") as fh:
        data = fh.read()
    out = reconstruct_document(data, fmt, bronze_id="smoke",
                               content_hash="sha256:smoke",
                               document_number=os.path.basename(path))
    print(f"{fmt:8} {os.path.basename(path):28} "
          f"components={len(out['components']):4} segments={len(out['segments']):4} "
          f"connections={len(out['connections']):5} equipment={len(out['equipment']):3}")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2:
        _run(argv[0], argv[1])
        return 0
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel, fmt in SAMPLES:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            _run(p, fmt)
        else:
            print(f"(missing) {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
