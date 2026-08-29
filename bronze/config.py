"""Configuration for the Bronze ingestion layer.

Everything that varies between projects, environments, or source-file dialects
lives here as data — no behaviour is hard-coded in the ingestion job. This keeps
the "onboard a project by pointing at config, not by editing code" discipline the
architecture note (Section 3) establishes for the rest of the product.

See ``bronze_layer_spec.md`` for the design this implements.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class HeaderFieldConfig:
    """Candidate source names for the shallow header read (bronze/header.py).

    The exact element/attribute names differ between the DEXPI/Proteus and
    INGR-PostProc dialects, and even between vendor export versions. Rather than
    bake one dialect's names in, we list *candidates*; the parser takes the first
    that resolves. Confirm/extend these against real export files at onboarding
    (see bronze_layer_spec.md §9, open decision #1 and #7).

    IMPORTANT: this is a *shallow* read for identity, versioning and routing only.
    It must never grow into a content parser — that is Silver's job
    (master_data.ga / reconstructed._adapter_for). See spec §1.2 and §4.
    """

    # Attribute carrying the exporting system, e.g. "SPPID" (PostProc) or a
    # DEXPI originator string. Read from any element bearing this attribute.
    originating_system_attrs: List[str] = field(
        default_factory=lambda: ["OriginatingSystem", "originatingSystem"]
    )

    # Title-block-like elements that carry the drawing's identity fields.
    title_block_tags: List[str] = field(
        default_factory=lambda: [
            "Drawing",
            "DrawingTitleBlock",
            "TitleBlock",
            "DocumentInformation",
        ]
    )

    # Attribute names (on a title-block element) for the client's drawing number.
    client_document_number_attrs: List[str] = field(
        default_factory=lambda: [
            "Name",
            "DrawingName",
            "ClientDocumentNumber",
            "ClientNumber",
        ]
    )

    # Attribute names for our internal drawing number.
    document_number_attrs: List[str] = field(
        default_factory=lambda: [
            "Number",
            "DrawingNumber",
            "DocumentNumber",
        ]
    )

    # Attribute names for the drawing revision.
    revision_attrs: List[str] = field(
        default_factory=lambda: ["Revision", "RevisionNumber", "Rev"]
    )

    # Attribute names for the revision issue/approval date, if the title block
    # exposes one (spec §6 / §9.1). Optional — Gold falls back to revision order
    # + source_last_modified when absent.
    revision_date_attrs: List[str] = field(
        default_factory=lambda: ["RevisionDate", "IssueDate", "Date", "ApprovalDate"]
    )

    # Some exports carry identity as <GenericAttribute Name=".." Value=".."> pairs
    # rather than element attributes. These map a GenericAttribute Name to one of
    # our logical fields. Checked in addition to the title-block attributes.
    generic_attribute_tag: str = "GenericAttribute"
    generic_attribute_name_key: str = "Name"
    generic_attribute_value_key: str = "Value"
    generic_client_document_names: List[str] = field(
        default_factory=lambda: ["ClientDocumentNumber", "ClientDrawingNumber"]
    )
    generic_document_names: List[str] = field(
        default_factory=lambda: ["DocumentNumber", "DrawingNumber"]
    )
    generic_revision_names: List[str] = field(
        default_factory=lambda: ["Revision", "DrawingRevision"]
    )
    generic_revision_date_names: List[str] = field(
        default_factory=lambda: ["RevisionDate", "IssueDate"]
    )

    # --- Format-detection signals (spec §4) --------------------------------
    # A value that, if it appears (case-insensitively) in the OriginatingSystem
    # attribute, marks the file as PostProc.
    postproc_originating_markers: List[str] = field(
        default_factory=lambda: ["SPPID"]
    )
    # Structural fallback: a PipingNetworkSegment element that carries a TagName
    # attribute marks PostProc (mirrors reconstructed._adapter_for). If the
    # originating marker is absent we scan for this.
    segment_element_tags: List[str] = field(
        default_factory=lambda: ["PipingNetworkSegment"]
    )
    segment_tagname_attrs: List[str] = field(
        default_factory=lambda: ["TagName"]
    )

    # --- Parser budgets (keep the scan shallow) ----------------------------
    # Stop searching for title-block identity after this many elements when the
    # originating system has already been resolved (identity lives near the top).
    header_element_budget: int = 400
    # When the originating marker is missing we must look deeper for the segment
    # TagName fallback; cap that scan so a huge file can't stall ingestion.
    segment_scan_element_budget: int = 20000


@dataclass
class BronzeConfig:
    """Top-level configuration for a Bronze ingestion run."""

    # Where the source export files are read from (a folder; scanned recursively).
    source_dir: str = ""

    # The Delta table to land into. Provide table_name (catalog) and/or table_path.
    table_name: str = "bronze_pid_documents"
    table_path: Optional[str] = None  # storage location for the Delta table

    # Which files to consider.
    path_glob: str = "*.xml"
    recursive_lookup: bool = True

    # Optional project/tenant tag stamped onto every row this run lands.
    project_code: Optional[str] = None

    # Store a UTF-8 decoded convenience copy of the payload (spec §3.3).
    store_content_text: bool = True

    # Partition columns for the Delta table (spec §8.1).
    partition_by: List[str] = field(default_factory=lambda: ["ingest_date"])

    # Hash algorithm for the version discriminator (spec §5.2). sha-256 is the
    # documented default; the Spark job uses the built-in sha2(.., 256).
    hash_bits: int = 256

    header: HeaderFieldConfig = field(default_factory=HeaderFieldConfig)

    def resolve_table(self) -> str:
        """Return the identifier used in SQL (path-based or catalog name)."""
        if self.table_path:
            return f"delta.`{self.table_path}`"
        return self.table_name
