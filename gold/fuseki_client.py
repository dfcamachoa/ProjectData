"""Stdlib-only Fuseki client — no `requests`, no `SPARQLWrapper`, no
`rdflib` needed for this module specifically (it only ever sends/receives
plain text and JSON over HTTP), so it runs unmodified whether or not the
rest of `gold/` has `rdflib`/`owlrl`/`pyspark` installed — in this sandbox
(PyPI unreachable) and in a real deployment alike. Implements just the two
protocols the Gold job needs:
the SPARQL 1.1 Graph Store HTTP Protocol (to load one named graph's Turtle)
and the SPARQL 1.1 Protocol (to run a query or update).

**HTTP Basic Auth (added 2026-09-10, real-deployment finding):** a real
Fuseki instance rejects an unauthenticated Graph Store Protocol PUT with a
plain HTTP 401 — confirmed against the user's own `fuseki/docker-compose.yml`
instance, not a hypothetical. This isn't unique to this deployment: the
sibling `ido-prototype`'s own `pipeline/store.py` already sends
`HTTPBasicAuth(config.FUSEKI_USER, config.FUSEKI_PW)` (admin/admin, matching
its `docker-compose.yml`'s `ADMIN_PASSWORD`) on every request for the same
reason, and this module simply hadn't needed it yet because it had never
been run against a real, running Fuseki before. `FusekiConfig.user`/
`.password`, when set, are sent as an HTTP Basic `Authorization` header
(stdlib `base64`, not `requests.auth.HTTPBasicAuth`, to keep this module
dependency-free) on every request this module builds — PUT, SPARQL query,
and SPARQL update alike, since a real instance can (and, in the default
`stain/jena-fuseki` config this project's compose file uses, does) also
require it for reads and updates, not just the Graph Store PUT that
surfaced it. `fuseki_bootstrap.py` supplies the actual admin/admin
local-dev default that matches `fuseki/docker-compose.yml`'s
`ADMIN_PASSWORD`; this module stays credential-agnostic (an unset
`user`/`password` just means no `Authorization` header is sent, same
behaviour as before this was added).

Request construction (`build_graph_store_put_request` etc.) is unit-tested
in this sandbox (`tests/test_fuseki_client.py`); a live round-trip against
a running Fuseki, including that the Basic Auth header actually clears the
401 above, can only be confirmed in an environment where Fuseki is
reachable — this sandbox still isn't one.
"""
from __future__ import annotations

import base64
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
    user: Optional[str] = None      # HTTP Basic Auth username, e.g. "admin"
    password: Optional[str] = None  # HTTP Basic Auth password

    def auth_header(self) -> dict:
        """`{"Authorization": "Basic ..."}` when `user` is set, `{}`
        otherwise -- merged into every request this module builds. A real
        Fuseki instance (at minimum the `stain/jena-fuseki` image this
        project's own `fuseki/docker-compose.yml` and the sibling
        `ido-prototype`'s use) requires this for dataset writes even when
        `user`/`password` are left unset by a caller that hasn't hit the
        401 yet -- see this module's docstring."""
        if not self.user:
            return {}
        raw = f"{self.user}:{self.password or ''}".encode("utf-8")
        token = base64.b64encode(raw).decode("ascii")
        return {"Authorization": f"Basic {token}"}


def build_graph_store_put_request(cfg: FusekiConfig, graph_uri: str, turtle_text: str) -> tuple:
    """Returns (url, method, headers, data) for a Graph Store Protocol PUT
    that REPLACES the named graph's content — the right verb for a Gold
    rebuild of one graph (masterdata/refdata/oracle/results are each
    rebuilt wholesale per run, not incrementally patched)."""
    url = f"{cfg.base_url}/{cfg.dataset}/data?" + urllib.parse.urlencode({"graph": graph_uri})
    headers = {"Content-Type": "text/turtle; charset=utf-8", **cfg.auth_header()}
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
        **cfg.auth_header(),
    }
    return url, "POST", headers, query.encode("utf-8")


def sparql_query(cfg: FusekiConfig, query: str) -> dict:
    url, method, headers, data = build_sparql_query_request(cfg, query)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_sparql_update_request(cfg: FusekiConfig, update: str) -> tuple:
    url = f"{cfg.base_url}/{cfg.dataset}/update"
    headers = {"Content-Type": "application/sparql-update; charset=utf-8", **cfg.auth_header()}
    return url, "POST", headers, update.encode("utf-8")


def sparql_update(cfg: FusekiConfig, update: str) -> int:
    url, method, headers, data = build_sparql_update_request(cfg, update)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
        return resp.status
