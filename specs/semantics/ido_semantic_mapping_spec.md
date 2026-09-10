# IDO Semantic Mapping Specification — Pre-commissioning Systemization

**Status:** Draft for review · v0.1 (consolidated)
**Date:** 2026-09-10
**Data Product:** Automatic Pre-commissioning Systemization based on P&ID interoperability data
**Purpose:** Define how the data product's canonical plant model and computed systemization are mapped to a POSC Caesar / IDO semantic representation — the ontology foundation, the object-by-object coverage, the real-data bridge coverage across both source formats, and the placement of each concern across the medallion tiers.

**Consumes:** *Master Data, Reference Data & Metadata Specification* **[MD]**, *Systemization Output Specification* **[SS]**, *Algorithm Specification* **[ALG]**.
**Companions in the medallion set:** `silver_layer_spec.md`, `claude_gold_layer_spec.md`, `medallion_rdf_ido_strategy_mapping.md`, `architecture_note.md`.
**Supersedes:** `ido_coverage_table.md` (now §3) and `ido_mapping_placement_note.md` (now §5) — this document folds both in and adds the empirical coverage analysis (§4).

---

## 1. Purpose, scope & baseline

### 1.1 What this document does

The data product reconstructs a format-independent plant model from P&ID interoperability files and computes a pre-commissioning systemization over it. This specification defines the **semantic layer**: how that plant model and its computed output are represented in RDF against the IDO / LIS-14 upper ontology and the POSC Caesar PLM domain library, what maps natively, what needs a small domain extension, what stays as rules, and — for each piece — which medallion tier owns it.

It is the on-ramp to the program's semantic thesis (rules applied to data, no hardcode, IDO-aligned, provenance-traceable). It does **not** re-derive the systemization rules; those are specified in [ALG] and [SS] and are re-housed here into a declarative surface, exactly as the medallion strategy re-houses rather than re-derives.

### 1.2 Ontology baseline (decided)

**The program baseline is the LIS-14 2021 core** — the PCA "Part 14" upper ontology, the OWL 2 DL rendering of ISO 15926-2, issued 2021-08-06, namespace `lis: <http://rds.posccaesar.org/ontology/lis14/rdl/>`. Verified contents: **33 classes, 63 object properties, 3 datatype properties.**

> **Version caveat — carried forward.** This is **not** LIS-14-for-FDIS (the IDO 4.2 candidate the sibling schedule-IDO project used). Temporal-datum terms that project confirmed (`TemporalDatum`, `temporalValue`, `Specified`/`Actual`) are **absent from this core** — they belong to the FDIS layer. This does not affect the systemization mapping, which needs physical objects, connectivity, and composition (all present). But it means the schedule-IDO prototype and this systemization work rest on **different LIS-14 versions**; if the two are ever unified, that gap must be reconciled. For this product, the 2021 core is the baseline.

### 1.3 The domain library (assessed, recommended for adoption)

The **PCA PLM equipment ontology** (`rds.posccaesar.org/ontology/plm/`, v0.9.0, `ottr:status incomplete`) supplies the domain vocabulary the upper ontology deliberately omits: **246 equipment and component classes** with human-readable labels, arranged in a real taxonomy that roots cleanly into the LIS-14 core —

```
Gate Valve   ⊑ Valve       ⊑ Artefact ⊑ lis:InanimatePhysicalObject
Pipe Reducer ⊑ Pipe Fitting ⊑ Artefact ⊑ lis:InanimatePhysicalObject
Separator    ⊑ Static Equipment ⊑ Artefact ⊑ lis:InanimatePhysicalObject
```

It covers the product's actual component vocabulary directly (all valve types, flanges, reducers/tees/elbows, nozzles, actuator types, vessels/columns/pumps/exchangers, instrument element/transmitter types). **Recommendation: adopt it as the domain-vocabulary tier.** It is the "resolve component class to an RDL URI" target that the coverage table (§3) marks *RDL*, and it ships with a pre-built crosswalk to the older RDS identifier namespace (§4.2) that materially reduces the mapping effort. Two cautions: it is marked `incomplete` and imports two further PLM ontologies (`document`, `process`), and a few labels are rough (`Gas Liquide Seperator`, `Pulsation Dampner`) — so **pin the version**.

---

## 2. Architecture: the three-layer landing

