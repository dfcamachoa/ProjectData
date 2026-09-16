# IDO Semantic Mapping Specification — Pre-commissioning Systemization

**Status:** Draft for review · v0.2 (reconciled to the built ontology)
**Date:** 2026-09-16
**Data Product:** Automatic Pre-commissioning Systemization based on P&ID interoperability data
**Purpose:** Define how the data product's canonical plant model and computed systemization are mapped to a POSC Caesar / IDO semantic representation — the ontology foundation, the object-by-object coverage, the real-data bridge coverage across both source formats, and the placement of each concern across the medallion tiers.

**Consumes:** *Master Data, Reference Data & Metadata Specification* **[MD]**, *Systemization Output Specification* **[SS]**, *Algorithm Specification* **[ALG]**.
**Companions in the medallion set:** `silver_layer_spec.md`, `claude_gold_layer_spec.md`, `medallion_rdf_ido_strategy_mapping.md`, `architecture_note.md`.
**Built artifacts (Workstream 2):** `pidsys_extension.ttl` (the extension ontology, reasoner-verified consistent under HermiT), `document_types_refdata.ttl` (document-type SKOS stub), `workstream_2_precomm_ontology_scoping.md` (the extension's scoping note — authoritative for the extension's internal design).
**Supersedes:** `ido_coverage_table.md` (now §3), `ido_mapping_placement_note.md` (now §5), and v0.1 of this document.

> **v0.2 reconciliation note.** This revision aligns the spec with the extension ontology actually built and reasoner-verified in Workstream 2. Three changes are **deliberate reversals of v0.1 decisions**, not typo fixes, and are called out where they occur: (a) the extension prefix is **`pidsys:`**, not `precomm:`; (b) the ontology baseline is re-pinned to **IDO/LIS-14 v4.2 (FDIS)**, reversing v0.1 §1.2's choice of the 2021 core; (c) made physical items anchor on **`lis:PhysicalArtefact`**, not bare `lis:InanimatePhysicalObject`. The empirical bridge analysis (§4) and tier placement (§5) are unchanged — those findings are format facts, independent of the ontology version.

---

## 1. Purpose, scope & baseline

### 1.1 What this document does

The data product reconstructs a format-independent plant model from P&ID interoperability files and computes a pre-commissioning systemization over it. This specification defines the **semantic layer**: how that plant model and its computed output are represented in RDF against the IDO / LIS-14 upper ontology and the POSC Caesar PLM domain library, what maps natively, what needs a small domain extension, what stays as rules, and — for each piece — which medallion tier owns it.

It is the on-ramp to the program's semantic thesis (rules applied to data, no hardcode, IDO-aligned, provenance-traceable). It does **not** re-derive the systemization rules; those are specified in [ALG] and [SS] and are re-housed here into a declarative surface, exactly as the medallion strategy re-houses rather than re-derives.

This is a **mapping** spec — it states how each master-data object *maps in principle*. The extension ontology (`pidsys_extension.ttl`) is the **built** subset; where the two differ in scope (e.g. instrumentation, §3.2), the mapping is retained here as design intent while the built ontology defers it (§6.4).

### 1.2 Ontology baseline (decided — v4.2 FDIS)

**The baseline is IDO / LIS-14 v4.2 (FDIS candidate)** — the current POSC Caesar upper ontology, an OWL 2 DL rendering in the ISO 15926-2 lineage, `owl:versionIRI …/lis14/ont/core/4.2` ("FDIS proposal submitted April 2026", modified 2026-04-10), namespace `lis: <http://rds.posccaesar.org/ontology/lis14/rdl/>`, ontology IRI `http://rds.posccaesar.org/ontology/lis14/ont/core`. Approximate contents: **~50 named classes, ~102 object properties, 3 datatype properties.** The extension ontology `owl:imports` this core, and every `ido:`/`lis:` term the extension uses has been audited against this file and the assembled ontology confirmed **consistent under HermiT**.

