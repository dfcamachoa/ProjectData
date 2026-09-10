# Orchestration Layer — Design Specification

**Data Product:** Automatic Pre-commissioning Systemization based on P&ID interoperability data
**Layer:** Orchestration (scheduling, sequencing, metastore concurrency) — cross-cutting; sits above Bronze/Silver/Gold the way `medallion_rdf_ido_strategy_mapping.md` sits above the three layer specs, rather than inside any one of them
**Target runtime:** Apache Airflow (`LocalExecutor`) on the same **local WSL** host as Bronze/Silver/Gold, orchestrating existing CLI/job entrypoints as subprocess tasks — no layer logic is reimplemented here
**Status:** Draft v0.1 — design only; no DAG has been run against a live scheduler or a live Fuseki instance yet (§8)
**Date:** 2026-09-07
**Companions:** `bronze_layer_spec.md` (§8.4 — embedded Derby, WSL-native filesystem, venv-shadowing risk), `silver_layer_spec.md` (CLI stage ordering — reconstruct/assemble/quality/cdc), `gold_layer_spec.md` (§3 — bi-temporal versioning; §4.1a — `graph:masterdata` is current-truth, not the bi-temporal ledger; §4.5 — the oracle firewall; §8 — the real Spark job), `medallion_rdf_ido_strategy_mapping.md` (§10 — suggested phasing this spec's DAG operationalises).
**Companion artefact:** `pidsys_medallion_dag.py` — the DAG file this spec documents the design of.

---

## 0. Verdict up front

Every layer below this one already runs, manually, in the right order, against real data — Bronze's CLI, Silver's four-stage CLI, Gold's Spark job, and a stdlib-only Fuseki client whose request construction is unit-tested but has never made a live call. What's missing is not logic; it's **the thing that runs it on a schedule, in order, with retries, without a person clicking through a notebook** — and the two changes that make that safe: a metastore that tolerates more than one JVM touching it at once, and a hard gate that stops a bad run before it reaches the triplestore. This spec adds exactly those two things and nothing else. No Bronze/Silver/Gold business logic changes; this is a runtime upgrade, the same category of change as Bronze's own "embedded Derby now, Postgres/Unity Catalog later" graduation path (`bronze_layer_spec.md` §9.7) — just arriving on the orchestration side first because scheduling is what forces the concurrency question.

---

## 1. Purpose & scope

**In scope:**
- Sequencing Bronze ingest → Silver's four stages → Gold's run → the Fuseki push, as a single Airflow DAG.
- The metastore change (embedded Derby → Postgres-backed Hive metastore) that scheduling *requires*, not merely benefits from.
- The oracle-quarantine gate as a blocking task between Gold and any Fuseki push.
- Per-graph Fuseki push task design, consistent with `gold_layer_spec.md` §4.1a's current-truth decision.
- Retry, idempotency, and failure-isolation properties the DAG inherits from — and must not violate — each layer's own invariants.

**Out of scope, deliberately:**
- Any change to Bronze/Silver/Gold's own algorithms, schemas, or CLIs. If a task needs a capability a CLI doesn't have (e.g. `gold/cli.py` does not yet exist — §9.1), that is a small, separately-reviewed addition to that layer, not something this spec's DAG works around by reaching into internals.
- A production scheduler topology (`CeleryExecutor`, `KubernetesExecutor`, managed Airflow). This spec is the **PoC runtime**, matching Bronze/Silver/Gold's own "local WSL" scoping (`bronze_layer_spec.md` line 5) — graduation to a cluster scheduler is named as a future step (§9), not designed here.
- Historical/time-travel querying via Fuseki. `gold_layer_spec.md` §4.1a already decided `graph:masterdata` is current-truth only; this spec's job is to orchestrate that decision correctly, not revisit it.

---

## 2. Design decisions this spec resolves

Three things needed a documented decision before the DAG could be trusted, the same way `gold_layer_spec.md` §2 named the decisions the RDF layer needed before it could be built:

1. **Can the PoC metastore (embedded Derby) survive being scheduled?** No — resolved in §3.
2. **What stops a structurally invalid Gold run from reaching Fuseki?** The oracle-quarantine check, promoted from a library function to its own blocking DAG task — resolved in §6.
3. **Does the graph get one push per run, or does history accumulate there?** Already decided in `gold_layer_spec.md` §4.1a (current-truth, wholesale `PUT`); this spec's task design in §7 is the orchestration-side consequence of that decision, not a re-decision of it.

---

## 3. Runtime target: WSL, Airflow, and the metastore concurrency trigger

Bronze's spec already anticipates this moment: embedded Derby's defining constraint is **single-JVM-at-a-time access** (`bronze_layer_spec.md` §8.4, "single-session"). Every layer today runs that way on purpose — one notebook session, one Spark driver, one Derby lock file. Airflow breaks that assumption structurally: even with `LocalExecutor`, each task is its own process, so a scheduled DAG means multiple JVMs *will* attempt to touch the same catalog, sequentially at minimum and potentially overlapping on retry.

**Decision: replace embedded Derby with a real Postgres-backed Hive metastore, in the same WSL environment, before scheduling anything.** This is not a new idea — it is the exact "low-effort upgrade" Bronze's own graduation path names (`bronze_layer_spec.md` §9.7: "table naming, catalog & access... RESOLVED for the PoC; cloud conventions retained as graduation target") — this spec just triggers it earlier than a cluster migration would have, because concurrency, not scale, is the forcing function.

Practical shape:
- Install Postgres in WSL (`apt install postgresql`); create one database for the Hive metastore.
- Point every Spark session's `hive-site.xml` (`javax.jdo.option.ConnectionURL`/driver/credentials) at that Postgres instance instead of Derby's defaults. Table names, schemas, and paths are **unchanged** — `bronze.pid_documents`, `silver.silver_components`, etc. — this is a backend swap, not a schema migration.
- Keep everything on the **WSL-native filesystem** (`~/...`), never `/mnt/c/...` — that constraint from `bronze_layer_spec.md` §8.4 carries over unchanged; Postgres does not relax it.
- Airflow's own metadata database can be a **second database on the same Postgres server** — no need for two Postgres instances, just two databases, keeping the WSL footprint small.
- **Validate the swap in isolation before adding Airflow.** Run the existing notebook cells unchanged against the Postgres-backed metastore first. This isolates "did the metastore swap work" from "did the orchestration work" — the same incremental-validation discipline `medallion_concepts_with_gold.ipynb`'s own build history already follows (confirm each layer works before building the next on top of it).

**Airflow's own installation** goes in a **separate virtualenv** from the pyspark/delta-spark venv Bronze/Silver/Gold already pin. `bronze_layer_spec.md` §8.4 names a specific failure mode — a system Spark install shadowing the venv's pinned pyspark/delta-spark pair — and mixing Airflow's own dependency tree into that same interpreter risks the identical class of failure. Every DAG task therefore activates the pinned venv explicitly (`source <venv>/bin/activate`) rather than assuming Airflow's worker environment already has the right Spark on its path.

---

## 4. DAG structure (revised 2026-09-11 — see §7 for why)

One DAG, `pidsys_medallion_pipeline`, sequencing what already runs manually. **The Gold/Fuseki tail of this table changed from the original draft** — see §7 for the real-Fuseki findings that drove the change:

| Task | Calls | Mirrors |
|---|---|---|
| `ingest_bronze_project_a` / `_b` | `python -m bronze.cli ingest --source-dir ... --table bronze.pid_documents` | `bronze/cli.py` (§2, real CLI) |
| `reconstruct_silver` | `python -m silver.cli reconstruct --bronze-table ...` | `silver/cli.py` Stages A+B |
| `assemble_silver` | `python -m silver.cli assemble ...` | `silver/cli.py` Stage C |
| `quality_silver` | `python -m silver.cli quality ...` | `silver/cli.py` Stage D |
| `cdc_silver` | `python -m silver.cli cdc ...` | `silver/cli.py` Stage E |
| `run_gold_and_push` | in-process: `gold_job.build_rdf_dataset(inputs)` → `oracle_guard.assert_oracle_confined(ds)` → `gold.fuseki_bootstrap.push_dataset(ds, cfg)` | `gold_layer_spec.md` §4.5, §7/§8.2's confirmed-working notebook pattern (**not** `gold/spark_job.py::run_gold`, which remains unexecuted against a live Spark session — §9 item 3 flags this as an open path-choice, not a settled equivalence) |

```
ingest_bronze_a  ─┐
ingest_bronze_b  ─┴─> reconstruct_silver -> assemble_silver -> quality_silver -> cdc_silver
                                                                                      │
                                                                                      v
                                                                          run_gold_and_push
                                                                  (build dataset -> oracle guard -> push_dataset)
```

The DAG collapsed from a five-node Gold/Fuseki tail (`run_gold` → `check_oracle_guard` → three parallel `push_fuseki_*` tasks) to one task, `run_gold_and_push`, because the only pattern actually proven against real data is in-process (§7) — splitting it back into multiple Airflow tasks would require persisting or re-deriving the RDF dataset across a task boundary, which nothing in the validated path currently does. If task-level visibility of the oracle-guard gate is later judged worth that cost, see §9 item 2 for the trade-off.

`graph:oracle` is still **not** pushed on this schedule — `push_dataset` skips any empty graph automatically, and `graph:oracle` is additionally excluded by policy (`gold_layer_spec.md` §4.1's access restriction), not just by happening to be empty.

---

## 5. Task design principles

- **Every Bronze/Silver task is a subprocess CLI call (`BashOperator`)**, unchanged from the original design. **`run_gold_and_push` is the one exception**, run as an in-process Python task (e.g. an Airflow `@task`) rather than a subprocess, because it needs to build the RDF dataset, gate it through the oracle check, and push it to Fuseki all within one Python session — matching the only pattern proven against real data (§7). This task's venv must carry `rdflib`, `owlrl`, `pyspark`, and `delta-spark` together (§8), a broader footprint than the original design assumed.
- **Every task shares one working directory and one metastore.** `silver/cli.py`'s own docstring already states the constraint this generalizes: "Must run in the SAME working directory as the Bronze run so it shares the... metastore + spark-warehouse." Every `BashOperator` in the DAG `cd`s into the same repo root before invoking its CLI.
- **`SPARK_HOME`/`PYTHONPATH` are explicitly cleared per task**, not inherited from Airflow's own environment — directly closing the venv-shadowing risk named in §3.
- **Idempotency is inherited, not re-implemented.** Bronze's ingest is an insert-only `MERGE` on `content_hash` (`bronze_layer_spec.md` §5.1); Silver's CDC stage computes deltas, not accumulations; Gold's `run_gold` rewrites `gold_objects` wholesale each run reading back its own prior state (`gold_layer_spec.md` §8, "Updated 2026-09-06 (cont.)"); Fuseki's push is a `PUT`, replace not append (`gold_layer_spec.md` §4.1a). A task retry after a transient failure is therefore safe by construction across every stage — this spec adds no new idempotency logic, it relies on what's already there.
- **`max_active_runs=1`.** Even on Postgres, overlapping DAG runs against one shared warehouse are an avoidable risk this PoC doesn't need to take on; serialize runs until there's a concrete reason not to.

---

## 6. The oracle-quarantine gate (status changed — see §7)

This section's original design — `check_oracle_guard` as its own visible DAG task, sitting between a `run_gold` task and every `push_fuseki_*` task — assumed the dataset built by Gold could be handed across a task boundary. **That assumption no longer holds cleanly**: §7 documents that the only Gold→Fuseki pattern proven against real data is in-process (`build_rdf_dataset` → `push_dataset`, same Python session), which pulls the oracle check inside `run_gold_and_push` (§4) rather than leaving it as a separate node.

The invariant itself is unchanged: `gold_layer_spec.md` §4.5's `assert_oracle_confined` is a hard-fail check that a mapping bug routing an oracle field outside `graph:oracle` must trip, and it still runs — now as an explicit step inside `run_gold_and_push`, called after `build_rdf_dataset` and strictly before `push_dataset`, so a confinement failure still raises before anything reaches Fuseki. What's lost relative to the original design is the **visible, separate DAG node** — a failure now surfaces as an exception inside one combined task rather than as a distinctly-named upstream task the graph itself points to.

**This trade-off is named as an open decision, not silently accepted** — see §9 item 2. If task-level visibility is later judged worth the engineering cost of persisting the dataset across a boundary (an N-Quads snapshot write, purely to let a separate `check_oracle_guard` task read it back), that is a small, deliberate addition on top of the current proven pattern, not a reversal of it.

---

## 7. Fuseki push design (revised 2026-09-11 — a real Fuseki now exists)

This section originally designed against an unreachable, hypothetical Fuseki. That is no longer true, and the design changes accordingly rather than staying a paper exercise:

**What changed on the Gold side, and why it matters here:**

- **A real Fuseki instance is defined and has been run**: `fuseki/docker-compose.yml` (the `stain/jena-fuseki` image, dataset name `gold`, container name `gold-fuseki`), started via `docker compose up -d` in the user's own environment — not a placeholder any more.
- **Writes require HTTP Basic Auth.** A real `HTTP 401` against the live instance revealed the `stain/jena-fuseki` image requires authenticating as the admin user for dataset writes. `FusekiConfig` now carries `user`/`password` (default `admin`/`admin`, matching `docker-compose.yml`'s `ADMIN_PASSWORD`), and every request `fuseki_client.py` builds carries a stdlib-`base64`-encoded Basic `Authorization` header when credentials are set.
- **The push mechanism is `gold/fuseki_bootstrap.py::push_dataset(ds, cfg)`, not a hand-rolled per-graph loop.** This single call already does what §7's original three-task design was trying to hand-build: it iterates Gold's four named graphs, **skips (and reports) any graph with zero triples** rather than pushing an empty graph, and PUTs the rest. `graph:results` is skipped today simply because nothing writes systemization output back as RDF yet — not a bug, an honestly-named gap.
- **The push has now run against real, project-scale data, not just a fixture**: `graph:masterdata` (21,868 triples) and `graph:refdata` (164 triples) were pushed successfully (`HTTP 200` — a subsequent PUT replacing an existing graph, as opposed to the fixture run's `201 Created` on a first PUT into a fresh dataset; both are documented Graph Store Protocol success codes, not a regression).
- **The Gold→Fuseki hand-off is answered — but in-process, not via a file or an XCom.** The working pattern is `ds = gold_job.build_rdf_dataset(inputs)` immediately followed by `push_dataset(ds, cfg)`, in the same Python session. This resolves §9.2's open question, but resolves it in a way that assumes Gold's build and the Fuseki push happen **together**, not as independently-schedulable Airflow tasks reading a shared artifact.

**Revised task design:**

- **`run_gold` and `push_fuseki` collapse into fewer tasks than originally designed**, or at minimum `run_gold` must itself call `push_dataset` before returning, rather than a separate downstream task rebuilding or re-reading the dataset. The cleanest option, and the one this spec now recommends: **one task, `run_gold_and_push`**, wrapping exactly the working `build_rdf_dataset` → `push_dataset` sequence already proven in the notebook. Splitting Gold's build and the Fuseki push into separate Airflow tasks (the original §4/§7 design) would require re-deriving or persisting `ds` across a task boundary — a real engineering cost the in-process pattern doesn't have, for no proven benefit yet.
- **The per-graph split from the original design (`push_fuseki_refdata`/`_masterdata`/`_results` as three tasks) is superseded.** `push_dataset` already does the right thing per graph internally (skip empty, PUT non-empty) in one call; three separate Airflow tasks calling into the same `ds` would either duplicate that logic or need it split apart, undoing a mechanism that already works correctly as a unit. **Recommendation: one push step, not three** — retry the whole `push_dataset` call on failure, since it is already idempotent per graph (Graph Store `PUT` replaces wholesale) and cheap to repeat in full.
- **Credentials belong in an Airflow Connection (`fuseki_default`), not a bare `FusekiConfig(user="admin", password="admin")` literal in the DAG.** The real deployment's default credential is genuinely `admin`/`admin` — exactly the kind of value that must not sit in version-controlled DAG code even though it's a known default; store it as a Connection password field and read it at task-run time.
- **`check_oracle_guard` must run against the SAME in-memory `ds`** `run_gold_and_push` builds, checked *before* the `push_dataset` call inside that task — not as a separate upstream Airflow task reading a persisted snapshot, since no such snapshot is produced by the proven working pattern. This is a meaningful change from the original design (§6), where the gate was a distinct, visible DAG node; folding it inside one task trades that visibility for matching the mechanism that's actually been validated. If gate-visibility is a hard requirement, the alternative is having `run_gold_and_push` write the N-Quads snapshot itself (a small, deliberate addition, not yet built) purely so a downstream task can re-read it — see §9.2 (revised).
- **`graph:results` will legitimately keep getting skipped by `push_dataset`** until something writes systemization output back as RDF — the DAG should not treat a `graph:results`-skipped log line as a failure; `push_dataset`'s own reporting already distinguishes "skipped, empty" from an error.
- **`graph:oracle` remains excluded from the schedule**, unchanged from the original design (§4) — `push_dataset` will also skip it while it stays empty in the built dataset, but the DAG should not rely on emptiness alone as the reason it's never populated by a scheduled run; the access restriction in `gold_layer_spec.md` §4.1 is the actual reason.

---

## 7a. A known Gold data-quality gap the DAG must not paper over

`gold_layer_spec.md` §10 risk #10 names an open, unresolved gap: **RDF projection has not been realigned to Silver's line-grain rename.** `map_segment` still projects one node per raw `silver_segments` piece, not a collapsed `pidsys:Line` node per `(drawing_number, seg_tag)` — the bi-temporal/CDC path was realigned to line grain (§3.2), the RDF projection was not. This means **a systemization run reading `graph:masterdata` today sees piece-level, not line-level, piping nodes** — a real semantic gap, not a formatting detail.

This is a Gold-layer gap, not an orchestration one, and this spec does not attempt to fix it. But orchestration must not obscure it: a scheduled `push_dataset` call will happily push a `graph:masterdata` built from the un-realigned projection, and a stakeholder or rule author querying Fuseki afterward has no signal from the pipeline itself that line-grain collapse hasn't happened. **Recommendation:** until §10 risk #10 is resolved, the DAG's task documentation (and any run summary/notification built later) should carry an explicit, visible note that `graph:masterdata` is piece-grain, not line-grain, for piping — the same "name the gap loudly rather than let it hide" discipline the other specs already apply to their own open items.

---

## 8. Implementation notes / what has (and hasn't) been validated

Matching the honesty convention every other layer spec's implementation-status box already keeps, **revised** to reflect real progress since this spec's first draft:

- **No DAG in this spec has been run against a live Airflow scheduler.** The companion `pidsys_medallion_dag.py` is syntax-checked (compiles cleanly) but not execution-tested — no Airflow instance is reachable from the environment this was authored in. Unchanged.
- **A live Fuseki round-trip is no longer a gap — it is done and confirmed, in the user's own environment**, against both a fixture and real, project-scale data (§7). This closes one of the two "not validated" items the first draft of this spec carried. The remaining Fuseki-side gap is narrower: a **live Jena rules-engine load** against `jena_rules/classification.rules` has not been attempted — out of scope for `push_dataset`/`fuseki_bootstrap.py`, which only prove the Graph Store Protocol (data-loading) side, not the rules-engine side.
- **`run_gold`'s Spark job (`gold/spark_job.py`) itself remains unexecuted against a live Spark session** as of the latest Gold status entries ("structurally complete, unexecuted here"). Note this is a **different code path** from the notebook pattern that *has* been run for real (`gold_job.build_rdf_dataset` + `push_dataset`, executed directly, not via `spark_job.py`) — the DAG's `run_gold_and_push` task (§7) should be built against whichever of these two paths the team intends as the real production path, and that choice should be made explicit rather than assumed, since only one of the two has actually been proven against real data so far.
- **The Gold-to-Fuseki hand-off is resolved for the notebook/in-process pattern, but this changes the DAG's task boundaries** (§7) — the original three-task, file-or-XCom-mediated design (§9.2, original) no longer matches how Gold and Fuseki actually connect in the validated path. This spec's task design is revised accordingly (§7).
- **Gold's own dependency footprint grew.** As of 2026-09-09, `rdf_mapper.py`, `oracle_guard.py`, `rules_reference.py`, and `sparql_queries.py` all now require `rdflib` (Gold's RDF store graduated from a dependency-free stand-in to genuinely `rdflib`-backed, with `owlrl` added for a first OWL-RL class-hierarchy cross-check). This affects §5's task-design principle that `check_oracle_guard` avoids importing anything requiring `pyspark` — that isolation still holds (nothing in the oracle-guard path needs `pyspark`), but the venv running any Gold-touching task must now have `rdflib`/`owlrl` installed alongside `pyspark`/`delta-spark`, not just the latter pair. Confirm the DAG's target venv (§3) installs all four before relying on this task.

None of this blocks documenting the design; it mirrors exactly how Gold's own spec was written and trusted incrementally, gap by gap, as each was closed for real.

---

## 9. Open decisions for the team (revised)

1. **`gold/cli.py` still does not exist.** Unchanged from the original draft — `gold_job.py`/`gold/spark_job.py::run_gold` have no CLI wrapper. Given §7's revision (one `run_gold_and_push` task, built on the proven `build_rdf_dataset` + `push_dataset` in-process pattern rather than `spark_job.py::run_gold`), the more useful near-term artifact may be a thin CLI around **that** pattern specifically, rather than around `run_gold` — worth deciding which path gets the CLI first.
2. **The Gold → Fuseki dataset hand-off — resolved for in-process use, still open for cross-task use.** The proven pattern is in-process (`build_rdf_dataset` → `push_dataset`, same session) — this answers the original question for a single combined task. It does **not** answer whether `check_oracle_guard` should be a separately-visible DAG node (requiring `run_gold_and_push` to also persist an N-Quads snapshot purely for that downstream task to read) or folded into the one task (§7) at the cost of that visibility. This trade-off is now the open decision, replacing the original three-option list.
3. **Which Gold code path is the DAG's `run_gold_and_push` actually built on** — `gold/spark_job.py::run_gold` (Spark-native, matches Bronze/Silver's execution model, but unexecuted against a live session) or the notebook's proven `gold_job.build_rdf_dataset` + `push_dataset` sequence (validated against real data, but not yet wrapped as a Spark job the way Bronze/Silver's stages are)? These are not obviously the same code path end-to-end, and the DAG should be built against whichever one the team commits to as the real one — not against an assumption that they're interchangeable.
4. **Scheduling cadence.** Unchanged — `@daily` assumed, not confirmed against an actual export cadence.
5. **Whether `graph:oracle` should ever be scheduled.** Unchanged — no, by design, per `gold_layer_spec.md` §4.1's access restriction.
6. **Cluster graduation path.** Unchanged from the original draft.
7. **New: the target venv must carry `rdflib` and `owlrl`, not just `pyspark`/`delta-spark`**, given Gold's 2026-09-09 dependency graduation (§8) — confirm this before the first scheduled run, not after a task fails on a missing import.
8. **New: the RDF projection's line-grain gap (§7a / `gold_layer_spec.md` §10 risk #10) is unresolved.** Decide whether the orchestration layer should visibly flag this on every run (a log line, a run annotation) until Gold resolves it, or whether that's premature given the gap is already documented at the Gold layer.

---

## 10. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Scheduling against embedded Derby** — concurrent JVM access corrupts or deadlocks the single-session metastore the moment two tasks touch it near-simultaneously. | Postgres-backed metastore is a precondition for scheduling, not an optional upgrade (§3); validated standalone before Airflow is introduced. |
| 2 | **Airflow's own dependencies shadow the pinned pyspark/delta-spark pair**, the same failure class `bronze_layer_spec.md` §8.4 names for a system Spark install. | Airflow installed in its own venv; every task explicitly clears `SPARK_HOME`/`PYTHONPATH` and activates the correct venv (§3, §5). |
| 3 | **A structurally invalid Gold run (oracle leakage) reaches Fuseki** because the check is buried inside `run_gold`'s call stack rather than a visible gate. | `check_oracle_guard` promoted to its own blocking DAG task between `run_gold` and every `push_fuseki_*` (§6). |
| 4 | **A failed Fuseki push is mistaken for data loss or corruption.** | By design (§4.1a, §7), a failed `PUT` leaves the *previous* run's consistent snapshot in place — stale, never partial. Document this explicitly so an on-call engineer doesn't treat a failed push as an emergency requiring manual graph repair. |
| 5 | **Scheduled overlap of two DAG runs** against one shared warehouse/metastore, even on Postgres. | `max_active_runs=1` (§5) until there's a concrete, reviewed reason to relax it. |
| 6 | **The Gold→Fuseki dataset hand-off is wired ad hoc**, e.g. a hardcoded path that silently goes stale or is read before it's fully written. | Named as an explicit open decision (§9.2), not assumed; whichever mechanism is chosen should be atomic (a completed-file marker or an XCom, never "read whatever's at this path right now"). |
| 7 | **`graph:oracle` gets pushed on a schedule by accident**, e.g. a future engineer copy-pastes the `_make_push_task` pattern for a fourth graph without re-reading §4.1's access table. | Oracle push explicitly excluded from the DAG and named as requiring its own separately-flagged addition if ever needed (§7, §9.4). |

---

## 11. One-paragraph summary

Orchestration adds nothing to what Bronze, Silver, and Gold already compute — it adds the schedule, the sequencing, and a Postgres-backed Hive metastore in place of embedded Derby, because scheduling is precisely the thing single-session Derby cannot survive. Every Bronze/Silver task calls an existing CLI unchanged, in its own clean venv, sharing one working directory and one metastore, relying entirely on idempotency properties each layer already built and validated for its own reasons. **The Gold/Fuseki tail was revised once real progress overtook the original design (§7):** a live Fuseki now runs (`fuseki/docker-compose.yml`), requires HTTP Basic Auth for writes (a real 401, now fixed), and has been pushed to successfully with both a fixture and real, project-scale data (21,868 masterdata triples) via `gold/fuseki_bootstrap.py::push_dataset` — a single call that already does per-graph replace-and-skip-empty correctly, superseding this spec's original three-task push design. Because the only Gold→Fuseki pattern proven against real data is in-process (`build_rdf_dataset` → oracle guard → `push_dataset`, one Python session), the DAG's Gold tail collapsed from five nodes to one, `run_gold_and_push` — trading the oracle-guard task's original standalone visibility for matching the mechanism that actually works, a trade-off named explicitly (§6, §9) rather than accepted silently. Two things remain genuinely unresolved and are named rather than assumed away: which Gold code path (the proven notebook pattern vs. the still-unexecuted `spark_job.py::run_gold`) the DAG should actually be built against (§9 item 3), and a known, pre-existing Gold-layer gap — RDF projection has not been realigned to line grain, so `graph:masterdata` is piece-grain for piping today — that the orchestration layer must surface rather than silently ship (§7a).
