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


if __name__ == "__main__":
    unittest.main()