Every finding in this document resolves to one target architecture — a layered separation of *plant facts*, *domain vocabulary*, *domain extension*, and *rules*. This mirrors the [MD]/[SS]/[ALG] spec split and is the shape the whole semantic thesis depends on.

| Layer | Content | Source | Owned by |
|---|---|---|---|
| **Shared T-Box** | `lis:PhysicalObject`, `lis:System`, `lis:InformationObject`, `lis:connectedTo`, composition properties — the neutral upper ontology | LIS-14 2021 core | shared, use-case-neutral |
| **Domain vocabulary** | Component / equipment classes (Gate Valve, Column…) | PCA PLM library + RDS RDL | shared, use-case-neutral |
| **`precomm:` extension** | Reified `Connection`, `flowsTo`, `ProcessUnit`, `StartUpPackage`, `CommissioningSystem`, `OffPageConnector`, boundary role, node identity | net-new, small | this data product |
| **Rule layer** | Self-owning networks (OWL), directional guards + cut limits (SHACL/SPARQL) | net-new; ports [ALG] | rule package (systemization) |

**The neutrality line (non-negotiable, per strategy §8.3):** the shared T-Box and domain vocabulary model *plant data, not systemization data*. Each rule package brings its own extension classes and reference data; they meet only at the shared, neutral facts. Blur this and the framework stops generalising — you have a systemization tool with delusions of being a platform.

---

## 3. LIS-14 coverage — object by object (verified against the file)

**Legend.** **FOUND** — term exists in `lis14.rdf`, verified · **RDL** — a domain class resolved to a POSC Caesar RDL / PLM URI, not the upper ontology · **EXT** — needs a `precomm:` class/property · **SHACL/rule** — expressible only as a constraint or rule, not a T-Box axiom.

### 3.1 Foundational anchors confirmed present

| LIS-14 term | Kind | Domain→range | Role in this mapping |
|---|---|---|---|
| `lis:PhysicalObject` ⊑ `lis:Object` | class | — | Root for every placed plant item |
| `lis:InanimatePhysicalObject` ⊑ `lis:PhysicalObject` | class | — | Piping components, equipment |
| `lis:FunctionalObject` ⊑ `lis:Object` | class | — | **Confirmed real** — the strategy doc's "unconfirmed placeholder" caveat is resolved |
| `lis:System` ⊑ `lis:FunctionalObject` | class | — | Native home for grouping/system concepts |
| `lis:InformationObject` ⊑ `lis:Object` | class | — | Documents (drawings) |
| `lis:Stream` ⊑ `lis:InanimatePhysicalObject` | class | — | Fluid streams (future [MD] stream objects) |
| `lis:Activity` ⊑ owl:Thing | class | — | Commissioning activities (Phase C / test packages) |
| `lis:connectedTo` | obj prop | `PhysicalObject → (PhysicalObject)` | Undirected connectivity — the graph [SS] walks |
| `lis:directlyConnectedTo` | obj prop | — | Node-adjacent connectivity |
| `lis:contains` / `lis:containedBy` | obj prop | `PhysicalObject → PhysicalObject` | Containment |
| `lis:hasArrangedPart` / `lis:arrangedPartOf` | obj prop | ⊑ hasPart | Spatial/arrangement composition |
| `lis:hasAssembledPart` / `lis:assembledPartOf` | obj prop | ⊑ hasPart | Assembly composition (component on segment) |
| `lis:functionalPartOf` / `lis:hasFunctionalPart` | obj prop | `FunctionalObject → System` | Membership of a functional object in a system |
| `lis:installedAs` | obj prop | `PhysicalObject → FunctionalObject` | The ISO 15926 physical↔functional split |
| `lis:hasFunction` | obj prop | `→ Function` | Instrument → its function |
| `lis:hasPhysicalQuantity` | obj prop | `PhysicalObject → PhysicalQuantity` | Nominal diameter, etc. |
| `lis:hasDisposition` | obj prop | `→ Disposition` | Component behaviour (candidate for boundary role) |
| `lis:representedBy` / `lis:representedIn` | obj prop | `→ InformationObject` | Component shown on a Document |
| `lis:residesIn` | obj prop | `PhysicalObject → Location` | Component in a spatial location |

**Two structural gaps confirmed against the vocabulary:**

