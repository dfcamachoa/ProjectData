"""Live parity check: Jena's classification.rules vs rules_reference.py.

`jena_rules/classification.rules` has so far been validated only by
inspection. This module runs it for real, on the `gold-rules` Fuseki
service (`fuseki/gold-rules.ttl`), over the SAME triples `rules_reference.py`
reads from the in-memory `Dataset`, and reports every disagreement.

Flow (each step is its own function so a notebook can run them one by one):

1. `export_rule_input(ds)` — writes graph:masterdata and graph:refdata of
   `ds` as N-Triples into `fuseki/rules-input/`. Only those two graphs;
   graph:oracle and graph:results are never written (asserted).
2. `restart_fuseki()` — `docker restart gold-fuseki`, then waits for the
   `gold-rules` endpoint. The service loads its input and the rules at
   startup, so a restart recomputes every inference from scratch.
3. `run_parity(ds, cfg)` — computes the Python answers on `ds`, queries the
   Jena answers from `gold-rules`, and diffs them in four sections:
   fluid classification, flare guard, supply-tie-in guard, relief
   attribution. Plus a preflight: the input actually loaded matches `ds`
   (triple counts per rule-relevant predicate), the relief role URI the
   rule binds exists, and no oracle predicate reached the reasoner.

`ParityReport.ok` is the single pass/fail. Every comparison core
(`diff_pairs`, `diff_classification`, `diff_relief`) is pure — plain sets
and dicts of strings — so it is unit-tested without rdflib or Fuseki
(`tests/test_jena_parity.py`).

CLI smoke test, same fixture scenario as `fuseki_bootstrap --fixture`:

    python -m gold.jena_parity --fixture
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple

PIDSYS = "https://pidsys.example/ns#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RELIEF_ROLE_URI = PIDSYS + "boundary_role/relief"   # must match the rule's constant
RULES_DATASET = "gold-rules"
CONTAINER = "gold-fuseki"
DEFAULT_INPUT_DIR = Path(__file__).resolve().parents[1] / "fuseki" / "rules-input"

RULES_FILE = Path(__file__).resolve().parent / "jena_rules" / "classification.rules"
# The vocab constants rules_reference.py reads (names, resolved at run time
# against gold.vocab — never hard-coded URIs). Each must also appear in the
# rule bodies, or the two engines are reading different data. 2026-09-23
# real-run finding: the projection moved connectivity to ido:connectedTo
# (pidsys_extension.ttl, "no pidsys: connectivity property is minted") while
# the rules still read pidsys:isConnectedTo — 0 triples, every guard silent.
PYTHON_RULE_INPUTS = ("P_IS_CONNECTED_TO", "P_FLOWS_TO", "P_PART_OF", "P_FLUID_CODE",
                      "P_CATEGORY", "P_SUBCATEGORY", "P_BOUNDARY_MEMBER")
ORACLE_PREDICATES = (PIDSYS + "srcTurnoverSystem", PIDSYS + "srcSubsystem")

Pair = Tuple[str, str]


def rule_body_predicates(rules_text: str) -> Set[str]:
    """Full URIs of every predicate a rule BODY matches on (triple patterns
    and noValue() patterns), minus predicates any rule itself derives.
    Prefixes are expanded from the file's own @prefix lines."""
    import re

    prefixes = dict(re.findall(r"@prefix\s+(\w*):\s*<([^>]+)>", rules_text))
    body_preds: Set[str] = set()
    head_preds: Set[str] = set()

    def expand(tok: str) -> Optional[str]:
        if tok.startswith("<") and tok.endswith(">"):
            return tok[1:-1]
        if ":" in tok and not tok.startswith("?"):
            pfx, local = tok.split(":", 1)
            return prefixes[pfx] + local if pfx in prefixes else None
        return None

    # whole-line comments only: a '#' mid-line is usually inside an IRI
    # (<https://pidsys.example/ns#boundary_role/relief>), not a comment
    code = "\n".join("" if ln.lstrip().startswith(("#", "//", "@")) else ln
                     for ln in rules_text.splitlines())
    for rule in re.findall(r"\[\s*\w+\s*:(.*?)\]", code, flags=re.S):
        body, _, head = rule.partition("->")
        for target, part in ((body_preds, body), (head_preds, head)):
            for pattern in re.findall(r"\(([^()]*)\)", part):
                toks = pattern.split()
                if len(toks) == 3 and "," not in pattern:
                    uri = expand(toks[1])
                    if uri:
                        target.add(uri)
    return body_preds - head_preds


