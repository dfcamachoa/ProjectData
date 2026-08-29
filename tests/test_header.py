"""Unit tests for the Spark-independent Bronze core (bronze/header.py).

These run in plain Python — no PySpark required — and cover the format-detection
ladder, header extraction, malformed-tolerance, and hashing (spec §4, §5, §7).
"""
from __future__ import annotations

import pathlib

import pytest

from bronze.header import (
    FORMAT_DEXPI,
    FORMAT_POSTPROC,
    METHOD_ORIGINATING_SYSTEM,
    METHOD_SEGMENT_TAGNAME,
    METHOD_UNKNOWN,
    compute_hash,
    parse_header,
)

SAMPLE_DIR = pathlib.Path(__file__).resolve().parent.parent / "sample_data"


def _read(name: str) -> bytes:
    return (SAMPLE_DIR / name).read_bytes()


# --- format detection ------------------------------------------------------

def test_dexpi_detected_from_originating_system():
    info = parse_header(_read("projectA_dexpi_01010.xml"))
    assert info.source_format == FORMAT_DEXPI
    assert info.format_detection_method == METHOD_ORIGINATING_SYSTEM
    assert info.originating_system.startswith("SmartPlant")
    assert info.document_number == "215777C-36209-PID-0021-01010"
    assert info.client_document_number == "362-09-PR-PID-01010"
    assert info.drawing_revision == "01"
    assert info.drawing_revision_date == "2026-05-08"
    assert info.header_parse_ok is True


def test_postproc_detected_from_sppid():
    info = parse_header(_read("projectB_postproc_0001.xml"))
    assert info.source_format == FORMAT_POSTPROC
    assert info.format_detection_method == METHOD_ORIGINATING_SYSTEM
    assert info.originating_system == "SPPID"
    assert info.document_number == "A14-0001-001"
    assert info.drawing_revision == "A"
    # project B example carries only one drawing number -> client number optional
    assert info.header_parse_ok is True


def test_ambiguous_falls_back_to_segment_tagname():
    info = parse_header(_read("ambiguous_no_originator.xml"))
    assert info.originating_system is None
    assert info.source_format == FORMAT_POSTPROC
    assert info.format_detection_method == METHOD_SEGMENT_TAGNAME
    assert info.document_number == "A22-0007-003"
    assert info.drawing_revision == "B"
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
    # saw segments, none tagged -> DEXPI side of the structural test
    assert info.source_format == FORMAT_DEXPI
    assert info.format_detection_method == METHOD_SEGMENT_TAGNAME


def test_unknown_when_no_originator_and_no_segments():
    xml = b"""<?xml version="1.0"?><PlantModel><Drawing Number="X-2" Revision="1"/></PlantModel>"""
    info = parse_header(xml)
    assert info.source_format is None
    assert info.format_detection_method == METHOD_UNKNOWN
    # format unresolved -> not ok, even though identity was read
    assert info.header_parse_ok is False


# --- malformed / edge cases ------------------------------------------------

def test_malformed_lands_and_flags_not_ok():
    info = parse_header(_read("malformed_truncated.xml"))
    # originator was readable before the break -> format still resolves
    assert info.source_format == FORMAT_DEXPI
    assert info.format_detection_method == METHOD_ORIGINATING_SYSTEM
    # but the revision could not be read -> header_parse_ok is False (never rejected)
    assert info.drawing_revision is None
    assert info.header_parse_ok is False


def test_empty_content_yields_all_null():
    info = parse_header(b"")
    assert info.originating_system is None
    assert info.source_format is None
    assert info.format_detection_method == METHOD_UNKNOWN
    assert info.header_parse_ok is False


def test_non_xml_bytes_do_not_raise():
    info = parse_header(b"this is not xml at all")
    assert info.header_parse_ok is False
    # unreadable as XML -> unclassifiable, but no exception escapes
    assert info.source_format is None


# --- namespaced XML --------------------------------------------------------

def test_namespaced_tags_are_handled():
    xml = b"""<?xml version="1.0"?>
    <p:PlantModel xmlns:p="http://example.org/proteus">
      <p:PlantInformation OriginatingSystem="SPPID"/>
      <p:Drawing Number="NS-1" Revision="2"/>
      <p:PipingNetworkSystem>
        <p:PipingNetworkSegment ID="SG" TagName="PG-1"/>
      </p:PipingNetworkSystem>
    </p:PlantModel>"""
    info = parse_header(xml)
    assert info.source_format == FORMAT_POSTPROC
    assert info.document_number == "NS-1"
    assert info.drawing_revision == "2"


# --- hashing ---------------------------------------------------------------

def test_hash_is_deterministic_and_content_sensitive():
    a = _read("projectA_dexpi_01010.xml")
    assert compute_hash(a) == compute_hash(a)           # deterministic
    assert compute_hash(a) != compute_hash(a + b" ")    # one byte changes it
    assert len(compute_hash(a)) == 64                    # sha-256 hex


def test_hash_rejects_unsupported_bits():
    with pytest.raises(ValueError):
        compute_hash(b"x", hash_bits=123)