> **Reversal of v0.1 (tracked).** v0.1 baselined on the **LIS-14 2021 core** (33 classes / 63 obj props) and explicitly treated the FDIS layer as a *different* project's baseline. Workstream 2 re-pinned to v4.2 because it is the current PCA artifact, it contains every term the extension needs, and the built ontology is verified against it. Consequences of the move, all confirmed in v4.2: `lis:PhysicalArtefact ⊑ lis:PhysicalObject` **directly** (v4.2 deliberately does not place it under `InanimatePhysicalObject`, "to allow for artificially made organisms"); `lis:FunctionalObject` is present and **not deprecated**; `lis:Feature ⊑ lis:PhysicalObject`; `lis:installedAs` does **not** exist in v4.2 (see §3.1). The 2021-core temporal-datum gap noted in v0.1 is moot on this baseline.

> **Version-unification note.** The sibling schedule-IDO prototype used an earlier FDIS candidate; with this product now also on v4.2, the two are closer to a common baseline than under v0.1, but a precise version match should still be confirmed before any unification.

### 1.3 The domain library (assessed, recommended for adoption)

The **PCA PLM equipment ontology** (`rds.posccaesar.org/ontology/plm/`, v0.9.0, `ottr:status incomplete`) supplies the domain vocabulary the upper ontology deliberately omits: **246 equipment and component classes** with human-readable labels, arranged in a real taxonomy that roots cleanly into the LIS-14 core —

```
Gate Valve   ⊑ Valve        ⊑ Artefact ⊑ lis:PhysicalObject
Pipe Reducer ⊑ Pipe Fitting ⊑ Artefact ⊑ lis:PhysicalObject
Separator    ⊑ Static Equipment ⊑ Artefact ⊑ lis:PhysicalObject
```

It covers the product's actual component vocabulary directly (all valve types, flanges, reducers/tees/elbows, nozzles, actuator types, vessels/columns/pumps/exchangers, instrument element/transmitter types). **Recommendation: adopt it as the domain-vocabulary tier.** It is the "resolve component class to an RDL URI" target that the coverage table (§3) marks *RDL*, and it ships with a pre-built crosswalk to the older RDS identifier namespace (§4.2) that materially reduces the mapping effort. Two cautions: it is marked `incomplete` and imports two further PLM ontologies (`document`, `process`), and a few labels are rough (`Gas Liquide Seperator`, `Pulsation Dampner`) — so **pin the version**.

*(The PLM taxonomy's own upper anchor — whether its `Artefact` roots via `InanimatePhysicalObject` or, as in v4.2's core, directly under `PhysicalObject` — should be checked against the pinned v4.2 core when the crosswalk is wired; it does not change which PLM leaf class a component resolves to.)*

---

## 2. Architecture: the three-layer landing

Every finding in this document resolves to one target architecture — a layered separation of *plant facts*, *domain vocabulary*, *domain extension*, and *rules*. This mirrors the [MD]/[SS]/[ALG] spec split and is the shape the whole semantic thesis depends on.

| Layer | Content | Source | Owned by |
|---|---|---|---|
| **Shared T-Box** | `lis:PhysicalObject`, `lis:PhysicalArtefact`, `lis:Feature`, `lis:System`, `lis:InformationObject`, `lis:connectedTo`, composition properties — the neutral upper ontology | LIS-14 v4.2 core | shared, use-case-neutral |
| **Domain vocabulary** | Component / equipment classes (Gate Valve, Column…) | PCA PLM library + RDS RDL | shared, use-case-neutral |
| **`pidsys:` extension** | Reified `Connection`, `flowsTo`, `ProcessUnit`, `StartUpPackage`, `CommissioningSystem`, `InstrumentationLoop`, `OffPageConnector`, `Nozzle`, `ConnectionNode`, `Document`, `Line`/`Subline`, boundary role | net-new, small | this data product |
| **Rule layer** | Self-owning networks (OWL), directional guards + cut limits (SHACL/SPARQL) | net-new; ports [ALG] | rule package (systemization) |

