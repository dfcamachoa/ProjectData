"""Unit tests for the Spark-independent Bronze core (bronze/header.py), v0.2.

Plain Python — no PySpark. Covers the format-detection ladder, the per-format
document-number and revision-history extraction (spec §3.1, §6), project_code
derivation, malformed-tolerance, and the self-describing hash (spec §5.2).
"""
from __future__ import annotations

import pathlib

import pytest

from bronze.header import (
    FORMAT_DEXPI,
    FORMAT_POSTPROC,
    METHOD_APPLICATION,
    METHOD_SEGMENT_TAGNAME,
    METHOD_UNKNOWN,
    PC_SOURCE_DOCUMENT_NUMBER,
    PC_SOURCE_UNKNOWN,
    compute_hash,
    parse_header,
)

SAMPLE_DIR = pathlib.Path(__file__).resolve().parent.parent / "sample_data"


def _read(name: str) -> bytes:
    return (SAMPLE_DIR / name).read_bytes()


# --- DEXPI: EPC doc number + RevRow revision history (spec §3.1, §6) --------

def test_dexpi_epc_docnumber_client_and_revrow():
    info = parse_header(_read("projectA_dexpi_02231.xml"))
    assert info.source_format == FORMAT_DEXPI
    # both formats are OriginatingSystem=SPPID; DEXPI is decided by Application="Dexpi"
    assert info.format_detection_method == METHOD_APPLICATION
    assert info.originating_system == "SPPID"
    # EPC number from OperationCenterDocNo; client from DrawingNumber
    assert info.document_number == "215777C-36292-PID-0031-02231"
    assert info.client_document_number == "362-92-PR-PID-02231"
    # current revision = highest-numbered RevRow (RevRow2 = C), date verbatim
    assert info.drawing_revision == "C"
    assert info.drawing_revision_date == "12APR24"
    # project_code = leading token of the EPC document number
    assert info.project_code == "215777C"
    assert info.project_code_source == PC_SOURCE_DOCUMENT_NUMBER
    assert info.header_parse_ok is True


# --- PostProc: Drawing/@Revision + matching revision Label (spec §6) --------

def test_postproc_drawing_revision_and_label_date():
    info = parse_header(_read("projectB_postproc_0012.xml"))
    assert info.source_format == FORMAT_POSTPROC
    # PostProc has OriginatingSystem=SPPID and NO Application; classified by TagName
    assert info.format_detection_method == METHOD_SEGMENT_TAGNAME
    assert info.originating_system == "SPPID"
    # both numbers are Drawing/@Name (identical in project B)
    assert info.document_number == "216097C-A22-PID-0021-0012-001"
    assert info.client_document_number == "216097C-A22-PID-0021-0012-001"
    # current revision stated on Drawing/@Revision (G); date from matching Label
    assert info.drawing_revision == "G"
    assert info.drawing_revision_date == "2024/06/28"   # verbatim YYYY/MM/DD
    assert info.project_code == "216097C"
    assert info.header_parse_ok is True


def test_postproc_picks_the_matching_label_not_the_first():
    # the file has a B label and a G label; current rev is G, so date must be G's
    info = parse_header(_read("projectB_postproc_0012.xml"))
    assert info.drawing_revision_date == "2024/06/28"
    assert info.drawing_revision_date != "2024/03/03"


def test_postproc_revision_label_wrapper_tag_is_not_hardcoded():
    # Revision.* fields grouped by ANY enclosing element, not just <Label> — here
    # the wrapper is <Component>. The date must still resolve for current rev F.
    xml = b"""<?xml version="1.0"?>
    <PlantModel>
      <PlantInformation OriginatingSystem="SPPID"/>
      <Drawing Name="216097C-A14-PID-0005-001" Revision="F"/>
      <Component>
        <GenericAttribute Name="Revision.StatusType" Value="Revision"/>
        <GenericAttribute Name="Revision.RevisionNumber" Value="E"/>
        <GenericAttribute Name="Revision.TP_RevisionData" Value="2024/05/01"/>
      </Component>
      <Component>
        <GenericAttribute Name="Revision.StatusType" Value="Revision"/>
        <GenericAttribute Name="Revision.RevisionNumber" Value="F"/>
        <GenericAttribute Name="Revision.TP_RevisionData" Value="2024/09/15"/>
      </Component>
      <PipingNetworkSystem>
        <PipingNetworkSegment ID="SG" TagName="PG-1"/>
      </PipingNetworkSystem>
    </PlantModel>"""
    info = parse_header(xml)
    assert info.source_format == FORMAT_POSTPROC
    assert info.drawing_revision == "F"
    assert info.drawing_revision_date == "2024/09/15"


