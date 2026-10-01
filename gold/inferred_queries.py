"""SPARQL that illustrates graph:inferred — the Jena rule output (gold_layer_spec §5.7).

Every query joins graph:inferred back to the plant (graph:masterdata) and the
reference data (graph:refdata), so a result reads in engineering terms — tags,
component classes, fluid codes — not bare URIs.

Five groups:
  overview    what is in the graph, which run produced it, which rule made what
  fluids      fluid classification against the catalogue, and which fluids form their own systems
  guards      flare sinks and supply tie-ins, by fluid pair and in detail
  relief      relief valves, the side each protects, and where each discharges
  checks      invariants that must hold (expected result stated per query)
plus `rule_card(cfg, tag)` — everything the rules concluded about one component.

graph:inferred lives in Fuseki, not in the notebook's `ds`, so these run
against the live `gold` dataset (`run(cfg, name)`, notebook §8d). `run_local(ds,
name)` runs the same text through rdflib for tests. The reference document
`inferred_graph_sparql.md` is generated from QUERIES (`write_reference_doc`).
"""
from __future__ import annotations

from typing import Dict, List, Optional

PIDSYS = "https://pidsys.example/ns#"
G_INF = f"<{PIDSYS}graph/inferred>"
G_MD = f"<{PIDSYS}graph/masterdata>"
G_RD = f"<{PIDSYS}graph/refdata>"

PREFIXES = f"""PREFIX pidsys: <{PIDSYS}>
PREFIX prov:   <http://www.w3.org/ns/prov#>
PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>
"""

DERIVED = ("pidsys:selfOwningClass pidsys:skipAsConsumerSignal pidsys:skipFlareSink "
           "pidsys:skipSupplyTieIn pidsys:protectedBy")

# A node's short name: 'component/SP0A…', 'fluid/SF', 'offpageconnector/…'
SHORT = 'REPLACE(STR({v}), "^.*#", "")'

# fluid code of a component, via its segment (the fluid is the segment's, MD §2.4)
def _fluid_of(node: str, var: str) -> str:
    return (f"OPTIONAL {{ GRAPH {G_MD} {{ {node} pidsys:partOf ?_s_{var} . "
            f"?_s_{var} pidsys:fluidCode ?{var} }} }}")


QUERIES: Dict[str, dict] = {}


def _q(name, group, title, body, expect=None, explain=""):
    QUERIES[name] = {"group": group, "title": title, "query": PREFIXES + body.strip() + "\n",
                     "expect": expect, "explain": explain.strip()}


# ---------------------------------------------------------------- overview
_q("I1_overview", "overview", "What graph:inferred holds — facts per derived predicate", f"""
SELECT ?predicate (COUNT(*) AS ?facts)
WHERE {{
  GRAPH {G_INF} {{ VALUES ?p {{ {DERIVED} }} ?s ?p ?o }}
  BIND(STRAFTER(STR(?p), "#") AS ?predicate)
}}
GROUP BY ?predicate
ORDER BY DESC(?facts)
""", explain="""
`skipAsConsumerSignal` is the union of the two guard reasons, so it equals
`skipFlareSink + skipSupplyTieIn` (the guards fire on opposite flow directions and
never overlap — check C2). Run 5 (Project A): 1,310 = 47 + 1,263; 39 `protectedBy`;
68 `selfOwningClass`.""")

_q("I2_run", "overview", "Which run produced it — times, parity verdict, size", f"""
SELECT ?run ?started ?ended ?parity ?derivedTriples
WHERE {{
  GRAPH {G_INF} {{
    {G_INF} prov:wasGeneratedBy ?r ; pidsys:tripleCount ?derivedTriples .
    ?r prov:startedAtTime ?started ; prov:endedAtTime ?ended ; pidsys:parityStatus ?parity .
  }}
  BIND({SHORT.format(v="?r")} AS ?run)
}}
""", expect="one row", explain="""
Exactly one run: the graph is replaced wholesale on each materialisation.""")

