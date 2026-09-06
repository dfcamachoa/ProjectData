"""A minimal, dependency-free RDF quad store.

PyPI is unreachable from this sandbox (rdflib cannot be installed here), so
this module hand-rolls exactly the subset of RDF machinery the Gold layer
needs: typed terms, triples scoped to named graphs (i.e. quads), Turtle/N-Quads
serialisation, and a tiny triple-pattern matcher. Every shape here (URIRef /
Literal / BNode terms, `(s, p, o, graph)` quads, one named graph per logical
dataset) maps 1:1 onto rdflib's `ConjunctiveGraph` / `Dataset` API, so a
graduation to rdflib is a re-plumbing of this module's internals, not a
redesign of anything that calls it (`rdf_mapper.py`, `rules_reference.py`,
`sparql_queries.py`). This mirrors the precedent the Silver spec already sets
for the PoC: prefer a small native implementation over a heavy dependency,
document the swap-in point, keep the shapes standard (silver_layer_spec.md
§3.4, on choosing a native quality suite over the `great_expectations`
library for the same reason).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Optional, Union


@dataclass(frozen=True)
class URIRef:
    value: str

    def __str__(self) -> str:
        return self.value

    def n3(self) -> str:
        return f"<{self.value}>"


@dataclass(frozen=True)
class BNode:
    value: str

    def __str__(self) -> str:
        return f"_:{self.value}"

    def n3(self) -> str:
        return str(self)


@dataclass(frozen=True)
class Literal:
    value: object
    datatype: Optional[str] = None
    lang: Optional[str] = None

    def __str__(self) -> str:
        return str(self.value)

    def n3(self) -> str:
        text = str(self.value)
        if isinstance(self.value, bool):
            text = "true" if self.value else "false"
            return f'"{text}"^^<http://www.w3.org/2001/XMLSchema#boolean>'
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        if self.lang:
            return f'"{escaped}"@{self.lang}'
        if self.datatype:
            return f'"{escaped}"^^<{self.datatype}>'
        return f'"{escaped}"'


Term = Union[URIRef, BNode, Literal]


def U(value: str) -> URIRef:
    return URIRef(value)


def L(value, datatype: Optional[str] = None) -> Literal:
    return Literal(value, datatype=datatype)


@dataclass(frozen=True)
class Quad:
    s: Term
    p: URIRef
    o: Term
    g: URIRef  # named graph this triple is asserted in


class Dataset:
    """A conjunctive quad store: one default index, queryable per named graph."""

    def __init__(self) -> None:
        self._quads: list[Quad] = []
        self._seen: set[tuple] = set()

    def add(self, s: Term, p: URIRef, o: Term, graph: str) -> None:
        g = graph if isinstance(graph, URIRef) else URIRef(graph)
        key = (s, p, o, g)
        if key in self._seen:
            return  # quad sets are sets; keep it idempotent
        self._seen.add(key)
        self._quads.append(Quad(s, p, o, g))

    def __len__(self) -> int:
        return len(self._quads)

    def __iter__(self) -> Iterator[Quad]:
        return iter(self._quads)

    def graphs(self) -> set[str]:
        return {q.g.value for q in self._quads}

    def triples(
        self,
        s: Optional[Term] = None,
        p: Optional[Term] = None,
        o: Optional[Term] = None,
        graph: Optional[str] = None,
    ) -> Iterator[Quad]:
        g = URIRef(graph) if isinstance(graph, str) else graph
        for q in self._quads:
            if s is not None and q.s != s:
                continue
            if p is not None and q.p != p:
                continue
            if o is not None and q.o != o:
                continue
            if g is not None and q.g != g:
                continue
            yield q

    def graph_view(self, graph: str) -> "Dataset":
        """A read-only Dataset containing only the quads of one named graph."""
        sub = Dataset()
        for q in self.triples(graph=graph):
            sub.add(q.s, q.p, q.o, q.g)
        return sub

    def predicates_used(self, graph: Optional[str] = None) -> set[str]:
        return {q.p.value for q in self.triples(graph=graph)}

    def to_nquads(self) -> str:
        lines = []
        for q in sorted(self._quads, key=lambda q: (q.g.value, str(q.s), q.p.value, str(q.o))):
            lines.append(f"{q.s.n3()} {q.p.n3()} {q.o.n3()} {q.g.n3()} .")
        return "\n".join(lines) + ("\n" if lines else "")

    def to_turtle(self, graph: str) -> str:
        """Turtle for one named graph (Turtle itself carries no graph name;
        this is what `fuseki_client.push_named_graph` POSTs into that graph)."""
        lines = []
        for q in sorted(self.triples(graph=graph), key=lambda q: (str(q.s), q.p.value, str(q.o))):
            lines.append(f"{q.s.n3()} {q.p.n3()} {q.o.n3()} .")
        return "\n".join(lines) + ("\n" if lines else "")


def merge(*datasets: Dataset) -> Dataset:
    out = Dataset()
    for ds in datasets:
        for q in ds:
            out.add(q.s, q.p, q.o, q.g)
    return out
