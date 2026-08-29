# Automatic Pre-commissioning Systemization — Algorithm & Technical Design Specification
 
**Status:** Draft — Phase B (compute-only)
**Version:** 0.3
**Date:** 2026-08-27
**Consumes:** *Master Data, Reference Data & Metadata Specification* **[MD]**, *Systemization Output Specification* **[SS]**.
**Source formats:** DEXPI/Proteus XML (project A) and INGR ISO-15926 PostProc XML (project B) — see §1.
**Validated against:** DEXPI drawings `362-09-01010`, `362-09-01050`, `362-92-02265` (see the *Worked Example* **[WE]**); PostProc drawings `A14-0001-001`, `A14-0002-001`.
 
---
 
## 1. Purpose & scope
 
This document specifies **how** the Phase-B systemization output [SS] is computed from the master data [MD]. Where [SS] defines *what* a Commissioning System is and *why* the rules take their form, this document defines the *procedure* — the pipeline stages, the data structures, and the rule-by-rule computation — at a level a developer can implement.
 
It is a **technical design**, not code: it fixes the algorithm, its inputs and outputs, its ordering, and its failure handling, while leaving language and library choices to implementation. Several stages have already been demonstrated on real files in [WE]; those are marked **(validated)**.
 
**Scope boundary.** Phase B computes independently and does not reconcile against the source's `Z_TurnOverSystemNumber` [SS §1.2]. Reconciliation is Phase C. The output object is built C-ready — it carries the source assignment untouched (§9).
 
**Source formats.** The pipeline runs on two interoperability standards behind a single graph contract: **DEXPI/Proteus XML** (project A) and **INGR ISO-15926 PostProc XML**, OriginatingSystem `SPPID` (project B). A format-specific adapter produces the same reconstructed graph; every stage from §4 onward — assembly, self-owning tagging, partitioning, boundaries, naming — is **format-independent**. Where a stage's inputs differ by format it is noted in place (e.g. off-page pairing keys, §5).
 
**Two meanings of "validation."** This document distinguishes: (a) **source-agreement validation** — scoring the computed assignment against the drawing's own `Z_TurnOverSystemNumber` turnover data, the "answer key"; and (b) the engineer's **review/approve** pass in the UI [SS §6], which produces the POC's rework/time/SUS metrics. Source-agreement scoring requires turnover data and therefore applies **only to DEXPI/project A**; PostProc/project B files carry no `Z_TurnOverSystemNumber`, so there validation is **cohesion/structural only** and the review UI reports source-accuracy as "n/a". Both formats support the review/approve pass.
 
---
 
## 2. Pipeline overview
 
Systemization is a **single deterministic pass** built from seven stages. Each stage consumes the previous stage's output; none requires iteration or back-tracking.
 
| # | Stage | Input | Output |
|---|-------|-------|--------|
| 1 | **Extract & filter** | source XML (one per drawing) | clean master-data objects [MD] |
| 2 | **Build the graph** | components + connections | one plant graph (undirected + directed) |
| 3 | **Assemble across documents** | per-drawing graphs + OPC pairs | one merged multi-drawing graph |
| 4 | **Tag self-owning fluids** | graph + fluid catalogue | flare and steam/condensate tagged; all others traced |
| 5 | **Partition into systems** | graph + boundaries + flow direction | candidate Commissioning Systems |
| 6 | **Divide into sub-systems** | each system + isolation points | sub-systems |
| 7 | **Allocate, code & emit** | systems/sub-systems + rules [SS §5] | Commissioning System objects [SS §6] |
 
The stages are described in §3–§9. Determinism (§10) and failure handling (§11) apply across all of them.
 
---
 
## 3. Stage 1 — Extract & filter
 
**Goal:** turn a source export into the master-data objects [MD §2], discarding export artefacts.
 
### 3.1 Parse
 
Read the source XML and extract each object type per its source mapping [MD §4.2]. Attributes are recovered from the `GenericAttribute` name/value pairs — the key ones are `ItemTag`, `OperFluidCode`, `PipingMaterialsClass`, `ComponentClass`, `SP_PartNo`, and the connection endpoints. **(validated — the [WE] extraction reads exactly these.)**
 
### 3.2 Equipment ghost-filter *(validated, [WE §3.1])*
 
