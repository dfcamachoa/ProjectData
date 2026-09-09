"""Unit tests for gold/fuseki_bootstrap.py's pure control flow.

No live Fuseki is reachable in this sandbox (same limitation
tests/test_fuseki_client.py already documents) — these tests monkeypatch
`fuseki_client.push_named_graph` so `push_dataset`'s own logic (which
graphs get pushed vs. skipped, what the report shape is) is exercised
without a network call. They do NOT prove the real PUT succeeds against a
real Fuseki instance; only `docker compose up -d` in fuseki/ plus
`python -m gold.fuseki_bootstrap --fixture` run by hand can prove that.
"""
import unittest

from gold import fuseki_bootstrap as fb
from gold import vocab as v
from gold.fuseki_client import FusekiConfig
from tests.fixtures import build_fixture_dataset


class TestPushDataset(unittest.TestCase):
    def setUp(self):
        self.ds = build_fixture_dataset()
        self.cfg = FusekiConfig(base_url="http://localhost:3030", dataset="gold")
        self._orig_push = fb.push_named_graph
        self.calls = []

        def fake_push(cfg, graph_uri, turtle_text):
            self.calls.append((cfg, graph_uri, turtle_text))
            return 201

        fb.push_named_graph = fake_push

    def tearDown(self):
        fb.push_named_graph = self._orig_push

    def test_nonempty_graphs_are_pushed_and_reported(self):
        report = fb.push_dataset(self.ds, self.cfg)
        # tests/fixtures.py's scenario has masterdata + refdata triples,
        # nothing in oracle or results (no equipment/oracle fields, no
        # systemization output written yet — see fuseki_bootstrap.py's docstring).
        self.assertEqual(set(report["pushed"]), {v.GRAPH_MASTERDATA, v.GRAPH_REFDATA})
        self.assertEqual(set(report["skipped_empty"]), {v.GRAPH_ORACLE, v.GRAPH_RESULTS})
        self.assertGreater(report["pushed"][v.GRAPH_MASTERDATA], 0)
        self.assertGreater(report["pushed"][v.GRAPH_REFDATA], 0)

    def test_only_nonempty_graphs_trigger_an_http_call(self):
        fb.push_dataset(self.ds, self.cfg)
        pushed_graphs = {call[1] for call in self.calls}
        self.assertEqual(pushed_graphs, {v.GRAPH_MASTERDATA, v.GRAPH_REFDATA})
        self.assertEqual(len(self.calls), 2)

    def test_turtle_text_passed_to_push_is_nonempty(self):
        fb.push_dataset(self.ds, self.cfg)
        for _cfg, _graph, turtle_text in self.calls:
            self.assertIsInstance(turtle_text, str)
            self.assertGreater(len(turtle_text), 0)

    def test_restricting_to_one_graph_pushes_only_that_one(self):
        report = fb.push_dataset(self.ds, self.cfg, graphs=(v.GRAPH_MASTERDATA,))
        self.assertEqual(set(report["pushed"]), {v.GRAPH_MASTERDATA})
        self.assertEqual(report["skipped_empty"], [])
        self.assertEqual(len(self.calls), 1)

    def test_all_graphs_empty_pushes_nothing(self):
        from gold.rdf_model import Dataset

        report = fb.push_dataset(Dataset(), self.cfg)
        self.assertEqual(report["pushed"], {})
        self.assertEqual(set(report["skipped_empty"]), set(fb.NAMED_GRAPHS))
        self.assertEqual(self.calls, [])


class TestMainCli(unittest.TestCase):
    def setUp(self):
        self._orig_push = fb.push_named_graph
        fb.push_named_graph = lambda cfg, graph_uri, turtle_text: 201

    def tearDown(self):
        fb.push_named_graph = self._orig_push

    def test_no_fixture_flag_pushes_nothing_and_returns_nonzero(self):
        self.assertEqual(fb.main([]), 1)

    def test_fixture_flag_pushes_and_returns_zero(self):
        self.assertEqual(fb.main(["--fixture"]), 0)

    def test_fixture_flag_honours_base_url_and_dataset_overrides(self):
        seen_cfgs = []
        orig = fb.push_dataset

        def spy(ds, cfg, graphs=fb.NAMED_GRAPHS):
            seen_cfgs.append(cfg)
            return orig(ds, cfg, graphs)

        fb.push_dataset = spy
        try:
            fb.main(["--fixture", "--base-url", "http://example:3030", "--dataset", "myds"])
        finally:
            fb.push_dataset = orig
        self.assertEqual(len(seen_cfgs), 1)
        self.assertEqual(seen_cfgs[0].base_url, "http://example:3030")
        self.assertEqual(seen_cfgs[0].dataset, "myds")

    def test_fixture_flag_honours_user_and_password_overrides(self):
        seen_cfgs = []
        orig = fb.push_dataset

        def spy(ds, cfg, graphs=fb.NAMED_GRAPHS):
            seen_cfgs.append(cfg)
            return orig(ds, cfg, graphs)

        fb.push_dataset = spy
        try:
            fb.main(["--fixture", "--user", "someone", "--password", "secret"])
        finally:
            fb.push_dataset = orig
        self.assertEqual(len(seen_cfgs), 1)
        self.assertEqual(seen_cfgs[0].user, "someone")
        self.assertEqual(seen_cfgs[0].password, "secret")

    def test_fixture_flag_defaults_to_admin_admin_credentials(self):
        # Matches fuseki/docker-compose.yml's ADMIN_PASSWORD -- the 401
        # this module's docstring documents came from a caller (this CLI's
        # first version) that hadn't sent any credentials at all.
        seen_cfgs = []
        orig = fb.push_dataset

        def spy(ds, cfg, graphs=fb.NAMED_GRAPHS):
            seen_cfgs.append(cfg)
            return orig(ds, cfg, graphs)

        fb.push_dataset = spy
        try:
            fb.main(["--fixture"])
        finally:
            fb.push_dataset = orig
        self.assertEqual(seen_cfgs[0].user, fb.DEFAULT_USER)
        self.assertEqual(seen_cfgs[0].password, fb.DEFAULT_PASSWORD)


if __name__ == "__main__":
    unittest.main()
