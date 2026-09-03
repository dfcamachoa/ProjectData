"""Unit tests for Silver Stage C — cross-document OPC assembly (silver_spec §3.3).

Spark-free: exercises the per-drawing harvest and the plant-level matcher/reduce
(OPCTag for PostProc, GUID for DEXPI), the OffPage row shape, and the open-boundary
flag for an unmatched OPC.
"""
from __future__ import annotations

from silver.assemble import assemble_opcs, harvest_opcs_document


def _postproc_sheet(drawing, opctag, paired, opc_id):
    return (
        '<?xml version="1.0"?>'
        '<PlantModel>'
        '<Drawing><GenericAttributes>'
        f'<GenericAttribute Name="DrawingNumber" Value="{drawing}"/>'
        '</GenericAttributes></Drawing>'
        # a TagName-bearing segment makes Doc.is_postproc() true
        '<PipingNetworkSystem><PipingNetworkSegment ID="SG1" TagName="PG-1">'
        '<GenericAttributes><GenericAttribute Name="OperFluidCode" Value="PG"/>'
        '</GenericAttributes></PipingNetworkSegment></PipingNetworkSystem>'
        f'<PipeConnectorSymbol ID="{opc_id}" ComponentClass="OPC"><GenericAttributes>'
        f'<GenericAttribute Name="OPCTag" Value="{opctag}"/>'
        f'<GenericAttribute Name="PairedDrawingNumber" Value="{paired}"/>'
        '</GenericAttributes></PipeConnectorSymbol>'
        '</PlantModel>'
    ).encode()


# --- harvest ---------------------------------------------------------------

def test_harvest_returns_opc_records_with_lineage():
    recs = harvest_opcs_document(_postproc_sheet("D1", "4782", "D2", "OPC-A"),
                                 "POSTPROC", bronze_id="b1", document_number="D1",
                                 project_code="216097C")
    assert len(recs) == 1
    r = recs[0]
    assert r["eid"] == "OPC-A" and r["opctag"] == "4782"
    assert r["home"] == "D1" and r["paired"] == "D2"
    assert r["drawing_number"] == "D1" and r["bronze_id"] == "b1"


def test_harvest_malformed_is_empty_not_raising():
    assert harvest_opcs_document(b"not xml", "POSTPROC") == []


# --- match: PostProc OPCTag across two sheets ------------------------------

def test_two_sheets_same_opctag_stitch_into_one_offpage_edge():
    a = harvest_opcs_document(_postproc_sheet("D1", "4782", "D2", "OPC-A"),
                              "POSTPROC", bronze_id="b1", document_number="D1")
    b = harvest_opcs_document(_postproc_sheet("D2", "4782", "D1", "OPC-B"),
                              "POSTPROC", bronze_id="b2", document_number="D2")
    res = assemble_opcs(a + b)
    assert res["stats"]["opc_stitched"] == 1
    assert res["stats"]["opc_offset"] == 0
    edge = res["offpage_connections"][0]
    assert {edge["from_id"], edge["to_id"]} == {"OPC-A", "OPC-B"}
    assert edge["conn_type"] == "OffPage"
    assert edge["derived"] is True
    assert edge["flow_sense"] == "none"
    assert edge["connection_id"].startswith("sha256:")


# --- match: DEXPI GUID pairing --------------------------------------------

def test_dexpi_guid_pairs_stitch():
    recs = [
        {"eid": "SPaaa", "guid_self": "aaa", "guid_mate": "bbb", "opctag": None,
         "home": None, "paired": None, "drawing_number": "D1", "bronze_id": "b1"},
        {"eid": "SPbbb", "guid_self": "bbb", "guid_mate": "aaa", "opctag": None,
         "home": None, "paired": None, "drawing_number": "D2", "bronze_id": "b2"},
    ]
    res = assemble_opcs(recs)
    assert res["stats"]["opc_stitched"] == 1
    assert {res["offpage_connections"][0]["from_id"],
            res["offpage_connections"][0]["to_id"]} == {"SPaaa", "SPbbb"}


# --- open boundary: an OPC whose mate is not loaded ------------------------

def test_lone_opc_is_an_open_boundary_not_an_error():
    a = harvest_opcs_document(_postproc_sheet("D1", "9999", "D7", "OPC-Z"),
                              "POSTPROC", bronze_id="b1", document_number="D1")
    res = assemble_opcs(a)
    assert res["stats"]["opc_stitched"] == 0
    assert res["stats"]["opc_offset"] == 1
    assert not res["offpage_connections"]
    b = res["open_boundaries"][0]
    assert b["flag"] == "opc_open_boundary"
    assert b["severity"] == "info" and b["gate"] == "flag" and b["stage"] == "C"
    assert b["object_id"] == "OPC-Z"
    assert "9999" in b["detail"] and "D7" in b["detail"]


def test_mixed_matched_and_open_boundary():
    a = harvest_opcs_document(_postproc_sheet("D1", "100", "D2", "A"), "POSTPROC",
                              document_number="D1")
    b = harvest_opcs_document(_postproc_sheet("D2", "100", "D1", "B"), "POSTPROC",
                              document_number="D2")
    lone = harvest_opcs_document(_postproc_sheet("D1", "200", "D9", "C"), "POSTPROC",
                                 document_number="D1")
    res = assemble_opcs(a + b + lone)
    assert res["stats"]["opc_stitched"] == 1
    assert res["stats"]["opc_offset"] == 1