# ---------------------------------------------------------------------------
# Pure comparison cores (no rdflib, no network)
# ---------------------------------------------------------------------------

def diff_pairs(python: Set[Pair], jena: Set[Pair]) -> dict:
    return {
        "agree": len(python & jena),
        "python_only": sorted(python - jena),
        "jena_only": sorted(jena - python),
    }


def diff_classification(python: Dict[str, str], jena: Dict[str, Optional[str]]) -> dict:
    """python: fluid_code -> 'flare'|'steam_condensate'|'process'|'utility'.
    jena: fluid_code -> selfOwningClass literal, or None (= Python's 'utility')."""
    mismatches = []
    agree = 0
    for code in sorted(set(python) | set(jena)):
        p = python.get(code, "<absent>")
        j = jena.get(code, "<absent>") if code in jena else "<absent>"
        j_norm = "utility" if j is None else j
        if p == j_norm:
            agree += 1
        else:
            mismatches.append({"fluid_code": code, "python": p, "jena": j_norm})
    return {"agree": agree, "mismatches": mismatches}


def diff_relief(python: Dict[str, Optional[str]], jena: Dict[str, Set[str]]) -> dict:
    """python: valve -> protected neighbour or None (relief_attribution).
    jena: valve -> set of protectedBy objects (every qualifying neighbour).

    Categories: agree (same single answer, or both none); jena_multi (Python's
    answer is among 2+ Jena answers — Python returns the first it meets, so
    this is a real ambiguity in the data, not a rule bug); mismatch
    (everything else). Valves Jena attributes but Python never evaluated
    land in jena_only_valves — usually a relief-class membership difference."""
    agree, jena_multi, mismatch = 0, [], []
    for valve in sorted(python):
        p = python[valve]
        j = jena.get(valve, set())
        if (p is None and not j) or (p is not None and j == {p}):
            agree += 1
        elif p is not None and p in j:
            jena_multi.append({"valve": valve, "python": p, "jena": sorted(j)})
        else:
            mismatch.append({"valve": valve, "python": p, "jena": sorted(j)})
    jena_only_valves = sorted(set(jena) - set(python))
    return {"agree": agree, "jena_multi": jena_multi, "mismatch": mismatch,
            "jena_only_valves": jena_only_valves}


@dataclass
class ParityReport:
    preflight: dict = field(default_factory=dict)
    classification: dict = field(default_factory=dict)
    flare_guard: dict = field(default_factory=dict)
    supply_tie_in_guard: dict = field(default_factory=dict)
    relief: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        pf = self.preflight
        return (
            pf.get("input_counts_match", False)
            and pf.get("input_matches_ds", True)
            and not pf.get("python_inputs_missing_from_rules", ["?"])
            and not pf.get("rule_predicates_without_data", ["?"])
            and pf.get("relief_role_present", False)
            and not pf.get("oracle_leak", True)
            and not self.classification.get("mismatches")
            and not self.flare_guard.get("python_only") and not self.flare_guard.get("jena_only")
            and not self.supply_tie_in_guard.get("python_only")
            and not self.supply_tie_in_guard.get("jena_only")
            and not self.relief.get("mismatch") and not self.relief.get("jena_only_valves")
        )

    def summary(self, show: int = 5) -> str:
        pf = self.preflight
        lines = [f"PARITY {'PASS' if self.ok else 'FAIL'}",
                 "preflight:",
                 f"  input counts match ds : {pf.get('input_counts_match')}"]
        for pred, (n_py, n_j) in sorted(pf.get("input_counts", {}).items()):
            flag = "" if n_py == n_j else "   <-- differs"
            lines.append(f"    {pred:<24} ds={n_py:<7} jena={n_j}{flag}")
        lines += [
            "  Python-rule inputs no Jena rule reads : "
            f"{', '.join(pf.get('python_inputs_missing_from_rules', [])) or 'none'}",
            "  Jena-rule predicates with no data     : "
            f"{', '.join(pf.get('rule_predicates_without_data', [])) or 'none'}"]
        lines += [f"  relief role URI present: {pf.get('relief_role_present')}  "
                  f"(roles seen: {', '.join(pf.get('roles_seen', [])) or 'none'})",
                  f"  oracle predicate in reasoner input: {pf.get('oracle_leak')}",
                  f"  exported input is this ds          : {pf.get('input_matches_ds')}"]
        c = self.classification
        lines.append(f"fluid classification : {c.get('agree', 0)} agree, "
                     f"{len(c.get('mismatches', []))} mismatch")
        lines += [f"    {m}" for m in c.get("mismatches", [])[:show]]
        for name, d in (("flare guard", self.flare_guard),
                        ("supply-tie-in guard", self.supply_tie_in_guard)):
            lines.append(f"{name:<21}: {d.get('agree', 0)} agree, "
                         f"{len(d.get('python_only', []))} python-only, "
                         f"{len(d.get('jena_only', []))} jena-only")
            lines += [f"    python-only {p}" for p in d.get("python_only", [])[:show]]
            lines += [f"    jena-only   {p}" for p in d.get("jena_only", [])[:show]]
        r = self.relief
        lines.append(f"relief attribution   : {r.get('agree', 0)} agree, "
                     f"{len(r.get('jena_multi', []))} multi-candidate, "
                     f"{len(r.get('mismatch', []))} mismatch, "
                     f"{len(r.get('jena_only_valves', []))} jena-only valves")
        lines += [f"    multi    {m}" for m in r.get("jena_multi", [])[:show]]
        lines += [f"    mismatch {m}" for m in r.get("mismatch", [])[:show]]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "preflight": self.preflight,
                "classification": self.classification, "flare_guard": self.flare_guard,
                "supply_tie_in_guard": self.supply_tie_in_guard, "relief": self.relief}


