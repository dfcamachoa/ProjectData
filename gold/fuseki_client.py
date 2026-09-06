"""Stdlib-only Fuseki client — no `requests`, no `SPARQLWrapper`, no
`rdflib`, so it runs unmodified in this sandbox (PyPI unreachable) and in a
real deployment alike. Implements just the two protocols the Gold job needs:
the SPARQL 1.1 Graph Store HTTP Protocol (to load one named graph's Turtle)
and the SPARQL 1.1 Protocol (to run a query or update).

Not exercised by the test suite in this sandbox — there is no Fuseki
instance reachable here. Validated against Fuseki's documented HTTP
surface; `tests/test_fuseki_client.py` covers only request construction
(method, path, headers, body), not a live round-trip.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional


@dataclass
class FusekiConfig:
    base_url: str          # e.g. "http://localhost:3030"
    dataset: str            # e.g. "pidsys"
    timeout_s: float = 30.0


def build_graph_store_put_request(cfg: FusekiConfig, graph_uri: str, turtle_text: str) -> tuple:
    """Returns (url, method, headers, data) for a Graph Store Protocol PUT
    that REPLACES the named graph's content — the right verb for a Gold
    rebuild of one graph (masterdata/refdata/oracle/results are each
    rebuilt wholesale per run, not incrementally patched)."""
    url = f"{cfg.base_url}/{cfg.dataset}/data?" + urllib.parse.urlencode({"graph": graph_uri})
    headers = {"Content-Type": "text/turtle; charset=utf-8"}
    return url, "PUT", headers, turtle_text.encode("utf-8")


def push_named_graph(cfg: FusekiConfig, graph_uri: str, turtle_text: str) -> int:
    url, method, headers, data = build_graph_store_put_request(cfg, graph_uri, turtle_text)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
        return resp.status


def build_sparql_query_request(cfg: FusekiConfig, query: str) -> tuple:
    url = f"{cfg.base_url}/{cfg.dataset}/sparql"
    headers = {
        "Content-Type": "application/sparql-query; charset=utf-8",
        "Accept": "application/sparql-results+json",
    }
    return url, "POST", headers, query.encode("utf-8")


def sparql_query(cfg: FusekiConfig, query: str) -> dict:
    url, method, headers, data = build_sparql_query_request(cfg, query)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_sparql_update_request(cfg: FusekiConfig, update: str) -> tuple:
    url = f"{cfg.base_url}/{cfg.dataset}/update"
    headers = {"Content-Type": "application/sparql-update; charset=utf-8"}
    return url, "POST", headers, update.encode("utf-8")


def sparql_update(cfg: FusekiConfig, update: str) -> int:
    url, method, headers, data = build_sparql_update_request(cfg, update)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
        return resp.status