_q("I3_inputs", "overview", "What that run read — inputs and rules, identified by content hash", f"""
SELECT ?input ?about ?sha256
WHERE {{
  GRAPH {G_INF} {{
    {G_INF} prov:wasGeneratedBy ?r .
    ?r prov:used ?e .
    ?e pidsys:sha256 ?sha256 .
    OPTIONAL {{ ?e prov:specializationOf ?g }}
    OPTIONAL {{ ?e pidsys:sourceFile ?f }}
  }}
  BIND({SHORT.format(v="?e")} AS ?input)
  BIND(COALESCE({SHORT.format(v="?g")}, ?f) AS ?about)
}}
ORDER BY ?input
""", expect="three rows", explain="""
A masterdata snapshot, a refdata snapshot and the rules file. `inferred_is_current`
compares these hashes with the data a systemization run is about to walk.""")

_q("I4_parity", "overview", "The parity verdict it was published under — agreement per section", f"""
SELECT ?section ?agree
WHERE {{
  GRAPH {G_INF} {{
    {G_INF} prov:wasGeneratedBy ?r .
    ?r ?p ?agree .
    FILTER(STRSTARTS(STR(?p), "{PIDSYS}parityAgree_"))
  }}
  BIND(STRAFTER(STR(?p), "parityAgree_") AS ?section)
}}
ORDER BY ?section
""")

_q("I5_rules", "overview", "Which rule produced which facts", f"""
SELECT ?rule ?predicate (COUNT(*) AS ?facts)
WHERE {{
  GRAPH {G_INF} {{
    ?p pidsys:derivedByRule ?r .
    ?r rdfs:label ?rule .
    ?s ?p ?o .
    FILTER(?p != pidsys:skipAsConsumerSignal)
    # selfOwningClass has three rules; the value says which one fired
    FILTER(?p != pidsys:selfOwningClass
           || (?rule = "flareCategory" && STR(?o) = "flare")
           || (?rule = "steamCondensateCategory" && STR(?o) = "steam_condensate")
           || (?rule = "processCategory" && STR(?o) = "process"))
  }}
  BIND(STRAFTER(STR(?p), "#") AS ?predicate)
}}
GROUP BY ?rule ?predicate
ORDER BY ?predicate ?rule
""", explain="""
`skipAsConsumerSignal` is left out: it is asserted by both guard rules, and its
per-rule split is exactly the `skipFlareSink` / `skipSupplyTieIn` rows.""")

# ---------------------------------------------------------------- fluids
_q("F1_fluid_classes", "fluids", "Every catalogue fluid, its class, and how many segments carry it", f"""
SELECT ?fluid ?category ?subcategory ?class (COUNT(DISTINCT ?seg) AS ?segments)
WHERE {{
  GRAPH {G_RD} {{
    ?f a pidsys:Fluid ; pidsys:fluidCode ?fluid ; pidsys:category ?category .
    OPTIONAL {{ ?f pidsys:subcategory ?subcategory }}
  }}
  OPTIONAL {{ GRAPH {G_INF} {{ ?f pidsys:selfOwningClass ?c }} }}
  BIND(COALESCE(STR(?c), "utility") AS ?class)
  OPTIONAL {{ GRAPH {G_MD} {{ ?seg a pidsys:PipingSegment ; pidsys:fluidCode ?fluid }} }}
}}
GROUP BY ?fluid ?category ?subcategory ?class
ORDER BY ?class ?fluid
""", explain="""
`utility` is the absence of a `selfOwningClass` fact (Python's default class). A fluid
with 0 segments is in the catalogue but not on these drawings.""")

_q("F2_class_summary", "fluids", "Fluids and segments per class — who forms their own systems", f"""
SELECT ?class ?ownSystems (COUNT(DISTINCT ?f) AS ?fluids) (COUNT(DISTINCT ?seg) AS ?segments)
WHERE {{
  GRAPH {G_RD} {{ ?f a pidsys:Fluid ; pidsys:fluidCode ?fluid }}
  OPTIONAL {{ GRAPH {G_INF} {{ ?f pidsys:selfOwningClass ?c }} }}
  BIND(COALESCE(STR(?c), "utility") AS ?class)
  BIND(IF(?class IN ("flare", "steam_condensate"), "yes — never traced to a consumer",
                                                   "no") AS ?ownSystems)
  OPTIONAL {{ GRAPH {G_MD} {{ ?seg a pidsys:PipingSegment ; pidsys:fluidCode ?fluid }} }}
}}
GROUP BY ?class ?ownSystems
ORDER BY ?class
""", explain="""
Flare and steam/condensate are *self-owning*: they form their own commissioning
systems before any consumer trace runs (algorithm_spec §7.1a). Process fluids are
named as process systems; ordinary utilities are traced to the consumer they feed.""")