# ---------------------------------------------------------------------------
# Step 1 — export the reasoner's input (masterdata + refdata only)
# ---------------------------------------------------------------------------

def _graph_to_ntriples(ds, graph: str) -> Tuple[str, int]:
    """rdflib's own N-Triples serializer (correct escaping of multi-line and
    typed literals); rdflib raises on an IRI with characters N-Triples
    forbids, so a bad id fails here, loudly, not as a Fuseki startup error."""
    from rdflib import Graph

    g = Graph()
    for q in ds.triples(graph=graph):
        g.add((q.s, q.p, q.o))
    text = g.serialize(format="nt")
    text = text.decode("utf-8") if isinstance(text, bytes) else text
    return canonical_ntriples(text), len(g)


def canonical_ntriples(text: str) -> str:
    """Sorted, blank-line-free N-Triples — the same triples always give the
    same bytes, so a sha256 over it identifies a graph's content. (Holds
    while the graph has no blank nodes, true of masterdata/refdata today;
    rdflib relabels bnodes per serialisation.)"""
    lines = sorted(ln for ln in text.splitlines() if ln.strip())
    return "\n".join(lines) + ("\n" if lines else "")


def sha256_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def input_fingerprint(input_dir: Path = DEFAULT_INPUT_DIR, rules_file: Path = RULES_FILE) -> dict:
    """sha256 of exactly what gold-rules loaded: the two exported input files
    (as written, already canonical) and the rules file."""
    input_dir = Path(input_dir)
    fp = {name: sha256_text(canonical_ntriples((input_dir / f"{name}.nt").read_text(encoding="utf-8")))
          for name in ("masterdata", "refdata")}
    fp["rules"] = sha256_text(Path(rules_file).read_text(encoding="utf-8"))
    return fp


def ds_fingerprint(ds) -> dict:
    """The same masterdata/refdata sha256 computed from an in-memory Dataset,
    without writing files — for "are the materialised inferences still
    about this data?" checks (gold/inferred_graph.py)."""
    from . import vocab as v
    return {name: sha256_text(_graph_to_ntriples(ds, g)[0])
            for name, g in (("masterdata", v.GRAPH_MASTERDATA), ("refdata", v.GRAPH_REFDATA))}


