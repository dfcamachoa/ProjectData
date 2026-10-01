# Gold Layer — Design Specification & Implementation

**Data Product:** Automatic Pre-commissioning Systemization based on P&ID interoperability data
**Layer:** Gold (bi-temporal versioning · RDF/IDO projection · declarative classification · SPARQL serving) — the third medallion tier
**Target runtime:** Delta Lake on Apache Spark (bi-temporal tables) + Apache Jena / Fuseki (triplestore, SPARQL, rule engine) — PoC on local WSL, same as Bronze/Silver
**Status:** All three Gold sub-stages now run on real data. Bi-temporal versioning runs on real Spark. The RDF/IDO projection runs on real `rdflib`/`owlrl` and is pushed to a live Fuseki. **As of 2026-09-23, the Jena rules engine runs live on Fuseki (`gold-rules` service) and agrees exactly with the Python reference rules on real Project A data (§5.6). Its output is materialised, with PROV, into the `graph:inferred` named graph, ready for a systemization run to consume (§5.7).** Workstream 2's Off-Page Connector projection is confirmed on the same real load: 68 OPC nodes, 3 cross-sheet pairs, and the `terminates` guard holding (§4.6). See §8 for current implementation status and §9 for what remains open.
**Date:** 2026-09-23
**Companions:** `bronze_layer_spec.md`, `silver_layer_spec.md`, `medallion_rdf_ido_strategy_mapping.md` (§4, §5, §6, §10 steps 2–4), `data_specification.md` (§2.1a, §2.1b, §3.2, §3.4, note on §1 scope: *"Semantic-modeling concerns... are handled separately at the Gold layer"*), `algorithm_spec.md` (§7, §9), `systemization_spec.md` (§4–§5), `architecture_note.md` (§5), `claude/off_page_connector_representation.md` and `claude/workstream_2_precomm_ontology_scoping.md` (the OPC/Connection design this spec's §4.6 formalizes), `silver_opc_feed_change_spec.md` and `opc_edit_manifest.md` (the Silver-side feed and its checkout-verification checklist), `claude/jena_rules_deployment.md` (the live Jena rules deployment and its parity runs, §5.6), `opc_verification_sparql.md` (the SPARQL acceptance queries run against the real Gold graph).
**Grounding:** every rule ported below is to the actual `pidsys/walk.py` behaviour as already catalogued in `medallion_rdf_ido_strategy_mapping.md` §6 and `algorithm_spec.md` §7/§9 — this spec does not re-derive the rule content, it re-houses it, exactly as Silver re-housed reconstruction rather than re-deriving it.

**This is the current-state version of this spec** — design plus present implementation status, without the build's chronological narrative. The full dated engineering history (every risk found, every real-data finding, every patch delivered, in order) is preserved in `claude/gold_layer_spec_history.md` for audit and reference; the live Jena deployment's own run-by-run history is in `claude/jena_rules_deployment.md`.

---

## 0. Verdict up front

Gold is where the medallion strategy's two genuinely new, genuinely hard pieces meet: **bi-temporal history** and **the semantic serving/inference layer** (`medallion_rdf_ido_strategy_mapping.md` §7, "net-new" column). Neither has a `pidsys` predecessor to re-house — unlike Bronze (an ingestion wrapper) and Silver (a re-housing of validated reconstruction and rule content), Gold is where new design work is unavoidable. That makes the discipline different too: where Bronze and Silver's governing question is *"what already exists that we must not re-derive,"* Gold's is *"what does the strategy under-specify, and how do we decide it honestly rather than assume a term or a shape that doesn't exist."* `medallion_rdf_ido_strategy_mapping.md` §4 and §5 name exactly these gaps — a single `validFrom`/`validTo` pair collapsing two real time axes into one, and `ido:` terms (`FunctionalObject`, `validFrom`) that IDO does not actually ship — and this spec's job is to close them with documented decisions rather than inherited assumptions.

The one discipline that carries over unchanged from Silver: **Gold computes provenance and time, and projects meaning; it does not compute commissioning systems.** The global connected-fragment partition — the walk that actually decides which components form which system — stays exactly where it is validated: in Silver/Python (`pidsys/walk.py`), never reimplemented as Jena forward-chaining (medallion §6, point 5: SPARQL/Jena are awkward at "transitive closure that must stop at a node with a property," which is precisely what a boundary walk is). Gold's inference layer is deliberately narrower than "systemization in Jena" — it is the **local, node-local** half of the hybrid the strategy converges on — fluid self-ownership classification, boundary-role lookup, and the three directional guards, all read from `graph:refdata` and `graph:masterdata`, never from `graph:oracle`. Getting that boundary right is what keeps the Jena demonstration honest instead of pretending a forward-chainer is a graph-algorithms engine it is not.

---

## 1. Purpose & scope

### 1.1 What Gold is

Gold turns Silver's canonical, use-case-neutral plant model into **two things every rule package and every stakeholder-facing query reads**: a **bi-temporal history** of the object grain (component/segment/equipment/connection), queryable on either the engineering-reality axis or the audit axis; and a **semantic projection** of that same model into RDF/IDO across governed named graphs, serving SPARQL and hosting the declarative classification rules the medallion strategy assigns to Jena.

Concretely, Gold runs three sub-stages, each consuming Silver's tables (and Bronze's lineage) without re-parsing or re-reconstructing anything:

1. **Bi-temporal versioning** (§3) — object-grain New/Modified/Deleted deltas, sourced from Silver's Stage E CDC output, become append-only Gold rows with two independent time axes, never physically deleted, "never delete, close the interval."
2. **RDF/IDO projection** (§4) — the current (and, on request, historical) canonical objects are mapped to triples across `graph:masterdata`, `graph:refdata`, `graph:oracle`, and `graph:results`, with reified Connections carrying `derived` and an oriented `flowsTo`, domain classes honestly subclassed under IDO's foundational layer rather than asserting unconfirmed `ido:` domain terms, and — as of Workstream 2 (§4.6) — Off-Page Connectors projected as their own `InformationObject`-typed nodes rather than lost into the generic component fallback.
3. **Declarative classification** (§5) — the node-local, directional rules a Jena engine evaluates over `graph:refdata` + `graph:masterdata` — live on Fuseki since 2026-09-23, with `rules_reference.py` as the Python reference it is held in parity with: fluid self-ownership, boundary-role membership, the flare guard, the directional consumer guard, and relief-device attribution. The global partition itself is out of scope here — it stays in Silver.

