# Automatic Pre-commissioning Systemization (P&ID) — Systemization Output Specification
 
**Status:** Draft — Phase B (compute-only)
**Version:** 0.4
**Date:** 2026-08-27
**Data Product:** Automatic Pre-commissioning Systemization based on P&ID interoperability data
**Consumes:** *Master Data, Reference Data & Metadata Specification* (the master-data spec) — referenced throughout as **[MD §x]**; the *Algorithm Specification* **[ALG]**.
**Source formats:** DEXPI/Proteus XML (project A) and INGR ISO-15926 PostProc XML, OriginatingSystem `SPPID` (project B). A format adapter yields the same reconstructed graph; the systemization is format-independent.
 
> **Revision 0.3.** The commissioning-system **display-name shape is now reference-data-driven** — composed from a template in the `TaggingConvention` sheet (Object `CommissioningSystem`) by the same engine as the master-data Tags, defaulting to `{SUP}-{seq}-{fluid}` (§6.4, D4).
 
> **Revision 0.4.** The **SUP is now joined directly on the Process Unit** ([MD §2.1a]). `UnitSUP` is keyed by the bare Process-Unit code (`14`, the code on the line Tag), so `SUP = UnitSUP[ProcessUnit]`; the earlier drawing-number-derived unit (keyed `A14`) is demoted to a fallback for tag-less components and a data-quality cross-check (§6.4, D8).
 
---
 
## 1. Purpose & scope
 
This document defines the **computed systemization output** of the data product: the commissioning systems the product produces by traversing the master-data network and cutting at boundary-forming components. Where the master-data spec defines *what the product knows about the plant from the P&ID* (the input), this document defines *what the product produces from it* (the output).
 
> **Two meanings of "validation."** *Source-agreement validation* scores the computed assignment against the drawing's own turnover data (`Z_TurnOverSystemNumber`); it applies **only where turnover data is present** (DEXPI/project A). PostProc/project B files carry none, so there the check is **cohesion/structural only** and source-accuracy reports as "n/a". Separately, the engineer's **review/approve** pass in the UI produces the rework/time/usability evidence and applies to both formats.
 
### 1.1 The two documents
 
| | Master-data spec **[MD]** | This document |
|---|---|---|
| Answers | *What is in the plant?* (from the source) | *How is it partitioned for commissioning?* (computed) |
| Objects | Document, Pipeline System, Segment, Component, Connection, … | Commissioning System |
| Provenance | extracted / derived from the source | **computed** by this product |
| Layer | the identified, connected fact base | the business result built on it |
 
Every object here is **computed**, not extracted. This is the defining difference from the master-data spec, whose objects all trace back to the source. A Commissioning System exists only because the algorithm ran.
 
### 1.2 Phase B — compute-only (this revision)
 
The product will ultimately **reconcile** its computed systemization against the commissioning assignment the source already carries (`Z_TurnOverSystemNumber` / `SubsystemNo`, [MD §4.2]). That is **Phase C** and is out of scope here.
 
**Phase B computes systemization independently and does not reconcile.** It walks the connectivity graph, cuts at boundary-forming components, and emits commissioning systems from connectivity alone. The source's turnover assignment is **carried through untouched** (§6.3) but plays **no part** in the computation — it is neither an input to grouping nor validated against.
 
> **Locked decisions for Phase B:**
> 1. **Compute-only.** Systemization is derived from connectivity; the source's turnover assignment does not influence it.
> 2. **Carry, don't discard.** The source turnover assignment is retained on the output object as an optional attribute so Phase C can reconcile against it without a schema change. The Phase-B algorithm ignores it.
> 3. **No reconciliation, no review workflow.** Those are Phase C.
 
---
 
## 2. The plant-subdivision hierarchy
 
Systemization sits inside a **top-down functional breakdown** of the plant, defined to guide project efforts toward a **safe, sequential, ordered and structured** progression: construction completion → pre-commissioning → commissioning → final handover. The plant is first subdivided into **Start-Up Packages (SUP)**, then each SUP into **Systems**, and (where useful) each system into **Sub-systems**. This hierarchy is the commissioning framework the computed output belongs to.
 
### 2.1 The levels
 
| Level | What it is | Examples |
|-------|-----------|----------|
| **Start-Up Package (SUP) / Mechanical Completion Package (MCP)** | A stand-alone, large and complex functional section designed for a specific scope, that can be **started up and operated stand-alone** safely, and can be **geographically segregated on the plot plan**. It is the portion of works shared by *both* the layout-based designers/builders (Engineering & Construction) *and* the functional/system-based designers/operators (Process, Start-Up, Operation & Maintenance). Its extent varies with plant size. | Main Substation, Utilities Generation, Process Unit 1, Process Unit 2, Flare |
| **System** | A portion of the plant performing **one operational function or service**, with little or no interference from other systems during pre-commissioning. **Systems are placed in priority order to respect the commissioning/start-up sequence** (§5.3), establishing the flow from construction to commissioning. The main working level. | Instrument air, cooling water, fuel gas |
| **Sub-system** | An **optional** further subdivision of a system, implemented to facilitate the organisation and follow-up of specific activities. It performs a **partial** operational function of the system, with little interference from other sub-systems, and can be **safely isolated** and pre-commissioned/commissioned on its own. Typically: several equipment of the **same function** within a system. | Fire Water: storage / pumps / distribution (§6.4) |
 
A SUP includes several **Utilities, Process, and Non-Process** systems. SUP and System are the two main levels; a System is subdivided into Sub-systems where it helps organise activities (§2.5). The SUP is the meeting point of the **geographic** view (plot-plan layout) and the **functional** view (systems): conceptually a SUP's extent corresponds to the systems belonging to it, but **the authoritative SUP battery limits are defined on the project plot plan and given as input** (§2.4) — the product does not derive them from the systems it computes.
 
### 2.2 The Start-Up Package (SUP)
 
The plant is subdivided into SUPs on the basis of **functional considerations, start-up flexibility, and the Project WBS**. A SUP is a **stand-alone functional portion** of the plant that:
 
- **can be independently commissioned**, provided the necessary utilities are available at its battery limit; and
- **can be geographically and substantially segregated** on the plot plan.
The SUP is the **deepest level of the geographical WBS that still satisfies the functional approach** — the point where the layout view and the functional view meet.
 
**SUP prioritisation (start-up-driven).** SUPs are developed and sequenced with a start-up-driven approach, prioritising in this order:
 
