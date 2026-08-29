# Bronze Layer — Design Specification
 
**Data Product:** Automatic Pre-commissioning Systemization based on P&ID interoperability data
**Layer:** Bronze (raw ingestion) — the first medallion tier
**Target runtime:** Delta Lake on Apache Spark (PySpark)
**Status:** Draft v0.1
**Date:** 2026-08-27
**Companions:** `data_specification.md` (§2.1, §4.2), `medallion_rdf_ido_strategy_mapping.md` (§3.1, §3.4, §4), `architecture_note.md` (§4, §5), `algorithm_spec.md`, `systemization_spec.md`.
 
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
 
Bronze does exactly one interpretive act on the file: it computes a content hash and reads a **small, safe set of header fields** (drawing number, revision, `OriginatingSystem`) needed for identity, versioning, and routing. It does not descend into the network model. If a header field cannot be read, Bronze still lands the file and records the field as null with a `header_parse_ok = false` flag (§7) — the file is never rejected at Bronze for a content reason.
 
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
| **Project A** | DEXPI / Proteus XML | (DEXPI export) | one Document (§2.1) |
| **Project B** | INGR ISO-15926 PostProc XML | `SPPID` | one Document (§2.1) |
 
Key facts that shape the ingestion contract:
 
- **One source file per Document.** *"One source file (DEXPI or PostProc) is generated per Document and holds its data"* (`data_specification.md` §2.1). The natural Bronze grain — one row per file — therefore coincides with one row per Document-version. This is a convenient alignment, not an assumption Bronze enforces: Bronze does not verify that a file contains exactly one Document; it lands whatever the file is.
- **Files are UTF-8 XML.** Bronze stores the raw bytes; it does not re-encode. Encoding normalisation, if ever needed, is a Silver concern.
- **Off-page connectors (OPC) create cross-file relationships**, but those relationships live *between* Documents and are resolved in Silver (`data_specification.md` §2.11). Bronze lands each file independently; it does not attempt to pair OPCs or assemble drawing sets.
- **Both formats target the same master-data objects downstream** via a format adapter (`data_specification.md` §4.2), so Bronze deliberately keeps them in **one table**, discriminated only by a recorded `originating_system` / `source_format` column — never by separate tables. This preserves the format-independence the interoperability promise is built on (`architecture_note.md` §2).
---
 
## 3. Ingestion metadata — the Bronze record
 
Every landed file becomes one Bronze row. The columns fall into four groups: the **raw payload**, the **reserved lineage metadata** (`data_specification.md` §4.2), the **version-identity** fields, and the **routing/valid-time** fields the medallion strategy adds.
 
### 3.1 Bronze table schema
 
| Column | Type | Group | Meaning | Source |
|---|---|---|---|---|
| `bronze_id` | string (UUID) | identity | Surrogate row key for this file-version | generated at ingest |
| `content` | binary | payload | The raw source file, byte-for-byte | the file itself |
| `content_text` | string *(optional)* | payload | UTF-8 decode of `content`, for convenience/SQL — see §3.3 | derived, non-authoritative |
| `content_hash` | string (sha-256 hex) | identity + lineage | Hash of the raw bytes — the version discriminator (§5) | computed |
| `file_size_bytes` | long | lineage | Size of the raw file | filesystem / decode |
| `source_path` | string | lineage | Full path/URI the file was ingested from | ingest input |
| `source_filename` | string | lineage | Basename of the source file | ingest input |
| `source_last_modified` | timestamp | lineage | The source file's own last-modified time | filesystem metadata |
| `ingested_at` | timestamp (UTC) | lineage | When Bronze landed this file (transaction-time seed, §6) | ingest clock |
| `ingest_run_id` | string | lineage | Identifier of the ingestion batch/run that landed the row | ingest orchestration |
| `originating_system` | string | routing | `SPPID` (PostProc) or the DEXPI originator — the primary format signal | header read (§4) |
| `source_format` | string enum | routing | `DEXPI` \| `POSTPROC` — the resolved format tag Silver keys its adapter on | derived from detection (§4) |
| `format_detection_method` | string enum | routing | How `source_format` was decided: `ORIGINATING_SYSTEM` \| `SEGMENT_TAGNAME` \| `UNKNOWN` (§4) | detection |
| `client_document_number` | string | identity | The client's drawing number, e.g. `362-09-PR-PID-01010` (§2.1) | header read |
| `document_number` | string | identity | Our internal drawing number, e.g. `215777C-36209-PID-0021-01010` (§2.1) | header read |
| `drawing_revision` | string | valid-time | The drawing's Revision, e.g. `01` (§2.1) — the valid-time seed (§6) | header read |
| `header_parse_ok` | boolean | quality | False if any header field above could not be read (§7) | detection |
| `project_code` | string *(optional)* | routing | Project/tenant tag (`A`, `B`, or a project id) if supplied by the ingestion run | ingest input |
 