def export_rule_input(ds, out_dir: Path = DEFAULT_INPUT_DIR) -> dict:
    """Writes masterdata.nt and refdata.nt — nothing else — for gold-rules."""
    from . import vocab as v

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, graph in (("masterdata", v.GRAPH_MASTERDATA), ("refdata", v.GRAPH_REFDATA)):
        text, n = _graph_to_ntriples(ds, graph)
        leaked = [p for p in ORACLE_PREDICATES if f"<{p}>" in text]
        if leaked:
            raise RuntimeError(f"oracle predicate(s) {leaked} found in {graph} — refusing to "
                               "export them to the rule engine (oracle_guard invariant)")
        (out_dir / f"{name}.nt").write_text(text, encoding="utf-8")
        written[name] = n
    return {"dir": str(out_dir), "triples": written,
            "sha256": {k: v[:12] for k, v in input_fingerprint(out_dir).items()}}


# ---------------------------------------------------------------------------
# Step 2 — restart Fuseki so gold-rules reloads input + rules
# ---------------------------------------------------------------------------

def _request(url: str, user: Optional[str], password: Optional[str],
             data: Optional[bytes] = None, headers: Optional[dict] = None, timeout: int = 120):
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}))
    if user is not None:
        token = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    return urllib.request.urlopen(req, timeout=timeout)


# Where a query may be answered, in order. Fuseki versions and config styles
# differ in which of these they register; the first that answers is cached.
# (2026-09-23 real-run finding: /gold-rules/sparql answered HTTP 405 on the
# user's stain/jena-fuseki — the endpoint name did not register as a query
# operation.) A plain GET is used: every query endpoint must accept it.
_QUERY_PATHS = ("sparql", "query", "")
_working_query_url: Dict[str, str] = {}


def _query_urls(base_url: str, dataset: str):
    root = f"{base_url.rstrip('/')}/{dataset}"
    cached = _working_query_url.get(root)
    if cached:
        return [cached]
    return [f"{root}/{p}" if p else root for p in _QUERY_PATHS]


def sparql_select(base_url: str, query: str, user: Optional[str] = None,
                  password: Optional[str] = None, dataset: str = RULES_DATASET) -> list:
    root = f"{base_url.rstrip('/')}/{dataset}"
    refusals = []
    for url in _query_urls(base_url, dataset):
        full = url + "?" + urllib.parse.urlencode({"query": query})
        try:
            with _request(full, user, password,
                          headers={"Accept": "application/sparql-results+json"}) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (404, 405, 415):          # not a query endpoint here — try the next
                refusals.append(f"{url} -> HTTP {e.code}")
                continue
            raise
        _working_query_url[root] = url
        if "boolean" in payload:
            return [payload["boolean"]]
        return [{k: b[k]["value"] for k in b} for b in payload["results"]["bindings"]]
    raise EndpointNotFound("no SPARQL query endpoint answered on "
                           f"{root}: " + "; ".join(refusals))


class EndpointNotFound(RuntimeError):
    """Fuseki is up but gold-rules exposes no query endpoint — a config
    problem, never fixed by waiting, so it is not retried."""


def diagnose(base_url: str = "http://localhost:3030", container: str = CONTAINER,
             user: Optional[str] = "admin", password: Optional[str] = "admin") -> str:
    """What Fuseki itself says it loaded: version, every dataset and its
    endpoints (admin /$/server), plus the container log lines that mention
    gold-rules, rules, errors or warnings."""
    out = []
    try:
        with _request(f"{base_url.rstrip('/')}/$/server", user, password,
                      headers={"Accept": "application/json"}, timeout=15) as r:
            info = json.loads(r.read().decode("utf-8"))
        out.append(f"Fuseki version: {info.get('version')}")
        for ds in info.get("datasets", []):
            out.append(f"dataset {ds.get('ds.name')}  state={ds.get('ds.state')}")
            for svc in ds.get("ds.services", []):
                out.append(f"    {svc.get('srv.type'):<10} {svc.get('srv.endpoints')}")
    except Exception as e:  # noqa: BLE001 - diagnostic, report whatever happened
        out.append(f"/$/server unavailable: {e}")
    try:
        logs = subprocess.run(["docker", "logs", "--tail", "400", container],
                              capture_output=True, text=True, timeout=30)
        keep = [ln for ln in (logs.stdout + logs.stderr).splitlines()
                if any(k in ln.lower() for k in ("gold-rules", "rules", "error", "warn",
                                                   "exception", "version", "configuration"))]
        out.append("docker logs (filtered):")
        out += ["    " + ln for ln in keep[-40:]] or ["    (no matching lines)"]
    except Exception as e:  # noqa: BLE001
        out.append(f"docker logs unavailable: {e}")
    return "\n".join(out)


