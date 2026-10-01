# Workstream 2 verification — SPARQL over the Gold graph

Queries that confirm the off-page-connector work is present and correct in the
Gold RDF projection. Every prefix and predicate below matches what
`gold/rdf_mapper.py::map_off_page_connector` / `map_connection` actually emit
(verified against real triples 2026-09-23); the OPC master-data lives in the
named graph `pidsys:graph/masterdata`, so each query scopes to it with `GRAPH`.

Run them with the package's own engine — `gold.sparql_queries.run_sparql(ds, Q)`
(rdflib under the hood) — after building `ds` from either projection path
(§6 `build_rdf_dataset` or the Gold-objects path). Expected results assume this
run's data; the SELECTs are the durable checks, the counts are illustrative.

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
PREFIX ido:    <https://www.omg.org/spec/Commons/IndustrialData/>
PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>
```

---

## Q1 — OPCs exist as first-class nodes, and are InformationObjects (not physical)

The core Workstream-2 assertion: an OPC is its own node, typed
`pidsys:OffPageConnector`, and that class is a sub-class of `ido:InformationObject`
(a sibling of `pidsys:Connection`, never physical). If Q1 returns 0 nodes, the
OPC feed didn't reach Gold. The `ASK` confirms the class placement in the
skeleton.

```sparql
SELECT (COUNT(DISTINCT ?opc) AS ?opc_nodes)
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?opc a pidsys:OffPageConnector .
  }
}
```

```sparql
ASK {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    pidsys:OffPageConnector rdfs:subClassOf ido:InformationObject .
  }
}
```

---

## Q2 — the OPC descriptor set is projected (type, flow role, mate label, URI)

One row per OPC with its Workstream-2 attributes. Confirms the harvest→entity→
Gold chain carried the descriptive fields, not just the node. `OPTIONAL` because
`flow_direction`/`component_class_uri` are DEXPI-only (null for PostProc) and
`terminates` is piping-only (Q3).

```sparql
SELECT ?opc ?opc_type ?flow ?mate ?uri ?segment
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?opc a pidsys:OffPageConnector .
    OPTIONAL { ?opc pidsys:componentClass    ?opc_type }
    OPTIONAL { ?opc pidsys:flowSense          ?flow }
    OPTIONAL { ?opc pidsys:category           ?mate }
    OPTIONAL { ?opc pidsys:componentClassUri  ?uri }
    OPTIONAL { ?opc pidsys:terminates         ?segment }
  }
}
ORDER BY ?opc
```

---

## Q3 — the terminates guard holds (keyed on the FLOW ROLE, not the opc_type label)

The load-bearing correctness check. `terminates`' model range is `PipingSegment`,
so it is valid exactly when the OPC sits on a pipe — and that is decided by the
**flow role** (`flowSense` = `FlowIn`/`FlowOutPipeOffPageConnector`), NOT by the
`opc_type` business label.

**DESIGN CORRECTION (2026-09-23, from real Project-A data).** An earlier version
of Q3 keyed on `opc_type` and expected `"Utility Connector"` to have no
`terminates`. Real data disproved that: many OPCs are `opc_type="Utility Connector"`
with `flowSense="FlowOutPipeOffPageConnector"` and a symbol file
`Utility-Off Drawing Piping Connector` — utility *lines* (steam, flare, drain)
that sit on pipes and correctly terminate. `_is_piping_opc` was fixed to key on
the flow role; these queries now assert that fixed rule. A utility-on-a-pipe
*should* terminate; only a **Signal** flow role or an explicit **Instrument**
type must not.

**Q3a — everything with a `terminates` sits on a pipe (expect: all terminating OPCs; every ?flow is a `...PipeOffPageConnector`):**
```sparql
SELECT ?opc_type ?flow (COUNT(*) AS ?n)
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?opc a pidsys:OffPageConnector ; pidsys:terminates ?seg .
    OPTIONAL { ?opc pidsys:componentClass ?opc_type }
    OPTIONAL { ?opc pidsys:flowSense       ?flow }
  }
}
GROUP BY ?opc_type ?flow
ORDER BY ?opc_type
```
Every `?flow` should read `FlowIn`/`FlowOutPipeOffPageConnector` — i.e. all
terminating OPCs genuinely sit on a pipe. (Utility and Piping opc_types both
appear here; that is correct.)

**Q3b — the guard (expect: 0 rows). A `terminates` is invalid ONLY for a signal flow role or an instrument type:**
```sparql
SELECT ?opc ?opc_type ?flow ?segment
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?opc a pidsys:OffPageConnector ; pidsys:terminates ?segment .
    OPTIONAL { ?opc pidsys:componentClass ?opc_type }
    OPTIONAL { ?opc pidsys:flowSense       ?flow }
    FILTER( CONTAINS(LCASE(COALESCE(?flow, "")),     "signal")
         || CONTAINS(LCASE(COALESCE(?opc_type, "")), "instrument") )
  }
}
```
Empty is the pass — no signal/instrument OPC wrongly asserts piping topology.
(Note: this must NOT filter on `"utility"` — utility-on-a-pipe legitimately
terminates. Filtering on utility is the stale rule that made Q3b return 37 false
failures.)

**Q3c — genuinely non-piping OPCs are node-only (signal/instrument, present but no terminates):**
```sparql
SELECT ?opc ?opc_type ?flow
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?opc a pidsys:OffPageConnector .
    OPTIONAL { ?opc pidsys:componentClass ?opc_type }
    OPTIONAL { ?opc pidsys:flowSense       ?flow }
    FILTER( CONTAINS(LCASE(COALESCE(?flow, "")),     "signal")
         || CONTAINS(LCASE(COALESCE(?opc_type, "")), "instrument") )
    FILTER NOT EXISTS { ?opc pidsys:terminates ?any }
  }
}
```
On a DEXPI-only load this is typically empty too, because `SignalOffPageConnector`
elements are not harvested at all (they belong to the deferred instrument tier) —
so there is no signal/instrument OPC in the graph to be node-only. It becomes
non-empty only if a future harvest admits instrument OPCs.

---

## Q4 — the cross-document continuation edge joins two sheets via OPC nodes

The matched pair is a reified `pidsys:Connection` of `connType "OffPage"`, always
`derived`, whose `fromObject`/`toObject` BOTH resolve to `pidsys:OffPageConnector`
nodes. This is the edge that carries a system across sheets.

```sparql
SELECT ?conn ?from ?to ?derived
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?conn a pidsys:Connection ;
          pidsys:connType "OffPage" ;
          pidsys:derived  ?derived ;
          pidsys:fromObject ?from ;
          pidsys:toObject   ?to .
    ?from a pidsys:OffPageConnector .
    ?to   a pidsys:OffPageConnector .
  }
}
```

---

## Q5 — end-to-end walk: segment → OPC → (mate) OPC → segment (a system crossing a sheet)

The payoff query. Starting from a piping segment, hop out through its OPC, across
the `OffPage` continuation to the mate OPC, and onto the segment on the *other*
sheet. A non-empty result proves the graph can carry a commissioning system
across a drawing boundary — the whole reason OPCs are modelled.

```sparql
SELECT ?seg_a ?opc_a ?opc_b ?seg_b
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?opc_a a pidsys:OffPageConnector ; pidsys:terminates ?seg_a .
    ?opc_b a pidsys:OffPageConnector ; pidsys:terminates ?seg_b .
    ?conn  a pidsys:Connection ; pidsys:connType "OffPage" ;
           pidsys:fromObject ?opc_a ; pidsys:toObject ?opc_b .
    FILTER( ?seg_a != ?seg_b )
  }
}
```

---

## Q6 — coverage rollup: OPC nodes vs terminating edges, by type

A one-glance health check for a review: how many OPCs of each type, and how many
carry a `terminates`. Any OPC on a pipe should terminate — piping *and*
utility types alike (Q3's 2026-09-23 correction); only signal/instrument OPCs
should show `with_terminates = 0`.

```sparql
SELECT ?opc_type (COUNT(DISTINCT ?opc) AS ?nodes)
       (COUNT(DISTINCT ?seg) AS ?with_terminates)
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?opc a pidsys:OffPageConnector .
    OPTIONAL { ?opc pidsys:componentClass ?opc_type }
    OPTIONAL { ?opc pidsys:terminates ?seg }
  }
}
GROUP BY ?opc_type
ORDER BY ?opc_type
```

---

## Notebook cell (paste after §6d)

```python
from gold.sparql_queries import run_sparql

