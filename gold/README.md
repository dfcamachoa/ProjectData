# Gold layer prototype

Companion code to `gold_layer_spec.md`. Two halves, two dependency stories
as of 2026-09-09: the bi-temporal core (`temporal.py`, `silver_cdc.py`,
`spark_bridge.py`, `config.py`) is pure Python, zero third-party
dependencies, unit-tested in any sandbox including this one. The RDF/IDO
projection (`rdf_model.py`, `rdf_mapper.py`, `oracle_guard.py`,
`rules_reference.py`, `sparql_queries.py`, `owl_reasoning.py`) and Gold's
Spark job (`schema.py`, `spark_job.py`) now require real `rdflib`/`owlrl`
and `pyspark`/`delta-spark` respectively — see "Gold graduates to real
rdflib, owlrl, pyspark and delta-spark" below for the full story and why.
`fuseki_client.py` stays stdlib-only regardless (see its own docstring).

## Run the tests

```
cd gold_layer
pip install -r requirements.txt   # only if you want the rdflib/owlrl/pyspark tests too
python3 -m unittest discover -s tests -v
```

**In this sandbox** (no `rdflib`/`owlrl`/`pyspark` — PyPI is not on this
org's egress allowlist): 62 tests pass — the full pure core
(`test_temporal.py`, `test_silver_cdc.py`, `test_spark_bridge.py`) plus
`test_fuseki_client.py` (7 tests, including the HTTP Basic Auth coverage
added 2026-09-10 — see below). Five test modules fail to even import
(`test_rdf_mapper.py`, `test_rules_reference.py`, `test_gold_job.py`,
`test_owl_reasoning.py`, `test_fuseki_bootstrap.py`, 46 tests between them)
with a plain `ModuleNotFoundError: No module named 'rdflib'` (or `'owlrl'`
for `test_owl_reasoning.py`) — confirmed clean, not a broken import chain;
install `requirements.txt` and they run too. **In your own environment**,
with those installed: all 108.

## Layout

```
gold/
  vocab.py             namespace + predicate constants; the IDO-alignment boundary
  rdf_model.py          real rdflib-backed RDF quad store (was hand-rolled through 2026-09-06)
  temporal.py            bi-temporal versioning (two time axes)
  silver_cdc.py           consumes Silver Stage E's silver_cdc table (the primary path)
  rdf_mapper.py          canonical objects -> RDF/IDO projection
  oracle_guard.py         oracle-quarantine structural invariant
  rules_reference.py      declarative classification / directional guards
  sparql_queries.py       real SPARQL via rdflib (run_sparql) + a local pattern-matcher (run_local_pattern)
  owl_reasoning.py        real OWL-RL entailment via owlrl, cross-checked against a hand-walked closure (requires owlrl)
  fuseki_client.py        stdlib-only Fuseki Graph Store / SPARQL client
  fuseki_bootstrap.py     push a Dataset's named graphs into a real Fuseki (requires rdflib, via rdf_model)
  gold_job.py             orchestration (Spark-free; the bi-temporal Spark job lives in spark_job.py)
  config.py               GoldConfig -- everything that varies between projects/environments
  spark_bridge.py         pure dict-in/dict-out bridge: collected Silver rows -> SilverCdcEvent / GoldRow
  schema.py               Gold Delta table schemas (requires pyspark)
  spark_job.py            the real Spark job: run_gold() (requires pyspark, delta-spark)
  jena_rules/
    classification.rules  Apache Jena counterpart of rules_reference.py
fuseki/
  docker-compose.yml      stain/jena-fuseki, dataset "gold" auto-created on startup (mirrors ido-prototype)
tests/
  fixtures.py             the walk.py scenarios reproduced in miniature
  test_*.py               one file per gold/ module (test_silver_cdc.py / test_spark_bridge.py /
                           test_temporal.py / test_fuseki_client.py are the pure core; test_rdf_mapper.py /
                           test_rules_reference.py / test_gold_job.py / test_owl_reasoning.py /
                           test_fuseki_bootstrap.py need rdflib/owlrl)
requirements.txt           rdflib, owlrl, pyspark, delta-spark -- not pinned, see the file's own comment
gold_layer_spec.md         the design spec this code implements
medallion_concepts.ipynb   the one demo notebook -- see its own section below
```

(2026-09-10 cleanup: this is now the full file set. Earlier drafts also
carried a second, pre-Gold `medallion_concepts.ipynb`, a separate
`bronze_silver_gold_rev_c_to_d.ipynb` demo, the `build_notebook.py` /
`build_rev_cd_notebook.py` scripts that generated them, a `demo_pipeline/`
package of mini-reimplementations those scripts depended on, and a
duplicate `gold_layer_spec_current.md` — all removed as redundant once one
notebook and one spec file covered everything they did. Nothing about
`gold/`, `tests/`, or `fuseki/` changed; this was a file-count cleanup,
not a design change.)

## Gold graduates to real rdflib, owlrl, pyspark and delta-spark (2026-09-09)

Through 2026-09-06, `gold/`'s RDF/IDO projection was hand-rolled (`rdf_model.py`
reimplemented a minimal quad store) because PyPI was unreachable from the
build sandbox — `rdflib`, `owlrl`, `pyspark`, and `delta-spark` could not be
installed there. The user's own local environment carries all four, so
they're now real dependencies rather than a documented graduation point:

- **`gold/rdf_model.py`** is rewritten to be genuinely `rdflib`-backed —
  storage, quad identity, Literal XSD typing, and Turtle/N-Quads
  serialisation are all real `rdflib` now, nothing reimplemented. `URIRef` /
  `BNode` / `Literal` are rdflib's own classes, re-exported as-is (not
  wrapper dataclasses), so real RDF semantics flow through the whole
  package, not just at the edges.
- **The call surface is kept identical on purpose** — `Dataset.add(s, p, o,
  graph)`, `.triples(s=, p=, o=, graph=)`, `.graphs()`, `.graph_view()`,
  `.predicates_used()`, `.to_nquads()`, `.to_turtle()`, `merge()` — rather
  than switching every caller (`rdf_mapper.py`, `oracle_guard.py`,
  `rules_reference.py`, `sparql_queries.py`, `gold_job.py`, and their
  tests — roughly 40 call sites across five modules) to rdflib's own
  quad-tuple API. This sandbox has no way to install `rdflib` to verify a
  deeper rewrite, so keeping the surface that 91/91 tests already passed
  against bounds the risk to what's actually new: this file, plus two
  `.value` call sites in `oracle_guard.py` that read the old hand-rolled
  term's `.value` attribute, which rdflib's real (str-subclass) `URIRef`
  doesn't have — fixed to `str(q.p)` / `str(q.g)`. See `rdf_model.py`'s own
  docstring for the full reasoning, including why this is a deliberate
  choice, not an unstated shortcut.
- **`gold/sparql_queries.py`** gains `run_sparql(ds, query)` — the example
  `EXAMPLE_QUERIES` SPARQL strings now execute for real, against the
  in-memory `Dataset`, via rdflib's own SPARQL 1.1 engine (including their
  `FROM`/`GRAPH` named-graph clauses, since named graphs are real rdflib
  contexts under the hood) — no Fuseki required, and the identical query
  text runs unchanged against a real Fuseki later. `run_local_pattern`
  stays for lighter-weight lookups.