# ---------------------------------------------------------------- guards
_q("G1_flare_by_fluid", "guards", "Flare sinks by fluid pair — what discharges into flare", f"""
SELECT ?fragmentFluid ?flareFluid (COUNT(*) AS ?skips)
WHERE {{
  GRAPH {G_INF} {{ ?a pidsys:skipFlareSink ?b }}
  {_fluid_of("?a", "fa")}
  {_fluid_of("?b", "fb")}
  BIND(COALESCE(?fa, "(no segment)") AS ?fragmentFluid)
  BIND(COALESCE(?fb, "(no segment)") AS ?flareFluid)
}}
GROUP BY ?fragmentFluid ?flareFluid
ORDER BY DESC(?skips)
""", explain="""
Each row: a fragment of `fragmentFluid` flows one-way into a `flareFluid` neighbour, so
that neighbour is a discharge sink, not a consumer the fragment commissions with.
Same-fluid rows (flare → flare) are flow continuity along a flare header; the walk only
consults the guard for different-fluid neighbours, so they are harmless.""")

_q("G2_flare_detail", "guards", "Flare sinks in detail — tags and classes on both sides", f"""
SELECT ?fragment ?fragmentTag ?fragmentClass ?fragmentFluid ?sink ?sinkTag ?sinkClass ?flareFluid
WHERE {{
  GRAPH {G_INF} {{ ?a pidsys:skipFlareSink ?b }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?a pidsys:tag ?fragmentTag }} }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?a pidsys:componentClass ?fragmentClass }} }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?b pidsys:tag ?sinkTag }} }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?b pidsys:componentClass ?sinkClass }} }}
  {_fluid_of("?a", "fragmentFluid")}
  {_fluid_of("?b", "flareFluid")}
  FILTER(!BOUND(?fragmentFluid) || !BOUND(?flareFluid) || ?fragmentFluid != ?flareFluid)
  BIND({SHORT.format(v="?a")} AS ?fragment)
  BIND({SHORT.format(v="?b")} AS ?sink)
}}
ORDER BY ?flareFluid ?fragmentFluid ?fragment
LIMIT 50
""", explain="""
Cross-fluid flare sinks only — the ones that change a systemization result. Untagged
components show only their id; the class tells you what they are.""")

_q("G3_supply_split", "guards", "Supply tie-ins — flow continuity vs real cross-fluid tie-ins", f"""
SELECT ?kind (COUNT(*) AS ?skips)
WHERE {{
  GRAPH {G_INF} {{ ?a pidsys:skipSupplyTieIn ?b }}
  {_fluid_of("?a", "fa")}
  {_fluid_of("?b", "fb")}
  BIND(IF(!BOUND(?fa) || !BOUND(?fb), "endpoint without a segment fluid",
       IF(?fa = ?fb, "same fluid (upstream along the line)", "cross-fluid tie-in")) AS ?kind)
}}
GROUP BY ?kind
ORDER BY DESC(?skips)
""", explain="""
The directional guard fires for every neighbour that feeds a component one-way, so most
of its facts are ordinary upstream pipe along the same line. Only cross-fluid rows matter
for consumer detection — see G4.""")

_q("G4_supply_by_fluid", "guards", "Cross-fluid supply tie-ins by fluid pair — who feeds whom", f"""
SELECT ?supplyFluid ?supplyCategory ?intoFluid ?intoCategory (COUNT(*) AS ?tieIns)
WHERE {{
  GRAPH {G_INF} {{ ?a pidsys:skipSupplyTieIn ?b }}
  {_fluid_of("?a", "intoFluid")}
  {_fluid_of("?b", "supplyFluid")}
  FILTER(BOUND(?intoFluid) && BOUND(?supplyFluid) && ?intoFluid != ?supplyFluid)
  OPTIONAL {{ GRAPH {G_RD} {{ ?fs pidsys:fluidCode ?supplyFluid ; pidsys:category ?supplyCategory }} }}
  OPTIONAL {{ GRAPH {G_RD} {{ ?fi pidsys:fluidCode ?intoFluid ; pidsys:category ?intoCategory }} }}
}}
GROUP BY ?supplyFluid ?supplyCategory ?intoFluid ?intoCategory
ORDER BY DESC(?tieIns)
""", explain="""
Read each row as "`supplyFluid` ties into `intoFluid`": the supply commissions with the
line it feeds, not the other way round (nitrogen teeing into a process header commissions
with the header). A `Process → Flare` row is the flare guard seen from the flare side.""")