Gold emits: a bi-temporal Delta table per object kind (mirroring Silver's table family, with the two time-axis columns added); a quad set across the four named graphs, servable from Fuseki or read locally; and the classification predicates (`selfOwningClass`, `skipAsConsumerSignal` — with its per-guard reasons `skipFlareSink` / `skipSupplyTieIn` — and `protectedBy`) a downstream systemization run consumes as *additional*, not replacement, signal — the walk's own global partition remains the system of record until a validated Jena-only path exists (§9, phasing).

### 1.2 What Gold is NOT

| Concern                                                                   | Belongs to                                                                                           | Why not Gold                                                                                                                                                                                 |
| ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Parsing, reconstruction, cross-document assembly                          | **Silver**                                                                                     | Gold consumes Silver's tables; it never touches a DOM or a centerline                                                                                                                        |
| Data-quality gating (the`silver_quality` ledger, `quality_gate` enum) | **Silver**                                                                                     | Gold trusts Silver's`quality_gate`; it does not re-run expectations                                                                                                                        |
| Object-grain CDC identity (anchor matching, the three hashes)             | **Silver Stage E**                                                                             | Gold*consumes* a New/Modified/Deleted classification; it does not decide what counts as the same engineering item across delete+recreate (`silver_layer_spec.md` §3.5)                  |
| The global connected-fragment partition (the boundary walk itself)        | **Silver / `walk.py`**                                                                       | Forward-chaining is the wrong tool for "reachable without crossing a boundary" (medallion §6 point 5); Gold's rule layer is local-only                                                      |
| Allocation tie-breaks and fragment-merge as a*running system*           | **the systemization rule package**, consuming Gold's classification predicates as extra signal | Gold's`rules_reference.py` / Jena rules *express* the allocation functions (§5.4) as callable, data-driven logic; a systemization run is what actually invokes them over real fragments |
| Test Packages' cut rule, ITR/MC scoping, and every other rule package     | **that rule package**, reading the same Gold/RDF surface with a different cut rule             | Gold is the second consumer's entry point (`architecture_note.md` §6), not itself a second rule package                                                                                   |

### 1.3 Position in the medallion architecture

```
   Bronze                    Silver                              ┌──────── Gold (this spec) ────────┐
  raw XML,       ──▶  master data + reconstructed graph  ──▶      │ 1 bi-temporal versioning          │
  append-only         + quality verdicts + object-grain CDC       │   (object grain, two time axes)   │
                                                                   │ 2 RDF/IDO projection               │
                                                                   │   (4 named graphs, reified edges) │
                                                                   │ 3 declarative classification       │
                                                                   │   (local/directional rules only)  │
                                                                   └──────────────┬─────────────────────┘
                                                            bi-temporal Delta tables + RDF quad set
                                                                                  │
                                                    systemization pkg (global partition, Silver/Python)
                                                    · Test Packages pkg · ITR/MC · … — all read this Gold surface
```

---

## 2. Design decisions this spec resolves

`medallion_rdf_ido_strategy_mapping.md` §4 and §9 name four things the original strategy under-specified. Each is resolved here:

1. **Two time axes, named and kept independent** (§3.1) — not a single `validFrom`/`validTo` pair.
2. **`pidsys:validFrom` / `pidsys:validTo`, not `ido:validFrom` / `ido:validTo`** (§4.3, `vocab.py`) — IDO ships no such datatype properties; asserting them would be an over-claim, the same way `ido:FunctionalObject` would be.
3. **Domain classes subclass an IDO *foundational* class and carry an explicit `rdlUriPending` marker** (§4.3) rather than asserting a domain term IDO does not define, resolved via two format-scoped bridges to the POSC Caesar PLM reference-data library (§4.3.1) — a `RDS…→PLM` URI bridge for DEXPI and a `ComponentClass→PLM-label` bridge for PostProc, with confidence carried per class and a review gate on boundary-forming classes.
4. **Grain and interval discipline are explicit and tested** (§3.2–§3.3) — object grain aligned with Silver's CDC grain, "never delete, close the interval," and a named, honest limitation (no valid-time interval *splitting* for retroactive corrections — flagged for manual reconciliation rather than silently guessed at).

---

## 3. Bi-temporal versioning

### 3.1 The two axes

| Axis                       | Meaning                                                                            | Source                                                          | Predicate (never`ido:`)                             |
| -------------------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------- |
| **Valid time**       | When the plant configuration a record describes was*true* — engineering reality | Bronze`drawing_revision_date` (format-normalised)             | `pidsys:validFrom` / `pidsys:validTo`             |
| **Transaction time** | When the platform*learned* the fact — audit / system time                       | Bronze`ingested_at`, carried through Silver's lineage columns | `pidsys:transactionFrom` / `pidsys:transactionTo` |

These differ constantly in EPC reality: a revision issued three weeks ago but ingested today has valid-time three weeks back and transaction-time now. Gold carries **both**, independently, on every object-grain row (`gold/temporal.py`, `GoldRow`).

### 3.2 Grain and identity

Object grain, aligned with Silver's Stage E CDC grain (`silver_layer_spec.md` §3.5): component / **line** / equipment / connection, keyed on a stable **anchor id** — never the volatile source UID. `gold/silver_cdc.py::apply_silver_cdc_events` consumes `silver_cdc` rows directly, using Stage E's own anchor-match identity (`anchor_id`, derived from `anchor_hash`) as the Gold row's anchor — the same identity whose acceptance test proves a delete+recreate of an unchanged item produces zero deltas, carried through to Gold unmodified. `gold/temporal.py::diff_snapshots` remains as a documented fallback for a Silver build predating Stage E or a snapshot-only backfill.

**Anchor buckets, not per-instance ids, for components.** `silver_cdc`'s `anchor` is a *bucket* key for components — Stage E's own within-bucket member pairing can legitimately place two same-class siblings on one line under the identical anchor string, so a single batch can carry two simultaneous New/Modified/Deleted events for one `anchor_id`. `apply_delta`'s one-current-row-per-anchor contract (§3.3) is unchanged — what a caller needs is `apply_silver_cdc_events_tolerant`, which catches each such collision as a `CdcAnomaly` and keeps applying the rest of the batch, rather than the strict `apply_silver_cdc_events`, which aborts the whole batch on the first one. `gold_job.build_bitemporal_tables_from_cdc` uses the tolerant path by default and returns `(tables, anomalies)`.

**Line grain.** Silver's piping CDC versions at **line** grain — `(drawing_number, seg_tag)` — not per physical `PipingNetworkSegment` (real Project-B data: ~78% of `seg_tag`s are shared by 2+ pieces whose split points and UIDs churn between revisions; Silver's `cdc.aggregate_lines` collapses them before diffing). At this grain, `old_uid`/`new_uid` is `f"{drawing_number}|{seg_tag}"`, not a `silver_segments` row id — one line's key legitimately maps to multiple pieces, so there is no single row to resolve a line's attrs from. `gold/silver_cdc.py` calls Silver's own `cdc.aggregate_lines` directly when importable; a same-shape fallback (`aggregate_line_attrs`) groups the matching `silver_segments` rows by `(drawing_number, seg_tag)` and reduces each engineering attribute (fluid, unit, diameter, piping-materials-class, insulation triple) to a sorted distinct-value tuple, mirroring Silver's own `content_hash_eng` set-projection — a within-line disagreement across pieces surfaces as a multi-value tuple (`line_attr_inconsistent = True`) rather than being silently resolved to one value. Component/equipment/connection grain are unaffected by this — Stage E's anchor-match identity still re-mints one internal id per version for those.

**Off-Page Connectors version at their own grain (§4.6), added by Workstream 2.** Silver's Stage E now diffs a fifth grain, `off_page_connector`, with its own anchor shape and its own engineering-field set — see §4.6 and §8.1. It follows the same "never delete, close the interval" discipline as every other grain below; nothing in §3.2–§3.4 changes to accommodate it.

### 3.3 Interval discipline — "never delete, close the interval"

Implemented in `gold/temporal.py::apply_delta`, three cases:

- **New** — open a row: `valid_from = X, valid_to = None, tx_from = now, tx_to = None`.
- **Modified**, two sub-cases distinguished by comparing the new fact's `valid_from` to the current row's:
  - **Ordinary forward supersession** (`new.valid_from > current.valid_from`, the overwhelmingly common case): close **only** `valid_to = new.valid_from` on the old row. Its transaction interval is **not** retroactively closed — the platform is not correcting anything, it is recording that a later reality began.
  - **Correction** (`new.valid_from == current.valid_from`, same engineering-validity period but different content — a re-ingest that corrects what was recorded): close **only** `tx_to = now` on the old row, leaving `valid_to` untouched; open a new row starting at the *same* `valid_from`.
  - **Retroactive** (`new.valid_from < current.valid_from`) — would require splitting an *earlier* interval the row may not even represent. **Not implemented**: raises `RetroactiveCorrection` so it is routed to a person rather than silently asserting an interval shape nobody has validated.
- **Deleted** — closes **both** axes (`valid_to = tx_to = event time`): the object no longer exists, so it is no longer held as current on either axis. No new row opens.

**Two structural safeguards, both unit-tested:**

- **Identical content is a no-op** (`GoldRow.content_key`), so a byte-identical re-ingest never bumps a version.
- **An oracle-only change never triggers a version bump** — `content_key` excludes `src_turnover`/`src_subsystem` from the change-detection fingerprint by construction, restating Silver's compute-only firewall (`silver_layer_spec.md` §5) at the Gold layer.

### 3.4 Query surface: `current_truth`

`gold/temporal.py::current_truth(rows, as_of_valid=None, as_of_tx=None)`: both args `None` gives *"transaction_time = now ∧ valid_time = latest"*; either given alone answers *"what was true at Rev B"* or *"what did we believe as of transaction time T"* respectively, with the **unspecified** axis left unconstrained rather than silently defaulted to "latest" — defaulting it would wrongly exclude a historical-but-since-closed valid-time row from a pure transaction-time query merely because a later revision has since closed its valid interval.

### 3.5 A named, honest limitation

Full SQL:2011-style bi-temporal tables support **splitting** an already-recorded valid-time interval when a retroactive correction arrives mid-interval. This is not implemented (§3.3, the `RetroactiveCorrection` path) — a materially harder feature (multiple new rows replacing one, with careful boundary arithmetic) deliberately deferred to a reviewed addition rather than a first-cut guess.

---

## 4. RDF/IDO projection

### 4.1 Named-graph layout

| Graph                | Contents                                                                                                                                                                                                       | Who may write it                                                                                                 | Who may read it                                                                                             |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `graph:masterdata` | Reconstructed plant: components, segments, equipment, nozzles, reified connections, flow direction, Off-Page Connector nodes (§4.6)                                                                           | The Gold projection job only                                                                                     | Everything — the shared plant model                                                                        |
| `graph:refdata`    | Fluid catalogue, boundary role sets, tagging convention, UnitSUP, RDS→PLM crosswalk — the "rules as data" surface                                                                                            | The reference-data load job only                                                                                 | Everything, especially the classification rules (§5)                                                       |
| `graph:oracle`     | `src_turnover` / `src_subsystem` — the validation answer key                                                                                                                                              | The Gold projection job only                                                                                     | **Only** the oracle cross-check query (§6); never a rule, never a partition, never `graph:results` |
| `graph:inferred`   | The classification predicates the Jena rules derive (`selfOwningClass`, `skipAsConsumerSignal`, `skipFlareSink`, `skipSupplyTieIn`, `protectedBy`), plus PROV for the run that produced them (§5.7) | `gold/inferred_graph.py::materialise_inferences` only, and only after a passing parity check on the same input | The systemization run, read beside`graph:masterdata`; reporting                                           |
| `graph:results`    | Computed systems, their members, boundaries, and the rule/run that produced them (PROV)                                                                                                                        | The systemization rule package, after it runs                                                                    | Reporting, stakeholder queries, the oracle cross-check                                                      |

