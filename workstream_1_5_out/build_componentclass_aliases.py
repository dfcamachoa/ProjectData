#!/usr/bin/env python3
"""Build the PostProc ComponentClass-string -> PLM alias table -- Workstream
1.5's second asset (ido_semantic_mapping_spec.md §4.4, §4.6).

Usage:
    python build_componentclass_aliases.py [path/to/equipment.rdf] [-o OUTDIR]
                                            [--components-file FILE]

Defaults: ./equipment.rdf, output directory ./workstream_1_5_out/.

What it does
------------
§4.6 names three concrete PostProc renames confirmed against real data --
"PostProc's naming for classes PLM has": `ConcentricDiameterChange` (74
real items) -> PLM `Pipe Reducer`, `PipingNetworkBranch` (30 items) -> PLM
`Pipe Tee`, `Flange` (48 items) -> PLM `Pipe Flange`. Those three are
SEEDED here verbatim -- nothing invented -- and each target label is
resolved to its real `plm_uri` by parsing equipment.rdf's own rdfs:label
triples (same ElementTree approach as extract_rds_plm_crosswalk.py, zero
extra dependencies). A seed whose target label isn't found in your copy
of equipment.rdf is reported, NOT silently guessed at.

Optional: --components-file (a JSON list of real component_class strings,
or a JSON list of {"component_class": ...} dicts, or a CSV with a
component_class column) additionally tries a HIGH-CONFIDENCE auto-match
for every string in that file not already covered by the seed table:
normalize both sides (lowercase, strip spaces/underscores, split
CamelCase into words) and accept only an exact normalized match against
a PLM rdfs:label -- e.g. a literal "Actuator" or "Solenoid" or "Orifice"
string matching a PLM class labelled the same thing needs exactly this
kind of entry too (`resolve_rdl_uri`'s label bridge only ever consults
rows actually present in this table -- an unaliased exact-string match
is NOT resolved automatically). Anything that doesn't normalize-match
exactly is left OUT of the alias table and written instead to
`alias_candidates_for_review.json` as a fuzzy suggestion (best
normalized-prefix/substring overlap) for a human to confirm -- never
auto-applied, per §4.5's "label matching fails silently" discipline.

Output
------
componentclass_plm_aliases.json -- ready for
`GoldInputs(componentclass_plm_aliases=...)` as-is: each row is
{"component_class", "plm_uri", "plm_label", "source"} ("source" is
"spec-confirmed" for the three §4.6 seeds, "auto-exact-label-match" for
anything --components-file adds; only the first two keys are read by
`gold/rdf_mapper.py::map_componentclass_plm_aliases`).

alias_candidates_for_review.json -- present only when --components-file
surfaces class strings that need a human judgement call, not an exact
label match. Never fed into Gold by this script.
"""
import argparse
import csv
import difflib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"

# §4.6, verbatim: "PostProc's naming for classes PLM has" -- confirmed
# against real Project-B counts (74 / 30 / 48 items respectively).
SEED_ALIASES = [
    {"component_class": "ConcentricDiameterChange", "target_plm_label": "Pipe Reducer", "real_item_count": 74},
    {"component_class": "PipingNetworkBranch", "target_plm_label": "Pipe Tee", "real_item_count": 30},
    {"component_class": "Flange", "target_plm_label": "Pipe Flange", "real_item_count": 48},
]


def _normalize(label: str) -> str:
    # CamelCase -> words, then fold to a comparable lowercase token stream.
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label)
    return re.sub(r"[^a-z0-9]+", "", spaced.lower())


def _load_plm_labels(rdf_path: Path) -> dict:
    """{normalized_label: (plm_uri, real_label)} from every rdfs:label in
    the file -- direction-agnostic w.r.t. how the class is declared
    (owl:Class / rdf:Description), same parsing approach as
    extract_rds_plm_crosswalk.py."""
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


