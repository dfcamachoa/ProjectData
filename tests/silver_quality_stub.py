"""Re-housed verbatim (the two functions `silver/cdc.py` imports from
`silver/quality.py`) from the ProjectData repo, read via the project's
widened GitHub sync (2026-09-06) -- NOT re-derived. `silver/quality.py`
itself is Stage D's full Great-Expectations-style suite (out of scope for
this demo); these two are its shared predicates that Stage E's `cdc.py`
also depends on, so they are re-housed on their own rather than pulling in
the whole quality module.
"""
from __future__ import annotations

import re


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def is_uncomposable_seg_tag(v) -> bool:
    """True when a ``seg_tag`` is NOT a real composable line tag -- a
    connector / off-page pseudo-tag or a placeholder description rather than
    the reconstructed ``[dia-]fluid-lineCore[-class][-insul]`` line number
    (silver_spec Sec3.5). Real Project-B examples that must flag:
    ``Conn to process/supply-``, ``Pneumatic-``, ``PG-Utility, Secondary-``.
    The three symptoms, any of which disqualifies it:

      * empty / absent (can't anchor a line at all);
      * contains whitespace or a description separator (real tags are code strings);
      * ends with a separator ``- , /`` (composition left a trailing empty field);
      * has no numeric line core (no run of >=3 digits: no unit+sequence).

    Exposed as a shared predicate so line-grain CDC can exclude exactly the
    same rows Stage D quarantines -- one rule, two consumers.
    """
    s = "" if v is None else str(v).strip()
    if not s:
        return True
    if any(ch.isspace() for ch in s):
        return True
    if s.endswith(("-", ",", "/")):
        return True
    if re.search(r"\d{3,}", s) is None:
        return True
    return False