Notes on specific columns:
 
- **`content` is authoritative; `content_text` is a convenience.** The hash, replay, and audit trail are always computed against `content` (binary). `content_text` exists only so SPARQL-free, SQL-only consumers and quick debugging can read the XML without a decode step; it is explicitly non-authoritative and may be omitted to save space (§3.3).
- **`content_hash` is the whole-file hash.** At Bronze this is correct and sufficient — Bronze versions *files*. The object-grain hashing the strategy calls for to avoid re-export churn (`strategy §3.4`) is a **Silver/CDC** concern and is deliberately *not* done here (§5.3).
- **`document_number` / `client_document_number` are read but not trusted for logic.** They are captured for human-readable identity and lineage; any downstream keying still happens on the parsed model in Silver, exactly as the specs require (`data_specification.md` §4.2 "Internal keys").
- **`drawing_revision` is the one field added purely to serve Gold.** See §6.
### 3.2 The reserved-field alignment
 
`data_specification.md` §4.2 ("Lineage & operations") already reserves: *ingestion timestamp, content hash, file size, source last-modified time.* The strategy note confirms Bronze is where these land (`strategy §3.1`): *"land each `*.xml` … with the ingestion metadata `data_specification.md` already reserves … plus `OriginatingSystem` so the format adapter can be chosen downstream."* This spec adds only two things beyond that reserved set, and both are justified by a named downstream need:
 
1. `originating_system` + `source_format` + `format_detection_method` — so Silver's `_adapter_for` decision is *recorded at ingest*, not re-derived (§4).
2. `drawing_revision` — so Gold's **valid time** has a source (`strategy §4b`), which the reserved set does not currently carry.
Everything else in the schema is operational plumbing (surrogate key, run id, source path) that no spec forbids and every audit trail needs.
 
### 3.3 Payload storage: `binary` vs `text`
 
Store `content` as **binary** (`BinaryType`), because it is the only representation guaranteed byte-faithful across encodings and BOM handling, and the hash must be computed over exactly the bytes that were ingested. Optionally also materialise `content_text` (`StringType`, UTF-8) for ergonomics. Recommendation for the PoC: **populate both**, since drawing XML is modest in size (tens to low-hundreds of KB) and the convenience of SQL-readable XML during stakeholder demos outweighs the storage cost. If files grow or storage is constrained, drop `content_text` first — it is fully reconstructable from `content`.
 
---
 
## 4. Format / adapter detection (record, don't act)
 
Silver chooses its parser with `reconstructed._adapter_for`, which today decides by content: **PostProc is detected by a `PipingNetworkSegment` carrying a `TagName`; DEXPI otherwise** (`strategy §3.1`). Bronze's job is to **record the same signal at ingest** so Silver reads it from a column instead of re-sniffing the DOM, and so the audit trail shows how each file was classified.
 
Detection ladder (first match wins), recorded in `format_detection_method`:
 
1. **`ORIGINATING_SYSTEM`** — read the file's `OriginatingSystem` header. `SPPID` ⟶ `source_format = POSTPROC`. A DEXPI originator (or any non-SPPID DEXPI/Proteus marker) ⟶ `DEXPI`. This is the cheap, header-only path and the preferred one.
2. **`SEGMENT_TAGNAME`** — if the originator header is absent or ambiguous, fall back to the structural signal `_adapter_for` uses today: presence of a `PipingNetworkSegment` with a `TagName` ⟶ `POSTPROC`; else `DEXPI`. This requires a *shallow* scan, not a full parse — Bronze looks only far enough to find or rule out the marker.
3. **`UNKNOWN`** — neither signal resolves. Land the file anyway, set `source_format = null`, `format_detection_method = UNKNOWN`, `header_parse_ok = false`. Silver decides what to do with an unclassifiable file; Bronze never drops it.
Design rules:
 
