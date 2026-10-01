"""Pure-logic tests for gold/inferred_graph.py: rule-head parsing, the PROV
block, the materialisation gate, and freshness. No rdflib, no Fuseki — the
live half runs in notebook §8c against gold-fuseki."""
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gold.inferred_graph import (
    GRAPH_INFERRED, MaterialiseRefused, build_provenance_ntriples, check_gate, freshness,
    local_id, rule_heads,
)
from gold.jena_parity import ParityReport, diff_classification, diff_pairs, diff_relief

PID = "https://pidsys.example/ns#"
RULES = (Path(__file__).resolve().parents[1] / "gold" / "jena_rules"
         / "classification.rules").read_text(encoding="utf-8")
FP = {"masterdata": "a" * 64, "refdata": "b" * 64, "rules": "c" * 64}
NT_LINE = re.compile(r'^(<[^<>\s]+>|_:\S+) <[^<>\s]+> (<[^<>\s]+>|"(?:[^"\\]|\\.)*"(\^\^<[^<>\s]+>)?) \.$')


def _report(ok=True, fingerprint=FP):
    a, b = f"{PID}component/A", f"{PID}component/B"
    return ParityReport(
        preflight={"input_counts": {}, "input_counts_match": True,
                   "python_inputs_missing_from_rules": [], "rule_predicates_without_data": [],
                   "relief_role_present": True, "roles_seen": ["relief"],
                   "oracle_leak": not ok, "input_fingerprint": fingerprint,
                   "input_matches_ds": True},
        classification=diff_classification({"SF": "flare"}, {"SF": "flare"}),
        flare_guard=diff_pairs({(a, b)}, {(a, b)}),
        supply_tie_in_guard=diff_pairs(set(), set()),
        relief=diff_relief({}, {}),
    )


class TestRuleHeads(unittest.TestCase):
    def test_real_rules_file(self):
        h = rule_heads(RULES)
        self.assertEqual(sorted(h[PID + "selfOwningClass"]),
                         ["flareCategory", "processCategory", "steamCondensateCategory"])
        self.assertEqual(h[PID + "skipFlareSink"], ["flareGuardSkip"])
        self.assertEqual(h[PID + "skipSupplyTieIn"], ["directionalConsumerGuardSkip"])
        self.assertEqual(sorted(h[PID + "skipAsConsumerSignal"]),
                         ["directionalConsumerGuardSkip", "flareGuardSkip"])
        self.assertEqual(h[PID + "protectedBy"], ["reliefAttribution"])
        self.assertEqual(len(h), 5)   # only derived predicates, never body inputs

    def test_body_predicates_are_not_heads(self):
        h = rule_heads(RULES)
        self.assertNotIn(PID + "flowsTo", h)
        self.assertNotIn("https://www.omg.org/spec/Commons/IndustrialData/connectedTo", h)


class TestProvenance(unittest.TestCase):
    def setUp(self):
        t0 = datetime(2026, 9, 23, 21, 0, 0, tzinfo=timezone.utc)
        self.text = build_provenance_ntriples(
            run_id="20260923T210000Z", started=t0, ended=t0, fingerprint=FP,
            rules_heads=rule_heads(RULES), rules_path="gold/jena_rules/classification.rules",
            derived_triple_count=2000, parity_summary={"flare_guard": 162, "relief": 39})
        self.lines = self.text.strip().splitlines()

    def test_every_line_is_valid_ntriples(self):
        bad = [ln for ln in self.lines if not NT_LINE.match(ln)]
        self.assertEqual(bad, [])

    def test_core_prov_shape(self):
        run = f"<{PID}run/rules/20260923T210000Z>"
        g = f"<{GRAPH_INFERRED}>"
        for expected in (
            f"{g} <http://www.w3.org/ns/prov#wasGeneratedBy> {run} .",
            f"{g} <http://www.w3.org/ns/prov#wasDerivedFrom> <{PID}graph/masterdata> .",
            f"{run} <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/prov#Activity> .",
            f'{run} <{PID}parityStatus> "PASS" .',
            f'{run} <{PID}parityAgree_flare_guard> "162"^^<http://www.w3.org/2001/XMLSchema#integer> .',
            f"<{PID}skipFlareSink> <{PID}derivedByRule> <{PID}rule/flareGuardSkip> .",
        ):
            self.assertIn(expected, self.lines)

    def test_inputs_and_rules_identified_by_hash(self):
        self.assertIn(f'<{PID}input/masterdata/{"a" * 16}> <{PID}sha256> "{"a" * 64}" .', self.lines)
        self.assertIn(f'<{PID}rules/{"c" * 16}> <{PID}sha256> "{"c" * 64}" .', self.lines)

    def test_no_oracle_predicate(self):
        self.assertNotIn("srcTurnoverSystem", self.text)
        self.assertNotIn("srcSubsystem", self.text)


class TestGate(unittest.TestCase):
    def test_passing_report_on_same_input_opens(self):
        check_gate(_report(), FP)   # no raise

    def test_failed_parity_refuses(self):
        with self.assertRaises(MaterialiseRefused):
            check_gate(_report(ok=False), FP)

    def test_stale_report_refuses_and_names_what_changed(self):
        loaded = dict(FP, masterdata="d" * 64)
        with self.assertRaisesRegex(MaterialiseRefused, "masterdata"):
            check_gate(_report(), loaded)

    def test_rules_change_after_parity_refuses(self):
        with self.assertRaisesRegex(MaterialiseRefused, "rules"):
            check_gate(_report(), dict(FP, rules="e" * 64))

    def test_explicit_override(self):
        check_gate(_report(ok=False), {}, allow_unverified=True)


class TestFreshness(unittest.TestCase):
    PROV = {"masterdata_sha256": "a" * 64, "refdata_sha256": "b" * 64, "rules_sha256": "c" * 64}

    def test_current(self):
        self.assertEqual(freshness(self.PROV, FP),
                         {"masterdata": True, "refdata": True, "rules": True})

    def test_masterdata_reprojected(self):
        self.assertFalse(freshness(self.PROV, dict(FP, masterdata="x" * 64))["masterdata"])

    def test_nothing_materialised(self):
        self.assertEqual(freshness({}, FP), {"masterdata": False, "refdata": False, "rules": False})


class TestLocalId(unittest.TestCase):
    def test_component_uri(self):
        self.assertEqual(local_id(f"{PID}component/SP001EE775"), "SP001EE775")


if __name__ == "__main__":
    unittest.main()
