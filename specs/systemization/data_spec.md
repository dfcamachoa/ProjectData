# Automatic Pre-commissioning Systemization (P&ID) — Master Data, Reference Data & Metadata Specification
 
**Status:** Draft
**Version:** 0.6
**Date:** 2026-08-27
**Data Product:** Automatic Pre-commissioning Systemization based on P&ID interoperability data
**Scope:** This document defines the **Master Data, Reference Data, and Metadata** for the data product. These definitions establish the governed vocabulary the product uses to derive pre-commissioning systemization from P&ID drawings supplied as interoperability-standard files. The **computed systemization output** (commissioning systems/subsystems) is out of scope here — it is defined in a separate downstream specification that consumes this one.
 
> **Note on scope.** This specification covers master data, reference data, and metadata in business terms. Semantic-modeling concerns (formal ontology classes, identifiers, namespaces, and validation) are handled separately at the Gold layer and are intentionally left out here to keep the document accessible to the whole team.
 
> **Source formats.** The product accepts P&ID data in two interoperability standards: **DEXPI/Proteus XML** (project A) and **INGR ISO-15926 PostProc XML** with OriginatingSystem `SPPID` (project B). A format-specific adapter maps each to the same master-data objects defined here; all definitions below are format-independent unless a source-field mapping is explicitly noted per format. *(This supersedes the earlier "DEXPI-only" input decision: the SPPID/PostProc path is now a validated, first-class input.)*
 
> **Revision 0.3.** Adds the **Sub Piping System (Subline, §2.2a)** — an intermediate line level between the Pipeline System and the Piping Segment, present in project B and absent in project A.
 
> **Revision 0.4.** Promotes the **Process Unit (§2.1a)** to a first-class master-data object — the grouping level *above* the Pipeline System and the hinge to the commissioning hierarchy (Unit → Start-Up Package, §3.6). It was previously present only implicitly (a Tag field in §2.2 and a reference list in §3.6); this revision models it explicitly, with its **tag-unit ↔ drawing-unit** cross-check as a data-quality flag (§4.2).
 
> **Revision 0.5.** Relates the **Start-Up Package directly to the Process Unit** (§2.1a, §3.6). Previously SUP was reached by parsing the **drawing number** for a unit code keyed `A14` in `UnitSUP`; now `UnitSUP` is keyed by the **bare Process Unit code** (`14`, the code decoded from the line tag), so `SUP = UnitSUP[ProcessUnit]` — a direct join, independent of the drawing number. The drawing-number unit is retained only as the tag-vs-drawing **cross-check** (§4.2) and as a **fallback** for components that carry no tag unit (equipment, off-line instruments).
 
> **Revision 0.6.** Adds the **Start-Up Package (§2.1b)** as the **root of the master-data line hierarchy** — the grouping level *above* the Process Unit: `Start-Up Package → Process Unit → Pipeline System → Sub Piping System → Piping Segment → Piping Component`. The SUP is a **grouping node provided by project reference data** (the `SUP` and `UnitSUP` sheets, §3.6), not extracted from the P&ID; it is where the master-data line hierarchy meets the commissioning hierarchy (SS §2.2).
 
---
 
## 1. Purpose & scope
 
The first governance step for the data product: defining the three data classes it manages, so that pre-commissioning systemization can be derived reliably from P&ID interoperability data.
 
- **Master data** — the plant assets and drawings themselves, described in business terms and independent of any input format (Documents, Pipeline Systems, Piping Segments, and later valves, instruments, equipment, streams).
- **Reference data** — small, centrally-curated vocabularies every file resolves against (class mappings, fluid codes, units, materials classes).
- **Metadata** — split into **business metadata** (what the data means, §4.1) and **technical metadata** (schema, lineage, quality, §4.2).
### 1.1 How the Master Data supports systemization
 
Pre-commissioning systemization means grouping plant components into **commissioning systems and subsystems** — the packages a plant is cleaned, tested, and handed over in. The master data is the foundation that makes this grouping possible and auditable:
 
- **Every component has a stable identity (§1.2).** A commissioning system is ultimately a *list of tagged items*; systemization cannot assign a component to a system unless that component is uniquely and persistently identified. The Tag / identifier model gives each item that identity.
- **The Tag encodes the attributes systemization groups by.** Fluid/service, system, and unit — carried in the Pipeline System and Piping Segment Tags — are exactly the dimensions along which commissioning boundaries are drawn. Because these are governed reference values (§3), the same rule applies consistently across every drawing.
- **The Connection entity (§2.12) is the graph systemization walks.** Every link between objects — process pipe joins, nozzle attachments, instrument signals, and off-page continuations across drawings — is a first-class **Connection** record with a From/To object, nodes, type, and derived flag. Systemization traverses these Connections (together with the containment hierarchy Document → Pipeline System → Sub Piping System (Subline) → Piping Segment → Piping Component) to decide what belongs in the same commissioning package — a package that, via off-page connections, can span multiple drawings. Reifying the edge is what makes traversal, isolation boundaries, and connectivity validation possible against durable records.
- **The Document container scopes and provenances each assignment.** Because every component belongs to a Document, any systemization result can be traced back to the drawing(s) it came from — essential for review and sign-off.
In short: the master data supplies the *identified, classified, connected* components; systemization is the downstream step that partitions them into commissioning packages. This specification defines the former — the input master data extracted from the source — so the latter is reliable and repeatable. The systemization output model itself (computed system/subsystem objects and their boundary rules) is **out of scope for this document** and is defined in a **separate downstream specification** that consumes this one.
 
### 1.2 Identity: the Tag
 
Every master-data object has a **business identifier** — the Tag plant engineers use for assets, or the Document Number(s) for drawings. Two ways an identifier arises:
 
- **Direct** — a single identifier the business already assigns (e.g. a Document Number, a Pipeline System Tag, an instrument, a tagged valve). A Document has two direct identifiers: the client's and our internal one.
- **Composed** — no single identifier captures the object, so it is built by combining its defining attributes (e.g. a Piping Segment: fluid, unit, size, materials class, insulation).
> **Tagging conventions are project-specific.** The Tag structures and composition patterns in this document reflect the conventions of *this* project. Which attributes make up a Tag, their order, and their formatting are governed rules that **differ from project to project**. When onboarding a new project, its tagging convention must be captured as project reference data (§3) before its files are processed; the master-data model (the objects and their attributes) stays the same, only the Tag rules change.
 
Which rule applies depends on the object's class and is stated per object in §2. How Tags are constructed from the input file, and how internal keys guarantee uniqueness, are data-management concerns (§4.2) and do not surface to the business.
 
---
 
## 2. Master Data
 
Master data describes the plant assets in business terms — independent of any source file or its format. It answers *what the asset is*, not *where the data came from*. All mappings from the input file to these attributes live in the technical layer (§4.2).
 
> **Approach (locked):** master data is defined **gradually, object by object**. This revision defines **thirteen cross-project objects — the Document, the Process Unit, the Pipeline System, the Piping Segment, the Piping Component, the Instrument, the Actuator, the Instrument Function, the Instrumentation Loop, the Process Equipment (with its Nozzles), the Signal, the Off-Page Connector, and the Connection** — and their relationships, plus one **project-scoped** object, the **Sub Piping System (Subline, §2.2a)**, present in project B. Two of these — the **Process Unit (§2.1a)** and the **Start-Up Package (§2.1b)** — are *grouping* levels that root the derived line hierarchy rather than data extracted from the drawing: the Process Unit is decoded from the line Tag, and the Start-Up Package is provided by project reference data (§3.6). Further objects (streams) follow in later increments, each using the identity model of §1.2.
 
### 2.1 Document
 
**The P&ID drawing** — a single Piping & Instrumentation Diagram. One source file (DEXPI or PostProc) is generated per Document and holds its data. Every plant component shown on the drawing belongs to that Document: the Document is the **container** of the items represented on it.
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Client Document Number | The client's identifier for the drawing | `362-09-PR-PID-01010` | Yes |
| Document Number | Our organisation's internal identifier | `215777C-36209-PID-0021-01010` | Yes |
| Description | Title / subject of the drawing | `ACID GAS WASHING COLUMN` | Yes |
| Revision | Revision of the drawing | `01` | Yes |
| Contained items | The plant components represented on the drawing | *(see §2.13)* | Yes (≥1) |
 
**Identifiers** — a Document carries two identifiers, both business keys:
 
- **Client Document Number** (`362-09-PR-PID-01010`) — the client's own numbering.
- **Document Number** (internal) — `215777C-36209-PID-0021-01010`, structured as:
```
215777C - 36209 - PID - 0021 - 01010
   │        │       │      │       │
project   system/  doc    doc    sequence
number    unit     type   subtype number
```
 
| Part | Value | Meaning |
|------|-------|---------|
| Project number | `215777C` | Our project |
| — | `36209` | System / unit reference |
| Document type | `PID` | P&ID |
| Document subtype | `0021` | Subtype of document |
| Sequence number | `01010` | Sequence within the type/subtype |
 
The structure of the internal Document Number is a **project-scoped convention** (§3.3), like other Tag rules. The anatomy above is one project's scheme; another project's may differ (for example a unit-first number such as `A14-0001-001`). A component's **Process Unit** is taken from its **line Tag** (the *Unit* field, §2.2/§2.1a), not from the drawing number. The drawing number is parsed for a unit only as a **fallback** for components with no line Tag (equipment, off-line instruments) and as a data-quality **cross-check** against the tag unit — matched against the known unit codes in the Plant Unit catalogue (§3.6) so it adapts to any numbering scheme without code change ([ALG §9.3a]).
 
### 2.1a Process Unit
 
