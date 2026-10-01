
# Jena rules engine — live deployment design (2026-09-23)

**Status: CLOSED — full parity on real Project A data, 2026-09-23 (run 3), and the output is materialised into `graph:inferred` with PROV (run 4).** This closes the open item in gold_layer_spec §5.6 / §8.2: `gold/jena_rules/classification.rules` had previously been checked only by reading it against the Python rules, never executed. The spec's §5.6 and §5.7 carry the current-state description; this doc keeps the run-by-run record.

## Result (run 3, Project A, 4 DEXPI drawings)

Input: 23,876 masterdata triples and 164 refdata triples. Every rule-body predicate has the same count in `ds` and in Jena: `ido:connectedTo` 2,776, `pidsys:flowsTo` 1,265, `pidsys:partOf` 1,939, `pidsys:fluidCode` 752, `rdf:type` 3,907. No Python-rule input is unread by the rules, no rule predicate is without data, and no oracle predicate reached the reasoner.

| Rule                 | Python vs Jena                          |
| -------------------- | --------------------------------------- |
| Fluid classification | 39 / 39 agree                           |
| Flare guard          | 162 / 162 agree, 0 either side only     |
| Supply-tie-in guard  | 1,263 / 1,263 agree, 0 either side only |
| Relief attribution   | 39 / 39 valves agree, 0 multi-candidate |

## Design

- **Separate read-only Fuseki service `gold-rules`** (`fuseki/gold-rules.ttl`): in-memory `ja:MemoryModel` base → `ja:InfModel` with `GenericRuleReasoner`, `ja:rulesFrom file:///fuseki/rules/classification.rules`. Mounted via three read-only volumes in `fuseki/docker-compose.yml`. Query endpoints are declared in the explicit `fuseki:endpoint`/`fuseki:operation` form (`/sparql`, `/query`, and the dataset URL itself).
- **Input = exactly two files**, `fuseki/rules-input/{masterdata,refdata}.nt`, exported from the notebook's `ds` as sorted (canonical) N-Triples. Their content is therefore hashable. graph:oracle is never in the reasoner's input (firewall by construction).
- **Why not an InfModel over TDB2 `gold`:** the reasoner would not see `push_dataset`'s drop-and-replace PUTs (it would need a `rebind()` that HTTP can't trigger), and TDB2's strict transactions don't reliably pass through InfModel/UnionModel. Loading at startup + `docker restart` recomputes all inferences from scratch.

## Parity harness — `gold/jena_parity.py` (notebook §8b)

`export_rule_input(ds)` → `restart_fuseki()` → `run_parity(ds)`. The preflight checks:

- input triple counts are equal in `ds` and in Jena, for every predicate a rule body reads and every `vocab` constant the Python rules read;
- no Python-rule input is missing from the rule bodies, and no rule-body predicate has zero triples;
- the relief role URI is present;
- no oracle predicate reached the reasoner;
- the exported input is the same `ds` the Python side read (sha256).

Each report also records `input_fingerprint`: the sha256 of both input files and the rules file. The report is therefore a verdict about one specific input, which the materialisation gate relies on.

Then it runs four diffs: fluid classification, flare guard, supply-tie-in guard and relief attribution. A relief valve with 2+ protected neighbours is reported as "multi-candidate" (Python returns the first one it finds), not a failure. `restart_fuseki` retries only while Fuseki is starting up. An HTTP error from a running Fuseki stops it at once and prints `diagnose()`: the Fuseki version, each dataset's endpoints, and filtered container logs. `python -m gold.jena_parity --fixture` is the smoke test. `tests/test_jena_parity.py` (15 tests) covers the diff and parse logic, and checks the real rules file for vocabulary drift.

**Ongoing discipline** (strategy mapping §10 step 4): re-run §8b, then §8c, after any change to `classification.rules`, `rules_reference.py`, `vocab.py` or `rdf_mapper.py`. `PARITY PASS` is the gate.

## Materialisation — `gold/inferred_graph.py` (notebook §8c)

