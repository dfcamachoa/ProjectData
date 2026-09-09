"""Re-housed verbatim from the ProjectData repo's `silver/demo_cdc.py`
(read via the project's widened GitHub sync, 2026-09-06) -- NOT re-derived.

Synthetic two-revision Project-B P&ID set for the Stage-E CDC narrative.

`write_narrative(dest)` writes four PostProc XMLs -- two drawings at Rev C
(issued for HAZOP) and Rev D (re-issued for design) -- into ``dest/rev1_C``
and ``dest/rev2_D``. Every element UID is re-minted between the revisions (as
SmartPlant does on re-export), so Stage E must see past the churn to the real
engineering change. The change manifest (by discipline) is in
``narrative_project_b/NARRATIVE.md``; the expected result is 15 real deltas
and 0 false positives. These are synthetic, scrubbed fixtures -- illustrative
structure, confirm names against real exports.
"""
from __future__ import annotations

import os
from xml.sax.saxutils import escape


def _e(v):
    """XML-escape an attribute value -- & < > and the inch-mark \" -- so the
    file is well-formed and parses with the plain parser (no lxml recovery
    needed)."""
    return escape(str(v), {'"': "&quot;"})


D1 = "216097C-A22-PID-0021-0015-001"      # BFW SUPPLY TO STEAM DRUM
D2 = "216097C-A22-PID-0021-0016-001"      # STEAM GENERATION & BLOWDOWN

_REV_C = [("C", "2024/03/03", "ISSUED FOR HAZOP")]
_REV_D = [("C", "2024/03/03", "ISSUED FOR HAZOP"),
          ("D", "2024/07/15", "ISSUED FOR DESIGN (POST-HAZOP)")]

# segment tuple: (TagName, ItemTag, fluid, TagSuffix, PClass, Dia, InsulPurpose,
#                 InsulType, InsulThick, [ (elem_tag, ComponentClass, ItemTag?) ... ])
_D1_SEG_C = [
    ("WBF-2215101", "WBF-2215101", "WBF", "01", "B242A", '2"', "H", "MW", "25",
     [("PipingComponent", "GateValve", None), ("PipingComponent", "CheckValve", None)]),
    ("WBF-2215102", "WBF-2215102", "WBF", "02", "D341H", '3/4"', "N", None, None,
     [("PipingComponent", "GlobeValve", None)]),
    ("WBF-2215103", "WBF-2215103", "WBF", "03", "B242A", '4"', "H", "MW", "50",
     [("PipingComponent", "GateValve", None)]),
    ("WBF-2215104", "WBF-2215104", "WBF", "04", "D341H", '3/4"', "N", None, None,
     [("PipingComponent", "GateValve", None)]),
]
_D1_EQ_C = [("V-2201", 4), ("P-2201A", 2)]

_D1_SEG_D = [
    ("WBF-2215101", "WBF-2215101", "WBF", "01", "B242A", '2"', "H", "MW", "50",   # insul_thick 25->50
     [("PipingComponent", "GateValve", None), ("PipingComponent", "GateValve", None)]),
    ("WBF-2215102", "WBF-2215102", "WBF", "02", "D341H", '3/4"', "N", None, None,  # globe->control + PT
     [("PipingComponent", "ControlValve", None),
      ("ProcessInstrument", "PressureTransmitter", "PT-2201")]),
    ("WBF-2215103", "WBF-2215103", "WBF", "03", "B242A", '4"', "H", "MW", "50",   # unchanged, re-drawn
     [("PipingComponent", "GateValve", None)]),
    # WBF-2215104 removed; new warm-up bypass added:
    ("WBF-2215106", "WBF-2215106", "WBF", "06", "D341H", '3/4"', "N", None, None,
     [("PipingComponent", "GlobeValve", None)]),
]
_D1_EQ_D = [("V-2201", 5), ("P-2201A", 2), ("P-2201B", 2)]   # +nozzle, unchanged, +new spare

_D2_SEG_C = [
    ("SM-2203906", "SM-2203906", "SM", "06", "G400S", '6"', "H", "MW", "50",
     [("PipingComponent", "GateValve", None)]),
    ("SC-2204101", "SC-2204101", "SC", "01", "D341H", '2"', "N", None, None,
     [("PipingComponent", "GlobeValve", None)]),
]
_D2_EQ_C = [("V-2202", 3)]