- **Bronze detects; Silver adapts.** The recorded `source_format` is advisory metadata. Silver remains free to re-confirm with `_adapter_for` — the single source of truth for *how to parse* stays in Silver. Bronze's value is that the classification is *captured and auditable at ingest time*, and that a run can be filtered/partitioned by format without opening files.
- **One table, two formats.** Detection populates a column; it never routes to a separate table or path (§2).
- **Keep the scan shallow.** Detection must not evolve into parsing. If reading the `OriginatingSystem` and (fallback) a single segment tag requires more than a streaming/partial read, that is a smell that detection is over-reaching — pull it back.
---
 
## 5. Version identity, immutability & idempotency
 
### 5.1 Append-only, immutable
 
Bronze is **append-only**. A Bronze row, once written, is never updated or deleted. A new export of a drawing is a **new row**, not an edit of the old one. This is the immutability guarantee the audit trail depends on, and it maps directly onto Delta's append semantics plus time travel.
 
Operationally, on the Bronze Delta table:
 
- Writes are `append` only. No `MERGE` that updates payload columns, no `UPDATE`, no `DELETE` in normal operation.
- Enable Delta features that reinforce this: table property `delta.appendOnly = true`. This makes the immutability contract a property of the table, not merely a convention in the writer.
- Retain history generously — Bronze *is* the history, so `VACUUM` retention should be long (governance decision, §9); the point of Bronze is that old versions never disappear.
### 5.2 The version key
 
The **`content_hash` (sha-256 over raw bytes) is the version discriminator.** Two ingests of byte-identical content are the same version; any byte difference is a new version. Human-readable identity (`document_number`, `drawing_revision`) travels alongside but is *not* the key — two exports at the same nominal revision can differ byte-wise (re-export churn), and a revision string can be missing or wrong. The hash is the objective truth.
 
Recommended logical uniqueness for a *distinct landed version*:
 
```
(content_hash)                      — global dedup: identical bytes land once
```
 
with `(document_number, drawing_revision, content_hash)` available as the human-facing version tuple for reporting.
 
### 5.3 Idempotency & replay
 
**Re-ingesting the same file must not create a duplicate Bronze row.** The ingestion job is idempotent on `content_hash`:
 
- Before appending, the job checks whether `content_hash` already exists in Bronze. If it does, the file is **skipped** (already landed) — optionally recording a lightweight "seen again at T" observation in a separate sightings table if provenance of *re-presentation* is wanted, but never a second payload row.
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
- **Consider also capturing the revision *issue date*** if the source header carries it. The strategy's valid-time semantics ("valid-from Rev C's *issue date*") want a date, not just a revision label. If the export exposes an issue/approval date in the title block, land it as an optional `drawing_revision_date` column; if it does not, Gold derives valid-time ordering from revision sequence and falls back to `source_last_modified` / `ingested_at`, and this gap is flagged as an open item (§9). Bronze's contract is: *capture whatever revision-dating the source offers, verbatim; never fabricate it.*
Bronze does **not** compute `validFrom`/`validTo`, does not order revisions, and does not close intervals — all of that is Gold. Bronze only guarantees the raw inputs are present and faithful.
 
---
 
## 7. Data quality at Bronze (minimal, non-gating)
 
Bronze is not where Great Expectations lives (`strategy §3.3`), and it must not adopt GX's default abort-on-fail posture. Bronze's quality stance mirrors the specs' governing principle — *"quality issues raise flags, they do not abort"* (`algorithm_spec §11`, echoed in `strategy §3.3`) — but at the coarsest possible level:
 
Bronze performs only **landing-integrity** checks, and every one of them is a *flag*, never a rejection:
 
| Check | On failure |
|---|---|
| File is non-empty (`file_size_bytes > 0`) | Land it; flag `header_parse_ok = false`, empty payload recorded |
| File is readable as bytes | If truly unreadable, the ingest run logs it and skips — the only case Bronze does not land, because there is nothing to land |
| Header fields readable (`document_number`, `drawing_revision`, `originating_system`) | Land it; set the unreadable field(s) null and `header_parse_ok = false` |
| Format classifiable (§4) | Land it; `source_format = null`, `format_detection_method = UNKNOWN` |
 