### 4.2 Reified Connections

Every Silver `silver_connections` row becomes a `pidsys:Connection` node (`gold/rdf_mapper.py::map_connection`) carrying `fromObject`/`toObject`/`fromNode`/`toNode`/`connType` and a **mandatory** `derived` boolean — mapping a connection with no `derived` key is a hard `ValueError`, restating Silver's `derived_flagged` structural invariant at the projection boundary. Adjacency is emitted as IDO's own native, symmetric **`ido:connectedTo`**, both directions, unconditionally — no `pidsys:` connectivity property is minted (`specs/semantics/pidsys_extension.ttl`); `vocab.P_IS_CONNECTED_TO` resolves to that IDO term, and every consumer (Python rules, Jena rules, SPARQL) must reach it through `vocab` rather than a hard-coded URI (§5.6, risk #16). `flowsTo` is emitted **only** when `flow_sense` orients it — `forward`/`reverse`/`both` each produce the appropriate directed triple(s), `none` produces none at all, never a guessed direction. `pidsys:flowsTo` stays a standalone `pidsys:` property, deliberately not a subproperty of the symmetric `ido:connectedTo`, which would inherit symmetry and lose direction.

The Off-Page continuation edge (§4.6) is an ordinary `conn_type` value through this same, unmodified mapper — it reifies exactly like a Process or Signal connection, always `derived=True`.

### 4.3 Honest IDO alignment

`vocab.py` draws the line the strategy names but does not enforce. **Two** real, foundational IDO/LIS-14 classes are asserted — no bare, invented domain term stands in for either:

- `IDO_PHYSICAL_OBJECT` — every domain class (`GateValve`, `PipingSegment`, `Equipment`, ...) is declared `rdfs:subClassOf pidsys:PipingComponent`, itself `rdfs:subClassOf ido:PhysicalObject`.
- `IDO_INFORMATION_OBJECT` — added by Workstream 2 (§4.6) for `pidsys:OffPageConnector`, which is a drawing-continuation artifact, not a physical plant item, and is honestly modelled as such rather than forced under `PhysicalObject` to keep the "one IDO term" claim simple. LIS-14 already defines `lis:InformationObject` for Documents (`ido_semantic_mapping_spec.md` §3.1); this reuses that same confirmed term rather than minting a new one.

One real IDO **property** is used as well: `ido:connectedTo` for adjacency (§4.2), IDO's native connectivity term — used as-is rather than shadowed by a project property.

Where a real RDL (POSC Caesar / ISO 15926-4) URI has not yet been resolved for a physical domain class, `rdf_mapper.py` asserts `pidsys:rdlUriPending true` on it rather than fabricating one — this marker does not apply to `OffPageConnector`, whose `component_class_uri` is a direct pass-through of a real DEXPI sandbox RDL URI, never a PLM resolution target (§4.6). Validity timestamps are `pidsys:validFrom`/`pidsys:validTo`/`pidsys:transactionFrom`/`pidsys:transactionTo` — project predicates, never `ido:validFrom`.

**A component may carry no resolved class at all** — real Silver data does. `map_component` treats `component_class` as optional: when missing, `None`, or one of a confirmed set of source-tool catch-all placeholder strings (`CustomComponent`, `CustomPipingComponent`, `CustomPipeFitting`, `GenericComponent` — `vocab.CATCHALL_COMPONENT_CLASSES`, an exact-match set, confirmed against the real PCA PLM equipment library to have no counterpart there and never to gain one), the component is asserted as `vocab.C_UNCLASSIFIED_COMPONENT` instead — an honest "no real classification available" node, never a fabricated guess.

### 4.3.1 Resolving `rdlUriPending` — the two format-scoped bridges

§4.3 asserts `pidsys:rdlUriPending true` on any domain class not yet carrying a real RDL URI. Resolution is **reference-data-driven** (a crosswalk loaded into `graph:refdata`), never hardcoded in `rdf_mapper.py`, and format-specific, because the two source formats carry class identity differently:

| Source format                                       | Class signal in the file                                                                                         | Bridge to PLM/RDL                                                                                                             | Confidence                                                                 |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| **DEXPI (Project A / `pydsys`)**            | `ComponentClass` string **and** `ComponentClassURI` in the `data.posccaesar.org/rdl/RDS…` namespace | **URI bridge**: `RDS… → plm:PCA_…` via the PLM library's own published `skos:closeMatch`/`exactMatch` mappings | high where`exactMatch`; **review-gated** where `closeMatch`      |
| **PostProc/SPPID (Project B / `bppidsys`)** | `ComponentClass` string **only** — the export carries **no RDL URIs at all**                      | **label bridge**: `ComponentClass string → plm rdfs:label` via a curated alias/synonym table                         | always review-gated (label match can fail through a plausible wrong match) |

**Why two bridges, not one.** The Gold projection is shared, but the on-ramp to it is not — the same `graph:masterdata` node is reached by a URI lookup for DEXPI and a label lookup for PostProc, lining up with the program's "DEXPI and PostProc codebases never merge" constraint. Neither bridge changes `map_component`'s shape; each is a `graph:refdata` lookup feeding the same `rdlUri` assertion that clears `rdlUriPending`.

**`closeMatch` is not `exactMatch`.** The PLM library's mappings are overwhelmingly `skos:closeMatch` rather than `exactMatch`, and a `closeMatch` means "near," not "identical" — resolving a class through one is a semantic-alignment *claim*. The projection asserts, alongside the resolved URI, a confidence marker — `pidsys:rdlMatchType` ∈ {`exact`, `close`, `label`} — and a `close`/`label` resolution on a **boundary-forming** class (§5.3) must pass a human review gate (`pidsys:pendingReview`) before it feeds a trusted systemization run. `exact` resolutions and non-boundary `close`/`label` resolutions flow without a gate but remain queryable by match type.

**Coverage is partial by construction.** On a real drawing, roughly a third to half of physical items auto-resolve; the remainder stay `rdlUriPending true`. The unresolved remainder decomposes into three causes: (A) the source emitted a generic/`Custom`/`None` class — no real type to resolve, handled by §4.3's catch-all normalization; (B) a real class with no URI in the export — resolved by the label bridge; (C) a real `RDS…` URI absent from the PLM library's own published mappings — resolved only by a hand-curated crosswalk addition, boundary-forming classes reviewed first.

**Cause C, current real result (Project A / DEXPI).** With Silver correctly populating `component_class_uri` end to end (see §8.3), a real run of the gap-finding tool against real Project A data covers **187 real RDS codes** against the pre-published crosswalk, leaving **15 distinct RDS URIs** (3 of them boundary-forming) as a genuine, human-reviewable gap — written to a candidates file with a suggested PLM target and a similarity score per row, never auto-applied. Review of these 15 is in progress: two are confirmed against real PLM targets so far (`ProcessInstrumentationFunction` → `PCA_100005947`; `SafetyValveOrFitting` → `PCA_100004110`, "Relief Valve"); the rest are pending the reference-data owner's decision, including whether any of them are better handled as a project-owned reference-data *extension* (below) rather than a crosswalk entry.

**A project-owned extension path for a genuine library gap.** For any of the 15 (or a future run's) candidates that, on review, name a real physical/functional class the PCA PLM library genuinely does not carry — not merely an unreviewed `close` match — this project has designed a **T.EN-owned reference-data extension**: a separate namespace (`TEN_RDL = http://data.ten.com/rdl/`, `gold/vocab.py`) and a separate file (`ten_equipment.rdf`), deliberately never merged into or edited within the pinned, externally-owned `specs/semantics/equipment.rdf`. A `TEN_RDL` class still declares `rdfs:subClassOf` a real PCA/`RDL` parent where a reasonable one exists, so it augments the shared hierarchy rather than forking it, and a `rds_plm_crosswalk.json` entry can point its `plm_uri` at a `TEN_RDL` URI exactly as it would a PCA one — no change needed to the crosswalk mechanism itself. One real class is populated so far (`SteamTrap`, subclassed under the real PCA `Valve` class `PCA_100004125`). Deciding which further real classes belong here, and under which PCA parent, is a reference-data-governance decision for the project, not something this scaffolding makes on its own. **Off-Page Connectors are explicitly excluded from this path** (§4.6) — they are drawing artifacts, not equipment, and their DEXPI sandbox URI is a direct pass-through, never a TEN_RDL mint.

**Mechanism vs. data.** The resolver (`rules_reference.resolve_rdl_uri`), the two `graph:refdata` loaders (`map_rds_plm_crosswalk`, `map_componentclass_plm_aliases`), and the review-gate marker are all built and unit-tested. The crosswalk *data* itself — the actual RDS→PLM entries and the PostProc alias table — is populated incrementally from real project data (Workstream 1.5, tooling described in §8.3) rather than hardcoded; until a given class's entry exists, `rdlUriPending` remains asserted and honest for it, exactly as designed.

### 4.4 The rest of the canonical-schema mapping

`Document → StartUpPackage → ProcessUnit → PipelineSystem → Subline → PipingSegment → PipingComponent` maps via `pidsys:partOf`/`pidsys:hasPart` containment; `ProcessUnit → StartUpPackage` via `pidsys:hasStartUpPackage`; `Equipment`/`Nozzle` as physical objects reachable from a segment's connectivity through the nozzle wiring Silver already resolved. The Fluid catalogue and Boundary role sets load into `graph:refdata` as SKOS-shaped concepts (`rdf_mapper.map_fluid_catalogue`, `map_boundary_sets`) — this **is** the rules-as-data surface `rules_reference.py` (§5) reads.

**`Line` is a real containment level, not a placeholder.** `PipingSegment` sits under a `pidsys:Line` node (`PipingComponent --partOf--> PipingSegment --partOf--> Line`, a separate `partOf` edge alongside the physical-hierarchy one, not a replacement for it) — the RDF counterpart of Gold's line-grain bi-temporal versioning unit (§3.2). `map_line` asserts the Line's own aggregated fields (each of `fluid`/`unit`/`diameter`/`piping_materials_class`/insulation, a tuple since pieces can disagree) and, from `gold_objects`' own `attrs["pieces"]`, a real `map_segment` child node per physical piece with its own scalar attributes (`unit`/`diameter`/`piping_materials_class`/insulation type/purpose/thickness) — each piece inherits its parent Line's bi-temporal interval, since it has none of its own. A Line with no piece detail available still gets a correctly-asserted node with zero children, reported via `lines_without_piece_detail`.

**`Equipment`-kind component rows are excluded from this mapping entirely.** Silver's reconstruction emits every real Equipment element into both `silver_equipment` (correct) and `silver_components` (a classless-in-name duplicate of the same physical item) — mapping both would assert one physical item as two RDF individuals. `build_rdf_dataset_from_gold_objects` (and `build_rdf_dataset`) skip the component-grain duplicate, reporting every id skipped as `equipment_duplicate_components`. The real classification that duplicate row frequently carries (`silver_equipment`'s own `equipment_class` field is always `None` — "class enrichment: later") is harvested by equipment tag before the row is dropped, and passed into `map_equipment` as an optional `equipment_class` — asserted as a project-minted domain class `rdfs:subClassOf pidsys:Equipment` (a separate hierarchy from piping component classes), a `pidsys:equipmentClass` literal, and the same `rdlUriPending` marker `map_component` uses — so the item keeps its real classification rather than losing it along with the duplicate.

**Off-Page Connector rows are likewise excluded from `map_component`'s fallback path — see §4.6.** Before Workstream 2, an OPC element with no equipment/component classification would fall into the same `C_UNCLASSIFIED_COMPONENT` path as an ordinary unclassified physical component, silently losing its `terminates`/mating-connector semantics. That is now a routing bug to guard against, not the intended behaviour.

### 4.5 The oracle firewall, restated at the RDF layer

`gold/oracle_guard.py::assert_oracle_confined` scans every quad and raises `OracleLeakage` if `srcTurnoverSystem`/`srcSubsystem` appears in any graph other than `graph:oracle` — `gold_job.build_rdf_dataset` calls it unconditionally on every run, so a mapping bug that routes an oracle field into `graph:masterdata` is a hard failure, not a silent circularity in the validation cross-check. `assert_rule_engine_did_not_read_oracle` is the companion check every `rules_reference.py` function threads through, asserting the *set of graphs a rule read* never includes `graph:oracle`. The live Jena deployment (§5.6) enforces the same firewall by construction: its reasoner is loaded from exported `graph:masterdata` and `graph:refdata` files only, so `graph:oracle` is absent from its input rather than merely unread, and every parity run re-checks that no oracle predicate reached it.

### 4.6 Off-Page Connectors (Workstream 2)

**Status (2026-09-23): confirmed on real data.** Run against the real Project A DEXPI load (4 drawings), the projection and the SPARQL acceptance queries in `opc_verification_sparql.md` give:

| Check                                                                   | Result                                                                                                                                          |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| OPC nodes (`pidsys:OffPageConnector`)                                 | **68** — all `PipeOffPageConnector`s; the 10 `SignalOffPageConnector`s are correctly not harvested (the instrument tier is deferred) |
| OPCs with a full descriptor set and a`terminates` edge                | 68                                                                                                                                              |
| `terminates` guard (a signal/instrument OPC asserting `terminates`) | **0** — pass                                                                                                                             |
| Cross-document`OffPage` pairs (both endpoints are OPC nodes)          | 3                                                                                                                                               |
| Open boundaries (mate on a sheet not in this load)                      | 62                                                                                                                                              |
| Cross-sheet walks (segment → OPC → OPC → segment)                    | 3                                                                                                                                               |

The conservation identity holds: `2 × pairs + open boundaries = OPC nodes` (2 × 3 + 62 = 68). Every OPC is either matched or flagged as continuing off-set, with none dropped or double-counted. Low pairing is expected on a 4-sheet slice, because DEXPI pairs by `SP_pairedWithID` only when both mating sheets are loaded; pairing climbs toward `pairs ≈ nodes / 2` as the loaded set approaches the whole plant. This closes the checkout-verification concern that `opc_edit_manifest.md` was written for: the path is demonstrably present and producing correct triples in the real checkout.

**What an Off-Page Connector is, and why it needed its own path.** Per `data_specification.md` §2.11–§2.13 and `claude/off_page_connector_representation.md`, an Off-Page Connector is a first-class master-data entity — not equipment, not an ordinary piping component — that *terminates* a Piping Segment (a direct, non-reified edge) and *connects to* its mating connector on another document via a Connection of type "Off-Page continuation" (a reified edge, like any other Connection). `FlowOutPipeOffPageConnector`/`FlowInPipeOffPageConnector` (confirmed against the real DEXPI 1.4 spec) are `ComponentClass` values carrying flow direction on a `PipeOffPageConnector` element, not distinct element types or equipment classes.

**RDF shape (`gold/vocab.py`, `gold/rdf_mapper.py::map_off_page_connector`):**

- `C_OFF_PAGE_CONNECTOR ⊑ IDO_INFORMATION_OBJECT` (§4.3) — a drawing-continuation artifact, never placed on the physical `ido:PhysicalObject`/`ido:connectedTo` graph as if it were a component.
- `P_TERMINATES`: a direct edge `OffPageConnector → PipingSegment`, asserted **only** when `on_segment` is present and the OPC sits on a pipe. **`_is_piping_opc` keys on the flow role, not the `opc_type` label** (design correction, 2026-09-23, from real Project A data): an OPC whose `flowSense` is `FlowIn`/`FlowOutPipeOffPageConnector` terminates its segment. That includes the many `opc_type="Utility Connector"` OPCs (steam, flare, drain lines) that genuinely sit on pipes. Only a **signal** flow role or an explicit **instrument** type gets a node with no `terminates` edge. An earlier version keyed on `opc_type` and would have denied `terminates` to utility lines on pipes. That was the stale rule behind 37 false failures in the first SPARQL guard. Deliberately **not reified** — §2.13 does not mark this relationship "(via Connection)", unlike the mating-pair relationship.
- `P_COMPONENT_CLASS_URI`: a direct pass-through of the real DEXPI sandbox RDL URI (e.g. `http://sandbox.dexpi.org/rdl/FlowOutPipeOffPageConnector`) — never resolved against the PLM crosswalk, never minted into `TEN_RDL` (§4.3.1's exclusion, restated).
- The mating-pair edge reuses `map_connection` **unmodified**, `conn_type="Off-Page continuation"`, `derived=True` always (per `data_specification.md` §2.12's own worked example: a matched off-page pair is the literal case given for `Derived`, since the cross-document edge is reconstructed by matching two independent per-document statements, never stated whole in either document's XML).
- An **unmatched** OPC (no mating pair found) gets a node only — no Connection is asserted for it. The miss is carried as a Silver `silver_quality` data-quality flag (`opc_open_boundary`), not a graph edge — nothing to guess at the other end.
- **Guardrail:** the OPC element must be excluded from the ordinary component projection path, the same way an Equipment-kind component duplicate is excluded (§4.4) — otherwise it is asserted twice, once correctly via `map_off_page_connector` and once wrongly via `map_component`'s `C_UNCLASSIFIED_COMPONENT` fallback (its pre-Workstream-2 behaviour).

**`conn_type` vocabulary alignment.** Silver's Stage C (already built, independent of this workstream) emits the matched-pair row into `silver_connections` as `conn_type="OffPage"` — the string this project's `CONNECTION_STRUCT` and quality gate have always used. Rather than rename Stage C's column, Gold treats `conn_type ∈ {"OffPage", "Off-Page continuation"}` as the same continuation type at the mapping/dispatch boundary — a one-line Gold-side alias, not a Silver rewrite.

**Silver feed (`silver_opc_feed_change_spec.md`), summarized — this is what makes the Gold path reachable at all:**

- **Already built, independent of this work:** Stage C's cross-document OPC assembly (`silver/assemble.py`/`assemble_job.py`) already ran `match_pairs`, wrote the `OffPage` connection row, and flagged unmatched OPCs — Gold's Connection-side path had a real feed all along.
- **The actual gap, now closed:** Stage C harvested and stitched OPC *pairs* but never persisted a per-OPC *entity* row — nothing for `map_off_page_connector`'s node/`terminates` side to read. Closed by: widening both formats' harvest (`_harvest_opcs` / `bppidsys/offpage.py::harvest_opcs`) to carry `opc_type`, `flow_direction` (DEXPI only), `component_class_uri` (DEXPI only), and the `to_from_dir`/`to_from_text` mate-narrative fields; retaining the stitched pair/unmatched structure instead of collapsing it to bare counts; attaching `on_segment` (a Spark join to `silver_components.segment_id`, not a second pipeline run); and persisting one row per placed OPC into a new `silver_off_page_connectors` table.
- **CDC:** `off_page_connector` is now a fifth grain in Silver's Stage E producer (`silver/cdc.py`/`cdc_job.py`, extending the existing generic per-grain diff machinery rather than a parallel differ) and in Gold's consumer (`gold/silver_cdc.py::OBJECT_KINDS`, `gold/spark_bridge.py::GRAIN_TABLE`/`GRAIN_ID_COL`). Its anchor is UID-free and segment-free — `("OPC", drawing_number, mate_key)` — so re-routing an OPC to a different segment reads as an ordinary engineering Modify, not a Delete+New; `gold/spark_job.py::run_gold` needed **no edit at all**, because its per-grain attrs loop already drives off `GRAIN_TABLE` generically.
- **First-load path (no CDC needed for a single-version PoC run):** `gold_job.py`'s direct `GoldInputs.off_page_connectors`/`.opc_connections` inputs read `silver_off_page_connectors` directly — Stage E's OPC-CDC producer is only required to *version* OPCs across drawing revisions, not to get them into Gold at all.

**Tests:** `tests/test_rdf_mapper.py::TestMapOffPageConnector` (node shape, `terminates` gating, `conn_type` alias, unmatched-OPC no-edge case) and `silver/tests/test_cdc_opc.py` (13 tests: delete+recreate immunity both formats, engineering-change Modify, the segment-reroute-is-Modify decision, anchor UID/segment/GUID-normalisation boundaries). The real-data results above are the end-to-end confirmation; `opc_verification_sparql.md`'s expect-0 guard (Q3b) is the standing acceptance check.

---

## 5. Declarative classification — the Jena/local half of the hybrid

### 5.1 Scope, precisely

Local, node-local rules only. Given a component and its immediate neighbours (from `graph:masterdata`) and the reference catalogues (from `graph:refdata`), classify and guard — never partition the whole graph into fragments. Each function below is individually testable against a single component and its neighbourhood; none of them iterates the whole plant.

### 5.2 Fluid self-ownership

`classify_fluid_category(fluid_code, catalogue)` returns `flare | steam_condensate | process | utility`, reading `graph:refdata`'s Category/Subcategory (Category=`Flare` → flare; Category=`Utility` with Subcategory containing `Steam` or `Condensate` → steam_condensate; Category=`Process` → process; else utility). `is_self_owning` is `True` for flare and steam_condensate — decided **before** any consumer trace runs, or a naive engine walks a steam header into whatever it terminates at.

### 5.3 Boundary roles

`load_boundary_roles` reads `graph:refdata`'s four role sets (isolation / positive / relief / trap). `CheckValve` is boundary-forming in **no** role set, by construction. `SafetyValveOrFitting` and `Reliefdevices` both resolve to `relief` (the export's two class names for the same role).

### 5.4 The three directional guards

All three read `pidsys:flowsTo` over `ido:connectedTo` adjacency (§4.2), and are proven against fixtures reproducing `walk.py`'s own documented scenarios (nitrogen teeing into a process header; a process fragment discharging into flare; a relief valve protecting an upstream process line) and, since 2026-09-23, against real Project A data in live parity with Jena (§5.6):

- **`flare_guard(ds, fragment_component, neighbour, catalogue)`** — `True` (skip as consumer) iff the neighbour's fluid classifies as `flare` **and** flow runs strictly one-way *into* it. Falls through whenever direction is unknown or reversed.
- **`directional_consumer_guard(ds, fragment_component, neighbour)`** — generalises the flare guard to every fluid: `True` (skip) iff flow runs strictly one-way *from the neighbour into the fragment*.
- **`relief_attribution(ds, relief_component)`** — returns the **protected** neighbour (flow strictly *into* the valve), or `None` when direction is not identifiable — only ever *adds* a correct assignment, never forces a wrong one.

### 5.5 Allocation & identity rules

Small, data-driven functions a systemization run calls the same way a Jena rule body would invoke a declared predicate: `allocate_boundary_valve_or_blind(priority_a, priority_b)` (higher-priority/earlier-commissioned side wins), `allocate_steam_trap()` (always steam side), `allocate_sampling_connection()` (always upstream), `allocate_interface_valve("flare"|"drain")`, `allocate_vessel_column_instrument()` (equipment system), and `fragment_merge_key(system_kind, sup, fluid=..., anchor_equipment=...)` — `(SUP, fluid)` for utilities, `(SUP, anchor-equipment)` for process.

### 5.6 The Jena rules engine — live, in parity with the Python reference

`gold/jena_rules/classification.rules` is the Apache Jena generic-rule-syntax counterpart of §5.2–§5.4. **Since 2026-09-23 it runs live on Fuseki, and its output matches `rules_reference.py` exactly on real Project A data** (4 DEXPI drawings: 23,876 masterdata and 164 refdata triples):

| Rule                                               | Python reference vs. Jena, live                         |
| -------------------------------------------------- | ------------------------------------------------------- |
| Fluid classification (§5.2)                       | 39 / 39 fluids agree                                    |
| Flare guard (§5.4)                                | 162 / 162 skips agree, none on one side only            |
| Directional consumer (supply-tie-in) guard (§5.4) | 1,263 / 1,263 skips agree, none on one side only        |
| Relief attribution (§5.4)                         | 39 / 39 valves agree, none with more than one candidate |

**Deployment** (`fuseki/gold-rules.ttl`, `fuseki/docker-compose.yml`). A second, read-only Fuseki service, `gold-rules`, runs beside the TDB2 `gold` dataset in the same container:

- It is an in-memory `ja:MemoryModel`, wrapped in a `ja:InfModel` with Jena's `GenericRuleReasoner` and `ja:rulesFrom` pointing at the rules file.
- Its query endpoints are declared in the explicit `fuseki:endpoint` / `fuseki:operation` form. The legacy `fuseki:serviceQuery` form did not register a query operation on the project's `stain/jena-fuseki` image.
- **Its input is exactly two files**, `fuseki/rules-input/masterdata.nt` and `refdata.nt`, exported from the same `ds` the notebook builds. `graph:oracle` is never among them (§4.5).

**Why not a reasoner over the TDB2 dataset directly:**

- An InfModel over TDB2 does not see the drop-and-replace pushes from `fuseki_bootstrap.push_dataset`; it would need a `rebind()` that HTTP cannot trigger.
- TDB2's strict transactions do not reliably pass through an InfModel/UnionModel stack.

Loading at startup avoids both problems. Re-export, restart the container, and every inference is recomputed from scratch.

**Parity harness** (`gold/jena_parity.py`; notebook §8b): `export_rule_input(ds)` → `restart_fuseki()` → `run_parity(ds)`. A preflight check must pass before the comparisons count:

- **Input:** for every predicate a rule body reads, and every `vocab` constant the Python rules read, the triple count in `ds` equals the count in Jena.
- **Predicate drift:** no predicate the Python rules read is missing from the rule bodies, and no predicate a rule body reads has zero triples.
- **Relief role:** the relief role URI is present in `graph:refdata`.
- **Oracle:** no oracle predicate reached the reasoner.

The report then compares the four sections above. A relief valve with 2+ protected neighbours is reported as "multi-candidate" rather than failed: Jena asserts every such neighbour, while Python returns the first it meets. `python -m gold.jena_parity --fixture` is the smoke test. `tests/test_jena_parity.py` (15 tests) covers the diff logic and checks the real rules file for vocabulary drift.

**Output predicates:**

- `selfOwningClass` on fluids.
- `skipAsConsumerSignal` from both guards, plus a per-guard reason (`skipFlareSink`, `skipSupplyTieIn`) so the two can be compared separately.
- `protectedBy` from relief attribution.

These are queryable live at `http://localhost:3030/gold-rules/sparql`, and materialised into `graph:inferred` in the `gold` dataset (§5.7).

**Rule corrections made while bringing it live**, each aligning the Jena rules with the Python reference:

1. **Steam/condensate subcategory:** the pattern is now `'.*(Steam|Condensate).*'`, because Jena's `regex()` matches the whole lexical form, not a substring.
2. **Relief role:** the rule binds the exact role URI (`pidsys:boundary_role/relief`) instead of applying a text regex to a URI node.
3. **Flare guard:** the rule reads only the neighbour's fluid, as `flare_guard` does. It no longer also requires the fragment's own fluid and a `notEqual` check.
4. **Guard reasons:** the per-guard reason predicates above were added.
5. **Adjacency:** the rules read `ido:connectedTo`, not the retired `pidsys:isConnectedTo` (§4.2, risk #16).

Full run history: `claude/jena_rules_deployment.md`.

**Ongoing discipline** (`medallion_rdf_ido_strategy_mapping.md` §10 step 4): re-run §8b after any change to `classification.rules`, `rules_reference.py`, `vocab.py` or `rdf_mapper.py`. `PARITY PASS` is the gate.

### 5.7 `graph:inferred` — the rule output, materialised with PROV

`gold/inferred_graph.py` (notebook §8c) turns the live rule output into a named graph in the `gold` dataset, so a systemization run can read it next to `graph:masterdata`. It is a separate graph from `graph:results`: these predicates are *inputs* to the systemization computation, not its output.

**Write — `materialise_inferences(parity, cfg)`.** It runs only if two conditions hold:

- **Parity passed.** Jena's output is published only once it has been shown to equal the Python reference (§5.6).
- **The parity check was about this exact input.** Every parity report records a sha256 of the two exported input files and the rules file. If what `gold-rules` has loaded now differs (a re-export, or an edit to the rules), the write is refused and the changed input is named.

It then CONSTRUCTs exactly the predicates the rule heads derive — read from the rules file, not hard-coded — adds PROV, re-checks for oracle predicates, and PUTs the graph (drop-and-replace, like every other Gold graph).

**PROV shape.**

- `graph:inferred` is a `prov:Entity`, `prov:wasGeneratedBy` a run and `prov:wasDerivedFrom` `graph:masterdata` and `graph:refdata`.
- The run is a `prov:Activity`. It records start and end times, the agent (the `gold-rules` service), and the parity status with per-section agreement counts.
- Its `prov:used` inputs are identified by content hash: a masterdata snapshot and a refdata snapshot (`prov:specializationOf` their graphs) and the rules file (a `prov:Plan`).
- Each derived predicate states which rule produces it, e.g. `pidsys:skipFlareSink pidsys:derivedByRule <rule/flareGuardSkip>`. Every predicate comes from a known rule set, so "which rule fired" is answered per fact without reification or RDF-star. For `selfOwningClass`, which three rules produce, the value (`flare` / `steam_condensate` / `process`) identifies the rule.

**Read — for the systemization run.**

- `load_inferred(cfg)` returns plain Python sets and dicts keyed by Silver component id: self-owning fluid classes, flare-sink skips, supply-tie-in skips, and relief valve → protected neighbours.
- `inferred_is_current(cfg, ds)` compares the run's recorded hashes with the `ds` about to be walked and the current rules file. `push_dataset` does not touch `graph:inferred`, so a re-projection leaves it out of date until §8b and §8c are run again; this check is what makes that visible.

**First real run (Project A, 2026-09-23):** HTTP 201. The graph holds 2,897 derived triples plus 59 PROV triples:

| Predicate                | Triples                                                                                       |
| ------------------------ | --------------------------------------------------------------------------------------------- |
| `skipFlareSink`        | 162                                                                                           |
| `skipSupplyTieIn`      | 1,263                                                                                         |
| `skipAsConsumerSignal` | 1,425 (= 162 + 1,263; the two guards fire on opposite flow directions, so they never overlap) |
| `protectedBy`          | 39                                                                                            |
| `selfOwningClass`      | 8                                                                                             |

The read-back matches the parity counts exactly, and the freshness check is current on masterdata, refdata and rules.

The eight `selfOwningClass` values are flare and process only. The other 31 fluids in the catalogue classify as ordinary utility, and none as steam/condensate. Both engines agree on this (parity 39/39), so it reflects the Project A Fluid sheet: no Category=Utility row has a Subcategory containing "Steam" or "Condensate". Worth confirming against the plant's actual steam services.

---

## 6. SPARQL query surface

`gold/sparql_queries.py::EXAMPLE_QUERIES` are real SPARQL 1.1, run via `fuseki_client.sparql_query` against a live Fuseki, or `run_sparql(ds, query)` locally through `rdflib`'s own query engine:

- **`systems_and_members`** — every computed system's member count, from `graph:results` alone.
- **`derived_connections`** — every reified Connection flagged `derived` (now including matched Off-Page continuation pairs, §4.6).
- **`oracle_cross_check`** — the **one** query permitted to join `graph:oracle` against `graph:results`, and it is read-only reporting for a person to review, never an input to another query or a rule.
- **`boundary_roles`** — the rules-as-data surface itself, queryable directly.

`run_local_pattern` is a plain triple-pattern matcher over the in-memory `Dataset`, for answering the same questions without a running Fuseki. `opc_verification_sparql.md` adds the Workstream-2 and master-data acceptance queries, including four expect-0 guards: M1b dual-typed segments, M7b membership pointing at a physical item, M8 oracle leak, and OPC Q3b. M7b stays trivially satisfied until `graph:results` has a writer (§8.2). The live classification predicates are queryable separately on the `gold-rules` endpoint (§5.6).

---

## 7. Gold table family (bi-temporal Delta tables)

One table per Silver object kind, each Silver column carried through plus the four temporal columns:

| Table                | Grain                             | New columns beyond the matching`silver_*` table                                                                    |
| -------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `gold_components`  | one Piping Component version      | `valid_from`, `valid_to`, `transaction_from`, `transaction_to`                                               |
| `gold_segments`    | one Piping Segment version        | *(same four)* — `src_turnover`/`src_subsystem` still ride along as quarantined lineage, never a compute input |
| `gold_equipment`   | one real Equipment version        | *(same four)*                                                                                                      |
| `gold_connections` | one undirected connection version | *(same four)*                                                                                                      |

Design rules, carried over from Silver's own table-family discipline: one table per kind across both source formats; append-only, "never delete, close the interval"; the oracle columns physically isolated to `gold_segments` and never copied elsewhere.

**Off-Page Connectors do not get a new physical table.** `gold_objects` is already object-kind-agnostic (`object_kind` column + JSON `attrs`), so `off_page_connector` rides that existing generic mechanism — the same one every kind above is actually persisted through — reading `silver_off_page_connectors` (§4.6) rather than requiring a dedicated `gold_off_page_connectors` table or a `silver/schema.py`/`gold/schema.py` change beyond registering the new Silver table.

---

## 8. Current implementation status

### 8.1 What's built and running

- **Bi-temporal versioning** (§3): `gold/temporal.py` (pure, dependency-free), `gold/silver_cdc.py` (consumes real `silver_cdc` events, tolerant of anchor-bucket collisions and line-grain aggregation), `gold/spark_job.py::run_gold` — confirmed executing against a live Spark session, writing real `gold_objects`/`gold_anomalies` Delta tables.
- **RDF/IDO projection** (§4): `gold/rdf_model.py` is genuinely `rdflib`-backed (real `URIRef`/`Literal`/`Dataset`, real Turtle/N-Quads serialisation via rdflib's own engine); `gold/rdf_mapper.py`, `gold/vocab.py`, `gold/oracle_guard.py` implement the full mapping described in §4, including the Line/Segment containment, the Equipment-dedup-plus-classification-harvest, the catch-all/unclassified fallback, and the two RDL/PLM resolution bridges (§4.3.1). `gold_job.build_rdf_dataset_from_gold_objects` sources the projection from `gold_objects`' current-truth rows, sequentially after `run_gold` — the two capabilities compose into one pipeline rather than being independent Silver consumers.
- **`gold/owl_reasoning.py`** — a first OWL-RL cross-check (real `owlrl.DeductiveClosure`) validating class-hierarchy subsumption against the declarative rules' own assumptions. Deliberately scoped to hierarchy only — the fluid/flow business rules are not re-expressed as OWL/SWRL.
- **Declarative classification** (§5): `gold/rules_reference.py` + `gold/jena_rules/classification.rules`, validated against fixtures reproducing `walk.py`'s documented scenarios.
- **`graph:inferred`** (§5.7): `gold/inferred_graph.py` materialises the Jena output into the `gold` dataset with PROV, gated on a passing parity check over the same input, with a read API and a freshness check for the systemization run. Confirmed on real Project A data: 2,897 derived triples, matching parity exactly. `tests/test_inferred_graph.py` has 15 tests.
- **The live Jena rules engine** (§5.6): the `gold-rules` Fuseki service (`fuseki/gold-rules.ttl`) plus the parity harness `gold/jena_parity.py` (notebook §8b). On real Project A data it passes with full agreement between Jena and Python across all four rule families: 39 fluids, 162 flare-guard skips, 1,263 supply-tie-in skips and 39 relief valves.
- **A live Fuseki**: `fuseki/docker-compose.yml` + `gold/fuseki_bootstrap.py::push_dataset`, confirmed working end to end against a real, `docker compose up`-started instance, including HTTP Basic Auth and real project-scale data (tens of thousands of triples).
- **`gold/fuseki_client.py`** is stdlib-only (`urllib`) and genuinely production-usable against a real Fuseki instance with no dependency at all.
- **Silver's `component_class_uri` carrier column** (the input Cause B/C's resolution bridges depend on) is confirmed flowing end to end on real Project A data — see §8.3.
- **Off-Page Connector projection (Workstream 2, §4.6) — confirmed on real data.** This covers the Gold side:

  - `gold/vocab.py` (`C_OFF_PAGE_CONNECTOR`, `P_TERMINATES`, `P_COMPONENT_CLASS_URI`);
  - `gold/rdf_mapper.py::map_off_page_connector`, with `_is_piping_opc` keyed on the flow role;
  - `gold/gold_job.py`'s `off_page_connector` routing.

  It also covers the Silver side: the harvest/stitch enrichment, the `silver_off_page_connectors` entity table, and the Stage E CDC grain. On the real Project A load it produces 68 OPC nodes, 3 cross-sheet pairs and 62 open boundaries, the conservation identity holds, and the Q3b `terminates` guard returns 0 rows.

The user's own environment runs `rdflib`, `owlrl`, `pyspark`, and `delta-spark` alongside Bronze/Silver's existing Spark/Delta stack; this development environment does not carry any of them, so code changes made here are verified by direct execution where possible and, for the `rdflib`/`owlrl`-dependent modules, against a throwaway local stub built to the documented API contract — an internal-consistency check, not a real-library pass. The user's own `python3 -m unittest discover -s tests` in their environment is the actual confirmation step for that portion of the suite.

### 8.2 Genuinely open

- **A systemization run that consumes `graph:inferred`.** The predicates are materialised and readable (§5.7). The Silver/Python walk does not yet call `load_inferred`: its flare guard, supply-tie-in guard and relief attribution still compute their own answers. Wiring it in means checking `inferred_is_current` first, then using the materialised signals in place of the walk's own guard evaluations.
- **Moving `GRAPH_INFERRED` into `gold/vocab.py`.** It is defined in `gold/inferred_graph.py` for now; it should move beside `GRAPH_RESULTS`, and `fuseki_bootstrap.NAMED_GRAPHS` should *not* include it (it has its own writer).
- **A `graph:results` writer.** No systemization run yet writes computed `CommissioningSystem`s and their members back as RDF. Until one does, `systems_and_members` and `oracle_cross_check` (§6) return nothing, and the expect-0 guard M7b is trivially satisfied.
- **Interval splitting for retroactive corrections** (§3.5) — named, not implemented; a `RetroactiveCorrection` is raised and routed to a person instead.
- **Per-object valid-time keyed off each object's own drawing, for the deprecated `diff_snapshots` fallback path** — takes one shared `valid_from` per run rather than per-drawing. The primary CDC path (`gold/spark_job.py`) already resolves this per-drawing from Bronze; the limitation is scoped to the fallback only.
- **Parity on Project B.** Jena/Python parity (§5.6) is demonstrated on Project A (DEXPI). Project B (PostProc) runs through the same harness unchanged, but has not been run yet.
- **Reference-data hygiene.** The Boundary sheet contains an empty Role cell, which pandas reads as NaN and the projection mints as `pidsys:boundary_role/nan`. It is harmless to the rules, but the notebook's refdata load should drop empty roles (`dropna(subset=["Role"])`).
- **Workstream 1.5 — the remaining Cause C review, and the T.EN extension.** See §8.3.
- **Workstream 2 — the rest of the `precomm:` extension.** The Off-Page Connector piece is done (§4.6).
  - Still a nice-to-have: `flow_sense` enrichment on the `OffPage` connection, orienting FlowOut→FlowIn instead of the current hard-coded `"none"`.
  - Beyond the OPC piece, the originally scoped list (`ido_semantic_mapping_spec.md` §6.1) still has unaddressed items: formal declarations for `ProcessUnit`/`StartUpPackage`/`CommissioningSystem`/`InstrumentationLoop`, the boundary role, and node identity.
  - The standalone ontology document is still to write: `specs/semantics/pidsys_extension.ttl` exists as a draft, and must be reconciled with what `gold/vocab.py` and `rdf_mapper.py` actually emit (`claude/workstream_2_precomm_ontology_scoping.md`).

### 8.3 Workstream 1.5 — crosswalk data status

The resolution *mechanism* (§4.3.1) is built and tested; the crosswalk *data* it reads is populated incrementally against real project data, not hand-authored:

- The DEXPI RDS→PLM crosswalk's pre-published portion (the PLM library's own `skos:exactMatch`/`closeMatch` mappings) is machine-extracted directly from the real reference ontology (`specs/semantics/equipment.rdf`), self-verified against that file's own published mapping counts.
- The PostProc alias table is seeded from confirmed real renames and extendable by matching real `component_class` strings against real PLM labels.
- **Silver's `component_class_uri` carrier column, confirmed working end to end on real Project A data.** A prior gap — Silver's reconstruction pipeline extracted `ComponentClassURI` from the source DEXPI element but never carried it through to the Silver row shape — is fixed and verified against real data: 546 of 640 raw pipeline-extraction components and 357 of 435 final Silver rows carry a real RDS URI on a representative real drawing.
- **Cause C's real gap, current result.** With that column flowing, the gap-finding tool run against real Project A data covers **187 RDS codes** already resolved by the pre-published crosswalk, leaving **15 distinct RDS URIs (3 boundary-forming)** as a genuine, ranked, human-reviewable gap. Review is in progress via a small confirm/reject tool that records each decision either as a new crosswalk entry (confirmed) or a no-op note (deliberately rejected, e.g. a drawing-artifact class that isn't a real physical/functional component at all) — two of the 15 are confirmed so far (`ProcessInstrumentationFunction`, `SafetyValveOrFitting`).
- **A project-owned reference-data extension (`TEN_RDL`) is designed and has one real class populated.** For any reviewed Cause C candidate that turns out to be a genuine PLM-library gap rather than an unreviewed match, a separate namespace and file (`gold/vocab.py`'s `TEN_RDL`, `ten_equipment.rdf`) hold project-owned classes, kept structurally distinct from and never edited into the pinned `equipment.rdf` (§4.3.1). `SteamTrap` is populated, subclassed under the real PCA `Valve` class (`PCA_100004125`). Off-Page Connectors are explicitly excluded from this path (§4.6) — they are drawing artifacts with their own real DEXPI sandbox URI, not an equipment-library gap.
- Confirming each residual gap's correct PLM target (or determining that none of the published library's classes fit, and it belongs in `TEN_RDL` instead) remains a human engineering review step, not something this resolution mechanism decides on its own — by design, the same way a `close` match on a boundary-forming class is gated for review rather than auto-trusted.

