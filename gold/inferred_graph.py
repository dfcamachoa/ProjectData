"""graph:inferred — the Jena rules' output, materialised with PROV.

The `gold-rules` Fuseki service (gold_layer_spec §5.6) computes the
classification predicates live, but only on its own read-only endpoint.
This module writes them into the `gold` dataset as a named graph a
systemization run can read next to graph:masterdata:

    graph:masterdata  --(gold-rules, GenericRuleReasoner)-->  graph:inferred

Three parts:

1. `materialise_inferences(parity, cfg)` — WRITE. Gated:
   * the ParityReport must pass (`parity.ok`) — Jena output is only
     published once it has been shown to equal the Python reference;
   * the report's input fingerprint must equal what gold-rules has loaded
     *now* (the two exported .nt files + the rules file) — a verdict about
     yesterday's input does not license today's output.
   It CONSTRUCTs exactly the predicates the rules derive (read from the rule
   heads, not hard-coded), adds PROV, and PUTs the graph (drop-and-replace,
   like every other Gold graph). graph:oracle cannot reach it: gold-rules
   never loads it, and the text is re-checked before the PUT.

2. The PROV shape (`build_provenance_ntriples`, pure, unit-tested):
     <graph/inferred>  a prov:Entity ; prov:wasGeneratedBy <run> ;
                       prov:wasDerivedFrom <graph/masterdata>, <graph/refdata> ;
                       pidsys:tripleCount n .
     <run>             a prov:Activity ; prov:startedAtTime ; prov:endedAtTime ;
                       prov:used <input/masterdata/sha>, <input/refdata/sha>, <rules/sha> ;
                       prov:wasAssociatedWith <agent/gold-rules> ;
                       prov:qualifiedAssociation <run/association> ;
                       pidsys:parityStatus "PASS" ; pidsys:parity<Section>Agree n .
     <input/...>       a prov:Entity ; prov:specializationOf <graph/masterdata> ;
                       pidsys:sha256 "..." .
     <rules/sha>       a prov:Plan ; pidsys:sha256 ; pidsys:sourceFile "..." .
     <rule/NAME>       a prov:Plan ; pidsys:definedIn <rules/sha> .
     pidsys:PRED       pidsys:derivedByRule <rule/NAME> .   (one per rule head)
   Per-fact provenance ("which rule fired") is carried by the predicate
   itself — each derived predicate is produced by a known rule set, stated
   as `derivedByRule` — so no reification or RDF-star is needed.
   `selfOwningClass` has three rules; its value ("flare" / "steam_condensate"
   / "process") says which one fired.

3. `load_inferred(cfg)` / `inferred_is_current(cfg, ds)` — READ, for the
   systemization run. Returns plain Python sets/dicts keyed by component id,
   and a freshness check: the run's recorded input sha256 against the
   masterdata/refdata of the `ds` about to be walked. push_dataset does not
   touch graph:inferred, so after a re-projection it goes stale until the
   next §8b + §8c — this check is what makes that visible.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .jena_parity import (
    DEFAULT_INPUT_DIR, ORACLE_PREDICATES, PIDSYS, RULES_DATASET, RULES_FILE,
    _query_urls, _request, canonical_ntriples, ds_fingerprint, input_fingerprint,
)

GRAPH_INFERRED = PIDSYS + "graph/inferred"   # TODO: move to gold/vocab.py beside GRAPH_RESULTS
GRAPH_MASTERDATA = PIDSYS + "graph/masterdata"
GRAPH_REFDATA = PIDSYS + "graph/refdata"
PROV = "http://www.w3.org/ns/prov#"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
AGENT_GOLD_RULES = PIDSYS + "agent/gold-rules"

# the predicates a systemization run consumes (all derived by the rules)
P_SELF_OWNING = PIDSYS + "selfOwningClass"
P_SKIP = PIDSYS + "skipAsConsumerSignal"
P_SKIP_FLARE = PIDSYS + "skipFlareSink"
P_SKIP_SUPPLY = PIDSYS + "skipSupplyTieIn"
P_PROTECTED_BY = PIDSYS + "protectedBy"


class MaterialiseRefused(RuntimeError):
    """The gate said no — parity failed, or it is about different input."""


# ---------------------------------------------------------------------------
# Pure helpers (no network, no rdflib) — unit-tested
# ---------------------------------------------------------------------------

def rule_heads(rules_text: str) -> Dict[str, List[str]]:
    """{predicate URI: [rule names whose HEAD asserts it]} from the rules file,
    prefixes expanded from its own @prefix lines."""
    prefixes = dict(re.findall(r"@prefix\s+(\w*):\s*<([^>]+)>", rules_text))
    code = "\n".join("" if ln.lstrip().startswith(("#", "//", "@")) else ln
                     for ln in rules_text.splitlines())
    heads: Dict[str, List[str]] = {}
    for name, body in re.findall(r"\[\s*(\w+)\s*:(.*?)\]", code, flags=re.S):
        _, _, head = body.partition("->")
        for pattern in re.findall(r"\(([^()]*)\)", head):
            toks = pattern.split()
            if len(toks) != 3:
                continue
            tok = toks[1]
            if tok.startswith("<") and tok.endswith(">"):
                uri = tok[1:-1]
            elif ":" in tok and tok.split(":", 1)[0] in prefixes:
                pfx, local = tok.split(":", 1)
                uri = prefixes[pfx] + local
            else:
                continue
            heads.setdefault(uri, [])
            if name not in heads[uri]:
                heads[uri].append(name)
    return heads


def _iri(u: str) -> str:
    return f"<{u}>"


def _lit(value, datatype: Optional[str] = None) -> str:
    esc = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{esc}"' + (f"^^<{datatype}>" if datatype else "")


def _count(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))


def build_provenance_ntriples(*, run_id: str, started: datetime, ended: datetime,
                              fingerprint: Dict[str, str], rules_heads: Dict[str, List[str]],
                              rules_path: str, derived_triple_count: int,
                              parity_summary: Dict[str, int]) -> str:
    """N-Triples for the PROV block described in the module docstring."""
    g = _iri(GRAPH_INFERRED)
    run = _iri(PIDSYS + f"run/rules/{run_id}")
    assoc = _iri(PIDSYS + f"run/rules/{run_id}/association")
    rules = _iri(PIDSYS + f"rules/{fingerprint['rules'][:16]}")
    t = lambda s, p, o: f"{s} {_iri(p)} {o} ."  # noqa: E731
    out = [
        t(g, RDF_TYPE, _iri(PROV + "Entity")),
        t(g, PROV + "wasGeneratedBy", run),
        t(g, PROV + "wasDerivedFrom", _iri(GRAPH_MASTERDATA)),
        t(g, PROV + "wasDerivedFrom", _iri(GRAPH_REFDATA)),
        t(g, PIDSYS + "tripleCount", _lit(derived_triple_count, XSD + "integer")),
        t(run, RDF_TYPE, _iri(PROV + "Activity")),
        t(run, RDFS_LABEL, _lit(f"Jena classification rules run {run_id}")),
        t(run, PROV + "startedAtTime", _lit(started.isoformat(), XSD + "dateTime")),
        t(run, PROV + "endedAtTime", _lit(ended.isoformat(), XSD + "dateTime")),
        t(run, PROV + "wasAssociatedWith", _iri(AGENT_GOLD_RULES)),
        t(run, PROV + "qualifiedAssociation", assoc),
        t(assoc, RDF_TYPE, _iri(PROV + "Association")),
        t(assoc, PROV + "agent", _iri(AGENT_GOLD_RULES)),
        t(assoc, PROV + "hadPlan", rules),
        t(_iri(AGENT_GOLD_RULES), RDF_TYPE, _iri(PROV + "SoftwareAgent")),
        t(_iri(AGENT_GOLD_RULES), RDFS_LABEL,
          _lit("Fuseki gold-rules service (Jena GenericRuleReasoner)")),
        t(rules, RDF_TYPE, _iri(PROV + "Plan")),
        t(rules, PIDSYS + "sha256", _lit(fingerprint["rules"])),
        t(rules, PIDSYS + "sourceFile", _lit(rules_path)),
        t(run, PROV + "used", rules),
        t(run, PIDSYS + "parityStatus", _lit("PASS")),
    ]
    for name, graph in (("masterdata", GRAPH_MASTERDATA), ("refdata", GRAPH_REFDATA)):
        snap = _iri(PIDSYS + f"input/{name}/{fingerprint[name][:16]}")
        out += [t(snap, RDF_TYPE, _iri(PROV + "Entity")),
                t(snap, PROV + "specializationOf", _iri(graph)),
                t(snap, PIDSYS + "sha256", _lit(fingerprint[name])),
                t(run, PROV + "used", snap)]
    for section, n in sorted(parity_summary.items()):
        out.append(t(run, PIDSYS + f"parityAgree_{section}", _lit(n, XSD + "integer")))
    for pred, names in sorted(rules_heads.items()):
        for name in names:
            rule = _iri(PIDSYS + f"rule/{name}")
            out += [t(_iri(pred), PIDSYS + "derivedByRule", rule),
                    t(rule, RDF_TYPE, _iri(PROV + "Plan")),
                    t(rule, RDFS_LABEL, _lit(name)),
                    t(rule, PIDSYS + "definedIn", rules)]
    return "\n".join(sorted(set(out))) + "\n"


def check_gate(parity, loaded_fingerprint: Dict[str, str], allow_unverified: bool = False) -> None:
    """Raises MaterialiseRefused unless parity passed on exactly this input."""
    if allow_unverified:
        return
    if not parity.ok:
        raise MaterialiseRefused("parity report is FAIL — Jena output is published only after it "
                                 "equals the Python reference (gold_layer_spec §5.6). "
                                 "Fix the drift, re-run §8b, then materialise.")
    checked = parity.preflight.get("input_fingerprint")
    if checked != loaded_fingerprint:
        changed = sorted(k for k in loaded_fingerprint
                         if (checked or {}).get(k) != loaded_fingerprint[k])
        raise MaterialiseRefused(f"parity report is about different input than gold-rules has loaded "
                                 f"now (changed: {', '.join(changed) or 'no fingerprint recorded'}). "
                                 "Re-run §8b on the current export, then materialise.")


def parity_summary(parity) -> Dict[str, int]:
    return {
        "classification": parity.classification.get("agree", 0),
        "flare_guard": parity.flare_guard.get("agree", 0),
        "supply_tie_in_guard": parity.supply_tie_in_guard.get("agree", 0),
        "relief": parity.relief.get("agree", 0),
    }


# ---------------------------------------------------------------------------
# Network parts
# ---------------------------------------------------------------------------

def _construct_ntriples(base_url: str, query: str, user: Optional[str], password: Optional[str],
                        dataset: str = RULES_DATASET) -> str:
    refusals = []
    for url in _query_urls(base_url, dataset):
        full = url + "?" + urllib.parse.urlencode({"query": query})
        try:
            with _request(full, user, password, headers={"Accept": "application/n-triples"}) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (404, 405, 415):
                refusals.append(f"{url} -> HTTP {e.code}")
                continue
            raise
    raise RuntimeError("no query endpoint answered on gold-rules: " + "; ".join(refusals))


def materialise_inferences(parity, cfg, base_url: str = "http://localhost:3030",
                           user: Optional[str] = "admin", password: Optional[str] = "admin",
                           input_dir: Path = DEFAULT_INPUT_DIR, rules_file: Path = RULES_FILE,
                           allow_unverified: bool = False) -> dict:
    """Gate → CONSTRUCT the rule-derived triples from gold-rules → add PROV →
    PUT graph:inferred into the `gold` dataset `cfg` points at."""
    from .fuseki_client import push_named_graph

    started = datetime.now(timezone.utc).replace(microsecond=0)
    loaded = input_fingerprint(input_dir, rules_file)
    check_gate(parity, loaded, allow_unverified)

    rules_text = Path(rules_file).read_text(encoding="utf-8")
    heads = rule_heads(rules_text)
    if not heads:
        raise RuntimeError(f"no rule heads parsed from {rules_file}")
    values = " ".join(_iri(p) for p in sorted(heads))
    derived = canonical_ntriples(_construct_ntriples(
        base_url, f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ VALUES ?p {{ {values} }} ?s ?p ?o }}",
        user, password))
    n_derived = _count(derived)

    ended = datetime.now(timezone.utc).replace(microsecond=0)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    prov = build_provenance_ntriples(
        run_id=run_id, started=started, ended=ended, fingerprint=loaded, rules_heads=heads,
        rules_path="gold/jena_rules/classification.rules", derived_triple_count=n_derived,
        parity_summary=parity_summary(parity) if parity is not None else {})
    text = derived + prov

    leaked = [p for p in ORACLE_PREDICATES if f"<{p}>" in text]
    if leaked:   # cannot happen while gold-rules never loads graph:oracle; checked anyway
        raise RuntimeError(f"oracle predicate(s) {leaked} in graph:inferred — refusing to push")

    status = push_named_graph(cfg, GRAPH_INFERRED, text)   # N-Triples is valid Turtle
    by_pred: Dict[str, int] = {}
    for ln in derived.splitlines():
        parts = ln.split(" ", 2)
        if len(parts) == 3:
            name = parts[1].strip("<>").rsplit("#", 1)[-1]
            by_pred[name] = by_pred.get(name, 0) + 1
    return {"graph": GRAPH_INFERRED, "http_status": status, "run": PIDSYS + f"run/rules/{run_id}",
            "derived_triples": n_derived, "prov_triples": _count(prov),
            "by_predicate": dict(sorted(by_pred.items())),
            "input_sha256": {k: v[:12] for k, v in loaded.items()},
            "unverified": bool(allow_unverified)}


# ---------------------------------------------------------------------------
# Consumer side — what a systemization run reads
# ---------------------------------------------------------------------------

def local_id(uri: str) -> str:
    """'…#component/SP0A…' -> 'SP0A…' (the Silver id the walk uses)."""
    return uri.rsplit("/", 1)[-1]


@dataclass
class InferredSignals:
    self_owning: Dict[str, str] = field(default_factory=dict)       # fluid_code -> class
    skip_flare_sink: Set[Tuple[str, str]] = field(default_factory=set)       # (fragment, neighbour)
    skip_supply_tie_in: Set[Tuple[str, str]] = field(default_factory=set)
    protected_by: Dict[str, Set[str]] = field(default_factory=dict)  # valve -> protected neighbours
    provenance: Dict[str, str] = field(default_factory=dict)

    @property
    def skip_as_consumer(self) -> Set[Tuple[str, str]]:
        return self.skip_flare_sink | self.skip_supply_tie_in

    def summary(self) -> str:
        p = self.provenance
        return "\n".join([
            f"graph:inferred from run {p.get('run', '?').rsplit('/', 1)[-1]} "
            f"(parity {p.get('parityStatus', '?')}, ended {p.get('ended', '?')})",
            f"  self-owning fluids        : {len(self.self_owning)} "
            f"({', '.join(sorted(set(self.self_owning.values())))})",
            f"  flare-sink skips          : {len(self.skip_flare_sink)}",
            f"  supply-tie-in skips       : {len(self.skip_supply_tie_in)}",
            f"  relief valves attributed  : {len(self.protected_by)}",
        ])


def _select(cfg, query: str) -> List[dict]:
    from .fuseki_client import sparql_query
    res = sparql_query(cfg, query)
    return [{k: b[k]["value"] for k in b} for b in res["results"]["bindings"]]


def load_inferred(cfg, ids: bool = True) -> InferredSignals:
    """Reads graph:inferred (plus refdata for fluid codes) from the `gold`
    dataset. ids=True returns Silver ids (local names) instead of URIs."""
    g, rd = GRAPH_INFERRED, GRAPH_REFDATA
    conv = local_id if ids else (lambda u: u)
    sig = InferredSignals()
    for r in _select(cfg, f"""PREFIX pidsys: <{PIDSYS}>
        SELECT ?code ?cls WHERE {{ GRAPH <{g}> {{ ?f pidsys:selfOwningClass ?cls }}
                                  GRAPH <{rd}> {{ ?f pidsys:fluidCode ?code }} }}"""):
        sig.self_owning[r["code"]] = r["cls"]
    for pred, target in ((P_SKIP_FLARE, sig.skip_flare_sink), (P_SKIP_SUPPLY, sig.skip_supply_tie_in)):
        for r in _select(cfg, f"SELECT ?a ?b WHERE {{ GRAPH <{g}> {{ ?a <{pred}> ?b }} }}"):
            target.add((conv(r["a"]), conv(r["b"])))
    for r in _select(cfg, f"SELECT ?v ?p WHERE {{ GRAPH <{g}> {{ ?v <{P_PROTECTED_BY}> ?p }} }}"):
        sig.protected_by.setdefault(conv(r["v"]), set()).add(conv(r["p"]))
    sig.provenance = read_provenance(cfg)
    return sig


def read_provenance(cfg) -> Dict[str, str]:
    rows = _select(cfg, f"""PREFIX prov: <{PROV}> PREFIX pidsys: <{PIDSYS}>
        SELECT ?run ?ended ?status ?md ?rd ?rules WHERE {{ GRAPH <{GRAPH_INFERRED}> {{
          <{GRAPH_INFERRED}> prov:wasGeneratedBy ?run .
          ?run prov:endedAtTime ?ended ; pidsys:parityStatus ?status ;
               prov:used ?mdE, ?rdE, ?rulesE .
          ?mdE prov:specializationOf <{GRAPH_MASTERDATA}> ; pidsys:sha256 ?md .
          ?rdE prov:specializationOf <{GRAPH_REFDATA}> ; pidsys:sha256 ?rd .
          ?rulesE a prov:Plan ; pidsys:sha256 ?rules . }} }} LIMIT 1""")
    if not rows:
        return {}
    r = rows[0]
    return {"run": r["run"], "ended": r["ended"], "parityStatus": r["status"],
            "masterdata_sha256": r["md"], "refdata_sha256": r["rd"], "rules_sha256": r["rules"]}


def freshness(provenance: Dict[str, str], current: Dict[str, str]) -> Dict[str, bool]:
    """Pure: which of masterdata/refdata(/rules) still match the recorded run."""
    out = {name: provenance.get(f"{name}_sha256") == current.get(name)
           for name in ("masterdata", "refdata")}
    if "rules" in current:
        out["rules"] = provenance.get("rules_sha256") == current["rules"]
    return out


def inferred_is_current(cfg, ds, rules_file: Path = RULES_FILE) -> Dict[str, bool]:
    """Are the materialised inferences about the `ds` a run is about to walk,
    under the current rules? All True, or re-run §8b + §8c first."""
    from .jena_parity import sha256_text
    current = dict(ds_fingerprint(ds))
    current["rules"] = sha256_text(Path(rules_file).read_text(encoding="utf-8"))
    return freshness(read_provenance(cfg), current)