def test_postproc_takes_latest_revision_number_and_date_as_a_pair():
    # The revision NUMBER and DATE are the companion pair of the latest logged
    # revision (max date), regardless of Drawing/@Revision or document order.
    # Here the latest date (2026/05/08) belongs to E, and the labels are out of
    # order in the file — the parser must still pick E, not B and not the header F.
    xml = b"""<?xml version="1.0"?>
    <PlantModel>
      <PlantInformation OriginatingSystem="SPPID"/>
      <Drawing Name="216097C-A14-PID-0005-001" Revision="F"/>
      <PipingNetworkSystem>
        <PipingNetworkSegment ID="SG" TagName="PG-1"/>
      </PipingNetworkSystem>
      <Rev>
        <GenericAttribute Name="Revision.RevisionNumber" Value="E"/>
        <GenericAttribute Name="Revision.TP_RevisionData" Value="2026/05/08"/>
      </Rev>
      <Rev>
        <GenericAttribute Name="Revision.RevisionNumber" Value="B"/>
        <GenericAttribute Name="Revision.TP_RevisionData" Value="2024/03/03"/>
      </Rev>
    </PlantModel>"""
    info = parse_header(xml)
    assert info.source_format == FORMAT_POSTPROC
    assert info.drawing_revision == "E"                 # companion of the latest date
    assert info.drawing_revision_date == "2026/05/08"   # latest, not B's, not header F


def test_postproc_labels_after_the_first_segment_are_still_read():
    # Budget fix: a tagged segment appears BEFORE the revision labels; the scan must
    # keep reading through the header region rather than stopping at the segment.
    xml = b"""<?xml version="1.0"?>
    <PlantModel>
      <PlantInformation OriginatingSystem="SPPID"/>
      <Drawing Name="216097C-A14-PID-0005-001" Revision="F"/>
      <PipingNetworkSystem>
        <PipingNetworkSegment ID="SG" TagName="PG-1"/>
      </PipingNetworkSystem>
      <Component>
        <GenericAttribute Name="Revision.RevisionNumber" Value="F"/>
        <GenericAttribute Name="Revision.TP_RevisionData" Value="2024/09/15"/>
      </Component>
    </PlantModel>"""
    info = parse_header(xml)
    assert info.source_format == FORMAT_POSTPROC
    assert info.drawing_revision_date == "2024/09/15"


# --- format detection: both formats are OriginatingSystem=SPPID -------------

def test_sppid_alone_does_not_imply_postproc():
    # Regression: OriginatingSystem=SPPID with NO Application and NO tagged segment
    # must NOT be classified as a format on the strength of SPPID (it is not a signal).
    xml = b"""<?xml version="1.0"?>
    <PlantModel>
      <PlantInformation OriginatingSystem="SPPID"/>
      <Drawing Name="Z-1" Revision="1"/>
    </PlantModel>"""
    info = parse_header(xml)
    assert info.source_format is None
    assert info.format_detection_method == METHOD_UNKNOWN