1. **Electrical power distribution** — power first, to enable motor and loop tests
2. **Control system**
3. **Safety**
4. **Utility**
5. **Process**
This is the **SUP-level** priority order (a project input, §2.4). It complements the **system-level** priority ordering (§5.3): SUP priority sequences the packages; system priority sequences the systems within them. Together they establish the overall commissioning sequence — and both feed the boundary-allocation rule (§5.1) that assigns shared hardware to the higher-priority side.
 
**How SUPs are provided.** SUPs arrive as a **list of nominated packages** — `SUP01`, `SUP02`, `SUP03`, … — and **the sequence is coded in the naming**: the number in the SUP name *is* its position in the start-up order (`SUP01` before `SUP02`, and so on). So the product reads the SUP ordering directly from the SUP list; no separate sequence field is required. The prioritisation reasoning above (electrical → … → process) is what *drives* how the list is numbered at project definition; the product consumes the resulting numbered list.
 
**The Process Unit links a SUP to the P&ID.** Each SUP **contains one or more Process Units**, and — crucially — the **Process Unit is represented in the P&ID** (and in the plant components on it). The Unit is therefore the **bridge between the geographic SUP breakdown and the P&ID connectivity** the product works from: it is a field the product already reads from the source (the segment Unit, [MD §3.6], carried on every component and used to resolve the SUP in the system name, §6.4) *and* an element of the SUP breakdown.
 
- **SUP → Process Unit** is one-to-many: a SUP contains one or more Units.
- **Process Unit → SUP** gives the join the product needs: knowing which Unit a computed system belongs to (from the P&ID), and which SUP contains that Unit (from the SUP breakdown), places each system in its SUP **via the Unit** — without needing a geographic area match. This is the concrete resolution of the SUP-matching question (D8).
### 2.3 System categories — and what this product can compute
 
Systems fall into three categories, and the distinction is **decisive for this product's scope**:
 
| Category | Defined from | In this product's scope? |
|----------|--------------|--------------------------|
| **Utilities** | P&IDs — utility fluids | **Yes** |
| **Process** | P&IDs — process fluids | **Yes** |
| **Non-Process** | **Not** P&IDs — one-line diagrams, plot plans, layouts | **No** |
 
- **Utilities systems** relate to utility fluids and are defined on P&IDs: instrument air, fire water, nitrogen, etc.
- **Process systems** relate to process fluids and are defined on P&IDs: reaction section, distillation section, catalyst handling, wax system, hydrogen section, etc.
- **Non-Process systems** complete a SUP but are **not based on P&IDs**: lighting, steel structures, electrical distribution, buildings, HVAC, roads — shown on one-line diagrams, plot plans, or layouts.
> **Scope boundary (decisive).** This product reads **only P&ID data** (DEXPI or PostProc). It can therefore compute **Utilities and Process systems only** — the two P&ID-based categories. **Non-Process systems are outside what this product can produce**, because their definition lives in documents the product does not ingest. A complete SUP subdivision requires Non-Process systems from other sources; this product supplies the P&ID-based part of that picture, not the whole.
 
### 2.4 Where this product operates in the hierarchy
 
- **SUPs are a project input, not computed here.** Start-Up Packages — their **definition**, their **sequence**, and their **battery limits** — are established at the start of the project (battery limits are defined on the **plot plan**) and are **outside the scope of this initiative**. They are **given to this product as input**: the SUP breakdown and sequence are consumed, not produced.
- **This product computes at the System level, and the Sub-system level where a system warrants subdivision** (§2.5), for the Utilities and Process categories, **within the SUPs provided**. Each computed system belongs to a SUP from the input breakdown.
- **The Utilities/Process distinction is derivable from P&ID data** — it follows the fluid/service ([MD §3.4]): a utility fluid marks a Utilities system, a process fluid a Process system. So the product both computes the systems and categorises them, within the two categories it covers.
- **Non-Process systems remain out of scope** (§2.3) — they are not P&ID-based, so the product cannot produce them; they enter the SUP picture from other project sources.
> **Inputs from the project (not from the P&ID source):** the SUP breakdown, the SUP sequence, and SUP battery limits (from the plot plan). The product consumes these to place its computed systems within the correct SUP; it does not define them. See **D8** (§9).
 
### 2.5 When a system is divided into sub-systems
 
A system is subdivided into sub-systems when doing so helps organise and follow up specific pre-commissioning/commissioning activities. Sub-systems are **optional** — many systems have none — but where a system is large or heterogeneous, splitting it into sub-systems that each perform a **partial function** and can be **safely isolated** is the norm.
 
The characteristic trigger is **grouping equipment of the same function** within the system. The Fire Water example (§6.4) is canonical: one Fire Water *system* splits into *storage*, *pumps*, and *distribution* sub-systems — each a functional part that can be isolated and worked on independently.
 
- **Placement of the sub-system break** follows §5.4: at isolation points, preferably at spacers/blind spacers to reduce temporary blinds.
- **A sub-system is a safely-isolatable partial function** — the sub-system split is the isolation-region granularity applied *within* a system (§4.4). A single service physically split by a real isolation becomes one system with several sub-systems.
- **Whether a given system is subdivided at all, and into how many sub-systems**, is a commissioning-organisation judgement (function grouping), not a purely topological result — so it is guided, not fully determined, by the algorithm.
- **Sub-system by construction module — a derivable case, deferred.** Where a system crosses module boundaries, the sub-system corresponds to the portion of the system inside each module (plus a stick-built remainder), e.g. `SUP01-IA-001-001_AMR4`. The source data supports this: module boundaries are carried on `GenericComponent` piping components via `TP_Module1`/`TP_Module2` attributes, so the split can be derived by cutting the system graph at those boundaries and propagating the module label. This derivation is **not implemented in the current revision** — it awaits a utility system that genuinely crosses modules to validate against — and is recorded here so the model is complete; the function-grouping cases above remain guided.
---
 
## 3. What this document consumes
 
Phase B is built on objects from the master-data spec **[MD]**, plus a small set of **project inputs** that are not in the P&ID source. It introduces no new *extracted* data — only the computed output object (§6).
 
### 3.1 From the master data [MD]
 
| From [MD] | Used for |
|-----------|----------|
| **Connection** [MD §2.12] | The graph the algorithm traverses. Every join — Process, Nozzle attachment, Signal, Off-Page continuation — is one addressable edge, so traversal is uniform. |
| **Boundary-forming component classes** [MD §3.2] | The governed list of classes that end a package (block valve, spectacle blind, blind flange, line break). The algorithm cuts the graph at these. |
| **Piping Component / Segment** [MD §2.3–2.4] | The members that fall inside a system. |
| **Process Equipment + Nozzle** [MD §2.9] | Equipment enters a system through its nozzle connections; a nozzle is a natural package boundary. |
| **Instrumentation Loop** [MD §2.8] | A coherent handover unit — a loop belongs with the system its instruments sit on. |
| **Off-Page Connector** [MD §2.11] | Lets a system span drawings; the algorithm follows off-page continuations across Documents. |
| **Source turnover assignment** [MD §4.2] | Carried through only (§6.3); not used in Phase B computation. |
 