WS2 = {
"q1_opc_nodes": '''
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT (COUNT(DISTINCT ?opc) AS ?opc_nodes) WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> { ?opc a pidsys:OffPageConnector . } }''',

"q3b_guard_must_be_empty": '''
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT ?opc ?opc_type ?flow ?segment WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?opc a pidsys:OffPageConnector ; pidsys:terminates ?segment .
    OPTIONAL { ?opc pidsys:componentClass ?opc_type }
    OPTIONAL { ?opc pidsys:flowSense      ?flow }
    FILTER( CONTAINS(LCASE(COALESCE(?flow, "")),     "signal")
         || CONTAINS(LCASE(COALESCE(?opc_type, "")), "instrument") ) } }''',

"q4_offpage_edges": '''
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT ?conn ?from ?to ?derived WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?conn a pidsys:Connection ; pidsys:connType "OffPage" ; pidsys:derived ?derived ;
          pidsys:fromObject ?from ; pidsys:toObject ?to .
    ?from a pidsys:OffPageConnector . ?to a pidsys:OffPageConnector . } }''',

"q5_cross_sheet_walk": '''
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT ?seg_a ?opc_a ?opc_b ?seg_b WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?opc_a a pidsys:OffPageConnector ; pidsys:terminates ?seg_a .
    ?opc_b a pidsys:OffPageConnector ; pidsys:terminates ?seg_b .
    ?conn a pidsys:Connection ; pidsys:connType "OffPage" ;
          pidsys:fromObject ?opc_a ; pidsys:toObject ?opc_b .
    FILTER( ?seg_a != ?seg_b ) } }''',
}