**A subdivision of the plant that groups Pipeline Systems** — the unit a plant engineer means by "unit 14" or "the acid-gas unit". A Process Unit sits **above** the line: it contains one or more Pipeline Systems (§2.2), and through them their Sublines, Segments, and Components. It is the top of the master-data line hierarchy and the **hinge to the commissioning hierarchy** — its code resolves to a Start-Up Package (SUP), which is what the computed commissioning systems are named under (SS §2.2, §6.4).
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Code | The unit code — the identifier embedded in every line Tag and drawing number | `14` | Yes |
| Description | The unit's name, from the Plant Unit catalogue (§3.6) | `Shift Conversion` | No |
| Functional Block | The functional block the unit rolls up to (§3.6) | `05` | No |
| Start-Up Package (SUP) | The SUP the unit belongs to, from the Plant Unit catalogue (§3.6) | `SUP07` | No |
| Pipeline Systems | The lines that run through this unit | *(see §2.13)* | Yes (≥1) |
| Spanned Documents | The P&IDs on which the unit's components appear | `A14-0001-001`, … | No |
 
**The unit code is the single join key.** It appears in two places in the source, and the Process Unit reconciles them:
 
- **Tag unit** — decoded from the line Tag (the *Unit* field of the Pipeline System, §2.2; e.g. `14` in `PG-14151`). This is the **grouping code and the SUP join key**: it keys the unit's *Description* and *Functional Block* (`Unit` sheet) **and** its *Start-Up Package* (`UnitSUP` sheet) in the Plant Unit catalogue (§3.6). `SUP = UnitSUP[ProcessUnit]` — a direct lookup on the bare unit code.
- **Drawing unit** — the unit code embedded in the P&ID **Document Number** (e.g. `A14` → `14` in `A14-0001-001`). Since `UnitSUP` is keyed by the bare code (`14`), the code is recovered from the drawing as a digit run. This is **not** the SUP join; it serves two lesser roles: a **fallback** unit for components that carry no line Tag (equipment, off-line instruments), and a **cross-check** against the tag unit.
The two views normally agree (e.g. `A14` ↔ `14`). Where they **disagree**, it is a data-quality signal, carried as a flag (§4.2), never silently reconciled. *(This is a change from an earlier design in which SUP was reached through the drawing unit, keyed `A14`; SUP now joins directly on the Process Unit — Revision 0.5.)*
 
**Notes**
 
- **It is a grouping/reference fact, not the commissioning grouping.** Like Pipeline-System membership (§2.2) and the Subline (§2.2a), the Process Unit says how the plant is *organised*, not which commissioning system a component ends up in — that is computed downstream. What makes the Process Unit special is that it is the level at which the master-data hierarchy **connects** to the commissioning hierarchy (via the SUP), so it is the natural place to roll a systemization result back up for review (a line "split" across systems is read against its unit — [SS §6.3]).
- **Derived by grouping, not a dedicated source element.** There is no `ProcessUnit` element in the source; the unit is recovered from the line Tag (tag unit) and the Document Number (drawing unit). The Process Unit is the grouping of a batch's Pipeline Systems on their shared unit code.
- **Cross-project.** Both projects have units (project A's `92`, project B's `14`); only the *keying* and the presence of the Subline level below differ. The Process Unit object is the same in both.
- **Data-quality flags it carries (§4.2).** (i) *tag-vs-drawing unit* — the line Tag's unit and the P&ID's unit disagree; (ii) *multi-unit line* — a single Pipeline System whose components decode to more than one unit code (attributed to the dominant one, flagged for review); (iii) *unknown unit* — a unit code absent from the Plant Unit catalogue, or with no SUP in `UnitSUP` (its SUP resolves to a placeholder).
### 2.1b Start-Up Package (SUP)
 
**The top of the plant's commissioning breakdown** — the stand-alone functional package a plant is started up and handed over in (a substation, a utilities block, a process unit, the flare). It is the **root of the master-data line hierarchy**: a Start-Up Package groups one or more **Process Units** (§2.1a), and through them their Pipeline Systems, Sublines, Segments, and Components. The full derived hierarchy is:
 
```
Start-Up Package  →  Process Unit  →  Pipeline System  →  Sub Piping System (Subline)  →  Piping Segment  →  Piping Component
   §2.1b               §2.1a             §2.2                 §2.2a                          §2.3               §2.4
```
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Code | The SUP identifier; its **number encodes the start-up order** (`SUP01` before `SUP02` …, SS §2.2) | `SUP07` | Yes |
| Description | The package name, from the `SUP` catalogue (§3.6) | `Ammonia plant` | No |
| Start-up sequence | The order position read from the code's number | `07` | No |
| Process Units | The Process Units this package contains | *(see §2.13)* | Yes (≥1) |
| Spanned Documents | The P&IDs on which the package's components appear | `A14-0001-001`, … | No |
 
**It is a grouping node provided by reference data, not extracted from the P&ID.** This is the defining difference from every object beneath it. A Document, a Pipeline System, a Segment — all are read from the source file. A Start-Up Package is **not**: which SUPs exist, their sequence, and their plot-plan battery limits are a **project input** established at project definition (SS §2.2, §2.4), delivered here as the `SUP` and `UnitSUP` reference sheets (§3.6). The P&ID contributes only the **Process Unit** (decoded from the line Tag, §2.1a); the `UnitSUP` catalogue is what **attaches** each Process Unit — and thereby each line, segment, and component beneath it — to its SUP. So the SUP sits atop the master-data hierarchy as its organising root, while its *authority* lives in the project breakdown, not the drawing.
 
**Notes**
 
- **It is the hinge to the commissioning hierarchy.** The commissioning breakdown is `SUP → System → Sub-system` (SS §2.1); the master-data line hierarchy is `SUP → Process Unit → … → Component`. The **Start-Up Package is the shared apex** of both — the computed commissioning systems (SS §6.4) are named under the same SUP a line rolls up to here, via the Process Unit → SUP join (§2.1a, §3.6). Rolling a systemization result up this tree therefore reads a line "split" (SS §6.3) against both its Process Unit and its package.
- **Derived by grouping.** There is no `StartUpPackage` element in the source; the package is recovered by grouping the batch's Process Units on their `UnitSUP`-resolved SUP code. A Process Unit whose code has no SUP in `UnitSUP` is grouped under a placeholder package and flagged (§4.2), never dropped.
- **Cross-project.** Both projects have a SUP breakdown; only the codes and the `UnitSUP` contents differ. The object is the same in both.
### 2.2 Pipeline System
 
**The whole line** — a run of piping carrying one fluid, in one service, through one plant unit, made up of one or more Piping Segments. This is the entity a plant engineer means by "the line".
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Tag | Business identifier of the line | `LS362920131` | Yes |
| Fluid / Service | The medium and service the line carries | `LS` (LP steam) | Yes |
| System | Process system the line belongs to | `362` | Yes |
| Unit | Plant unit the line runs through | `92` | Yes |
| Sequence Number | Serial number distinguishing lines of the same fluid/unit | `0131` | Yes |
| Segments | The Piping Segments that make up the line | *(see §2.13)* | Yes (≥1) |
 
**Tag** — the Pipeline System Tag is the line's natural identifier: **Fluid + System + Unit + Sequence** concatenated (`LS362920131` = `LS` · `362` · `92` · `0131`). It requires no construction; it is the identifier the business already uses.
 
Diameter, materials class, and insulation are **not** line attributes — a line spans segments that differ in those. They belong to the Piping Segment (§2.3).
 
> **Sub Piping System (Subline) — project B.** In project B the line is subdivided one level further before segments: a Pipeline System is composed of one or more **Sublines** (§2.2a), and each Subline of one or more Piping Segments. In project A there is no subline level — segments belong directly to the Pipeline System.
 
**Pipeline System nesting is a source structural fact, not a commissioning grouping.** A segment's membership in a Pipeline System reflects how the drawing organises the line — it is **not** a proxy for which commissioning system the segment belongs to. In the validation drawing, a vent (`VT`) segment was piped *inside* a low-pressure-steam Pipeline System yet was assigned to a *different* commissioning sub-system. Downstream systemization must therefore group on **fluid/service and commissioning intent**, not on Pipeline System containment. This keeps the master-data fact (the line a segment belongs to) distinct from the computed commissioning grouping (a separate downstream concern).
 
### 2.2a Sub Piping System (Subline)
 
**An intermediate line level** — a subdivision of a Pipeline System, sitting between the whole line (§2.2) and the individual Piping Segment (§2.3). A Pipeline System is composed of one or more **Sublines**, and each Subline is composed of one or more Piping Segments. This level is **project-scoped**: it is present in **project B** (the tag carries a distinct subline sequence) and **absent in project A** (whose lines go straight from Pipeline System to Piping Segment).
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Tag | Business identifier of the subline | `PG-1415109` | Yes |
| Pipeline System | The line this subline belongs to | `PG-14151` | Yes |
| Fluid / Service | The medium the subline carries | `PG` | Yes |
| Unit | Plant unit | `14` | Yes |
| Sequence Number | The Pipeline System sequence | `151` | Yes |
| Subline Sequence | Serial number distinguishing sublines within the line | `09` | Yes |
| Segments | The Piping Segments that make up the subline | *(see §2.13)* | Yes (≥1) |
 
**Tag** — the Subline Tag **extends its parent Pipeline System's Tag** with the **subline sequence**: `{PipelineSystemTag}{SublineSeq}` — e.g. `PG-14151` + `09` = `PG-1415109`. Like the Pipeline System, it is a direct identifier the business already uses; the subline sequence is carried in the source as the segment's tag suffix (§4.2).
 
The three line levels nest by tag prefix:
 
```
Piping System   PG-14151
  Subline       PG-1415109             = PG-14151 + 09
    Segment     36"-PG-1415109-D341H-H = Subline + diameter, class, insulation
```
 
**Notes**
 