def restart_fuseki(base_url: str = "http://localhost:3030", container: str = CONTAINER,
                   user: Optional[str] = "admin", password: Optional[str] = "admin",
                   timeout_s: int = 120) -> float:
    """docker restart, then poll until gold-rules answers a query (the first
    query also triggers the reasoner's preparation). Returns seconds taken.

    Retries only while Fuseki is still starting (connection refused/reset,
    HTTP 503). An HTTP refusal from a running Fuseki is a config problem:
    it fails at once, with diagnose()'s output attached."""
    _working_query_url.clear()
    t0 = time.time()
    subprocess.run(["docker", "restart", container], check=True, capture_output=True)
    last_err = None
    while time.time() - t0 < timeout_s:
        try:
            sparql_select(base_url, "ASK { ?s ?p ?o }", user, password)
            return round(time.time() - t0, 1)
        except EndpointNotFound as e:
            raise EndpointNotFound(f"{e}\n\n{diagnose(base_url, container, user, password)}") from None
        except urllib.error.HTTPError as e:
            if e.code != 503:
                body = e.read().decode("utf-8", "replace")[:500]
                raise RuntimeError(f"gold-rules answered HTTP {e.code}: {body}\n\n"
                                   f"{diagnose(base_url, container, user, password)}") from None
            last_err = e
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = e
        time.sleep(2)
    raise TimeoutError(f"gold-rules not answering after {timeout_s}s: {last_err}\n\n"
                       f"{diagnose(base_url, container, user, password)}")


# ---------------------------------------------------------------------------
# Step 3 — compute both sides and diff
# ---------------------------------------------------------------------------

def _python_side(ds) -> dict:
    from . import vocab as v
    from . import rules_reference as rr
    from .rdf_model import URIRef

    md, rd = v.GRAPH_MASTERDATA, v.GRAPH_REFDATA
    catalogue = rr.load_fluid_catalogue(ds)
    classification = {code: rr.classify_fluid_category(code, catalogue) for code in catalogue}

    pairs = {(q.s, q.o) for q in ds.triples(p=URIRef(v.P_IS_CONNECTED_TO), graph=md)}
    flare, supply = set(), set()
    for a, b in pairs:
        if rr.flare_guard(ds, a, b, catalogue):
            flare.add((str(a), str(b)))
        if rr.directional_consumer_guard(ds, a, b):
            supply.add((str(a), str(b)))

    relief_classes = {q.o for q in ds.triples(s=URIRef(RELIEF_ROLE_URI),
                                               p=URIRef(v.P_BOUNDARY_MEMBER), graph=rd)}
    valves = {q.s for cls in relief_classes
              for q in ds.triples(p=URIRef(RDF_TYPE), o=cls, graph=md)}
    relief = {}
    for valve in valves:
        protected = rr.relief_attribution(ds, valve)
        relief[str(valve)] = str(protected) if protected is not None else None

    python_inputs = {getattr(v, name) for name in PYTHON_RULE_INPUTS}
    rule_preds = rule_body_predicates(RULES_FILE.read_text(encoding="utf-8"))
    counts = {}
    for uri in sorted(rule_preds | python_inputs):
        p = URIRef(uri)
        counts[uri] = sum(1 for _ in ds.triples(p=p, graph=md)) + \
            sum(1 for _ in ds.triples(p=p, graph=rd))
    roles = sorted({str(q.s) for q in ds.triples(p=URIRef(v.P_BOUNDARY_MEMBER), graph=rd)})
    return {"classification": classification, "flare": flare, "supply": supply,
            "relief": relief, "counts": counts, "roles": roles,
            "python_inputs": python_inputs, "rule_preds": rule_preds}


_PFX = f"PREFIX pidsys: <{PIDSYS}>\nPREFIX rdf: <{RDF_TYPE[:-4]}>\n"


