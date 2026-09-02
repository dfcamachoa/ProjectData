# Bronze Layer — Design Specification

**Data Product:** Automatic Pre-commissioning Systemization based on P&ID interoperability data
**Layer:** Bronze (raw ingestion) — the first medallion tier
**Target runtime:** Delta Lake on Apache Spark (PySpark) — PoC on **local WSL, Spark local mode, embedded Derby metastore** (§8.4); cloud / Unity-Catalog conventions are the graduation target, not the PoC setup
**Status:** Draft v0.2
**Date:** 2026-08-27 (rev. 2026-08-29 — payload-size finding: drawings reach ~13 MB; `content_text` and payload-location decisions revised) (rev. 2026-08-31 — **format detection corrected**: both DEXPI and PostProc are exported with `OriginatingSystem="SPPID"`, so the discriminator is `PlantInformation/@Application="Dexpi"`, not the originating system — §4, §3.1) (rev. 2026-09-01 — **PostProc revision rule corrected against real files**: revision labels carry no `Revision.StatusType`, are wrapped in an unfixed element tag, and are scattered through the file; the drawing's `Drawing/@Revision` letter can run ahead of the last logged revision. Bronze now takes the **latest logged revision as a number+date pair** — the `Revision.RevisionNumber` companion of the maximum `Revision.TP_RevisionData` — §6, §3.1)
**Companions:** `data_specification.md` (§2.1, §4.2), `medallion_rdf_ido_strategy_mapping.md` (§3.1, §3.4, §4), `architecture_note.md` (§4, §5), `algorithm_spec.md`, `systemization_spec.md`, `silver_layer_spec.md` (§6 — the UDF that reads `content`).

---

## 0. Verdict up front

Bronze is the **cheapest layer to build and the one the PoC most conspicuously lacks**. Today `pidsys` runs `run_batch.py` over a folder, holds each drawing's DOM in memory, computes systems, and lets the DOM go — nothing is retained, there is no audit trail, and a run cannot be reproduced against the exact bytes that produced it (`architecture_note.md` §4: *"a batch script over an in-memory graph — no persistence, no query surface"*). Bronze fixes exactly that gap and nothing more.

The one discipline that makes this layer correct: **Bronze stores the source XML as-is and never interprets it.** No parsing of `GenericAttribute`, no topology reconstruction, no quality gates, no adapter selection — those all belong to Silver. Bronze's single job is to land every source file, once per distinct version, with enough ingestion metadata that every downstream layer can be replayed, audited, and time-travelled. The two design decisions that carry weight are (a) capturing the **drawing revision** at ingestion so the bi-temporal Gold layer has a valid-time axis to work with (`strategy §4b`), and (b) recording — but **not acting on** — the **format-detection signal** so Silver can choose the DEXPI vs. PostProc adapter exactly as `reconstructed._adapter_for` does today.

Everything below serves those two decisions and the immutability guarantee.

---

## 1. Purpose & scope

### 1.1 What Bronze is

Bronze is an **append-only, immutable landing zone** for the raw P&ID interoperability files the product consumes. Each source file is stored verbatim, one row per distinct file-version, alongside the ingestion metadata that `data_specification.md` §4.2 already reserves — *"ingestion timestamp, content hash, file size, source last-modified time"* — plus the small number of additional fields the medallion strategy needs (§3 below).

Bronze gives the product four things it does not have today:

- **Durability.** The source bytes survive the run, so any downstream result can be traced back to the exact input that produced it — the provenance root the whole "data product" claim rests on.
- **Replayability.** Silver, Gold, and the semantic layer can be rebuilt from Bronze at any time without re-collecting files from the source system.
- **An immutable audit trail.** Every version of every drawing that was ever ingested is retained; nothing is overwritten. This is the record an EPC audit or a stakeholder review reads against.
- **A stable ingestion boundary.** Everything upstream of Bronze (how files arrive from SmartPlant P&ID exports) is decoupled from everything downstream (parsing, reconstruction, rules).

### 1.2 What Bronze is NOT

This boundary is the most important thing in the document, because the medallion strategy's value depends on it holding:

| Concern | Belongs to | Why not Bronze |
|---|---|---|
| Parsing `GenericAttribute` name/value pairs into columns | **Silver** (`master_data.ga()`) | Bronze is format-agnostic raw storage; the moment it shreds attributes it has taken on the parser's schema and lost "raw as-is" |
| Topology reconstruction (skeleton + inline valve ordering) | **Silver** (`pidtool`/`bppidsys`) | Geometry-dependent, per-drawing, stateful — not an ingestion concern (`strategy §2`) |
| Choosing the DEXPI vs. PostProc adapter | **Silver** (`reconstructed._adapter_for`) | Bronze *records* the detection signal; it does not *act* on it (§4) |
| Data-quality expectations / gates (Great Expectations) | **Silver** (`strategy §3.3`) | Bronze accepts a file even if its content is later found unusable; quality judgement is Silver's |
| CDC — New/Modified/Deleted at object grain | **Silver** (`strategy §3.4`) | Bronze's grain is the *file version*, not the engineering object |
| Bi-temporal `validFrom`/`validTo` | **Gold** (`strategy §4`) | Bronze *captures the inputs* to valid time (revision + ingestion time); it does not model intervals |

Bronze does exactly one interpretive act on the file: it computes a content hash and reads a **small, safe set of header fields** (drawing number, revision, the `Application` format marker, `OriginatingSystem`) needed for identity, versioning, and routing. It does not descend into the network model. If a header field cannot be read, Bronze still lands the file and records the field as null with a `header_parse_ok = false` flag (§7) — the file is never rejected at Bronze for a content reason.

### 1.3 Position in the medallion architecture

```
   source exports                 ┌─────────── Bronze (this spec) ───────────┐
  (SmartPlant P&ID)   ─ ingest ─▶ │  raw XML as-is, append-only, immutable    │
   DEXPI / PostProc XML           │  + ingestion metadata + version identity  │
                                  └───────────────────┬───────────────────────┘
                                                      │ replayable source
                                  ┌───────────────────▼──────────── Silver ───┐
                                  │  parse (ga) → reconstruct → GX → CDC       │
                                  └───────────────────┬────────────────────────┘
                                              Gold (bi-temporal) → RDF/IDO → Jena/SPARQL
```

Bronze is `strategy §10`'s **phase-1, step-1** deliverable: *"Land persistence first, semantics second… This alone converts the PoC from a batch script into a data product with an audit trail — the architecture note's #4 gap — without touching the rules."*

---

## 2. Source inputs

The product accepts P&ID data in **two interoperability standards**, and Bronze must land both without distinguishing them by content (`data_specification.md` source-formats note):

| Project | Standard | Originating system | One file = |
|---|---|---|---|
| **Project A** | DEXPI / Proteus XML | `SPPID` (Application `Dexpi`) | one Document (§2.1) |
| **Project B** | INGR ISO-15926 PostProc XML | `SPPID` (no Application) | one Document (§2.1) |

Key facts that shape the ingestion contract:

- **One source file per Document.** *"One source file (DEXPI or PostProc) is generated per Document and holds its data"* (`data_specification.md` §2.1). The natural Bronze grain — one row per file — therefore coincides with one row per Document-version. This is a convenient alignment, not an assumption Bronze enforces: Bronze does not verify that a file contains exactly one Document; it lands whatever the file is.
- **Files are UTF-8 XML.** Bronze stores the raw bytes; it does not re-encode. Encoding normalisation, if ever needed, is a Silver concern.
- **Both formats share `OriginatingSystem="SPPID"`.** SmartPlant P&ID exports *both* the DEXPI and the ISO-15926 PostProc files, so the originating system is identical on both — it is **not** a format discriminator (§4). The discriminator is `PlantInformation/@Application`: DEXPI exports carry `Application="Dexpi"` (with `ApplicationVersion`, e.g. `1.3.1`); PostProc exports carry no `Application` attribute.
- **File sizes span a wide range.** Real sheets run from a few KB to **~13 MB**; the schema and storage choices (§3.1, §3.3, §8.1) account for that variance rather than assuming small files.
- **Off-page connectors (OPC) create cross-file relationships**, but those relationships live *between* Documents and are resolved in Silver (`data_specification.md` §2.11). Bronze lands each file independently; it does not attempt to pair OPCs or assemble drawing sets.
- **Both formats target the same master-data objects downstream** via a format adapter (`data_specification.md` §4.2), so Bronze deliberately keeps them in **one table**, discriminated only by a recorded `source_format` column — never by separate tables. This preserves the format-independence the interoperability promise is built on (`architecture_note.md` §2).

---

## 3. Ingestion metadata — the Bronze record

Every landed file becomes one Bronze row. The columns fall into four groups: the **raw payload**, the **reserved lineage metadata** (`data_specification.md` §4.2), the **version-identity** fields, and the **routing/valid-time** fields the medallion strategy adds.

### 3.1 Bronze table schema

| Column | Type | Group | Meaning | Source |
|---|---|---|---|---|
| `bronze_id` | string (UUID) | identity | Surrogate row key for this file-version | generated at ingest |
| `content` | binary | payload | The raw source file, byte-for-byte | the file itself |
| `content_text` | string *(optional, off by default)* | payload | UTF-8 decode of `content` — **not stored by default; decode on read instead** (§3.3) | derived, non-authoritative |
| `content_hash` | string (`sha256:<hex>`) | identity + lineage | Self-describing hash of the raw bytes — the version discriminator (§5.2) | computed |
| `file_size_bytes` | long | lineage | Size of the raw file — also the skew key for size-aware processing (§8.1; `silver_layer_spec.md` §6) | filesystem / decode |
| `source_path` | string | lineage | Full path/URI the file was ingested from | ingest input |
| `source_filename` | string | lineage | Basename of the source file | ingest input |
| `source_last_modified` | timestamp | lineage | The source file's own last-modified time | filesystem metadata |
| `ingested_at` | timestamp (UTC) | lineage | When Bronze landed this file (transaction-time seed, §6) | ingest clock |
| `ingest_run_id` | string | lineage | Identifier of the ingestion batch/run that landed the row | ingest orchestration |
| `originating_system` | string | routing | `SPPID` for **both** formats — captured as lineage only, **not** a format signal (§4) | header read (§4) |
| `source_format` | string enum | routing | `DEXPI` \| `POSTPROC` — the resolved format tag Silver keys its adapter on | derived from detection (§4) |
| `format_detection_method` | string enum | routing | How `source_format` was decided: `APPLICATION` \| `SEGMENT_TAGNAME` \| `UNKNOWN` (§4) | detection |
| `client_document_number` | string | identity | The client's drawing number, e.g. `362-09-PR-PID-01010` (§2.1) | header read |
| `document_number` | string | identity | Our internal drawing number, e.g. `215777C-36209-PID-0021-01010` (§2.1) | header read |
| `drawing_revision` | string | valid-time | The current revision — the highest-numbered populated `RevRow{N}No` (DEXPI) or the `Revision.RevisionNumber` **companion of the latest revision date** (PostProc) (e.g. `C`); note schemes vary (`01` numeric vs `B`/`C` alpha) (§2.1, §6) | header read |
| `drawing_revision_date` | string | valid-time | The current revision's issue date, **verbatim** — format is project-scoped (`12APR24` `DDMMMYY` in DEXPI/A; `2024/06/28` `YYYY/MM/DD` in PostProc/B) — from `RevRow{N}Date` (DEXPI) or the **latest** `Revision.TP_RevisionData` (PostProc) (§6); normalised to a real date in Silver/Gold, never at Bronze | header read |
| `header_parse_ok` | boolean | quality | False if any header field above could not be read (§7) | detection |
| `project_code` | string | routing | EPC project code — the **leading token of `document_number`** (e.g. `215777C`), via a project-scoped rule; an explicit ingest-run value overrides, and a derived-vs-run mismatch is flagged | derived / ingest input |
| `project_code_source` | string enum | routing | Which authority set `project_code`: `DOCUMENT_NUMBER` \| `INGEST_RUN` \| `SOURCE_PATH` \| `UNKNOWN` (parallel to `format_detection_method`) | detection |

Notes on specific columns:

- **`content` is authoritative; `content_text` is a convenience that is off by default.** The hash, replay, and audit trail are always computed against `content` (binary). `content_text` would exist only so SPARQL-free, SQL-only consumers and quick debugging can read the XML without a decode step; it is explicitly non-authoritative and, because drawings reach ~13 MB (§3.3), is **not stored by default** — a decode-on-read view serves the same convenience without doubling every retained row.
- **`content_hash` is the whole-file hash.** At Bronze this is correct and sufficient — Bronze versions *files*. The object-grain hashing the strategy calls for to avoid re-export churn (`strategy §3.4`) is a **Silver/CDC** concern and is deliberately *not* done here (§5.3).
- **`originating_system` is lineage, not routing logic.** Because SmartPlant P&ID stamps `SPPID` on both the DEXPI and the PostProc export, it cannot separate the two — it is retained for provenance only. Format is decided by the `Application` marker (§4).
- **`document_number` / `client_document_number` are read but not trusted for logic.** They are captured for human-readable identity and lineage; any downstream keying still happens on the parsed model in Silver, exactly as the specs require (`data_specification.md` §4.2 "Internal keys").
- **`drawing_revision` is the one field added purely to serve Gold.** See §6.
- **`project_code` is derived from the document, not the delivery.** It is the leading token of `document_number` — the EPC document number, sourced from the `OperationCenterDocNo` GenericAttribute in DEXPI/Project A (`215777C-36292-PID-0031-02231` → `215777C`) and from the **`Drawing/@Name` header attribute in PostProc/Project B** (`216097C-A22-PID-0021-0012-001` → `216097C`) — **confirmed**. Both take the leading `-`-delimited token; only the source attribute differs (a GenericAttribute vs a `Drawing` element attribute). Because it is intrinsic to the file it survives path moves, renames, format, and mixed-project batches — so one ingest run can safely carry several projects, which is what dissolves the multi-project-collision worry the tag was meant to solve. The per-format **source-attribute mapping** (which attribute feeds `document_number`) and the per-project **token rule** (delimiter + index) are reference-data config, not hardcode — the same externalisation the tagging conventions already use (`architecture_note.md` §3). An explicit ingest-run `project_code` **overrides** when supplied (the fallback for a missing attribute or an unconfigured project); `project_code_source` records the authority; and if the derived and run-supplied codes **disagree, the mismatch is flagged** (`project-code mismatch`), never silently reconciled — the same two-source cross-check as tag-vs-drawing unit (`data_specification.md` §2.1a). Format-inference is rejected outright (format ≠ project); source-path is last-resort only.
- **Client vs EPC document number is project-scoped.** In Project A the two differ — `client_document_number` = `DrawingNumber` (`362-92-PR-PID-02231`), `document_number` (EPC) = `OperationCenterDocNo` (`215777C-…`). In **Project B they are identical** — the client and EPC contractor drawing number are the same, both `Drawing/@Name` (`216097C-A22-PID-0021-0012-001`), so both columns hold that one value. Which attributes feed the two columns, and whether they coincide, is per-project config; `project_code` always derives from the EPC `document_number`'s leading token regardless.

### 3.2 The reserved-field alignment

`data_specification.md` §4.2 ("Lineage & operations") already reserves: *ingestion timestamp, content hash, file size, source last-modified time.* The strategy note confirms Bronze is where these land (`strategy §3.1`): *"land each `*.xml` … with the ingestion metadata `data_specification.md` already reserves … plus `OriginatingSystem` so the format adapter can be chosen downstream."* This spec adds only two things beyond that reserved set, and both are justified by a named downstream need:

1. `source_format` + `format_detection_method` (plus `originating_system` as lineage) — so Silver's `_adapter_for` decision is *recorded at ingest*, not re-derived (§4).
2. `drawing_revision` — so Gold's **valid time** has a source (`strategy §4b`), which the reserved set does not currently carry.

Everything else in the schema is operational plumbing (surrogate key, run id, source path) that no spec forbids and every audit trail needs.

### 3.3 Payload storage: `binary`, and why not stored `text`

Store `content` as **binary** (`BinaryType`) — the only representation guaranteed byte-faithful across encodings and BOM handling, and exactly the bytes the hash is computed over (§5.2).

**Do not materialise `content_text` by default.** An earlier draft recommended populating both on the assumption that drawings are "tens to low-hundreds of KB"; that assumption is **wrong** — real sheets reach **~13 MB**. A stored `content_text` then roughly **doubles a retained-forever row** (≈13 MB binary + ≈13 MB decoded text ≈ 26 MB), and because Bronze is append-only every version of every drawing pays that doubling across the whole audit history you are deliberately keeping (§5.1). Since `content_text` is a pure UTF-8 decode of `content` — non-authoritative and fully reconstructable — materialising it is the worst possible storage trade at this scale. Serve the SQL-readability it was meant to provide **on demand**: a view or UDF that decodes `content` on read. If a team still insists on materialising it, gate it behind a size threshold (e.g. only files < 256 KB) plus a `content_text_present` boolean — but **binary-only with decode-on-read is the recommended default**.

**Payload location — inline vs external blob.** The same ~13 MB finding raises *where* the payload lives: inline in Delta binary, or as an object-store blob that Bronze references by path + hash. **Keep it inline for now.** The instinct that multi-MB blobs wreck the table is largely mitigated by Parquet being **columnar** — a metadata/lineage query that projects only `content_hash`, `drawing_revision`, `source_format`, … never reads the `content` column, so the blobs do not slow the scans Bronze is actually queried with. The residual costs (write amplification, file/row-group sizing) are handled by tuning Delta target file sizes (§8.1) so a big payload does not force one giant row group. **Externalising** large payloads (Bronze holds path + hash; the blob lives in object store) is the *scale* fallback — warranted only when measured retained-payload volume becomes a storage/cost problem, because it adds a fetch indirection and a blob-vs-row consistency concern. Either way the choice is invisible to Silver: Silver reads `content` from Bronze (bytes-in-column now, blob-by-reference later), Bronze-mediated in both cases, so replayability holds (§5.3, `silver_layer_spec.md` §6).

---

## 4. Format / adapter detection (record, don't act)

Silver chooses its parser with `reconstructed._adapter_for`. Bronze's job is to **record an equivalent signal at ingest** so Silver reads it from a column instead of re-sniffing the DOM, and so the audit trail shows how each file was classified.

**`OriginatingSystem` is not the signal.** SmartPlant P&ID exports *both* formats with `OriginatingSystem="SPPID"` — confirmed from real headers:

```
DEXPI:    <PlantInformation SchemaVersion="4.1.1" OriginatingSystem="SPPID" … Application="Dexpi" ApplicationVersion="1.3.1">
PostProc: <PlantInformation SchemaVersion="3.3.3" OriginatingSystem="SPPID" … >   (no Application attribute)
```

So the discriminator is `PlantInformation/@Application`. Detection ladder (first match wins), recorded in `format_detection_method`:

1. **`APPLICATION`** — `PlantInformation/@Application` contains `Dexpi` ⟶ `source_format = DEXPI`. This is the clean, header-only path and the primary one. (An earlier draft used `OriginatingSystem="SPPID" ⟶ POSTPROC`; that was **wrong** — it would misclassify every DEXPI file, which are all SPPID too, and send them down the wrong Silver adapter and the wrong revision/doc extraction.)
2. **`SEGMENT_TAGNAME`** — no `Application` marker: fall back to the structural signal `_adapter_for` uses — a `PipingNetworkSegment` carrying a `TagName` ⟶ `POSTPROC` (PostProc has no `Application`); segments present but untagged ⟶ `DEXPI`. This requires a *shallow* scan, not a full parse — Bronze looks only far enough to find or rule out the marker.
3. **`UNKNOWN`** — neither signal resolves. Land the file anyway, set `source_format = null`, `format_detection_method = UNKNOWN`, `header_parse_ok = false`. Silver decides what to do with an unclassifiable file; Bronze never drops it.

`Application` takes precedence over the segment-tag fallback: a DEXPI file that also carries tagged segments still resolves DEXPI.

Design rules:

- **Bronze detects; Silver adapts.** The recorded `source_format` is advisory metadata. Silver remains free to re-confirm with `_adapter_for` — the single source of truth for *how to parse* stays in Silver. Bronze's value is that the classification is *captured and auditable at ingest time*, and that a run can be filtered/partitioned by format without opening files.
- **One table, two formats.** Detection populates a column; it never routes to a separate table or path (§2).
- **Keep the scan shallow.** Detection must not evolve into parsing. Reading `PlantInformation/@Application` (and, as fallback, a single segment `TagName`) is the whole budget; anything deeper is a smell that detection is over-reaching — pull it back. (At ~13 MB this matters: use an incremental/streaming reader — `iterparse` with early exit — so detection never has to build a full DOM just to read a header.)

---

## 5. Version identity, immutability & idempotency

### 5.1 Append-only, immutable

Bronze is **append-only**. A Bronze row, once written, is never updated or deleted. A new export of a drawing is a **new row**, not an edit of the old one. This is the immutability guarantee the audit trail depends on, and it maps directly onto Delta's append semantics plus time travel.

Operationally, on the Bronze Delta table:

- Writes are `append` only. No `MERGE` that updates payload columns, no `UPDATE`, no `DELETE` in normal operation.
- Enable Delta features that reinforce this: table property `delta.appendOnly = true`. This makes the immutability contract a property of the table, not merely a convention in the writer.
- **Payload-version retention = handover + 5 years** (records decision), anchored to each project's handover milestone. Old versions are **never deleted as routine** — deletion is a deliberate, governed exception. The audit trail lives in the **rows** of this append-only table, so it is preserved by the append discipline itself, not by Delta time-travel.
- **`VACUUM` retention is operational, not archival — set it modest (~30 days).** It governs only Delta's table-level time-travel / rollback and reclaims `OPTIMIZE`/compaction leftovers; because every payload version is a *live row* (not a superseded table-version), a short `VACUUM` window never touches the audit history. Do not set it long to "protect history" (unnecessary) or short to "prune history" (impossible) — the two knobs are independent.
- **Reconcile long retention with 13 MB payloads by lifecycle-tiering, not deletion:** age old versions hot→cold (archive object storage) while keeping them logically retained and replayable — exactly what the externalised-blob option (§3.3, §9.8) enables.

### 5.2 The version key

The **`content_hash` (sha-256 over raw bytes) is the version discriminator.** Two ingests of byte-identical content are the same version; any byte difference is a new version. Human-readable identity (`document_number`, `drawing_revision`) travels alongside but is *not* the key — two exports at the same nominal revision can differ byte-wise (re-export churn), and a revision string can be missing or wrong. The hash is the objective truth.

The hash is a **content fingerprint, not a security primitive**, and that framing fixes its whole recipe:

- **Algorithm: sha-256.** FIPS-approved (so it clears an org data-integrity standard), collision-resistant enough for an audit key, and hardware-accelerated on the target runtime (SHA-NI / the JVM `MessageDigest` intrinsic Spark's `sha2` uses), so hashing every file — including the ~13 MB ones (§3.3) — is cheap. **Not MD5/SHA-1** (both broken; MD5 in the strategy note §3.4 is a *Silver-CDC* hash, a different concern); SHA-512 is an acceptable swap only if a standard names it.
- **No salt, no keyed hash (HMAC), no per-run seed.** Content-addressing requires identical bytes → identical hash, **globally and forever**, or dedup (§5.3) collapses. A salt does the exact opposite of what is needed here; this is categorical, not a preference.
- **No pre-hash normalisation.** Hash the **exact raw bytes as ingested, before any decode** — no BOM strip, no CRLF↔LF, no whitespace/attribute canonicalisation. Normalising would (a) desync the hash from the stored `content`, and (b) collide genuinely distinct byte-versions and make Bronze **silently lose a version**; whitespace/line-ending churn that carries no *engineering* change is **Silver CDC's** call (object grain, `strategy §3.4`), never Bronze's. BOM/encoding is therefore legitimately part of the version identity (§2).
- **Stored self-describing as `sha256:<hex>`** (the OCI-image / Git-object convention). The algorithm is part of the value, so a future migration to another function can dedup within-algorithm during the transition instead of silently double-storing every file.
- **Trust the hash on a dedup match** (no byte-compare — sha-256 collision probability is negligible), and rely on **cross-engine determinism**: Spark `sha2(content,256)` and Python `hashlib.sha256(content).hexdigest()` agree, so a re-ingest dedups correctly whichever engine computes it.

Recommended logical uniqueness for a *distinct landed version*:

```
(content_hash)                      — global dedup: identical bytes land once
```

with `(document_number, drawing_revision, content_hash)` available as the human-facing version tuple for reporting.

### 5.3 Idempotency & replay

**Re-ingesting the same file must not create a duplicate Bronze row.** The ingestion job is idempotent on `content_hash`:

- Before appending, the job checks whether `content_hash` already exists in Bronze. If it does, the file is **silently skipped** (already landed) — no second payload row, and, by decision, **no sightings record** (kept simple for now; §9.4). The Bronze row's `ingested_at` therefore stays **first-seen** and is never updated — mutating it would break append-only immutability (§5.1) and corrupt the transaction-time seed Gold reads (§6). If re-presentation provenance ("when did we last receive this, from where") is ever wanted, it is added as a *separate* append-only `bronze_sightings` table, never as an in-row counter — but that is deferred, not built.
- Because dedup is on content, a bit-identical re-export is a no-op, while a genuine re-export (even at the same revision) lands as a new version — which is exactly what CDC in Silver then reasons about (`strategy §3.4`).
- Implement the skip as an anti-join against existing hashes (batch) or a Delta `MERGE ... WHEN NOT MATCHED THEN INSERT` keyed on `content_hash` (which inserts new versions and ignores known ones without ever mutating an existing row). The `appendOnly` property still holds because `MERGE` here only inserts.

**Whole-file hash is deliberate at Bronze.** The strategy's caution about whole-file hashing flooding CDC with false deltas (`strategy §3.4`) is a caution about *CDC*, which operates at **object grain in Silver**. At Bronze, the unit of versioning *is* the file, so a whole-file hash is precisely right: it answers "have I already stored these exact bytes?" Object-grain hashing to distinguish engineering change from re-export churn is Silver's job and must not be pulled forward into Bronze.

**Replay** means: truncate/rebuild any downstream layer purely from Bronze, deterministically, because Bronze holds every version's exact bytes plus the run metadata. No downstream layer should ever need to reach back past Bronze to the original export folder.

---

## 6. Feeding the bi-temporal Gold layer

Gold is bi-temporal (`strategy §4`), and its two axes both originate at ingestion — so Bronze must capture the seeds even though it models no intervals itself:

| Gold axis | Meaning | Bronze seed |
|---|---|---|
| **Transaction / system time** | when the platform *learned* a fact | `ingested_at` (§3.1) — comes free |
| **Valid time** | when the plant configuration a record describes was *true* | `drawing_revision` (§3.1) — the field added for this purpose |

The strategy is explicit (`§4b`): valid time is *"driven by drawing revision. A valve added at Rev C is valid-from Rev C's issue date."* Two consequences for Bronze:

- **Capture `drawing_revision` at ingest**, from the drawing header (`data_specification.md` §2.1, Revision `01`). Without it, Gold has only one usable axis and "bi-temporal" collapses (`strategy §4b`, risk §9.7).
- **Capture the revision *issue date* — the source does carry it (confirmed).** The DEXPI title block exposes a **revision-history table** as header attributes: `RevRow{N}No` / `RevRow{N}Date` / `RevRow{N}Desc` (e.g. `RevRow2No=C`, `RevRow2Date=12APR24`, `RevRow2Desc="ISSUED FOR HAZOP (IFH)"`). The **current revision** is the highest-numbered populated `RevRow{N}`; Bronze captures its number into `drawing_revision` and its date into `drawing_revision_date`, **verbatim** — dates arrive in a project-local `DDMMMYY` form (`12APR24`) that Bronze does *not* normalise (parsing to a real date is Silver/Gold, per the raw-capture contract). Do **not** use `DateCreated` (the SPPID object-creation timestamp) or `IFCDrawnDate` for valid-time — the revision-row date is the issue date Gold's valid-time axis wants. Where a file genuinely carries no revision row, Gold falls back to revision-sequence order with `source_last_modified` as tie-break, flagged. Bronze's contract holds: *capture whatever revision-dating the source offers, verbatim; never fabricate it.*
- **PostProc / Project B — the latest logged revision, as a number+date pair (corrected against real files, 2026-09-01).** ISO-15926 PostProc carries **one revision-label object per revision**, each a set of `GenericAttribute` `Name`/`Value` pairs: `Revision.RevisionNumber` (e.g. `B`), `Revision.TP_RevisionData` (the date, e.g. `2024/09/30`), `Revision.Text` (description), and prepared/checked/approved-by. Three properties of the **real** export corrected an earlier draft's assumptions and shape the rule:
  - **No `Revision.StatusType`.** The earlier draft keyed labels off `Revision.StatusType="Revision"`; real files carry no such field. A revision label is therefore identified structurally — *any element that directly holds a `Revision.RevisionNumber` GenericAttribute is a revision label* — and the label's **wrapper element tag is not relied upon** (it varies by export), which makes the reader robust to the actual tag.
  - **Labels are scattered through the file**, not clustered in the title block; the current revision's label can sit deep, so a fixed header budget would miss it.
  - **`Drawing/@Revision` can run ahead of the last logged revision.** The `<Drawing>` header letter (e.g. `F`) frequently has **no matching revision label** (labels run A…E), which would leave the date null and the number/date mismatched if the two were read from different places.

  So Bronze takes the **latest logged revision as a consistent pair** (project decision): it selects the revision label with the **maximum** `Revision.TP_RevisionData` and reads **both** `drawing_revision` (that label's `Revision.RevisionNumber`) and `drawing_revision_date` (that label's date) from it. PostProc dates are `YYYY/MM/DD`, so the lexical-max date is the chronological latest; the number is always the *companion* of that date, never `Drawing/@Revision`. `Drawing/@Revision` is retained only as a **fallback** for the number when a file has no dated revision labels at all. The date format differs from DEXPI — `YYYY/MM/DD` vs `DDMMMYY` — which is precisely why Bronze stores it **verbatim** and the **normaliser in Silver/Gold is format-aware**. So the extraction is format-specific — DEXPI reads flat `RevRow{N}` header attributes and takes the highest `N`; PostProc groups the scattered `Revision.*` GenericAttributes per label and takes the latest by date — but both feed the same two Bronze columns, the per-format source-mapping pattern described for `project_code` (§3.1).
- **The full revision history is Silver's, not Bronze's.** All the revisions (DEXPI `RevRow*` rows / PostProc revision Labels — each with by / checked / approved / description) are richer lineage than *versioning* needs, and structuring a multi-row/multi-object table is past Bronze's shallow-header remit (§1.2, §4). Bronze reads only the current-revision number + date (two fields, squarely in the permitted identity/versioning whitelist); parsing the whole revision history into a table is a Silver enrichment. The *decision* (capture a verbatim revision date) is now **confirmed for both formats** — only the source shape differs (flat attributes vs Label objects), an adapter detail.
- **The revision scheme is externalised to Project Reference Data.** The revision **token set and ordering** (alpha `A<B<C`, numeric `0<1<2`, or a project-specific mix) and the **date format** (`DDMMMYY`, `YYYY/MM/DD`, …) are project-scoped and belong in reference data, not code — the same rules-as-data externalisation as tagging conventions, fluids, and boundaries (`architecture_note.md` §3). Bronze still captures `drawing_revision` / `drawing_revision_date` **verbatim** and needs no scheme at ingest (current revision comes positionally in DEXPI — the last populated `RevRow{N}` — and, in PostProc, as the label carrying the latest `Revision.TP_RevisionData`, which for the `YYYY/MM/DD` form needs only a lexical max, not the project scheme); the scheme is consumed **downstream** by Silver/Gold to *order* revisions across projects/formats and to normalise the verbatim date for the bi-temporal valid-time axis. Onboarding a project's revision convention is then a reference-data edit, not a code change.

Bronze does **not** compute `validFrom`/`validTo`, does not order revisions, and does not close intervals — all of that is Gold. Bronze only guarantees the raw inputs are present and faithful.

---

## 7. Data quality at Bronze (minimal, non-gating)

Bronze is not where Great Expectations lives (`strategy §3.3`), and it must not adopt GX's default abort-on-fail posture. Bronze's quality stance mirrors the specs' governing principle — *"quality issues raise flags, they do not abort"* (`algorithm_spec §11`, echoed in `strategy §3.3`) — but at the coarsest possible level:

Bronze performs only **landing-integrity** checks, and every one of them is a *flag*, never a rejection:

| Check | On failure |
|---|---|
| File is non-empty (`file_size_bytes > 0`) | Land it; flag `header_parse_ok = false`, empty payload recorded |
| File is readable as bytes | If truly unreadable, the ingest run logs it and skips — the only case Bronze does not land, because there is nothing to land |
| Header fields readable (`document_number`, `drawing_revision`, the `Application` marker) | Land it; set the unreadable field(s) null and `header_parse_ok = false` |
| Format classifiable (§4) | Land it; `source_format = null`, `format_detection_method = UNKNOWN` |

Explicitly **not** at Bronze: XML well-formedness enforcement, schema validation, `GenericAttribute` presence, connectivity checks, ghost-equipment filtering, tag round-trip (`compose(decode(tag))==tag`) — all Silver (`strategy §3.3`, `data_specification.md` §4.2). Bronze will happily store a malformed or partial XML file; discovering it is unusable is Silver's job, and the immutable Bronze copy is what lets that diagnosis be reproduced.

The reason this matters: the PoC's honest-partial-result behaviour (`algorithm_spec §11`) is a *feature*. If Bronze started rejecting files, a whole drawing could vanish before Silver ever gets the chance to produce a flagged partial result. Bronze rejecting a file would be a regression, not a safeguard.

---

## 8. Storage layout, partitioning & PySpark ingestion

### 8.1 Table & partitioning

One Delta table, `bronze.pid_documents` (name illustrative). Partitioning is chosen for how Bronze is *written and replayed*, not how Silver queries it:

- **Partition by `ingest_date`** (derived `date(ingested_at)`), and optionally sub-partition by `source_format` or `project_code`. Ingestion is naturally batch-by-day, replay and audit are naturally "what did we land, when", and format/project are low-cardinality filters that let Silver read one format's files without scanning the other.
- **Do not partition by `content_hash` or `document_number`** — high-cardinality, would produce the small-files problem Spark punishes.
- **Payloads span a wide range — a few KB to ~13 MB — so Bronze faces two opposite pressures at once.** The **many-small-files** pattern (compact / `OPTIMIZE`; prefer a modest number of larger Delta files per partition over one file per drawing) *and* occasional **very large rows**: tune target file / row-group sizes so a 13 MB payload does not force one giant row group, and keep `content_text` unmaterialised (§3.3) so no row is doubled. Because Parquet is columnar, lineage/metadata queries that don't project `content` never pay the blob cost regardless.

### 8.2 Parallelism is over *files*, not rows

The strategy is emphatic that Spark's role here is scale-out over drawings, not a reformulation of any algorithm (`strategy §2`, §3.2). At Bronze this is straightforward because ingestion is embarrassingly parallel per file:

- Read the input folder with Spark's binary-file source (`spark.read.format("binaryFile")`), which yields one row per file with `path`, `modificationTime`, `length`, and `content` (binary) — a near-perfect match for the Bronze lineage columns.
- Compute `content_hash`, read the shallow header fields (§4), and detect format in a `mapInPandas` / UDF pass — **per file, no cross-file state**.
- Deduplicate against existing hashes (§5.3) and append.

Because the payload column can reach ~13 MB, keep Arrow batches small on the header-detection pass (a modest `spark.sql.execution.arrow.maxRecordsPerBatch`) so a task never buffers many large blobs at once; Bronze itself never builds a DOM (detection is a shallow streaming read, §4), so its per-task memory stays low even for the big files — the heavier DOM cost is Silver's, handled there (`silver_layer_spec.md` §6).

Sketch (illustrative, not final code):

```python
raw = (spark.read.format("binaryFile")
            .option("pathGlobFilter", "*.xml")
            .option("recursiveFileLookup", "true")
            .load(SOURCE_DIR))
# raw columns: path, modificationTime, length, content

bronze = (raw
    .withColumn("content_hash",       concat(lit("sha256:"), sha2(col("content"), 256)))  # self-describing (§5.2)
    .withColumn("file_size_bytes",    col("length"))
    .withColumn("source_path",        col("path"))
    .withColumn("source_last_modified", col("modificationTime"))
    .withColumn("ingested_at",        current_timestamp())
    .withColumn("ingest_run_id",      lit(RUN_ID))
    # header read + format detection: shallow, per-file
    .transform(detect_and_read_headers)   # adds source_format, format_detection_method,
                                          # originating_system, document_number,
                                          # client_document_number, drawing_revision,
                                          # drawing_revision_date, project_code, header_parse_ok
    .withColumn("bronze_id",          expr("uuid()"))
    .withColumn("ingest_date",        to_date("ingested_at")))

# idempotent append: insert only unseen content_hash
(DeltaTable.forName(spark, "bronze.pid_documents")
    .alias("t")
    .merge(bronze.alias("s"), "t.content_hash = s.content_hash")
    .whenNotMatchedInsertAll()
    .execute())
```

- `detect_and_read_headers` must stay shallow (streaming/partial XML read for `PlantInformation/@Application`, then a single segment-tag probe as fallback) — it is the one place the temptation to "just parse a bit more" appears, and §1.2/§4 forbid it. At ~13 MB a shallow `iterparse` with early exit also keeps detection cheap.
- The `MERGE ... whenNotMatchedInsertAll` gives idempotency while honouring `delta.appendOnly = true` (inserts only).

### 8.3 Streaming variant (optional, later)

If exports arrive continuously rather than in nightly batches, the same logic runs as a Delta/Spark structured-streaming job with the file source and `foreachBatch` doing the idempotent merge. For the PoC, **batch is sufficient** and simpler; note the streaming path only as a forward option so the schema doesn't need to change to adopt it.

### 8.4 PoC runtime & metastore (local WSL)

The PoC runs on **local WSL (Windows) with Spark in local mode** — one JVM, no cluster, no Unity Catalog. That collapses §9.7's naming/storage/access question to a local shape, and the cloud conventions become the graduation target rather than the PoC setup:

- **Metastore: embedded Apache Derby** — Spark's *default* Hive metastore, so there is almost nothing to configure. `enableHiveSupport()` auto-creates a `metastore_db/` on first use, giving named tables: `bronze.pid_documents` (Hive's two-level `database.table`; Unity Catalog's third `catalog.` level simply collapses, so the schema/table names carry over **unchanged** on graduation). If named-table ergonomics aren't needed, **path-based Delta needs no metastore at all** (`DeltaTable.forPath(...)`) — the simpler option, and the full Delta API (MERGE, time-travel, OPTIMIZE) is available either way. Both are legitimate; pick named tables for the SQL-readability the spec values (§3.3), path-based for zero setup.
- **Single-session only.** Embedded Derby allows one JVM against `metastore_db/` at a time — fine for a single-user PoC; stop one Spark session before starting another (a lingering session throws *"Another instance of Derby may have already booted the database"*). The low-effort upgrade, if concurrency ever bites, is a local Postgres metastore backend (or Derby network-server mode).
- **Keep all state on the WSL-native filesystem** (`~/…`, e.g. `/home/you/pid/…`), **never under `/mnt/c/…`** — the Windows mount's I/O and file-locking quirks upset both Derby and Delta's `_delta_log`. This is the main WSL footgun.
- **A system Spark can shadow the venv.** If `SPARK_HOME` / `PYTHONPATH` point at an installed Spark (e.g. `/opt/spark` at a different major version), Python imports *that* pyspark instead of the venv's, breaking against the pinned `delta-spark` (a `_to_seq` ImportError / `DeltaSparkSessionExtension` class-not-found). Keep the pair matched (pyspark 3.5.x ↔ delta-spark 3.x) and `unset SPARK_HOME PYTHONPATH` for the venv. **WSL also avoids the `winutils.exe` / `HADOOP_HOME` mess** native Windows Spark requires; just ensure a compatible JDK (Java 8/11/17 for Spark 3.5).
- **Access control is moot locally** (single user). RBAC, external locations, and storage credentials belong to the cloud graduation (§9.7), not the PoC.

---

## 9. Open decisions for the team

These need a call before or during implementation; none blocks starting:

1. **Revision issue date — RESOLVED (§6), PostProc rule corrected against real files (2026-09-01).** Confirmed present: the DEXPI title block carries a revision-history table (`RevRow{N}No`/`Date`/`Desc`), so Gold gets a **true date** valid-time axis; Bronze takes the highest `RevRow{N}` (`DDMMMYY`). For **PostProc/B**, the real export differs from the earlier assumption: revision labels carry **no `Revision.StatusType`**, sit in an **unfixed wrapper element**, are **scattered through the file**, and `Drawing/@Revision` can run ahead of the last logged revision. Bronze therefore takes the **latest logged revision as a number+date pair** — the `Revision.RevisionNumber` companion of the maximum `Revision.TP_RevisionData` (`YYYY/MM/DD`) — with `Drawing/@Revision` only a fallback when no dated label exists. Dates are captured **verbatim** and normalised (format-aware) by Silver/Gold, never Bronze; `DateCreated`/`IFCDrawnDate` are explicitly *not* the valid-time source. Full revision history is a Silver enrichment. EPC doc number for B is `Drawing/@Name` (§3.1, #6). The **revision scheme** (token ordering + date format) is externalised to Project Reference Data (§6). Client vs EPC document number is project-scoped: distinct in A, identical in B (§3.1).
2. **`content_text` on/off — RESOLVED (§3.3).** Binary-only; `content_text` is **not stored by default** — drawings reach ~13 MB, so materialising a derivable UTF-8 decode roughly doubles every retained, append-only row. Serve SQL-readability as a decode-on-read view (or UDF); materialise only below a size threshold (e.g. < 256 KB) plus a `content_text_present` flag if a team insists.
3. **Hash algorithm & salt — RESOLVED (§5.2).** sha-256 (FIPS-approved, hardware-accelerated on the runtime); **no salt / no keyed hash / no per-run seed** (content-addressing needs identical-in → identical-out, forever); **no pre-hash normalisation** (hash the exact raw bytes before decode — normalising would desync the hash from the payload and silently lose byte-versions; whitespace/BOM churn is Silver CDC's concern); stored **self-describing as `sha256:<hex>`** so a future algorithm change can't silently break dedup. Only genuinely external item: confirm no org data-integrity standard mandates a different function (sha-256 clears FIPS, so most are covered) — swap and re-tag if so.
4. **Re-presentation tracking — RESOLVED (§5.3).** **Silent skip**, no sightings — kept simple for now. A byte-identical re-ingest is dropped (no second payload row), and the Bronze row's first-seen `ingested_at` is never mutated (immutability + transaction-time seed). Trade-off accepted: "when did we last receive this / from where" is not answerable while off. If that provenance is later needed, add a *separate* append-only `bronze_sightings` table (never an in-row counter); deferred, not built.
5. **Retention / VACUUM — RESOLVED (§5.1).** Payload-version retention = **handover + 5 years**, anchored to each project's handover milestone; old versions are never deleted as routine, only by governed exception. Two knobs kept distinct: the audit trail lives in the append-only **rows**, so `VACUUM` is operational-only (~30 days, for rollback/compaction) and never threatens history. Long retention is reconciled with 13 MB payloads by hot→cold lifecycle-tiering (archive storage), not deletion (§3.3, §9.8).
6. **Project/tenant tagging — RESOLVED (§3.1).** Primary authority: **derive `project_code` from the EPC document number** — the leading token of `document_number` (`OperationCenterDocNo` in DEXPI/Project A → `215777C`; `Drawing/@Name` in PostProc/Project B → `216097C` — both confirmed). Being intrinsic to the file, it survives path/run/format and lets one run carry many projects, dissolving the collision worry. Source-attribute mapping (per format) and token rule (per project) are reference-data config, not hardcode. An explicit ingest-run value **overrides** when supplied; `project_code_source` records the authority; a derived-vs-run disagreement is **flagged**, not silently picked. Format-inference is rejected; source-path is last-resort only.
7. **Table naming, catalog & access — RESOLVED for the PoC (§8.4); cloud conventions retained as graduation target.** PoC runs local Spark in WSL: Delta tables on the **WSL-native filesystem**, registered as `bronze.pid_documents` in the default **embedded Derby** Hive metastore (`enableHiveSupport()`, single-session) — or path-based Delta with no metastore. No RBAC (single user). **Graduation to a cluster** (owner-ratified): tier-as-schema naming `<catalog>.bronze.pid_documents` in Unity Catalog; external-location storage for lifecycle-tiering (§5.1); append-only enforced at the permission layer (ingestion service-principal writes, no `UPDATE`/`DELETE` grants); eng-only read; and `project_code` row-level filtering for client-confidentiality isolation.
8. **Payload location: inline vs external blob — RESOLVED for now (§3.3).** Keep `content` inline in Delta binary; because Parquet is columnar, lineage/metadata scans skip the blob column, so multi-MB payloads don't slow the queries Bronze is actually used for. Revisit externalising to object store (Bronze holds path + hash) only on a **measured** retained-payload volume threshold. Silver reads `content` from Bronze either way, so the choice doesn't change the Silver contract (`silver_layer_spec.md` §6).
9. **Format detection signal — RESOLVED / CORRECTED (§4).** Both DEXPI and PostProc are exported by SmartPlant P&ID with `OriginatingSystem="SPPID"`, so the originating system is **not** a discriminator (an earlier draft's `SPPID ⟶ POSTPROC` rule was wrong and would misclassify every DEXPI file). The discriminator is `PlantInformation/@Application`: `Dexpi` ⟶ DEXPI (`APPLICATION` method); else a `PipingNetworkSegment@TagName` ⟶ POSTPROC, untagged segments ⟶ DEXPI (`SEGMENT_TAGNAME`); else UNKNOWN. `originating_system` is still captured, as lineage only. The `Application`/segment-tag names are the confirmed real-file signals; keeping them in reference-data config (not hardcode) lets a future dialect be onboarded without a code change.

---

## 10. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Scope creep into parsing.** Header-read / format-detection quietly grows into a full parser, duplicating Silver's `ga()`/`_adapter_for` and re-introducing the two-schema problem the consolidation just removed (`architecture_note.md` §2). | Header read is a fixed, shallow whitelist (§4); anything deeper is a Silver PR, not a Bronze one. |
| 2 | **Whole-file hash misused for CDC.** Someone wires Silver's change-detection to `content_hash`, flooding CDC with re-export churn (`strategy §3.4`). | Document loudly (§5.3) that `content_hash` is *version identity*, not *engineering-change* detection; CDC hashes at object grain in Silver. |
| 3 | **Missing revision ⟶ broken bi-temporal Gold.** `drawing_revision` unread or null collapses valid time (`strategy §4b`). | Capture at ingest, flag when unread (§7); §9.1 resolves the date question; Gold has a documented fallback. |
| 4 | **Bronze starts rejecting files**, killing the honest-partial-result behaviour (`algorithm_spec §11`). | Bronze flags, never rejects (§7); the only non-land case is a physically unreadable file. |
| 5 | **Storage pressure from large + many payloads.** Mixed KB-to-13 MB files produce both a small-files problem and oversized row groups; a materialised `content_text` would double every retained row. | Binary-only with decode-on-read (§3.3); tune file/row-group sizes and `OPTIMIZE` (§8.1); columnar projection keeps lineage scans off the blob; externalise on a measured threshold (§9.8). |
| 6 | **Format misclassification from a false signal.** Keying format off `OriginatingSystem` (both `SPPID`) would send every DEXPI file down the PostProc adapter and the wrong revision/doc extraction. | Discriminate on `PlantInformation/@Application="Dexpi"`, with the segment-TagName structural fallback (§4); `originating_system` is lineage only. |
| 7 | **Two-table drift** between DEXPI and PostProc. | One table, format is a column (§2, §4) — preserves the format-independence the interoperability contract rests on. |
| 8 | **Silent overwrite** of a prior version. | `delta.appendOnly = true` + insert-only MERGE make immutability a table property, not a convention (§5.1). |

---

## 11. One-paragraph summary

Bronze converts the PoC from a batch script into a data product with an audit trail (`architecture_note.md` §4) by landing every DEXPI and PostProc file **as-is**, once per distinct byte-version, in one append-only, immutable Delta table — discriminated by a recorded format column, keyed on a whole-file sha-256, and carrying exactly the lineage metadata `data_specification.md` §4.2 already reserves plus two justified additions: the **format-detection signal** (so Silver's `_adapter_for` decision is auditable, not re-sniffed) and the **drawing revision** (so Gold's valid-time axis has a source). Because SmartPlant P&ID exports both formats with `OriginatingSystem="SPPID"`, the format is decided by `PlantInformation/@Application="Dexpi"` (with a segment-TagName fallback), not the originating system. It does one interpretive act only — a shallow header read for identity, versioning, and routing — and refuses every other temptation: no parsing, no reconstruction, no quality gates, no CDC, no interval modelling, all of which belong to Silver and Gold. Payloads run to ~13 MB, so Bronze stores `content` as binary only (a decoded `content_text` would double every retained row) and keeps it inline while columnar projection spares lineage scans the blob cost. Get this boundary right and every layer above can be replayed, audited, and time-travelled against the exact bytes that produced it; blur it and Bronze stops being raw and the provenance story the whole program turns on is compromised at its root.