An export contains `Equipment` elements that are **not real process items** — untagged, nozzle-less placeholder/duplicate shells. Across the validation drawings, the large majority of `Equipment` elements were artefacts.
 
> **Rule.** Keep an `Equipment` element **only if it has a Tag and at least one Nozzle**. Discard untagged, nozzle-less elements. Retain a tagged-but-nozzle-less element but raise the *ghost equipment* quality flag [MD §4.2] for review.
 
### 3.3 Attribute normalisation
 
- **Fluid code** → look up Category / Subcategory in the fluid catalogue [MD §3.4]. Unknown codes raise a data-quality flag but are retained (the code itself is the grouping key, §6).
- **Materials class**, **nominal diameter**, **insulation** → carried as-is; materials class is boundary-relevant (§7).
- **Tags** → validated against the tagging convention [MD §3.3] where one applies; malformed tags are flagged, not dropped.
**Output:** the master-data object set for one drawing, artefact-free.
 
---
 
## 4. Stage 2 — Build the graph
 
**Goal:** represent the plant as one traversable graph.
 
### 4.1 Nodes and edges
 
- **Nodes** = Piping Components, Instruments, Equipment (via nozzles), Instrument Functions — every tagged or connectable object [MD §2].
- **Edges** = the reified **Connection** entity [MD §2.12], which already unifies the four link types (Process, Nozzle attachment, Signal, Off-Page continuation). One Connection = one edge. **(validated — [WE] traverses these directly.)**
### 4.2 Undirected for grouping, directed where flow matters
 
Grouping and isolation depend only on *whether* two objects connect, not on flow direction [MD §2.3.2]. The graph is therefore treated as **undirected** for most of Stages 5–6. The reconstruction also produces a **directed** graph (`adj`/`radj`): the segment From/To sense propagated along the centerline chain, one-way-directed on essentially all reconstructed edges. This flow direction is used where a rule is directional: the **flare guard** (§7.2), the **directional consumer guard** (§7.3), and **relief-device attribution** (§7.5) — all three read the same From/To sense. Synthetic edges (equipment↔nozzle, OPC mates) carry no direction and are treated as undirected.
 
### 4.3 Segment membership
 
A component belongs to exactly one Piping Segment [MD §2.3]; segments carry the fluid code and materials class. Component-level edges and segment-level attributes are both kept, because grouping keys on segment fluid (§6) while boundaries key on component class (§7).
 
**Output:** one undirected graph per drawing, nodes and edges attributed.
 
---
 
## 5. Stage 3 — Assemble across documents *(validated, [WE §6])*
 
**Goal:** merge per-drawing graphs into one plant graph, following lines across sheets.
 
Systems span drawings via **Off-Page Connectors** [MD §2.11]. In the validation set, adjacent drawings share several commissioning sub-systems, stitched by paired OPCs (`PairedDrawingNumber`).
 
> **Rule.** For each OPC on drawing *A* whose `PairedDrawingNumber` = *B*, find the mating OPC on *B* and **merge the two into a single edge**, joining the two graphs at that point. The result is one graph spanning the document set.
 
- **Unmatched OPC** — a connector whose mate is not in the loaded document set: leave as an **open boundary** and raise the *OPC unmatched* flag [MD §4.2] (**D5** — the system is incomplete but not an error; it simply continues onto an unloaded drawing).
- **Ordering** — assembly is order-independent; merging is commutative.
**Output:** one merged, multi-drawing plant graph.
 
---
 
## 6. Stage 4 — Tag self-owning fluids *([SS §4])*
 
**Goal:** identify the fluid groups that form their **own** commissioning systems rather than being traced to a consumer. Every other fluid is traced the same way in Stage 5. There are two self-owning groups:
 
- **Flare/relief** — a fluid is *flare* iff its **Category is `Flare`** in the fluid catalogue (`Reference_Data.xlsx`). The member codes are **project reference data, not algorithm constants**: project A (DEXPI) uses `SF`/`RV`/`CV` (sour/wet/dry gas flare); project B (ISO-15926/PostProc) uses `AG`/`FA`. The engine reads the set at load time and falls back to the project-A codes only when no catalogue is present, so existing project-A runs are unchanged. Each connected flare network is one flare system (§7.2).
- **Steam/condensate utility** — a fluid is *steam/condensate* iff its Category is `Utility` and its Subcategory **contains** `Steam` or `Condensate` in the fluid catalogue (the substring match accepts both `Steam`, `Condensate`, and a combined `Steam / Condensate` spelling). In project A this resolves to the steam levels `HHS`/`HS`/`MS`/`LS` and the condensate variants `SC`/`SCC`/`SCL`/`SCM`/`SCH`/`SCT`/`CC`/`VC`; in project B to `PC`/`SC`/`SH`/`SL`/`SM`. As with flare, the codes are catalogue-derived project reference data. Each connected same-fluid network is one system (§7.1a).
All other fluids — process services, the remaining utilities and ancillaries, and services that ride with their parent (vents, drains, trim, blowdown) — are **traced** in Stage 5.
 
