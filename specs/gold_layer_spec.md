# Gold Layer — Design Specification & Implementation

**Data Product:** Automatic Pre-commissioning Systemization based on P&ID interoperability data
**Layer:** Gold (bi-temporal versioning · RDF/IDO projection · declarative classification · SPARQL serving) — the third medallion tier
**Target runtime:** Delta Lake on Apache Spark (bi-temporal tables) + Apache Jena / Fuseki (triplestore, SPARQL, rule engine) — PoC on local WSL, same as Bronze/Silver
**Status:** Draft v0.1 — **prototype IMPLEMENTED & UNIT-TESTED** (Spark-free core; see §8)
**Date:** 2026-09-03
**Companions:** `bronze_layer_spec.md`, `silver_layer_spec.md`, `medallion_rdf_ido_strategy_mapping.md` (§4, §5, §6, §10 steps 2–4), `data_specification.md` (§2.1a, §2.1b, §3.2, §3.4, note on §1 scope: *"Semantic-modeling concerns... are handled separately at the Gold layer"*), `algorithm_spec.md` (§7, §9), `systemization_spec.md` (§4–§5), `architecture_note.md` (§5).
**Grounding:** every rule ported below is to the actual `pidsys/walk.py` behaviour as already catalogued in `medallion_rdf_ido_strategy_mapping.md` §6 and `algorithm_spec.md` §7/§9 — this spec does not re-derive the rule content, it re-houses it, exactly as Silver re-housed reconstruction rather than re-deriving it.

---

> ## Implementation status — prototype (2026-09-03)
>
> **Built & unit-tested** as a standalone `gold/` package (49 tests, all green, zero third-party dependencies — PyPI is unreachable from the build environment, so `rdflib` / `pyspark` / `delta-spark` / `owlrl` could not be installed; see §8.1 for why this is a deliberate, documented substitution rather than a gap). The package implements:
>
> - **`temporal.py`** — object-grain bi-temporal versioning (`apply_delta`, `current_truth`, `diff_snapshots`), with the two axes named and kept independent per §3.
> - **`rdf_model.py`** — a minimal, dependency-free RDF quad store (terms, named-graph quads, Turtle/N-Quads serialisation) that mirrors `rdflib`'s `Dataset`/`ConjunctiveGraph` shape one-to-one, so graduating to `rdflib` is a re-plumb of this module's internals, not a redesign of anything above it.
> - **`vocab.py`** — the namespace and predicate vocabulary, with the IDO-alignment boundary named explicitly (§4.3).
> - **`rdf_mapper.py`** — the canonical-objects → RDF projection across the four named graphs (§4.4), reified Connections with mandatory `derived` and oriented `flowsTo` (§4.5).
> - **`oracle_guard.py`** — the oracle-quarantine structural invariant restated for the RDF layer (§5.3).
> - **`rules_reference.py`** + **`jena_rules/classification.rules`** — the declarative classification / local-directional rule layer (§5), as a Python reference implementation validated against fixtures reproducing `walk.py`'s own documented scenarios, and a one-to-one Apache Jena generic-rule-syntax counterpart for when a real Fuseki is stood up.
> - **`sparql_queries.py`** + **`fuseki_client.py`** — an example SPARQL query surface and a stdlib-only (no `requests`/`SPARQLWrapper`) Fuseki Graph Store Protocol / SPARQL Protocol client.
> - **`gold_job.py`** — the orchestration a Spark driver calls once per run, Spark-free and unit-tested as such (mirroring Silver's Stage C/D "pure core wrapped in a thin Spark job" split), plus an illustrative Spark-wrapper sketch (not executed — no Spark/Delta available in this sandbox, exactly as Bronze/Silver's own sketches are illustrative).
>
> **Updated 2026-09-03 — Silver Stage E (object-grain CDC) is now BUILT.** `silver_layer_spec.md`'s implementation-status box confirms `silver/cdc.py` + `silver/cdc_job.py` are built, unit-tested, and validated on real Project A + B data, with the acceptance test passing (a delete+recreate of an unchanged object yields zero `silver_cdc` deltas) — "Silver is complete... Next layer: Gold." This closes the one dependency §3.2/§8.2 originally flagged. `gold/silver_cdc.py` is the new **primary** bi-temporal path: `gold_job.build_bitemporal_tables_from_cdc` consumes `silver_cdc` rows directly via `apply_silver_cdc_events`, applying Stage E's own New/Modified/Deleted classification and its anchor-match identity (never a source UID) through `apply_delta` (§3.3) — no snapshot-diff heuristic, no re-derivation of whether something changed. The old `diff_snapshots` fallback (`gold_job.build_bitemporal_tables`) is kept, deprecated, for a Silver build predating Stage E or a snapshot-only backfill; 8 new tests (`tests/test_silver_cdc.py`) cover the CDC consumption path specifically, for **57 tests total, all green.**
>
> **Updated 2026-09-04 — a real-data run surfaced a genuine anchor-bucket collision, now handled.** Running the Gold cells against a real Project B Rev C -> Rev D `silver_cdc` batch (`medallion_concepts.ipynb`, §5) hit `apply_delta`'s "NEW delta for anchor already current" guard for real: `silver_cdc`'s `anchor` is a **bucket key** for components (`silver_layer_spec.md` §3.5 pairs same-class siblings on one segment within a shared `(segment, component_class)` bucket, not a per-instance string), so one batch can legitimately carry two simultaneous events for one anchor — the same non-uniqueness §3f already documents for `seg_tag`, one layer up. `gold/silver_cdc.py::apply_silver_cdc_events_tolerant` is the fix: it applies the same `apply_delta` calls but collects an anchor collision (or a `RetroactiveCorrection`) as a `CdcAnomaly` instead of aborting the batch — Stage D's own "observe and record, don't crash on dirty data" discipline, applied one layer up. `gold_job.build_bitemporal_tables_from_cdc` now uses the tolerant path by default and returns `(tables, anomalies)`; the strict `apply_silver_cdc_events` is unchanged and still tested, for callers that want one bad event to be a hard stop. 3 new tests, **60 tests total, all green.** See §3.2, §10 risk #8.
>
> **Not yet built:** a live Fuseki/Jena round-trip (no Fuseki instance is reachable from this environment — `fuseki_client.py`'s request-construction is tested, not a live call), the Spark wrapper's actual execution against real `silver_cdc` rows, and per-object-kind valid-time keyed off each object's own drawing when a run spans multiple drawings (the non-CDC fallback path takes one shared `valid_from` per run — see `gold_job._valid_from_for_kind`'s docstring; the CDC path (§3.2) already takes `drawing_revision_date` per-event, per-object, so this limitation is scoped to the deprecated fallback only). None of these gaps change the shapes this spec commits to; they are execution, not design.