1. **No flow-direction property.** `connectedTo` is undirected; there is no `flowsTo`. The three directional guards ([ALG §6]) *require* an EXT `precomm:flowsTo`.
2. **No native reified-connection class.** `connectedTo` is a bare property and cannot carry the Source/Derived provenance flag ([MD §2.12]). Reify as `precomm:Connection` (or RDF-star).

### 3.2 Master-data objects ([MD §2])

| # | [MD] object | Mapping | Verdict | Notes |
|---|---|---|---|---|
| 2.1 | **Document** (P&ID) | `lis:InformationObject` | FOUND | Component→doc via `lis:representedIn`. Two business keys → EXT datatype props. |
| 2.1a | **Process Unit** | `lis:System` + EXT | EXT | Tag-decoded grouping level. `precomm:ProcessUnit ⊑ lis:System`; SUP join is a classification. |
| 2.1b | **Start-Up Package** | `lis:System` + EXT | EXT | Reference-data-provided root. `precomm:StartUpPackage ⊑ lis:System`; `UnitSUP` = classification link; sequence = datatype prop. |
| 2.2 | **Pipeline System** | `lis:System` | FOUND-ish | Functional grouping via `lis:hasFunctionalPart`; Tag → EXT datatype prop. |
| 2.2a | **Sub Piping System (Subline)** | `lis:System` + EXT | FOUND-ish | Project-B-only intermediate level; same pattern one level down. |
| 2.3 | **Piping Segment** | `lis:InanimatePhysicalObject` + RDL | FOUND | Physical object; class resolves to RDL; components via `lis:hasAssembledPart`. |
| 2.4 | **Piping Component** | `lis:InanimatePhysicalObject` + **RDL** | FOUND+RDL | Object is FOUND; **Component Class** (GateValve, Reducer…) is **RDL** — where the class-resolution bridge (§4) plugs in. |
| 2.4.1 | **Connection Node** | `lis:Feature` + EXT | EXT | `lis:Feature ⊑ PhysicalObject`; per-node diameter via `hasPhysicalQuantity`; node identity EXT. |
| 2.5 | **Instrument** | `lis:InanimatePhysicalObject` + RDL | FOUND+RDL | Specialisation of Piping Component; in-line vs off-line = EXT attribute. |
| 2.6 | **Actuator** | `lis:InanimatePhysicalObject` + RDL | FOUND+RDL | `operates` the valve → EXT property. |
| 2.7 | **Instrument Function** | `lis:Function` | FOUND | `lis:hasFunction`; realised-by via `lis:realizedIn` / EXT. |
| 2.8 | **Instrumentation Loop** | `lis:System` + EXT | EXT | `precomm:InstrumentationLoop ⊑ lis:System`; members via `hasFunctionalPart`. |
| 2.9 | **Process Equipment** | `lis:InanimatePhysicalObject` + RDL | FOUND+RDL | Equipment Class is RDL; ghost-filter is an ingestion/SHACL rule, not ontology. |
| 2.9.1 | **Nozzle** | `lis:Feature` + EXT | EXT | Feature/part of equipment; the pipe-to-equipment boundary. |
| 2.10 | **Signal** | `lis:connectedTo` variant + EXT | EXT | Instrument-side connectivity; `precomm:signalConnectedTo` or reified. |
| 2.11 | **Off-Page Connector** | EXT | EXT | No LIS-14 term; `precomm:OffPageConnector` + pairing key; drives [ALG §5] assembly. |
| 2.12 | **Connection** (reified edge) | EXT `precomm:Connection` | EXT | **Keystone EXT class.** From/To, nodes, type, Source/Derived flag. |

### 3.3 Systemization output ([SS]) — all computed, all EXT

None are extracted; all are `computed` provenance, and belong in the extension + rule layer, never the shared T-Box.