**The neutrality line (non-negotiable, per strategy §8.3):** the shared T-Box and domain vocabulary model *plant data, not systemization data*. Each rule package brings its own extension classes and reference data; they meet only at the shared, neutral facts. Blur this and the framework stops generalising — you have a systemization tool with delusions of being a platform.

---

## 3. LIS-14 coverage — object by object (verified against v4.2)

**Legend.** **FOUND** — term exists in the v4.2 core, verified · **RDL** — a domain class resolved to a POSC Caesar RDL / PLM URI, not the upper ontology · **EXT** — needs a `pidsys:` class/property · **SHACL/rule** — expressible only as a constraint or rule, not a T-Box axiom.

### 3.1 Foundational anchors confirmed present (v4.2)

| LIS-14 v4.2 term | Kind | Domain→range | Role in this mapping |
|---|---|---|---|
| `lis:PhysicalObject` ⊑ `lis:Object` | class | — | Root for every placed plant item |
| `lis:InanimatePhysicalObject` ⊑ `lis:PhysicalObject` | class | — | Inanimate physical objects (e.g. the physical pipe pieces realising a segment) |
| `lis:PhysicalArtefact` ⊑ `lis:PhysicalObject` | class | — | **Made physical items** — piping components, equipment. Direct under PhysicalObject in v4.2 |
| `lis:Feature` ⊑ `lis:PhysicalObject` | class | — | Connection points / nozzles (a part on a physical object); on the connectivity graph |
| `lis:FunctionalObject` ⊑ `lis:Object` | class | — | **Confirmed real and NOT deprecated in v4.2**; `⊑ Object` + `hasFunction some Function` |
| `lis:System` ⊑ `lis:FunctionalObject` | class | — | Native home for grouping/system concepts; `⊑ hasFunctionalPart some FunctionalObject` |
| `lis:InformationObject` ⊑ `lis:Object` | class | — | Documents (drawings), reified connections; disjoint with PhysicalObject |
| `lis:Function` | class | — | Instrument / component function |
| `lis:Activity` | class | — | Commissioning activities (Phase C / test packages) |
| `lis:connectedTo` | obj prop | `PhysicalObject → PhysicalObject`, **Symmetric** | Undirected connectivity — the graph [SS] walks |
| `lis:directlyConnectedTo` | obj prop | ⊑ connectedTo, Symmetric | Node-adjacent connectivity |
| `lis:hasArrangedPart` / `lis:arrangedPartOf` | obj prop | ⊑ hasPart | Spatial/arrangement composition |
| `lis:hasAssembledPart` / `lis:assembledPartOf` | obj prop | ⊑ hasArrangedPart | Assembly composition (component on segment) |
| `lis:hasFeature` / `lis:featureOf` | obj prop | `→ Feature` (⊑ hasArrangedPart) | Component/equipment → its feature (nozzle, connection node) |
| `lis:functionalPartOf` / `lis:hasFunctionalPart` | obj prop | `System → FunctionalObject` | Membership of a functional object in a system |
| `lis:hasFunction` | obj prop | `→ Function` | A functional object → its function |
| `lis:realizedIn` | obj prop | `Potential → Activity` | Function realised in an activity (the physical/functional bridge, with participation) |

**Removed from the v0.1 anchor table:** `lis:installedAs` — it does **not** exist in v4.2, and it was in any case the tag-vs-serial-numbered-artefact relation (`InstalledObject → PrescriptiveObject`), **not** the physical↔functional bridge v0.1 implied. The physical→functional link is the **function-realization pattern**: a functional individual `hasFunction some Function`; that Function (`⊑ Potential`) is `realizedIn` an `Activity` in which the physical object participates.

**Two structural gaps confirmed against the vocabulary:**