for name, q in WS2.items():
    rows = list(run_sparql(ds, q))
    print(f"{name}: {len(rows)} row(s)")
    for r in rows[:8]:
        print("   ", tuple(str(x).split('/')[-1] for x in r))
    if name == "q3b_guard_must_be_empty":
        print("   -> PASS (guard holds)" if not rows else "   -> FAIL: signal/instrument OPC asserts terminates")
```
```
```

---

## Interpreting the results — expected counts and the conservation identity

Verified against the real Project-A DEXPI load (4 drawings) 2026-09-23:

| check | result | meaning |
|---|---|---|
| Q1 OPC nodes | 68 | all `PipeOffPageConnector`s; 10 `SignalOffPageConnector`s correctly not harvested |
| Q2 full descriptors | 68 | `opc_type` + `flowSense` + `componentClassUri` + `terminates` on every node |
| Q3a terminating OPCs | 68 | every one carries a `terminates`; every `?flow` is a `...PipeOffPageConnector` |
| Q3b guard | **0** | no signal/instrument OPC asserts a `terminates` (empty = pass) |
| Q4 matched pairs | 3 | cross-document `OffPage` edges; both endpoints resolve to OPC nodes |
| open boundaries | 62 | OPCs whose mate is on a sheet not in this load |
| Q5 cross-sheet walks | 3 | `segment → OPC → OPC → segment` chains crossing a drawing boundary |

**The conservation identity (the key sanity check):**

```
matched endpoints + open boundaries = total OPC nodes
      (3 pairs × 2) + 62            = 68            ✓
