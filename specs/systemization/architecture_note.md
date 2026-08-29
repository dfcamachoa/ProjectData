# Data Product — Architecture Note & Roadmap
 
**Status:** Working note (companion to the three specs)
**Date:** 2026-08-25 (rev. 2026-08-27; SUP↔Process-Unit direct join)
**Scope:** How the `pidsys` prototype stands as a *data product*, the consolidation
(now complete), the reference-data externalization (now complete), the **materialized
master-data hierarchy** (Process Unit → Pipeline System → Subline → Segment → Component,
now in place), the persistence/semantic direction (RDF/IDO vs. Neo4j), and the
downstream use cases the product is meant to feed. Complements — does not replace —
`data_specification.md`, `systemization_spec.md`, and `algorithm_spec.md`.
 
---
 
## 1. Where the product stands
 
`pidsys` is a spec-driven, data-centric PoC that turns P&ID interoperability exports
into computed commissioning systems. Its strengths as a data product are real:
 
- **Spec-to-code traceability.** The code carries `[MD §x]`/`[SS §x]`/`[ALG §x]`
  references back to the governing rule, so the PoC doubles as a communication
  artifact for stakeholders and the dev team.
- **A working multi-format contract.** `pidtool` (DEXPI/Proteus) and `bppidsys`
  (ISO-15926 PostProc/SPPID) both emit the same reconstructed-graph shape, so the
  systemization core is format-independent — the interoperability promise made
  concrete, not a slide.
- **Topology-first reconstruction.** Raw DEXPI `<Connection>` records are
  incomplete; the reconstruction repairs the topology (skeleton from shared
  endpoints, inline order by centerline arc-length) and yields a directed graph.
- **Compute-only validation.** The walk never reads the source turnover data; that
  data is used *only* as an answer key, giving the ~97% source-agreement figure
  without leakage.
The honest gaps are documented in the specs (sub-systems, instrument layer, flare
interface hardware) and are not repeated here. This note covers the *architectural*
work that makes the product reusable and extensible.
 
## 2. Canonical master-data schema (consolidation — done)
 
**Finding.** Master data was represented twice: a typed, spec-faithful model
(`pidsys/model.py` + `extract.py`) that was *off the compute path*, and a generic
`attrs`-dict working record produced by the adapters that the runtime actually
uses. Business/tag logic (segment & Pipeline-System tag composition, a third copy
of the GenericAttribute reader, a second copy of the ghost filter) had begun
accreting as loose helpers in `reconstructed.py`, with the unit/sequence split
hardcoded as a 2/3-digit assumption.
 
**Action taken.** `pidsys/master_data.py` is now the single home for the typed
master-data objects:
 
- One home for the master-data business tags, with composition living on typed
  objects (`PipingSegment`, `Subline`, `PipelineSystem`, `Connection` with a
  `Derived`/`Source` flag — the reified edge `[MD §2.12]`; plus a `Document`
  container `[MD §2.1]`).
- **The tag grammar is data in BOTH directions, loaded from the workbook.** A
  `TaggingConvention` sheet carries the decode knobs and the composition templates;
  `from_refdata` reads both, verified authoritative. Project B renders
  `36"-PG-1415109-D341H-H`; project A renders its packed
  `AG362090006-44"(1C6AS)-S(45)(40)` (spec §2.3). Populated for **both** projects.
- **Three-level line hierarchy (project B):** Piping System → **Subline (Sub Piping
  System)** → Segment — `PG-14151` → `PG-1415109` → `36"-PG-1415109-D341H-H`
  (`[MD §2.2a]`). The Subline is stamped as traceability (`.subline`), not a
  grouping signal. Field names read clearly: `line_core` (Piping System body),
  `subline_core` (Subline body); old `pns_core`/`seg_core` kept as deprecated aliases.
