import unittest
import urllib.parse

from gold.fuseki_client import (
    FusekiConfig,
    build_graph_store_put_request,
    build_sparql_query_request,
    build_sparql_update_request,
)


class TestFusekiRequestConstruction(unittest.TestCase):
    """No live Fuseki is reachable in this sandbox; these tests check only
    that the HTTP requests are built per Fuseki's documented protocols
    (Graph Store Protocol for graph PUT, SPARQL 1.1 Protocol for
    query/update) — see fuseki_client.py's module docstring."""

    def setUp(self):
        self.cfg = FusekiConfig(base_url="http://localhost:3030", dataset="pidsys")

    def test_graph_store_put_targets_the_named_graph(self):
        url, method, headers, data = build_graph_store_put_request(
            self.cfg, "https://pidsys.example/ns#graph/masterdata", "<a> <b> <c> ."
        )
        self.assertEqual(method, "PUT")
        self.assertIn("/pidsys/data?graph=", url)
        decoded = urllib.parse.unquote(url)
        self.assertIn("https://pidsys.example/ns#graph/masterdata", decoded)
        self.assertEqual(headers["Content-Type"], "text/turtle; charset=utf-8")
        self.assertEqual(data, b"<a> <b> <c> .")

    def test_sparql_query_request_shape(self):
        url, method, headers, data = build_sparql_query_request(self.cfg, "SELECT * WHERE { ?s ?p ?o }")
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/pidsys/sparql"))
        self.assertEqual(headers["Accept"], "application/sparql-results+json")
        self.assertIn(b"SELECT", data)

    def test_sparql_update_request_shape(self):
        url, method, headers, data = build_sparql_update_request(self.cfg, "CLEAR GRAPH <urn:x>")
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/pidsys/update"))
        self.assertEqual(headers["Content-Type"], "application/sparql-update; charset=utf-8")


class TestFusekiBasicAuth(unittest.TestCase):
    """A real Fuseki instance rejects an unauthenticated Graph Store
    Protocol PUT with HTTP 401 (confirmed against the user's own running
    instance) — see fuseki_client.py's module docstring for why
    FusekiConfig.user/.password exist and are sent as HTTP Basic Auth on
    every request this module builds."""

    def test_no_credentials_means_no_authorization_header(self):
        # Backward-compatible default: a caller that doesn't set
        # user/password (e.g. the pre-2026-09-10 call sites) gets exactly
        # the same headers as before this was added.
        cfg = FusekiConfig(base_url="http://localhost:3030", dataset="pidsys")
        self.assertEqual(cfg.auth_header(), {})
        _, _, headers, _ = build_graph_store_put_request(cfg, "urn:g", "<a> <b> <c> .")
        self.assertNotIn("Authorization", headers)

    def test_credentials_produce_correct_basic_auth_token(self):
        cfg = FusekiConfig(base_url="http://localhost:3030", dataset="gold", user="admin", password="admin")
        # base64("admin:admin") == "YWRtaW46YWRtaW4=" -- a fixed, checkable value.
        self.assertEqual(cfg.auth_header(), {"Authorization": "Basic YWRtaW46YWRtaW4="})

    def test_authorization_header_present_on_all_three_request_builders(self):
        cfg = FusekiConfig(base_url="http://localhost:3030", dataset="gold", user="admin", password="admin")
        _, _, put_headers, _ = build_graph_store_put_request(cfg, "urn:g", "<a> <b> <c> .")
        _, _, query_headers, _ = build_sparql_query_request(cfg, "SELECT * WHERE { ?s ?p ?o }")
        _, _, update_headers, _ = build_sparql_update_request(cfg, "CLEAR GRAPH <urn:x>")
        for headers in (put_headers, query_headers, update_headers):
            self.assertEqual(headers["Authorization"], "Basic YWRtaW46YWRtaW4=")

    def test_user_without_password_still_builds_a_header(self):
        # Edge case: an empty/unset password is treated as "" rather than
        # raising, so a misconfiguration surfaces as a 401 from Fuseki
        # (the failure mode a user can act on), not a crash here.
        cfg = FusekiConfig(base_url="http://localhost:3030", dataset="gold", user="admin")
        self.assertEqual(cfg.auth_header(), {"Authorization": "Basic YWRtaW46"})


if __name__ == "__main__":
    unittest.main()