_D2_SEG_D = [
    ("SM-2203906", "SM-2203906", "SM", "06", "G400S", '6"', "H", "MW", "50",      # unchanged, re-drawn
     [("PipingComponent", "GateValve", None)]),
    ("SC-2204101", "SC-2204101", "SC", "01", "D341H", '2"', "N", None, None,      # unchanged, re-drawn
     [("PipingComponent", "GlobeValve", None)]),
    ("SM-2203910", "SM-2203910", "SM", "10", "G400S", '2"', "N", None, None,      # new safety vent + PSV
     [("PipingComponent", "SafetyValveOrFitting", "PSV-2201")]),
]
_D2_EQ_D = [("V-2202", 3)]


class _Ids:
    def __init__(self, start=0):
        self.n = start

    def __call__(self, prefix):
        self.n += 1
        return f"{prefix}-{self.n:04d}"


def _ga(name, val):
    return f'<GenericAttribute Name="{_e(name)}" Value="{_e(val)}"/>' if val is not None else ""


def _comp(nid, elem_tag, cls, itemtag=None):
    inner = f'<GenericAttributes>{_ga("ItemTag", itemtag)}</GenericAttributes>' if itemtag else ""
    return f'<{elem_tag} ID="{nid("PC")}" ComponentClass="{_e(cls)}">{inner}</{elem_tag}>'


def _seg(nid, tagname, itemtag, fluid, suffix, pclass, dia, ip, itype, ithk, comps):
    gas = "".join([_ga("OperFluidCode", fluid), _ga("ItemTag", itemtag),
                   _ga("TagSuffix", suffix), _ga("PipingMaterialsClass", pclass),
                   _ga("NominalDiameter", dia), _ga("InsulPurpose", ip),
                   _ga("InsulType", itype), _ga("InsulThick", ithk)])
    body = "".join(_comp(nid, *c) for c in comps)
    return (f'<PipingNetworkSegment ID="{nid("SG")}" TagName="{_e(tagname)}">'
            f'<GenericAttributes>{gas}</GenericAttributes>'
            f'<CenterLine><Coordinate X="0" Y="0"/><Coordinate X="100" Y="0"/></CenterLine>'
            f'{body}</PipingNetworkSegment>')


def _equip(nid, itemtag, nozzles):
    noz = "".join(f'<Nozzle ID="{nid("NZ")}" ComponentClass="Nozzle"/>' for _ in range(nozzles))
    return (f'<Equipment ID="{nid("EQ")}"><GenericAttributes>{_ga("ItemTag", itemtag)}'
            f'</GenericAttributes>{noz}</Equipment>')


def _drawing(nid, docno, title, current_rev, rev_history, equipment, segments):
    labels = "".join(
        f'<Label ComponentClass="TitleBlockRevision"><GenericAttributes>'
        f'{_ga("Revision.StatusType", "Revision")}{_ga("Revision.RevisionNumber", r)}'
        f'{_ga("Revision.TP_RevisionData", d)}{_ga("Revision.Text", t)}'
        f'</GenericAttributes></Label>' for (r, d, t) in rev_history)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<PlantModel><PlantInformation OriginatingSystem="SPPID"/>'
        f'<Drawing Name="{_e(docno)}" Revision="{_e(current_rev)}" Title="{_e(title)}"/>{labels}'
        + "".join(_equip(nid, *e) for e in equipment)
        + f'<PipingNetworkSystem ID="{nid("SY")}">'
        + "".join(_seg(nid, *s) for s in segments)
        + '</PipingNetworkSystem></PlantModel>')


def write_narrative(dest: str) -> dict:
    """Write the four narrative XMLs under ``dest/rev1_C`` and ``dest/rev2_D``.
    Returns {"rev1_C": path, "rev2_D": path}."""
    rev1, rev2 = os.path.join(dest, "rev1_C"), os.path.join(dest, "rev2_D")
    os.makedirs(rev1, exist_ok=True)
    os.makedirs(rev2, exist_ok=True)

    nid = _Ids(0)                                   # Rev C ids
    _put(rev1, D1, _drawing(nid, D1, "BFW SUPPLY TO STEAM DRUM", "C", _REV_C, _D1_EQ_C, _D1_SEG_C))
    _put(rev1, D2, _drawing(nid, D2, "STEAM GENERATION & BLOWDOWN", "C", _REV_C, _D2_EQ_C, _D2_SEG_C))

    nid = _Ids(10000)                               # Rev D ids -- all re-minted
    _put(rev2, D1, _drawing(nid, D1, "BFW SUPPLY TO STEAM DRUM", "D", _REV_D, _D1_EQ_D, _D1_SEG_D))
    _put(rev2, D2, _drawing(nid, D2, "STEAM GENERATION & BLOWDOWN", "D", _REV_D, _D2_EQ_D, _D2_SEG_D))
    return {"rev1_C": rev1, "rev2_D": rev2}


def _put(folder, docno, xml):
    with open(os.path.join(folder, f"{docno}.xml"), "w") as fh:
        fh.write(xml)