- A **round-trip data-quality check** `compose(decode(tag)) == tag` `[MD §4.2]`.
- One `ga()` accessor and one ghost-filter definition (`equipment_is_real`).
- A drop-in `stamp_master_data(graph, dom)` replaced six helpers in
  `reconstructed.py` (net **−167 / +5** lines); output byte-identical. It now also
  stamps `.unit` (the tag-decoded Process Unit) onto every component.
- **`model.py` and `extract.py` removed.** The canonical `PipingSegment`/`Equipment`/
  `Connection` were enriched with the business/source fields (`src_turnover`,
  `src_subsystem`, `unit`, `system_id`, `materials_class` alias, connection `id`).
  `model.py` (the duplicate schema) and `extract.py` (an off-path DEXPI-only parser
  the runtime never used) were both deleted — **one authoritative schema, no shim,
  one extraction path** (`pidtool`/`bppidsys` → `reconstructed`). The typed objects
  (`Document`, `PipingComponent`, `Equipment`, `Nozzle`, `PipelineSystem`, `Subline`,
  `ProcessUnit`, `Connection`) remain as the published contract for the Phase-2 store
  binding and Test Packages; `PipingSegment` is additionally used live for tag composition.
- A contract test (`test_master_data.py`, **70 checks**) runs with **no drawing
  files**; an adapter-parity section activates once a sample drawing is present.
## 2a. Materialized master-data hierarchy (Process Unit stack — done)
 
The stamped strings (`.pns`, `.subline`, `.seg_tag`, `.unit`) told each consumer
*which line a component belongs to* but forced every consumer to re-derive the tree.
`pidsys/hierarchy.py` now folds them into the typed objects once:
 
```
StartUpPackage [MD §2.1b]  →  ProcessUnit [MD §2.1a]  →  PipelineSystem [MD §2.2]  →  Subline [MD §2.2a]  →  PipingSegment  →  Component
```
 
- **`build_hierarchy(graph, refdata_path=None)`** groups the stamped components into
  the typed line tree (read-only; recomputes nothing, mutates nothing), now **rooted
  in `StartUpPackage` objects** (`.process_units` beneath each). `iter_process_units`
  flattens to the Process-Unit level. The **Start-Up Package** [MD §2.1b] is the top
  grouping node and the apex shared with the commissioning hierarchy; unlike the
  levels below it, it is **not extracted from the P&ID** — the `SUP`/`UnitSUP`
  reference sheets provide it (its code, its description, and which Process Units it
  contains), a project input per SS §2.2. Process Units with no SUP fall under a
  `SUP??` placeholder package, flagged not dropped.
- **The Process Unit** is the grouping level *above* the line and the **hinge to the
  commissioning hierarchy**: `SUP = UnitSUP[ProcessUnit.code]` — a **direct join** on
  the Process Unit code.
- **SUP joins directly on the Process Unit (rev. 2026-08-27).** `UnitSUP` is now keyed
  by the **bare** unit code (`14`), the same code decoded from the line Tag, so SUP
  resolves without any drawing-number parsing. (Previously the join went through a
  drawing-derived unit keyed `A14`; the leading character was dropped from `UnitSUP`
  so the key equals the Process Unit.) The *tag unit* keys both the `Unit` catalogue
  (description/functional-block) and `UnitSUP` (SUP). The *drawing unit* (parsed from
  the P&ID number, `A14` → digit-run `14`) is kept only as a **cross-check** and as a
  **fallback** for components with no line Tag. `system_naming` matches: it resolves
  SUP from each member's tag `unit`, drawing as fallback.
- **Data-quality cross-checks as flags `[MD §4.2]`, never edits:** *tag-vs-drawing
  unit* mismatch, *multi-unit line* (a Pipeline System whose components decode to more
  than one unit), and *unknown / SUP-less unit*.