| [SS] concept | Mapping | Verdict | Notes |
|---|---|---|---|
| **Commissioning System** | `precomm:CommissioningSystem ⊑ lis:System` | EXT | `hasMember`, `hasBoundary`, `computed` provenance, carried `sourceTurnoverAssignment` for Phase C. |
| **Boundary-forming role** | EXT defined class + SKOS | EXT+rule | `precomm:BoundaryFormingComponent ≡ component whose class ∈ Boundary list`. List stays SKOS refdata; reasoner marks boundaries. |
| **Fluid classification** | SKOS scheme on fluid code | EXT+refdata | Category/Subcategory (Process/Utility/Flare/Steam) drives self-owning-network rules. |
| **Self-owning networks** (flare, steam/condensate) | OWL defined class / property chain | rule (OWL-expressible) | Membership by fluid code + connectivity; no arithmetic. |
| **Directional guards** (flare, consumer, relief inlet-side) | SHACL/SPARQL over `precomm:flowsTo` | SHACL/rule | Needs materialised flow direction; not OWL-DL-expressible. |
| **"Cut at last manual valve before class change"** | SHACL/SPARQL | SHACL/rule | Ordering + class comparison; arithmetic-adjacent, stays out of OWL. |
| **Sub-system split** | EXT + human judgement | rule + manual | Open Decisions #1,#2 — organisational judgement, NOT ontology content. |

### 3.4 What must NOT go in the ontology

- **Open Decisions #1, #2, #7, #10** ([SS §9]) — equipment-anchoring trigger, sub-system automation, rule precedence, rating-based split. Project inputs or commissioning judgement; surface as reference data / review gates, never T-Box axioms.
- **The oracle** (`Z_TurnOverSystemNumber`) — a rule-invisible named graph (`graph:oracle`), never read by rules, exactly as `walk.py` refuses it today. Structural enforcement of the compute-only discipline.
- **Systemization concepts in the shared T-Box** — see the neutrality line (§2).

---

## 4. Real-data bridge coverage — the empirical finding

The coverage in §3 says *what maps in principle*. This section reports *what actually resolves*, measured on real validation drawings from both source formats — because the bridge from a source's component class to the PLM library behaves very differently per format, and that difference shapes the architecture.

### 4.1 The two identifier namespaces

Project A (DEXPI) files carry class URIs, but in the **`data.posccaesar.org/rdl/RDS…`** namespace (the classic ISO 15926-4 RDL) — **not** the PLM library's **`rds.posccaesar.org/ontology/plm/rdl/PCA_…`** namespace. They are distinct URI systems; a `RDS…` type assertion does not match a `PCA_…` class on string equality. So there is **no direct URI join** — the bridge runs through a crosswalk.

### 4.2 The crosswalk is pre-published (for DEXPI)

The PLM equipment library **already contains** the crosswalk back to the RDS namespace: **1,057 references across 208 distinct RDS codes**, wired via SKOS mapping predicates (**345 `closeMatch`, 22 `relatedMatch`, 1 `exactMatch`**). So DEXPI's `RDS…` URIs resolve to PLM classes through mappings **PCA already shipped**, not ones the product must invent. Verified end-to-end on a real vessel: DEXPI `RDS427229` → `skos:closeMatch` → PLM `Pressure Vessel` (`PCA_100005976`) → `⊑ lis:InanimatePhysicalObject`.

### 4.3 Coverage measured on real drawings

| | **Project A — DEXPI** (`362-09-01010`) | **Project B — PostProc** (`216097C-A14-…-0001`) |
|---|---|---|
| Physical items | 478 | 323 |
| Type carrier | `ComponentClass` + **`ComponentClassURI` (RDS…)** | `ComponentClass` **string only** |
| RDL URIs present | yes | **none** — zero `posccaesar`/`RDS`/`ComponentClassURI` in 10.7 MB |
| Primary bridge | URI → SKOS `closeMatch` → PLM | **label → PLM `rdfs:label`** (only option) |
| Auto-resolve to PLM | **36%** via URI | **49.5%** via label |
| Boundary-forming valves | Gate/Check/Globe/Butterfly ✓ | Gate/Check/Globe ✓ + **Blind Flange** ✓ |

The counterintuitive result: **PostProc resolves at a higher raw rate (49.5% vs 36%) but through the weaker mechanism** (label match, not URI). It also surfaces Blind Flange (38 items) — a positive-isolation boundary class in [MD §3.2] — that Project A's data did not.

The critical structural finding — despite the name, **INGR ISO-15926 PostProc carries no ISO-15926 reference-data URIs at all.** Typing is by SmartPlant class string only.

### 4.4 The unresolved items decompose into three distinct causes

Neither "64% unresolved" (DEXPI) nor "50%" (PostProc) is mostly a library gap. Decomposed:

| Cause | Meaning | DEXPI count | Fix |
|---|---|---|---|
| **A — generic/"Custom"/None class** | source emitted a catch-all; no real type to map | 132 (28%) | none possible — a data-quality finding about the export, not the ontology |
| **B — real type but no URI in export** | usable `ComponentClass` string, no `ComponentClassURI` (Flange, Actuator, Solenoid, Orifice, instruments) | 106 (22%) | **label backstop** — PLM has these classes; match on string |
| **C — has RDS URI but not in the 208 mappings** | stable URI, no PLM SKOS target | 102 (21%) | **local extension mapping** — hand-curate; prioritise boundary-forming (`PipeFlangeSpacer` first) |

For PostProc, the large unresolved buckets (`PipingNetworkBranch` 74, `ConcentricDiameterChange` 30, `Flange` 48) are **PostProc's naming** for classes PLM *has* (`Pipe Tee`, `Pipe Reducer`, `Pipe Flange`) — a synonym/normalization problem fixable with a small alias table, not a library gap.

### 4.5 Consequence — the bridge is format-specific, the target is shared

The two formats need **structurally different bridges to the same PLM library**:

- **Project A (DEXPI):** bridge on **URI** (`RDS… → PLM` via published closeMatch), robust and code-based; label match is the *backstop* for cause-B items.
- **Project B (PostProc):** bridge on **label** (`ComponentClass → PLM rdfs:label`), the *only* path — no URIs exist; needs a curated synonym/alias table.

This lands exactly on the program's **"DEXPI and PostProc codebases never merge"** constraint: the class-resolution strategy is **format-specific and lives inside each adapter**, while both converge on the same shared PLM target vocabulary above the adapter. The semantic layer is shared; the on-ramp to it is not.

**Two disciplines this imposes:**
1. **closeMatch is "near," not "equal."** For boundary-forming classes specifically (valves, spacers), the closeMatch target passes a **human review gate** before feeding trusted systemization — the same rule as "unreviewed CV output never feeds trusted priors."
2. **Label matching fails silently.** URI matching fails loudly (code not found); label matching can fail through a *plausible wrong match*. The review gate on the Project-B crosswalk is therefore load-bearing, not polish.

### 4.6 The two crosswalk assets (Workstream 1.5, to build)

- **`RDS → PLM`** (DEXPI): mostly the pre-published 208 SKOS mappings, plus a hand-curated set for the ~102 cause-C gaps, boundary-forming first.
- **`ComponentClass-string → PLM-label`** (PostProc): a curated alias/synonym table (`ConcentricDiameterChange → Pipe Reducer`, `PipingNetworkBranch → Pipe Tee`, `Flange → Pipe Flange`, …), review-gated.

Two assets, one per adapter, meeting at the shared PLM vocabulary. Both are governed reference data (§5), not code.

---

## 5. Tier placement — where each concern lives

### 5.1 The mapping is two operations, not one

Treating "the mapping" as a single thing is what makes its tier placement feel ambiguous. Split it:

| | Operation | Produces | Nature |
|---|---|---|---|
| **(1) Class-signal extraction** | Lift the source's own class signal: `ComponentClass` string **and** native `RDS…` URI where present | plain **columns** | format-specific *shred* — Silver Stage A |
| **(2) Vocabulary resolution** | Resolve to the PLM/LIS-14 target (`RDS→PLM` closeMatch; `ComponentClass→PLM` label), then project as RDF | resolved URI + **triples** | semantic-alignment judgement — projection time |

Silver's contract "**Silver emits tables, not triples**" (`silver_layer_spec.md` §1.2) cleanly excludes (2)'s triples. It does not by itself settle (1) vs (2)'s *resolution* — which this section decides.

### 5.2 The decision

**Silver carries the raw class signal. The resolution to PLM/IDO vocabulary is reference-data-driven, applied at RDF-projection time (the Silver→Gold boundary).**

- **In Silver** — `silver_components` / `silver_equipment` carry, as plain columns: `component_class` (the source string, always present) and `component_class_uri` (the native `RDS…` URI when the format provides it — null for PostProc). Pure Stage-A shred. **No PLM/LIS-14 vocabulary enters a Silver column.**
- **At projection (Gold / `graph:refdata`)** — the crosswalks (§4.6) resolve the signal to the target vocabulary; misses become `class_unresolved` quality flags with a LIS-14-core-class fallback and the raw string retained; boundary-forming resolutions pass the review gate (§4.5).

