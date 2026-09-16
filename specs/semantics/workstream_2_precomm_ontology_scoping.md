# Workstream 2 — the `pidsys:` structural extension ontology

**Status (2026-09-16):** Scoping note + ontology `pidsys_extension.ttl` (v0.2.0-draft) + reference-data stub `document_types_refdata.ttl`. All IDO anchors **audited directly against IDO/LIS-14 v4.2 (FDIS)** — the file `LIS-14-for-FDIS.ttl` (owl:versionIRI `.../core/4.2`, "FDIS proposal submitted April 2026", modified 2026-04-10), the pinned import layer. **Reasoner-verified:** HermiT runs over the assembled ontology + v4.2 core and reports it **consistent, with no unsatisfiable class** (confirmed by David, 2026-09-16). The core semantic gate is cleared; remaining work is code reconciliation and reference-data population, not ontology correctness (see §7).

**Relates to:** `ido_semantic_mapping_spec.md` §6.1/§6.3 (defines Workstream 2), `claude_off_page_connector_representation.md` (Connection/OffPageConnector design), `claude_gold_layer_spec.md` §4.3–§4.4 (the running `vocab.py`/`rdf_mapper.py`/`walk.py` this must reconcile with), `claude_ten_rdl_extension_design.md` (Workstream 1.5 equipment extension — a *different* ontology).

---

## 1. What this is

Workstream 2 is the small, bounded structural ontology that gives the pre-commissioning systemization data product the vocabulary IDO/LIS-14 does not natively provide. Until this work it existed only as `gold/vocab.py` constants and inline `ds.add(...)` triples in `gold/rdf_mapper.py`; `pidsys_extension.ttl` is the first browsable, versioned OWL document for it.

It is distinct from **TEN_RDL** (Workstream 1.5, `http://data.ten.com/rdl/`), which fills gaps in the *equipment/component* vocabulary (real physical things like `SteamTrap`). This extension supplies *structural/relational* terms: a reified connection, flow direction, connection-node and nozzle identity, the grouping/output system classes, the off-page connector, and the boundary role.

**Namespace / publishing IRI:** `pidsys:` → `http://data.ten.com/ont/pidsys#`, mirroring TEN_RDL's T.EN-owned pattern. (The spec drafts the prefix as `precomm:`; the running code and live Fuseki data use `pidsys:` for the identical vocabulary, so `pidsys:` is authoritative and `ido_semantic_mapping_spec.md` §6.1/§6.3 should be reconciled to it.)

**Import pin:** `owl:imports <http://rds.posccaesar.org/ontology/lis14/ont/core>`, prefix `ido:` → `<http://rds.posccaesar.org/ontology/lis14/rdl/>`, resolved by the local **v4.2 FDIS** file.

**Governing principle:** anchor to a real IDO term wherever one is verified to exist; mint a `pidsys:` term only for a genuine gap.

---

## 2. Class model (as pinned to v4.2)

Every anchor below was confirmed in v4.2.

| `pidsys:` class | IDO anchor | Why |
|---|---|---|
| `PipingComponent` | `ido:PhysicalArtefact` | A made physical item (valve, reducer, flange). In v4.2 `PhysicalArtefact ⊑ PhysicalObject` **directly**. |
| `Equipment` | `ido:PhysicalArtefact` | A vessel, pump, exchanger — a manufactured physical object. Separate physical hierarchy from PipingComponent. |
| `UnclassifiedComponent` | `⊑ pidsys:PipingComponent` | Honest "no real classification available" node; never a fabricated guess. |
| `Nozzle` | `ido:Feature` | A connection point *on* equipment (a flanged opening), `featureOf` its parent Equipment. On the connectivity graph because `Feature ⊑ PhysicalObject`. |
| `ConnectionNode` | `ido:Feature` | A connection point on a component; carries per-node nominal diameter; what a Connection's `fromNode`/`toNode` point at. |
| `PipingSegment` | `ido:System` | A **functional grouping** of components sharing diameter/class/insulation — a slot, not steel. |
| `Line` (= **Pipeline System**, §2.2) | `ido:System` | Functional grouping one level above segment. "Pipeline System" is the master-data name for this class — no separate class is minted. |
| `Subline` (Sub Piping System, §2.2a) | `ido:System` | Project-B grouping level between Line and Segment. |
| `ProcessUnit` | `ido:System` | Tag-decoded grouping level (§2.1a). |
| `StartUpPackage` | `ido:System` | Reference-data grouping root (§2.1b). |
| `InstrumentationLoop` | `ido:System` | Instrument-function grouping (§2.8). |
| `CommissioningSystem` | `ido:System` | The computed systemization output; `computed` provenance, lands in `graph:results`. |
| `BoundaryFormingComponent` | `⊑ pidsys:PipingComponent` | A component whose resolved class is in a boundary-role set (role sets are SKOS refdata, not T-Box). |
| `Connection` | `ido:InformationObject` | The reified edge — an information artifact *about* two objects. |
| `OffPageConnector` | `ido:InformationObject` | A drawing-continuation symbol and a Connection *endpoint*; sibling of Connection. |
| `Document` | `ido:InformationObject` | The P&ID / engineering document; root of the containment chain. |