def _jena_side(base_url: str, user: Optional[str], password: Optional[str],
               count_predicates: Iterable[str] = ()) -> dict:
    q = lambda s: sparql_select(base_url, _PFX + s, user, password)  # noqa: E731
    classification = {}
    for r in q("SELECT ?code ?cls WHERE { ?f rdf:type pidsys:Fluid ; pidsys:fluidCode ?code . "
               "OPTIONAL { ?f pidsys:selfOwningClass ?cls } }"):
        classification[r["code"]] = r.get("cls")
    flare = {(r["a"], r["b"]) for r in q("SELECT ?a ?b WHERE { ?a pidsys:skipFlareSink ?b }")}
    supply = {(r["a"], r["b"]) for r in q("SELECT ?a ?b WHERE { ?a pidsys:skipSupplyTieIn ?b }")}
    relief: Dict[str, Set[str]] = {}
    for r in q("SELECT ?v ?p WHERE { ?v pidsys:protectedBy ?p }"):
        relief.setdefault(r["v"], set()).add(r["p"])
    counts = {}
    for uri in count_predicates:
        rows = q(f"SELECT (COUNT(*) AS ?n) WHERE {{ ?s <{uri}> ?o }}")
        counts[uri] = int(rows[0]["n"]) if rows else 0
    oracle_leak = any(q(f"ASK {{ ?s <{p}> ?o }}")[0] for p in ORACLE_PREDICATES)
    return {"classification": classification, "flare": flare, "supply": supply,
            "relief": relief, "counts": counts, "oracle_leak": oracle_leak}


def _short(uri: str) -> str:
    for pfx, ns in (("pidsys:", PIDSYS), ("rdf:", RDF_TYPE[:-4]),
                    ("ido:", "https://www.omg.org/spec/Commons/IndustrialData/")):
        if uri.startswith(ns):
            return pfx + uri[len(ns):]
    return f"<{uri}>"


def run_parity(ds, base_url: str = "http://localhost:3030", user: Optional[str] = "admin",
               password: Optional[str] = "admin",
               input_dir: Path = DEFAULT_INPUT_DIR) -> ParityReport:
    py = _python_side(ds)
    je = _jena_side(base_url, user, password, count_predicates=py["counts"].keys())
    input_counts = {_short(k): (py["counts"][k], je["counts"][k]) for k in py["counts"]}
    return ParityReport(
        preflight={
            "input_counts": input_counts,
            "input_counts_match": all(a == b for a, b in input_counts.values()),
            # predicates the Python rules read that no Jena rule body reads
            "python_inputs_missing_from_rules": sorted(
                _short(u) for u in py["python_inputs"] - py["rule_preds"]),
            # predicates a Jena rule body reads that have no data at all
            "rule_predicates_without_data": sorted(
                _short(u) for u in py["rule_preds"] if py["counts"].get(u, 0) == 0),
            "relief_role_present": RELIEF_ROLE_URI in py["roles"],
            "roles_seen": [r.rsplit("/", 1)[-1] for r in py["roles"]],
            "oracle_leak": je["oracle_leak"],
            # what this verdict is ABOUT: the exact input files and rules
            # gold-rules loaded. materialise_inferences refuses a report
            # whose fingerprint no longer matches what is loaded.
            "input_fingerprint": input_fingerprint(input_dir),
            # and whether that input is the ds the Python side just read
            "input_matches_ds": {k: val for k, val in ds_fingerprint(ds).items()}
                                == {k: input_fingerprint(input_dir)[k]
                                    for k in ("masterdata", "refdata")},
        },
        classification=diff_classification(py["classification"], je["classification"]),
        flare_guard=diff_pairs(py["flare"], je["flare"]),
        supply_tie_in_guard=diff_pairs(py["supply"], je["supply"]),
        relief=diff_relief(py["relief"], je["relief"]),
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", action="store_true",
                    help="run against tests/fixtures.py's nitrogen/process/flare scenario")
    ap.add_argument("--base-url", default="http://localhost:3030")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--no-restart", action="store_true",
                    help="skip docker restart (input already loaded)")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if not args.fixture:
        ap.error("from the CLI only --fixture is supported; for real data call "
                 "export_rule_input / restart_fuseki / run_parity from the notebook (§8b)")
    from tests.fixtures import build_fixture_dataset
    ds = build_fixture_dataset()
    print(json.dumps(export_rule_input(ds)))
    if not args.no_restart:
        print(f"gold-rules ready after {restart_fuseki(args.base_url, user=args.user, password=args.password)}s")
    report = run_parity(ds, args.base_url, args.user, args.password)
    print(report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