---

## 0. Verdict up front

Gold is where the medallion strategy's two genuinely new, genuinely hard pieces meet: **bi-temporal history** and **the semantic serving/inference layer** (`medallion_rdf_ido_strategy_mapping.md` §7, "net-new" column). Neither has a `pidsys` predecessor to re-house — unlike Bronze (an ingestion wrapper) and Silver (a re-housing of validated reconstruction and rule content), Gold is where new design work is unavoidable. That makes the discipline different too: where Bronze and Silver's governing question is *"what already exists that we must not re-derive,"* Gold's is *"what does the strategy under-specify, and how do we decide it honestly rather than assume a term or a shape that doesn't exist."* `medallion_rdf_ido_strategy_mapping.md` §4 and §5 name exactly these gaps — a single `validFrom`/`validTo` pair collapsing two real time axes into one, and `ido:` terms (`FunctionalObject`, `validFrom`) that IDO does not actually ship — and this spec's job is to close them with documented decisions rather than inherited assumptions.

The one discipline that carries over unchanged from Silver: **Gold computes provenance and time, and projects meaning; it does not compute commissioning systems.** The global connected-fragment partition — the walk that actually decides which components form which system — stays exactly where it is validated: in Silver/Python (`pidsys/walk.py`), never reimplemented as Jena forward-chaining (medallion §6, point 5: SPARQL/Jena are awkward at "transitive closure that must stop at a node with a property," which is precisely what a boundary walk is). Gold's inference layer is deliberately narrower than "systemization in Jena" — it is the **local, node-local, declarative** half of the hybrid the strategy converges on: fluid self-ownership classification, boundary-role lookup, and the three directional guards, all read from `graph:refdata` and `graph:masterdata`, never from `graph:oracle`. Getting that boundary right is what keeps the Jena demonstration honest instead of pretending a forward-chainer is a graph-algorithms engine it is not.

---

## 1. Purpose & scope

### 1.1 What Gold is

Gold turns Silver's canonical, use-case-neutral plant model into **two things every rule package and every stakeholder-facing query reads**: a **bi-temporal history** of the object grain (component/segment/equipment/connection), queryable on either the engineering-reality axis or the audit axis; and a **semantic projection** of that same model into RDF/IDO across governed named graphs, serving SPARQL and hosting the declarative classification rules the medallion strategy assigns to Jena.

Concretely, Gold runs three sub-stages, each consuming Silver's tables (and Bronze's lineage) without re-parsing or re-reconstructing anything:

1. **Bi-temporal versioning** (§3) — object-grain New/Modified/Deleted deltas (from Silver's Stage E once it ships, or the snapshot-diff fallback this prototype implements today) become append-only Gold rows with two independent time axes, never physically deleted, "never delete, close the interval."
2. **RDF/IDO projection** (§4) — the current (and, on request, historical) canonical objects are mapped to triples across `graph:masterdata`, `graph:refdata`, `graph:oracle`, and `graph:results`, with reified Connections carrying `derived` and an oriented `flowsTo`, and domain classes honestly subclassed under IDO's foundational layer rather than asserting unconfirmed `ido:` domain terms.
3. **Declarative classification** (§5) — the node-local, directional rules a Jena engine (or, here, `rules_reference.py`) evaluates over `graph:refdata` + `graph:masterdata`: fluid self-ownership, boundary-role membership, the flare guard, the directional consumer guard, and relief-device attribution. The global partition itself is out of scope here — it stays in Silver.

Gold emits: a bi-temporal Delta table per object kind (mirroring Silver's table family, with the two time-axis columns added); a quad set across the four named graphs, servable from Fuseki or read locally; and the classification predicates (`selfOwningClass`, `skipAsConsumerSignal`, `protectedBy`) a downstream systemization run consumes as *additional*, not replacement, signal — the walk's own global partition remains the system of record until a validated Jena-only path exists (§9, phasing).

### 1.2 What Gold is NOT

| Concern | Belongs to | Why not Gold |
|---|---|---|
| Parsing, reconstruction, cross-document assembly | **Silver** | Gold consumes Silver's tables; it never touches a DOM or a centerline |
| Data-quality gating (the `silver_quality` ledger, `quality_gate` enum) | **Silver** | Gold trusts Silver's `quality_gate`; it does not re-run expectations |
| Object-grain CDC identity (anchor matching, the three hashes) | **Silver Stage E** | Gold *consumes* a New/Modified/Deleted classification; it does not decide what counts as the same engineering item across delete+recreate (`silver_layer_spec.md` §3.5). Until Stage E ships, Gold's own `diff_snapshots` degrades to a coarser full-diff — documented as a known precision loss, not a redesign of Stage E's job |
| The global connected-fragment partition (the boundary walk itself) | **Silver / `walk.py`** | Forward-chaining is the wrong tool for "reachable without crossing a boundary" (medallion §6 point 5); Gold's rule layer is local-only |
| Allocation tie-breaks and fragment-merge as a *running system* | **the systemization rule package**, consuming Gold's classification predicates as extra signal | Gold's `rules_reference.py` / Jena rules *express* the allocation functions (§5.4) as callable, data-driven logic; a systemization run is what actually invokes them over real fragments |
| Test Packages' cut rule, ITR/MC scoping, and every other rule package | **that rule package**, reading the same Gold/RDF surface with a different cut rule | Gold is the second consumer's entry point (`architecture_note.md` §6), not itself a second rule package |

### 1.3 Position in the medallion architecture

```
   Bronze                    Silver                              ┌──────── Gold (this spec) ────────┐
  raw XML,       ──▶  master data + reconstructed graph  ──▶      │ 1 bi-temporal versioning          │
  append-only         + quality verdicts + (eventually) CDC       │   (object grain, two time axes)   │
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

Gold is the strategy's phase-1 remaining steps and phase-2 (`medallion_rdf_ido_strategy_mapping.md` §10, steps 2–4): *"Bi-temporal Gold over the object-grain CDC... RDF/IDO projection of Gold via `rdflib` into Fuseki... Jena rules for classification + local/directional rules, validated against the oracle; keep the global partition in Python/LPG."*

---

## 2. Design decisions this spec resolves (the strategy's stated gaps)

`medallion_rdf_ido_strategy_mapping.md` §4 and §9 (risks #4, #7) name four things the original strategy under-specified. Each is resolved here, not deferred further:

1. **Two time axes, named and kept independent** (§3.1) — not a single `validFrom`/`validTo` pair.
2. **`pidsys:validFrom` / `pidsys:validTo`, not `ido:validFrom` / `ido:validTo`** (§4.3, `vocab.py`) — IDO ships no such datatype properties; asserting them would be an over-claim the same way `ido:FunctionalObject` is (medallion §9 risk #4).
3. **Domain classes subclass an IDO *foundational* class and carry an explicit `rdlUriPending` marker** (§4.3) rather than asserting a domain term IDO does not define, with resolution to the POSC Caesar RDL left as a named, tracked follow-up (the sibling IDO prototype's PIM workflow already does this tag→RDL-URI resolution and is the pattern to reuse, not reinvent).
4. **Grain and interval discipline are explicit and tested** (§3.2–§3.3) — object grain aligned with Silver's CDC grain, "never delete, close the interval," and a named, honest limitation (no valid-time interval *splitting* for retroactive corrections — flagged for manual reconciliation rather than silently guessed at).

---

## 3. Bi-temporal versioning

### 3.1 The two axes

| Axis | Meaning | Source | Predicate (never `ido:`) |
|---|---|---|---|
| **Valid time** | When the plant configuration a record describes was *true* — engineering reality | Bronze `drawing_revision_date` (format-normalised; Bronze stores it verbatim per `bronze_layer_spec.md` §6, one column per format) | `pidsys:validFrom` / `pidsys:validTo` |
| **Transaction time** | When the platform *learned* the fact — audit / system time | Bronze `ingested_at`, carried through Silver's lineage columns | `pidsys:transactionFrom` / `pidsys:transactionTo` |

These differ constantly in EPC reality (`medallion_rdf_ido_strategy_mapping.md` §4b): a revision issued three weeks ago but ingested today has valid-time three weeks back and transaction-time now. Gold carries **both**, independently, on every object-grain row (`gold/temporal.py`, `GoldRow`).

### 3.2 Grain and identity

Object grain, aligned with the CDC grain Silver's Stage E is built around (`silver_layer_spec.md` §3.5): component / segment / equipment / connection, keyed on a stable **anchor id** — never the volatile source UID. **Stage E is now built and this is live**: `gold/silver_cdc.py::apply_silver_cdc_events` consumes `silver_cdc` rows directly, using Stage E's own anchor-match identity (`anchor_id`, derived from `anchor_hash`) as the Gold row's anchor — the same identity whose acceptance test (`silver_layer_spec.md` §3.5) proves a delete+recreate of an unchanged item produces zero deltas, so that guarantee now reaches Gold unmodified rather than being re-earned here. `gold/temporal.py::diff_snapshots` remains as the pre-Stage-E fallback (its own anchor is whatever id a Silver *snapshot* row carries, coarser than Stage E's true anchor-match), used only when `silver_cdc` is unavailable.

**A real-data wrinkle, not a design change:** `silver_cdc`'s `anchor` is a *bucket* key for components — Stage E's own within-bucket member pairing (`silver_layer_spec.md` §3.5) can legitimately place two same-class siblings on one segment under the identical anchor string, so a single batch can carry two simultaneous New/Modified/Deleted events for one `anchor_id`. `apply_delta`'s one-current-row-per-anchor contract is unchanged (§3.3) — what changed is how a caller applies a *batch* of events against it: `apply_silver_cdc_events_tolerant` (rather than the strict `apply_silver_cdc_events`) catches each such collision as a `CdcAnomaly` and keeps applying the rest of the batch, the same "flag, don't silently fail" posture §3.3 already uses for a retroactive correction. `gold_job.build_bitemporal_tables_from_cdc` uses the tolerant path and returns `(tables, anomalies)`.

### 3.3 Interval discipline — "never delete, close the interval"

Implemented in `gold/temporal.py::apply_delta`, three cases:

- **New** — open a row: `valid_from = X, valid_to = None, tx_from = now, tx_to = None`.
- **Modified**, two sub-cases distinguished by comparing the new fact's `valid_from` to the current row's:
  - **Ordinary forward supersession** (`new.valid_from > current.valid_from`, the overwhelmingly common case — a later revision naturally supersedes an earlier one): close **only** `valid_to = new.valid_from` on the old row. Its transaction interval is **not** retroactively closed — the platform is not correcting anything, it is recording that a later reality began; closing `tx_to` here would make every past interval look "corrected" on every subsequent revision, which is not what happened.
  - **Correction** (`new.valid_from == current.valid_from`, same engineering-validity period but different content — a re-ingest that corrects what was recorded): close **only** `tx_to = now` on the old row, leaving `valid_to` untouched; open a new row starting at the *same* `valid_from`. This is the one case where the platform genuinely revises its own record of history.
  - **Retroactive** (`new.valid_from < current.valid_from`) — would require splitting an *earlier* interval the row may not even represent. **Not implemented**: raises `RetroactiveCorrection` so it is routed to a person rather than silently asserting an interval shape nobody has validated (the same "flag, don't silently resolve" discipline `algorithm_spec.md` §11 uses everywhere else).
- **Deleted** — closes **both** axes (`valid_to = tx_to = event time`): the object no longer exists, so it is no longer held as current on either axis. No new row opens.

**Two structural safeguards, both unit-tested:**

- **Identical content is a no-op** (`GoldRow.content_key`), so a byte-identical re-ingest never bumps a version.
- **An oracle-only change never triggers a version bump** — `content_key` excludes `src_turnover`/`src_subsystem` from the change-detection fingerprint by construction, restating Silver's compute-only firewall (`silver_layer_spec.md` §5) at the Gold layer: a source-turnover correction must never look like an engineering Modify.

### 3.4 Query surface: `current_truth`

`gold/temporal.py::current_truth(rows, as_of_valid=None, as_of_tx=None)` implements the strategy's stated query shapes (§4b): both args `None` gives *"transaction_time = now ∧ valid_time = latest"*; either given alone answers *"what was true at Rev B"* or *"what did we believe as of transaction time T"* respectively, with the **unspecified** axis left unconstrained rather than silently defaulted to "latest" — defaulting it would wrongly exclude a historical-but-since-closed valid-time row from a pure transaction-time query merely because a later revision has since closed its valid interval (the module docstring works the example through in full; `tests/test_temporal.py::TestCurrentTruth` locks the behaviour down).

### 3.5 A named, honest limitation

Full SQL:2011-style bi-temporal tables support **splitting** an already-recorded valid-time interval when a retroactive correction arrives mid-interval. This prototype does not implement interval splitting (§3.3, the `RetroactiveCorrection` path) — it is a materially harder feature (multiple new rows replacing one, with careful boundary arithmetic) that the specs' own precedent (flag rather than silently resolve) argues should be a deliberate, reviewed addition rather than a first-cut guess. It is named here as the one piece of "real" bi-temporal semantics this spec defers, not hidden inside a passing test.

---

## 4. RDF/IDO projection

### 4.1 Named-graph layout (unchanged from the strategy, now implemented)

| Graph | Contents | Who may write it | Who may read it |
|---|---|---|---|
| `graph:masterdata` | Reconstructed plant: components, segments, equipment, nozzles, reified connections, flow direction | The Gold projection job only | Everything — the shared plant model |
| `graph:refdata` | Fluid catalogue, boundary role sets, tagging convention, UnitSUP — the "rules as data" surface | The reference-data load job only | Everything, especially the classification rules (§5) |
| `graph:oracle` | `src_turnover` / `src_subsystem` — the validation answer key | The Gold projection job only | **Only** the oracle cross-check query (§6); never a rule, never a partition, never `graph:results` |
| `graph:results` | Computed systems, their members, boundaries, and the rule/run that produced them (PROV) | The systemization rule package, after it runs | Reporting, stakeholder queries, the oracle cross-check |

### 4.2 Reified Connections (correctness note 2)

Every Silver `silver_connections` row becomes a `pidsys:Connection` node (`gold/rdf_mapper.py::map_connection`) carrying `fromObject`/`toObject`/`fromNode`/`toNode`/`connType` and a **mandatory** `derived` boolean — mapping a connection with no `derived` key is a hard `ValueError`, restating Silver's `derived_flagged` structural invariant (`silver_layer_spec.md` §3.4) at the projection boundary instead of letting a bare `isConnectedTo` triple silently assert inferred topology as source truth. `isConnectedTo` is emitted symmetrically and unconditionally (both directions); `flowsTo` is emitted **only** when `flow_sense` orients it — `forward`/`reverse`/`both` each produce the appropriate directed triple(s), `none` produces none at all, never a guessed direction.

### 4.3 Honest IDO alignment (resolving strategy risk #4)

`vocab.py` draws the line the strategy names but does not enforce: `IDO_PHYSICAL_OBJECT` (a real, foundational IDO class) is the **only** IDO term this project asserts. Every domain class (`GateValve`, `PipingSegment`, `Equipment`, ...) is declared `rdfs:subClassOf` `pidsys:PipingComponent`, itself `rdfs:subClassOf ido:PhysicalObject` — never a bare, invented `ido:GateValve` or `ido:FunctionalObject`. Where a real RDL (POSC Caesar / ISO 15926-4) URI has not yet been resolved for a class, `rdf_mapper.py` asserts `pidsys:rdlUriPending true` on it rather than fabricating one — an explicit, queryable "this class needs resolution" marker instead of a silent gap (`tests/test_rdf_mapper.py::test_rdl_uri_pending_when_not_resolved` locks this down). Validity timestamps are `pidsys:validFrom`/`pidsys:validTo`/`pidsys:transactionFrom`/`pidsys:transactionTo` — project predicates, never `ido:validFrom`, because IDO does not ship one (medallion §4a).

### 4.4 The rest of the canonical-schema mapping

`Document → StartUpPackage → ProcessUnit → PipelineSystem → Subline → PipingSegment → PipingComponent` maps via `pidsys:partOf`/`pidsys:hasPart` containment; `ProcessUnit → StartUpPackage` via `pidsys:hasStartUpPackage` (the architecture note §5's named example of a classification IDO expresses declaratively); `Equipment`/`Nozzle` as physical objects reachable from a segment's connectivity through the nozzle wiring Silver already resolved (`silver_layer_spec.md` §3.1, the ghost-filter). The Fluid catalogue and Boundary role sets load into `graph:refdata` as SKOS-shaped concepts (`rdf_mapper.map_fluid_catalogue`, `map_boundary_sets`) — this **is** the rules-as-data surface `rules_reference.py` (§5) reads.

### 4.5 The oracle firewall, restated at the RDF layer

`gold/oracle_guard.py::assert_oracle_confined` scans every quad and raises `OracleLeakage` if `srcTurnoverSystem`/`srcSubsystem` appears in any graph other than `graph:oracle` — `gold_job.build_rdf_dataset` calls it unconditionally on every run, so a mapping bug that routes an oracle field into `graph:masterdata` is a hard failure, not a silent circularity in the ~97% figure. `assert_rule_engine_did_not_read_oracle` is the companion check every `rules_reference.py` function threads through, asserting the *set of graphs a rule read* never includes `graph:oracle` — this is what makes "the oracle is a validation answer key only" a structural property of the code path, not a comment.

---

## 5. Declarative classification — the Jena/local half of the hybrid

### 5.1 Scope, precisely

Per the hybrid design `medallion_rdf_ido_strategy_mapping.md` §6 converges on: **local, node-local rules only.** Given a component and its immediate neighbours (from `graph:masterdata`) and the reference catalogues (from `graph:refdata`), classify and guard — never partition the whole graph into fragments. The functions below are individually testable against a single component and its neighbourhood; none of them iterates the whole plant.

### 5.2 Fluid self-ownership [`data_specification.md` §3.4; `algorithm_spec.md` §7.1a/§7.2]

`classify_fluid_category(fluid_code, catalogue)` returns `flare | steam_condensate | process | utility`, reading `graph:refdata`'s Category/Subcategory exactly as the catalogue defines them (Category=`Flare` → flare; Category=`Utility` with Subcategory containing `Steam` or `Condensate` → steam_condensate; Category=`Process` → process; else utility). `is_self_owning` is `True` for flare and steam_condensate — these must be decided **before** any consumer trace runs, or a naive engine walks a steam header into whatever it terminates at, "the single highest-value grouping fix" the source PoC validated against ~97% source agreement.

### 5.3 Boundary roles [`data_specification.md` §3.2]

`load_boundary_roles` reads `graph:refdata`'s four role sets (isolation / positive / relief / trap) exactly as `refdata.load_boundary_sets` does today — `CheckValve` is boundary-forming in **no** role set, by construction, never a special-cased exclusion in the rule body. `SafetyValveOrFitting` and `Reliefdevices` both resolve to `relief` (the export's two class names for the same role).

### 5.4 The three directional guards [`pidsys/walk.py`; `algorithm_spec.md` §7.2, §7.2a, §7.4a]

All three read `pidsys:flowsTo` (§4.2) and are proven, in `tests/test_rules_reference.py`, against a fixture reproducing `walk.py`'s own documented scenarios verbatim (nitrogen teeing into a process header; a process fragment discharging into flare; a relief valve protecting an upstream process line):

- **`flare_guard(ds, fragment_component, neighbour, catalogue)`** — `True` (skip as consumer) iff the neighbour's fluid classifies as `flare` **and** flow runs strictly one-way *into* it. Falls through (returns `False`) whenever direction is unknown or reversed — a safe default, never a wrong attach.
- **`directional_consumer_guard(ds, fragment_component, neighbour)`** — generalises the flare guard to every fluid: `True` (skip) iff flow runs strictly one-way *from the neighbour into the fragment* (a supply tying in), never the reverse case.
- **`relief_attribution(ds, relief_component)`** — returns the **protected** neighbour (flow strictly *into* the valve), or `None` when direction is not identifiable — this function only ever *adds* a correct assignment, never forces a wrong one, exactly as `algorithm_spec.md` §7.4a specifies.

### 5.5 Allocation & identity rules [`algorithm_spec.md` §9.1, §9.3, §9.5]

Expressed as small, data-driven functions rather than inline conditionals, so a systemization run calls them the same way a Jena rule body would invoke a declared predicate: `allocate_boundary_valve_or_blind(priority_a, priority_b)` (higher-priority/earlier-commissioned side wins), `allocate_steam_trap()` (always steam side), `allocate_sampling_connection()` (always upstream), `allocate_interface_valve("flare"|"drain")`, `allocate_vessel_column_instrument()` (equipment system), and `fragment_merge_key(system_kind, sup, fluid=..., anchor_equipment=...)` — `(SUP, fluid)` for utilities, `(SUP, anchor-equipment)` for process, the stable identity that keeps a re-run's system count from fragmenting into dozens of pieces (`algorithm_spec.md` §9.5, §10).

### 5.6 The portable Jena artifact

`gold/jena_rules/classification.rules` is the one-to-one Apache Jena generic-rule-syntax counterpart of §5.2–§5.4 (fluid classification, both guards, relief attribution) — the artifact a real Fuseki/Jena deployment loads. It is not executed in this sandbox (no JVM/Jena available), so it is validated by **inspection and by construction** against the same rule content `rules_reference.py` implements and *that* module's tests verify; `medallion_rdf_ido_strategy_mapping.md` §10 step 4 names diffing the two on any change as the ongoing discipline once a real Jena engine is stood up.

---

## 6. SPARQL query surface

`gold/sparql_queries.py::EXAMPLE_QUERIES` are real SPARQL 1.1, meant for `fuseki_client.sparql_query` once Fuseki is running (or `rdflib.Graph.query` on a graduation to that library):

- **`systems_and_members`** — every computed system's member count, from `graph:results` alone.
- **`derived_connections`** — every reified Connection flagged `derived`, the provenance query reifying the edge was for.
- **`oracle_cross_check`** — the **one** query permitted to join `graph:oracle` against `graph:results`, and it is read-only reporting for a person to review, never an input to another query or a rule.
- **`boundary_roles`** — the rules-as-data surface itself, queryable directly: "onboarding a project is editing this sheet, not the rule," made literal as a SPARQL result set.

`run_local_pattern` is a plain triple-pattern matcher over the in-memory `Dataset`, for answering the same questions without a running Fuseki — used throughout the test suite, not a production query path.

---

## 7. Gold table family (bi-temporal Delta tables)

One table per Silver object kind, each Silver column carried through plus the four temporal columns:

| Table | Grain | New columns beyond the matching `silver_*` table |
|---|---|---|
| `gold_components` | one Piping Component version | `valid_from`, `valid_to`, `transaction_from`, `transaction_to` |
| `gold_segments` | one Piping Segment version | *(same four)* — `src_turnover`/`src_subsystem` still ride along as quarantined lineage, per §4.5, never a compute input here either |
| `gold_equipment` | one real Equipment version | *(same four)* |
| `gold_connections` | one undirected connection version | *(same four)* |

Design rules, carried over from Silver's own table-family discipline (`silver_layer_spec.md` §4): one table per kind across both source formats; append-only, "never delete, close the interval"; the oracle columns physically isolated to `gold_segments` and never copied elsewhere.

---

## 8. Implementation notes

### 8.1 Why this prototype has zero third-party dependencies

PyPI (`pypi.org`, `files.pythonhosted.org`) returned HTTP 403 from this build environment — `rdflib`, `pyspark`, `delta-spark`, and `owlrl` could not be installed. Rather than leave the RDF/IDO projection and the classification rules as unexecuted pseudocode, `gold/rdf_model.py` hand-rolls the minimal quad-store surface needed (typed terms, named-graph quads, Turtle/N-Quads output), documented as mirroring `rdflib`'s `Dataset` API shape exactly so a graduation to `rdflib` — the moment PyPI is reachable in the real target environment — is a re-plumb of one module's internals, not a redesign of `rdf_mapper.py`, `rules_reference.py`, or `sparql_queries.py`. This is the same choice Silver already made and documented for the same reason: `silver_layer_spec.md` §3.4 chose a native, Spark-free expectation suite over the `great_expectations` library specifically to stay dependency-light and unit-testable, with the heavier library named as a later, optional layer over the same ledger. This spec makes the identical trade for the identical reason, one layer up.

`fuseki_client.py` is stdlib-only (`urllib`) by the same logic, and is genuinely production-usable against a real Fuseki instance without any dependency at all — it is not a stand-in the way `rdf_model.py` is, only untested end-to-end here for lack of a reachable Fuseki.

### 8.2 What is genuinely deferred to a production build

- **A live Fuseki/Jena round-trip.** `fuseki_client.py`'s request construction is unit-tested; an actual `docker run` Fuseki plus a live Jena rules engine load is the next validation step once such an instance is reachable, exactly as the sibling IDO prototype already runs Fuseki in Docker (`architecture_note.md` §5).
- **Per-object valid-time keyed off each object's own drawing.** `gold_job._valid_from_for_kind` takes one shared `valid_from` per run (the run's latest known drawing revision date across the whole batch) rather than joining each object to its own `drawing_number`'s revision — correct for a single-drawing batch (this prototype's fixtures), and named in the docstring as the one-line change (`drawing_lineage[obj["drawing_number"]]["drawing_revision_date"]`) a multi-drawing production run makes.
- ~~Consuming Silver's real Stage E CDC classification once it ships~~ — **done**: `gold/silver_cdc.py` + `gold_job.build_bitemporal_tables_from_cdc` (§3.2); `diff_snapshots` is now the documented fallback, not the primary path.
- **Interval splitting for retroactive corrections** (§3.5) — named, not hidden; still deferred.

None of these gaps require redesigning a table shape, a predicate, or a named graph this spec has already committed to; each is additive.

---

## 9. Suggested phasing (continuing `medallion_rdf_ido_strategy_mapping.md` §10)

1. *(Already specified/built — Bronze, Silver, including Stage E.)*
2. **Bi-temporal Gold over the object-grain CDC** — this spec's §3, now driven directly by Silver's `silver_cdc` table (`gold/silver_cdc.py`) rather than the snapshot-diff fallback, since Stage E has shipped.
3. **RDF/IDO projection of Gold** — this spec's §4, buildable today with the dependency-free quad store, upgrading to `rdflib` + a real Fuseki the moment either is reachable.
4. **Jena rules for classification + local/directional rules, validated against the oracle** — this spec's §5; "validated against the oracle" here means run `rules_reference.py`'s classification against real fixtures and cross-check the resulting `selfOwningClass`/`skipAsConsumerSignal`/`protectedBy` predicates against `walk.py`'s own output on the same drawings, the same discipline the ~97% figure was earned with — never against `graph:oracle` directly (§4.5).
5. **Second consumer (Test Packages)** reuses this same Gold/RDF surface with a different cut rule (class/rating breaks + test-containment) — `architecture_note.md` §6's earn-your-data-product-status milestone, and the reason Gold's schema (§7) and named graphs (§4.1) are kept use-case-neutral rather than systemization-specific.

---

## 10. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | **A Jena/forward-chaining engine is asked to do the global partition**, regressing the "SPARQL struggles with connected components" trap the architecture note names by name. | `rules_reference.py` and `classification.rules` are scoped, by construction and by docstring, to local/node-local rules only; the global walk stays in Silver/Python (§5.1, §9 step 4). |
| 2 | **An unconfirmed IDO domain term is asserted** (`ido:FunctionalObject`, `ido:validFrom`), quietly over-claiming standards alignment. | `vocab.py` names the one real IDO term used (`IDO_PHYSICAL_OBJECT`) and routes everything else through `pidsys:` predicates and an explicit `rdlUriPending` marker (§4.3). |
| 3 | **Oracle leakage into a rule or a served graph**, making the ~97% cross-check circular. | `assert_oracle_confined` (hard-fail, run unconditionally) + `assert_rule_engine_did_not_read_oracle` (threaded through every classification function) — §4.5, unit-tested. |
| 4 | **A bare, unreified connection or a missing `derived` flag** lets inferred topology masquerade as source truth in the semantic layer. | `map_connection` raises on a missing `derived` key; every connection is a reified node, never a bare triple (§4.2). |
| 5 | **Bi-temporal collapse to one axis**, or a retroactive correction silently mis-modelled. | Two independent axes throughout `temporal.py`; retroactive corrections raise rather than guess (§3.3, §3.5). |
| 6 | **An oracle-only change is mistaken for an engineering Modify**, corrupting the version history's meaning. | `GoldRow.content_key` excludes oracle fields from the change-detection fingerprint by construction (§3.3), unit-tested. |
| 7 | *(Resolved 2026-09-03.)* Silver Stage E's absence was treated as a blocker rather than an upgrade path. | Stage E is now built and consumed directly (`gold/silver_cdc.py`, §3.2); `diff_snapshots` remains only as an explicit, documented fallback for a pre-Stage-E Silver build. |
| 8 | *(Resolved 2026-09-04.)* A real `silver_cdc` batch's component `anchor` is a bucket key, not a per-instance id — a strict one-event-per-anchor application can abort an entire Gold run on real, legitimate multi-sibling data (found running against real Project B data, not a hypothetical). | `apply_silver_cdc_events_tolerant` (§3.2) collects each collision as a `CdcAnomaly` and keeps applying the rest of the batch; `gold_job.build_bitemporal_tables_from_cdc` uses it by default. |

---

## 11. One-paragraph summary

Gold is where the medallion strategy's two genuinely new pieces — bi-temporal history and the RDF/IDO semantic layer — get built, and where its two under-specified gaps get closed rather than inherited: two independent time axes (valid vs. transaction), not one collapsed pair, with "never delete, close the interval" implemented down to the ordinary-supersession-vs-correction distinction and a named limit (no interval splitting) rather than a silent guess; and an RDF projection that is honest about IDO — one real foundational class asserted, domain terms subclassed under it with an explicit pending-resolution marker rather than an invented `ido:FunctionalObject`, reified Connections that never let inferred topology pass as source truth, and an oracle firewall enforced as a hard-fail structural invariant, not a convention. The inference layer is deliberately the *local* half of the hybrid the strategy converges on — fluid self-ownership, boundary roles, and the three directional guards, faithfully ported from `walk.py` and validated against fixtures that reproduce its own documented scenarios — while the global connected-fragment partition stays exactly where it is proven, in Silver's Python walk. Every piece of this is implemented and unit-tested today, with zero third-party dependencies (PyPI was unreachable in the build environment, so a native quad store stands in for `rdflib` and a stdlib HTTP client for a Fuseki SDK, both documented as direct, shape-preserving swap-in points) — the same "prefer a small native implementation, document the swap-in" discipline Silver's own quality gate already established.