- **`materialise_inferences(parity, cfg)`** is refused unless the parity report passed **and** its `input_fingerprint` equals what `gold-rules` has loaded now. If it proceeds:
  1. It CONSTRUCTs exactly the predicates the rule heads derive; they are parsed from the rules file, not hard-coded.
  2. It adds PROV:
     - the run (`prov:Activity`), with its times, agent and parity status;
     - the input snapshots and rules file, identified by hash;
     - `derivedByRule` per derived predicate.
  3. It re-checks for oracle predicates, then PUTs `graph:inferred` into the `gold` dataset.
- **`load_inferred(cfg)`** returns plain sets and dicts keyed by Silver component id for the systemization run. **`inferred_is_current(cfg, ds)`** compares the recorded hashes with the `ds` about to be walked and the current rules.
- `tests/test_inferred_graph.py` (15 tests): rule-head parsing against the real rules file, N-Triples validity of the PROV block, every gate refusal, freshness.

## Rule fixes (Python is the reference)

1. `steamCondensateCategory`: regex `'Steam|Condensate'` → `'.*(Steam|Condensate).*'` (Jena documents `regex()` as matching the whole lexical form).
2. `reliefAttribution`: `regex(?role,'relief')` on a URI node → the exact role URI `pidsys:boundary_role/relief`.
3. `flareGuardSkip`: removed the fragment's own-fluid lookup and the `notEqual` check (the Python `flare_guard` reads only the neighbour's fluid).
4. Guards also assert `skipFlareSink` / `skipSupplyTieIn` alongside `skipAsConsumerSignal`, so the two guards can be compared separately.
5. Adjacency: `pidsys:isConnectedTo` → `ido:connectedTo` (run 2 finding, below).

## Live-run history

- **Run 1: HTTP 405 on `/gold-rules/sparql`.** The legacy `fuseki:serviceQuery` form did not register a query operation on the user's stain/jena-fuseki. Fixed by switching to explicit endpoints.
- **Run 2: classification 39/39, but every guard and relief rule matched nothing in Jena.** Root cause: vocabulary drift. The projection had moved adjacency to IDO's native `ido:connectedTo` (`specs/semantics/pidsys_extension.ttl`), but the rules still read `pidsys:isConnectedTo`, which had 0 triples. The first preflight hard-coded the same stale URI, so it reported 0 = 0 as a match. The preflight now derives its predicate set from `vocab` and from the rules file.
- **Run 3: PARITY PASS** (above).
- **Run 4 (`run/rules/20260923T222757Z`): `graph:inferred` materialised, HTTP 201.** Contents: 2,897 derived triples plus 59 PROV triples, from inputs masterdata `143a05b1565c…`, refdata `2b4580d0e29b…` and rules `f419d761acf6…`.

  - `skipFlareSink` 162, `skipSupplyTieIn` 1,263.
  - `skipAsConsumerSignal` 1,425: exactly their sum, since the two guards fire on opposite flow directions and never overlap.
  - `protectedBy` 39, `selfOwningClass` 8.

  The read-back through `load_inferred` matches the parity counts exactly, and `inferred_is_current` is True for masterdata, refdata and rules.

## Open follow-ups

- **Consume it.** The Silver/Python walk does not yet read `graph:inferred`. Next step: check `inferred_is_current`, then use the materialised guard signals in place of the walk's own guard evaluations.
- **Steam/condensate check.** `selfOwningClass` is only flare (and process) on Project A: no fluid classifies as steam/condensate. Both engines agree, so this reflects the Fluid sheet: no Category=Utility row has a Subcategory containing "Steam" or "Condensate". Worth confirming against the plant's actual steam services.
- **Move `GRAPH_INFERRED` into `gold/vocab.py`** (currently in `gold/inferred_graph.py`). Keep it out of `fuseki_bootstrap.NAMED_GRAPHS`; it has its own writer.
- **`nan` boundary role.** An empty Role cell in the Boundary sheet is read by pandas as NaN and minted as `pidsys:boundary_role/nan`. Filter it in the §6 refdata cell (`dropna(subset=["Role"])`).
- **Project B.** Parity and materialisation have been shown on Project A (DEXPI) only. Project B (PostProc) goes through the same harness unchanged.
