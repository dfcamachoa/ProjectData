"""Shallow header read, format detection, and hashing — the Spark-independent core.

This module has no PySpark import, so it is unit-tested in plain Python and reused
outside Spark. It performs the *only* interpretive act Bronze is allowed (spec §1.2):
a bounded, streaming read of a handful of header fields for identity, versioning and
routing. It never parses the network model, never reconstructs topology.

Design: ONE shallow streaming scan COLLECTS every candidate signal; then the logical
fields are RESOLVED per detected format (spec §3.1, §6). This keeps the per-format
source mapping (which differs between DEXPI and PostProc) as data, not branches strewn
through the scan.

Format-detection ladder (spec §4), first match wins. NOTE: both DEXPI and PostProc
are exported by SmartPlant P&ID with OriginatingSystem="SPPID", so OriginatingSystem
is captured only as lineage and is NOT used to decide format.
  1. APPLICATION     — PlantInformation/@Application contains "Dexpi" -> DEXPI.
  2. SEGMENT_TAGNAME — else a PipingNetworkSegment carrying a TagName -> POSTPROC;
                       segments present but untagged -> DEXPI.
  3. UNKNOWN         — neither signal resolves; the file is still landed.

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

METHOD_APPLICATION = "APPLICATION"
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
    application: Optional[str] = None                           # PlantInformation/@Application
    tb_attrs: Dict[str, str] = field(default_factory=dict)      # title-block element attrs
    generics: Dict[str, str] = field(default_factory=dict)      # flat GenericAttribute name->value
    revrows: Dict[int, Dict[str, str]] = field(default_factory=dict)  # DEXPI RevRow{N} -> {No,Date}
    labels: List[Dict[str, str]] = field(default_factory=list)  # PostProc revision Labels (grouped)
    rev_dates: List[str] = field(default_factory=list)  # every Revision.TP_RevisionData seen (flat)
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
    # getattr-guarded so a stale config (older field set) degrades instead of
    # crashing — the code and config should still be replaced as a matched set.
    rev_prefix = getattr(cfg, "postproc_rev_generic_prefix", "Revision.")
    rev_number_name = getattr(cfg, "postproc_rev_number_name", "Revision.RevisionNumber")
    rev_date_name = getattr(cfg, "postproc_rev_date_name", "Revision.TP_RevisionData")
    ga_tag = cfg.generic_attribute_tag
    # Frame stack: one dict per open NON-GenericAttribute element, collecting the
    # Revision.* GenericAttributes that are its direct children. The element's own
    # tag is not relied upon — any element that directly holds Revision.* fields is
    # treated as a revision-label group (spec §6; robust to the real export's tag).
    frame_stack: List[Dict[str, str]] = []

    def _record_generic(name: str, value: str) -> None:
        # RevRow{N}No / RevRow{N}Date (DEXPI revision history)
        m = revrow_re.match(name)
        if m:
            c.revrows.setdefault(int(m.group(1)), {})[m.group(2)] = value
            return
        # Revision.* -> the enclosing element's revision-label group (PostProc).
        if name.startswith(rev_prefix):
            # Also collect every revision date flat, so the "latest date" fallback
            # works regardless of how the labels nest (robust to unknown wrappers).
            if name == rev_date_name:
                c.rev_dates.append(value)
            if frame_stack:
                frame_stack[-1].setdefault(name, value)
            return
        c.generics.setdefault(name, value)

    try:
        for event, elem in iterparse(io.BytesIO(content), events=("start", "end")):
            tag = _local_tag(elem.tag)

            if event == "start":
                seen += 1
                attrib = elem.attrib
                is_ga = tag == ga_tag

                if c.originating is None:
                    found = _first(attrib, cfg.originating_system_attrs)
                    if found:
                        c.originating = found
                if c.application is None:
                    app = _first(attrib, cfg.application_attrs)
                    if app:
                        c.application = app

                if tag in cfg.title_block_tags:
                    for k, v in attrib.items():
                        cv = _clean(v)
                        if cv is not None:
                            c.tb_attrs.setdefault(_local_tag(k), cv)
                    for k, v in attrib.items():
                        m = revrow_re.match(_local_tag(k))
                        cv = _clean(v)
                        if m and cv is not None:
                            c.revrows.setdefault(int(m.group(1)), {})[m.group(2)] = cv

                if is_ga:
                    name = _clean(attrib.get(cfg.generic_attribute_name_key))
                    value = _clean(attrib.get(cfg.generic_attribute_value_key))
                    if name and value is not None:
                        _record_generic(name, value)
                else:
                    # A non-GA element opens a potential revision-label group.
                    frame_stack.append({})

                if tag in cfg.segment_element_tags:
                    c.segment_element_seen = True
                    if _first(attrib, cfg.segment_tagname_attrs) is not None:
                        c.segment_tagname_seen = True

                # budget control
                is_dexpi_hint = c.application is not None and any(
                    m.lower() in c.application.lower()
                    for m in cfg.dexpi_application_markers
                )
                if is_dexpi_hint:
                    # DEXPI identity + RevRow revision history live in the title
                    # block near the top; a header-sized budget suffices.
                    if seen >= cfg.header_element_budget:
                        break
                else:
                    # PostProc revision labels are scattered through the file, so we
                    # do NOT stop at a header budget — the early-exit below stops us
                    # once the current revision's dated label is found AND the format
                    # is classifiable. This absolute cap only bounds the pathological
                    # case (no such label).
                    if seen >= cfg.segment_scan_element_budget:
                        break

            else:  # end
                if tag != ga_tag and frame_stack:
                    group = frame_stack.pop()
                    # A group that carries a RevisionNumber is a revision label.
                    if group.get(rev_number_name) is not None:
                        c.labels.append(group)
                elem.clear()
                # No early-exit: PostProc takes the LATEST revision (by date), so we
                # must see every revision label. The absolute element cap (in the
                # start branch) bounds the scan.
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
        # Project decision: for PostProc, take the LATEST logged revision as a
        # consistent PAIR — the Revision.RevisionNumber that is the companion of the
        # latest Revision.TP_RevisionData — rather than reading the number from
        # Drawing/@Revision (whose letter can run ahead of the last logged revision,
        # leaving a number/date mismatch). PostProc dates are YYYY/MM/DD, so the
        # lexical max date is the chronological latest.
        best_date: Optional[str] = None
        best_num: Optional[str] = None
        for lab in c.labels:
            d = _clean(lab.get(cfg.postproc_rev_date_name))
            if d is None:
                continue
            if best_date is None or d > best_date:
                best_date = d
                best_num = _clean(lab.get(cfg.postproc_rev_number_name))
        if best_date is not None:
            rev = best_num          # companion RevisionNumber of the latest date
            rev_date = best_date
        else:
            # No dated revision labels grouped — fall back to the Drawing header for
            # the number and the flat latest date (robust to unknown label nesting).
            rev = _clean(c.tb_attrs.get(cfg.postproc_current_revision_attr))
            dates = [d for d in (_clean(x) for x in c.rev_dates) if d is not None]
            if dates:
                rev_date = max(dates)

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
    # OriginatingSystem is "SPPID" for BOTH formats, so it is captured only as
    # lineage; the discriminator is PlantInformation/@Application.
    source_format = None
    method = METHOD_UNKNOWN
    app = (c.application or "").lower()
    if app and any(m.lower() in app for m in cfg.dexpi_application_markers):
        source_format, method = FORMAT_DEXPI, METHOD_APPLICATION
    elif c.segment_tagname_seen:
        source_format, method = FORMAT_POSTPROC, METHOD_SEGMENT_TAGNAME
    elif c.segment_element_seen:
        # segments present but none tagged -> the DEXPI side of the structural test
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
