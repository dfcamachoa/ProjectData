# Workstream 1.5 — building the two real crosswalk assets

`ido_semantic_mapping_spec.md` §4.6 names two assets Workstream 1.5 still
owed: the DEXPI `RDS → PLM` crosswalk and the PostProc
`ComponentClass-string → PLM-label` alias table. These three scripts build
them from your real `equipment.rdf` and your real Silver data — none of
them invent a mapping; every row is either parsed straight out of the
real ontology file or flagged for you to confirm by hand.

Run them in this order, all from inside your `ProjectData` checkout (next
to `specs/semantics/equipment.rdf`):

## 1. `extract_rds_plm_crosswalk.py` — the pre-published 208

```
python extract_rds_plm_crosswalk.py specs/semantics/equipment.rdf
```

Parses every real `skos:exactMatch`/`closeMatch` in the file (zero extra
dependencies — plain `xml.etree.ElementTree`) into
`workstream_1_5_out/rds_plm_crosswalk.json`, ready to hand straight to
`GoldInputs(rds_plm_crosswalk=...)`. Self-checks its own counts against
`ido_semantic_mapping_spec.md` §4.2's already-published numbers (1 exact,
345 close, 208 distinct RDS codes) and tells you plainly if your copy of
the file doesn't match — that's a prompt to look, not necessarily a bug.

`skos:relatedMatch` (22 of them) is deliberately excluded, not silently
mixed in — see the script's own docstring for why (today's `map_rds_
plm_crosswalk` would mistype any non-"exact" match_type as "close",
which is the wrong confidence tier for SKOS's loosest relation).

## 2. `build_componentclass_aliases.py` — the PostProc alias table

```
python build_componentclass_aliases.py specs/semantics/equipment.rdf \
    --components-file your_real_postproc_component_classes.json
```

Seeds the table with §4.6's three confirmed renames
(`ConcentricDiameterChange→Pipe Reducer`, `PipingNetworkBranch→Pipe Tee`,
`Flange→Pipe Flange` — 74/30/48 real Project-B items respectively),
resolving each target label to its real `plm_uri`. `--components-file`
is optional — point it at a JSON list (or CSV with a `component_class`
column) of the real `component_class` strings your Silver data actually
carries, and it also auto-matches anything that normalizes identically
to a real PLM label (e.g. a bare `"Actuator"` string against a PLM class
literally labelled `Actuator`) — those need a table row too, not just a
label that happens to match, since the label bridge only ever consults
rows present in this table. Anything that doesn't match exactly goes to
`alias_candidates_for_review.json` as a fuzzy suggestion, never applied
automatically. Writes `workstream_1_5_out/componentclass_plm_aliases.json`,
ready for `GoldInputs(componentclass_plm_aliases=...)`.

## 3. `find_cause_c_candidates.py` — the hard ~102, for review only

```
python find_cause_c_candidates.py specs/semantics/equipment.rdf \
    --crosswalk workstream_1_5_out/rds_plm_crosswalk.json \
    --components-file your_real_dexpi_components.json \
    --boundary-file your_real_boundary_rows.json
```

Finds every RDS URI your real DEXPI data actually uses that isn't in
step 1's published crosswalk (your real Cause-C set — §4.4 reports 102
for its own validation drawing; yours may be a different number), ranks
a candidate PLM target for each by string similarity to the real labels,
and sorts boundary-forming classes first (per §4.4's own priority —
`PipeFlangeSpacer` first). Writes `cause_c_candidates.csv` with a blank
`confirmed_plm_uri` column for you to fill in after checking each
suggestion. **This script never writes to the crosswalk itself** — once
you've reviewed a row, add it to `rds_plm_crosswalk.json` yourself as
`{"rds_uri": ..., "plm_uri": <your confirmed value>, "match_type":
"close"}` (never `"exact"` — these are curated, not PCA-published, so
they get the same review-gate treatment as any other close match).

## Why the split

Steps 1 and 2 are safe to trust as-is: real parsing of a real published
file, self-verified against numbers `ido_semantic_mapping_spec.md`
already reports. Step 3 stops one line short of a final answer on
purpose — confirming that a specific real component really is a
`Pipe Flange Spacer` (or whatever) is an engineering judgement call this
tooling has no business making silently for a system feeding real
pre-commissioning boundary determination.
