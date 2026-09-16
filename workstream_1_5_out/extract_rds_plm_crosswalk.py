#!/usr/bin/env python3
"""Extract the REAL, already-published RDS<->PLM SKOS crosswalk out of
equipment.rdf -- Workstream 1.5's first asset (ido_semantic_mapping_spec.md
§4.2, §4.6): "mostly the pre-published 208 SKOS mappings".

Usage:
    python extract_rds_plm_crosswalk.py [path/to/equipment.rdf] [-o OUTDIR]

Defaults: ./equipment.rdf, output directory ./workstream_1_5_out/.

What it does
------------
Parses equipment.rdf with the standard-library `xml.etree.ElementTree` --
deliberately NOT rdflib, so this one preprocessing step has zero extra
dependencies and runs even before your environment is fully set up. It
walks every `skos:exactMatch` / `skos:closeMatch` / `skos:relatedMatch`
element in the file, direction-agnostic (some real ontologies assert the
mapping on the PLM class pointing at the RDS code; some assert it the
other way around -- this script checks both and classifies each side by
URI substring: `rds.posccaesar.org/ontology/plm/` = the PLM side,
`data.posccaesar.org/rdl/` = the RDS side).

Two outputs land in --outdir:

  rds_plm_crosswalk.json
      The exact/close matches only, ready to hand straight to
      `GoldInputs(rds_plm_crosswalk=...)` as-is: each row is
      {"rds_uri", "plm_uri", "match_type": "exact"|"close", "plm_label"}
      (`plm_label` is extra context for your own review, not read by
      `gold/rdf_mapper.py::map_rds_plm_crosswalk`, which only reads the
      first three keys).

  excluded_related_matches.json
      Every `skos:relatedMatch` found, kept OUT of the crosswalk above on
      purpose. `gold/rules_reference.py::load_rds_plm_crosswalk` (as of
      risk #18) only recognises "exact"/"close" -- a "related" match_type
      passed through today would silently be written as if it were a
      closeMatch (`rdf_mapper.map_rds_plm_crosswalk`'s `... else
      SKOS_CLOSE_MATCH` fallback), which is wrong: SKOS relatedMatch is
      the loosest, non-hierarchical "these are thematically associated"
      relation -- not something to auto-assert as a component's class.
      These are written out separately so nothing is lost, but they are
      NOT loaded into Gold until `gold/` grows real "related" handling
      (a natural small follow-up, not done here since it wasn't asked
      for and changes shipped code, not just data).

Self-verification
------------------
ido_semantic_mapping_spec.md §4.2 already reports the expected shape of
this exact file: "1,057 references across 208 distinct RDS codes...
345 closeMatch, 22 relatedMatch, 1 exactMatch". This script recomputes
those same three counts from the real file and prints a clear PASS/CHECK
line against them -- if your local equipment.rdf has since been updated
(a newer owl:versionInfo than the 0.9.1 this session verified against)
the counts may legitimately differ; a mismatch is a prompt to look, not
necessarily a bug in either the file or this script.
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
SKOS_NS = "http://www.w3.org/2004/02/skos/core#"
PLM_MARK = "rds.posccaesar.org/ontology/plm/"
RDS_MARK = "data.posccaesar.org/rdl/"

MATCH_TAGS = {
    f"{{{SKOS_NS}}}exactMatch": "exact",
    f"{{{SKOS_NS}}}closeMatch": "close",
    f"{{{SKOS_NS}}}relatedMatch": "related",
}

EXPECTED = {"exact": 1, "close": 345, "related": 22, "distinct_rds_codes": 208}


def _classify(uri: str) -> str:
    if PLM_MARK in uri:
        return "plm"
    if RDS_MARK in uri:
        return "rds"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rdf_path", nargs="?", default="equipment.rdf")
    ap.add_argument("-o", "--outdir", default="workstream_1_5_out")
    args = ap.parse_args()

    rdf_path = Path(args.rdf_path)
    if not rdf_path.exists():
        print(f"error: {rdf_path} not found", file=sys.stderr)
        return 1
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    tree = ET.parse(rdf_path)
    root = tree.getroot()
    parent_of = {child: parent for parent in root.iter() for child in parent}

    # rdfs:label per subject, for readable output (best-effort -- absence
    # of a label is never a reason to drop a real mapping).
    label_of: dict[str, str] = {}
    for elem in root.iter():
        about = elem.get(f"{{{RDF_NS}}}about")
        if about is None:
            continue
        for child in elem:
            if child.tag == f"{{{RDFS_NS}}}label" and child.text:
                label_of[about] = child.text.strip()

    crosswalk_rows = []
    related_rows = []
    unrecognized = []
    counts = Counter()

    for elem in root.iter():
        match_type = MATCH_TAGS.get(elem.tag)
        if match_type is None:
            continue
        target = elem.get(f"{{{RDF_NS}}}resource")
        if target is None:
            continue  # a skos:*Match with inline content rather than rdf:resource -- not expected, skip safely
        parent = parent_of.get(elem)
        subject = parent.get(f"{{{RDF_NS}}}about") if parent is not None else None
        if subject is None:
            continue
        counts[match_type] += 1

        s_kind, t_kind = _classify(subject), _classify(target)
        if s_kind == "plm" and t_kind == "rds":
            plm_uri, rds_uri = subject, target
        elif s_kind == "rds" and t_kind == "plm":
            plm_uri, rds_uri = target, subject
        else:
            unrecognized.append({"subject": subject, "predicate": match_type, "object": target})
            continue

        row = {"rds_uri": rds_uri, "plm_uri": plm_uri, "match_type": match_type, "plm_label": label_of.get(plm_uri)}
        (related_rows if match_type == "related" else crosswalk_rows).append(row)

    (outdir / "rds_plm_crosswalk.json").write_text(json.dumps(crosswalk_rows, indent=2), encoding="utf-8")
    (outdir / "excluded_related_matches.json").write_text(json.dumps(related_rows, indent=2), encoding="utf-8")
    if unrecognized:
        (outdir / "unrecognized_matches.json").write_text(json.dumps(unrecognized, indent=2), encoding="utf-8")

    distinct_rds = len({r["rds_uri"] for r in crosswalk_rows + related_rows})

    print(f"Parsed {rdf_path}")
    print(f"  exactMatch  : {counts.get('exact', 0):4d}  (expected {EXPECTED['exact']})")
    print(f"  closeMatch  : {counts.get('close', 0):4d}  (expected {EXPECTED['close']})")
    print(f"  relatedMatch: {counts.get('related', 0):4d}  (expected {EXPECTED['related']}, "
          f"excluded from the crosswalk -- see excluded_related_matches.json)")
    print(f"  distinct RDS codes touched: {distinct_rds}  (expected {EXPECTED['distinct_rds_codes']})")
    if unrecognized:
        print(f"  WARNING: {len(unrecognized)} match triple(s) had neither side recognisable as PLM/RDS -- "
              f"see unrecognized_matches.json, inspect before trusting the counts above.")

    all_match = (
        counts.get("exact", 0) == EXPECTED["exact"]
        and counts.get("close", 0) == EXPECTED["close"]
        and counts.get("related", 0) == EXPECTED["related"]
        and distinct_rds == EXPECTED["distinct_rds_codes"]
        and not unrecognized
    )
    print("PASS -- matches ido_semantic_mapping_spec.md §4.2 exactly." if all_match else
          "CHECK -- counts differ from §4.2's reported numbers (see note above); "
          "inspect before trusting this output as-is.")
    print(f"\nWrote {len(crosswalk_rows)} exact/close rows -> {outdir / 'rds_plm_crosswalk.json'}")
    print(f"Wrote {len(related_rows)} excluded relatedMatch rows -> {outdir / 'excluded_related_matches.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