def _load_component_classes(path: Path) -> list:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if "component_class" not in (reader.fieldnames or []):
                raise ValueError(f"{path}: no 'component_class' column (found {reader.fieldnames})")
            return sorted({row["component_class"] for row in reader if row.get("component_class")})
    data = json.loads(path.read_text(encoding="utf-8"))
    out = set()
    for item in data:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, dict) and item.get("component_class"):
            out.add(item["component_class"])
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rdf_path", nargs="?", default="equipment.rdf")
    ap.add_argument("-o", "--outdir", default="workstream_1_5_out")
    ap.add_argument("--components-file", default=None,
                     help="optional JSON list / CSV of real component_class strings to also auto-match")
    args = ap.parse_args()

    rdf_path = Path(args.rdf_path)
    if not rdf_path.exists():
        print(f"error: {rdf_path} not found", file=sys.stderr)
        return 1
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    plm_labels = _load_plm_labels(rdf_path)
    print(f"Parsed {len(plm_labels)} labelled PLM classes from {rdf_path}")

    aliases = []
    seeded_classes = set()
    for seed in SEED_ALIASES:
        norm = _normalize(seed["target_plm_label"])
        hit = plm_labels.get(norm)
        seeded_classes.add(seed["component_class"])
        if hit is None:
            print(f"  MISSING: seed alias {seed['component_class']!r} -> {seed['target_plm_label']!r} -- "
                  f"no PLM class with that label found in {rdf_path}. Not written -- check the label spelling "
                  f"or your equipment.rdf version before retrying.", file=sys.stderr)
            continue
        plm_uri, real_label = hit
        aliases.append({
            "component_class": seed["component_class"],
            "plm_uri": plm_uri,
            "plm_label": real_label,
            "source": "spec-confirmed",
            "note": f"ido_semantic_mapping_spec.md §4.6 -- {seed['real_item_count']} real Project-B items",
        })
        print(f"  seed OK: {seed['component_class']!r} -> {real_label!r} ({plm_uri})")

    candidates = []
    if args.components_file:
        classes = _load_component_classes(Path(args.components_file))
        print(f"\n{len(classes)} distinct component_class strings in {args.components_file}")
        for cc in classes:
            if cc in seeded_classes:
                continue
            norm = _normalize(cc)
            hit = plm_labels.get(norm)
            if hit:
                plm_uri, real_label = hit
                aliases.append({
                    "component_class": cc, "plm_uri": plm_uri, "plm_label": real_label,
                    "source": "auto-exact-label-match",
                    "note": "component_class string normalizes identically to this PLM label",
                })
                print(f"  auto-match: {cc!r} == PLM label {real_label!r}")
            else:
                # best-effort fuzzy suggestion only, for a human to look at -- sequence
                # similarity on the normalized strings, nothing cleverer than that.
                best_norm, best_score = None, 0.0
                for candidate_norm in plm_labels:
                    score = difflib.SequenceMatcher(None, norm, candidate_norm).ratio()
                    if score > best_score:
                        best_norm, best_score = candidate_norm, score
                if best_norm is not None and best_score >= 0.5:
                    plm_uri, real_label = plm_labels[best_norm]
                    candidates.append({
                        "component_class": cc, "suggested_plm_uri": plm_uri, "suggested_plm_label": real_label,
                        "similarity": round(best_score, 2),
                        "confidence_note": "fuzzy string similarity only -- NOT auto-applied, needs human review",
                    })

    (outdir / "componentclass_plm_aliases.json").write_text(json.dumps(aliases, indent=2), encoding="utf-8")
    print(f"\nWrote {len(aliases)} alias row(s) -> {outdir / 'componentclass_plm_aliases.json'}")
    if candidates:
        (outdir / "alias_candidates_for_review.json").write_text(json.dumps(candidates, indent=2), encoding="utf-8")
        print(f"Wrote {len(candidates)} unresolved candidate(s) for human review -> "
              f"{outdir / 'alias_candidates_for_review.json'} (NOT loaded into Gold)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