Explicitly **not** at Bronze: XML well-formedness enforcement, schema validation, `GenericAttribute` presence, connectivity checks, ghost-equipment filtering, tag round-trip (`compose(decode(tag))==tag`) — all Silver (`strategy §3.3`, `data_specification.md` §4.2). Bronze will happily store a malformed or partial XML file; discovering it is unusable is Silver's job, and the immutable Bronze copy is what lets that diagnosis be reproduced.
 
The reason this matters: the PoC's honest-partial-result behaviour (`algorithm_spec §11`) is a *feature*. If Bronze started rejecting files, a whole drawing could vanish before Silver ever gets the chance to produce a flagged partial result. Bronze rejecting a file would be a regression, not a safeguard.
 
---
 
## 8. Storage layout, partitioning & PySpark ingestion
 
### 8.1 Table & partitioning
 
One Delta table, `bronze_pid_documents` (name illustrative). Partitioning is chosen for how Bronze is *written and replayed*, not how Silver queries it:
 
- **Partition by `ingest_date`** (derived `date(ingested_at)`), and optionally sub-partition by `source_format` or `project_code`. Ingestion is naturally batch-by-day, replay and audit are naturally "what did we land, when", and format/project are low-cardinality filters that let Silver read one format's files without scanning the other.
- **Do not partition by `content_hash` or `document_number`** — high-cardinality, would produce the small-files problem Spark punishes.
- Because each row's payload is small, watch for the **many-small-files** pattern: prefer a modest number of larger Delta files per partition (compaction / `OPTIMIZE`) over one file per drawing.
### 8.2 Parallelism is over *files*, not rows
 
The strategy is emphatic that Spark's role here is scale-out over drawings, not a reformulation of any algorithm (`strategy §2`, §3.2). At Bronze this is straightforward because ingestion is embarrassingly parallel per file:
 
- Read the input folder with Spark's binary-file source (`spark.read.format("binaryFile")`), which yields one row per file with `path`, `modificationTime`, `length`, and `content` (binary) — a near-perfect match for the Bronze lineage columns.
- Compute `content_hash`, decode the shallow header fields (§4), and detect format in a `mapInPandas` / UDF pass — **per file, no cross-file state**.
- Deduplicate against existing hashes (§5.3) and append.
Sketch (illustrative, not final code):
 
```python
raw = (spark.read.format("binaryFile")
            .option("pathGlobFilter", "*.xml")
            .option("recursiveFileLookup", "true")
            .load(SOURCE_DIR))
# raw columns: path, modificationTime, length, content
 
bronze = (raw
    .withColumn("content_hash",       sha2(col("content"), 256))
    .withColumn("file_size_bytes",    col("length"))
    .withColumn("source_path",        col("path"))
    .withColumn("source_last_modified", col("modificationTime"))
    .withColumn("ingested_at",        current_timestamp())
    .withColumn("ingest_run_id",      lit(RUN_ID))
    # header read + format detection: shallow, per-file
    .transform(detect_and_read_headers)   # adds originating_system, source_format,
                                          # document_number, client_document_number,
                                          # drawing_revision, format_detection_method,
                                          # header_parse_ok
    .withColumn("bronze_id",          expr("uuid()"))
    .withColumn("ingest_date",        to_date("ingested_at")))
 
# idempotent append: insert only unseen content_hash
(DeltaTable.forName(spark, "bronze_pid_documents")
    .alias("t")
    .merge(bronze.alias("s"), "t.content_hash = s.content_hash")
    .whenNotMatchedInsertAll()
    .execute())
```
 
- `detect_and_read_headers` must stay shallow (streaming/partial XML read for `OriginatingSystem`, then a single segment-tag probe as fallback) — it is the one place the temptation to "just parse a bit more" appears, and §1.2/§4 forbid it.
- The `MERGE ... whenNotMatchedInsertAll` gives idempotency while honouring `delta.appendOnly = true` (inserts only).
### 8.3 Streaming variant (optional, later)
 