**Physical / functional / information split (the spine of the model):**
- Made things are `PhysicalArtefact`; parts/points on them are `Feature`; both are `PhysicalObject`, so both sit on the `ido:connectedTo` graph.
- Groupings are `System` (functional). A segment is the functional slot; the physical pipe pieces that realise it are separate `InanimatePhysicalObject` individuals.
- Documents and reified edges are `InformationObject`, which v4.2 declares disjoint from `PhysicalObject` — so a Connection or OPC *cannot* be physical (the ontology enforces this).

**Containment chain:** Document → Line (Pipeline System) → Subline (project B) → PipingSegment → PipingComponent. Each grouping level is `ido:System`; membership uses `ido:functionalPartOf` / `ido:hasFunctionalPart`.

---

## 3. Property model

| `pidsys:` property | Shape | Notes |
|---|---|---|
| `flowsTo` | Domain/Range `ido:PhysicalObject` | Directed flow — the term IDO lacks. Standalone, **not** a subproperty of the symmetric `connectedTo` (which would destroy direction). Emitted only when flow sense orients the edge; never guessed. |
| `hasMember` | `⊑ ido:hasFunctionalPart`, domain `CommissioningSystem`, **no explicit range** | Inherits Range `FunctionalObject` from the parent (v4.2: `hasFunctionalPart` Domain System, Range FunctionalObject). No local range = clean inheritance if the core changes. |
| `hasBoundary` | `CommissioningSystem` → `PipingComponent` | Boundary-forming components delimiting a computed system. |
| `hasBoundaryRole` | `PipingComponent` → `skos:Concept` | Links to the boundary-role concept (isolation/positive/relief/trap) held as SKOS refdata — role vocabulary stays data. |
| `hasDocumentType` | `Document` → `skos:Concept` | Document type held as governed SKOS refdata (see §5). |
| `hasStartUpPackage` | `ProcessUnit` → `StartUpPackage` | |
| `terminates` | `OffPageConnector` → `PipingSegment` | Direct edge (not reified through Connection). |
| `connectionType`, `derived` | on `Connection` | Type (Process/Nozzle/Signal/Off-Page); `derived` is a mandatory provenance boolean. |
| `fromObject`/`toObject` | `Connection` → `owl:unionOf(ido:PhysicalObject, pidsys:OffPageConnector)` | The two real endpoint types; deliberately not widened to `owl:Thing`. |
| `fromNode`/`toNode` | `Connection` → `ConnectionNode` | |
| `rdlUriPending`, `rdlMatchType` | resolution markers | Honest placeholders on classes not yet resolved to a real RDL/PLM URI (exact\|close\|label\|pending). |

**Connectivity is native.** The projection emits `ido:connectedTo` (symmetric, `PhysicalObject`-domained) and `ido:directlyConnectedTo` directly — no minted connectivity property. `connectedTo` has no directional companion in IDO, which is why `flowsTo` must be standalone.

**Membership reaches physical components through function, not identity.** A `CommissioningSystem hasMember` a component's *functional* individual, reached via the function-realization pattern (`hasFunction some Function`; that `Function realizedIn` an `Activity` the physical object participates in) — never the physical component directly, which would wrongly infer it a functional individual. This requires `rdf_mapper.py` to mint functional individuals and the `hasFunction`/`realizedIn` edges; **not done in the current code** (see §6).

---

## 4. Scope: what is deliberately out

**Instrument (§2.5), Actuator (§2.6), Instrument Function (§2.7), and Signal (§2.10) are intentionally out** of Workstream 2 and the whole pipeline for now. These are decisions, not gaps — do not "helpfully" add them. Revisit if/when the instrumentation scope is opened. (Instrument Function and Signal are the two that would need a functional-vs-edge modelling call; deferred with the rest of instrumentation.)

The rule layer is also out of this ontology: populating `CommissioningSystem`, applying boundary roles, computing systemization — all live in SHACL/SPARQL/Jena over `graph:refdata`. This file declares the classes and properties; it does not populate them.

---

## 5. Document types → a governed Reference Data Library (not T-Box)

Document **type** (P&ID, isometric, datasheet, GA, cause-&-effect, …) is reference data, not ontology: a project generates a large, evolving, client-varying set, so modelling each as an `owl:Class` would bake a mutable list into the T-Box — the anti-pattern already avoided for boundary roles, fluids, and component classes.