- **Prefix integrity extends one level.** As a segment's Tag must begin with its subline's Tag, a subline's Tag must begin with its Pipeline System's Tag — a Subline whose Tag does not start with its line's Tag is mis-assigned (a business integrity rule, cf. §2.3).
- **It is a source structural fact, not a commissioning grouping.** Like Pipeline-System membership (§2.2), the Subline reflects how the drawing organises the line; systemization does **not** group on it. It is carried for **traceability** — knowing which subline (and line) a component belongs to — not as a grouping signal.
- **Derived by grouping, not a dedicated element.** In the source (project B) the Pipeline System is the `PipingNetworkSystem` and each `PipingNetworkSegment`'s `ItemTag` carries its Subline (§4.2); the Subline is recovered by grouping a line's segments on their shared subline identity.
- **Project-scoped.** A project's tagging convention (§3.3) declares whether the Subline level exists and how its tag is composed; onboarding a project without sublines simply omits it.
### 2.3 Piping Segment
 
**A single run of pipe** within a line — one continuous stretch of uniform specification (one diameter, one materials class, one insulation), bounded by its endpoints, carrying inline components. Every segment belongs to exactly one Sub Piping System (Subline, §2.2a) where the project defines that level, and thereby to exactly one Pipeline System; where the project has no subline level, it belongs directly to the Pipeline System.
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Tag | Business identifier of the segment | `AG362090006-44"(1C6AS)-S(45)(40)` | Yes |
| Pipeline System | The line this segment is part of | `AG362090006` | Yes |
| Fluid / Service | The medium the segment carries | `AG` | Yes |
| System | Process system | `362` | Yes |
| Unit | Plant unit | `09` | Yes |
| Sequence Number | Line serial number | `0006` | Yes |
| Nominal Diameter | Pipe size | `44"` | Yes |
| Piping Materials Class | Materials specification of the pipe | `1C6AS` | Yes |
| Insulation Type | Kind of insulation | `S` | No |
| Insulation Purpose | Why the line is insulated | `45` | No |
| Insulation Thickness | Insulation thickness (mm) | `40` | No |
| Flow Direction | Which end is the inlet — the intended flow orientation (§2.3.2) | End 1 is upstream (Inlet) | No |
| Centerline | The geometric path of the run — the ordered points the pipe follows (§2.3.1) | polyline of (x, y) points | Yes |
 
**Tag** — the Piping Segment Tag is **composed** because no single business identifier captures the segment's full specification. It extends its parent line's Tag with the physical spec. The pattern below is **this project's tagging convention** (§1.2) — another project may compose these attributes differently:
 
```
AG362090006-44"(1C6AS)-S(45)(40)
└─ line ──┘ └dia┘└class┘└insulation┘
 
Pattern (this project):  {Fluid}{System}{Unit}{Sequence}-{Diameter}({MaterialsClass})-{InsulationType}({InsulationPurpose})({InsulationThickness})
```
 
The segment Tag therefore **begins with its parent Pipeline System's Tag** — a segment whose Tag does not start with its line's Tag is mis-assigned (a business integrity rule).
 
> **Project B — the segment Tag begins with its Subline's Tag.** In project B the segment Tag extends the **Subline** Tag (§2.2a), not the Pipeline System Tag directly: e.g. `36"-PG-1415109-D341H-H` begins with the subline `PG-1415109` (which in turn begins with the line `PG-14151`). The example in the table above is project A, which has no subline level.
 
#### 2.3.1 Centerline
 
**The geometric path a segment's pipe follows on the drawing** — an ordered list of points (a polyline) tracing the run from one end to the other. The centerline is not just decoration: it is the geometry that **establishes the order of components along the run**, and so it underpins the connection topology of the network.
 
| Attribute | Meaning |
|-----------|---------|
| Points | The ordered (x, y) coordinates of the path, end to end |
| Point count | How many points define the path |
 
- **The centerline orders the inline components.** A segment's components (valves, fittings) are placed at positions along the pipe; projecting each component's location onto the centerline by distance-along-the-path (arc position) gives their **sequence** from one end of the segment to the other. This ordering is what turns a bag of components into an ordered chain `end → component → … → component → end`.
- **Ordering is what makes the Process connections correct.** The node-to-node Connections (§2.12, type Process) between *adjacent* inline components depend on knowing which component follows which — that adjacency comes from the centerline ordering. Without it, the components on a run are known but their sequence (and therefore which is next to which) is not.
- **Geometry is used only for ordering, not for the skeleton.** *Which* segments connect to which (the network skeleton) comes from shared endpoint components and is independent of geometry; the centerline is used **only** to order components *within* a segment. This keeps the reliable part (skeleton) separate from the geometric part (inline order).
- **Inline order is therefore a derived attribute.** Each component's position along its segment (§2.4) is computed from the centerline, so it is flagged **derived** (§4.2), reliable for sequencing but reconstructed rather than stated.
#### 2.3.2 Flow Direction
 
**Which way the medium flows through the segment** — its inlet-to-outlet orientation. Flow direction matters to systemization and commissioning because a commissioning package is often traced *with* the flow (source → destination), and because directionality distinguishes upstream from downstream of a valve or boundary.
 
Flow direction is a **single attribute on the segment**: a per-segment statement naming which end is the inlet — e.g. *"End 1 is upstream (Inlet)"*. Combined with the segment's two ends, it orients the run from inlet to outlet.
 
**Notes**
 
- **Connectivity does not depend on direction.** *Whether* two objects connect (the Connection, §2.12) is firm regardless of flow direction; direction is a **directed overlay** on the undirected connectivity. Isolation and reachability results are reliable regardless of direction — only upstream/downstream labelling depends on it.
- **Direction is carried onto the Connection.** A Process Connection can record the flow orientation across it (From → To) taken from the owning segment's flow direction.
### 2.4 Piping Component
 
**An individual item placed within a Piping Segment** — a valve, a fitting (reducer, flange, tee/branch), or a similar in-line part. Piping Components are the leaf items of the piping hierarchy: the actual things that get operated, isolated, and commissioned. Every Piping Component belongs to a Piping Segment, and some also serve as the junction where two segments connect (§2.12).
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Tag | Business identifier — present when the component is tagged; whether bulk items are tagged is project-defined (§3.3) | `362-QV050884` | No |
| Component Class | What kind of component it is | Gate Valve, Concentric Reducer | Yes |
| Piping Segment | The segment this component sits on | *(parent segment)* | Yes |
| Connection Nodes | The physical connection points on the component (§2.4.1) | node 1, node 2, … | Yes (≥1) |
| Nominal Diameter(s) | Pipe size(s) — one per connection node | `2"` (or `3"`/`6"` for a reducer) | Yes |
| Inline position | Order of the component along its segment, from the segment centerline (§2.3.1) | 1st, 2nd, … | No (derived — §4.2) |
 
**Identity — two cases (per §1.2):**
 
- **Tagged components** (valves, instruments) carry their **Tag directly** from the source — e.g. a manual valve `362-QV050884`. No composition.
- **Bulk components** (reducers, flanges, tees/branches) **may or may not be tagged** — this depends on the **client requirements defined for each project** (§3.3). Where a project requires them tagged, the Tag is taken from the source like any other; where it does not, they are legitimate master data identified only by their internal key, and a missing Tag is *not* a data-quality error. The product does not assume bulk items are untagged; it applies the project's tagging rule.
#### 2.4.1 Connection Nodes
 
Each Piping Component has one or more **connection nodes** — the physical points at which it joins other components. A node is where pipe actually meets the component: a simple in-line valve has two (inlet and outlet); a tee/branch has three; a reducer's two nodes carry *different* sizes.
 
| Node attribute | Meaning |
|----------------|---------|
| Node number | Identifies the point on the component (1, 2, 3, …) |
| Nominal Diameter | Pipe size at that node — may differ between nodes of the same component |
 
- **Nodes are where physical connections attach.** A connection between two components is a link from a node on one to a node on the other. The segment-to-segment connection (§2.12) is realised at the node level: a component on one segment is joined node-to-node to a component on the neighbouring segment.
- **Nodes explain multi-size components.** A component's several nominal diameters (a reducer's `6"` and `3"`) belong to its individual nodes, which is why diameter is recorded per node rather than once per component.
- **Node count reflects component role.** Two nodes = a pass-through item (valve, reducer); three or more = a junction (tee/branch) where segments meet — directly relevant to how the network is walked for systemization.
**Notes**
 
- **Component Class is governed reference data (§3.1).** It determines how the component behaves in systemization — e.g. whether it is a *valve* (a potential isolation/commissioning boundary) or a passive fitting.
- **A component may have more than one Nominal Diameter.** A reducer, by definition, joins two sizes; diameter is recorded per connection node (§2.4.1), not as a single value.
- **Bulk fittings are often where segments meet** (§2.12) — a branch or reducer belongs to one segment but connects, node-to-node, to a component on the neighbouring segment, forming the junction between the two runs.
### 2.5 Instrument
 
**A physical measurement or control device on the P&ID** — a transmitter, gauge, sensing element, venturi tube, or similar. An Instrument is a **specialisation of Piping Component (§2.4)**: it is a placed physical item with a Tag, a Component Class, and connection nodes, and it inherits all Piping Component attributes. What distinguishes an Instrument is that its purpose is to *sense or act on* the process rather than convey or stop flow.
 
Instruments come in two kinds by how they are mounted:
 
- **In-line Instrument** — installed *in* the pipe run, in direct contact with the process fluid (e.g. a **venturi tube**, an in-line flow element). It sits on a Piping Segment like any other Piping Component, with connection nodes, and is part of the flow path.
- **Off-line Instrument** — mounted beside the process (field-mounted); it attaches to the line or equipment it monitors but does not sit in the pipe and does not carry fluid.
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| *(inherits all Piping Component attributes — §2.4)* | Tag, Component Class, Connection Nodes, etc. | | |
| Instrument Kind | In-line or Off-line | In-line (venturi) / Off-line (field transmitter) | Yes |
| Attaches to | For an Off-line instrument, the line or equipment it monitors | *(monitored line / equipment)* | For Off-line |
 