### 3.2 Project inputs (not from the P&ID source)
 
These are established by the project, outside this initiative, and given to the product:
 
| Project input | Used for | Decision |
|---------------|----------|----------|
| **SUP list** (`SUP01`, `SUP02`, …) + **SUP→Unit mapping** | Places each computed system within its Start-Up Package **via the Process Unit** (§2.2); the **sequence is encoded in the SUP naming**. | D8 |
| **SUP battery limits** (plot plan) | The authoritative SUP extents (§2.1, §2.4). | D8 |
| **System service schedule & commissioning priority** | Drives boundary allocation via the priority ordering (§5.3). | D6 |
 
---
 
## 4. The grouping definition
 
A commissioning system is formed by **tracing every fluid outward to the consumer
it feeds** and bounding it at the §5 isolation and class-break limits. Two fluid
groups are exceptions — they form their **own** commissioning systems rather than
being traced to a consumer: **flare/relief** fluids (§4.3) and **steam/condensate
utility** fluids (§4.4). Every other fluid — process services and the remaining
utilities and ancillaries, including vents, drains, trim, and blowdown — is traced
the same way.
 
### 4.1 The rule
 
1. **Trace every fluid to its consumer.** Starting from a fluid's components,
   follow Connections [MD §2.12] outward to the first consumer:
   - **C1 — equipment nozzle:** the trace reaches a tagged equipment item; the
     components join that equipment's system. (This is also how a service that
     rides with its parent — e.g. vessel trim — is assigned: it traces to the
     equipment it is mounted on.)
   - **C2 — different-fluid junction with a boundary:** the trace tees into a
     different service where a boundary-forming component [MD §3.2] is present;
     the components join the served system.
   (See the Consumer & Boundary Definition for the full C1/C2 rules, the
   stop-ladder, and the hard guard against traversing into the consumer.)
2. **Bounded by the §5 limits.** The system *ends* where §5 says — the last
   manual valve before a class change, a piping class break (§5.2), an interface
   isolation valve/blind (§5.5–5.13), the flange downstream of a steam trap
   (§5.7). Boundary-forming components place the *edges*.
3. **Equipment-anchoring.** A trace that reaches equipment anchors on it — the
   vessel and its directly connected lines up to the first boundary form one
   system.
A "dedicated" system — one service that stays within a single commissioning
system — is simply the case where a fluid traces into one coherent group; it is
an outcome of the trace, not a separate rule.
 
> **What defines a System — fluid governs; rating is a future refinement.** A
> System is a subdivision of a SUP performing a given operational function that
> can be commissioned with little interference from others. In principle its
> members share three things: isolation-bounded extent (§5.1, §5.4), similar
> stream characteristics (**fluid/service**), and similar design pressure/
> temperature (**piping rating**). In this revision **fluid/service is the
> governing criterion** — a commissioning system is a connected same-fluid
> network bounded by §5 limits. **Rating similarity is named as a desirable
> refinement but is not yet enforced**: a single named utility system may still
> span more than one piping class. Splitting a fluid's system further by rating
> (so each rating band is its own system) is a future refinement, not a current
> rule — see D-items (§9). This keeps the definition faithful to the engineering
> intent while stating plainly what the product computes today.
 
### 4.2 Flow direction
 
The reconstructed graph is directed (the source segment From/To sense propagated
along each centerline). Grouping and isolation use only connectivity, but flow
direction is used where a rule is directional. Three rules read it: the **flare
guard** (§4.3), the **directional consumer guard** (§4.6), and **relief-device
attribution** (§5.9). In each, a neighbour that flow runs *into* is a sink or
supply, not the consumer the fragment feeds.
 
### 4.3 Flare and relief systems
 
The flare and relief fluids form their own collection system: each connected
flare-fluid network is one flare system. A fluid is flare when its **Category is
`Flare`** in the fluid catalogue ([MD §3.4]) — project reference data, not a
fixed code list: project A's flare fluids are `SF`/`RV`/`CV`, project B's are
`AG`/`FA`. A **directional guard** keeps process fragments from mis-attaching —
if a process fragment reaches a flare neighbour and flow runs *into* the flare,
the flare is a discharge sink, not a consumer, and is not treated as one. Flare
components remain **boundary-forming**: because boundary detection keys on
component *class* (`SafetyValveOrFitting`/`Reliefdevices`), the flare's own
components still act as limits for adjacent process systems. See §5.9 for the
flare business rule and its current computation status.
 
### 4.4 Steam and condensate utility systems
 
The steam and condensate utility fluids form their **own** commissioning systems,
one per connected same-fluid network — they are not traced to a consumer. This is
the commissioning philosophy applied directly: *each steam network is one system*,
and *steam and condensate are separate systems, the steam trap is the boundary
between them*. Because steam and condensate carry different fluid codes, a
per-fluid connected network keeps them separate automatically — the trap is
simply where one fluid's network ends and the other's begins.
 
A fluid is treated as steam/condensate when its Category is `Utility` and its
**Subcategory contains** `Steam` or `Condensate` ([MD §3.4]) — catalogue-derived
per project, not a fixed code list. A utility fluid commonly appears as **several
disconnected networks** across the plant; each connected network is found
separately and the networks are then **merged by system identity** ({SUP},fluid;
§6.4) so one utility fluid in one SUP reads as a single commissioning system in
the output, not as many fragments.
 
The set is the **Steam / Condensate** subcategory of the fluid catalogue
(`Reference_Data.xlsx`, Category `Utility`): the steam pressure levels (`HHS`,
`HS`, `MS`, `LS`) and the condensate variants (`SC`, `SCC`, `SCL`, `SCM`, `SCH`,
`SCT`, `CC`, `VC`).
 