- **`gold/owl_reasoning.py`** is new: real OWL-RL entailment via `owlrl`,
  the first concrete step of this project's own next phase ("explore the
  use of an owl Semantic approach... business rules applied to industrial
  plant data based on IDO"). Deliberately narrow scope — see the module's
  own docstring — it cross-checks ONE thing real reasoning is the right
  tool for: that every component/equipment/nozzle instance really is
  transitively an `ido:PhysicalObject` via `rdfs:subClassOf` (the thing
  every rule in `rules_reference.py` and `rdf_mapper.py`'s correctness
  note #1 already assume), by comparing owlrl's real entailment against a
  hand-walked closure over the *asserted* subclass edges — two independent
  computations of the same answer, not owlrl grading its own homework. It
  does NOT attempt to re-express the fluid/flow business rules as OWL/SWRL
  — those stay Jena's / `rules_reference.py`'s territory, exactly as the
  project already decided (medallion_rdf_ido_strategy_mapping.md §6).
- **`gold/gold_job.py`** and **`gold/__init__.py`** docstrings updated —
  `build_rdf_dataset` is no longer dependency-free (it calls into
  `rdf_mapper.py`), even though it stays Spark-free; `build_bitemporal_tables*`
  stay pure (`temporal.py`/`silver_cdc.py` only).

**Verification, given this sandbox cannot install `rdflib`/`owlrl` at all**
(confirmed: `pypi.org` returns "Host not in allowlist" — an org egress
policy, not a transient outage): every module that imports `rdf_model.py`
fails with a plain `ModuleNotFoundError: No module named 'rdflib'`
(`'owlrl'` for `owl_reasoning.py`), never a broken import chain, and the
pure core's 58 tests are unaffected (`test_fuseki_client.py` included,
since `fuseki_client.py` never imports `rdf_model`). Beyond that static
check, this rewrite was exercised against a throwaway, deliberately
minimal LOCAL STUB faithfully implementing rdflib's/owlrl's documented API
shapes (`Dataset.add`/`.quads`/`.contexts`/`.get_context`/`.serialize`,
`DeductiveClosure(OWLRL_Semantics).expand`) — **not the real libraries** —
and all 94 tests (the full suite, including the four modules and
`test_owl_reasoning.py` that need `rdflib`/`owlrl`) passed against it. That
stub was built only to catch control-flow and API-shape mistakes before
shipping, then deleted — it is not included here and proves nothing about
the real libraries' actual behaviour. Run
`pip install -r requirements.txt && python3 -m unittest discover -s tests -v`
in your own environment to confirm against the real thing.

(94 was the count as of this graduation alone; the Fuseki setup and its
2026-09-10 auth fix, next two sections, bring the total once installed to
108 — see "Run the tests" above for the current numbers.)

## A real Fuseki, mirroring the sibling `ido-prototype` (2026-09-09)

The user's `ido-prototype` project already runs Fuseki locally via Docker
(`fuseki/docker-compose.yml`, `stain/jena-fuseki` image, `FUSEKI_DATASET_1`
auto-creating a persistent dataset on startup — see that project's README /
`CONTINUATION_BRIEF.md` §2, §9) and pushes named graphs into it via a thin
`requests` + `rdflib`-based `pipeline/store.py`. Asked to follow the same
approach here, this package now has:

- **`fuseki/docker-compose.yml`** — the same image and auto-create
  mechanism, one-for-one, just named for this dataset (`gold`, at the
  user's choice — an arbitrary, unrelated placeholder string in
  `tests/test_fuseki_client.py`'s fixtures, `"pidsys"`, is not this and
  needs no renaming) and a different container name (`gold-fuseki`) so it
  doesn't collide with a simultaneously-running `ido-prototype` instance.
  `cd fuseki && docker compose up -d` brings up Fuseki on
  `http://localhost:3030`, dataset already created.
- **`gold/fuseki_bootstrap.py`** — the orchestration `pipeline/bootstrap.py`
  + `pipeline/store.py::load_named_graph` play there, built on this
  package's own already-stdlib-only `gold/fuseki_client.py` instead of
  adding `requests`/`SPARQLWrapper` as new dependencies (that module was
  already genuinely production-usable without them — see its own
  docstring). `push_dataset(ds, cfg)` PUTs each of Gold's four named graphs
  that has at least one triple in `ds` — a drop-and-replace Graph Store
  Protocol PUT per graph, safe to re-run, the same idempotent-on-rerun
  discipline `store.load_named_graph`'s own docstring names — and skips
  (reporting, not silently) a graph with zero triples: today that's
  `graph:results`, which has no writer yet (a systemization run writing
  its computed `pidsys:CommissioningSystem` output back as RDF is a
  separate, not-yet-built piece — `sparql_queries.py`'s own
  `EXAMPLE_QUERIES["systems_and_members"]` already reads from it, ready
  for when that exists).
- **A standalone smoke test**: `python -m gold.fuseki_bootstrap --fixture`
  pushes the exact same small nitrogen/process/flare scenario
  `tests/fixtures.py::build_fixture_dataset` builds and the unit suite
  already validates, into whatever Fuseki instance `--base-url`/`--dataset`
  point at (defaulting to `http://localhost:3030` / `gold`) — the same role
  `ido-prototype`'s `bootstrap.py` plays loading its fixed ontology files
  first, before real data flows through. For real Silver data, call
  `push_dataset(ds, cfg)` directly once you have `ds =
  gold_job.build_rdf_dataset(inputs)`.