**Tag** — an Instrument carries its **Tag directly** from the source and is **always tagged** (mandatory), so a missing Instrument Tag is a data-quality error. The Tag follows the instrument grammar defined in §3.3 (e.g. `362TE920076`).
 
**Notes**
 
- **Kind decides its place in the network.** An **In-line** instrument is a Piping Component on its segment (connection nodes, §2.4.1) and part of the flow path — the venturi tube is a node in the segment-to-segment connectivity (§2.12). An **Off-line** instrument is not in the flow path; it is associated with what it monitors.
- **The physical Instrument is distinct from its function.** What the instrument *does* — the measured variable, the control loop it participates in — is modelled separately as the **Instrument Function (§2.7)**. Each physical instrument realises exactly one function.
- **Instruments are items handed over in commissioning.** The source often already records a turnover system/subsystem for each instrument (§4.2), a useful cross-check against derived systemization.
### 2.6 Actuator
 
**The device that operates a valve** — the powered mechanism (pneumatic, motor-driven, hydraulic, solenoid) that opens, closes, or throttles a valve on command. Like the Instrument, an Actuator is a **specialisation of Piping Component (§2.4)**: a placed physical item with a Tag and a Component Class. It is not in the flow path itself; it is mounted on the valve it drives.
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| *(inherits all Piping Component attributes — §2.4)* | Tag, Component Class, etc. | | |
| Actuator Type | The operating mechanism | Pneumatic, Motor, Hydraulic, Solenoid | Yes |
| Operates | The valve (Piping Component) this actuator drives | *(driven valve §2.4)* | Yes |
 
**Tag** — an Actuator carries its **Tag directly** from the source where tagged; its tagging follows the project convention (§3.3).
 
**Notes**
 
- **An actuator turns a valve into an operated valve.** A plain valve is operated manually; an actuated valve is driven by its actuator on a control signal — which is what links it to the instrument/control side of the plant.
- **The actuator is driven by a control function.** The signal that moves it comes from an Instrument Function / control loop (§2.7); the actuator is the physical device that acts, the function is the logic that commands it.
- **Relevant to systemization as an operated element.** Actuated valves are commissioned and function-tested as part of their control loop; the actuator ties a valve into that loop's handover.
### 2.7 Instrument Function
 
**The functional role a single instrument performs** — one measurement or control function (a sensing element, a transmitter, an indicator, a controller). Where the Instrument (§2.5) is the *thing on the pipe*, the Instrument Function is *what that device does*. Each Instrument realises exactly one Function, and each Function belongs to an Instrumentation Loop (§2.8).
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Function Tag | Business identifier of the function | `362TE920076` | Yes |
| Measured Variable | The process variable measured or controlled | `T` (temperature), `F` (flow) | Yes |
| Function Modifier | The role letter | `E` (element), `T` (transmitter), `I` (indicator), `C` (controller) | No |
| Control Case | Whether the function participates in a control loop, and its role | Control | No |
| Realised by | The single physical Instrument that performs this function | *(instrument §2.5)* | Yes |
| Instrumentation Loop | The loop this function belongs to | `920076` | Yes |
 
**Tag** — the Function Tag follows the instrument grammar (§3.3): system + measured variable + modifier + sequence.
 
```
362TE920076   =   362 (system) · T (measured variable) · E (function modifier) · 920076 (sequence)
```
 
**Notes**
 
- **One Instrument, one Function.** Each physical device performs a single function; there is no multi-function device in this model (a deliberate simplification). Multiple functions of the same measurement point are separate Instruments, grouped by their shared Instrumentation Loop (§2.8).
- **Systemization hands over functions as well as devices.** A commissioning subsystem is validated on its instrument loops; the source's turnover system/subsystem (§4.2) applies at this level too.
### 2.8 Instrumentation Loop
 
**A logical instrument loop** — the group of instrument functions that together measure or control one process point (e.g. one temperature measurement made up of a sensing element, a transmitter, and an indicator). The loop is the unit engineers reason about when they talk about "the temperature loop"; it is not a physical item but a grouping of the functions that serve one point.
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Loop Tag | Business identifier of the loop | `920076` (or `362-920076`) | Yes |
| Measured Variable | The process variable the loop serves | `T` (temperature) | Yes |
| System / Unit | Where the loop belongs | `362` / `92` | Yes |
| Functions | The Instrument Functions that make up the loop | element, transmitter, indicator | Yes (≥1) |
 
**Tag** — the Loop Tag is the shared identity of its functions: the **sequence** portion of the instrument grammar, common to every function in the loop, optionally with system/measured-variable. Its member functions differ only by function modifier:
 
```
Loop  920076  (temperature)
 ├─ 362TE920076   sensing element
 ├─ 362TT920076   transmitter
 └─ 362TW920076   thermowell
```
 
**Notes**
 
- **The loop groups functions, not raw devices.** Each function in the loop is realised by one Instrument (§2.5, §2.7); the loop is the logical whole they form.
- **The loop is a natural commissioning unit.** Loops are handed over as coherent units; the source's turnover system/subsystem (§4.2) typically aligns to the loop, making it a strong anchor for systemization.
- **Loop membership is a project-scoped tagging matter (§3.3).** Which tag portion identifies the loop, and how functions are recognised as belonging to it, follow the project's instrument tagging convention.
### 2.9 Process Equipment
 
**A major process item on the P&ID** — a vessel, column, drum, pump, exchanger, tank, or similar. Process Equipment is where the process happens (separation, reaction, storage, pumping); piping runs to and from it. It is a top-level physical object, not a Piping Component: piping connects *to* it rather than passing *through* it.
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Tag | Business identifier of the equipment | `362-C-0001` | Yes |
| Equipment Class | What kind of equipment it is | Column, Vessel, Pump, Exchanger | Yes |
| Equipment Type | The project equipment-type code (§3.7) | `C` (Column) | Yes |
| Nozzles | The connection points on the equipment (§2.9.1) | N1, N2, … | Yes (≥1) |
 
**Tag** — Process Equipment carries its **Tag directly** from the source and is always tagged. The equipment-type letter comes from the project Equipment Type catalogue (§3.7, e.g. `C` = Column, `A` = Pond).
 
**Notes**
 
- **Equipment is a systemization anchor.** Commissioning systems are frequently built *around* equipment items (a column and its associated lines and loops form a package). Equipment is one of the primary things handed over.
- **Piping attaches at nozzles, not to the equipment body.** The connection between a line and its equipment is always at a specific nozzle (§2.9.1) — this is what lets systemization trace flow into and out of an equipment item precisely.
- **Real equipment must be filtered from export artefacts.** An SPPID/DEXPI export can contain `Equipment` elements that are **not real process items** — untagged placeholder or duplicate shells, often with no nozzles. (In the validation drawings, the majority of `Equipment` elements were untagged and nozzle-less artefacts; only a minority were real vessels.) Extraction must therefore keep an `Equipment` element **only if it is tagged and bears at least one nozzle**, and flag (not silently drop) any tagged-but-nozzle-less item for review. Without this filter, ghost equipment would surface as spurious systemization anchors. See the quality flags (§4.2).
#### 2.9.1 Nozzle
 
**A connection point on a Process Equipment** — the flanged opening where a pipe attaches to the vessel or column. A nozzle is a **sub-part of its equipment** (it belongs to exactly one equipment item) and is the point at which a Piping Segment connects to that equipment.
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Nozzle Tag | Identifier of the nozzle on its equipment | `N1` | Yes |
| Equipment | The equipment this nozzle belongs to | *(parent equipment)* | Yes |
| Nominal Diameter | Size of the nozzle opening | `6"` | Yes |
| Connected Segment | The Piping Segment attached at this nozzle | *(attached line)* | No |
 
- **Nozzles are always tagged** — tagging is mandatory, so a missing Nozzle Tag is a data-quality error (as for Instruments and Signals).
- **Piping connects to a nozzle via a Piping Component.** A Piping Segment attaches to a nozzle through a connecting component — typically a **flange** (§2.4) — joined node-to-node (§2.4.1), exactly as two segments connect to each other (§2.12). The nozzle is one side of that junction, the pipe's flange the other.
- **A nozzle bounds the piping network at the equipment.** Tracing the flow path, a nozzle is where a line terminates into (or originates from) equipment — a natural boundary for systemization.
- **Nozzles are how equipment enters the connectivity graph.** Equipment is not in a pipe run, but through its nozzles it is linked into the segment-to-segment network, so systemization can include equipment in the traced package.
### 2.10 Signal
 
**The link that connects the instrumentation entities** — the line carrying a measurement or command between instrument functions (e.g. from a field sensing element to the control-room indicator/controller). Where piping connectivity (§2.12) ties the *process* side together, the Signal ties the *instrument/control* side together. On the drawing it is the dashed line between instruments; it is not a pipe and carries no fluid.
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Tag | Business identifier of the signal | *(signal run tag)* | Yes |
| Signal Type | The kind of signal | Electric, Pneumatic, Software/Data | Yes |
| From | The source instrument function | `362TE920076` (sensing element) | Yes |
| To | The destination instrument function | `362TI920076` (indicator, control room) | Yes |
 
**Notes**
 
- **A Signal connects two instrumentation entities.** It links a source Instrument Function to a destination Instrument Function (§2.7) — for example carrying a temperature reading from the field element to the DCS indicator in the main board. This is the instrument-side counterpart of the segment-to-segment connection.
- **Signals build the loop's connectivity.** The functions grouped by an Instrumentation Loop (§2.8) are wired together by Signals; following the signals reconstructs the loop's signal path from measurement to display/control to actuation.
- **Signals carry commissioning relevance.** A control loop is function-tested end to end along its signal path; the source records a turnover system/subsystem on signals too (§4.2), aligning them to the same commissioning unit as their instruments.
- **Signals are always tagged** — like instruments, tagging is mandatory, so a missing signal Tag is a data-quality error.
### 2.11 Off-Page Connector
 