1. **No flow-direction property.** `connectedTo` is undirected and Symmetric; there is no `flowsTo`. The three directional guards ([ALG §6]) *require* an EXT `pidsys:flowsTo` — which cannot be a subproperty of the symmetric `connectedTo` (it would inherit symmetry and lose direction), so it is a standalone extension property.
2. **No native reified-connection class.** `connectedTo` is a bare property and cannot carry the Source/Derived provenance flag ([MD §2.12]). Reify as `pidsys:Connection ⊑ lis:InformationObject` (an information artifact about two objects).

### 3.2 Master-data objects ([MD §2])

| # | [MD] object | Mapping | Verdict | Notes |
|---|---|---|---|---|
| 2.1 | **Document** (P&ID) | `pidsys:Document ⊑ lis:InformationObject` | FOUND+EXT | Declared as `pidsys:Document` so Connection/OPC have a concrete "about" target and the containment chain has a root. Component→doc via `lis:representedIn`. Document *type* is SKOS refdata (§5, `document_types_refdata.ttl`), not subclasses. |
| 2.1a | **Process Unit** | `pidsys:ProcessUnit ⊑ lis:System` | EXT | Tag-decoded grouping level; SUP join is a classification. |
| 2.1b | **Start-Up Package** | `pidsys:StartUpPackage ⊑ lis:System` | EXT | Reference-data-provided root; `UnitSUP` = classification link; sequence = datatype prop. |
| 2.2 | **Pipeline System** = **Line** | `pidsys:Line ⊑ lis:System` | FOUND+EXT | "Pipeline System" is the master-data name for `pidsys:Line`; **no separate class**. Functional grouping via `lis:hasFunctionalPart`; Tag → EXT datatype prop. |
| 2.2a | **Sub Piping System (Subline)** | `pidsys:Subline ⊑ lis:System` | EXT | Project-B-only intermediate level; same pattern one level down. |
| 2.3 | **Piping Segment** | `pidsys:PipingSegment ⊑ lis:System` | EXT | **Functional grouping**, not a physical object — the segment is the slot; the physical pieces realising it are separate `lis:InanimatePhysicalObject` individuals. Components join via `hasFunctionalPart`; shared diameter/class/insulation are the grouping's attributes. |
| 2.4 | **Piping Component** | `pidsys:PipingComponent ⊑ lis:PhysicalArtefact` + **RDL** | FOUND+RDL | A made physical item; **Component Class** (GateValve, Reducer…) is **RDL** — where the class-resolution bridge (§4) plugs in. |
| 2.4.1 | **Connection Node** | `pidsys:ConnectionNode ⊑ lis:Feature` | EXT | On the connectivity graph (Feature ⊑ PhysicalObject); per-node diameter via `hasPhysicalQuantity`; node identity EXT; `featureOf` its component. |
| 2.5 | **Instrument** | `lis:PhysicalArtefact` + RDL | FOUND+RDL *(deferred, §6.4)* | Maps as a made physical item; **out of the built ontology's current scope.** |
| 2.6 | **Actuator** | `lis:PhysicalArtefact` + RDL | FOUND+RDL *(deferred, §6.4)* | `operates` the valve → EXT property. Out of current built scope. |
| 2.7 | **Instrument Function** | `lis:Function` | FOUND *(deferred, §6.4)* | `lis:hasFunction`; realised via `lis:realizedIn`. Out of current built scope. |
| 2.8 | **Instrumentation Loop** | `pidsys:InstrumentationLoop ⊑ lis:System` | EXT | Members via `hasFunctionalPart`. (Class declared; population tied to instrumentation scope.) |
| 2.9 | **Process Equipment** | `pidsys:Equipment ⊑ lis:PhysicalArtefact` + RDL | FOUND+RDL | Equipment Class is RDL; ghost-filter (tagged + ≥1 nozzle) is an ingestion/SHACL rule, not ontology. |
| 2.9.1 | **Nozzle** | `pidsys:Nozzle ⊑ lis:Feature` | EXT | Feature of equipment (`featureOf`); the pipe-to-equipment boundary; on the connectivity graph. **Not** a FunctionalObject (that would leave the connectivity graph). |
| 2.10 | **Signal** | `lis:connectedTo` variant + EXT | EXT *(deferred, §6.4)* | Instrument-side connectivity. Out of current built scope. |
| 2.11 | **Off-Page Connector** | `pidsys:OffPageConnector ⊑ lis:InformationObject` | EXT | A drawing-continuation symbol and a Connection **endpoint** (sibling of Connection, not a subclass); `terminates` a PipingSegment; pairing key drives [ALG §5] assembly. |
| 2.12 | **Connection** (reified edge) | `pidsys:Connection ⊑ lis:InformationObject` | EXT | **Keystone EXT class.** From/To (union of PhysicalObject and OffPageConnector), nodes, type, mandatory Source/Derived flag. |

