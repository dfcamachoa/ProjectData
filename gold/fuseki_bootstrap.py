"""Push the Gold RDF/IDO projection into a real, locally-running Fuseki.

Mirrors the sibling `ido-prototype`'s Fuseki setup one-for-one, adapted to
this package's own already-built pieces rather than duplicating them:

  - `fuseki/docker-compose.yml` (this package's own copy, alongside this
    module) is the same `stain/jena-fuseki` image + `FUSEKI_DATASET_1`
    auto-create-on-startup mechanism as the sibling prototype's
    `fuseki/docker-compose.yml` (CONTINUATION_BRIEF.md §2, §9), just named
    for this dataset ("gold") instead of theirs ("ido").
  - The HTTP layer is `gold/fuseki_client.py`, already built stdlib-only
    (no `requests`/`SPARQLWrapper` needed here, unlike the sibling
    prototype's `pipeline/store.py`, which uses `requests` + `rdflib`) —
    see that module's own docstring for why it didn't need to change for
    this. This module is the orchestration `pipeline/bootstrap.py` +
    `pipeline/store.py::load_named_graph` play there: reading a `Dataset`
    (or, for a smoke test, the same `tests/fixtures.py` scenario the unit
    suite already validates against) and pushing each of Gold's named
    graphs into it.

Design choice carried over directly from the sibling prototype: every push
below is a Graph Store Protocol PUT — a **drop-and-replace** of one named
graph's entire content, not an incremental patch. That's what makes
`push_dataset` safe to re-run (the same property `store.load_named_graph`'s
own docstring names: "re-running a revision is idempotent"), and it's the
correct verb here specifically because Gold's four named graphs
(graph:masterdata, graph:refdata, graph:oracle, graph:results — vocab.py)
are each rebuilt wholesale by one Gold run, never patched triple-by-triple
(`fuseki_client.py`'s own docstring already said this about
`build_graph_store_put_request`; this module is the first caller that
actually uses it against all four graphs together instead of one
illustrative example).

One graph is expected to come back empty today: graph:results has no writer
yet (`sparql_queries.py`'s `EXAMPLE_QUERIES["systems_and_members"]` reads
from it, but nothing in this package populates it — a systemization run
writing its computed `pidsys:CommissioningSystem` output back as RDF is a
separate, not-yet-built piece). `push_dataset` skips an empty graph rather
than PUTting a no-op empty replace, and reports the skip so that's visible
in the output, not silently absent.

**Authentication (added 2026-09-10, real-deployment finding):** a real
Fuseki instance rejects an unauthenticated PUT with HTTP 401 — confirmed
against the user's own running instance, not a hypothetical. `fuseki_client.
FusekiConfig` now carries `user`/`password`, sent as HTTP Basic Auth on
every request; the defaults below (`DEFAULT_USER`/`DEFAULT_PASSWORD`,
"admin"/"admin") match `fuseki/docker-compose.yml`'s `ADMIN_PASSWORD` and
the sibling `ido-prototype`'s own `pipeline/config.py::FUSEKI_USER,
FUSEKI_PW`, which its `pipeline/store.py` already sends this same way on
every request. Override with `--user`/`--password` on the CLI, or the
`FUSEKI_ADMIN_USER`/`FUSEKI_ADMIN_PASSWORD` environment variables, if your
Fuseki instance's `ADMIN_PASSWORD` differs from the compose file's demo
default (it should, past a local prototype).

Usage
-----

Smoke test, once `docker compose up -d` in `fuseki/` has a Fuseki instance
running locally (no separate "create the dataset" step needed —
`FUSEKI_DATASET_1` in the compose file does that on container startup):

    python -m gold.fuseki_bootstrap --fixture

This pushes the exact same small nitrogen/process/flare scenario
`tests/fixtures.py::build_fixture_dataset` builds and the unit suite
already validates — a known-good baseline to confirm the round trip works
before pointing this at real Silver data, the same role the sibling
prototype's `bootstrap.py` plays loading its fixed ontology/reference-data
files first.

Against real data, call `push_dataset` directly from a notebook or driver
script once you have a `Dataset` from `gold_job.build_rdf_dataset`:

    from gold.gold_job import build_rdf_dataset
    from gold.fuseki_bootstrap import push_dataset, DEFAULT_BASE_URL, DEFAULT_DATASET, DEFAULT_USER, DEFAULT_PASSWORD
    from gold.fuseki_client import FusekiConfig

    ds = build_rdf_dataset(inputs)   # inputs: gold_job.GoldInputs
    cfg = FusekiConfig(base_url=DEFAULT_BASE_URL, dataset=DEFAULT_DATASET,
                        user=DEFAULT_USER, password=DEFAULT_PASSWORD)
    report = push_dataset(ds, cfg)

Unexecuted in this sandbox for the same reason `fuseki_client.py` itself
is: no Fuseki instance is reachable here. Run the smoke test above against
your own `docker compose up -d` to close the "live Fuseki round-trip" gap
`gold_layer_spec.md` §8.2 / §10 names — this module cannot itself prove
that closure from inside this sandbox, only that its request construction
follows Fuseki's documented HTTP surface (same caveat `fuseki_client.py`
and `tests/test_fuseki_client.py` already carry).
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Iterable

from . import vocab as v
from .fuseki_client import FusekiConfig, push_named_graph
from .rdf_model import Dataset

DEFAULT_BASE_URL = "http://localhost:3030"
DEFAULT_DATASET = "gold"  # matches fuseki/docker-compose.yml's FUSEKI_DATASET_1
# Local-dev defaults matching fuseki/docker-compose.yml's ADMIN_PASSWORD --
# override via --user/--password or these env vars for anything beyond a
# local prototype (see this module's docstring, "Authentication").
DEFAULT_USER = os.environ.get("FUSEKI_ADMIN_USER", "admin")
DEFAULT_PASSWORD = os.environ.get("FUSEKI_ADMIN_PASSWORD", "admin")

# Pushed in this order: masterdata/refdata (what the classification rules
# and SPARQL surface actually read from) before oracle (rule-invisible,
# see oracle_guard.py) and results (empty until a systemization run
# populates it, per this module's own docstring) — order has no functional
# effect (each PUT targets a distinct named graph independently), it just
# reads top-to-bottom in the same priority the strategy itself gives them.
NAMED_GRAPHS = (v.GRAPH_MASTERDATA, v.GRAPH_REFDATA, v.GRAPH_ORACLE, v.GRAPH_RESULTS)


def push_dataset(ds: Dataset, cfg: FusekiConfig, graphs: Iterable[str] = NAMED_GRAPHS) -> dict:
    """PUTs each named graph in `graphs` that has at least one triple in
    `ds` into the Fuseki dataset `cfg` points at, via the Graph Store
    Protocol (drop-and-replace — safe to re-run). Returns a report:
    ``{"pushed": {graph: triple_count}, "skipped_empty": [graph, ...]}``.

    Raises whatever `urllib.error.HTTPError`/`URLError` `fuseki_client.
    push_named_graph` raises on a failed PUT (connection refused, wrong
    dataset name, ...) — this function does not swallow those, since a
    partially-pushed dataset (some graphs replaced, one failed) is exactly
    the kind of state a caller needs to see, not have hidden."""
    pushed: dict = {}
    skipped: list = []
    for graph in graphs:
        triples = list(ds.triples(graph=graph))
        if not triples:
            skipped.append(graph)
            continue
        turtle = ds.to_turtle(graph)
        status = push_named_graph(cfg, graph, turtle)
        pushed[graph] = len(triples)
        print(f"  PUT {graph} -> HTTP {status} ({len(triples)} triples)")
    for graph in skipped:
        print(f"  skip {graph} -> 0 triples in this Dataset, nothing to push")
    return {"pushed": pushed, "skipped_empty": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Push a Gold RDF Dataset into a real Fuseki instance "
        "(see this module's docstring for the two ways to use it)."
    )
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"default: {DEFAULT_BASE_URL}")
    ap.add_argument("--dataset", default=DEFAULT_DATASET, help=f"default: {DEFAULT_DATASET}")
    ap.add_argument(
        "--user", default=DEFAULT_USER,
        help=f"HTTP Basic Auth username, default: {DEFAULT_USER!r} "
        "(or $FUSEKI_ADMIN_USER) -- matches fuseki/docker-compose.yml's admin user",
    )
    ap.add_argument(
        "--password", default=DEFAULT_PASSWORD,
        help="HTTP Basic Auth password, default: matches fuseki/docker-compose.yml's "
        "ADMIN_PASSWORD (or $FUSEKI_ADMIN_PASSWORD)",
    )
    ap.add_argument(
        "--fixture",
        action="store_true",
        help="Push tests/fixtures.py's small nitrogen/process/flare scenario "
        "as a smoke test, instead of real Silver data. This is currently the "
        "only data source this CLI knows how to build standalone -- for real "
        "data, call push_dataset(ds, cfg) directly from your own script or "
        "notebook once you have `ds = gold_job.build_rdf_dataset(inputs)` "
        "(see the module docstring).",
    )
    args = ap.parse_args(argv)

    if not args.fixture:
        print(
            "Nothing to push: pass --fixture for a smoke test against "
            "tests/fixtures.py's scenario, or call push_dataset(ds, cfg) "
            "directly from your own script/notebook with a Dataset built "
            "from real Silver data via gold_job.build_rdf_dataset(inputs)."
        )
        return 1

    from tests.fixtures import build_fixture_dataset  # local import: test-only fixture, not a runtime dependency

    ds = build_fixture_dataset()
    cfg = FusekiConfig(base_url=args.base_url, dataset=args.dataset, user=args.user, password=args.password)
    print(f"Pushing tests/fixtures.py's scenario into {cfg.base_url}/{cfg.dataset} "
          f"(authenticating as {cfg.user!r}) ...")
    report = push_dataset(ds, cfg)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