If exports arrive continuously rather than in nightly batches, the same logic runs as a Delta/Spark structured-streaming job with the file source and `foreachBatch` doing the idempotent merge. For the PoC, **batch is sufficient** and simpler; note the streaming path only as a forward option so the schema doesn't need to change to adopt it.
 
---
 
## 9. Open decisions for the team
 
These need a call before or during implementation; none blocks starting:
 
1. **Revision issue date.** Does the DEXPI/PostProc title block reliably expose a revision *date*? If yes, add `drawing_revision_date` (§6) and Gold's valid-time gets a true date axis; if no, agree that Gold orders valid-time by revision sequence with `source_last_modified` as the tie-break, and log the imprecision.
2. **`content_text` on/off.** Populate the decoded-text convenience column, or binary-only? (Recommendation: on for the PoC, §3.3.)
3. **Hash algorithm & salt.** sha-256 proposed; confirm it meets any organisational data-integrity standard, and confirm no normalisation (whitespace, BOM, line-endings) is applied before hashing — Bronze hashes exactly what it stores.
4. **Re-presentation tracking.** When a byte-identical file is seen again, do we want a lightweight sightings record (who re-presented it, when) or a silent skip? (§5.3)
5. **Retention / VACUUM.** How long must Bronze keep every version — indefinitely, or a governed window? Bronze is the audit trail, so the default should be long; a legal/storage owner should set it (§5.1).
6. **Project/tenant tagging.** Is `project_code` supplied by the ingestion run, derived from the source path, or inferred from `source_format`? Decide the authority so multi-project runs don't collide.
7. **Table & path naming, catalog registration.** Unity Catalog / Hive metastore naming, storage location, and access control for the Bronze table.
---
 
## 10. Risks & mitigations
 
| # | Risk | Mitigation |
|---|---|---|
| 1 | **Scope creep into parsing.** Header-read / format-detection quietly grows into a full parser, duplicating Silver's `ga()`/`_adapter_for` and re-introducing the two-schema problem the consolidation just removed (`architecture_note.md` §2). | Header read is a fixed, shallow whitelist (§4); anything deeper is a Silver PR, not a Bronze one. |
| 2 | **Whole-file hash misused for CDC.** Someone wires Silver's change-detection to `content_hash`, flooding CDC with re-export churn (`strategy §3.4`). | Document loudly (§5.3) that `content_hash` is *version identity*, not *engineering-change* detection; CDC hashes at object grain in Silver. |
| 3 | **Missing revision ⟶ broken bi-temporal Gold.** `drawing_revision` unread or null collapses valid time (`strategy §4b`). | Capture at ingest, flag when unread (§7); §9.1 resolves the date question; Gold has a documented fallback. |
| 4 | **Bronze starts rejecting files**, killing the honest-partial-result behaviour (`algorithm_spec §11`). | Bronze flags, never rejects (§7); the only non-land case is a physically unreadable file. |
| 5 | **Small-files explosion** from one Delta file per drawing. | Partition by ingest date, compact/`OPTIMIZE`, avoid high-cardinality partitioning (§8.1). |
| 6 | **Two-table drift** between DEXPI and PostProc. | One table, format is a column (§2, §4) — preserves the format-independence the interoperability contract rests on. |
| 7 | **Silent overwrite** of a prior version. | `delta.appendOnly = true` + insert-only MERGE make immutability a table property, not a convention (§5.1). |
 
---
 
## 11. One-paragraph summary
 
Bronze converts the PoC from a batch script into a data product with an audit trail (`architecture_note.md` §4) by landing every DEXPI and PostProc file **as-is**, once per distinct byte-version, in one append-only, immutable Delta table — discriminated by a recorded format column, keyed on a whole-file sha-256, and carrying exactly the lineage metadata `data_specification.md` §4.2 already reserves plus two justified additions: the **format-detection signal** (so Silver's `_adapter_for` decision is auditable, not re-sniffed) and the **drawing revision** (so Gold's valid-time axis has a source). It does one interpretive act only — a shallow header read for identity, versioning, and routing — and refuses every other temptation: no parsing, no reconstruction, no quality gates, no CDC, no interval modelling, all of which belong to Silver and Gold. Get this boundary right and every layer above can be replayed, audited, and time-travelled against the exact bytes that produced it; blur it and Bronze stops being raw and the provenance story the whole program turns on is compromised at its root.