def test_application_marker_wins_over_segment_tagname():
    # A DEXPI file (Application="Dexpi", OriginatingSystem="SPPID") that also happens
    # to carry a tagged segment must resolve DEXPI — Application precedes the fallback.
    xml = b"""<?xml version="1.0"?>
    <PlantModel>
      <PlantInformation OriginatingSystem="SPPID" Application="Dexpi"/>
      <Drawing Number="215777C-1" Revision="A"/>
      <PipingNetworkSystem>
        <PipingNetworkSegment ID="SG" TagName="PG-1"/>
      </PipingNetworkSystem>
    </PlantModel>"""
    info = parse_header(xml)
    assert info.source_format == FORMAT_DEXPI
    assert info.format_detection_method == METHOD_APPLICATION


# --- format detection fallbacks --------------------------------------------

def test_ambiguous_falls_back_to_segment_tagname():
    info = parse_header(_read("ambiguous_no_originator.xml"))
    assert info.originating_system is None
    assert info.source_format == FORMAT_POSTPROC
    assert info.format_detection_method == METHOD_SEGMENT_TAGNAME
    assert info.project_code == "A22"          # from A22-0007-003
    assert info.header_parse_ok is True


def test_dexpi_without_originator_and_untagged_segments():
    xml = b"""<?xml version="1.0"?>
    <PlantModel>
      <Drawing Number="X-1" Revision="0"/>
      <PipingNetworkSystem>
        <PipingNetworkSegment ID="SG-1">
          <PipingComponent ID="PC-1" ComponentClass="GateValve"/>
        </PipingNetworkSegment>
      </PipingNetworkSystem>
    </PlantModel>"""
    info = parse_header(xml)
    assert info.source_format == FORMAT_DEXPI
    assert info.format_detection_method == METHOD_SEGMENT_TAGNAME


def test_unknown_when_no_originator_and_no_segments():
    xml = b"""<?xml version="1.0"?><PlantModel><Drawing Number="X-2" Revision="1"/></PlantModel>"""
    info = parse_header(xml)
    assert info.source_format is None
    assert info.format_detection_method == METHOD_UNKNOWN
    assert info.header_parse_ok is False       # format unresolved


# --- malformed / edge cases ------------------------------------------------

def test_malformed_lands_and_flags_not_ok():
    info = parse_header(_read("malformed_truncated.xml"))
    assert info.source_format == FORMAT_DEXPI          # Application marker read first
    assert info.format_detection_method == METHOD_APPLICATION
    # the Drawing title block broke before doc/rev could be read
    assert info.document_number is None
    assert info.drawing_revision is None
    assert info.project_code is None
    assert info.project_code_source == PC_SOURCE_UNKNOWN
    assert info.header_parse_ok is False


def test_empty_content_yields_all_null():
    info = parse_header(b"")
    assert info.source_format is None
    assert info.header_parse_ok is False


def test_non_xml_bytes_do_not_raise():
    info = parse_header(b"this is not xml at all")
    assert info.header_parse_ok is False
    assert info.source_format is None


def test_namespaced_tags_are_handled():
    xml = b"""<?xml version="1.0"?>
    <p:PlantModel xmlns:p="http://example.org/proteus">
      <p:PlantInformation OriginatingSystem="SPPID"/>
      <p:Drawing Name="216097C-NS-PID-1" Revision="2"/>
      <p:PipingNetworkSystem>
        <p:PipingNetworkSegment ID="SG" TagName="PG-1"/>
      </p:PipingNetworkSystem>
    </p:PlantModel>"""
    info = parse_header(xml)
    assert info.source_format == FORMAT_POSTPROC
    assert info.document_number == "216097C-NS-PID-1"
    assert info.drawing_revision == "2"
    assert info.project_code == "216097C"


# --- self-describing hash (spec §5.2) --------------------------------------

def test_hash_is_self_describing_deterministic_and_content_sensitive():
    a = _read("projectA_dexpi_02231.xml")
    h = compute_hash(a)
    assert h.startswith("sha256:")
    assert h == compute_hash(a)                 # deterministic
    assert h != compute_hash(a + b" ")          # one byte changes it
    assert len(h) == len("sha256:") + 64        # prefix + sha-256 hex


def test_hash_rejects_unsupported_bits():
    with pytest.raises(ValueError):
        compute_hash(b"x", hash_bits=123)