**The device that continues a line onto another P&ID Document** — where a pipe (or signal) leaves the edge of one drawing and picks up on another, an Off-Page Connector (OPC) marks the exit point and names where the line continues. Off-Page Connectors are how connectivity extends **beyond a single Document (§2.1)**: each connector on one drawing is paired with a matching connector on another, and together they join the two drawings' contents into one continuous network.
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| OPC Tag | Identifier of the connector | `108125` | Yes |
| On Segment | The Piping Segment that terminates at this connector | *(terminating line)* | Yes |
| Continues on Document | The other P&ID Document the line continues to | `362-92-02230` | Yes |
| Mating Connector | The paired connector on the other Document | `108126` | Yes |
| Description | What the line carries across | `LLP STEAM` | No |
 
**Notes**
 
- **A pair of connectors joins two Documents.** The connector on this drawing and its mating connector on the other drawing are two halves of one crossing; matching them (by mating tag / paired reference) reconnects a line that the drawing boundary split. This is the only place inter-Document connectivity is established.
- **Off-Page Connectors let systemization span drawings.** A commissioning system rarely fits on one P&ID; because connectors carry the mating Document and connector, the network can be traced across sheets, so a package that continues onto another drawing is followed rather than cut at the sheet edge (§1.1).
- **They apply to both process and signal lines.** A connector continues a Piping Segment (a process line crossing) or, analogously, a signal crossing — the same pairing mechanism in either case.
- **Off-Page Connectors are always tagged** — tagging is mandatory, so a missing OPC Tag is a data-quality error, and an unmatched connector (no mating pair found) is a connectivity data-quality flag (§4.2).
### 2.12 Connection
 
**A physical connection between two objects in the network** — promoted here from a mere relationship to a **first-class master-data entity**. A Connection is the *reified edge* of the plant graph: the explicit, addressable record that object A is joined to object B at specific nodes. Making the edge an entity (rather than only an implicit relationship) is what lets the product **traverse the network, store isolation boundaries, and validate connectivity** against durable, identified records.
 
| Attribute | Meaning | Example | Required |
|-----------|---------|---------|----------|
| Connection ID | Identifier of the connection (its own key) | *(internal key)* | Yes |
| From Object | The object at one end (a Piping Component, Nozzle, Instrument Function, Off-Page Connector, …) | *(component)* | Yes |
| To Object | The object at the other end | *(component)* | Yes |
| From Node | The connection node (§2.4.1) on the From Object | node 2 | No |
| To Node | The connection node on the To Object | node 1 | No |
| Connection Type | The kind of link | Process (pipe), Nozzle attachment, Signal, Off-Page continuation | Yes |
| Derived Flag | Whether the connection was stated in the source or reconstructed | Derived / Source | Yes |
 
**Why Connection is master data, not just a relationship**
 
- **Traversal.** An explicit edge with stable identity lets systemization walk the graph deterministically — from any object, follow its Connections to neighbours — without re-deriving links each time.
- **Isolation boundaries.** An isolation set is naturally expressed as a set of Connections cut at valves; storing the edge as an entity means a boundary can be recorded, named, and re-checked.
- **Validation.** Connectivity data-quality (dangling ends, unmatched off-page connectors, orphan nodes) is checked against Connection records rather than inferred on the fly.
**Connection Types** (one entity, several kinds of edge):
 
| Type | Joins | Notes |
|------|-------|-------|
| Process | a Piping Component to a Piping Component (node-to-node) | the segment-to-segment link where two runs meet at a fitting; may cross Pipeline Systems |
| Nozzle attachment | a Piping Component (flange) to a Nozzle | where a line attaches to Process Equipment (§2.9.1) |
| Signal | an Instrument Function to an Instrument Function | the instrument-side link carried by a Signal (§2.10) |
| Off-Page continuation | an Off-Page Connector to its mating connector | the cross-Document link (§2.11) — the two ends live on different drawings |
 
**Notes**
 
- **Direction is separate from the connection.** *Whether* two objects connect (the Connection itself) is firm; the *direction* of flow or signal across it is a distinct, directed overlay taken from the owning segment's flow direction (§2.3.2). Connectivity is trustworthy regardless of direction — so the Connection is the reliable backbone, direction an overlay.
- **The Derived Flag preserves provenance.** Connections read directly from the source are marked `Source`; those reconstructed (e.g. inferred node-to-node piping links, matched off-page pairs) are marked `Derived` — traceability the graph carries with every edge (§4.2).
- **A Connection can cross Pipeline Systems and Documents.** Process connections may join segments of different lines; off-page continuations join objects on different Documents. The Connection entity is what makes those crossings explicit and followable.
### 2.13 Relationships
 
Connectivity between objects is carried by the **Connection** entity (§2.12); the rows below marked *(via Connection)* are realised as Connection records rather than as direct links.
 
| Relationship | Cardinality |
|--------------|-------------|
| Document **contains** plant components (Pipeline Systems, Sub Piping Systems, Piping Segments, Piping Components, Instruments, Actuators, Instrument Functions, Instrumentation Loops, Process Equipment, Signals, Off-Page Connectors, Connections, and other items) | one → many (≥1) |
| Plant component **appears on** a Document | many → one *(per drawing)* |
| Start-Up Package **groups** Process Units *(via `UnitSUP`, §2.1b, §3.6)* | one → many (≥1) |
| Process Unit **belongs to** one Start-Up Package | many → one |
| Process Unit **groups** Pipeline Systems | one → many (≥1) |
| Pipeline System **belongs to** one Process Unit *(via its Unit code, §2.1a)* | many → one |
| Pipeline System **is composed of** Sub Piping Systems (Sublines) *(project B)* | one → many (≥1) |
| Sub Piping System **belongs to** one Pipeline System *(project B)* | many → one |
| Sub Piping System **is composed of** Piping Segments *(project B)* | one → many (≥1) |
| Pipeline System **is composed of** Piping Segments *(directly in project A; via its Sublines in project B)* | one → many (≥1) |
| Piping Segment **belongs to** one Sub Piping System *(project B)* / one Pipeline System *(project A)* | many → one |
| Piping Segment **contains** Piping Components | one → many |
| Piping Component **belongs to** one Piping Segment | many → one |
| Connection **joins** two objects *(From/To, at nodes)* | one → two |
| Piping Component **connects to** Piping Component *(via Connection, type Process)* | many ↔ many |
| Instrument **is a** Piping Component | subclass |
| In-line Instrument **is installed in** a Piping Segment | many → one |
| Off-line Instrument **monitors** a line, segment, or equipment | many → one |
| Instrument **realises** one Instrument Function | one → one |
| Instrument Function **is realised by** one Instrument | one → one |
| Instrumentation Loop **groups** Instrument Functions | one → many (≥1) |
| Instrument Function **belongs to** one Instrumentation Loop | many → one |
| Actuator **is a** Piping Component | subclass |
| Actuator **operates** a valve (Piping Component) | one → one |
| Actuator **is driven by** an Instrument Function *(control signal)* | many → one |
| Process Equipment **has** Nozzles | one → many (≥1) |
| Nozzle **belongs to** one Process Equipment | many → one |
| Piping Component (flange) **connects to** a Nozzle *(via Connection, type Nozzle attachment)* | many → one |
| Instrument Function **connects to** Instrument Function *(via Connection, type Signal)* | many ↔ many |
| Off-Page Connector **terminates** a Piping Segment | one → one |
| Off-Page Connector **connects to** its mating connector *(via Connection, type Off-Page continuation, on another Document)* | one → one |
 
- A Document contains one or more plant components; each component shown on a drawing belongs to that Document.
- A Start-Up Package (§2.1b) groups one or more Process Units, resolved through the `UnitSUP` catalogue (§3.6); it is the reference-data-provided root of the derived line hierarchy and the apex shared with the commissioning hierarchy (SS §2.2).
- A Process Unit groups one or more Pipeline Systems (by their shared Unit code, §2.1a) and belongs, via the Plant Unit catalogue (§3.6), to one Start-Up Package — the point where the master-data line hierarchy meets the commissioning hierarchy.
- A line has one or more segments; each segment belongs to exactly one line. In project B the line is subdivided into Sublines (§2.2a) between the line and its segments; in project A segments belong directly to the line.
- A segment carries zero or more Piping Components; each component sits on exactly one segment.
- **Every physical or signal link between objects is a Connection (§2.12)** — one entity with a type (Process, Nozzle attachment, Signal, Off-Page continuation), giving one uniform, addressable edge for traversal, isolation, and validation.
- An In-line instrument sits in one segment and is part of its flow path; an Off-line instrument monitors one line, segment, or equipment and does not carry flow.
- Each Instrument realises exactly one Instrument Function (one-to-one); the functions of one measurement point are grouped by their Instrumentation Loop.
- An Instrumentation Loop groups one or more Instrument Functions; each function belongs to exactly one loop.
- An Actuator operates one valve and is driven by a control function; an actuated valve is one that has an actuator.
- Process Equipment has one or more Nozzles; each nozzle belongs to exactly one equipment item, and a Piping Segment attaches to equipment at a nozzle via a Connection.
- A Signal connects a source Instrument Function to a destination Instrument Function; the signals of a loop wire its functions together.
- An Off-Page Connector terminates a line at the drawing edge and, via a Connection, pairs with a mating connector on another Document.
---
 
## 3. Reference Data
 
Small, governed lists every asset resolves against. Reference data is of two scopes:
 
- **Shared** — stable across projects (units of measure, the component classification catalogue).
- **Project-scoped** — defined per project and captured at onboarding (tagging conventions, process fluids, systems, units, equipment types, materials classes). These **differ from project to project**.
### 3.1 Component class catalogue *(shared)*
 