# ---------------------------------------------------------------- relief
_q("R1_relief", "relief", "Relief valves — the side each protects, and where it discharges", f"""
SELECT ?valveTag ?valveClass ?protectedClass ?protectedLine ?protectedFluid
       ?dischargeClass ?dischargeLine ?dischargeFluid ?valve ?protected
WHERE {{
  GRAPH {G_INF} {{ ?v pidsys:protectedBy ?p }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?v pidsys:tag ?valveTag }} }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?v pidsys:componentClass ?valveClass }} }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?p pidsys:componentClass ?protectedClass }} }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?p pidsys:partOf ?ps . ?ps pidsys:fluidCode ?protectedFluid .
                             OPTIONAL {{ ?ps pidsys:tag ?protectedLine }} }} }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?v pidsys:flowsTo ?d .
                             OPTIONAL {{ ?d pidsys:componentClass ?dischargeClass }}
                             ?d pidsys:partOf ?ds . ?ds pidsys:fluidCode ?dischargeFluid .
                             OPTIONAL {{ ?ds pidsys:tag ?dischargeLine }} }} }}
  BIND({SHORT.format(v="?v")} AS ?valve)
  BIND({SHORT.format(v="?p")} AS ?protected)
}}
ORDER BY ?valveTag ?valve
""", explain="""
A relief valve commissions with the system it protects (flow *into* the valve), not the
header it vents to (algorithm_spec §7.4a). The neighbours are often spec-break symbols
(`PropertyBreak`) at the valve's inlet and outlet; the line and fluid come from the segment
each sits on. Discharge columns are blank when the outlet's direction or segment is not in
the data (e.g. venting to atmosphere).""")

_q("R2_relief_summary", "relief", "Relief attribution by fluid — protected side → discharge side", f"""
SELECT ?protectedFluid ?dischargeFluid (COUNT(DISTINCT ?v) AS ?valves)
WHERE {{
  GRAPH {G_INF} {{ ?v pidsys:protectedBy ?p }}
  {_fluid_of("?p", "pf")}
  OPTIONAL {{ GRAPH {G_MD} {{ ?v pidsys:flowsTo ?d . ?d pidsys:partOf ?ds .
                             ?ds pidsys:fluidCode ?df }} }}
  BIND(COALESCE(?pf, "(no segment)") AS ?protectedFluid)
  BIND(COALESCE(?df, "(unknown)") AS ?dischargeFluid)
}}
GROUP BY ?protectedFluid ?dischargeFluid
ORDER BY DESC(?valves)
""")

# ---------------------------------------------------------------- checks
_q("C1_union_explained", "checks", "Every consumer skip has a reason", f"""
SELECT ?a ?b
WHERE {{
  GRAPH {G_INF} {{
    ?a pidsys:skipAsConsumerSignal ?b .
    FILTER NOT EXISTS {{ ?a pidsys:skipFlareSink ?b }}
    FILTER NOT EXISTS {{ ?a pidsys:skipSupplyTieIn ?b }}
  }}
}}
""", expect="empty")

_q("C2_guards_disjoint", "checks", "No pair is both a flare sink and a supply tie-in", f"""
SELECT ?a ?b
WHERE {{ GRAPH {G_INF} {{ ?a pidsys:skipFlareSink ?b . ?a pidsys:skipSupplyTieIn ?b }} }}
""", expect="empty")

_q("C3_relief_class", "checks", "Only relief-role classes are attributed", f"""
SELECT ?v
WHERE {{
  GRAPH {G_INF} {{ ?v pidsys:protectedBy ?p }}
  FILTER NOT EXISTS {{
    GRAPH {G_MD} {{ ?v a ?cls }}
    GRAPH {G_RD} {{ <{PIDSYS}boundary_role/relief> pidsys:boundaryMember ?cls }}
  }}
}}
""", expect="empty")

