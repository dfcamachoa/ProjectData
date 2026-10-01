"""Pure-logic tests for gold/jena_parity.py — the diff cores and the report's
pass/fail. No rdflib, no Fuseki: the live half is exercised by
`python -m gold.jena_parity --fixture` against a running gold-fuseki."""
import unittest

from pathlib import Path

from gold.jena_parity import (ParityReport, diff_classification, diff_pairs, diff_relief,
                              rule_body_predicates)

C1, F1, N1, RV1 = (f"https://pidsys.example/ns#component/{x}" for x in ("C1", "F1", "N1", "RV1"))


class TestDiffPairs(unittest.TestCase):
    def test_identical_sets_agree(self):
        d = diff_pairs({(C1, F1)}, {(C1, F1)})
        self.assertEqual((d["agree"], d["python_only"], d["jena_only"]), (1, [], []))

    def test_each_side_reported(self):
        d = diff_pairs({(C1, F1)}, {(C1, N1)})
        self.assertEqual(d["python_only"], [(C1, F1)])
        self.assertEqual(d["jena_only"], [(C1, N1)])


class TestDiffClassification(unittest.TestCase):
    def test_jena_absence_means_utility(self):
        d = diff_classification({"N": "utility", "SF": "flare"}, {"N": None, "SF": "flare"})
        self.assertEqual((d["agree"], d["mismatches"]), (2, []))

    def test_substring_steam_mismatch_is_caught(self):
        # the old full-match regex('Steam|Condensate') would leave "LP Steam" unclassified
        d = diff_classification({"LS": "steam_condensate"}, {"LS": None})
        self.assertEqual(d["mismatches"], [{"fluid_code": "LS", "python": "steam_condensate",
                                            "jena": "utility"}])

    def test_fluid_missing_on_one_side(self):
        d = diff_classification({"PG": "process"}, {})
        self.assertEqual(d["mismatches"][0]["jena"], "<absent>")


class TestDiffRelief(unittest.TestCase):
    def test_single_answer_agrees(self):
        self.assertEqual(diff_relief({RV1: C1}, {RV1: {C1}})["agree"], 1)

    def test_both_none_agrees(self):
        self.assertEqual(diff_relief({RV1: None}, {})["agree"], 1)

    def test_python_first_of_many_is_multi_not_mismatch(self):
        d = diff_relief({RV1: C1}, {RV1: {C1, N1}})
        self.assertEqual((d["agree"], len(d["jena_multi"]), d["mismatch"]), (0, 1, []))

    def test_jena_silent_is_mismatch(self):
        # e.g. the old regex(?role,'relief') on a URI never firing
        d = diff_relief({RV1: C1}, {})
        self.assertEqual(d["mismatch"], [{"valve": RV1, "python": C1, "jena": []}])

    def test_valve_only_jena_attributes(self):
        self.assertEqual(diff_relief({}, {RV1: {C1}})["jena_only_valves"], [RV1])


PID = "https://pidsys.example/ns#"
IDO = "https://www.omg.org/spec/Commons/IndustrialData/"


class TestRuleBodyPredicates(unittest.TestCase):
    def test_real_rules_file(self):
        text = (Path(__file__).resolve().parents[1] / "gold" / "jena_rules"
                / "classification.rules").read_text(encoding="utf-8")
        preds = rule_body_predicates(text)
        self.assertIn(IDO + "connectedTo", preds)
        self.assertNotIn(PID + "isConnectedTo", preds)      # the 2026-09-23 drift
        for local in ("flowsTo", "partOf", "fluidCode", "category", "subcategory",
                      "boundaryMember"):
            self.assertIn(PID + local, preds)
        # derived by the rules themselves -> not an input
        self.assertNotIn(PID + "selfOwningClass", preds)
        self.assertNotIn(PID + "skipAsConsumerSignal", preds)

    def test_builtins_and_comments_ignored(self):
        text = """@prefix p: <http://x/> .
        # (?a p:commented ?b)
        [r1: (?a p:in ?b) regex(?b, '.*(a|b).*') noValue(?b p:neg ?c) -> (?a p:out ?b)]"""
        self.assertEqual(rule_body_predicates(text), {"http://x/in", "http://x/neg"})


class TestReportOk(unittest.TestCase):
    def _clean(self):
        return ParityReport(
            preflight={"input_counts": {"flowsTo": (3, 3)}, "input_counts_match": True,
                       "python_inputs_missing_from_rules": [], "rule_predicates_without_data": [],
                       "relief_role_present": True, "roles_seen": ["relief"], "oracle_leak": False},
            classification=diff_classification({"SF": "flare"}, {"SF": "flare"}),
            flare_guard=diff_pairs({(C1, F1)}, {(C1, F1)}),
            supply_tie_in_guard=diff_pairs({(C1, N1)}, {(C1, N1)}),
            relief=diff_relief({RV1: C1}, {RV1: {C1}}),
        )

    def test_clean_report_passes(self):
        r = self._clean()
        self.assertTrue(r.ok)
        self.assertIn("PARITY PASS", r.summary())

    def test_multi_candidate_relief_does_not_fail(self):
        r = self._clean()
        r.relief = diff_relief({RV1: C1}, {RV1: {C1, N1}})
        self.assertTrue(r.ok)

    def test_each_failure_mode_fails(self):
        for mutate in (
            lambda r: r.preflight.update(input_counts_match=False),
            lambda r: r.preflight.update(python_inputs_missing_from_rules=["ido:connectedTo"]),
            lambda r: r.preflight.update(rule_predicates_without_data=["pidsys:isConnectedTo"]),
            lambda r: r.preflight.update(relief_role_present=False),
            lambda r: r.preflight.update(oracle_leak=True),
            lambda r: setattr(r, "classification", diff_classification({"SF": "flare"}, {"SF": None})),
            lambda r: setattr(r, "flare_guard", diff_pairs({(C1, F1)}, set())),
            lambda r: setattr(r, "supply_tie_in_guard", diff_pairs(set(), {(C1, N1)})),
            lambda r: setattr(r, "relief", diff_relief({RV1: C1}, {})),
        ):
            r = self._clean()
            mutate(r)
            self.assertFalse(r.ok)
            self.assertIn("PARITY FAIL", r.summary())


if __name__ == "__main__":
    unittest.main()
