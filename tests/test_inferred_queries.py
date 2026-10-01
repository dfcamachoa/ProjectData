"""gold/inferred_queries.py — every query parses (rdflib), and every query
gives the hand-computed answer on the walk.py fixture scenario:

    N1 --> C1 --> F1        nitrogen ties into process; process discharges to flare
           |
           +--> RV1         relief valve protecting the process line

graph:inferred is built here from the Python reference rules (in parity with
Jena, gold_layer_spec §5.6) plus the real PROV block, so the queries run
through rdflib exactly as they will against Fuseki.
"""
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rdflib import Graph, Literal
from rdflib.plugins.sparql import prepareQuery

from gold import rules_reference as rr
from gold import vocab as v
from gold.inferred_graph import GRAPH_INFERRED, build_provenance_ntriples, rule_heads
from gold.inferred_queries import QUERIES, RULE_CARD, run_local, verdict
from gold.rdf_model import URIRef
from tests.fixtures import build_fixture_dataset

RULES = (Path(__file__).resolve().parents[1] / "gold" / "jena_rules"
         / "classification.rules").read_text(encoding="utf-8")
P = lambda local: URIRef(v.PIDSYS + local)  # noqa: E731


def build_with_inferred():
    ds = build_fixture_dataset()
    md, rd = v.GRAPH_MASTERDATA, v.GRAPH_REFDATA
    catalogue = rr.load_fluid_catalogue(ds)
    for q in ds.triples(p=URIRef(v.P_FLUID_CODE), graph=rd):
        cls = rr.classify_fluid_category(str(q.o), catalogue)
        if cls != "utility":
            ds.add(q.s, P("selfOwningClass"), Literal(cls), GRAPH_INFERRED)
    for q in list(ds.triples(p=URIRef(v.P_IS_CONNECTED_TO), graph=md)):
        a, b = q.s, q.o
        if rr.flare_guard(ds, a, b, catalogue):
            ds.add(a, P("skipFlareSink"), b, GRAPH_INFERRED)
            ds.add(a, P("skipAsConsumerSignal"), b, GRAPH_INFERRED)
        if rr.directional_consumer_guard(ds, a, b):
            ds.add(a, P("skipSupplyTieIn"), b, GRAPH_INFERRED)
            ds.add(a, P("skipAsConsumerSignal"), b, GRAPH_INFERRED)
    relief_classes = {q.o for q in ds.triples(s=P("boundary_role/relief"),
                                               p=URIRef(v.P_BOUNDARY_MEMBER), graph=rd)}
    for cls in relief_classes:
        for q in list(ds.triples(p=URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
                                 o=cls, graph=md)):
            protected = rr.relief_attribution(ds, q.s)
            if protected is not None:
                ds.add(q.s, P("protectedBy"), protected, GRAPH_INFERRED)
    t0 = datetime(2026, 9, 24, 1, 0, 26, tzinfo=timezone.utc)
    prov = build_provenance_ntriples(
        run_id="20260924T010026Z", started=t0, ended=t0,
        fingerprint={"masterdata": "a" * 64, "refdata": "b" * 64, "rules": "c" * 64},
        rules_heads=rule_heads(RULES), rules_path="gold/jena_rules/classification.rules",
        derived_triple_count=12, parity_summary={"flare_guard": 1, "relief": 1})
    g = Graph()
    g.parse(data=prov, format="nt")
    for s, p, o in g:
        ds.add(s, p, o, GRAPH_INFERRED)
    return ds


def rows(ds, name):
    return [{k: (str(val) if val is not None and not isinstance(val, int) else val)
             for k, val in r.items()} for r in run_local(ds, name)]


def table(ds, name, *cols):
    return sorted(tuple(r.get(c) for c in cols) for r in rows(ds, name))


class TestQueriesParse(unittest.TestCase):
    def test_every_query_parses(self):
        for name, q in QUERIES.items():
            with self.subTest(name=name):
                prepareQuery(q["query"])
        prepareQuery(RULE_CARD.replace("%TAG%", "PSV-01"))


class TestOnFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = build_with_inferred()

    def test_I1_overview(self):
        self.assertEqual(dict(table(self.ds, "I1_overview", "predicate", "facts")), {
            "selfOwningClass": 3, "skipAsConsumerSignal": 4, "skipFlareSink": 1,
            "skipSupplyTieIn": 3, "protectedBy": 1})

    def test_I2_I3_I4_provenance(self):
        run = rows(self.ds, "I2_run")
        self.assertEqual(len(run), 1)
        self.assertEqual(run[0]["run"], "run/rules/20260924T010026Z")
        self.assertEqual(run[0]["parity"], "PASS")
        self.assertEqual(verdict("I3_inputs", rows(self.ds, "I3_inputs")), "PASS")
        self.assertEqual(dict(table(self.ds, "I4_parity", "section", "agree")),
                         {"flare_guard": 1, "relief": 1})

    def test_I5_rules(self):
        self.assertEqual(table(self.ds, "I5_rules", "rule", "predicate", "facts"), sorted([
            ("directionalConsumerGuardSkip", "skipSupplyTieIn", 3),
            ("flareGuardSkip", "skipFlareSink", 1),
            ("reliefAttribution", "protectedBy", 1),
            ("flareCategory", "selfOwningClass", 1),
            ("steamCondensateCategory", "selfOwningClass", 1),
            ("processCategory", "selfOwningClass", 1)]))

    def test_F1_fluid_classes(self):
        self.assertEqual(table(self.ds, "F1_fluid_classes", "fluid", "class", "segments"), sorted([
            ("LS", "steam_condensate", 0), ("N", "utility", 1),
            ("PG", "process", 1), ("SF", "flare", 1)]))

    def test_F2_class_summary(self):
        self.assertEqual(table(self.ds, "F2_class_summary", "class", "fluids", "segments"), sorted([
            ("flare", 1, 1), ("process", 1, 1), ("steam_condensate", 1, 0), ("utility", 1, 1)]))
        own = {r["class"]: r["ownSystems"] for r in rows(self.ds, "F2_class_summary")}
        self.assertTrue(own["flare"].startswith("yes") and own["steam_condensate"].startswith("yes"))
        self.assertEqual(own["process"], "no")

    def test_G1_G2_flare(self):
        self.assertEqual(table(self.ds, "G1_flare_by_fluid", "fragmentFluid", "flareFluid", "skips"),
                         [("PG", "SF", 1)])
        detail = rows(self.ds, "G2_flare_detail")
        self.assertEqual(len(detail), 1)
        self.assertEqual((detail[0]["fragment"], detail[0]["fragmentClass"],
                          detail[0]["sink"], detail[0]["sinkClass"]),
                         ("component/C1", "PipeReducer", "component/F1", "PipeFlangeSpacer"))

    def test_G3_G4_supply(self):
        split = dict(table(self.ds, "G3_supply_split", "kind", "skips"))
        self.assertEqual(split, {"cross-fluid tie-in": 2, "same fluid (upstream along the line)": 1})
        self.assertEqual(table(self.ds, "G4_supply_by_fluid", "supplyFluid", "supplyCategory",
                               "intoFluid", "intoCategory", "tieIns"),
                         sorted([("N", "Utility", "PG", "Process", 1),
                                 ("PG", "Process", "SF", "Flare", 1)]))

    def test_R1_R2_relief(self):
        r = rows(self.ds, "R1_relief")
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["valveTag"], r[0]["valveClass"], r[0]["protected"],
                          r[0]["protectedClass"], r[0]["protectedLine"], r[0]["protectedFluid"],
                          r[0].get("dischargeFluid"), r[0].get("dischargeLine")),
                         ("PSV-01", "SafetyValveOrFitting", "component/C1",
                          "PipeReducer", "PG-000001", "PG", None, None))
        self.assertEqual(table(self.ds, "R2_relief_summary", "protectedFluid", "dischargeFluid",
                               "valves"), [("PG", "(unknown)", 1)])

    def test_every_check_passes(self):
        for name, q in QUERIES.items():
            if q["group"] == "checks":
                with self.subTest(name=name):
                    self.assertEqual(verdict(name, rows(self.ds, name)), "PASS")

    def test_checks_catch_violations(self):
        ds = build_with_inferred()
        c1, n1 = P("component/C1"), P("component/N1")
        ds.add(c1, P("skipAsConsumerSignal"), P("component/RV1"), GRAPH_INFERRED)   # no reason
        ds.add(c1, P("skipFlareSink"), n1, GRAPH_INFERRED)                         # N is not flare
        ds.add(c1, P("skipSupplyTieIn"), n1, GRAPH_INFERRED)
        ds.add(P("fluid/N"), P("selfOwningClass"), Literal("flare"), GRAPH_INFERRED)  # category Utility
        ds.add(c1, P("protectedBy"), n1, GRAPH_INFERRED)                              # C1 not relief
        ds.add(P("boundary_role/isolation"), URIRef(v.P_BOUNDARY_MEMBER), P("CheckValve"),
               v.GRAPH_REFDATA)
        for name in ("C1_union_explained", "C3_relief_class", "C4_flare_class", "C8_checkvalve_member"):
            with self.subTest(name=name):
                self.assertEqual(verdict(name, rows(ds, name)), "FAIL")
        # C2 and C5: C1→N1 is now both a flare sink and a tie-in, and N1 sits on
        # SG-N1 (fluid N) — which the injected triple above just classed flare,
        # so C5 still passes; C2 must fail
        self.assertEqual(verdict("C2_guards_disjoint", rows(ds, "C2_guards_disjoint")), "FAIL")

    def test_rule_card(self):
        card = run_local(self.ds, None, RULE_CARD.replace("%TAG%", "PSV-01"))
        got = sorted((r["direction"], str(r["fact"]), str(r["otherClass"]), str(r["onLine"]),
                      str(r["fluid"]), str(r["rule"]), str(r["other"])) for r in card)
        self.assertEqual(got, [
            ("this →", "protects", "PipeReducer", "PG-000001", "PG",
             "reliefAttribution", "component/C1"),
            ("this →", "skipSupplyTieIn", "PipeReducer", "PG-000001", "PG",
             "directionalConsumerGuardSkip", "component/C1")])

    def test_rule_card_from_the_other_side(self):
        # N1's card (nitrogen gate valve): the process fragment C1 sees N1 as a supply tying in
        card = run_local(self.ds, None, RULE_CARD.replace("%TAG%", "N-GV-01"))
        got = sorted((r["direction"], str(r["fact"]), str(r["other"])) for r in card)
        self.assertEqual(got, [("→ this", "skipSupplyTieIn", "component/C1")])

if __name__ == "__main__":
    unittest.main()
