"""Configuration for the Bronze ingestion layer.

Everything that varies between projects, environments, or source-file dialects
lives here as data — no behaviour is hard-coded in the ingestion job. This keeps
the "onboard a project by pointing at config, not by editing code" discipline the
architecture note (Section 3) establishes for the rest of the product.

See ``bronze_layer_spec.md`` (Draft v0.2) for the design this implements.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class HeaderFieldConfig:
    """Candidate source names for the shallow header read (bronze/header.py).

    The exact element/attribute names differ between the DEXPI/Proteus and
    INGR-PostProc dialects. Rather than bake one dialect's names in, the parser
    COLLECTS every candidate it sees during one shallow scan and then RESOLVES the
    logical fields per detected format (bronze/header.py). Confirm/extend these
    against real export files at onboarding — the spec frames the per-format
    source mapping as an adapter/reference-data detail (spec §3.1, §6, §9).

    IMPORTANT: this is a *shallow* read for identity, versioning and routing only.
    It must never grow into a content parser — that is Silver's job
    (master_data.ga / reconstructed._adapter_for). See spec §1.2 and §4.
    """

    # Attribute carrying the exporting system. NOTE: both DEXPI and PostProc are
    # exported by SmartPlant P&ID with OriginatingSystem="SPPID", so this is NOT a
    # format discriminator — it is captured only as lineage. Format is decided by
    # the Application marker below (spec §4).
    originating_system_attrs: List[str] = field(
        default_factory=lambda: ["OriginatingSystem", "originatingSystem"]
    )

    # Format discriminator (spec §4): PlantInformation/@Application. DEXPI exports
    # carry Application="Dexpi" (e.g. with ApplicationVersion="1.3.1"); PostProc
    # exports carry no Application attribute. Read from any element bearing it.
    application_attrs: List[str] = field(
        default_factory=lambda: ["Application"]
    )
    dexpi_application_markers: List[str] = field(
        default_factory=lambda: ["Dexpi"]
    )

    # Title-block-like elements whose attributes carry drawing identity
    # (e.g. PostProc <Drawing Name=".." Revision=".." Title="..">).
    title_block_tags: List[str] = field(
        default_factory=lambda: [
            "Drawing",
            "DrawingTitleBlock",
            "TitleBlock",
            "DocumentInformation",
        ]
    )

    # GenericAttribute element carrying Name/Value pairs (both flat and dotted
    # names such as "Revision.RevisionNumber" in PostProc).
    generic_attribute_tag: str = "GenericAttribute"
    generic_attribute_name_key: str = "Name"
    generic_attribute_value_key: str = "Value"

    # --- EPC vs client document number (spec §3.1, per-format mapping) -------
    # DEXPI/Project A: the EPC document number is the OperationCenterDocNo
    # GenericAttribute (e.g. 215777C-36292-PID-0031-02231); the client number is
    # the DrawingNumber. PostProc/Project B: both are Drawing/@Name.
    epc_document_generic_names: List[str] = field(
        default_factory=lambda: ["OperationCenterDocNo"]
    )
    # Drawing-element attribute holding the EPC/client number in PostProc.
    postproc_document_attr: str = "Name"
    # Client drawing-number sources (DEXPI): element attr or GenericAttribute.
    client_document_number_attrs: List[str] = field(
        default_factory=lambda: ["DrawingNumber", "Name", "ClientDocumentNumber"]
    )
    client_document_generic_names: List[str] = field(
        default_factory=lambda: ["DrawingNumber", "ClientDocumentNumber"]
    )
    # Generic fallback for the internal/EPC number when the confirmed source is
    # absent (older/other exports).
    document_number_attrs: List[str] = field(
        default_factory=lambda: ["Number", "DrawingNumber", "DocumentNumber"]
    )
    document_number_generic_names: List[str] = field(
        default_factory=lambda: ["DocumentNumber", "DrawingNumber"]
    )

    # --- project_code derivation (spec §3.1) --------------------------------
    # project_code = a token of the EPC document_number. Default: the leading
    # "-"-delimited token (215777C-… -> 215777C). Project-scoped reference-data.
    project_code_delimiter: str = "-"
    project_code_token_index: int = 0

    # --- revision + revision date (spec §6) ---------------------------------
    # DEXPI: a revision-history table exposed as flat header attributes /
    # GenericAttributes RevRow{N}No / RevRow{N}Date / RevRow{N}Desc. The current
    # revision is the highest-numbered populated RevRow{N}.
    dexpi_revrow_prefix: str = "RevRow"
    dexpi_revrow_no_suffix: str = "No"
    dexpi_revrow_date_suffix: str = "Date"

    # PostProc: Drawing/@Revision states the current revision directly
    # (authoritative — no max-by-sequence inference). The date is the
    # TP_RevisionData of the revision Label whose RevisionNumber matches it.
    postproc_current_revision_attr: str = "Revision"
    postproc_rev_status_name: str = "Revision.StatusType"
    postproc_rev_status_value: str = "Revision"
    postproc_rev_number_name: str = "Revision.RevisionNumber"
    postproc_rev_date_name: str = "Revision.TP_RevisionData"
    # A revision Label is detected structurally — any element that directly
    # contains GenericAttributes whose Name starts with this prefix is treated as
    # a revision-label group (the element's own tag name is NOT relied upon, since
    # it varies by export). Robust to <Label>, <Component>, <PlantItem>, etc.
    postproc_rev_generic_prefix: str = "Revision."

    # Generic fallback revision sources (older/other exports, or the synthetic
    # samples): a plain Revision / RevisionDate attribute or GenericAttribute.
    revision_attrs: List[str] = field(
        default_factory=lambda: ["Revision", "RevisionNumber", "Rev"]
    )
    revision_generic_names: List[str] = field(
        default_factory=lambda: ["Revision", "DrawingRevision"]
    )
    revision_date_attrs: List[str] = field(
        default_factory=lambda: ["RevisionDate", "IssueDate", "Date"]
    )
    revision_date_generic_names: List[str] = field(
        default_factory=lambda: ["RevisionDate", "IssueDate"]
    )

    # --- Structural fallback signal (spec §4) -------------------------------
    # When no Application marker resolves, a PipingNetworkSegment carrying a
    # TagName marks PostProc (mirrors reconstructed._adapter_for).
    segment_element_tags: List[str] = field(
        default_factory=lambda: ["PipingNetworkSegment"]
    )
    segment_tagname_attrs: List[str] = field(default_factory=lambda: ["TagName"])

    # --- Parser budgets (keep the scan shallow; ~13 MB files, spec §4/§8.2) ---
    # Cover the whole title block (Drawing header + revision labels), which can sit
    # after the first PipingNetworkSegment, before stopping. Elements are small, so
    # a few thousand is cheap even on a 13 MB file; the network body is skipped.
    header_element_budget: int = 6000
    segment_scan_element_budget: int = 60000


@dataclass
class BronzeConfig:
    """Top-level configuration for a Bronze ingestion run."""

    # Where the source export files are read from (a folder; scanned recursively).
    source_dir: str = ""

    # The Delta table to land into. Provide table_name (catalog) and/or table_path.
    table_name: str = "bronze.pid_documents"
    table_path: Optional[str] = None  # storage location for the Delta table

    # Which files to consider.
    path_glob: str = "*.xml"
    recursive_lookup: bool = True

    # Optional project/tenant tag. When supplied it OVERRIDES the code derived
    # from the document number, and a derived-vs-run mismatch is surfaced
    # (spec §3.1). Normally leave unset and let derivation win.
    project_code: Optional[str] = None

    # content_text (spec §3.3): OFF by default — drawings reach ~13 MB, so a
    # derivable UTF-8 decode would roughly double every retained, append-only
    # row. Turn on only with a size gate; decode-on-read is the recommended way
    # to get SQL-readable XML.
    store_content_text: bool = False
    content_text_max_bytes: Optional[int] = 262144  # 256 KB gate when enabled

    # Partition columns for the Delta table (spec §8.1).
    partition_by: List[str] = field(default_factory=lambda: ["ingest_date"])

    # Hash algorithm for the version discriminator (spec §5.2). Stored
    # self-describing as "sha256:<hex>". sha-256 is the documented default.
    hash_bits: int = 256

    # PoC runtime (spec §8.4): register named tables in the embedded Derby Hive
    # metastore. Set False for path-based-only (no metastore) usage.
    enable_hive: bool = True

    header: HeaderFieldConfig = field(default_factory=HeaderFieldConfig)

    def resolve_table(self) -> str:
        """Return the identifier used in SQL (path-based or catalog name)."""
        if self.table_path:
            return f"delta.`{self.table_path}`"
        return self.table_name