- **`tests/test_fuseki_bootstrap.py`** (8 new tests) — unit-tests
  `push_dataset`'s own control flow (which graphs get pushed vs. skipped,
  the report shape, that overriding `--base-url`/`--dataset` actually
  reaches the `FusekiConfig` used) by monkeypatching
  `fuseki_client.push_named_graph`, the same "test request construction and
  logic, not a live round-trip" scope `test_fuseki_client.py` already has.
  None of this proves the real PUT succeeds against a real Fuseki — only
  running the smoke test above against your own `docker compose up -d` can
  prove that; this closes the code-and-tests half of the "live Fuseki
  round-trip" gap named in `gold_layer_spec.md` §8.2/§10, not the "actually
  run it" half, which only your own environment can do.
- The notebook's §8 cell (`medallion_concepts.ipynb`) is rewired to call
  `push_dataset` for all four graphs instead of building one illustrative
  PUT request for `graph:masterdata` alone.

Unexecuted in this sandbox for the same reason everything else RDF-related
here is: no `rdflib` (so `fuseki_bootstrap.py` can't even import) and no
Fuseki instance reachable from here regardless. `test_fuseki_bootstrap.py`
was verified the same way the rdflib/owlrl graduation above was — against
a throwaway local stub, deleted after use, proving control-flow
correctness against the documented API shapes, not correctness against a
real Fuseki.

## Real-deployment finding: Fuseki writes need HTTP Basic Auth (2026-09-10)

Running `python -m gold.fuseki_bootstrap --fixture` against a real,
`docker compose up`-started Fuseki instance for the first time surfaced
what the sandbox couldn't: the PUT failed with a plain `HTTP Error 401:
Unauthorized`. Fuseki's `stain/jena-fuseki` image (this package's own
`fuseki/docker-compose.yml`, and the sibling `ido-prototype`'s) requires
authenticating as the admin user for dataset writes — the sibling
prototype's `pipeline/store.py` already sends
`HTTPBasicAuth(config.FUSEKI_USER, config.FUSEKI_PW)` (admin/admin) on
every request for exactly this reason; `gold/fuseki_client.py` simply
hadn't needed it yet because it had never been run against a real,
running Fuseki before this.

Fixed: `FusekiConfig` gains `user`/`password` fields; when set, every
request this module builds (`build_graph_store_put_request`,
`build_sparql_query_request`, `build_sparql_update_request`) now carries
an HTTP Basic `Authorization` header, built with stdlib `base64` rather
than adding `requests`/`requests.auth.HTTPBasicAuth` as a new dependency —
`fuseki_client.py` stays dependency-free either way. `gold/
fuseki_bootstrap.py`'s `DEFAULT_USER`/`DEFAULT_PASSWORD` default to
`admin`/`admin`, matching `fuseki/docker-compose.yml`'s `ADMIN_PASSWORD`
(override with `--user`/`--password` on the CLI, or the
`FUSEKI_ADMIN_USER`/`FUSEKI_ADMIN_PASSWORD` environment variables, once
you change that default past a local prototype — you should). An unset
`user` still means no `Authorization` header at all, so this is
backward-compatible with any code that built a bare `FusekiConfig` before
this fix.

7 new tests in `tests/test_fuseki_client.py` (a fixed, checkable Basic
Auth token; the header appears on all three request builders when
credentials are set; it's absent when they aren't) plus 2 new tests in
`tests/test_fuseki_bootstrap.py` (the CLI's `--user`/`--password` reach
the `FusekiConfig` used; the no-flags default is `admin`/`admin`) — all
run and pass in this sandbox for `test_fuseki_client.py` (no `rdflib`
needed there), verified against the same kind of throwaway stub as
before for `test_fuseki_bootstrap.py` (needs `rdflib`, so it still can't
run here directly). This is a real, reported 401 from the user's own
environment, not a hypothetical this sandbox invented — the fix is
believed correct against Fuseki's documented Basic Auth support, but
only the user re-running `python -m gold.fuseki_bootstrap --fixture`
against their own instance can confirm it clears the 401 for real.

**Confirmed, same day:** the user re-ran the smoke test against their own
instance and it worked — `HTTP 201` on both non-empty named graphs
(`graph:masterdata`, 80 triples; `graph:refdata`, 23 triples),
`graph:oracle`/`graph:results` correctly skipped as empty. This is the
first piece of Gold's RDF/IDO projection proven end-to-end against a real,
running Fuseki instance, not a stub or a request-construction unit test —
the "live Fuseki round-trip" gap this project has carried since its first
implementation-status entry is closed for the Graph Store Protocol side.
A live Jena rules-engine load against `jena_rules/classification.rules`
remains open and was never in scope for `fuseki_bootstrap.py`.

## Gold now runs on real Spark (2026-09-06)

Bronze and Silver in the user's own environment already run on real
Spark/Delta (`bronze/ingest.py`, `silver/cdc_job.py`). Gold's execution path
previously stopped short of that: `medallion_concepts.ipynb`'s (then
called `medallion_concepts_with_gold.ipynb`, before the 2026-09-10
cleanup) Bridge 1/2 drove Gold from `.toPandas()`'d
DataFrames as notebook glue, and `gold_job.py`'s own `SPARK_SKETCH` was
explicitly illustrative pseudocode (placeholder calls like
`load_reference_sheet` / `write_gold_delta_tables` that don't exist
anywhere), never exercised. Gold now has a real Spark job instead, built to
mirror Bronze/Silver's own conventions line for line rather than
introducing a new style:

- **`gold/config.py`** — `GoldConfig`, the same role as `BronzeConfig` /
  `SilverConfig`: everything that varies (which Bronze table/path to read,
  the Silver schema, the Gold output schema, whether to register tables in
  the Hive/Derby metastore) lives here as data.
- **`gold/spark_bridge.py`** — pure, zero-pyspark-import bridge that takes
  the already-`.collect()`ed rows (`Row.asDict(recursive=True)`, exactly
  what `silver/cdc_job.py::run_cdc` already produces at this PoC scale) and
  resolves them into `SilverCdcEvent`s / `GoldRow` dicts. This is what
  replaces `append_gold_cells.py`'s pandas-based Bridge 1/2 — the same
  `valid_from`-by-drawing, per-grain attrs, and line-grain
  `aggregate_lines`-preferred-with-fallback logic, just reshaped to plain
  dicts instead of DataFrames so it stays unit-tested without a
  `SparkSession` (`tests/test_spark_bridge.py`, 18 new tests).
- **`gold/schema.py`** — the two Gold Delta table schemas (`gold_objects`,
  `gold_anomalies`), mirroring `bronze/schema.py` / Silver's own per-table
  StructTypes.
- **`gold/spark_job.py`** — `run_gold(cfg, spark=None)`, the thin
  orchestration layer itself: `bronze.spark_session.get_spark()` for the
  session (the exact function Bronze/Silver already use), reads this run's
  `silver_cdc` batch plus whichever per-grain Silver tables it touches,
  reads back Gold's OWN previous `gold_objects` table (`silver_cdc` is
  overwritten each Silver run, not accumulated — Gold's own table is what
  carries bi-temporal history across runs), applies the batch via
  `apply_silver_cdc_events_tolerant`, and writes `gold_objects` (full
  rewrite — a lossless re-serialization of every row-version, open and
  closed, not a truncate-and-lose-history) plus `gold_anomalies` when this
  run produced any.

Usage, once run in an environment with `pyspark`/`delta-spark` installed
(this sandbox has neither, so this is unexecuted here — see the caveat
below):

```python
from gold.config import GoldConfig
from gold.spark_job import run_gold

summary = run_gold(GoldConfig(
    bronze_table="bronze.pid_documents",
    silver_schema="silver",
    gold_schema="gold",
))
```

Run it once per orchestration cycle, right after Silver's Stage E
(`silver.cdc_job.run_cdc`) — it consumes exactly the `silver_cdc` batch
that run just wrote.

**Honest caveat:** `gold/schema.py` and `gold/spark_job.py` both `import
pyspark`, which is not installed in this sandbox (confirmed: importing
either raises a plain `ModuleNotFoundError`, not a broken import chain —
`gold/__init__.py` does not import them eagerly, so nothing else in this
package or its test suite is affected). Every function these two modules
call into (`spark_bridge.py`, `silver_cdc.py`, `temporal.py`) is real and
unit-tested; the orchestration shell around them (`_read_previous`,
`run_gold`'s read/apply/write sequence) is new, mirrors `silver/cdc_job.py`
structurally line for line, but has not itself been run against a live
Spark session anywhere. Exercise it in the user's own Spark environment —
the one Bronze and Silver already run in — before trusting it the way
Bronze/Silver's own real jobs are already trusted there.

### `medallion_concepts.ipynb` rewired to call it (2026-09-07)

The notebook itself didn't automatically pick up the change above — it
still had its own §5 built from a pandas Bridge 1/2. The generator script
is rewritten so §5's cells now call `GoldConfig` / `run_gold` directly
instead of building `resolve_drawing_valid_from` / `cdc_to_gold_events`
inline, and the notebook was regenerated from it. The rest of the notebook
is untouched: the first 51 cells (Bronze/Silver, verbatim from the user's
own base notebook) are still byte-identical, and §6-§8 (the RDF/IDO
projection, SPARQL surface, Fuseki push) still read Silver via
`.toPandas()` — that side of Gold doesn't have its own Spark job yet
(`gold_job.py`'s docstring names this as a separate, scoped gap, not an
oversight). §5's new cells are unexecuted here for the same reason as
before (no Spark session in this sandbox); re-run §5 top-to-bottom in your
environment once `gold/` is merged in.

## Real-data finding: Silver's line-grain rename (2026-09-05 / 06)

Silver's piping CDC moved from per-physical-segment grain to **line** grain
on 2026-09-05 (`silver_layer_spec.md`): ~78% of Project-B `seg_tag`s are
shared by 2+ physical `PipingNetworkSegment` pieces (SmartPlant draws one
engineering line as many pieces whose split points and UIDs churn between
revisions), so Stage E now versions piping at `(drawing_number, seg_tag)`
via `cdc.aggregate_lines`, collapsing the pieces into one line object before
diffing.

A fresh `grain='line'` `silver_cdc` sample (2026-09-06) showed the knock-on
effect for Gold: `old_uid`/`new_uid` at this grain is literally
`f"{drawing_number}|{seg_tag}"`, **not** a `silver_segments` row id -- there
is no single row to look up, since one line's key maps to multiple pieces.
Two things changed to fix this:

- `gold/silver_cdc.py::OBJECT_KINDS` now accepts `"line"` in place of the
  retired `"segment"` -- matching what a real `silver_cdc` batch's `grain`
  column actually contains.
- `gold/silver_cdc.py::aggregate_line_attrs` (+ `resolve_line_seg_tag`)
  groups a line's matching `silver_segments` pieces by
  `(drawing_number, seg_tag)` and reduces each engineering attribute
  (fluid, unit, diameter, piping-materials-class, insulation triple) to a
  sorted distinct-value tuple -- mirroring Silver's own `content_hash_eng`
  set-projection, including surfacing (not silently resolving) a
  within-line disagreement via `line_attr_inconsistent`. This is a
  documented re-derivation, not a re-implementation of Silver's algorithm
  from scratch: it is unit-tested against the semantics
  `silver_layer_spec.md` states, with a standing `TODO(confirm)` in its
  docstring to swap in Silver's own `cdc.aggregate_lines` directly the
  moment that's confirmed importable (this project's "re-house, don't
  re-derive" discipline).

One thing this did **not** require changing: `anchor` is treated as an
opaque string everywhere in `gold/` -- whether a `grain='component'` anchor
is formatted `CMP|SEG|...` or `CMP|LINE|...` post-rename makes no
functional difference to Gold's bridging logic. **Confirmed on a second
real batch (project 216097C, 2026-09-06):** the component anchor format is
still `CMP|SEG|...`, unchanged by the line-grain rename, and `old_uid`/
`new_uid` at component grain are still plain single-instance ids
(`PC-0010`, `PC-10014`, ...) -- so the notebook's original single-row
`.loc[new_uid]` lookup needed no change there. That same batch also
contained a genuine anchor-bucket collision in the wild (one anchor
carrying both a Modified and a New event in one transaction — two GateValves
on one segment), which `apply_silver_cdc_events_tolerant` handled correctly:
flagged as one `CdcAnomaly`, the other 5 events applied cleanly. Locked in
as `tests/test_silver_cdc.py::TestRealComponentGrainBatch`.

`append_gold_cells.py`'s `cdc_to_gold_events` (the notebook glue that turns
real `silver_cdc` rows into `SilverCdcEvent`s) now branches on grain: line
grain calls `aggregate_line_attrs` over the matching `silver_segments`
pieces; component/equipment/connection grain keep the original
single-row `.loc[new_uid]` lookup, since Stage E's anchor-match identity
still re-mints one internal id per version for those object kinds.

## Historical validation: a two-revision Rev C -> Rev D supersession run (2026-09-06)

*(The standalone notebook and mini-reimplementation package this was built
around were removed in the 2026-09-10 cleanup — see "Layout" above. The
finding itself is real and still holds; kept here in condensed form rather
than dropped.)*

A Spark-free rebuild of the real Project-B Unit-22 narrative (issued at Rev
C for HAZOP, re-issued at Rev D for design, every element UID re-minted)
ran Bronze -> Silver Stage E -> Gold **twice** — once for Rev C alone (all
New), then again after Rev D, diffing against Rev C for real — specifically
because the platform's own real-Spark run against this narrative showed
*zero* anchors with more than one row-version (every result was a
first-time New), so a smaller, purpose-built rebuild was needed to exercise
the Modified/supersession path end to end.

**Result:** Rev C ingested as 16 New events (0 anomalies); the real Rev D
vs Rev C diff produced 14 deltas, checked by hand against the narrative's
own manifest and matching it in full except one out-of-scope case — including
the same class of real-world anchor-bucket collision the 2026-09-04 finding
below documents (two GateValves on one segment, flagged as a `CdcAnomaly`,
not a crash). Applying both batches onto the same `gold_rows` produced a
genuine supersession: `EQ|V-2201`'s nozzle count changed 4 -> 5 between
revisions, with `current_truth`/point-in-time truth both verified to return
the correct, different attrs for a closed vs. current row-version. This
was the concrete proof that Gold's bi-temporal supersession path (§3) works
end to end on a real revision pair, not only on synthetic fixtures.

## Resolved: `silver.cdc.aggregate_lines` is real and now wired in directly (2026-09-06)

The widened `ProjectData` GitHub sync (previously excluding `/gold/` and
`/silver/`) made it possible to read the actual Stage E source instead of
inferring its contract from the spec. Three things are now confirmed
straight from `silver/cdc.py`, `silver/cdc_job.py`, and `silver/schema.py`:

- `silver.cdc.aggregate_lines(segments, components, connections)` is real,
  pure Python, importable with zero extra dependencies (only `.quality`,
  itself dependency-free). It groups `silver_segments` pieces by
  `(drawing_number, seg_tag)`, reduces each engineering attribute to a
  sorted `<field>_set` tuple, flags disagreement via an `inconsistent`
  *list* of field names (not a boolean), and — something this project's own
  re-derivation could never produce from a single line's pieces alone —
  computes `neighbour_lines` by walking `silver_connections` across the
  whole drawing.
- `silver/cdc_job.py` reshapes raw Silver table rows before calling it:
  `silver_segments.segment_id` -> `uid`, `silver_components.component_id`
  -> `uid` (keeping `segment_id` as the FK). Real Delta schemas confirmed
  via `silver/schema.py`; the non-line `GRAIN_ID_COL` mapping
  (`component_id`/`equipment_id`/`connection_id`) needed no change.
- `append_gold_cells.py::cdc_to_gold_events` now imports
  `silver.cdc.aggregate_lines` and prefers it whenever it's importable
  (i.e. once this package sits in the same checkout as `silver/`),
  converting its `<field>_set` shape into the same attrs dict
  `SilverCdcEvent` expects. `gold/silver_cdc.py::aggregate_line_attrs`
  remains as the standalone fallback for a `gold_layer`-only checkout (this
  zip doesn't ship `silver/`), and its output was extended with
  `inconsistent_fields` (tuple) and `neighbour_lines` (always `()` here --
  documented as a known gap, since a per-line piece list alone can't route
  neighbours) so both paths hand `SilverCdcEvent.attrs` the same key set
  regardless of which one resolved it. Covered by
  `tests/test_silver_cdc.py::TestAggregateLineAttrs::test_output_shape_matches_the_real_silver_cdc_aggregate_lines_path`.

This retires the `TODO(confirm)` from the 2026-09-05/06 line-grain entry
below -- the "re-house, don't re-derive" swap it called for is done.

**Important:** as of this update, the `ProjectData` repo's own committed
`gold/silver_cdc.py` is still the pre-line-grain version
(`OBJECT_KINDS = ("component", "segment", "equipment", "connection")`) --
none of the line-grain realignment, the anchor-collision tolerant path, or
this `aggregate_lines` wiring has landed there yet. Widening repo access
let this session *read* `silver/`; it did not merge this `gold/` package
back into the repo (no GitHub write access here -- see "Merging into the
`ProjectData` repo" below). The `gold_layer.zip` delivered alongside this
README is the current code; the repo needs it copied in.

## Real-data finding: component anchors can collide (2026-09-04)

Running §5 of the (since-superseded) standalone fixture notebook against a
real Project B Rev C -> Rev D `silver_cdc` batch surfaced a genuine case
`apply_silver_cdc_events`
doesn't handle: `anchor` is a **bucket key** for components
(`silver_layer_spec.md` §3.5 pairs same-class siblings on one segment within
a shared `(segment, component_class)` bucket, not a per-instance string), so
one batch can legitimately carry two simultaneous events for one anchor --
the same non-uniqueness §3f already documents for `seg_tag`, one layer up.

`gold/silver_cdc.py::apply_silver_cdc_events_tolerant` is the fix: it
applies the same `apply_delta` calls but catches an anchor collision (or a
`RetroactiveCorrection`) as a `CdcAnomaly` instead of aborting the whole
batch -- this project's own Stage D "observe and record, don't crash on
dirty data" discipline. `gold_job.build_bitemporal_tables_from_cdc` now uses
it and returns `(tables, anomalies)`. The strict `apply_silver_cdc_events`
is unchanged and still tested, for callers that want one bad event to be a
hard stop.

## Silver Stage E (object-grain CDC)

Silver's Stage E (`silver/cdc.py` / `silver/cdc_job.py` in the `ProjectData`
repo) is now built — see `gold_layer_spec.md`'s implementation-status box.
`gold/silver_cdc.py` is the primary bi-temporal path: it consumes
`silver_cdc`'s New/Modified/Deleted rows directly, keyed on Stage E's own
anchor-match identity. `gold/temporal.py::diff_snapshots` remains only as a
documented fallback for a pre-Stage-E Silver build.

## `medallion_concepts.ipynb`

The one demo notebook (2026-09-10 cleanup consolidated what used to be
several — see "Layout" above). Its first 51 cells are the user's own real
Bronze -> Silver Stage E run, byte-identical to their own base notebook and
already executed against real Project A/B data. From there it exercises
**Gold** end to end, code-generated (not hand-typed) onto that real base:

- **§5** — bi-temporal versioning via the real Spark job (`GoldConfig` /
  `run_gold`), consuming that run's actual `silver_cdc` batch.
- **§6 / §6a / §6b** — the RDF/IDO projection over the real Silver
  snapshot, the oracle-quarantine guard re-asserted and passing, and the
  fluid classification / three directional guards run against real
  connectivity.
- **§7 / §7a** — the SPARQL query surface (real `run_sparql` via rdflib,
  alongside the local pattern-matcher) and a first OWL-RL cross-check via
  `owlrl` (`owl_reasoning.py`).
- **§8** — a real push of all four named graphs into a locally-run Fuseki
  (`fuseki/docker-compose.yml` + `gold/fuseki_bootstrap.py`) — confirmed
  working end to end by the user against their own instance on 2026-09-10
  (see that section above).

§5-§8 are unexecuted **in the copy in this package** — no Spark session,
no `rdflib`/`owlrl`, and no Fuseki instance are reachable from the sandbox
that generated it. Re-run the notebook top to bottom in your own
environment (`jupyter notebook medallion_concepts.ipynb`, or open it
directly in VS Code / JupyterLab) once `requirements.txt` is installed and
`gold/` sits next to `silver/` — cell 0 resolves the import path either
way, from inside `gold_layer/` or from the `ProjectData` repo root once
merged in (see below).

## Merging into the `ProjectData` repo

This session has no GitHub write access, so the `gold/` package was built
and tested standalone. To land it alongside `bronze/` and `silver/`, copy
`gold/`, `tests/`, `fuseki/`, `requirements.txt`, and `medallion_concepts.ipynb`
into the `ProjectData` repo at the same level as `silver/`, and add
`gold_layer_spec.md` under `claude/` alongside `bronze_layer_spec.md` /
`silver_layer_spec.md`.