The governed list of component classes the product recognises, grouped by family. Most classes are well-established; three specialised classes are **not yet standardised** and are on the governance backlog:
 
| Component class | Family | Status |
|-----------------|--------|--------|
| Gate / Check / Ball / Globe Valve | Valve | Recognised |
| Concentric Reducer, Flange, Blind Flange, Pipe Branch | Fitting | Recognised |
| Steam Trap | Fitting / boundary-forming | **Missing from source class list — see note** |
| Hose Rack Station | — | **Not yet standardised — backlog** |
| Steam Trace Header | — | **Not yet standardised — backlog** |
| Condensate Recovery Header | — | **Not yet standardised — backlog** |
| Field instruments, instrument functions | Instrument | Recognised |
 
This catalogue governs *which* classes exist and their standardisation status. Mapping these classes to formal reference-data identifiers is a separate Gold-layer activity, out of scope here.
 
> **Steam Trap — a known gap.** The validation drawing contained **no component classified as a steam trap**, yet the steam trap is the boundary between a steam system and its condensate system (a downstream systemization rule). Because the source export does not carry a `SteamTrap` class, the steam/condensate boundary cannot be located by class alone and must be detected another way (e.g. the fluid change from steam to condensate at the flange, or a materials-class break). Recognising Steam Trap as a class — and defining how it is identified when the source omits it — is an open reference-data item.
 
### 3.2 Boundary-forming component classes *(project-scoped)*
 
Systemization needs a governed list of the component classes that **form a commissioning or isolation boundary** — the places where a package naturally ends. This is a reference list (which classes act as boundaries), not a new master-data object; the components themselves are already Piping Components (§2.4). The list is grounded in the classes actually present in the source export.
 
**Boundary-forming classes** (present in the project drawings):
 
| Class | Boundary role | Boundary? |
|-------|---------------|-----------|
| `GateValve` | Manual block/isolation valve | **Yes** |
| `GlobeValve` | Manual isolation (throttling, but isolates) | **Yes** |
| `ButterflyValve` | Manual isolation valve | **Yes** |
| `PipeFlangeSpacer` | Spacer / spectacle-blind position — positive isolation, preferred limit [SS §5.4] | **Yes** |
| `SteamTrap` | Steam→condensate boundary (special, directional) [SS §5.7] | **Yes (special)** |
| `SafetyValveOrFitting` | Relief device — bounds the flare/relief side, limit downstream of relieving devices [SS §5.9] | **Yes (relief)** |
| `Reliefdevices` | Relief device — the second class name used for safety/relief valves; same role as `SafetyValveOrFitting` [SS §5.9] | **Yes (relief)** |
| `CheckValve` | Non-return device — **not** a manual isolation; does not bound a system *(project decision)* | **No** |
| `Flange`, `PipeReducer`, `RestrictionOrifice`, instruments | Joints, fittings, sensing — not isolations | No |
 
**Decisions and notes:**
 
- **Manual-isolation valves plus two special boundaries.** `GateValve`, `GlobeValve`, `ButterflyValve`, and `PipeFlangeSpacer` bound a system by manual/positive isolation. Two further classes bound by domain rule: **`SteamTrap`** (steam↔condensate, [SS §5.7]) and **relief devices** (the flare/relief limit, [SS §5.9]). Exports carry relief devices under **two** class names — `SafetyValveOrFitting` **and** `Reliefdevices` — and both are treated as boundary-forming. A **`CheckValve` is not a boundary** — it is directional, not man-operable to isolate — so it is carried as an ordinary component but never chosen as a system limit (project decision).
- **Blinds and line breaks appear as spacers here.** The generic candidates "spectacle blind / blind flange / line break" have **no dedicated class in this export**; positive-isolation points are represented by `PipeFlangeSpacer`. The list follows the data, not the generic names.
- **The steam trap is a special, directional boundary** — it bounds steam from condensate [SS §5.7] and is detected by the `SteamTrap` class (confirmed present, [WE §7]); where absent, the steam→condensate fluid change is the fallback signal.
- **It is project-governed and drives, but is separate from, the output.** The list says *which components can bound a package*; the actual boundaries chosen are computed by the downstream systemization algorithm, not this document.
- **Implementation status.** This governed list is now loaded from the `Boundary` reference sheet (columns *Class*, *Role*, *Boundary*) rather than held as code constants — the four role-sets (isolation / positive / relief / trap, with `CheckValve` excluded) are read from reference data, with a built-in fallback. Changing a project's boundary classes is a reference-data edit, not a code change.
### 3.3 Tagging conventions *(project-scoped)*
 
The per-object Tag rules the project uses — **which classes are tagged**, and for each, which attributes compose the Tag, in what order and format (see §1.2, §2). Both the scope of tagging (e.g. whether bulk fittings carry Tags) and the Tag structure are set by the **client requirements defined for each project**. This project's conventions:
 
| Object | Tag rule |
|--------|----------|
| Document (internal Number) | `{Project}-{System/Unit}-{DocType}-{DocSubtype}-{Sequence}` (e.g. `215777C-36209-PID-0021-01010`) |
| Pipeline System | Direct: `{Fluid}{System}{Unit}{Sequence}` |
| Sub Piping System (Subline) *(project B)* | Direct: `{PipelineSystemTag}{SublineSeq}` (e.g. `PG-14151` + `09` = `PG-1415109`) |
| Piping Segment | Composed: (Sub) Pipeline System Tag + `-{Diameter}({MaterialsClass})-{InsulationType}({InsulationPurpose})({InsulationThickness})` |
| Piping Component (tagged) | Direct, e.g. valve `362-{ValveType}{Sequence}` (`362-QV050884`). Whether bulk fittings are tagged is set by the project's client requirements |
| Instrument / Instrument Function | Direct: `{System}{MeasuredVariable}{FunctionModifier}{Sequence}` (e.g. `362TE920076`) — the same grammar identifies the function and the device realising it |
| Instrumentation Loop | The shared `{Sequence}` (optionally with `{System}{MeasuredVariable}`) common to a loop's functions, e.g. `920076` — functions differ only by function modifier |
| Process Equipment | Direct: `{System}-{EquipmentType}-{Sequence}` (e.g. `362-C-0001`), equipment-type letter per §3.6 |
| Nozzle | Direct: nozzle identifier on its equipment (e.g. `N1`), always tagged |
| Signal | Direct: signal-run tag from the source (always tagged) |
| Off-Page Connector | Direct: OPC tag from the source (e.g. `108125`), always tagged; the mating connector on the other Document carries the paired tag |
 
A different project may order or format these differently, tag additional or fewer object classes, or require bulk items tagged where this one does not; its convention replaces this table without changing the master-data model. In particular, whether the **Sub Piping System (Subline)** level exists is project-scoped — project A omits it (segments belong directly to the Pipeline System). These conventions are captured in the `TaggingConvention` reference sheet (decode widths/separator and the per-object composition templates) so onboarding a project is a reference-data edit, not a code change.
 
### 3.4 Process Fluid catalogue *(project-scoped)*
 
Fluids are governed by a **four-column catalogue**: **Category → Subcategory → Fluid Code → Fluid Description**. The **Fluid Code** is what appears in a Tag ([MD §2.2–2.3]); the **Category** and **Subcategory** classify the fluid for downstream use. The catalogue is a project reference list (delivered in `Reference_Data.xlsx`, sheet *Fluid*); the code set and the categories present are **project-specific** — different projects enumerate different fluids and may use different Category values.
 