- **`pidsys:hasDocumentType`** (`Document` → `skos:Concept`) is declared in the ontology — the property is structural, the values are data.
- **`document_types_refdata.ttl`** is a SKOS ConceptScheme stub (P&ID, isometric, datasheet, GA, C&E) that **loads into `graph:refdata`, never merged into the T-Box.** Populated per project.
- **Crosswalk pending.** Each concept should carry `skos:exactMatch`/`closeMatch` to an external standard with `pidsys:rdlMatchType` recording confidence (exact\|close\|label\|pending); the stub marks all `pending`. **Open question:** is **CFIHOS** the crosswalk target, or does T.EN have its own document-type master to align to first? That decides what the real URIs resolve to.

---

## 6. The draft is ahead of the code

`pidsys_extension.ttl` states the correct target; the running `vocab.py`/`rdf_mapper.py`/`walk.py` do not yet match it on several points. This is the intended direction (ontology leads, code catches up), but the gaps are real and load-bearing:

- **Membership (function-realization).** `rdf_mapper.py` currently does not mint functional individuals or `hasFunction`/`realizedIn` edges. If it cannot yet, use direct physical `hasBoundary`-style edges rather than relaxing `hasMember`.
- **Functional-only segments.** The model asserts `PipingSegment`/`Line ⊑ System` only, off the physical connectivity graph. This is sound **because `walk.py` traverses adjacency at the component/piece level, not segment-to-segment.** If the walk ever needed segment-to-segment edges, a `pidsys:` functional-connectivity property would be required (`connectedTo` is `PhysicalObject`-domained). This dependency must hold.
- **Anchors.** The code (`vocab.py`) asserts only `ido:PhysicalObject`; the ontology uses `ido:System`, `ido:InformationObject`, `ido:PhysicalArtefact`, `ido:Feature`, `ido:connectedTo`/`directlyConnectedTo`, and the `hasFunctionalPart`/`hasFunction` membership pattern.

---

## 7. Remaining work, in order

1. **~~Reasoner run~~ — DONE (HermiT, 2026-09-16): ontology CONSISTENT, no unsatisfiable class.** Loaded `pidsys_extension.ttl` with the v4.2 core; HermiT reports consistency and every class satisfiable — `PipingSegment`/`Line` stay satisfiable (confirming v4.2 asserts no `System`|`PhysicalObject` disjointness), the `owl:unionOf` endpoint range and the `InformationObject`/`Feature`/`PhysicalArtefact` anchors all cohere. **Optional follow-up:** eyeball the *inferred* class hierarchy in Protégé to confirm each `pidsys:` class sits under its intended anchor as entailed (not just as asserted) — a sanity check on subsumption, not a correctness gate now that consistency holds.
2. **Reconcile `vocab.py` / `rdf_mapper.py` / `walk.py`** to the model — the function-realization membership and the functional-only segment nature are the load-bearing gaps — or record, per point, where they intentionally lag.
3. **Reconcile `ido_semantic_mapping_spec.md` §6.1/§6.3** to the `pidsys:` naming.
4. **Resolve the document-type crosswalk target** (CFIHOS vs T.EN master) and populate `document_types_refdata.ttl` beyond the stub.

---

## Appendix — decision history

The model above is the settled state. It was reached by auditing against successive IDO artifacts (PCA web pages → 2023 WD → 2021 core → v4.2 FDIS), each of which corrected something; recording the corrections so they are not re-litigated:

- **`precomm:` → `pidsys:`** — matched the ontology to the running code, not the spec draft.
- **No `pidsys:System` intermediate** — an early draft added one; removed as a hop no axiom/rule/shape used. Grouping classes subclass `ido:System` directly.
- **`installedAs` is not the physical/functional bridge** — an early draft used it; it is the tag-vs-serial relation (and does not exist in v4.2 at all). The bridge is `hasFunction`/`realizedIn`.
- **Segment disjointness is a modelling choice, not an enforced axiom** — an early draft called a dual-typed segment "unsatisfiable"; in fact no IDO version asserts `System`|`PhysicalObject` disjointness, so a dual-typed segment would be permitted-but-wrong. Functional-only is the deliberate choice.
- **`FunctionalObject` deprecation was 2023-WD-only** — an interim draft (pinned to the WD) tracked a `deprecated? true` flag and designed `hasMember` around it. v4.2 does **not** deprecate `FunctionalObject`; the no-explicit-range design stands, reframed as clean inheritance.
- **`PhysicalArtefact ⊑ PhysicalObject` directly in v4.2** — earlier artifacts placed it under `InanimatePhysicalObject`; v4.2 changed this "to allow for artificially made organisms." Our anchor is unaffected (still resolves to PhysicalObject).
- **Import pin: 2021 core → v4.2 FDIS** — briefly pinned to the 2021 RDL core, then re-pinned when the v4.2 file was supplied, and all terms re-audited against it.