- **Line rollup for explainability (`rule_trace.build_line_rollup`).** The systemization
  result is rolled back up the master-data hierarchy: each `SublineRecord` records which
  computed commissioning system(s) its line landed in, flagging a **`split`** where a
  single master-data line was cut across systems `[SS §6.3]`. This closes the gap the
  Design-Thinking review named — the Subline was stamped for traceability but never
  read; now it is a review signal ("line PG-1415109 split across two systems — check
  the boundary"). Reporting columns (Piping System / Subline / Segment) already surface
  the strings in `systems_export.py`.
## 3. Reference-data externalization — done
 
The reusability thesis — *onboard a project by pointing at its `Reference_Data.xlsx`,
not by editing code* — now holds for every rule that drives systemization, including
the output naming. **All composition templates live in one `TaggingConvention`
sheet** (Decode params + Piping System / Subline / Segment / ItemTag + the output
CommissioningSystem name); the earlier stub `NamingConvention` sheet was folded in
and deleted.
 
| Rule | Status |
|---|---|
| Fluids (flare / steam-condensate by Category/Subcategory) | **Done** — `Fluid` sheet, `refdata.load_fluid_sets` |
| Tagging convention (decode knobs + composition templates for Piping System / Subline / Segment) | **Done** — `TaggingConvention` sheet, `from_refdata`; populated for project A (CFI) and B (base) |
| Boundary-forming classes (isolation / positive / relief / trap; CheckValve excluded) | **Done** — `Boundary` sheet, `refdata.load_boundary_sets`; `reconstructed.py` + `walk.py` read it (one source of truth), with a built-in fallback |
| Duplicated `VALVE_CLASSES` in both adapters | **Done** — single definition in `pidtool/pipeline.py`, imported by `bppidsys` |
| Process Unit → SUP / Unit catalogue (description, functional block) | **Done** — `UnitSUP` (keyed by the **bare** Process-Unit code) + `Unit` sheets, `hierarchy.load_unit_sup` / `load_unit_catalogue`; SUP is a **direct join** on the Process Unit, in both `hierarchy` and `system_naming` |
| **Commissioning-system name** (`{SUP}-{seq}-{code}` shape) | **Done** — same `TaggingConvention` sheet (Object `CommissioningSystem`), `system_naming.load_naming_template`; same template engine as the tags, with a built-in default. The fuller `SUPxx-Z(Z)-nnn-ppp_tttt` intent is expressible by editing the sheet once subsystems/modules exist |
| Flare / steam fallback code sets in `walk.py` | Fallback only; catalogue is authoritative (low priority) |
 
Fluids, tagging, boundaries, the Process-Unit/SUP catalogue, and the
commissioning-system name shape now live in reference data, not code — the
precondition for the declarative-rules demonstration (Section 5) to be credible.
 
## 4. Persistence & serving (the current gap)
 
Today the product is a batch script over an in-memory graph — no persistence, no
query surface. Every direction below needs a store; the choice is covered in
Section 5. Whatever store is chosen, the canonical schema (Section 2) is what binds
to it, so settling the schema first de-risks all of it.
 
## 5. Semantic / graph layer — RDF/IDO vs. Neo4j
 
The problem has three separable concerns, and no single store is best at all three:
 
1. **Topology/geometry reconstruction** — data engineering; stays procedural
   (Python), store-agnostic.
2. **Graph computation** (connected components, trace-to-consumer, pathfinding) —
   **Neo4j/LPG is strongest** (native traversal, GDS algorithms, Cypher reads close
   to the spec, excellent visual exploration for stakeholder demos).
3. **Semantics, rules-as-data, provenance, interoperability** — **RDF/IDO is
   strongest** (OWL subsumption + SHACL + stored SPARQL are literally data;
   named-graph/PROV provenance; ISO-15926/IDO alignment; onboarding by swapping a
   vocabulary). RDF-star removes the old "property-rich edges are ugly in RDF"
   objection.
**Recommended target: layered/polyglot.** RDF/IDO as the canonical system-of-record
(master data + reference data + rules + provenance) — the governance and
interoperability layer that makes the innovation program distinctive — with graph
computation projected to an LPG (Neo4j GDS) or kept in the current reconstruction.
RDF says *what things mean and which rules apply*; the LPG *computes fast*. The
Process-Unit hinge (Section 2a) maps cleanly onto IDO: `ProcessUnit` and its
`hasStartUpPackage` relation to the SUP is exactly the kind of classification the
ontology layer expresses declaratively.
 
**For the PoC, pick by the demo's job:** if the goal is the thesis (rules applied on
data, no hardcode, IDO-aligned, provenance-traceable) → **RDF/Jena** (already in
hand: SHACL, named-graph provenance, the `derive.py` derivation engine). If the goal
is a fast, explorable engine the team can build quickly → **Neo4j** — but note that
an LPG advances the *engine*, not the semantic/standards story, so keep an RDF/IDO
governance layer in the plan. In all cases, **keep the Python reconstruction and the
validation oracle** — the ~97% figure is the cross-check for whichever store is
adopted. The trap to avoid: choosing Neo4j because SPARQL struggles with connected
components, then quietly losing the semantic layer that is the point of the program.
 