**Categories.** The categories the systemization consumes are **`Process`**, **`Utility`**, **`Flare`**, and (project-dependent) ancillary groupings. Example rows (project-specific — a code's category can differ between projects):
 
| Category | Subcategories (examples) | Example codes |
|----------|--------------------------|---------------|
| **Process** | Process General, Refrigerant, Solvent, Chemical, Catalyst | `PG`, `NG`, `FG`, `H`, `CF`, `AW` |
| **Utility** | Steam / Condensate, Nitrogen, Air, Water, … | steam `HHS`/`HS`/`MS`/`LS` (or `SM`/`SL`/`SH`), condensate `SC`/`SCL`/`SCM`/… (or `PC`), `AI` (Instrument Air), `AP` (Plant Air), `WM` (Service Water) |
| **Flare** | Flare | project A: `SF`/`RV`/`CV`; project B: `AG`/`FA` |
 
**Category now selects systemization behaviour for two cases — this is a change from earlier revisions:**
 
- **Flare** — a fluid with **Category = `Flare`** forms its own flare collection system ([SS §5.9], [ALG §6]). Which codes are flare is therefore read directly from this catalogue, per project. (In an earlier revision flare membership was a hardcoded code list `SF`/`RV`/`CV`; it is now catalogue-driven, so project B's `AG`/`FA` resolve correctly. Note `AG` is **Process** in one project's catalogue and **Flare** in another's — the catalogue, not a fixed code list, is authoritative.)
- **Process** — a fluid with **Category = `Process`** is named as a process system; a system carrying **more than one** Process fluid takes the generic `P` fluid symbol in its name ([SS §6.4], [ALG §9.3]).
**Category does *not* select the distribute-vs-dedicated topology.** Whether a fluid forms its own network or traces to many users is still **computed from connectivity**, not read from the Category — a Process fluid may distribute across many systems while a Utility fluid is dedicated to one. Category governs the flare/process-naming decisions above; connectivity governs the trace.
 
**Steam and condensate.** A fluid is treated as steam/condensate when its Category is `Utility` and its **Subcategory contains** `Steam` or `Condensate` (the substring test accepts `Steam`, `Condensate`, and a combined `Steam / Condensate` spelling). The distinct **codes** (one per pressure level and per condensate type) are what make each steam and condensate network a separate system ([SS §5.7]) — the systemization keys on the code, not the subcategory.
 
### 3.5 System catalogue — utility system sequence *(project-scoped)*
 
The System catalogue assigns each utility fluid a **stable sequence number** and a descriptive **system name**, used to build the commissioning-system display name `{SUP}-{seq}-{fluid}` ([SS §6.4], [ALG §9.3]). Delivered in `Reference_Data.xlsx`, sheet **`SystemSeq`**:
 
| Column | Meaning |
|--------|---------|
| **Fluid Code** | the utility fluid code (matches the Fluid catalogue, §3.4) |
| **Seq** | the stable sequence number for that fluid's system (e.g. `020`) |
| **System Name** | the descriptive name (e.g. *Medium Pressure Steam System*) |
 
Example rows:
 
| Fluid Code | Seq | System Name |
|-----------|-----|-------------|
| `FA` | 003 | Process Gas Vent System |
| `AI` | 007 | Instrument Air System |
| `WF` | 010 | Firewater System |
| `AP` | 011 | Service Air / Plant Air System |
| `SM` | 020 | Medium Pressure Steam System |
 
Notes:
- The sequence is **stable reference data** — a utility system's number does not change between runs. (Process systems have no entry here; they are numbered provisionally by anchor-equipment order at compute time — [ALG §9.3].)
- A utility fluid **absent** from this sheet is named with sequence `000`, a visible flag to add the row.
- This sheet governs *utility* commissioning-system naming; it is distinct from any process-system tag-numbering that may appear inside Tags.
### 3.6 Plant Unit catalogue — Process Unit → Start-Up Package *(project-scoped)*
 
The Plant Unit catalogue maps each **Process Unit to a Start-Up Package (SUP)** and, in doing so, also defines the **valid unit codes**. It is keyed by the **bare Process Unit code** — the same code decoded from the line Tag (§2.1a, §2.2), e.g. `14`, **not** a drawing-prefixed form like `A14`. Delivered in `Reference_Data.xlsx`, sheet **`UnitSUP`**:
 
| Column | Meaning |
|--------|---------|
| **Unit** | the Process Unit code, as decoded from the line Tag (e.g. `14`) |
| **SUP** | the Start-Up Package it belongs to (e.g. `SUP07`) |
 
This catalogue has **one primary role and one secondary role**:
 
1. **SUP resolution — the direct join.** A system's SUP is the SUP of its members' **Process Units**, chosen by the unit contributing the most components ([ALG §9.3]). Because the sheet is keyed by the Process Unit code, this is a **direct lookup** — `SUP = UnitSUP[ProcessUnit]` — with no drawing-number parsing in the path. The mapping is **many units → one SUP** (a Start-Up Package spans several process units); it is not one-to-one. *(Revision 0.5: previously the join went through a drawing-derived unit keyed `A14`; the leading character has been dropped so the key equals the Process Unit.)*
2. **Drawing-number parser — fallback & cross-check only.** The unit-code set is *also* the dictionary for recovering a unit from a **drawing number**, used (a) as a fallback for components that carry no line Tag (equipment, off-line instruments), and (b) as the tag-vs-drawing cross-check (§2.1a, §4.2). Because the codes are now bare, the drawing carries the code as a digit run: `A14-0001-001` → `14`; a later field `216097C-A14-…` → `14` ([ALG §9.3a]). A project with a different drawing-numbering scheme is onboarded simply by listing its Process Units here — no code change. A drawing whose number matches no listed unit is flagged.
Example rows:
 
| Unit | SUP |
|------|-----|
| `13` | SUP07 |
| `14` | SUP07 |
| `22` | SUP07 |
 
**Companion `SUP` sheet — package descriptions.** A separate `SUP` sheet gives each Start-Up Package code a description, used to label the package in the master-data hierarchy (§2.1b). It carries no P&ID-derived data — the SUP breakdown itself is a project input (SS §2.2, §2.4).
 
| Code | Description |
|------|-------------|
| `SUP01` | Main substation |
| `SUP07` | Ammonia plant |
 
The package's **start-up sequence** is read from the number in its code (`SUP01` before `SUP02` …, SS §2.2); no separate order column is needed.
 
### 3.7 Equipment Type catalogue *(project-scoped)*
 
The equipment-type code is the letter(s) embedded in an Equipment Tag ([MD §2.9]) that classify the item. Governed list (from `Reference_Data.xlsx`, sheet *Equipment Type* — 33 codes):
 
| Code | Equipment Type | | Code | Equipment Type |
|------|----------------|---|------|----------------|
| A | Pond, Basin, Concrete Pit | | S | Filter |
| B | Boiler | | SEW | Safety Shower with Eye Washer |
| C | Column | | SSWR | Safety Shower |
| D | Desuperheater | | T | Tank, Pit, Silo, Hopper |
| E | Heat Exchanger / Air Cooled | | V | Vessel, Drum, Converter, Reactor |
| EW | Eye Wash | | WE | Workshop Equipment |
| F | Furnace | | X | Miscellaneous |
| FH | Fire Hydrant | | XC | Crane |
| FL | Flare | | XH | Steel Structure, Platform |
| G | Electric Generator | | XK | Building, Loading Station |
| H | Electrical Heater | | XM | Mixer / Agitator |
| HG | Heat Recovery Steam Generator | | XP | Portable Equipment |
| HT | Hydraulic Turbine | | XZ | Fire and Safety Equipment |
| K | Compressor, Blower | | Y | Package Unit |
| L | Loading Arm | | YH | HVAC Equipment (Structure) |
| LB | Liquid Burner | | YK | HVAC Equipment (Building) |
| P | Pump | | | |
 
The real vessel in the validation drawing (`362-C0953`) is type `C` (Column), consistent with this list.
 
### 3.8 Unit-of-measure catalogue *(shared)*
 
| Unit | Used for |
|------|----------|
| Inch | Nominal diameter |
| mm | Insulation thickness |
| °C | Temperature |
 
### 3.9 Piping Materials Class list *(project-scoped)*
 
Governed list of materials-class codes (e.g. `1S1A`, `1C6AS`), each defining a piping specification. Referenced by every Piping Segment; full enumeration to come from the piping specification (§6.2).
 
### 3.10 Instrument function codes *(project-scoped)*
 
The instrument tag letters — the **measured variable** (first letter) and **function modifier** (following letter) — usually follow an ISA-style convention adapted per project. Started here from the sample:
 
| Code | Position | Meaning |
|------|----------|---------|
| T | Measured variable | Temperature |
| E | Function modifier | Element (primary sensing) |
| T | Function modifier | Transmitter |
| W | Function modifier | Well / thermowell |
| *(others — §6.2)* | | |
 
### 3.11 Insulation Type catalogue *(project-scoped)*
 
The Insulation Type code appears in the composed Piping Segment Tag ([MD §2.3], §3.3). Governed list (from `Reference_Data.xlsx`, sheet *Insulation Type* — 31 codes):
 
| Code | Meaning | | Code | Meaning |
|------|---------|---|------|---------|
| N | Non-Insulation | | 9 | Cold-service personnel protection / condensation (below −10 °C) |
| 1 | Heat Conservation | | S | LP Steam Tracing |
| 2 / 2M | Personnel Protection (mass / metal mesh) | | SA–SD | Steam Tracing + Acoustic class A–D |
| 3A–3D | Heat Conservation + sound control A–D | | E | Electrical Heat Tracing |
| 4A–4D | Sound Control A–D | | JF | Steam Jacketing (Full) |
| 5 | Reduction of Heat Gain / Condensation Control | | ZF | Controtrace |
| 5N | Heat-Gain reduction, line above dew point | | VJ | Vacuum Jacketed Insulation |
| 7 | Dual-Temperature Cyclic Condition | | CV | Vacuum Insulation in Cold Box |
| 8A–8D | Heat-Gain reduction + sound control A–D | | FP | Fireproofing |
 
The **Insulation Purpose** and **Thickness** parts of the tag (§3.3) are separate numeric fields; only the type code is enumerated here.
 
---
 
## 4. Metadata
 
The product distinguishes two kinds, and keeps them separate:
 
### 4.1 Business Metadata
 
Meaning of the data to the business — definitions, ownership, and how to read a Tag.
 
| Item | Content |
|------|---------|
| Drawing number | `362-92-02231` |
| Product | P&ID asset connectivity & isolation (steam systems) |
| Owner / domain | *(to assign)* — plant piping engineering |
| Tag grammar | Project-specific tagging conventions — see §3.3 |
| Process fluids | Four-column catalogue (Category → Subcategory → Code → Description), e.g. `AG` = Process/Process General/Acid Gas — see §3.4 |
 
### 4.2 Technical Metadata
 
Data-management metadata — source mapping, schema, lineage, and quality. Not visible to business users.
 
**Source → master-data mapping.** How each business attribute in §2 is obtained from the source input. The table below gives the **DEXPI** mapping; the **PostProc/ISO-15926** adapter targets the same master-data attributes from the equivalent PostProc elements (e.g. `DrawingNumber`, segment `TagName`/`ItemTag`, `GenericAttribute` sets), producing the identical objects.
 
| Master-data attribute | DEXPI source |
|-----------------------|--------------|
| Document | the DEXPI file itself (one file per Document) |
| Document Number / Client Document Number / Description / Revision | drawing title-block / drawing header in the file |
| Document → contained items | all component and network elements in the file belong to that Document |
| Pipeline System | `PipingNetworkSystem` element (`ID` prefixed `SY…`) |
| Sub Piping System (Subline) Tag *(project B)* | the `PipingNetworkSegment` `ItemTag` (e.g. `PG-1415109`) — the segments of one line sharing a subline tag form that Subline |
| Subline Sequence *(project B)* | `TagSuffix` (e.g. `09`); the Pipeline System Tag is the subline tag minus this suffix |
| Piping Segment | `PipingNetworkSegment` element (`ID` prefixed `SG…`) |
| Piping Component | `PipingComponent` (and related component elements) placed within a segment |
| Segment → Components | component elements nested under the `PipingNetworkSegment`, excluding its two boundary terminals |
| Segment Centerline | `CenterLine` element on the `PipingNetworkSegment`: ordered `Coordinate` (x, y) points, `NumPoints` |
| System → Segments | `PipingNetworkSegment` elements nested under `PipingNetworkSystem` |
| Segment ↔ Segment connection | derived: a `Connection` element links a component in one segment to a component in another via their nodes (`FromID`/`FromNode` → `ToID`/`ToNode`); the two owning segments are thereby connected |
| Connection *(entity §2.12)* | one record per link: From/To Object = `FromID`/`ToID`, From/To Node = `FromNode`/`ToNode`, Type = Process (pipe `Connection`) / Nozzle attachment / Signal (`SignalLine` `Connection`) / Off-Page continuation (OPC pairing); Derived Flag set where the link is reconstructed rather than stated |
| Component Class | `ComponentClass` attribute |
| Connection Nodes | `ConnectionPoints` → numbered `Node` elements on the component |
| Node Nominal Diameter | per-`Node` `NominalDiameter` |
| Component connection (node-to-node) | `Connection` element endpoints: `FromID`/`FromNode` → `ToID`/`ToNode` |
| Piping Component Tag | `ItemTag` (taken directly where present; whether bulk fittings carry one is project-defined) |
| Component Nominal Diameter(s) | per connection-point `NominalDiameter` (may differ per node, e.g. a reducer) |
| Inline position | derived: project each component's `ConnectionPoints` location onto the segment `CenterLine` by arc distance to order components end-to-end (§2.3.1); reconstructed — see quality flags |
| Instrument (physical) | `InstrumentComponent` / `ProcessInstrument` element |
| Instrument Kind | `IsInline` / `Location` (In-line vs Off-line, e.g. `Field`) |
| Actuator | `ActuatingSystem` element |
| Actuator → operated valve | the valve (`PipingComponent`) the actuating system is associated with |
| Process Equipment | `Equipment` element |
| Equipment Tag / Type | `ItemTag`; equipment-type code per §3.7 |
| Nozzle | `Nozzle` element (sub-part of the `Equipment`) |
| Nozzle → connected segment | `Connection` linking the nozzle's node to a piping component (flange) on the attaching segment |
| Signal | `SignalLine` element (`ComponentClass="SignalRun"`, dashed presentation) |
| Signal Type | `ComponentName` / signal-run class (e.g. Electric) |
| Signal From / To | `Connection` `FromID` / `ToID` on the `SignalLine` (the linked instrument functions); instruments also back-reference via `SP_SignalRunID` |
| Off-Page Connector | `PipeConnectorSymbol` element with `ComponentClass="OPC"` |
| OPC Tag | `OPCTag` |
| OPC mating connector / continues-on Document | `MatingOPCPath` / `SP_pairedWithID`; the continuing Document number appears in the connector's label (e.g. `362-92-02230`) |
| OPC unmatched *(quality flag)* | no mating connector resolves for the OPC — flagged as a connectivity data-quality issue |
| Instrument Tag | `ItemTag` (taken directly; `TagReqdFlag=True`, `IsBulkItem=False`) |
| Instrument Function | the function aspect linked via `InstrumentInstrFunction.SP_ID` (DEXPI `ProcessInstrumentationFunction` / `Systemfunctions`) |
| Function Measured Variable / Modifier / Sequence | `Set="Instrument"` attribute group: `MeasuredVariableCode`, `InstrumentTypeModifier`, `TagSequenceNo` |
| Function Control Case | `ControlCase.*` attributes |
| Instrumentation Loop | derived: group instrument functions by their shared sequence (`TagSequenceNo`) within a system/measured-variable; the loop is the grouping key of the instrument tags |
| Instrument turnover system/subsystem *(cross-check)* | `Z_TurnOverSystemNumber` / `SubsystemNo` (source-supplied commissioning assignment) |
| Pipeline System Tag | `ItemTag` (taken directly) |
| Piping Segment Tag | composed (see mapping rows below) |
| Fluid / Service | `OperFluidCode` |
| Unit | `UnitCode` |
| Sequence Number | `TagSequenceNo` |
| System code | `SP_PartNo` (e.g. `362`); also embedded in `ItemTag` (`LS`+`362`+`92`+`0131`) as a cross-check |
| Nominal Diameter | `NominalDiameter` |
| Piping Materials Class | `PipingMaterialsClass` |
| Insulation Type / Purpose / Thickness | `InsulType` / `InsulPurpose` / `InsulThick` |
| Flow Direction | `FlowDirection` on the segment (e.g. `End 1 is upstream (Inlet)`) |
 
**Internal keys.** Each object carries an opaque internal key from the source element `ID` (`SY…`, `SG…`), guaranteeing uniqueness even when business Tags repeat. Business users never see it; identity is keyed on it, not on the Tag.
 
**Schema & ingestion**
- Input: P&ID interoperability XML — DEXPI/Proteus (project A) or INGR ISO-15926 PostProc/SPPID (project B); each mapped by a format adapter to the master data herein (§1).
- Attribute dictionary: source attribute name→meaning; vendor-internal prefixes (`SP_`, `Z_`, `Color_`, `Update…`) flagged non-business and dropped from the product surface.
**Lineage & operations**
- Ingestion timestamp, content hash, file size, source last-modified time.
- Per-object counts and the completeness/validation report produced when the model is published.
> Formal identifiers, namespaces, and semantic validation are Gold-layer concerns handled separately (§1 scope note).
 
**Quality flags** (must travel with the master data, never silently dropped):
 
| Flag | Meaning |
|------|---------|
| Tag-input completeness | a null in any Tag-composition source (e.g. missing insulation thickness) is flagged, not blanked |
| Prefix integrity | segment Tag must begin with its subline Tag, and a subline Tag must begin with its Pipeline System Tag (§2.2a, §2.3); the round-trip check `compose(decode(tag)) == tag` validates the convention against the source tag |
| Tag-vs-drawing unit | a line's **tag unit** (decoded from its Tag) and its **drawing unit** (parsed from the P&ID Document Number against `UnitSUP`) disagree (§2.1a) — the component's Process Unit is ambiguous; carried for review, not reconciled |
| Multi-unit line | a single Pipeline System whose components decode to more than one unit code (§2.1a) — attributed to the dominant unit and flagged |
| Unknown / SUP-less unit | a unit code absent from the Plant Unit catalogue (`Unit` sheet), or present but with no Start-Up Package in `UnitSUP` (§2.1a, §3.6) — its SUP resolves to a placeholder |
| Flow direction | taken from the segment `FlowDirection` attribute (§2.3.2). Connectivity and isolation are reliable regardless of direction; only upstream/downstream labelling depends on it |
| Class standardisation | whether a component's class is recognised or on the backlog (§3.1) |
| Inline position = DERIVED | a component's order along its segment is reconstructed by projecting onto the segment centerline (§2.3.1), not stated in the source; reliable for ordering but flagged as derived |
| Segment connectivity = DERIVED | Process connections reconstructed from node-to-node component links carry a Derived flag on the Connection (§2.12); undirected connectivity is reliable |
| OPC unmatched | an Off-Page Connector with no resolvable mating connector on another Document — breaks cross-drawing continuity (§2.11) |
| Ghost equipment | an `Equipment` element with no Tag and no nozzles — an export placeholder/duplicate artefact, not a real process item; excluded from extraction. A tagged item with no nozzles is retained but flagged for review (§2.9) |
 
---
 
## 5. Ownership & governance summary
 
| Category | Entities | Key strategy | Governance owner |
|----------|----------|--------------|------------------|
| Master data | Document, Process Unit, Pipeline System, Sub Piping System (Subline, project B), Piping Segment, Piping Component, Instrument, Actuator, Instrument Function, Instrumentation Loop, Process Equipment (+ Nozzle), Signal, Off-Page Connector, Connection *(more added incrementally)* | Key on `ID`; `Tag` as business key (where present) | Data steward |
| Master data — grouping roots | Process Unit (§2.1a, tag-decoded), Start-Up Package (§2.1b, reference-data-provided) | Key on unit / SUP code | Data steward / project data steward |
| Reference data (shared) | Class catalogue, units | Governed list | Reference-data steward |
| Reference data (project-scoped) | Tagging conventions, process fluids, systems, units, equipment types, materials classes | Captured per project at onboarding | Project data steward |
| Business metadata | Drawing, Tag grammar, fluid codes, ownership | — | Product owner |
| Technical metadata | Schema, attribute dictionary, lineage, quality flags | — | Data engineer / quality steward |
 
---
 
## 6. Open items for the next revision
 
1. **Define the next master-data object** to increment (candidate: process-condition attributes on the Piping Segment — operating/design conditions and phase — then the systemization output object). Thirteen cross-project objects (including the Process Unit, §2.1a) plus the Connection edge and the project-scoped Sub Piping System (Subline, §2.2a) are defined in this revision.
2. **Complete the remaining project-scoped reference lists.** Delivered in `Reference_Data.xlsx`: process fluids (§3.4), equipment types (§3.7), insulation types (§3.11), the System catalogue / `SystemSeq` (§3.5), the Plant Unit catalogue / `UnitSUP` (§3.6), the `Boundary` sheet (§3.2), and the `TaggingConvention` sheet (§3.3). Still to enumerate: the Piping Materials Class list (§3.9) and instrument function codes (§3.10).
3. **Specify Tag null-handling** — how a missing composition input (e.g. absent insulation thickness) is represented in the composed Segment Tag and flagged for data quality.
4. **Standardise the backlog classes** (§3.1) — Hose Rack Station, Steam Trace Header, Condensate Recovery Header. Their formal reference-data mapping is a Gold-layer activity, tracked separately.