### 3.3 Systemization output ([SS]) — all computed, all EXT

None are extracted; all are `computed` provenance, and belong in the extension + rule layer, never the shared T-Box.

| [SS] concept | Mapping | Verdict | Notes |
|---|---|---|---|
| **Commissioning System** | `pidsys:CommissioningSystem ⊑ lis:System` | EXT | `hasMember` (⊑ `lis:hasFunctionalPart`, members are functional individuals reached via the function-realization pattern), `hasBoundary`, `computed` provenance. |
| **Boundary-forming role** | EXT defined class + SKOS | EXT+rule | `pidsys:BoundaryFormingComponent ≡ component whose class ∈ Boundary list`. List stays SKOS refdata; reasoner marks boundaries. |
| **Fluid classification** | SKOS scheme on fluid code | EXT+refdata | Category/Subcategory (Process/Utility/Flare/Steam) drives self-owning-network rules. |
| **Self-owning networks** (flare, steam/condensate) | OWL defined class / property chain | rule (OWL-expressible) | Membership by fluid code + connectivity; no arithmetic. |
| **Directional guards** (flare, consumer, relief inlet-side) | SHACL/SPARQL over `pidsys:flowsTo` | SHACL/rule | Needs materialised flow direction; not OWL-DL-expressible. |
| **"Cut at last manual valve before class change"** | SHACL/SPARQL | SHACL/rule | Ordering + class comparison; arithmetic-adjacent, stays out of OWL. |
| **Sub-system split** | EXT + human judgement | rule + manual | Open Decisions #1,#2 — organisational judgement, NOT ontology content. |

### 3.4 What must NOT go in the ontology

- **Open Decisions #1, #2, #7, #10** ([SS §9]) — equipment-anchoring trigger, sub-system automation, rule precedence, rating-based split. Project inputs or commissioning judgement; surface as reference data / review gates, never T-Box axioms.
- **The oracle** (`Z_TurnOverSystemNumber`) — a rule-invisible named graph (`graph:oracle`), never read by rules, exactly as `walk.py` refuses it today. Structural enforcement of the compute-only discipline.
- **Systemization concepts in the shared T-Box** — see the neutrality line (§2).
- **Document types as classes** — a large, mutable, client-varying vocabulary; SKOS refdata, never `owl:Class` per type (§5).

---

## 4. Real-data bridge coverage — the empirical finding

