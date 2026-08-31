"""Shallow header read, format detection, and hashing — the Spark-independent core.

This module has no PySpark import, so it is unit-tested in plain Python and reused
outside Spark. It performs the *only* interpretive act Bronze is allowed (spec §1.2):
a bounded, streaming read of a handful of header fields for identity, versioning and
routing. It never parses the network model, never reconstructs topology.

Design: ONE shallow streaming scan COLLECTS every candidate signal; then the logical
fields are RESOLVED per detected format (spec §3.1, §6). This keeps the per-format
source mapping (which differs between DEXPI and PostProc) as data, not branches strewn
through the scan.

Format-detection ladder (spec §4), first match wins:
  1. ORIGINATING_SYSTEM  — OriginatingSystem contains an SPPID marker -> POSTPROC,
                           any other originator -> DEXPI.
  2. SEGMENT_TAGNAME     — no usable originator: a PipingNetworkSegment carrying a
                           TagName -> POSTPROC, else DEXPI.
  3. UNKNOWN             — neither signal resolves; the file is still landed.

Revision capture (spec §6):
  * DEXPI:    current revision = highest-numbered populated RevRow{N}No; its
              RevRow{N}Date is the (verbatim) issue date.
  * PostProc: Drawing/@Revision states the current revision directly; the date is
              the TP_RevisionData of the revision Label whose RevisionNumber matches.
  Dates are stored VERBATIM (project-scoped format); normalisation is Silver/Gold's.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional
from xml.etree.ElementTree import ParseError, iterparse

from .config import HeaderFieldConfig

FORMAT_DEXPI = "DEXPI"
FORMAT_POSTPROC = "POSTPROC"

METHOD_ORIGINATING_SYSTEM = "ORIGINATING_SYSTEM"
METHOD_SEGMENT_TAGNAME = "SEGMENT_TAGNAME"
METHOD_UNKNOWN = "UNKNOWN"

# project_code_source authorities (spec §3.1).
PC_SOURCE_DOCUMENT_NUMBER = "DOCUMENT_NUMBER"
PC_SOURCE_INGEST_RUN = "INGEST_RUN"
PC_SOURCE_SOURCE_PATH = "SOURCE_PATH"
PC_SOURCE_UNKNOWN = "UNKNOWN"


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
    project_code: Optional[str] = None
    project_code_source: str = PC_SOURCE_UNKNOWN
    header_parse_ok: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #
def _algo_name(hash_bits: int) -> str:
    name = {224: "sha224", 256: "sha256", 384: "sha384", 512: "sha512"}.get(hash_bits)
    if name is None:
        raise ValueError(f"unsupported hash_bits={hash_bits}")
    return name


def compute_hash(content: bytes, hash_bits: int = 256) -> str:
    """Self-describing content fingerprint, e.g. ``sha256:<hex>`` (spec §5.2).

    Hashes the EXACT raw bytes as ingested — no BOM strip, no CRLF/LF or whitespace
    normalisation — so the hash never desyncs from the stored payload and distinct
    byte-versions never silently collide. Cross-engine deterministic with Spark's
    sha2(content, 256).
    """
    name = _algo_name(hash_bits)
    return f"{name}:{hashlib.new(name, content).hexdigest()}"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _local_tag(tag) -> str:
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _clean(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _first(d: Dict[str, str], keys) -> Optional[str]:
    for k in keys:
        v = _clean(d.get(k))
        if v is not None:
            return v
    return None


@dataclass
class _Collected:
    originating: Optional[str] = None
    tb_attrs: Dict[str, str] = field(default_factory=dict)      # title-block element attrs
    generics: Dict[str, str] = field(default_factory=dict)      # flat GenericAttribute name->value
    revrows: Dict[int, Dict[str, str]] = field(default_factory=dict)  # DEXPI RevRow{N} -> {No,Date}
    labels: List[Dict[str, str]] = field(default_factory=list)  # PostProc revision Labels
    segment_element_seen: bool = False
    segment_tagname_seen: bool = False


def _scan(content: bytes, cfg: HeaderFieldConfig) -> tuple[_Collected, bool, int]:
    """One shallow, bounded, malformed-tolerant pass collecting raw signals."""
    c = _Collected()
    parse_error = False
    seen = 0

    revrow_re = re.compile(
        rf"^{re.escape(cfg.dexpi_revrow_prefix)}(\d+)"
        rf"({re.escape(cfg.dexpi_revrow_no_suffix)}|{re.escape(cfg.dexpi_revrow_date_suffix)})$"
    )
    label_tags = set(cfg.postproc_label_tags)
    # stack of label-group dicts for GenericAttributes nested under a Label
    label_stack: List[Dict[str, str]] = []

    def _record_generic(name: str, value: str) -> None:
        # RevRow{N}No / RevRow{N}Date (DEXPI revision history)
        m = revrow_re.match(name)
        if m:
            n = int(m.group(1))
            c.revrows.setdefault(n, {})[m.group(2)] = value
            return
        if label_stack:
            # a Revision.* field belonging to the current Label group
            label_stack[-1][name] = value
        else:
            c.generics.setdefault(name, value)

    try:
        for event, elem in iterparse(io.BytesIO(content), events=("start", "end")):
            tag = _local_tag(elem.tag)

            if event == "start":
                seen += 1
                attrib = elem.attrib

                if c.originating is None:
                    found = _first(attrib, cfg.originating_system_attrs)
                    if found:
                        c.originating = found

                if tag in cfg.title_block_tags:
                    for k, v in attrib.items():
                        cv = _clean(v)
                        if cv is not None:
                            c.tb_attrs.setdefault(_local_tag(k), cv)
                    # RevRow* can also appear as flat title-block attributes
                    for k, v in attrib.items():
                        m = revrow_re.match(_local_tag(k))
                        cv = _clean(v)
                        if m and cv is not None:
                            c.revrows.setdefault(int(m.group(1)), {})[m.group(2)] = cv

                if tag in label_tags:
                    label_stack.append({})

                if tag == cfg.generic_attribute_tag:
                    name = _clean(attrib.get(cfg.generic_attribute_name_key))
                    value = _clean(attrib.get(cfg.generic_attribute_value_key))
                    if name and value is not None:
                        _record_generic(name, value)

                if tag in cfg.segment_element_tags:
                    c.segment_element_seen = True
                    if _first(attrib, cfg.segment_tagname_attrs) is not None:
                        c.segment_tagname_seen = True

                # budget control
                have_id = _first(c.tb_attrs, ["Name", "Number"]) is not None or bool(
                    c.generics
                )
                if c.originating is not None:
                    if seen >= cfg.header_element_budget:
                        break
                else:
                    if c.segment_tagname_seen and have_id:
                        break
                    if seen >= cfg.segment_scan_element_budget:
                        break

            else:  # end
                if tag in label_tags and label_stack:
                    group = label_stack.pop()
                    if group.get(cfg.postproc_rev_status_name) == cfg.postproc_rev_status_value:
                        c.labels.append(group)
                elem.clear()
    except ParseError:
        parse_error = True

    return c, parse_error, seen


def _resolve_revision(
    c: _Collected, source_format: Optional[str], cfg: HeaderFieldConfig
) -> tuple[Optional[str], Optional[str]]:
    """Return (drawing_revision, drawing_revision_date), both verbatim (spec §6)."""
    rev: Optional[str] = None
    rev_date: Optional[str] = None

    if source_format == FORMAT_POSTPROC:
        # Current revision stated directly on the Drawing header.
        rev = _clean(c.tb_attrs.get(cfg.postproc_current_revision_attr))
        if rev is not None:
            for lab in c.labels:
                if _clean(lab.get(cfg.postproc_rev_number_name)) == rev:
                    rev_date = _clean(lab.get(cfg.postproc_rev_date_name))
                    break

    if rev is None and c.revrows:
        # DEXPI: current revision = highest-numbered populated RevRow{N}No.
        for n in sorted(c.revrows.keys(), reverse=True):
            row = c.revrows[n]
            no = _clean(row.get(cfg.dexpi_revrow_no_suffix))
            if no is not None:
                rev = no
                rev_date = _clean(row.get(cfg.dexpi_revrow_date_suffix))
                break

    if rev is None:
        # Fallback: a plain Revision attribute / GenericAttribute.
        rev = _first(c.tb_attrs, cfg.revision_attrs) or _first(
            c.generics, cfg.revision_generic_names
        )
    if rev_date is None:
        rev_date = _first(c.tb_attrs, cfg.revision_date_attrs) or _first(
            c.generics, cfg.revision_date_generic_names
        )
    return rev, rev_date


def _resolve_documents(
    c: _Collected, source_format: Optional[str], cfg: HeaderFieldConfig
) -> tuple[Optional[str], Optional[str]]:
    """Return (document_number [EPC], client_document_number), per-format (spec §3.1)."""
    doc: Optional[str] = None
    client: Optional[str] = None

    if source_format == FORMAT_POSTPROC:
        # Both numbers are Drawing/@Name (identical in project B).
        name = _clean(c.tb_attrs.get(cfg.postproc_document_attr))
        doc = name
        client = name
    else:
        # DEXPI: EPC number is the OperationCenterDocNo GenericAttribute;
        # client number is the DrawingNumber.
        doc = _first(c.generics, cfg.epc_document_generic_names)
        client = _first(c.tb_attrs, cfg.client_document_number_attrs) or _first(
            c.generics, cfg.client_document_generic_names
        )

    # Fallbacks for other/older exports (and the earlier synthetic samples).
    if doc is None:
        doc = _first(c.tb_attrs, cfg.document_number_attrs) or _first(
            c.generics, cfg.document_number_generic_names
        )
    if client is None:
        client = _first(c.tb_attrs, cfg.client_document_number_attrs)
    return doc, client


def _derive_project_code(document_number: Optional[str], cfg: HeaderFieldConfig):
    """Leading token of the EPC document number (spec §3.1)."""
    if not document_number:
        return None, PC_SOURCE_UNKNOWN
    parts = document_number.split(cfg.project_code_delimiter)
    idx = cfg.project_code_token_index
    if 0 <= idx < len(parts):
        token = _clean(parts[idx])
        if token:
            return token, PC_SOURCE_DOCUMENT_NUMBER
    return None, PC_SOURCE_UNKNOWN


def parse_header(content: bytes, cfg: Optional[HeaderFieldConfig] = None) -> HeaderInfo:
    """Shallow, bounded, malformed-tolerant header read for one file (spec §7).

    Returns a fully-populated HeaderInfo. On malformed XML or missing fields it still
    returns what it found with header_parse_ok=False — Bronze flags, never rejects.
    Only a physically unreadable (empty/None) file yields an all-null result.
    """
    cfg = cfg or HeaderFieldConfig()
    info = HeaderInfo()
    if not content:
        return info

    c, parse_error, seen = _scan(content, cfg)

    # --- resolve format (spec §4) ---
    source_format = None
    method = METHOD_UNKNOWN
    if c.originating:
        upper = c.originating.upper()
        if any(m.upper() in upper for m in cfg.postproc_originating_markers):
            source_format = FORMAT_POSTPROC
        else:
            source_format = FORMAT_DEXPI
        method = METHOD_ORIGINATING_SYSTEM
    elif c.segment_tagname_seen:
        source_format, method = FORMAT_POSTPROC, METHOD_SEGMENT_TAGNAME
    elif c.segment_element_seen:
        source_format, method = FORMAT_DEXPI, METHOD_SEGMENT_TAGNAME
    else:
        source_format, method = None, METHOD_UNKNOWN

    document_number, client_document_number = _resolve_documents(c, source_format, cfg)
    drawing_revision, drawing_revision_date = _resolve_revision(c, source_format, cfg)
    project_code, project_code_source = _derive_project_code(document_number, cfg)

    info.originating_system = c.originating
    info.source_format = source_format
    info.format_detection_method = method
    info.client_document_number = client_document_number
    info.document_number = document_number
    info.drawing_revision = drawing_revision
    info.drawing_revision_date = drawing_revision_date
    info.project_code = project_code
    info.project_code_source = project_code_source

    # header_parse_ok: required fields read and XML clean. Required = EPC
    # document_number + drawing_revision + a resolved source_format. The revision
    # date and client number are not required.
    info.header_parse_ok = (
        (not parse_error)
        and document_number is not None
        and drawing_revision is not None
        and source_format is not None
    )
    return info