### 5.3 Why this line, and not "mapping in Silver"

**Why the raw signal and the crosswalk-as-refdata are legitimate:** class resolution is reference-data lookup — Silver's established remit (Stage D already resolves Fluid/Unit/Naming sheets as "rules-as-data"). Resolving a valve's class is use-case-neutral (a plant fact), and computing it once serves every rule package.

**Why the target vocabulary stays out of Silver columns:**
1. **Store-neutrality.** Silver is the *store-binding contract* — bindable to RDF, an LPG, or plain SQL. A `plm:PCA_…` URI in a Silver column silently commits Silver to the RDF target. Writing `resolved_class = plm:PCA_100005929` onto `silver_components` is the RDF layer wearing a table costume — the line not to cross.
2. **closeMatch is a claim, not a lookup.** Asserting a closeMatch is good enough to *be* a component's class is an ontology-alignment judgement needing review — semantic-layer work. `GateValve → "Gate Valve"` (string) is safe normalization; `GateValve ≡ plm:PCA_100005929` is a claim Silver has no remit to make.

**The test:** a *string or source-native code* can sit in Silver; a *target-ontology URI* belongs at projection. The join between them is governed reference data at the boundary.

### 5.4 Where the semantic thesis sits (the wider placement)

| Part of the thesis | Tier | Status |
|---|---|---|
| **RDF/IDO projection + serving** — canonical objects → LIS-14/PLM RDF, reified Connections w/ `flowsTo`/`derived`, named graphs, SPARQL, Fuseki | **Gold** | Built; real Project-B master data pushed to live Fuseki |
| **Declarative classification rules** — fluid→category, boundary role, directional guards, relief attribution, as Jena/OWL-RL over `graph:refdata` | **Gold (rule sub-layer)** | Specified; classification/partition split locked |
| **Global graph partition** — the walk forming commissioning systems | **compute engine (Python)** | Stays in `walk.py`; a reasoner is not a graph-algorithms engine |
| **Topology reconstruction** — the crown jewel the rules classify | **Silver** | Built, ~97% agreement, re-housed not re-derived |

**The semantic thesis is Gold-tier in the target architecture** (RDF projection *and* the declarative rule layer), **above the facts it makes meaningful** — reconstruction (Silver) and the partition (compute).

**Delivery-sequence caveat:** the first prototypes (pydsys, bppidsys) are compute-only Python — the Phase-B [ALG] artifact, no RDF. The IDO/OWL exploration is the *next* phase, consuming the validated rules rather than replacing them. **Gold-tier in architecture, later-phase in delivery.** The coverage table (§3) and crosswalks (§4.6) are the on-ramp.

---

## 6. Net-new work summary & open decisions

### 6.1 The net-new ontology is small and bounded

The plant-fact backbone (physical objects, connectivity, composition, systems, documents) is **natively covered by LIS-14**; the component/equipment vocabulary is **covered by the PLM library**. The net-new `precomm:` extension is only: one reified `Connection` class, a `flowsTo` property, three grouping/output classes (`ProcessUnit`, `StartUpPackage`, `CommissioningSystem`, plus `InstrumentationLoop`), the `OffPageConnector`, the boundary role, and node identity. Everything genuinely hard is the **rule layer**, which is the point of the semantic thesis.

### 6.2 Open decisions carried by this spec

1. **LIS-14 version reconciliation** — this product baselines on the 2021 core; the schedule-IDO prototype used FDIS. Reconcile if the two are ever unified (§1.2).
2. **PLM library version pin** — adopt v0.9.0 but pin it; it is `incomplete` and imports `document`/`process` (§1.3).
3. **Cause-C manual mappings** — curate the ~102 RDS-with-no-PLM-target codes, boundary-forming first (§4.4, §4.6).
4. **PostProc alias table** — build and review the synonym set that lifts Project-B coverage (§4.4, §4.6).
5. **Boundary-forming review gate** — operationalise the human review of closeMatch/label targets for boundary classes (§4.5).

### 6.3 Next artifacts (workstreams)

- **Workstream 1.5** — the two crosswalk files (§4.6), boundary-forming prioritised and review-flagged.
- **Workstream 2** — the `precomm:` extension ontology (§6.1).