_q("C4_flare_class", "checks", "Every fluid classed flare has Category = Flare in the catalogue", f"""
SELECT ?f
WHERE {{
  GRAPH {G_INF} {{ ?f pidsys:selfOwningClass "flare" }}
  FILTER NOT EXISTS {{ GRAPH {G_RD} {{ ?f pidsys:category "Flare" }} }}
}}
""", expect="empty")

_q("C5_flare_sink_fluid", "checks", "Every flare sink really sits on a flare-class fluid", f"""
SELECT ?b ?code
WHERE {{
  GRAPH {G_INF} {{ ?a pidsys:skipFlareSink ?b }}
  GRAPH {G_MD} {{ ?b pidsys:partOf ?s . ?s pidsys:fluidCode ?code }}
  FILTER NOT EXISTS {{
    GRAPH {G_RD} {{ ?f pidsys:fluidCode ?code }}
    GRAPH {G_INF} {{ ?f pidsys:selfOwningClass "flare" }}
  }}
}}
""", expect="empty")

_q("C6_no_dangling", "checks", "Every node the rules talk about exists in masterdata", f"""
SELECT DISTINCT ?n
WHERE {{
  GRAPH {G_INF} {{
    {{ ?n ?p ?o . FILTER(?p IN (pidsys:skipAsConsumerSignal, pidsys:protectedBy)) }}
    UNION
    {{ ?s ?p ?n . FILTER(?p IN (pidsys:skipAsConsumerSignal, pidsys:protectedBy)) }}
  }}
  FILTER NOT EXISTS {{ GRAPH {G_MD} {{ ?n ?x ?y }} }}
}}
""", expect="empty")

_q("C7_oracle_firewall", "checks", "No oracle predicate in graph:inferred", f"""
SELECT ?s ?p
WHERE {{
  GRAPH {G_INF} {{ ?s ?p ?o . FILTER(?p IN (pidsys:srcTurnoverSystem, pidsys:srcSubsystem)) }}
}}
""", expect="empty")

_q("C8_checkvalve_member", "checks", "CheckValve is in no boundary role", f"""
SELECT ?role
WHERE {{ GRAPH {G_RD} {{ ?role pidsys:boundaryMember pidsys:CheckValve }} }}
""", expect="empty")

_q("C9_checkvalve_excluded", "checks", "CheckValve's exclusion is recorded as data", f"""
SELECT ?flag
WHERE {{ GRAPH {G_RD} {{ pidsys:CheckValve pidsys:boundaryForming ?flag }} }}
""", expect="one row", explain="""
The project decision ([MD §3.2]) as a triple: `pidsys:CheckValve pidsys:boundaryForming
false` (gold_layer_spec §5.3).""")

RULE_CARD = PREFIXES + f"""
SELECT ?direction ?fact ?otherClass ?otherTag ?onLine ?fluid ?rule ?other
WHERE {{
  GRAPH {G_MD} {{ ?c pidsys:tag ?tag }}
  FILTER(STR(?tag) = "%TAG%")
  GRAPH {G_INF} {{
    {{ ?c ?p ?x . BIND("this →" AS ?direction) }}
    UNION
    {{ ?x ?p ?c . BIND("→ this" AS ?direction) }}
    FILTER(?p IN (pidsys:skipFlareSink, pidsys:skipSupplyTieIn, pidsys:protectedBy))
    ?p pidsys:derivedByRule ?r . ?r rdfs:label ?rule .
  }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?x pidsys:tag ?otherTag }} }}
  OPTIONAL {{ GRAPH {G_MD} {{ ?x pidsys:componentClass ?otherClass }} }}
  OPTIONAL {{ GRAPH {G_MD} {{
    ?x pidsys:partOf ?seg .
    OPTIONAL {{ ?seg pidsys:tag ?onLine }}
    OPTIONAL {{ ?seg pidsys:fluidCode ?fluid }} }} }}
  # "valve protectedBy X" reads backwards; from the valve's side it means "protects X"
  BIND(IF(?p = pidsys:protectedBy && ?direction = "this →", "protects",
       STRAFTER(STR(?p), "#")) AS ?fact)
  BIND({SHORT.format(v="?x")} AS ?other)
}}
ORDER BY ?fact ?direction ?other
"""


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def _json_rows(result: dict) -> List[dict]:
    rows = []
    for b in result.get("results", {}).get("bindings", []):
        row = {}
        for k, cell in b.items():
            val = cell["value"]
            if cell.get("datatype", "").endswith(("#integer", "#int", "#long", "#decimal")):
                try:
                    val = int(val)
                except ValueError:
                    pass
            row[k] = val
        rows.append(row)
    return rows