**Why they own themselves.** A steam or condensate network runs across a plant to
many users, passing through exchangers, heaters, and traps. Tracing it to "a
consumer" makes it lose its identity — it would be absorbed into whichever
equipment or fluid it happened to terminate at (a steam header walking into a
fired heater would be commissioned with the heater; steam meeting condensate at a
trap would be commissioned with the condensate). Validation across the drawing set
confirmed this: treating these networks as self-owning raised strict
per-component agreement on the affected components from the low tens of percent to
closely matched the source turnover data — it keeps each utility network in
its own system. Equipment still anchors its *own* directly-connected components
(a heat exchanger's process side follows the heat-exchanger rules, §5.14); the
utility supply/return network simply stops at the equipment nozzle instead of
being absorbed by it.
 
Utility components remain **boundary-forming** by component class (a `SteamTrap`
is a limit for the adjacent process system regardless of how the utility fluid
itself is grouped).
 
### 4.5 Systems and sub-systems operate at different granularities
 
A single service split by a real isolation is **one system containing multiple
sub-systems** (§2.5) — the trace defines the system; the isolation defines the
sub-system split within it (Fire Water example, §6.4).
 
### 4.6 Directional consumer guard
 
When a traced fragment reaches a neighbour of a **different fluid** at a boundary,
that neighbour is its **consumer** only if the fragment flows *toward* it. If flow
runs from the neighbour *into* the fragment, the neighbour is a **supply tying
in** — an upstream source, not a consumer — and the fragment is not assigned to
it. A utility teeing into a process header (nitrogen or steam into a process line)
therefore does not pull the header into the utility's system; the header keeps its
own identity. This generalises the flare guard (§4.3) to every tie-in.
 
The guard is **directional and per-fragment**: the same junction can be a consumer
for one side and a supply for the other. Nitrogen flowing *into* an acid-gas line
still commissions with that line (the line is nitrogen's consumer), while the line
does not commission with nitrogen. It fires only when flow direction is known and
one-way into the fragment; otherwise it falls through to ordinary consumer
detection. Where a fragment's only apparent consumer is a supply tie-in, the
fragment resolves as off-document or a dead-end (an honest coverage gap) rather
than taking a wrong system.
 
---
 
## 5. Governing business rules
 
 
These are commissioning rules the systemization must honour, layered on top of the grouping rule (§4). They come from commissioning philosophy, not from the source data, and they constrain *how boundaries are placed and allocated*.
 
**Summary — general rules (§5.1–5.6):**
 
| § | Rule | In one line |
|---|------|-------------|
| 5.1 | Boundary allocation by priority | The boundary valve/blind belongs to the higher-priority (earlier-commissioned) system. |
| 5.2 | Prefer piping class breaks | Place limits at class breaks so each line number maps to one system (eases pressure testing). |
| 5.3 | System priority ordering | Earlier-in-service → commissioning-priority → utilities-over-process. |
| 5.4 | Boundary placement | Limits at isolation points; sub-system breaks preferably at spacers/blind spacers. |
| 5.5 | Process ↔ Utility interface | Utility takes priority; blind at most suitable flange; extension limit at the tie-in point. |
| 5.6 | Component-to-system allocation | Per-component rules for blinds, spades, traps, sampling, flare/drain valves. |
 
**Summary — domain-specific system rules (§5.7–5.15):**
 
| § | System | Boundary / allocation rule |
|---|--------|----------------------------|
| 5.7 | Steam & condensate | Each steam network (HP/MP/LP) and each condensate network is its own system; boundary at the flange downstream of the steam trap. |
| 5.8 | Nitrogen (inert gas) | Limit at the class break downstream of the last manual isolation valve. |
| 5.9 | Flare | Flare includes the process-interface isolation valves; limit downstream of relieving devices (safety/PCV/blow-down/ESD). |
| 5.10 | Closed drains & blowdown | Drain includes the isolation valves and spectacle blind at the process interface. |
| 5.11 | Fire water | Per SUP: underground lines, monitors, hydrants to first isolation valve = one system; deluge branches independent. |
| 5.12 | Other water | Cooling supply+return (incl. pump cooling) one system; service/potable/BFW/demi to flange downstream last user valve. |
| 5.13 | Chemical injection | Limit downstream of the supply isolation valve at each injection point. |
| 5.14 | Heat exchanger | By precedence: cooling-water→cooling sub-system; refrigerant→loop; steam-vs-process→process; else utility; else shell side. |
| 5.15 | Instrument allocation | Soft tag → sub-system; vessel/column instruments → equipment system; pump instruments → pump system. |
 
### 5.1 Boundary allocation by priority
 
**The physical limit of a process or utility system should, wherever possible, be an isolation valve or a spectacle/paddle blind, and that boundary component is allocated to the system that has the highest priority — the one to be commissioned first.**
 
This resolves the boundary-ownership question directly: a boundary component sits between two systems, and it belongs to the **higher-priority (earlier-commissioned)** of the two. In practice:
 
- The boundary valve/blind is a real component that must be commissioned with exactly one system; this rule says *which*.
- "Highest priority / commissioned first" is the tie-breaker — the earlier system claims the shared boundary hardware.
- Where a boundary is not a valve or blind (an open end, an off-page connector), no allocation is needed — there is no shared hardware to assign.
> This requires a **system priority / commissioning sequence** as an input. The *ordering rule* is defined in §5.3 (earlier-in-service → commissioning-priority → utilities-over-process); the *data* to apply it (the project service schedule and commissioning-priority values) is not in the P&ID source — see **D6** (§9). Until that data is available, Phase B records the boundary component and the two systems it separates, and leaves the allocation unresolved rather than guessing.
 
### 5.2 Prefer piping class breaks as system limits
 
**System limits should, where possible, be placed at piping class breaks** — a change in Piping Materials Class ([MD §2.3]). The purpose is explicit and important:
 
- It ensures **each piping line number maps to exactly one system** (a line does not straddle a system boundary).
- That one-line-to-one-system property **simplifies pressure-test management on site**, because a pressure test is run per line and per system — if a line belongs to a single system, its test belongs to a single system too.
Connection to the data: a piping class break is **detectable from the master data** — the Piping Segment carries its Materials Class ([MD §2.3]), and the composed Segment Tag embeds it, so a change of class along a connected path is visible. Where a class break and a boundary component coincide, that location is the preferred system limit. Where they do not, this rule biases the choice of limit toward the class break, subject to Rule 5.1.
 
> These two rules interact: 5.1 says a limit *should be* an isolation valve or blind; 5.2 says a limit *should* coincide with a class break. Where both can be satisfied at once (a valve or blind at a class break), that is the ideal system limit. Where they conflict, the priority between them is a commissioning-engineering call — see **D7** (§9).
 
### 5.3 System priority ordering
 
Boundary allocation (5.1) needs a **priority ordering** of systems — which of two adjacent systems is "higher priority." The order of preference for assigning a shared component to a system is:
 
1. **By schedule — earlier in service wins.** The system that is **earlier in service** has higher precedence than one **later in service**.
2. **By commissioning priority — as tie-break.** Where service order does not decide, the system with the **higher commissioning priority** wins over one with lower.
3. **Utilities over Process.** At a utility/process interface, the **Utilities system always takes priority** over the Process system (§5.5).
This ordering is the input the boundary-allocation rule consumes: the shared valve, blind, or spade is allocated to whichever adjacent system ranks higher under this sequence. Isolation valves are therefore **installed on (allocated to) the priority system**.
 
> The service schedule and commissioning-priority values are **project inputs**, not present in the P&ID source. Until they are supplied, Phase B records each shared boundary and the two systems it separates, and leaves the allocation unresolved (see **D6**, §9). The *ordering rule* is defined here; the *data to apply it* comes from the project schedule.
 
### 5.4 Boundary placement
 
- **System and sub-system breakdowns occur at isolation points.** A system or sub-system limit falls at an isolation point (valve, blind), consistent with 5.1.
- **Sub-system breakdowns preferentially at spacers / blind spacers.** Where a sub-system split is needed, locate it at a **spacer or blind spacer** if available. *This is recommended, not mandatory* — its purpose is to **reduce the number of temporary blinds** needed during commissioning.
- **Upstream and downstream must both be considered.** A boundary separates an upstream side from a downstream side; both are taken into account when placing and allocating the limit (relevant to directional rules such as sampling connections, §5.6).
### 5.5 Interface between Process and Utility systems
 
The physical limit of a Process or Utility system or sub-system shall, wherever possible, be an **isolation valve, allocated to the system or sub-system that has the highest priority and is commissioned first** (§5.1, §5.3). Additional cases:
 
- **Utility always takes priority over Process.** At a utility/process interface the Utilities system is the higher-priority side, so shared boundary hardware is allocated to it.
- **Isolation spades at the interface are installed on (allocated to) the priority system** — i.e. the Utilities system.
- **Where a blind is necessary, the sub-system limit is at the most suitable flange** — the blind is placed at the flange best suited to it, rather than forcing the limit to a valve.
- **When a facility is being extended, the limit is at the tie-in point** — the connection to existing plant defines the boundary.
### 5.6 Component-to-system allocation
 
Specific rules for which system a shared or ambiguous component belongs to. Each resolves a case the topological grouping alone cannot.
 
| Component | Belongs to | Rationale |
|-----------|-----------|-----------|
| **Blind / Spacer** | the system of the **isolation valves** it is associated with | keeps the blank with the isolation hardware that defines the boundary |
| **Isolation spade** (utility/process interface) | the **priority system** (Utilities, §5.5) | interface hardware follows the higher-priority side |
| **Steam trap** | the **Steam system** | the trap (with its pots/collecting lines) is on the steam side; the boundary is the **flange downstream of the trap** (§5.7) |
| **Sampling connection** | the **upstream** commissioning system | the sample point is taken with the system feeding it (§5.4, upstream/downstream) |
| **Flare isolation valve** (process ↔ flare) | the **Flare system** | the flare includes the isolation valves to the process; the flare limit is downstream of relieving devices (§5.9) |
| **Drain isolation valve / spectacle blind** (process ↔ drain) | the **Drain system** | the drain includes the isolation valves and spectacle blind at the process interface (§5.10) |
 
### 5.7 Steam and condensate systemization
 
**Each steam network is a separate system.** HP, MP, and LP steam each constitute their own system — they are not merged. A steam network system spans:
 
- its **header and branches**, up to the **last manual valve before the piping class change** to the user system;
- its **condensation pots and collecting lines**, up to the **first flange downstream of the steam traps**.
**Each condensate network is a single system.** A condensate network system spans its **headers and branches, up to the flange downstream of the steam traps**.
 
**The steam/condensate boundary is the first flange downstream of the steam trap** — not the trap itself. The steam side (including the trap, pots, and collecting lines) owns everything up to that flange; the condensate side owns everything from it. So:
 
- **The steam trap belongs to the Steam system** — it and its associated pots/collecting lines are on the steam side of the boundary.
- **The flange downstream of the trap is the limit** at which steam ends and condensate begins.
### 5.8 Nitrogen (inert gas) systemization
 
**Nitrogen / inert gas system limits are at piping class breaks, downstream of the last manual isolation valve.** The nitrogen system ends at the class break just past its final manual isolation valve — beyond that, the served system begins.
 
### 5.9 Flare systemization
 
The Flare system (or sub-system) **includes the isolation valves between the associated Process systems and the Flare** — the interface hardware belongs to the Flare side.
 
**The Flare limit is downstream of** the relieving/blow-down devices — i.e. the device stays with its Process system, and the Flare owns the network from the isolation valve onward. The limit is downstream of:
 
- Safety valves
- Pressure control valves
- Blow-down valves
- ESD (emergency shut-down) valves
Respecting piping class breaks (§5.2), **all piping components tested in the flare pressure test belong to the same system**, which facilitates pressure-test management — the same one-line-to-one-system principle applied to the flare network.
 
**Relief-device attribution.** A relief device (safety/relief valve) **stays with the system it protects** — the process line or equipment on its inlet side — not the flare it discharges to, consistent with the limit being *downstream* of the relieving device. The two sides are distinguished by flow direction: the side flow runs *from* (into the valve) is the protected side; the side the valve discharges *to* is the flare. The device takes the protected side's system. Exports carry relief devices under two component classes — `SafetyValveOrFitting` and `Reliefdevices` — and both are boundary-forming and both are attributed this way.
 
> **Computation status.** The flare *network* (the connected flare-fluid components) and the relief-device attribution above are computed. The rule's first clause — that the Flare system **absorbs the interface isolation valves/spades** between process and flare, up to the relieving/blow-down/ESD device — is **not yet computed**: reliably locating those limit devices needs instrument data not currently reconstructed (many are instrument-actuated, not distinguishable by component class). Until then the interface stubs are **flagged rather than absorbed** into the flare system. (An earlier keyword/class-based attempt at this absorption was found unreliable and removed; this is a clean deferral.)
 
> **Computation status.** The flare *network* (the flare-fluid components) is computed automatically at 100% source agreement, with a directional guard (reconstructed flow into the flare) preventing process fragments from mis-attaching. **Relief-device attribution is computed**: a relief valve whose protected side is directionally identifiable is assigned to that side (validated — process relief valves discharging to flare now commission with their process line, matching the source, instead of dead-ending). The **isolation-valve/spade inclusion** between process and flare is the correct business rule but is **not yet computed automatically**: reliably locating the safety/pressure-control/blow-down/ESD limit devices requires instrument data not currently reconstructed (many are instrument-actuated and not distinguishable by component class alone). Until then those interface stubs are left flagged rather than mis-assigned.
 
### 5.10 Closed drains and blowdown systemization
 
The Drain system's limits **include the isolation valves and the spectacle blind between the associated Process systems and the Drain system** — as with flare (§5.9), the interface hardware belongs to the Drain side. The Process system stops at that interface; the Drain system owns the isolation valve and spectacle blind onward.
 
### 5.11 Fire water distribution
 
Fire water is split **per SUP/MCP** (§2.4) — the SUP scoping matters, so the same rule is applied within each package:
 
- **For every SUP/MCP, all underground lines, monitors, and hydrants up to the first isolation valve of each branch constitute a single system** — provided isolation valves are available to bound it.
- **Deluge branches, including their deluge valves, are independent systems** — each deluge branch is its own system, separate from the distribution network.
### 5.12 Other water networks
 
- **Cooling water — supply and return are one system**, including the pump cooling system. The circuit is commissioned as a whole rather than split into supply and return.
- **Service water, potable water, boiler feed water (BFW), and demi water** each include the **header and branches up to the flange downstream of the last valve on the user system** (respecting piping class changes).
- **Utility pipework belongs to the Utilities systems** — consistent with the utility-priority rule (§5.5).
### 5.13 Chemical injection systemization
 
**The chemical injection system limit is downstream of the supply isolation valve** at each injection point into a process line or equipment item. Piping class breaks (§5.2) are met as far as possible. The injection system owns up to and including its supply isolation valve; the process line or equipment it injects into is on the other side of the limit.
 
### 5.14 Heat exchanger systemization
 
A heat exchanger connects two sides — often a process stream and a utility service (cooling water, steam, hot oil, refrigerant) — so it sits between systems and needs an explicit allocation rule. The exchanger is allocated by the following rules, **in order of precedence** (the first that applies wins):
 
**a. Specific cases:**
1. **Cooling-water exchangers** (`CW` supply / `HW` return) → the **cooling-water (FCW/SCW) sub-system**.
2. **Refrigerant-loop exchangers** (e.g. a C3/propane loop) → the **refrigerant-loop sub-system**.
3. **Steam-vs-process exchangers** (steam on one side, a process stream on the other) → the **Process sub-system**.
**b.** Otherwise, if none of the above applies → the **utility sub-system**.
 
**c.** Otherwise, if none of the above applies → the **shell-side sub-system**.
 
The allocation classifies the exchanger by the fluids on its two sides (detectable from the fluid codes at its nozzles). Where the deciding side is a **utility that owns itself** (steam/condensate §4.4, cooling water §5.12), the exchanger's own components take the rule-a/b/c assignment, while the **utility supply/return network stops at the exchanger nozzle** and remains its own system — it is not absorbed into the exchanger. This is the equipment-anchoring boundary applied to exchangers: the exchanger claims its directly-connected components; the through-utility keeps its identity.
 
> **Computation status.** Equipment-anchoring already claims an exchanger's directly-connected components correctly (validated: a heat exchanger's own nozzle-adjacent components sit in its own system). The **side-classification** that selects among rules a.1–a.3/b/c is a scoped build item: it requires reading the fluid on each exchanger side and, for the priority tie-breaks the philosophy allows, the project commissioning-priority schedule (a project input, §5.3). Until built, an exchanger is anchored by the general equipment rule (§4.1, step 3).
 
### 5.15 Instrument allocation
 
Where an instrument's system is not simply the line it sits on:
 
| Instrument case | Belongs to |
|-----------------|-----------|
| **Field instrument — soft tag** | the **P&ID Sub-system** (for management of loop tags and function tags — see Instrumentation Loop, [MD §2.8]) |
| **Instrument on a Vessel or Column** | the **equipment's** pre-commissioning system |
| **Instrument on a Pump** (incl. the pump driver's instrumentation) | the **pump's** system — driver instrumentation is associated with the pump |
 
These align with the master-data model: an instrument realises a function grouped into a loop ([MD §2.8]), and the loop is a natural handover unit; equipment-mounted instruments follow their equipment ([MD §2.9]) into that equipment's system.
 
---
 
## 6. The output object — Commissioning System
 
The single object this document produces. Built **Phase-C-ready**: it reserves a place for the source turnover assignment (§6.3) that Phase B populates-if-present but does not use.
 
### 6.1 Attributes
 
| Attribute | Meaning | Required |
|-----------|---------|----------|
| Internal ID | Stable identity of the computed system — `(SUP,fluid)` for utilities, `(SUP,anchor)` for process (§6.4). The durable key across runs; used to merge fragments and key review decisions. | Yes |
| Display name | The business name `{SUP}-{seq}-{fluid}` (§6.4); its composition shape is **reference-data-driven** (§6.4). A trailing `*` marks a provisional process sequence. | Yes |
| Start-Up Package | The SUP this system belongs to, resolved from members' Unit → SUP ([MD §3.6]) | Yes |
| System Category | Utilities or Process — from the fluid Category ([MD §3.4], §2.3). Never Non-Process (out of scope). | Yes |
| Member components | The set of Piping Components / Segments / Equipment / Instruments in the system | Yes (≥1) |
| Boundary components | The boundary-forming components that bound it ([MD §3.2]) | Yes |
| Fluid / Service | The medium(s) the system carries ([MD §3.4]) | No |
| Spanned Documents | The P&ID Document(s) the system covers (via off-page connectors) | Yes (≥1) |
| Fragment count | How many connected networks were merged (by internal ID) into this system | No |
| Provenance | Always `computed` in Phase B | Yes |
| Source turnover assignment | The `Z_TurnOverSystemNumber` / `SubsystemNo` of members, carried through — **not used in Phase B** (§6.3); absent for PostProc | No |
 
### 6.2 Notes
 
- **A system is a set of members plus its boundary.** The members are what gets commissioned together; the boundaries are where the package ends and the next begins.
- **A system can span Documents.** Because traversal follows off-page connectors [MD §2.11], a system is not confined to one drawing; `Spanned Documents` records which drawings it touches — essential for review.
- **Instrumentation travels with its process.** An Instrumentation Loop [MD §2.8] whose instruments sit on a system's components belongs to that system as a handover unit.
- **Provenance is always `computed`.** Every Commissioning System in Phase B is a product of the algorithm; none is read from the source.
### 6.3 Carried source assignment (Phase-C hook)
 
Each member component may carry a source turnover assignment (`Z_TurnOverSystemNumber` / `SubsystemNo`, [MD §4.2]). Phase B **records these on the system** (as the set of source assignments its members hold) but **does not compute with them, group by them, or reconcile against them**. They are retained so that Phase C can align computed systems to source systems without a schema change. On PostProc/project B input there is no turnover attribute, so the hook simply carries nothing and Phase-C reconciliation would rely on other means.
 
> A Phase-B system whose members hold *several different* source assignments is not an error in Phase B — it is exactly the kind of divergence Phase C will surface. Phase B simply records the set and moves on.
 
### 6.4 System name and internal ID
 
A computed system carries a stable **internal ID** and a human-readable **display name**. The naming convention is:
 
```
{SUP} - {seq} - {fluid}
```
 
for example `SUP07-020-SM` (Medium Pressure Steam) or `SUP07-002-PG` (a process-gas system). This resolves the former numbering question (D4): a system gets a business name composed — like the master-data Tags [MD §1.2] — from attributes the output object already holds, plus a sequence from reference data.
 
> **Project specification or contractual requirements prevail** over the convention below if they differ. An ancillary document (the system-boundary drawing and the system/sub-system list with codes) accompanies this one.
 
> **The naming shape is reference-data-driven.** The display-name composition — the field order, the separators, and which parts appear — lives in the **`TaggingConvention` sheet** (Object `CommissioningSystem`) of `Reference_Data.xlsx` and is rendered by the **same template engine** as the master-data Tags ([MD §3.3]): each row is a field (`sup`, `seq`, `code`), joined per the sheet's separator. The built-in default is `{SUP}-{seq}-{fluid}`. Changing the name shape is therefore a **reference-data edit, not a code change** — and the fuller project convention `SUPxx-Z(Z)-nnn-ppp_tttt` (§6.4.2) is expressible by adding its fields (`system_type`, `subsystem_seq`, `module`) to the template once sub-systems/modules are computed. Fields the template may reference: `sup`, `seq`, `code` (= fluid, or `P` for a mixed-process system, §6.4.1), `fluid` (alias of `code`), `system_type`, `subsystem_seq`, `module`.
 
**Name components:**
 
| Part | Meaning | Source |
|------|---------|--------|
| **SUP** | Start-Up Package code | the members' Unit → SUP ([MD §3.6]), chosen by the unit contributing the most components |
| **seq** | sequence number | **utility** systems: the stable per-fluid sequence in the System catalogue ([MD §3.5]); **process** systems: assigned by anchor-equipment ordering at compute time (provisional — see below) |
| **fluid** | fluid code | the system's fluid; or `P` when a process system carries more than one process fluid (§6.4.1) |
 
- **SUP** is resolved from each member's **Process Unit** via the Unit→SUP mapping (the Process Unit is the unit on the member's line Tag, [MD §2.1a, §3.6]; `UnitSUP` is keyed by that bare unit code, so the join is direct — for a member with no line Tag the unit falls back to its drawing number). It is not taken as a free input to the code — the product places each system in its SUP by the Process Unit its components belong to.
- **Utility vs process** is decided by the fluid's **Category** ([MD §3.4], `Process`), not by how the system was traced — a utility fluid traced to a consumer is still named as the utility it is.
**Stable internal ID.** Beneath the display name, each system has an internal ID that does not change between runs or across loaded-drawing subsets:
 
| System kind | Internal ID | Example |
|-------------|-------------|---------|
| Utility | `(SUP, fluid)` | `SUP07\|fluid:SM` |
| Process | `(SUP, anchor-equipment)` | `SUP07\|anchor:14R001` |
 
The internal ID is the durable identity — it is what the product uses to **merge** the several connected networks that make up one system, and to key the engineer's review decisions. The **display name may change** while the ID does not (see the provisional-number note). Downstream references (work packs, decisions, turnover) should cite the internal ID, or a utility name whose number is stable — never a provisional process number on its own. (The internal-ID scheme is a fixed key, distinct from the reference-data-driven *display*-name shape above.)
 
**Sequence stability — utility vs process:**
- **Utility sequence is stable.** It comes from the System catalogue ([MD §3.5]); `SM` is always `-020-` regardless of what is loaded.
- **Process sequence is provisional (run-scoped)** and is marked with a trailing **`*`** in the UI and exports (e.g. `SUP07-002-PG *`). Process systems have no fluid-sequence table; they are numbered by ordering their anchor-equipment tag within the SUP, so the number can shift if the set of loaded drawings changes. This is acceptable for the current phase because the stable handle is the internal ID; a production step could promote process numbering to a registered anchor→sequence table ([MD §3.5]-style) to make it stable, at which point the `*` is dropped.
#### 6.4.1 Mixed-process fluids — the `P` symbol
 
A **process** system that carries **more than one** process fluid (fluids with Category `Process`, [MD §3.4]) is named with the generic **`P`** fluid symbol in place of a single code — e.g. `SUP07-00N-P`. Utility tie-ins do not trigger this; only the presence of two or more distinct *process* fluids in one system does. (This rule is defined and implemented; on single-process-fluid data it does not fire, and it awaits a genuinely multi-process drawing to exercise.)
 
#### 6.4.2 Sub-system naming
 
Where a system is divided into sub-systems (§2.5), the sub-system name extends the system name with a progressive suffix and, where applicable, the construction-scope identifier — e.g. `SUP01-IA-001-001_AMR4` for the portion of system `SUP01-IA-001` inside module `AMR4`, `…-003` for the stick-built remainder. Sub-system derivation is **deferred** in this revision (§2.5); the naming shape is recorded here so the convention is complete. When it is built, the `subsystem_seq` and `module` fields of the `CommissioningSystem` naming template (§6.4) carry these parts, so the fuller `SUPxx-Z(Z)-nnn-ppp_tttt` name is produced by reference-data edit rather than code change.
 
## 7. The algorithm (Phase B)
 
A single deterministic pass over the master-data graph. No geometry beyond what [MD] already derived; no reconciliation.
 
1. **Load the graph.** Take the components as nodes and Connections [MD §2.12] as edges. The graph is undirected for grouping; the reconstruction also carries flow direction (the segment From/To sense along each centerline), used by three directional rules: the flare guard (step 5), the directional consumer guard (step 4), and relief-device attribution (step 6).
2. **Mark boundaries.** Flag every component whose class is in the boundary-forming list [MD §3.2] (isolation valves, positive-isolation spacers, steam traps, and relief devices — `SafetyValveOrFitting` and `Reliefdevices`).
3. **Form the self-owning utility systems.** Group each connected Steam/Condensate network (§4.4) as its own commissioning system, one per fluid — these are not traced to a consumer. (Steam and condensate carry different fluid codes, so per-fluid connectivity separates them at the trap.)
4. **Trace every remaining fluid to its consumer.** For each fluid not handled in steps 3 or 5, follow each connected fragment outward to its first consumer — an equipment nozzle (C1) or a different-fluid junction with a boundary (C2) — and set the system's edge at the §5 limit (last manual valve before a class change, class break, interface valve/blind, flange downstream of a trap). The **directional consumer guard** (§4.6) applies: a different-fluid neighbour is the consumer only if the fragment flows *toward* it; a neighbour flowing *into* the fragment is a supply tie-in, not a consumer. Services that ride with a parent (trim, blowdown, vents, drains) resolve to their equipment via C1. Apply equipment-anchoring (§4.1, step 3) for the equipment-centric cases. Each result is a candidate Commissioning System.
5. **Form the flare system.** Group the connected flare/relief-fluid network (§4.3) as its own collection system. A directional guard uses flow direction to keep process fragments from mis-attaching: a fragment whose flow runs *into* the flare is discharging to it, not consuming it.
6. **Attribute relief devices.** Assign each relief device to the system it protects — its inlet (protected) side by flow direction, not the flare it discharges to (§5.9).
7. **Attach instrumentation.** Assign each Instrumentation Loop [MD §2.8] to the system its instruments sit on.
8. **Record spanned Documents and carried source assignments.** For each system, note which Documents it touches and the set of source turnover assignments its members hold (§6.3).
9. **Name & emit.** Assign each system its internal ID and its display name — the display name **composed from the `CommissioningSystem` template in the `TaggingConvention` sheet** (§6.4) — and output each Commissioning System with members, boundaries, provenance `computed`.
- **Self-owning networks, then trace.** Flare and Steam/Condensate networks own themselves (they are their own systems); every other fluid is traced to its consumer the same way, subject to the directional consumer guard. There is no per-fluid classification beyond identifying these two self-owning groups.
- **The pass is deterministic.** Given the same master data and boundary list, the output is reproducible.
---
 
## 8. What Phase B deliberately does not do
 
Held for Phase C (reconciliation) or later, and named here so the boundary is explicit:
 
- **No reconciliation** against the source turnover assignment — no agree / conflict / source-only / computed-only status.
- **No set-alignment** of computed systems to source systems (the many-to-many matching problem).
- **No review workflow** for dispositioning differences.
- **No semantic-model / RDF output** — that remains a Gold-layer concern, as in [MD].
- **Sub-systems are included, but their split is guided not forced** (§2.5) — the product supports the Sub-system level; deciding *whether* a specific system is subdivided (by function grouping) is a commissioning-organisation judgement, not a purely topological output. No level below sub-system (no sub-sub-systems).
- **No SUP definition** (§2.4) — the SUP breakdown, sequence, and plot-plan battery limits are **project inputs consumed** by this product, not produced by it. The product places its computed systems *within* the given SUPs; it does not define the SUPs.
- **No Non-Process systems** (§2.3) — lighting, HVAC, structures, roads and the like are not P&ID-based and are outside what this product can produce.
---
 
## 9. Open decisions
 
These require commissioning-engineering or project input; they cannot be derived from the P&ID source. The PoC applies a working default where noted.
 
1. **Equipment-anchoring trigger.** Exactly when a process system is treated as equipment-centric (the vessel and its lines forming one system) rather than left to the per-fluid trace. *The PoC applies a working trigger — multi-service convergence on a tagged equipment item — pending engineering ratification.*
2. **Sub-system automation.** The product proposes sub-system candidates from isolation regions within a system; whether a given system is subdivided, and how finely, is partly a commissioning-organisation judgement (grouping equipment of the same function). Confirm the degree of automation vs. manual review.
3. **Boundary-list confirmation.** Confirm the [MD §3.2] boundary-forming classes for this project's commissioning philosophy — e.g. whether a check valve or a particular blind bounds a system — so that the domain limits (steam-trap flange §5.7; last manual valve before a class change §5.7–5.8; downstream of relieving devices for flare §5.9) can be placed against identifiable features.
4. **System naming (§6.4) — resolved, and now reference-data-driven.** The convention is `{SUP}-{seq}-{fluid}` with a stable internal ID beneath it. SUP resolves from Unit→SUP; utility `seq` is stable reference data ([MD §3.5]); the `P` symbol handles mixed-process systems. The **display-name shape is externalized** to the `CommissioningSystem` template in the `TaggingConvention` sheet (§6.4), rendered by the same engine as the master-data tags — so switching to the fuller `SUPxx-Z(Z)-nnn-ppp_tttt` convention is a reference-data edit. **Open only for process systems:** their `seq` is provisional (anchor-ordering, marked `*`) until a registered anchor→sequence table is introduced to make process numbers stable.
5. **Cross-document completeness.** How to handle a system that continues through an off-page connector [MD §2.11] whose mating document is not in the current input set — open boundary, or flagged incomplete?
6. **Priority data (§5.3).** The priority ordering rule is defined; the data (service schedule, commissioning-priority values) is a project input, not in the P&ID. Until supplied, shared-boundary allocation (§5.1) is recorded but left unresolved.
7. **Rule precedence (§5.1 vs §5.2).** When an isolation-valve/blind limit and a piping-class-break limit cannot coincide, which governs. *The PoC applies a working default — isolation valve first, then class break.*
8. **System-to-SUP matching.** A system maps to its SUP via its Process Unit ([MD §2.1a, §3.6]); the residual is that the **Unit → SUP mapping** must be provided as reference data (the `UnitSUP` sheet, keyed by the bare Process-Unit code, [MD §3.6]). Confirmed and implemented.
9. **Flare interface-hardware inclusion (§5.9).** The flare business rule includes the isolation valves/spades up to the relieving/blow-down/ESD device; automating this requires instrument data not currently reconstructed. Until then the flare network is computed and the interface stubs are flagged.
10. **Rating-based system split (§4.1).** The System definition names design pressure/temperature (piping rating) similarity as a criterion, but the current model groups a utility system by fluid only — a single named system may span more than one piping class. Whether to subdivide a fluid's system by rating band is deferred (fluid governs today).
11. **Sub-system by module (§2.5).** Module boundaries (`GenericComponent` `TP_Module1`/`TP_Module2`) make module sub-systems derivable; the derivation is deferred pending a cross-module utility system to validate against.