## 6. Downstream use cases — from PoC to platform
 
The product earns "data product" status when a *second* application consumes it
without re-modeling the plant. **Test Packages is the ideal next use case**, because
it reuses almost the same primitives:
 
- Same graph, boundary components, materials class, equipment/nozzles, spanned
  documents, and the systemization output that *scopes* each pack. The **Subline**
  (`[MD §2.2a]`) is a ready-made line-level handle to scope and group test packs on,
  and the **line rollup** (Section 2a) already surfaces which lines a system spans.
- **Different cut rule:** test packs split at **piping class / rating breaks** (a
  hard split — you cannot pressure-test across two ratings) and at **test-containment
  boundaries** (a spade/blind/valve that can hold the medium — not every
  commissioning boundary qualifies). Note: the code carries materials class per
  segment but does **not yet cut on it** — Test Packages is exactly what justifies
  building the class-break detection that `systemization_spec.md` §5.2 already calls
  for. Build it once, both applications sharpen.
- **One new reference dataset:** a Piping-Materials-Class table extended with design
  pressure / test pressure / test medium (`data_specification.md` §3.9 already
  reserves the class list). Everything else is graph work that exists.
- **Reuse the validation methodology:** compute packs first-principles, then score
  against any source test-pack numbers as an answer key — as with systemization.
- **Scope honestly:** P&ID data supports the *boundary and scope definition* (the
  judgment-heavy part, where the ROI is); iso/weld-level detail (volumes, golden
  welds) is a later integration with isometric/line-list data.
Beyond test packs, the same product feeds ITR/check-sheet generation, punch-list
scoping, MC certificates, and commissioning-sequence/AWP alignment — each another
consumer of the one governed data product.
 
## 7. Prioritized next steps
 
1. **Land the consolidation** — apply the updated `master_data.py`, `hierarchy.py`,
   `rule_trace.py`, `__init__.py`, `reconstructed.py`, `walk.py`, `refdata.py`,
   `system_naming.py`, the reporting files, the two adapter files, and
   `test_master_data.py`, plus the updated `Reference_Data.xlsx`; **delete
   `pidsys/model.py` and `pidsys/extract.py`**.
   *(All ready; 70/70. The updated `reconstructed.py` supersedes the earlier patch.
   Consolidation, reference-data externalization, and the materialized Process-Unit
   hierarchy — Sections 2, 2a and 3 — are complete.)*
2. **Add a synthetic sample drawing** to `data/` so the adapter-parity test runs in CI.
3. **Surface the line rollup in the UI** — the `SublineRecord.split` flag is the
   highest-value review signal; show split lines against their Process Unit.
4. **Choose the Phase-2 store** per Section 5 and stand up a persistence/serving layer
   bound to the canonical schema; keep the Python validation oracle.
5. **Prototype Test Packages** as the second consumer: class-break detection +
   test-containment classification + the class→test-pressure reference table, overlaid
   on the P&ID and validated against source pack numbers.
 