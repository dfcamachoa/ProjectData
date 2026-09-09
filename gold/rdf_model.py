"""The Gold RDF quad store -- now real `rdflib`, not hand-rolled.

Through 2026-09-06 this module hand-rolled the RDF machinery the Gold layer
needs (typed terms, named-graph quads, Turtle/N-Quads serialisation) because
PyPI was unreachable from the build sandbox. As of 2026-09-09 the user's own
local environment carries `rdflib` (plus `owlrl`, `pyspark`, `delta-spark`),
so this module is rewritten to be a real `rdflib.Dataset` underneath --
storage, quad identity, Literal typing, and Turtle/N-Quads serialisation are
all genuinely rdflib's now, nothing reimplemented.

**Why the call surface below is kept identical to the old hand-rolled
version** (`Dataset.add(s, p, o, graph)`, `.triples(s=, p=, o=, graph=)`,
`.graphs()`, `.graph_view()`, `.predicates_used()`, `.to_nquads()`,
`.to_turtle()`, plus the `merge()` free function) rather than switching
every caller to rdflib's own quad-tuple API (`ds.add((s,p,o,c))`,
`ds.quads((s,p,o,c))`): this sandbox has no network access to install
`rdflib` itself (`pypi.org` is not on this org's egress allowlist -- the
same constraint the spec has documented since the module was first built),
so nothing in this file, or in any module built on it
(`rdf_mapper.py`, `oracle_guard.py`, `rules_reference.py`,
`sparql_queries.py`, `gold_job.py`) or their tests, can actually be
*executed* here to verify a deeper rewrite. Keeping the exact call surface
that 91/91 tests already passed against means every one of those ~40 call
sites across five modules and their tests needs zero logic changes -- only
`rdflib`/`owlrl` installed to run again -- which bounds the risk of this
change to what's actually new (this file, plus the two `.value` call sites
in `oracle_guard.py` that were reading the old hand-rolled term's `.value`
attribute, which real rdflib terms don't have). `URIRef` / `BNode` /
`Literal` below are the REAL rdflib classes, re-exported as-is (not
wrapper dataclasses), so real RDF semantics -- proper XSD Literal typing,
real N-Quads/Turtle escaping, genuine term equality -- flow through the
whole package from here on, not just at the edges.

Unexecuted in this sandbox for the reason above: confirm with
`python3 -m unittest discover -s tests` once `rdflib` is installed in your
environment (the pure core -- `temporal.py`, `silver_cdc.py`,
`spark_bridge.py` -- and `fuseki_client.py`, none of which import this
module, keep passing here regardless).
"""
from __future__ import annotations

from typing import Iterator, Optional, Set, Union

import rdflib
from rdflib import BNode, Literal, URIRef

Term = Union[URIRef, BNode, Literal]


def U(value: str) -> URIRef:
    return URIRef(value)


def L(value, datatype: Optional[str] = None) -> Literal:
    """A Literal, XSD-typed automatically by rdflib from the Python type of
    `value` when `datatype` is omitted (rdflib's own `bool`/`int`/`float`/
    `date`/`datetime` -> XSD mapping -- an upgrade over the old hand-rolled
    version, which only special-cased `bool`)."""
    if datatype is not None:
        return Literal(value, datatype=URIRef(datatype))
    return Literal(value)


class Quad:
    """A lightweight (s, p, o, g) view over one rdflib quad. `g` is always
    the bare graph identifier (a URIRef), never an `rdflib.Graph` object --
    see `_graph_id` below, which normalises the two shapes rdflib itself
    hands back depending on call site and version."""

    __slots__ = ("s", "p", "o", "g")

    def __init__(self, s: Term, p: URIRef, o: Term, g: URIRef) -> None:
        self.s = s
        self.p = p
        self.o = o
        self.g = g

    def __repr__(self) -> str:  # pragma: no cover -- debugging aid only
        return f"Quad({self.s!r}, {self.p!r}, {self.o!r}, {self.g!r})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, Quad)
                and (self.s, self.p, self.o, self.g) == (other.s, other.p, other.o, other.g))

    def __hash__(self) -> int:
        return hash((self.s, self.p, self.o, self.g))


def _graph_id(context) -> URIRef:
    identifier = getattr(context, "identifier", None)
    return identifier if identifier is not None else context


def _as_text(value) -> str:
    """`Graph.serialize`/`Dataset.serialize` return `str` on current rdflib
    (6.x/7.x) but returned `bytes` on some older releases when no
    `destination` is given — decode defensively rather than assume one or
    the other, since this file can't be executed here to pin a version."""
    return value.decode("utf-8") if isinstance(value, bytes) else value


class Dataset:
    """Same call surface as the hand-rolled quad store this replaces, now
    backed end to end by a real `rdflib.Dataset` (`default_union=False`, so
    an unscoped pattern never silently leaks across named graphs -- the
    same graph isolation `graph:oracle`'s quarantine depends on)."""

    def __init__(self) -> None:
        self._ds = rdflib.Dataset(default_union=False)

    def add(self, s: Term, p: URIRef, o: Term, graph) -> None:
        g = graph if isinstance(graph, URIRef) else URIRef(graph)
        self._ds.add((s, p, o, g))

    def __len__(self) -> int:
        return sum(1 for _ in self._ds.quads((None, None, None, None)))

    def __iter__(self) -> Iterator[Quad]:
        for s, p, o, g in self._ds.quads((None, None, None, None)):
            yield Quad(s, p, o, _graph_id(g))

    def graphs(self) -> "Set[str]":
        out = set()
        for ctx in self._ds.contexts():
            gid = _graph_id(ctx)
            if any(True for _ in self._ds.quads((None, None, None, gid))):
                out.add(str(gid))
        return out

    def triples(
        self,
        s: Optional[Term] = None,
        p: Optional[Term] = None,
        o: Optional[Term] = None,
        graph: Optional[str] = None,
    ) -> Iterator[Quad]:
        g = URIRef(graph) if isinstance(graph, str) else graph
        for s_, p_, o_, g_ in self._ds.quads((s, p, o, g)):
            yield Quad(s_, p_, o_, _graph_id(g_))

    def graph_view(self, graph: str) -> "Dataset":
        """A Dataset containing only the quads of one named graph."""
        sub = Dataset()
        for q in self.triples(graph=graph):
            sub.add(q.s, q.p, q.o, q.g)
        return sub

    def predicates_used(self, graph: Optional[str] = None) -> "Set[str]":
        return {str(q.p) for q in self.triples(graph=graph)}

    def to_nquads(self) -> str:
        return _as_text(self._ds.serialize(format="nquads"))

    def to_turtle(self, graph: str) -> str:
        """Turtle for one named graph (Turtle itself carries no graph name;
        this is what `fuseki_client.push_named_graph` POSTs into that
        graph)."""
        g = URIRef(graph) if isinstance(graph, str) else graph
        return _as_text(self._ds.get_context(g).serialize(format="turtle"))

    def rdflib_dataset(self) -> "rdflib.Dataset":
        """Escape hatch to the real `rdflib.Dataset` underneath, for
        anything this thin wrapper doesn't expose -- real SPARQL execution
        (`sparql_queries.run_sparql`) and owlrl reasoning
        (`owl_reasoning.py`) both use this rather than duplicating access
        another way."""
        return self._ds


def merge(*datasets: Dataset) -> Dataset:
    out = Dataset()
    for ds in datasets:
        for q in ds:
            out.add(q.s, q.p, q.o, q.g)
    return out