```

Every OPC is accounted for — matched to a mate on a loaded sheet, or flagged as
continuing off-set. None is dropped or double-counted. This identity should hold
for **any** load: `2 × Q4 + open_boundaries = Q1`.

**Why low pairing is expected, not a defect.** DEXPI pairs by `SP_pairedWithID`
GUID, which resolves only when BOTH mating sheets are loaded. On a 4-sheet slice
of a tens-of-sheets plant, most mates live on unloaded drawings, so most OPCs are
open boundaries. Pairing (Q4) and cross-sheet walks (Q5) climb as the loaded set
widens toward the whole plant; the `PairedDrawingNumber` on each open-boundary OPC
names exactly which drawing to add to resolve it. On a full-plant load, expect
`Q4 ≈ Q1 / 2` and `open_boundaries ≈ 0`.

**Silver cross-check (same numbers, one layer down):**
```python
opc = spark.table("silver.silver_off_page_connectors")
opc.groupBy("matched").count().show()                 # expect ~6 matched, ~62 not
spark.table("silver.silver_quality").where("flag='opc_open_boundary'").count()  # ~62
```

---

## Master-data verification — the other spec objects in the graph

The OPC queries above verify Workstream 2. These verify the rest of the
master-data model is projected as the spec/ontology require. **Graph scoping is
load-bearing:** master data is in `graph/masterdata`, reference data (fluids) in
`graph/refdata`, computed systems + functions in `graph/results`, and the oracle
(source turnover/subsystem) is quarantined in `graph/oracle`. Querying the wrong
graph returns 0 and looks like a failure — each query below scopes explicitly.

### M1 — every domain object is anchored to the correct IDO foundational class

The reasoner-verified anchoring: physical artefacts, functional systems, features,
information objects. This is the heart of the model reconciliation.

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
PREFIX ido:    <https://www.omg.org/spec/Commons/IndustrialData/>
PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>
ASK {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    pidsys:PipingComponent rdfs:subClassOf ido:PhysicalArtefact .
    pidsys:Equipment       rdfs:subClassOf ido:PhysicalArtefact .
    pidsys:Nozzle          rdfs:subClassOf ido:Feature .
    pidsys:PipingSegment   rdfs:subClassOf ido:System .
    pidsys:Line            rdfs:subClassOf ido:System .
    pidsys:Connection      rdfs:subClassOf ido:InformationObject .
    pidsys:Document        rdfs:subClassOf ido:InformationObject .
  }
}
```
`true` = the anchoring is intact. This is the single most important non-OPC check:
segments/lines are functional `System`s (not physical), components/equipment are
`PhysicalArtefact`s, nozzles are `Feature`s.

### M1b — the functional-only-segment discipline: no segment/line is dual-typed as physical

The reasoner allows a dual-typed segment (no disjointness axiom); the projection
must not create one. Expect **0 rows**.

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
PREFIX ido:    <https://www.omg.org/spec/Commons/IndustrialData/>
PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
SELECT ?s
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    { ?s rdf:type pidsys:PipingSegment } UNION { ?s rdf:type pidsys:Line }
    ?s rdf:type ?other .
    ?other rdfs:subClassOf* ido:PhysicalObject .
  }
}
```

### M2 — components: count, class, and RDL-pending marker

Every component carries a `componentClass`; unresolved ones carry
`rdlUriPending true` (the §4.3.1 review gate). This shows the split.

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT
  (COUNT(DISTINCT ?c) AS ?components)
  (COUNT(DISTINCT ?p) AS ?rdl_pending)
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?c pidsys:componentClass ?cls .
    OPTIONAL { ?c pidsys:rdlUriPending ?p }
  }
}
```

### M2b — unclassified components fall back honestly (no invented domain class)

Components with no source class must be `pidsys:UnclassifiedComponent`, never a
fabricated domain class. Lists them (expect the ~1.9% null-class rows seen in the
diagnostic).

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT (COUNT(DISTINCT ?c) AS ?unclassified)
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?c a pidsys:UnclassifiedComponent .
  }
}
```

### M3 — segments carry their engineering attributes

A segment's own master data: fluid code, diameter, piping-materials class. Shows
how many segments carry each (completeness signal, per Stage D).

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT
  (COUNT(DISTINCT ?s)  AS ?segments)
  (COUNT(DISTINCT ?sf) AS ?with_fluid)
  (COUNT(DISTINCT ?sd) AS ?with_diameter)
  (COUNT(DISTINCT ?sm) AS ?with_pmc)
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?s a pidsys:PipingSegment .
    OPTIONAL { ?s pidsys:fluidCode           ?f . BIND(?s AS ?sf) }
    OPTIONAL { ?s pidsys:diameter            ?d . BIND(?s AS ?sd) }
    OPTIONAL { ?s pidsys:pipingMaterialsClass ?m . BIND(?s AS ?sm) }
  }
}
```

### M4 — equipment and its nozzles (the Feature part-of relation)

