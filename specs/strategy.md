# Implementing `pidsys` on a Medallion + RDF/IDO + Jena-Rules Architecture
 
**Companion to** `data_product_architecture_note.md` (Section 5 in particular), `algorithm_spec.md`, `systemization_spec.md`, `data_specification.md`.
**Subject:** how the existing `pidsys` / `pidtool` / `bppidsys` work maps onto the proposed strategy — Bronze/Silver/Gold Delta Lake, Great Expectations, RDF/IDO on Jena Fuseki, and a Jena-Rules inference engine for fluid-network boundaries.
**Fidelity:** the real `pidsys` rules are mapped in full — where the strategy's simplified "walk the fluid, pass check valves, stop at gate/globe valves" understates what the PoC actually does, that gap is called out, because it is the single biggest technical risk in the port.
**Date:** 2026-08-27.
 
---
 
## 0. Verdict up front
 
This strategy **is** the RDF/Jena branch the architecture note already recommends for the semantic thesis (§5: *"if the goal is the thesis — rules applied on data, no hardcode, IDO-aligned, provenance-traceable → RDF/Jena"*). Adopting it is coherent with the program, not a detour.
 
The important framing: **`pidsys` has already built the hard 80% that the strategy treats as a black box, and the strategy adds the three things `pidsys` genuinely lacks.**
 
What `pidsys` already has, and the strategy quietly assumes is easy:
 
- A **topology-first reconstruction** that repairs the incomplete raw connectivity (valves 0/35 → 35/35 on the steam sheet). Without it, *any* boundary-walking engine — Jena Rules included — walks a graph that is missing ~97% of its inline valves and produces nonsense. This is not "shred the XML and pivot"; it is geometry-dependent computation.
- The **actual systemization rules**, validated at ~97% source agreement, that are far richer than "stop at a gate valve": self-owning flare and steam/condensate networks, three directional guards, relief-device attribution, equipment anchoring, role-based boundaries, and a compute-only validation oracle.
- A **reference-data-driven** rule surface (fluids, boundary roles, tagging, Unit→SUP) — the precondition for any credible "rules as data" claim.
What the strategy correctly adds, and `pidsys` does **not** have today:
 
1. **Persistence + an immutable audit trail** (Bronze/Delta) — today it is a batch script over an in-memory graph (architecture note §4: *"no persistence, no query surface"*).
2. **Formal, gated data quality** (Great Expectations) — today the quality checks exist but as advisory `flags`, not as enforced expectations with pass/fail gates.
3. **Bi-temporal history + a semantic serving layer** (Gold/RDF/IDO/SPARQL) — today there is no history and no query surface; the "Semantic/RDF output" is explicitly deferred as the Gold layer (algorithm_spec §12).
So the correct mental model is **not** "reimplement `pidsys` in Spark + Jena." It is: **wrap the existing reconstruction and rules in a persisted, versioned, quality-gated data platform, and lift the rule surface — not the graph walk — into RDF/IDO where it earns provenance and interoperability.** The trap to avoid is the one the architecture note already named: choosing a stack because a walk is awkward in it, then losing the semantic layer that is the whole point.
 
---
 
## 1. Layer-by-layer map: strategy → existing `pidsys` asset
 
