#!/usr/bin/env python3
"""Find real Cause-C candidates -- RDS URIs your own DEXPI data actually
uses that aren't among the pre-published PLM crosswalk -- and rank
candidate PLM targets for human review. This is Workstream 1.5's third,
hardest asset (ido_semantic_mapping_spec.md §4.4: "~102 RDS-with-no-
PLM-target codes, boundary-forming first"; §6.2 open decision #3).

This script deliberately does NOT invent the ~102 mappings. Only your
real Project A data can say which RDS codes actually appear unmapped in
YOUR drawings, and only a person can confirm that a given component
really is, say, a Pipe Flange Spacer -- getting this wrong feeds a wrong
class into pre-commissioning system-boundary determination. What this
script does do: the mechanical part -- find the real gap, rank real
candidates by string similarity to real PLM labels, sort boundary-
forming classes to the top exactly as §4.4 asks -- and hand you a review
sheet, never a fait accompli.

Usage:
    python find_cause_c_candidates.py path/to/equipment.rdf \\
        --crosswalk workstream_1_5_out/rds_plm_crosswalk.json \\
        --components-file real_dexpi_components.json \\
        [--boundary-file boundary_rows.json] \\
        [-o OUTDIR]

--components-file: real silver_components-shaped rows (JSON list of
    dicts with "component_class" and "component_class_uri", or a CSV
    with those two columns). Only rows where component_class_uri is
    present are relevant here (Cause C is a DEXPI-only phenomenon --
    PostProc never carries a component_class_uri at all, per §4.1).

--boundary-file: optional JSON list of {"component_class", "role"} rows
    (the same shape `gold/rdf_mapper.py::map_boundary_sets` reads) so
    isolation/positive/relief/trap classes are correctly identified and
    sorted first, per §4.4's own prioritisation.

Output: cause_c_candidates.csv in --outdir, columns:
    rds_uri, occurrence_count, is_boundary_forming, component_class,
    suggested_plm_uri, suggested_plm_label, similarity, confirmed_plm_uri

`confirmed_plm_uri` is left BLANK for you to fill in after checking each
row (or to leave blank / write "REJECT" if no suggestion is right). Once
reviewed, turn your confirmed rows into crosswalk entries yourself --
append {"rds_uri": ..., "plm_uri": <your confirmed_plm_uri>, "match_type":
"close"} to rds_plm_crosswalk.json for each confirmed row (never
"exact" -- these are curated, not PCA-published, so they get the same
closeMatch confidence tier and the same boundary-forming review gate as
any other close match, exactly per §4.5's discipline). This script does
not do that merge for you on purpose -- it stops right at the point that
needs a human decision.
"""
import argparse
import csv
import difflib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"

BOUNDARY_ROLES = ("isolation", "positive", "relief", "trap")


def _normalize(label: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label)
    return re.sub(r"[^a-z0-9]+", "", spaced.lower())


def _load_plm_labels(rdf_path: Path) -> dict:
    root = ET.parse(rdf_path).getroot()
    labels: dict = {}
    for elem in root.iter():
        about = elem.get(f"{{{RDF_NS}}}about")
        if about is None:
            continue
        for child in elem:
            if child.tag == f"{{{RDFS_NS}}}label" and child.text:
                labels[_normalize(child.text)] = (about, child.text.strip())
    return labels


def _load_rows(path: Path) -> list:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    return json.loads(path.read_text(encoding="utf-8"))


def _best_match(norm: str, plm_labels: dict):
    best_norm, best_score = None, 0.0
    for candidate_norm in plm_labels:
        score = difflib.SequenceMatcher(None, norm, candidate_norm).ratio()
        if score > best_score:
            best_norm, best_score = candidate_norm, score
    if best_norm is None:
        return None, None, 0.0
    plm_uri, real_label = plm_labels[best_norm]
    return plm_uri, real_label, best_score


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rdf_path")
    ap.add_argument("--crosswalk", required=True, help="rds_plm_crosswalk.json from extract_rds_plm_crosswalk.py")
    ap.add_argument("--components-file", required=True)
    ap.add_argument("--boundary-file", default=None)
    ap.add_argument("-o", "--outdir", default="workstream_1_5_out")
    args = ap.parse_args()

    rdf_path = Path(args.rdf_path)
    if not rdf_path.exists():
        print(f"error: {rdf_path} not found", file=sys.stderr)
        return 1
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    plm_labels = _load_plm_labels(rdf_path)
    covered = {row["rds_uri"] for row in json.loads(Path(args.crosswalk).read_text(encoding="utf-8"))}
    print(f"{len(covered)} RDS codes already covered by the published crosswalk")

    boundary_classes = set()
    if args.boundary_file:
        for row in _load_rows(Path(args.boundary_file)):
            if row.get("role") in BOUNDARY_ROLES and row.get("component_class"):
                boundary_classes.add(row["component_class"])
        print(f"{len(boundary_classes)} boundary-forming component_class value(s) loaded from {args.boundary_file}")

    components = _load_rows(Path(args.components_file))
    gap_counter: Counter = Counter()
    class_by_rds: dict = {}
    for row in components:
        rds_uri = row.get("component_class_uri")
        cc = row.get("component_class")
        if not rds_uri or rds_uri in covered:
            continue
        gap_counter[rds_uri] += 1
        class_by_rds.setdefault(rds_uri, cc)

    print(f"{len(gap_counter)} distinct RDS URI(s) used in your real data but NOT in the published crosswalk "
          f"(this is your real Cause-C count -- ido_semantic_mapping_spec.md §4.4 reports 102 for its own "
          f"validation drawing; yours may differ)")

    rows_out = []
    for rds_uri, count in gap_counter.items():
        cc = class_by_rds.get(rds_uri)
        is_boundary = cc in boundary_classes
        plm_uri, plm_label, score = (None, None, 0.0)
        if cc:
            plm_uri, plm_label, score = _best_match(_normalize(cc), plm_labels)
        rows_out.append({
            "rds_uri": rds_uri,
            "occurrence_count": count,
            "is_boundary_forming": is_boundary,
            "component_class": cc or "",
            "suggested_plm_uri": plm_uri or "",
            "suggested_plm_label": plm_label or "",
            "similarity": round(score, 2),
            "confirmed_plm_uri": "",
        })

    # boundary-forming first (§4.4's own priority), then by real impact (occurrence count desc)
    rows_out.sort(key=lambda r: (not r["is_boundary_forming"], -r["occurrence_count"]))

    out_path = outdir / "cause_c_candidates.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()) if rows_out else
                                 ["rds_uri", "occurrence_count", "is_boundary_forming", "component_class",
                                  "suggested_plm_uri", "suggested_plm_label", "similarity", "confirmed_plm_uri"])
        writer.writeheader()
        writer.writerows(rows_out)

    n_boundary = sum(1 for r in rows_out if r["is_boundary_forming"])
    print(f"\nWrote {len(rows_out)} candidate row(s) ({n_boundary} boundary-forming, sorted first) -> {out_path}")
    print("Review each row, fill in confirmed_plm_uri (or leave blank / write REJECT), then hand-add the "
          "confirmed ones to rds_plm_crosswalk.json yourself as {\"rds_uri\":..., \"plm_uri\": <your value>, "
          "\"match_type\": \"close\"} -- see this script's own docstring for why \"close\" and not \"exact\".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
