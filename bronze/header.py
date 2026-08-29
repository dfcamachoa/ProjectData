"""Shallow header read, format detection, and hashing — the Spark-independent core.

This module is deliberately free of any PySpark import so it can be unit-tested
in plain Python and reused outside Spark. It performs the *only* interpretive act
Bronze is allowed (spec §1.2): a bounded, streaming read of a handful of header
fields for identity, versioning and routing. It never parses the network model,
never touches GenericAttribute business values beyond the whitelisted identity
names, and never reconstructs topology — those are Silver's job.

Format-detection ladder (spec §4), first match wins:
  1. ORIGINATING_SYSTEM  — OriginatingSystem contains an SPPID marker -> POSTPROC,
                           any other originator -> DEXPI.
  2. SEGMENT_TAGNAME     — no usable originator: a PipingNetworkSegment carrying a
                           TagName -> POSTPROC, else DEXPI (mirrors
                           reconstructed._adapter_for).
  3. UNKNOWN             — neither signal resolves; the file is still landed.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import asdict, dataclass
from typing import Optional
from xml.etree.ElementTree import ParseError, iterparse

from .config import HeaderFieldConfig

# Resolved-format tags stored in the `source_format` column.
FORMAT_DEXPI = "DEXPI"
FORMAT_POSTPROC = "POSTPROC"

# How `source_format` was decided, stored in `format_detection_method`.
METHOD_ORIGINATING_SYSTEM = "ORIGINATING_SYSTEM"
METHOD_SEGMENT_TAGNAME = "SEGMENT_TAGNAME"
METHOD_UNKNOWN = "UNKNOWN"


@dataclass
class HeaderInfo:
    """The shallow-read result for one source file."""

    originating_system: Optional[str] = None
    source_format: Optional[str] = None
    format_detection_method: str = METHOD_UNKNOWN
    client_document_number: Optional[str] = None
    document_number: Optional[str] = None
    drawing_revision: Optional[str] = None
    drawing_revision_date: Optional[str] = None
    header_parse_ok: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def compute_hash(content: bytes, hash_bits: int = 256) -> str:
    """Hex digest over the raw bytes — the version discriminator (spec §5.2).

    No normalisation of whitespace, BOM, or line endings: Bronze hashes exactly
    what it stores (spec §9, open decision #3).
    """
    algo = {224: "sha224", 256: "sha256", 384: "sha384", 512: "sha512"}.get(hash_bits)
    if algo is None:
        raise ValueError(f"unsupported hash_bits={hash_bits}")
    return hashlib.new(algo, content).hexdigest()


def _local_tag(tag: str) -> str:
    """Strip an XML namespace prefix: '{ns}Drawing' -> 'Drawing'."""
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _first_attr(attrib: dict, candidates) -> Optional[str]:
    for name in candidates:
        val = attrib.get(name)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return None


def parse_header(content: bytes, cfg: Optional[HeaderFieldConfig] = None) -> HeaderInfo:
    """Shallow, bounded, malformed-tolerant header read for one file.

    Returns a fully-populated HeaderInfo. On malformed XML or missing fields it
    still returns what it found with header_parse_ok=False — Bronze flags, never
    rejects (spec §7). Only a physically unreadable file (empty/None) yields an
    all-null result.
    """
    cfg = cfg or HeaderFieldConfig()
    info = HeaderInfo()

    if not content:
        # Nothing to land content-wise; caller still records the row (spec §7).
        return info

    originating: Optional[str] = None
    client_doc: Optional[str] = None
    internal_doc: Optional[str] = None
    revision: Optional[str] = None
    revision_date: Optional[str] = None
    segment_element_seen = False   # any PipingNetworkSegment at all
    segment_tagname_seen = False   # a PipingNetworkSegment carrying a TagName
    parse_error = False

    seen = 0
    try:
        # start events expose attributes immediately, before children are read,
        # so a single streaming pass suffices. We clear elements on end to bound
        # memory even for large files.
        for event, elem in iterparse(io.BytesIO(content), events=("start", "end")):
            if event == "end":
                elem.clear()
                continue

            seen += 1
            tag = _local_tag(elem.tag)
            attrib = elem.attrib

            # (a) originating system — may sit on the root or a PlantInformation
            #     element; read from whichever element carries the attribute.
            if originating is None:
                found = _first_attr(attrib, cfg.originating_system_attrs)
                if found:
                    originating = found

            # (b) title-block identity fields.
            if tag in cfg.title_block_tags:
                client_doc = client_doc or _first_attr(
                    attrib, cfg.client_document_number_attrs
                )
                internal_doc = internal_doc or _first_attr(
                    attrib, cfg.document_number_attrs
                )
                revision = revision or _first_attr(attrib, cfg.revision_attrs)
                revision_date = revision_date or _first_attr(
                    attrib, cfg.revision_date_attrs
                )

            # (c) identity carried as GenericAttribute Name/Value pairs.
            elif tag == cfg.generic_attribute_tag:
                name = attrib.get(cfg.generic_attribute_name_key)
                value = attrib.get(cfg.generic_attribute_value_key)
                if name and value is not None and str(value).strip() != "":
                    value = str(value).strip()
                    if name in cfg.generic_client_document_names:
                        client_doc = client_doc or value
                    elif name in cfg.generic_document_names:
                        internal_doc = internal_doc or value
                    elif name in cfg.generic_revision_names:
                        revision = revision or value
                    elif name in cfg.generic_revision_date_names:
                        revision_date = revision_date or value

            # (d) structural PostProc fallback signal.
            if tag in cfg.segment_element_tags:
                segment_element_seen = True
                if not segment_tagname_seen and (
                    _first_attr(attrib, cfg.segment_tagname_attrs) is not None
                ):
                    segment_tagname_seen = True

            # --- early-exit / budget control -------------------------------
            have_identity = internal_doc is not None and revision is not None
            if originating is not None:
                # Cheap path: originator resolves the format; once identity is
                # also in hand we can stop without scanning into the network body.
                if have_identity:
                    break
                if seen >= cfg.header_element_budget:
                    break
            else:
                # Must look deeper for the segment TagName fallback.
                if segment_tagname_seen and have_identity:
                    break
                if seen >= cfg.segment_scan_element_budget:
                    break
    except ParseError:
        # Malformed XML: keep whatever we gathered before the break point.
        parse_error = True

    # --- resolve format (spec §4) -------------------------------------------
    source_format = None
    method = METHOD_UNKNOWN
    if originating:
        upper = originating.upper()
        if any(m.upper() in upper for m in cfg.postproc_originating_markers):
            source_format = FORMAT_POSTPROC
        else:
            source_format = FORMAT_DEXPI
        method = METHOD_ORIGINATING_SYSTEM
    elif segment_tagname_seen:
        source_format = FORMAT_POSTPROC
        method = METHOD_SEGMENT_TAGNAME
    elif segment_element_seen:
        # We scanned segments and none carried a TagName — the "otherwise" side of
        # reconstructed._adapter_for's structural test resolves to DEXPI.
        source_format = FORMAT_DEXPI
        method = METHOD_SEGMENT_TAGNAME
    else:
        # No originator and no segment element observed: genuinely unclassifiable.
        # Land it anyway (spec §4, step 3); Silver decides.
        source_format = None
        method = METHOD_UNKNOWN

    info.originating_system = originating
    info.source_format = source_format
    info.format_detection_method = method
    info.client_document_number = client_doc
    info.document_number = internal_doc
    info.drawing_revision = revision
    info.drawing_revision_date = revision_date

    # header_parse_ok: every field needed downstream was read and XML was clean.
    # Required = document_number + drawing_revision + a resolved source_format.
    # client_document_number is optional (project B often carries only one number).
    info.header_parse_ok = (
        (not parse_error)
        and internal_doc is not None
        and revision is not None
        and source_format is not None
    )
    return info
