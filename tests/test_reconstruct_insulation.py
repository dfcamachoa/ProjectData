"""Regression: insulation (and ItemTag) must survive PostProc extraction.

The bppidsys (PostProc) adapter copies only a whitelist of segment attributes into
`Component.attrs`; insulation was missing from it, so the Silver `insul_*` columns
came back null even though the composed `seg_tag` carried the `-H` purpose suffix —
a Bronze->Silver drift the §3h lineage trace surfaced. This test pins the fix by
running the real Spark-free Silver core over a minimal PostProc segment.
"""
from __future__ import annotations

from silver.reconstruct import reconstruct_document

_POSTPROC_SEGMENT = b"""<?xml version="1.0"?>
<PlantModel>
  <PlantInformation OriginatingSystem="SmartPlant P&amp;ID"/>
  <PipingNetworkSystem ID="PNS1">
    <GenericAttributes><GenericAttribute Name="OperFluidCode" Value="SM"/>
      <GenericAttribute Name="TagSequenceNo" Value="1404001"/></GenericAttributes>
    <PipingNetworkSegment ID="SG1" TagName="040-SM-Capillary">
      <GenericAttributes>
        <GenericAttribute Name="OperFluidCode" Value="SM"/>
        <GenericAttribute Name="PipingMaterialsClass" Value="F242S"/>
        <GenericAttribute Name="ItemTag" Value="SM-1404001"/>
        <GenericAttribute Name="TagSuffix" Value="01"/>
        <GenericAttribute Name="InsulPurpose" Value="H"/>
        <GenericAttribute Name="InsulType" Value="MW"/>
        <GenericAttribute Name="InsulThick" Value="50"/>
      </GenericAttributes>
      <CenterLine><Coordinate X="0" Y="0"/><Coordinate X="10" Y="0"/></CenterLine>
    </PipingNetworkSegment>
  </PipingNetworkSystem>
</PlantModel>"""


def _segment_row():
    out = reconstruct_document(_POSTPROC_SEGMENT, "POSTPROC", bronze_id="b1",
                               content_hash="h1", document_number="D1",
                               project_code="216097C")
    assert len(out["segments"]) == 1
    return out["segments"][0]


def test_postproc_insulation_reaches_the_silver_row():
    s = _segment_row()
    assert s["insul_purpose"] == "H"          # was None before the whitelist fix
    assert s["insul_type"] == "MW"
    assert s["insul_thick"] == "50"


def test_postproc_seg_tag_suffix_matches_the_insul_purpose_column():
    # the whole point: the -H suffix and the insul_purpose column are one fact
    s = _segment_row()
    assert s["seg_tag"] == "SM-1404001-F242S-H"
    assert s["seg_tag"].rsplit("-", 1)[-1] == s["insul_purpose"]


def test_postproc_item_tag_also_survives():
    assert _segment_row()["item_tag"] == "SM-1404001"