---

## 9. Suggested phasing

1. *(Built.)* Bronze, Silver (including object-grain CDC).
2. *(Built.)* Bi-temporal Gold over the object-grain CDC (§3), driven directly by Silver's `silver_cdc` table.
3. *(Built.)* RDF/IDO projection of Gold (§4), on real `rdflib`, sourced from `gold_objects` after bi-temporal resolution.
4. *(Built and live, 2026-09-23.)* Jena rules for classification and local/directional rules (§5), running on Fuseki in full parity with the Python reference on real Project A data (§5.6). "Validated" still means comparing the classification predicates with `walk.py`'s own logic on the same drawings; a rule never reads `graph:oracle` directly (§4.5).
   - Its output is materialised into `graph:inferred` with PROV (§5.7, built 2026-09-23). Next (open, §8.2): have the systemization run consume it.
5. *(Built; confirmed on real data 2026-09-23.)* Off-Page Connector projection (§4.6) — the second Workstream-2 deliverable, after the Connection reification proven in phase 3.
6. **Second consumer (Test Packages)** reuses this same Gold/RDF surface with a different cut rule (class/rating breaks + test-containment) — the reason Gold's schema (§7) and named graphs (§4.1) are kept use-case-neutral rather than systemization-specific.

---

## 10. Risks & mitigations (current, standing)