*(Unchanged from v0.1 — these are format facts, independent of the ontology version. The one edit is the upper-ontology anchor in §4.2's worked example: v4.2 roots the PLM leaf under `lis:PhysicalObject`.)*

The coverage in §3 says *what maps in principle*. This section reports *what actually resolves*, measured on real validation drawings from both source formats — because the bridge from a source's component class to the PLM library behaves very differently per format, and that difference shapes the architecture.

### 4.1 The two identifier namespaces

Project A (DEXPI) files carry class URIs, but in the **`data.posccaesar.org/rdl/RDS…`** namespace (the classic ISO 15926-4 RDL) — **not** the PLM library's **`rds.posccaesar.org/ontology/plm/rdl/PCA_…`** namespace. They are distinct URI systems; a `RDS…` type assertion does not match a `PCA_…` class on string equality. So there is **no direct URI join** — the bridge runs through a crosswalk.

### 4.2 The crosswalk is pre-published (for DEXPI)

The PLM equipment library **already contains** the crosswalk back to the RDS namespace: **1,057 references across 208 distinct RDS codes**, wired via SKOS mapping predicates (**345 `closeMatch`, 22 `relatedMatch`, 1 `exactMatch`**). So DEXPI's `RDS…` URIs resolve to PLM classes through mappings **PCA already shipped**, not ones the product must invent. Verified end-to-end on a real vessel: DEXPI `RDS427229` → `skos:closeMatch` → PLM `Pressure Vessel` (`PCA_100005976`) → `⊑ lis:PhysicalObject`.

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

*(Unchanged from v0.1 — the placement logic is version-independent. `precomm:` → `pidsys:` only.)*

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

The plant-fact backbone (physical objects, connectivity, composition, systems, documents) is **natively covered by LIS-14 v4.2**; the component/equipment vocabulary is **covered by the PLM library**. The net-new `pidsys:` extension is only: one reified `Connection` class, a `flowsTo` property, the grouping/output classes (`ProcessUnit`, `StartUpPackage`, `CommissioningSystem`, `InstrumentationLoop`) plus the line-hierarchy groupings (`Line`, `Subline`, `PipingSegment`), the `OffPageConnector`, `Nozzle`/`ConnectionNode` feature identity, `Document`, and the boundary role. Everything genuinely hard is the **rule layer**, which is the point of the semantic thesis.

**Built and reasoner-verified:** `pidsys_extension.ttl` is consistent under HermiT against the v4.2 core. Its internal design is authoritative in `workstream_2_precomm_ontology_scoping.md`.

### 6.2 Open decisions carried by this spec

1. **LIS-14 version reconciliation** — this product now baselines on v4.2; confirm an exact version match with the schedule-IDO prototype before any unification (§1.2).
2. **PLM library version pin** — adopt v0.9.0 but pin it; it is `incomplete` and imports `document`/`process` (§1.3). Confirm its upper anchor against the v4.2 core when wiring the crosswalk.
3. **Cause-C manual mappings** — curate the ~102 RDS-with-no-PLM-target codes, boundary-forming first (§4.4, §4.6).
4. **PostProc alias table** — build and review the synonym set that lifts Project-B coverage (§4.4, §4.6).
5. **Boundary-forming review gate** — operationalise the human review of closeMatch/label targets for boundary classes (§4.5).
6. **Document-type crosswalk target** — is CFIHOS the alignment target for `pidsys:hasDocumentType` concepts, or a T.EN document-type master? Decides the real URIs in `document_types_refdata.ttl` (§5, currently `pending`).

### 6.3 Next artifacts (workstreams)

- **Workstream 1.5** — the two crosswalk files (§4.6), boundary-forming prioritised and review-flagged.
- **Workstream 2** — the `pidsys:` extension ontology (§6.1). **Built and reasoner-verified;** remaining work is code reconciliation, not ontology correctness.

### 6.4 Scope split — mapping spec vs built ontology

This spec maps every [MD] object *in principle*. The built `pidsys_extension.ttl` deliberately defers part of that scope:

- **Deferred from the built ontology (this phase):** Instrument (§2.5), Actuator (§2.6), Instrument Function (§2.7), Signal (§2.10) — the instrumentation cluster. These are decisions, not gaps; the mapping rows above record how they *would* map when the instrumentation scope is opened. The two that need a modelling call (Instrument Function: functional class; Signal: connectivity-variant vs edge) are deferred with the cluster.
- **Reconcile the code** — `vocab.py` / `rdf_mapper.py` / `walk.py` do not yet emit the model (function-realization membership; functional-only segments; the v4.2 anchors). The ontology leads; the code catches up, or records where it intentionally lags.