Equipment count with class, plus the nozzle→equipment `partOf` wiring (nozzles
are `ido:Feature`, dependent parts of equipment).

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT
  (COUNT(DISTINCT ?e) AS ?equipment)
  (COUNT(DISTINCT ?n) AS ?nozzles)
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?e a pidsys:Equipment .
    OPTIONAL { ?n a pidsys:Nozzle ; pidsys:partOf ?e }
  }
}
```

### M5 — connections are reified InformationObjects, and every one carries `derived`

Per the model, connectivity is never a bare triple: each is a `pidsys:Connection`
(an `InformationObject`) with a mandatory `derived` flag. Expect
`connections_without_derived = 0`.

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT
  (COUNT(DISTINCT ?c) AS ?connections)
  (COUNT(DISTINCT ?cd) AS ?with_derived)
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?c a pidsys:Connection .
    OPTIONAL { ?c pidsys:derived ?d . BIND(?c AS ?cd) }
  }
}
```
`connections = with_derived` is the pass (derived is mandatory on every edge).

### M6 — fluids are REFERENCE data (graph/refdata, not masterdata)

A scoping check that doubles as a guard: fluids must be in `graph/refdata`.
Querying masterdata for them returns 0 — that is correct, not a miss.

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT (COUNT(DISTINCT ?f) AS ?fluids)
FROM NAMED <https://pidsys.example/ns#graph/refdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/refdata> {
    ?f a pidsys:Fluid .
  }
}
```

### M7 — computed systems and functional membership (graph/results)

Commissioning systems and the function-realization membership pattern live in
`graph/results`. A system's members are FUNCTIONAL individuals (never physical
components directly): `system pidsys:member ?fn`, `?fn a pidsys:Function`,
`?component ido:hasFunction ?fn`, `?fn ido:realizedIn ?activity`.

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
PREFIX ido:    <https://www.omg.org/spec/Commons/IndustrialData/>
SELECT ?system ?function ?component
FROM NAMED <https://pidsys.example/ns#graph/results>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/results> {
    ?system  a pidsys:CommissioningSystem ;
             pidsys:member ?function .
    ?function a pidsys:Function .
    ?component ido:hasFunction ?function .
  }
}
LIMIT 25
```

### M7b — membership never points straight at a physical component (expect 0 rows)

> **Vacuous until `graph/results` is populated.** Nothing in the notebook
> writes computed systems yet (§9 recap: the walk.py partition step is not
> run there), so today M7 returns 0 rows and M7b passes trivially. Read
> M7b as a real guard only once M7 returns rows.

The guard for the function-realization decision: `pidsys:member` must resolve to
a `Function`, never a `PipingComponent`/`Equipment`.

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT ?system ?member
FROM NAMED <https://pidsys.example/ns#graph/results>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/results> {
    ?system pidsys:member ?member .
    GRAPH <https://pidsys.example/ns#graph/masterdata> {
      { ?member a pidsys:PipingComponent } UNION { ?member a pidsys:Equipment }
    }
  }
}
```

### M8 — the oracle firewall: source turnover/subsystem stays quarantined (expect 0 in every non-oracle graph)

The compute-only firewall (silver_spec §5): `srcTurnoverSystem`/`srcSubsystem`
must appear ONLY in `graph/oracle`, never where a rule could read them. This
query looks for a leak into masterdata; expect **0 rows**.

```sparql
PREFIX pidsys: <https://pidsys.example/ns#>
SELECT ?s ?p ?o
FROM NAMED <https://pidsys.example/ns#graph/masterdata>
WHERE {
  GRAPH <https://pidsys.example/ns#graph/masterdata> {
    ?s ?p ?o .
    FILTER( ?p = pidsys:srcTurnoverSystem || ?p = pidsys:srcSubsystem )
  }
}
```
Any row is a firewall breach — the oracle leaked into a compute graph. Empty is
the pass. (The oracle values live in `graph/oracle`; swap the graph URI to see
them there.)

### Master-data expected-counts (fill in on first run, then pin as the acceptance baseline)

| query | this load | meaning |
|---|---|---|
| M1 anchoring ASK | `true` | IDO foundational alignment intact |
| M1b dual-typed segments | 0 | functional-only-segment discipline holds |
| M2 components / rdl_pending | ? / ? | every component classed; unresolved marked |
| M2b unclassified | ? | honest fallback (≈1.9% on Project A) |
| M5 connections = with_derived | ? = ? | derived mandatory on every edge |
| M6 fluids (refdata) | ? | reference data present in the right graph |
| M7b member→physical | 0 | membership is functional, never physical |
| M8 oracle leak into masterdata | 0 | compute firewall intact |

The four **expect-0** guards (M1b, M7b, M8, and OPC Q3b) are the strongest
acceptance checks — each asserts a model invariant that a regression would break.