| Strategy layer / component | Exists in `pidsys` today? | The concrete asset | Net-new work |
|---|---|---|---|
| **Bronze** — raw XML as-is, append-only, immutable | No | `run_batch.py` reads a folder and releases each DOM; nothing is retained | Delta append + ingestion metadata (hash, size, source mtime) — data_specification §metadata already *reserves* exactly these fields |
| **Silver — Parse / shred GenericAttribute → columns** | **Yes, fully** | `master_data.ga()` (the one canonical reader), `extract.py`-style typed mapping into `PipingSegment`/`Component`/`Equipment`, `TaggingConvention` decode | Re-house as a Spark stage; keep the parser in Python (see §3.2 — it is not a row-wise pivot) |
| **Silver — topology reconstruction** | **Yes, and it is the crown jewel** | `pidtool/pipeline.py` (skeleton from shared endpoints, inline valve order by centerline arc-length, directed graph), wrapped by `pidsys/reconstructed.py`; `bppidsys` mirror for ISO-15926 | Package as a per-drawing UDF; **do not** try to express in Spark SQL |
| **Silver — Data Quality (Great Expectations)** | Partially, as advisory flags | Ghost-filter, `compose(decode(tag))==tag` round-trip, prefix integrity, unknown-fluid flag, unmatched-OPC flag, dangling-end checks (data_specification §4.2, §metadata) | Convert each flag into a GX expectation with an explicit gate/quarantine policy |
| **Silver — CDC (MD5 new/modified/deleted)** | No | — | New. Hash at object grain, not whole-file (see §3.4) |
| **Gold — bi-temporal `validFrom`/`validTo`** | No | data_specification §metadata reserves ingestion timestamp/hash — the hook exists | New; requires a **two-axis** temporal design (§4) the strategy under-specifies |
| **Semantic — map to IDO via `rdflib`** | Deferred by design | The typed canonical schema (`Document → ProcessUnit → PipelineSystem → Subline → PipingSegment → Component`, reified `Connection` with Source/Derived flag, `Equipment`/`Nozzle`) is *built to be the store-binding contract* (architecture note §2) | New mapper; needs an honest IDO alignment (§5 — the strategy's term names are not verbatim IDO) |
| **Semantic — Triplestore (Fuseki/GraphDB)** | No (but the sibling IDO prototype already runs Fuseki in Docker) | — | Stand up Fuseki; load master data + reference data + oracle as separate named graphs |
| **Inference — Jena Rules propagate fluid, stop at boundary** | **Yes — but far richer than the strategy states** | `walk.py` (self-owning fluids, C1/C2 consumer trace, 3 directional guards, relief attribution), `refdata.load_boundary_sets` (isolation/positive/relief/trap roles) | Re-expressing this faithfully in a forward-chaining engine is the **main risk** (§6) |
| **Query — SPARQL** | No | Reporting is Python (`systems_export.py`, `rule_trace.py`) | New; the rule-trace explainability already exists to port into SPARQL/PROV |
| **Compute-only validation oracle** | **Yes — protect this** | The walk never reads `Z_TurnOverSystemNumber`; it scores against it. ~97% agreement | Keep the oracle in a separate named graph; never let rules read it |
 
**Reading of the table:** the strategy's Bronze, CDC, bi-temporal Gold, triplestore, and SPARQL columns are genuinely new. Everything in Silver-parse, reconstruction, data-quality logic, the rule content, and the validation discipline **already exists and is validated** — the work there is re-housing and formalising, not inventing.
 
---
 
## 2. The one thing to get right: reconstruction is upstream of everything
 
The strategy's Silver layer says *"Uses PySpark to shred the XML and dynamically pivot the generic `<GenericAttribute>` tags into distinct, queryable columns."* That is correct and easy for the **attribute** side. It is silent on **connectivity**, and connectivity is where this project lives.
 
From the `pidsys` README, measured on real data:
 
> The raw DEXPI `<Connection>` records are **incomplete** — an SPPID adapter wires only each segment's two endpoints, so inline valves and most cross-segment links are missing (**as little as 1/34 valves** in a sample). A connectivity diagnostic on the raw graph therefore *understates* the plant's connectivity and wrongly suggests distributed-fluid boundary-walking is infeasible.
 
The reconstruction repairs this **topology-first**: the skeleton comes from components that share a segment endpoint (deterministic, geometry-independent), and geometry is used *only* to order the inline valves inside a segment by projecting their coordinates onto the segment centerline (arc length). Result: valves 0/35 → **35/35**, distributed fluids fully connect (AG 135/135, N 24/24, PW 36/36), and the graph becomes **directed** (flow sense along each centerline) — which the three directional guards then depend on.
 
**Consequences for the medallion design:**
 
- Reconstruction belongs in **Silver**, before data quality on connectivity can mean anything (an "orphan node" check against raw `<Connection>` would fail every inline valve — a false alarm). GX connectivity expectations must run on the **reconstructed** graph.
- It is **per-drawing, stateful, and geometry-dependent** — it does not decompose into a row-wise Spark transformation. Run it as a Python function per drawing (`mapInPandas` / a UDF over the file set, or simply Python orchestrated by Spark for scale-out over *files*, not rows). Spark buys you parallelism over hundreds of drawings, not a reformulation of the algorithm.
- The **Derived vs Source** distinction on every reconstructed edge (data_specification §2.12) is not cosmetic here: the strategy's whole premise is walking connectivity, and most of that connectivity is *derived*. When those edges become RDF triples they must be tagged with provenance (§5), or the graph silently presents inferred topology as source truth.
**Net:** treat `pidtool`/`bppidsys` as a fixed, validated Silver component. Porting it to Spark SQL would be re-deriving a solved, hard problem — and getting it wrong would quietly poison every layer above.
 
---
 
## 3. Bronze & Silver in detail
 
### 3.1 Bronze — raw ingestion
 
Straightforward and genuinely additive. Land each `*.xml` (DEXPI and PostProc) as-is in an append-only Delta table, one row per file-version, with the ingestion metadata `data_specification.md` already reserves: **ingestion timestamp, content hash, file size, source last-modified time**, plus `OriginatingSystem` so the format adapter can be chosen downstream exactly as `reconstructed._adapter_for` chooses it today (PostProc detected by a `PipingNetworkSegment` TagName; DEXPI otherwise). Nothing about the current parser changes; Bronze just gives it a durable, replayable source and the immutable audit trail the PoC lacks.
 
### 3.2 Silver — parse + reconstruct
 
Two sub-stages, in this order:
 
1. **Attribute shred** — this *is* the strategy's pivot, and `pidsys` already does it precisely: `master_data.ga()` is the single `GenericAttribute` name/value reader, and the typed mapping already lifts `ItemTag`, `OperFluidCode`, `PipingMaterialsClass`, `ComponentClass`, `SP_PartNo`, `Z_TurnOverSystemNumber`, `SubsystemNo`, `UnitCode` into columns. Keep this logic verbatim; wrap it in a Spark stage that emits the component/segment/equipment/connection tables.
2. **Topology reconstruction** — run `pidtool`/`bppidsys` per drawing (§2). Emit the reconstructed **undirected** and **directed** adjacency as edge tables, each edge carrying the Source/Derived flag.
### 3.3 Silver — Great Expectations (formalise existing flags)
 
The strategy names two example expectations (mandatory `ItemTag`, positive pressures). `pidsys` already contains a richer, *spec-referenced* quality set — the work is to promote these from advisory `flags` to gated GX expectations:
 
| Existing `pidsys` / spec check | GX expectation shape | Gate policy |
|---|---|---|
| Ghost equipment — keep only tagged + ≥1 nozzle (algorithm §3.2, `equipment_is_real`) | `expect_column_pair`... tagged ∧ has-nozzle | Untagged+nozzle-less → drop (as today); tagged+nozzle-less → quarantine + flag, don't fail the batch |
| `ItemTag` present on mandatory objects | `expect_column_values_to_not_be_null` | Flag, retain (tag is the grouping key) |
| Round-trip `compose(decode(tag)) == tag` (data_specification §4.2) | custom expectation over tag columns | Flag convention drift; retain |
| Prefix integrity — segment Tag begins with subline Tag begins with Pipeline-System Tag (§2.2a/§2.3) | custom cross-column | Flag |
| Unknown fluid code vs `Fluid` catalogue | `expect_column_values_to_be_in_set` (set = catalogue) | Flag, **retain** — the code is still the grouping key |
| Unmatched OPC (`PairedDrawingNumber` with no mate loaded) | connectivity expectation on the assembled graph | **Not a failure** — open boundary, flag (system continues off-set) |
| Dangling ends / orphan nodes | connectivity expectation on the **reconstructed** graph | Flag |
| Positive pressures / numeric sanity (strategy's example) | `expect_column_values_to_be_between` | Flag |
 
The critical GX design point inherited from the specs: **quality issues raise flags, they do not abort** (algorithm_spec §11 — *"each stage raises flags rather than aborting, so a whole-drawing result is always produced with problems surfaced"*). GX's default "fail the run" posture must be tuned to a **quarantine-and-annotate** posture for most expectations, or you will lose the PoC's honest-partial-result behaviour, which is a feature, not a bug.
 
### 3.4 Silver — CDC (genuinely new; one design caution)
 
The strategy proposes MD5 hashing to isolate New/Modified/Deleted records against the current state. Sound, with one caution the DEXPI/PostProc grain forces:
 
- **Hash at object grain, not file grain.** A single symbol move re-exports the whole drawing XML, so a whole-file hash marks *every* component on the sheet as "modified" and floods CDC with false deltas. Hash a canonical projection of each object — e.g. `(ItemTag, ComponentClass, OperFluidCode, PipingMaterialsClass, normalized-connectivity)` per component/segment — so the delta reflects engineering change, not re-export churn. This mirrors how `pidsys` already keys on `ItemTag`/stable internal IDs rather than volatile element IDs.
- **Deletes need care with reconstruction.** A "deleted" inline valve changes topology and therefore boundaries; CDC must recompute the affected drawing's reconstruction, not just tombstone a row. Scope the recompute to the changed drawing(s) plus their OPC-mated neighbours (the assembly already tracks these pairings).
---
 
## 4. Gold — bi-temporal, and where the strategy under-specifies it
 
The strategy says: *"Tracks when data is valid by applying `ido:validFrom` and `ido:validTo` timestamps."* Two problems to fix before building:
 
**(a) `ido:validFrom`/`ido:validTo` are not verbatim IDO vocabulary.** IDO (ISO 23726-3) is a foundational upper ontology; it does not ship ready-made `validFrom`/`validTo` datatype properties. Model validity with explicit project predicates (e.g. `pidsys:validFrom`/`pidsys:validTo`) or with IDO's temporal-parts pattern — but **decide and document it**, don't assume the term exists. (More on IDO alignment in §5.)
 
**(b) "Bi-temporal" needs its two axes named, or it collapses into one.** Bi-temporal means two independent time axes, and this domain genuinely has both:
 
- **Valid time (engineering reality):** when the plant configuration a record describes was true — driven by **drawing revision**. A valve added at Rev C is valid-from Rev C's issue date.
- **Transaction/system time (audit):** when *the platform* learned it — driven by **ingestion** (the Bronze timestamp/hash).
These differ constantly in EPC reality: a Rev C issued three weeks ago but ingested today has valid-time three weeks back and transaction-time now. The strategy's single `validFrom/validTo` pair captures only one axis. Recommendation: carry **both** — transaction time comes free from Bronze metadata; valid time comes from the drawing revision (a field to add to the Bronze metadata capture). The Gold "current truth" view is then `transaction_time = now ∧ valid_time = latest`, and history is queryable on either axis ("what did we believe last month" vs "what was true at Rev B").
 
**(c) Grain.** Version at the **object** grain (component/segment/equipment/connection), aligned with the CDC grain (§3.4). "Never delete, close the interval" (`validTo := change_time`, open a new row) is exactly the append-only Delta pattern, and it aligns with the reified-Connection model: a removed edge closes its interval rather than vanishing, preserving the audit trail the reconstruction's Derived edges especially need.
 
---
 
## 5. Semantic layer — mapping the canonical schema to RDF/IDO honestly
 
The architecture note already positions the typed schema as the **store-binding contract** (§2: the typed objects *"remain as the published contract for the Phase-2 store binding"*). That contract maps cleanly to RDF. The honest caveats are about IDO's actual vocabulary.
 
**What maps cleanly (`rdflib` triple emission from the canonical objects):**
 
| Canonical `pidsys` object | RDF individual | Relations |
|---|---|---|
| `Document` (drawing) | a document node | scopes/provenances every component (data_specification §2.1) |
| `ProcessUnit` → `StartUpPackage` | classification nodes | the `SUP = UnitSUP[ProcessUnit]` **direct join** is exactly an ontology classification (architecture note §2a) |
| `PipelineSystem → Subline → PipingSegment → PipingComponent` | containment individuals | `hasPart`/`partOf` chain |
| `Equipment` + `Nozzle` | physical-object + port individuals | equipment connects to the network via nozzles |
| `Connection` (reified edge, Source/Derived) | **a first-class node, not a bare triple** | its Source/Derived flag → provenance (see below) |
| `OperFluidCode`, `PipingMaterialsClass`, boundary role | typed properties / SKOS concepts | reference data as vocabulary |
 
**Three correctness notes on the IDO alignment (this is where to be careful):**
 
1. **`ido:FunctionalObject` is a placeholder, not a confirmed IDO term.** IDO's core is foundational (physical objects, aspects, activities, connectivity and composition relations, class-of-individual patterns). Domain classes like "valve" or "functional object" come from a **domain reference-data library aligned to IDO** — the POSC Caesar RDL / ISO 15926-4 lineage the sibling IDO prototype already reaches via the PCA RDL API — not from IDO itself. Model piping components as instances of **domain classes that are `rdfs:subClassOf` an IDO physical-object class**, and resolve the component-class vocabulary (GateValve, CheckValve, …) to RDL URIs (the IDO prototype already has a PIM workflow doing exactly this tag→RDL-URI resolution). Don't hardcode `ido:FunctionalObject` and assume it dereferences.
2. **The reified `Connection` is what makes `isConnectedTo` provenance-safe.** Because most reconstructed edges are **Derived**, emit connectivity as reified Connection nodes (or RDF-star quoted triples) carrying `derived: true/false`, not as bare `a ido:isConnectedTo b` triples. This is precisely the "property-rich edges" case the architecture note says RDF-star now handles. Otherwise the graph asserts inferred topology with the same authority as source topology — and the whole PoC thesis is provenance-traceability.
3. **Direction must be materialised as triples.** The three directional guards (§6) need flow sense *in the graph*. Emit the reconstructed directed edges as a `pidsys:flowsTo` property (distinct from the undirected `isConnectedTo`), or the Jena rules cannot express "flow runs *into* the flare."
**Named-graph layout (preserves the compute-only discipline):**
 
- `graph:masterdata` — reconstructed plant (components, reified connections, flow direction).
- `graph:refdata` — Fluid catalogue, Boundary roles, TaggingConvention, UnitSUP, as SKOS/OWL. **This is the "rules as data" surface.**
- `graph:oracle` — the source `Z_TurnOverSystemNumber`/`SubsystemNo`. **Rules must never read this graph**; it exists only to score results, exactly as `walk.py` refuses to read it today. Keeping it in a separate, rule-invisible named graph is how you enforce the ~97%-agreement discipline structurally rather than by convention.
- `graph:results` / PROV — computed systems with their provenance (`derivedFrom` the rule that fired). `rule_trace.py` already builds this explanation; it becomes PROV triples.
---
 
## 6. Inference — the real boundary logic vs. "stop at a gate valve"
 
This is the section that matters most, and where full fidelity changes the plan.
 
**The strategy's stated rule:** *"the reasoner automatically walks the piping topology, allowing fluid codes to pass through components like Check Valves, but completely halts network propagation the moment it encounters an isolation boundary item, such as a Gate Valve or Globe Valve."*
 
**What is correct in that:** CheckValve is deliberately **not** boundary-forming, and Gate/Globe (and Butterfly) valves are isolation boundaries. `refdata._FALLBACK_BOUNDARY` confirms exactly this, and notes *"CheckValve is deliberately NOT boundary-forming (project decision)."* So the strategy's instinct is right as far as it goes.
 
**What it under-states — the parts a naive "flood fill, stop at gate valve" gets wrong:**
 
1. **Boundaries are four reference-data-driven roles, not one valve list.** `load_boundary_sets` reads a `Boundary` sheet into **isolation** (Gate/Globe/Butterfly), **positive** (PipeFlangeSpacer), **relief** (SafetyValveOrFitting/Reliefdevices), and **trap** (SteamTrap). Onboarding a project is editing that sheet, not the rule. A Jena ruleset must read boundary membership from `graph:refdata`, not bake valve classes into rule bodies — or the "rules as data" thesis is lost at the first rule.
2. **Grouping is not "propagate the fluid code" — it is trace-to-consumer.** `walk.py` doesn't flood a fluid code outward and stop at valves. For each fluid it finds connected same-fluid fragments, then **classifies each fragment by the consumer it reaches**: `C1` = a tagged equipment nozzle (join that equipment's system); `C2` = a different-fluid junction **with a boundary component at the transition** (join the served system); `imprecise` = a bare tie-in with no boundary; `off-document` / `dead-ended` = unresolved. "Stop at a valve" is only the *bounding* step; the *assignment* step is reaching a consumer. A rule engine has to express both.
3. **Self-owning fluids break the flood-fill model entirely.** Flare/relief fluids (Category `Flare`) and Steam/Condensate utilities (Subcategory Steam/Condensate) form **their own** systems and are **not traced to a consumer**. A naive propagate-and-stop engine would walk a steam header straight into the fired heater it terminates at and mislabel it — the exact error `pidsys` calls its *"single highest-value grouping fix."* The ruleset must classify a fluid as self-owning *from the catalogue* and give it its own network, before any consumer trace runs.
4. **Three guards are directional — pure undirected forward-chaining cannot express them.** All three read the reconstructed From/To sense:
   - **Flare guard:** if a process fragment reaches a flare neighbour and flow runs *process → flare*, the flare is a discharge **sink**, not a consumer — don't attach.
   - **Directional consumer guard:** a different-fluid neighbour is a consumer only if the fragment flows *toward* it; if flow runs *neighbour → fragment*, the neighbour is a **supply tying in** (upstream), not a consumer. (Nitrogen teeing into a process header commissions with the header; the header does not commission with nitrogen.)
   - **Relief-device attribution:** a relief valve commissions with the side it **protects** (flow *into* the valve), not the discharge it vents to (flow *out*).
   These require `pidsys:flowsTo` triples in the graph (§5, note 3) and rule bodies that test flow direction. Model them, and Jena Rules can do it; forget the direction triples and no amount of rule authoring will recover these — you'd regress on precisely the cases the guards were built to fix.
5. **"Halt propagation at a boundary" is non-monotonic and is the hard part for a forward-chaining reasoner.** Forward-chaining rules and SPARQL property paths are natural at **transitive closure** (`isConnectedTo` is transitive → reachable set) but awkward at **transitive closure that must *stop* at a node with a property** ("reachable *without crossing* a boundary component"). SPARQL 1.1 property paths cannot exclude an intermediate node by its class; expressing "same-fluid connectivity that halts at an isolation valve" needs either recursive Jena rules with negation-as-failure over an explicit "blocked" predicate, or a bounded-recursion helper. This is doable but is the genuinely tricky rule to author and to keep performant on a full-plant graph — and it is the same "SPARQL struggles with connected components" caveat the architecture note §5 raises by name.
**Recommendation — a hybrid that keeps the semantics without fighting the reasoner:**
 
- Put **classification and local, node-local rules in Jena/OWL/SHACL**, read from `graph:refdata`: fluid → {flare | steam/condensate | process | utility}; component → boundary role; equipment anchoring; the three directional guards (each is a *local* test on a node and its neighbours + flow direction — a good fit for rules); relief attribution. These are where "rules as data + IDO + provenance" genuinely shines and where the thesis is proven.
- Keep the **global reachability / connected-fragment partition** where it already works and is validated — the Python reconstruction/walk, or a projected LPG (Neo4j GDS) if you want native fast traversal. Materialise the *result* (which components form which system, and *which rule fired*) back into `graph:results` as PROV.
- This is exactly the architecture note's **"RDF says what things mean and which rules apply; the LPG computes fast"** layering, and it sidesteps the note's stated trap (picking Neo4j for the walk and *losing* the semantic layer) by keeping RDF/IDO as the system-of-record and rule surface.
Doing it this way, the Jena-Rules demonstration is honest: it shows the boundary **philosophy** applied declaratively over data (which is the point), rather than pretending a forward-chainer is the fastest connected-components engine (which it is not).
 
**Also inherit these `pidsys` allocation rules** (algorithm §9.1) or the results will disagree with the validated PoC: boundary valve/blind → higher-priority system; steam trap → steam side; sampling connection → upstream; flare/drain interface valves → the flare/drain side; vessel/column instruments → equipment system. And the **fragment-merge by stable internal ID** (§9.5) — one system arrives as many connected fragments and they must be unioned on `(SUP, fluid)` or `(SUP, anchor-equipment)`, or SPARQL will report dozens of fragment-systems instead of the real handful.
 
---
 
## 7. Reusable vs. net-new — the honest split
 
**Reuse essentially unchanged (validated, don't rebuild):**
 
- `pidtool` / `bppidsys` reconstruction — the topology-first graph and its directed edges.
- `master_data.py` — the canonical schema, the one `ga()` reader, tag decode/compose, ghost filter.
- `refdata.py` — fluid sets and the four boundary roles from the workbook.
- The **rule content** in `walk.py` (self-owning fluids, C1/C2 trace, three guards, relief attribution) — as the *specification* the Jena rules must reproduce, and as the *reference implementation* to validate them against.
- The **validation oracle** and the ~97% figure — the cross-check for whatever the new stack computes.
- `rule_trace.py` / `systems_export.py` — the explainability that becomes PROV/SPARQL.
- The reference-data externalisation (Fluid / Boundary / TaggingConvention / UnitSUP sheets) — these *are* the rules-as-data surface; they load into `graph:refdata`.
**Net-new (the strategy's real contribution):**
 
- Bronze Delta ingestion + ingestion metadata capture.
- GX suites (promoting flags to gated expectations with quarantine policy).
- Object-grain CDC with hashing.
- Bi-temporal Gold with **two** time axes (§4).
- `rdflib` mapper from canonical objects → RDF/IDO, with reified Connections, provenance flags, and materialised flow direction.
- Fuseki setup + named-graph layout.
- Jena rules for the classification/local rules; SPARQL query surface.
- The hybrid boundary between rule-based classification and compute-based partition (§6).
**Do NOT rebuild in the new stack:** the reconstruction (as Spark SQL), the connected-fragment walk (as pure forward-chaining), or the attribute parser (as anything other than the existing `ga()` logic).
 
---
 
## 8. Data product as a reusable rule platform (the horizontal-reuse thesis)
 
The layers above answer "how do we implement systemization on this stack." This section answers the larger question the program actually turns on: **once the plant is modelled once, how many rule-driven business problems can consume that same data product without re-modelling anything — and how does the engineering know-how those problems encode get standardised from project to project?** This is where the declarative/semantic choice stops being a stylistic preference and becomes the load-bearing decision.
 
### 8.1 Model the plant once; apply many rule packages to it
 
The entire value proposition rests on one asset: a canonical, **use-case-neutral** model of the plant — objects, connectivity, attributes, equipment, materials class, spanned documents — that every downstream problem reads without re-extracting or re-modelling. `pidsys` already designed its schema to be exactly this contract (the reified `Connection`, the `ProcessUnit → PipelineSystem → Subline → Segment → Component` hierarchy, built as *"the published contract for the Phase-2 store binding"*).
 
Systemization is then just the **first** rule package on top of it. Test Packages is a second. ITR/check-sheet scoping, punch-list scoping, MC certificates, isolation studies, C&E consistency — each is another rule package over the **same facts**, each drawing on the same components, connectivity, boundaries, equipment and materials class. The marginal cost of business problem *N+1* is "author its rules and its reference data," not "turn P&IDs into a trustworthy graph again." That is where the man-hour curve bends: the expensive part — reconstructing and validating the plant graph — is paid **once** and amortised across every application.
 
### 8.2 Why RDF/Jena wins *for this goal* specifically
 
This is the goal on which RDF/IDO beats both hand-coded Python and an LPG such as Neo4j, and the architecture note already reached it (RDF is strongest at *"semantics, rules-as-data, provenance, interoperability… onboarding by swapping a vocabulary"*). The decisive property: **when the rules are data** — SKOS reference sets, OWL class hierarchies, SHACL/Jena rule files — the organisational know-how becomes an explicit, inspectable, versioned, ownable artefact. It stops living in a senior engineer's head or buried in procedural branches, and starts living in a governed repository that the **discipline** owns and each **project** instantiates. That is the literal meaning of "standardise the know-how project to project": the rule base *is* the standard, carried forward as an asset, run **identically** across projects. Two engineers — or two projects — no longer systemize the same plant three different ways, and the consistency is where the risk reduction (auditable, provenance-backed assignments vs. manual mark-up) comes from.
 
### 8.3 The design decision that makes or breaks it: protect model neutrality
 
The single biggest threat to a reusable data product is letting the first use case's concepts leak into the shared layer — modelling *"systemization data"* instead of *"plant data."* The specs already enforce the right discipline: `data_specification.md` is explicit that Pipeline-System membership is a **source structural fact, not a commissioning grouping**, and that master data stays distinct from computed grouping. Hold that line hard. The shared TBox is IDO + domain plant classes + connectivity; **each rule package brings its own extension classes and reference data**. Test Packages adds a `TestContainmentBoundary` class and a class→test-pressure table; systemization adds its boundary roles and fluid catalogue. They meet only at the shared, neutral facts. Get that layering right and the framework generalises; blur it and you have built a systemization tool with delusions of being a platform.
 
### 8.4 The reusable inference primitive — build it once
 
There is a single graph-reasoning pattern hiding across these use cases. Systemization partitions the connectivity graph at isolation valves + class breaks; Test Packages partitions it at rating breaks + test-containment devices; an isolation study partitions it at whatever can be positively isolated. That is **one parameterisable primitive** — *partition connectivity at a role-defined cut-set, with these directional refinements* — instantiated with a different cut-set per problem. Express it generically over ontology terms and the core graph-reasoning capability becomes shared infrastructure, not per-application code. You are not writing *N* boundary-walkers; you are writing one and configuring it *N* times. This is the deepest form of the reuse the program is after, and the strongest technical argument for the declarative approach.
 
### 8.5 Two honest boundaries on the ambition
 
**Not every business problem is a rule-application problem.** The declarative layer is excellent at *classification, grouping, boundary/cut, eligibility, and consistency-checking* — the judgment-heavy mark-up that today consumes precommissioning man-hours. It is **not a solver**: commissioning-sequence optimisation, AWP scheduling, and volume/weld calculations are optimisation and numerical problems that should **read** the same data product but run in their own tooling. Framing each candidate use case as *"rule application over the graph"* (great fit) vs. *"optimisation/simulation over the graph"* (different tool, same data) keeps the platform promise credible.
 
**The hard work is knowledge elicitation, not technology.** The value and the risk both live in getting senior engineers to externalise tacit rules into explicit, reviewed ones. The validation methodology generalises perfectly as the trust mechanism: for each new use case, compute first-principles, then score against known-good manual output as an answer key **before** anyone relies on it — exactly as systemization earned its ~97%. That is how you de-risk automating a manual process: you *measure* per-rule agreement before retiring the manual step, so standardisation is earned, not asserted.
 
### 8.6 Where the cost and risk reduction actually come from
 
The ROI compounds in three layers, and it is worth stating them separately because they land on different budgets:
 
- **Within-project automation** — the first application turns a manual mark-up into a reviewed first pass (the engineer approves exceptions instead of doing everything by hand). This is the Customer-Journey man-hour target.
- **Cross-application reuse** — because the plant is modelled once, the marginal cost of each additional use case drops; the second and third applications are mostly rule authoring.
- **Cross-project standardisation** — the rule base is an asset carried forward, so project *N+1* starts from a validated, governed body of know-how, not a blank sheet — and runs it identically, which is the consistency/auditability risk reduction.
This is a *"data product as platform"* story, and on the axis that actually matters — how many governed business problems one plant model can serve, and how faithfully those rules carry project to project — the semantic/declarative approach is not a trade-off. It is the only one of the three options (hand-coded, LPG, RDF) that treats **the rules themselves** as the reusable, standardisable product.
 
---
 
## 9. Risks & mitigations
 
1. **Boundary logic fidelity (highest).** "Stop at a gate valve" is a fraction of the real rule set. Mitigation: treat `walk.py` as the acceptance spec; port self-owning fluids, the three directional guards, relief attribution, allocation tie-breaks, and fragment-merge — and gate the new engine against the ~97% oracle before trusting it.
2. **Reconstruction mis-scoped into Spark.** Attempting the topology repair as row-wise Spark loses the geometry step and re-opens a solved problem. Mitigation: reconstruction is a per-drawing Python UDF; Spark parallelises over *files*.
3. **GX abort-on-fail vs. honest-partial-result.** Default GX gating would kill the whole-drawing-always-produced behaviour. Mitigation: quarantine-and-flag posture for most expectations; reserve hard failure for truly unusable inputs.
4. **IDO term over-claim.** `ido:FunctionalObject`/`ido:validFrom`/`ido:validTo` are not confirmed IDO vocabulary. Mitigation: subclass domain classes under IDO's physical-object hierarchy, resolve component classes to RDL URIs (reuse the IDO prototype's PIM workflow), and define validity predicates explicitly.
5. **Provenance loss on derived edges.** Most connectivity is *Derived*; bare `isConnectedTo` triples would assert inferred topology as fact. Mitigation: reified Connections / RDF-star with a `derived` flag.
6. **Oracle leakage.** If any rule reads `Z_TurnOverSystemNumber`, the 97% figure becomes circular. Mitigation: oracle in a rule-invisible named graph; keep the compute-only discipline structural.
7. **Bi-temporal collapsing to one axis.** Mitigation: capture valid time (drawing revision) at Bronze alongside transaction time.
8. **CDC churn from whole-file re-export.** Mitigation: object-grain hashing (§3.4).
---
 
## 10. Suggested phasing (fits the architecture note's "prioritized next steps")
 
1. **Land persistence first, semantics second.** Bronze Delta + Silver (existing parser + reconstruction as UDFs) + GX suites. This alone converts the PoC from a batch script into a data product with an audit trail — the architecture note's #4 gap — without touching the rules.
2. **Bi-temporal Gold** over the object-grain CDC. Now history and "current truth" are queryable; still no RDF required.
3. **RDF/IDO projection of Gold** via `rdflib` into Fuseki, with the named-graph layout and reified Connections. Master data + reference data become queryable by SPARQL. Interoperability/provenance story is now real.
4. **Jena rules for classification + local/directional rules**, validated against the oracle; keep the global partition in Python/LPG. This is the "rules applied on data, no hardcode, IDO-aligned" demonstration — the thesis, proven, without pretending the reasoner is a graph-algorithms engine.
5. **Second consumer (Test Packages)** reuses the same Gold/RDF surface with a different cut rule (class/rating breaks + test-containment) — the architecture note's earn-your-data-product-status milestone.
Each phase is independently demoable to stakeholders, and none requires throwing away the validated core.
 
---
 
## 11. One-paragraph answer
 
Yes — this strategy is implementable, and it is the right one for the semantic thesis, because it is the RDF/Jena path the architecture note already recommends. But the framing that makes it succeed is that **`pidsys` has already built the hard, validated middle of it** — the topology reconstruction and the real systemization rules — while the strategy's genuine additions are persistence (Bronze/Delta), gated quality (GX), bi-temporal history, and a semantic serving/inference layer (RDF/IDO/Fuseki/SPARQL/Jena). Keep the reconstruction and the compute-only validation oracle intact; promote the existing quality flags to GX expectations rather than reinventing them; map the canonical schema to RDF with reified, provenance-tagged Connections and materialised flow direction; and — most important — port the *full* boundary logic (self-owning flare/steam networks, three directional guards, relief attribution, role-based boundaries), not the "stop at a gate valve" shorthand, using Jena rules for the declarative classification and a compute engine for the global partition. Do that, and you get the interoperability-and-provenance win the program is for, with the ~97% agreement figure still standing as the cross-check.