**Output:** each fluid code tagged *flare*, *steam/condensate*, or *traced*.
 
---
 
## 7. Stage 5 — Partition into systems
 
**Goal:** form candidate Commissioning Systems — the self-owning networks (flare, steam/condensate) plus the traced fluids bounded at the §7.4 limits [SS §4].
 
### 7.1a Steam/condensate → own system [SS §4.4]
 
> Group each connected Steam/Condensate network (Stage 4) as its own commissioning system, one per fluid — these are **not** traced to a consumer. Because steam and condensate carry different fluid codes, per-fluid connectivity separates them at the steam trap: the trap is where one fluid's network ends and the other's begins. A steam or condensate network that were traced instead would lose its identity — absorbed into whichever equipment or fluid it terminated at (a steam header into a fired heater; steam into condensate at a trap). Validated across the drawing set: self-ownership raised strict per-component agreement on these components to closely match the source turnover data — the single highest-value grouping fix. Equipment still anchors its own directly-connected components (a heat exchanger's process side per the HX rules, [SS §5.14]); the utility network stops at the equipment nozzle.
 
### 7.1 Trace every remaining fluid to its consumer [SS §4.1]
 
> For each traced fluid, follow each connected fragment outward to the first consumer: an **equipment nozzle** (C1 — the fragment joins that equipment's system) or a **different-fluid junction with a boundary** (C2 — the fragment joins the served system). Bound at the §7.4 limits.
 
The C1 case is also how a service that rides with its parent is assigned — vessel trim (`VT`), blowdown (`BD`) and the like trace to the equipment they attach to and join its system. A fluid that traces into one coherent group is a "dedicated" system as an *output* — not a separate input rule.
 
### 7.2 Flare/relief → own collection system, with directional guard [SS §5.9]
 
> The flare fluids (Category `Flare` in the catalogue — `SF`/`RV`/`CV` in project A, `AG`/`FA` in project B; §6) form their own collection system: each connected flare-fluid network is one flare system. A **directional guard** prevents process fragments from mis-attaching: if a process fragment reaches a flare neighbour and reconstructed flow runs *into* the flare (`process → flare`, via the directed graph), the flare is a discharge sink — not a consumer — and is not treated as one.
>
> **Current limitation (see OPEN_POINTS):** SS §5.9 also places the isolation valves/spades between process and flare *inside* the flare system, up to the relieving/blow-down/ESD device. Reliably locating those limit devices needs instrument data not currently reconstructed (many are instrument-actuated, not distinguishable by component class). Until then the flare network is computed and the interface stubs are left flagged.
 
### 7.2a Directional consumer guard [SS §4.1]
 
> Generalises the flare guard to every fluid tie-in. When a traced fragment reaches a neighbour of a **different fluid** at a boundary, that neighbour is treated as the fragment's **consumer** only if the fragment flows *toward* it. If reconstructed flow runs from the neighbour *into* the fragment (`neighbour → fragment`, and not the reverse), the neighbour is a **supply tying in** — an upstream source, not a consumer — and is **not** taken as what the fragment is served by. A utility teeing into a process header (e.g. nitrogen or steam into a process line) therefore does not drag the header into the utility's system; the header keeps its own identity.
>
> The guard is **directional and per-fragment**, not symmetric: the same junction can be a consumer for one side and a supply for the other. Nitrogen that flows *into* an acid-gas line still commissions with that line (the acid gas is nitrogen's consumer), while the acid-gas line does not commission with nitrogen. The guard fires only when direction is known and one-way into the fragment; when direction is unknown or points outward it falls through to ordinary consumer detection — so it only *removes* consumer signals there is positive evidence are supplies. Validated across the drawing set: it substantially reduced spurious fluid-boundary crossings while preserving the validated nitrogen tie-ins, moving mis-assigned components to honest off-document/dead-ended rather than a wrong system.
 
### 7.3 Equipment-anchoring [SS §4.1 step 3]
 
> Where segments of **many services** converge on a **tagged equipment item** [MD §2.9], anchor a system on that equipment: the equipment plus its directly connected lines (up to the first boundary) form one system. This is orthogonal to the fluid trace.
 
### 7.4 Boundary placement [SS §5.1–5.2, §5.4]
 
Each candidate system's edges are set at boundary-forming components [MD §3.2]:
 
- **Prefer** a limit that is both an isolation valve/blind **and** a piping-class break [SS §5.1–5.2] — the ideal limit (one line → one system, for pressure-test management).
- Where they cannot coincide, the isolation valve at the user governs by default; the precedence is a project rule (§9).
- Directional limits use reconstructed flow direction (§4.2): the flare guard (§7.2) is in use.
**Output:** candidate Commissioning Systems, each a member set plus a boundary set.
 
### 7.4a Relief-device attribution [SS §5.9]
 
> A relief device (a safety/relief valve — exports carry these under both `SafetyValveOrFitting` and `Reliefdevices`, and **both** are boundary-forming [MD §3.2]) is attributed to the system it **protects**, not the discharge it vents to. Flow direction resolves the two sides deterministically: the neighbour whose flow runs *into* the valve is the protected (inlet) side — the line or equipment being relieved — while the side the valve flows *out* to is the discharge (flare/atmosphere). The device takes the **protected side's** system. This is the complement of the flare guard: the guard identifies the discharge side; relief attribution takes the other side. A relief valve on a process line venting to flare therefore commissions with the process line (matching the source), instead of being stranded by the flare guard. Devices whose protected side is not directionally identifiable fall through to the ordinary walk — attribution only *adds* correct assignments, never forces a wrong one.
 
---
 
## 8. Stage 6 — Divide into sub-systems [SS §2.5, §4.4]
 
**Goal:** split a system into sub-systems where warranted — the isolation-region granularity (G1) applied *within* a system.
 
### 8.1 Isolation regions
 
> Within a system, find the regions separated by isolation points [SS §5.4] — preferentially at spacers/blind spacers to reduce temporary blinds. Each safely-isolatable region is a candidate sub-system.
 
### 8.2 Instrument-defined sub-systems *(validated, [WE §3.5])*
 
A sub-system can be defined almost entirely by **instrumentation**, with little or no pipe — the `UZ`/`HZ`/`HSUZ` safeguarding clusters in [WE] (`11-0920-020`, `-030`). The census **must count all element types**, not only `PipingNetworkSegment`, or instrument-defined sub-systems are missed.
 
### 8.3 Services that ride with their parent
 
Services that attach to a parent (vessel trim `VT`, blowdown `BD`, vents, drains) are not a special case: the Stage 5 trace assigns each to the equipment or line it connects to via C1 (§7.1), so it inherits that system directly. No separate ancillary handling is required.
 
### 8.4 Degree of automation (**D2 residual**)
 
The split is **guided, not forced**: the algorithm proposes sub-system candidates from isolation regions and equipment-function grouping; whether a system is actually subdivided (and how finely) may require review, as it reflects commissioning-organisation judgement.
 
**Output:** each system's sub-systems (possibly none).
 
---
 
## 9. Stage 7 — Allocate, code & emit
 
**Goal:** finalise each Commissioning System object [SS §6].
 
### 9.1 Component-to-system allocation [SS §5.6]
 
Resolve shared/ambiguous components by the allocation rules: boundary valve/blind → higher-priority system [SS §5.1]; steam trap → steam side [SS §5.7]; sampling connection → upstream system; flare/drain interface valves → the flare/drain side [SS §5.6]; instruments → per [SS §5.15] (vessel/column instruments → equipment system, validated [WE §3]).
 
### 9.2 Place in the SUP hierarchy [SS §2.2–2.4]
 
Assign each system to its **Start-Up Package** via the **Process Unit**: the system's Unit [MD §3.6] maps to a SUP through the project SUP→Unit input (**D8**). Priority ordering [SS §5.3] and SUP sequence come from project inputs, not the P&ID (**D6**).
 
### 9.3 Assign the system name and internal ID [SS §6.4]
 
Each system receives a stable **internal ID** and a human-readable **display name** of the form `{SUP}-{seq}-{fluid}`.
 
**Display name components:**
- **SUP** — the Start-Up Package, resolved from the members' **Process Unit** via the Unit→SUP mapping [MD §3.6], choosing the SUP whose units contribute the **most** components (tie-break: lowest SUP number). The Process Unit is the unit **decoded from each member's line Tag** [MD §2.1a, §2.2] and `UnitSUP` is keyed by that bare unit code, so the SUP is a **direct join** on the Process Unit — no drawing-number parsing in the path. A member with no line Tag (equipment, off-line instruments) falls back to the unit parsed from its drawing number (§9.3a).
- **fluid** — the system's fluid code. For a **process** system (a system whose governing fluid has Category `Process`) that carries **more than one** process fluid, the code `P` is used instead of a single fluid code [SS §6.4].
- **seq** — the sequence number, whose source depends on system type:
  - **Utility systems** — the sequence assigned to the fluid code in the System catalogue [MD §3.5] (`Fluid Code → Seq → System Name`). This is **stable** across runs and input subsets, because it is fixed reference data.
  - **Process systems** — there is no fluid-sequence table; process systems are numbered by **ordering their anchor equipment tag** within the SUP. This number is **run-scoped / provisional** and is marked with a trailing `*`. It may change if the set of loaded drawings changes; the stable handle for a process system is its internal ID, not its number.
**Process vs utility** is decided by the fluid's **Category** in the catalogue (`Process` → process system), **not** by how the walk systemized the fragment. A utility fluid traced to a consumer (e.g. boiler feed water reaching a served system) is still named as the utility it is.
 
**Internal ID (stable identity):**
- Utility systems: `(SUP, fluid)` — e.g. `SUP07|fluid:SM`.
- Process systems: `(SUP, anchor-equipment)` — e.g. `SUP07|anchor:14R001`.
The internal ID is stable across runs and across different loaded document subsets (a process system's ID stabilises once its anchor equipment is in the loaded set). It is the identity used to (a) **merge** the several connected networks that make up one system (§9.5), and (b) key the engineer's review decisions [SS §6]. The **display name may change** — a provisional process number, or a re-lettered SUP — while the internal ID does not. Any downstream reference (a work pack, a decision record) should cite the internal ID; a provisional (`*`) number must not be treated as a durable identifier.
 
### 9.3a Process Unit resolution *(reference-data driven)*
 
A component's **Process Unit is the unit decoded from its line Tag** [MD §2.1a, §2.2] — the primary source, and the key the SUP join uses directly (`SUP = UnitSUP[ProcessUnit]`, §9.3). Since `UnitSUP` is now keyed by the **bare** unit code (`14`, not the drawing-prefixed `A14` — the leading character was dropped so the key equals the Process Unit), no drawing parsing is needed for a component that carries a line Tag.
 
**Drawing-number fallback and cross-check.** For a component with *no* line Tag (equipment, off-line instruments), the Unit is recovered from its **drawing number**, using the unit-code set [MD §3.6] as the parser: a code is matched against the drawing as (a) a whole token, then (b) a maximal digit run of a token (so a bare `14` is found inside `A14-0001-001`), then (c) a prefix — longest code first so `140` can't be shadowed by `14`. The same drawing-derived unit is compared against the tag unit as a **data-quality cross-check** (tag-vs-drawing mismatch, [MD §2.1a, §4.2]). A component whose unit resolves from neither Tag nor drawing is flagged (its SUP resolves to a placeholder). This avoids a hardcoded positional split and adapts to a project's numbering scheme simply by listing that project's Process Units [MD §3.6].
 
### 9.4 Carry the source assignment (Phase-C hook) [SS §6.3]
 
Record each member's `Z_TurnOverSystemNumber` / `SubsystemNo` on the system **without using it** — the Phase-C reconciliation hook. A system whose members hold several source assignments is not a Phase-B error; it is recorded for Phase C.
 
### 9.5 Emit
 
Output each Commissioning System with: **internal ID** (§9.3), **display name** (`{SUP}-{seq}-{fluid}`, with `*` marking a provisional process sequence), SUP, Category, member set, boundary set, spanned Documents, provenance = `computed`, and the carried source assignment.
 
**Fragment merge.** The walk finds each *connected network* separately, so one commissioning system commonly arrives as several fragments (a utility fluid appears as many disconnected networks; a process system splits into a main run plus tie-in stubs). Fragments that share an **internal ID** are the same system and are **merged for output** — member sets unioned, spanned Documents unioned, component counts summed. The emitted system count is the count of distinct internal IDs, not of raw fragments. (On a full-plant run this collapses many fragment rows to a much smaller set of real systems.)
 
**Output:** the Phase-B Commissioning System set, one entry per distinct internal ID.
 
---
 
## 10. Determinism & ordering
 
- **Deterministic.** Given the same master data, boundary list, and grouping rule, the output is identical [SS §7]. No stage depends on iteration order.
- **Stable identifiers.** Each system's **internal ID** is derived from a stable key — `(SUP, fluid)` for utilities, `(SUP, anchor-equipment)` for process systems (§9.3) — so re-runs produce the same identity for the same physical system. Utility **display numbers** are stable too (reference-data sequence); process display numbers are provisional (anchor-ordering) and marked `*`, with the internal ID carrying the stable identity.
- **Single pass.** Stages run once, in order; there is no convergence loop.
---
 
## 11. Failure handling & data-quality flags
 
Each stage raises flags [MD §4.2] rather than aborting, so a whole-drawing result is always produced with problems surfaced:
 
| Condition | Stage | Handling |
|-----------|-------|----------|
| Ghost equipment | 1 | discard (untagged+nozzle-less) or flag (tagged, nozzle-less) |
| Unknown fluid code | 1 | retain, flag; grouping still keys on the code |
| Malformed tag | 1 | retain, flag |
| Unmatched OPC | 3 | open boundary, flag (system continues off-set) |
| Steam-trap boundary with no `SteamTrap` class | 5 | fall back to the `steam → condensate` fluid change [WE §7]; flag |
| System spanning several source assignments | 7 | record all, flag for Phase C |
| Derived flow direction on a directional limit | 5 | use it, but mark the limit provisional (D-flag) |
 
---
 
## 12. What this design defers
 
- **Reconciliation** against `Z_TurnOverSystemNumber` — Phase C (the output already carries it, §9.4).
- **Flare interface-hardware inclusion** (SS §5.9) — reliably identifying the relieving/blow-down/ESD limit devices needs instrument data not currently reconstructed; the flare network is computed, the interface stubs are flagged. (An earlier keyword/class-based attempt at this was found non-functional and removed from the walk; it is a clean deferral, not partial code.)
- **Sub-system derivation by module** — module boundaries are carried on `GenericComponent` piping components (`TP_Module1`/`TP_Module2`), which would allow sub-systems of the form `SUP01-IA-001-001_AMR4` by graph-cut at module boundaries plus a stick-built remainder. This is **not yet built or validated** (needs a utility system that crosses modules to test) and is deferred; §8 keeps the guided isolation-region model in the interim.
- **Rating-based system split** — the current model groups a utility system by fluid (one system per fluid); it does **not** subdivide a fluid's system by piping rating. The [SS] System definition names rating similarity as a future refinement (fluid governs today); splitting by rating is deferred.
- **Instrument-defined sub-systems** (§8.2) — require reconstructing the instrument/signal layer (the instrument functions extract but do not yet connect).
- **Equipment-anchoring precedence, priority data** — project inputs/decisions, not algorithmic gaps (see SS §9). (SUP→Unit mapping and the fluid→sequence catalogue are now implemented as reference data, [MD §3.5–3.6].)
- **Semantic/RDF output** — Gold layer [MD].
---
 
## 13. Traceability — algorithm to source
 
| Stage | Governing rule | Validated in |
|-------|----------------|--------------|
| 1 Extract & filter | [MD §2, §4.2] | [WE §1, §3.1] |
| 2 Build graph | [MD §2.12] | [WE §2] |
| 3 Cross-document | [MD §2.11] | [WE §6] |
| 4 Deployment class | [SS §4], [WE §4.4] | [WE §4, §7] |
| 5 Partition | [SS §4, §5] | [WE §2, §3, §4, §7] |
| 6 Sub-systems | [SS §2.5, §4.4] | [WE §3.5] |
| 7 Allocate & code | [SS §5.6, §6] | [WE §3] |
 
Every algorithm stage traces to a governing rule and, for most, to a demonstration on real data — so the design rests on validated foundations, not assumptions.