| #  | Risk                                                                                                                                                                                                                                                                                                                                                                                                    | Mitigation                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| -- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1  | A Jena/forward-chaining engine is asked to do the global partition.                                                                                                                                                                                                                                                                                                                                     | `rules_reference.py` and `classification.rules` are scoped, by construction and by docstring, to local/node-local rules only; the global walk stays in Silver/Python (§5.1, §9).                                                                                                                                                                                                                                                                                 |
| 2  | An unconfirmed IDO domain term is asserted, quietly over-claiming standards alignment.                                                                                                                                                                                                                                                                                                                  | `vocab.py` names the two real IDO/LIS-14 classes used (`IDO_PHYSICAL_OBJECT`, `IDO_INFORMATION_OBJECT`) and IDO's native `connectedTo` property, and routes everything else through `pidsys:` predicates and an explicit `rdlUriPending` marker (§4.2, §4.3).                                                                                                                                                                                            |
| 3  | Oracle leakage into a rule or a served graph, making the validation cross-check circular.                                                                                                                                                                                                                                                                                                               | `assert_oracle_confined` (hard-fail, run unconditionally) + `assert_rule_engine_did_not_read_oracle` (threaded through every classification function) — §4.5, unit-tested; the live Jena reasoner is loaded without `graph:oracle` at all, re-checked on every parity run (§5.6).                                                                                                                                                                             |
| 4  | A bare, unreified connection or a missing`derived` flag lets inferred topology masquerade as source truth.                                                                                                                                                                                                                                                                                            | `map_connection` raises on a missing `derived` key; every connection is a reified node, never a bare triple (§4.2), including Off-Page continuation pairs (§4.6).                                                                                                                                                                                                                                                                                                |
| 5  | Bi-temporal collapse to one axis, or a retroactive correction silently mis-modelled.                                                                                                                                                                                                                                                                                                                    | Two independent axes throughout`temporal.py`; retroactive corrections raise rather than guess (§3.3, §3.5).                                                                                                                                                                                                                                                                                                                                                        |
| 6  | An oracle-only change is mistaken for an engineering Modify.                                                                                                                                                                                                                                                                                                                                            | `GoldRow.content_key` excludes oracle fields from the change-detection fingerprint by construction (§3.3), unit-tested.                                                                                                                                                                                                                                                                                                                                             |
| 7  | A real`silver_cdc` batch's component `anchor` is a bucket key, not a per-instance id — a strict one-event-per-anchor application can abort an entire Gold run on legitimate multi-sibling data.                                                                                                                                                                                                    | `apply_silver_cdc_events_tolerant` (§3.2) collects each collision as a `CdcAnomaly` and keeps applying the rest of the batch.                                                                                                                                                                                                                                                                                                                                     |
| 8  | RDF projection had not been realigned to line grain — a systemization run reading`graph:masterdata` would see piece-level, not line-level, piping nodes.                                                                                                                                                                                                                                             | `vocab.C_LINE` + `rdf_mapper.map_line` (§4.4): every current-truth line row is a real `pidsys:Line` node with real child `PipingSegment` nodes carrying their own attributes.                                                                                                                                                                                                                                                                                 |
| 9  | The`rdflib`/`owlrl` graduation and its dependent modules cannot be executed against the real libraries outside the user's own environment.                                                                                                                                                                                                                                                          | Call surface kept identical to the pre-graduation version; verification methodology and its limits are disclosed rather than implied to be a real-library pass; the user's own environment is the actual confirmation step.                                                                                                                                                                                                                                            |
| 10 | `run_gold` (bi-temporal) and the RDF/Fuseki push were independently-proven outputs with no composition, risking `graph:masterdata` disagreeing with `gold_objects`.                                                                                                                                                                                                                               | The RDF projection reads`gold_objects`' current-truth rows, sequentially after `run_gold` — `build_rdf_dataset_from_gold_objects`, asserting all four bi-temporal predicates per-object.                                                                                                                                                                                                                                                                        |
| 11 | A component with no`component_class`, or one carrying a source-tool catch-all placeholder string, crashes or is mis-asserted as a real domain class.                                                                                                                                                                                                                                                  | `component_class` is optional throughout `map_component`; both the missing/`None` case and a confirmed catch-all string collapse onto the same `C_UNCLASSIFIED_COMPONENT` fallback (§4.3).                                                                                                                                                                                                                                                                    |
| 12 | Every Equipment element is asserted as an RDF individual twice (once via`map_equipment`, once as a classless-or-not component duplicate), and excluding the duplicate can silently discard its only real classification.                                                                                                                                                                              | `kind==\"Equipment\"` component-grain rows are excluded from the RDF projection (reported as `equipment_duplicate_components`); their real classification, when present, is harvested by tag and passed into `map_equipment` as `equipment_class` before the row is dropped (§4.4).                                                                                                                                                                           |
| 13 | `rdlUriPending` had no defined resolution path, and Cause C's real gap (RDS codes the published PLM library doesn't cover) had no home.                                                                                                                                                                                                                                                               | Two format-scoped RDL/PLM resolution bridges (§4.3.1) with confidence carried per resolution and a review gate on boundary-forming classes; crosswalk data populated incrementally against real project data (§8.3, currently 187 covered / 15 gap / 3 boundary-forming, 2 confirmed); a scaffolded, project-owned`TEN_RDL` extension namespace with one real class (`SteamTrap`) for any candidate that is a genuine library gap rather than a crosswalk match. |
| 14 | An Off-Page Connector, with no dedicated Gold class or mapper, silently fell into`map_component`'s `C_UNCLASSIFIED_COMPONENT` fallback, losing its `terminates`/mating-connector semantics — or, once a projection existed, could be asserted twice (once correctly, once via the old fallback) if the exclusion guard were missed.                                                              | `map_off_page_connector` + the component-projection exclusion guard (§4.6, mirroring the Equipment-dedup pattern of risk #12); `TestMapOffPageConnector` pins both the correct projection and the no-double-assertion behaviour; OPC Q3b (expect 0) is the standing real-data check.                                                                                                                                                                              |
| 15 | Code delivered against a real repo in this kind of session can silently fail to land in the actual checkout — confirmed once already (`gold/silver_cdc.py`'s `OBJECT_KINDS` edit came back pristine on a real pull), which would leave a path dormant without any visible error.                                                                                                                   | Treat "delivered in the conversation" and "present in the checkout" as two different states until checked:`opc_edit_manifest.md`'s per-file `grep` checklist, and, more conclusively, a real-data acceptance run (the OPC path is confirmed this way, §4.6).                                                                                                                                                                                                      |
| 16 | **Vocabulary drift between the projection and a hand-written rule artifact.** The projection moved adjacency to `ido:connectedTo` while `classification.rules` still read `pidsys:isConnectedTo`. On real data every guard and the relief rule then matched nothing, silently: the run raised no error, and a preflight check that hard-coded the same stale URI reported 0 = 0 as a match. | `jena_parity.py`'s preflight derives its predicate set from `vocab` and from the rule bodies themselves. It fails when a predicate the Python rules read is missing from the rules, or when a rule-body predicate has no data. `tests/test_jena_parity.py` checks the real rules file for the retired predicate. The discipline in §5.6 (re-run §8b after any change to vocab, mapper or rules) is the gate.                                                   |

---

## 11. Summary

Gold turns Silver's canonical plant model into a bi-temporal history and a semantic RDF/IDO projection, closing the two gaps the original medallion strategy left open: two independent time axes (valid vs. transaction) rather than one collapsed pair, with "never delete, close the interval" implemented down to the ordinary-supersession-vs-correction distinction and a named limit (no interval splitting); and an RDF projection that is honest about IDO — two real foundational classes asserted (`PhysicalObject` for plant items, `InformationObject` for documents and Off-Page Connectors) and IDO's native `connectedTo` used for adjacency, domain terms subclassed under them with an explicit pending-resolution marker rather than an invented `ido:FunctionalObject`, reified Connections that never let inferred topology pass as source truth, and an oracle firewall enforced as a hard-fail structural invariant. The inference layer is deliberately the *local* half of the hybrid the strategy converges on — fluid self-ownership, boundary roles, and the three directional guards, faithfully ported from `walk.py` — while the global connected-fragment partition stays exactly where it is proven, in Silver's Python walk.

All three of Gold's execution paths now run on real data:

- The bi-temporal path runs on a live Spark session, producing real Delta tables.
- The RDF projection runs on real `rdflib` and is pushed to a live Fuseki instance at real project scale. It is sourced from `gold_objects`' current-truth rows after bi-temporal resolution, rather than reading Silver independently.
- **The Jena rules engine runs live on Fuseki and matches the Python reference exactly on real Project A data:** 39 fluids, 162 flare-guard skips, 1,263 supply-tie-in skips, 39 relief valves. The oracle is absent from its input by construction.

The parity harness that proved this also found and closed a silent vocabulary drift between the projection and the rules file. It now stands as the gate for any future change to either side.

The RDF mapping handles the shapes real production data actually has: components with no resolved class, source-tool catch-all placeholder strings, and Equipment elements that would otherwise be asserted twice. Each is handled with an honest fallback and a report rather than a crash or a silent gap. The rule output is materialised into `graph:inferred` with PROV. Each run is identified by a hash of its exact inputs and rules, is written only after a passing parity check, and can be checked for freshness by the systemization run that reads it. What remains open is downstream:

- the systemization run consuming `graph:inferred`;
- a `graph:results` writer for the systems it computes.

The RDL/PLM resolution bridges that clear `rdlUriPending` are built and tested as mechanism, and the crosswalk data they read is real, growing incrementally as real project drawings are checked against the published PLM reference library. Today's real result on Project A: 187 RDS codes already covered, and a genuinely small, ranked, human-reviewable gap of 15 (3 boundary-forming) — two confirmed so far — with a project-owned `TEN_RDL` reference-data-extension path holding one real class (`SteamTrap`) for gaps the published library genuinely doesn't cover.

**Workstream 2's first deliverable, Off-Page Connectors (§4.6), is confirmed on real data end to end**, on the Gold and Silver sides alike:

- Each OPC is a dedicated `InformationObject`-typed node, with a `terminates` edge gated on the flow role.
- The mating pair is a Connection that reuses the existing reification mechanism unmodified.
- The Silver feed makes the path reachable: harvest enrichment, a per-OPC entity table and a fifth CDC grain.

On the real Project A load this gives 68 OPC nodes, 3 cross-sheet pairs and 62 open boundaries, with the conservation identity holding. The remainder of Workstream 2's originally scoped `precomm:` extension is still open: the grouping classes, the boundary role, node identity, and reconciling the standalone ontology document with the emitted vocabulary.