def run(cfg, name: str) -> List[dict]:
    """One query against the live `gold` dataset (cfg: fuseki_client.FusekiConfig)."""
    from .fuseki_client import sparql_query
    return _json_rows(sparql_query(cfg, QUERIES[name]["query"]))


def run_local(ds, name: str, query: Optional[str] = None) -> List[dict]:
    """The same query through rdflib over an in-memory Dataset (tests)."""
    from .sparql_queries import run_sparql
    out = []
    for r in run_sparql(ds, query or QUERIES[name]["query"]):
        d = r.asdict()
        out.append({k: (v.toPython() if hasattr(v, "toPython") else v) for k, v in d.items()})
    return out


def rule_card(cfg, tag: str) -> List[dict]:
    """Everything the rules concluded about the component tagged `tag`."""
    from .fuseki_client import sparql_query
    safe = tag.replace("\\", "\\\\").replace('"', '\\"')
    return _json_rows(sparql_query(cfg, RULE_CARD.replace("%TAG%", safe)))


def verdict(name: str, rows: List[dict]) -> Optional[str]:
    """'PASS'/'FAIL' for queries with an expected shape, else None."""
    expect = QUERIES[name]["expect"]
    if expect is None:
        return None
    ok = {"empty": len(rows) == 0, "one row": len(rows) == 1, "three rows": len(rows) == 3}[expect]
    return "PASS" if ok else "FAIL"


def run_all(cfg, groups=("overview", "fluids", "guards", "relief", "checks"),
            show: int = 12, as_frames: bool = False) -> Dict[str, object]:
    """Runs every query in `groups`, prints each as a small table, returns
    {name: rows} (or pandas DataFrames with as_frames=True)."""
    results: Dict[str, object] = {}
    for name, q in QUERIES.items():
        if q["group"] not in groups:
            continue
        rows = run(cfg, name)
        v = verdict(name, rows)
        head = f"── {name} · {q['title']}" + (f"   [{v}: expect {q['expect']}]" if v else "")
        print(head)
        if rows:
            cols = list(rows[0].keys())
            for r in rows[:show]:
                print("   " + " | ".join(str(r.get(c, "")) for c in cols))
            if len(rows) > show:
                print(f"   … {len(rows) - show} more rows")
        else:
            print("   (no rows)")
        if as_frames:
            import pandas as pd
            results[name] = pd.DataFrame(rows)
        else:
            results[name] = rows
    return results


def write_reference_doc(path: str = "inferred_graph_sparql.md", header: str = "") -> str:
    """Regenerates the markdown reference from QUERIES, so doc and code agree."""
    titles = {"overview": "Overview — contents, run, provenance",
              "fluids": "Fluids — classification and self-owning systems",
              "guards": "Directional guards — flare sinks and supply tie-ins",
              "relief": "Relief attribution",
              "checks": "Invariant checks"}
    out = [header.rstrip() + "\n"] if header else []
    for group, gtitle in titles.items():
        out.append(f"\n## {gtitle}\n")
        for name, q in QUERIES.items():
            if q["group"] != group:
                continue
            exp = f" — expect **{q['expect']}**" if q["expect"] else ""
            out.append(f"\n### {name} — {q['title']}{exp}\n")
            if q["explain"]:
                out.append("\n" + q["explain"] + "\n")
            out.append("\n```sparql\n" + q["query"] + "```\n")
    out.append("\n## Rule card — everything the rules concluded about one component\n\n"
               "`rule_card(cfg, \"PSV-01\")` substitutes the tag.\n\n```sparql\n"
               + RULE_CARD.strip() + "\n```\n")
    text = "".join(out)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text
