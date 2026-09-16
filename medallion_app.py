"""
Medallion Layer Walkthrough
Automatic Pre-commissioning Systemization based on P&ID interoperability data (DEXPI / ISO-15926)

A stakeholder- and dev-facing illustration of the Bronze / Silver / Gold concepts.
It does NOT re-run the pipeline; it dramatises the concepts each layer establishes,
using figures taken directly from the layer specs so every number traces to a rule.

Run:  streamlit run medallion_app.py
Deps: streamlit, pandas   (pip install streamlit pandas)

Every claim carries a spec reference: [BR §x] Bronze, [SI §x] Silver, [GO §x] Gold,
[MAP §x] medallion strategy mapping.  These are the same tags the code base carries,
so the app doubles as a communication artifact for both audiences.
"""

import hashlib
import textwrap
from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

# --------------------------------------------------------------------------------------
# Data drawn from the specs (synthetic where a real payload would be needed, exact where
# the spec reports a validated figure). Nothing here re-computes systemization.
# --------------------------------------------------------------------------------------

# Validated Silver counts, from silver_layer_spec §"Implementation status" box.
SILVER_STATS = {
    "DEXPI (Project A)": dict(components=1566, valves=190, segments=713, connections=1385,
                              equipment=3, fmt="DEXPI", origin="SPPID",
                              flow=dict(forward=631, reverse=632, none=121, both=1)),
    "PostProc (Project B)": dict(components=1987, valves=232, segments=807, connections=1396,
                                 equipment=9, fmt="PostProc", origin="SPPID",
                                 flow=dict(forward=616, reverse=636, none=135, both=9)),
}

# Gold RDL/PLM crosswalk result on Project A, from gold_layer_spec §8 / §11.
RDL_CROSSWALK = dict(covered=187, gap=15, boundary_forming=3, confirmed=1)

# A small synthetic "raw payload" so Bronze's content-hash / dedup story is tangible.
SAMPLE_DEXPI = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <PlantModel>
      <PlantInformation OriginatingSystem="SPPID" Application="Dexpi"
                        SchemaVersion="4.0.0"/>
      <Drawing Name="215777C-PID-0001" Revision="C">
        <GenericAttributes>
          <GenericAttribute Name="OperationCenterDocNo" Value="215777C-PID-0001"/>
          <GenericAttribute Name="RevRow1No"   Value="C"/>
          <GenericAttribute Name="RevRow1Date" Value="14SEP26"/>
        </GenericAttributes>
      </Drawing>
      <PipingNetworkSegment>
        <GenericAttribute Name="Z_TurnOverSystemNumber" Value="14-ORACLE-ONLY"/>
        <!-- ...many more elements... -->
      </PipingNetworkSegment>
    </PlantModel>
""")


@dataclass
class Layer:
    key: str
    name: str
    one_liner: str
    color: str


LAYERS = [
    Layer("bronze", "Bronze", "Land every file as-is, once per version. Never interpret.", "#B5763A"),
    Layer("silver", "Silver", "Parse, reconstruct topology, quality-gate, detect change.", "#8A9199"),
    Layer("gold",   "Gold",   "Version in bi-temporal history and project to RDF/IDO.", "#C6A15B"),
]


# --------------------------------------------------------------------------------------
# Page config + house style
# --------------------------------------------------------------------------------------

st.set_page_config(page_title="Medallion Walkthrough — P&ID Systemization",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
  .block-container { padding-top: 2.2rem; max-width: 1150px; }
  h1, h2, h3 { letter-spacing: -0.01em; }
  .lede { font-size: 1.05rem; color: #4a4a4a; line-height: 1.5; max-width: 68ch; }
  .specref { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
             font-size: 0.78rem; color: #7a6a3a; background: #f7f2e6;
             padding: 1px 6px; border-radius: 4px; }
  .notbox { border-left: 3px solid #c0392b; background: #fdf3f2; padding: 0.6rem 0.9rem;
            border-radius: 4px; color: #7d2620; font-size: 0.9rem; }
  .isbox  { border-left: 3px solid #2d7a46; background: #f1f8f3; padding: 0.6rem 0.9rem;
            border-radius: 4px; color: #235c37; font-size: 0.9rem; }
  .pill { display:inline-block; padding: 2px 10px; border-radius: 999px;
          font-size: 0.75rem; font-weight: 600; margin-right: 6px; }
  .muted { color:#8a8a8a; font-size: 0.85rem; }
</style>
""", unsafe_allow_html=True)


def specref(*tags):
    return " ".join(f'<span class="specref">{t}</span>' for t in tags)


# --------------------------------------------------------------------------------------
# Sidebar navigation
# --------------------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### P&ID → Systemization")
    st.markdown('<span class="muted">Medallion concept walkthrough</span>',
                unsafe_allow_html=True)
    st.divider()
    page = st.radio(
        "Layer",
        ["Overview", "Bronze — raw landing", "Silver — canonical model",
         "Gold — history & semantics", "The through-line"],
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown('<span class="muted">Every figure and boundary shown here is drawn from '
                'the layer specs. The app illustrates concepts — it does not re-run the '
                'pipeline.</span>', unsafe_allow_html=True)


# --------------------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------------------

def overview():
    st.title("From a P&ID export to a governed data product")
    st.markdown(
        '<p class="lede">The systemization engine already works — it turns interoperability '
        'exports into computed commissioning systems at ~97% agreement with turnover data. '
        'What it lacked was a platform underneath it: durability, quality gates, history, and '
        'a query surface. The medallion layers add exactly that, and nothing more.</p>',
        unsafe_allow_html=True)
    st.markdown(specref("MAP §0", "ARCH §4"), unsafe_allow_html=True)
    st.write("")

    cols = st.columns(3)
    for col, layer in zip(cols, LAYERS):
        with col:
            st.markdown(
                f'<span class="pill" style="background:{layer.color}22;color:{layer.color}">'
                f'{layer.name}</span>', unsafe_allow_html=True)
            st.markdown(f"**{layer.one_liner}**")
    st.write("")
    st.divider()

    st.subheader("What each layer adds — and refuses")
    grid = pd.DataFrame([
        ["Bronze", "Durable, immutable raw landing; audit trail; replayability",
         "Any parsing, reconstruction, quality gate, or CDC", "BR §1.1 / §1.2"],
        ["Silver", "Attribute shred, topology reconstruction, quality punch-list, object CDC",
         "Any use-case concept (systems, SUP-as-commissioning grouping)", "SI §1.2 / §8.3"],
        ["Gold", "Bi-temporal history + RDF/IDO projection + node-local classification",
         "The global connected-fragment walk — stays in Silver/Python", "GO §1.2 / §5.1"],
    ], columns=["Layer", "Adds", "Refuses", "Spec"])
    st.dataframe(grid, use_container_width=True, hide_index=True)

    st.markdown(
        '<div class="isbox"><b>The discipline that makes it work:</b> each layer keeps a hard '
        'boundary. Bronze never interprets; Silver never groups; Gold never computes '
        'systems. Blur any one and the provenance story the program rests on breaks.</div>',
        unsafe_allow_html=True)


# --------------------------------------------------------------------------------------
# Bronze
# --------------------------------------------------------------------------------------

def bronze():
    st.title("Bronze — the immutable landing zone")
    st.markdown(
        '<p class="lede">Bronze stores each source file verbatim, once per distinct '
        'byte-version, with just enough ingestion metadata to replay and audit every '
        'layer above it. Its single interpretive act is a shallow header read — for '
        'identity, versioning, and routing.</p>', unsafe_allow_html=True)
    st.markdown(specref("BR §0", "BR §1.1"), unsafe_allow_html=True)
    st.divider()

    left, right = st.columns([1, 1])

    with left:
        st.subheader("Content-addressed identity")
        st.markdown("Paste or edit a raw payload. The whole-file **sha-256** is the version "
                    "key — identical bytes in, identical hash out, forever. A byte-identical "
                    "re-ingest is silently skipped; nothing is overwritten.")
        st.markdown(specref("BR §5.2", "BR §5.3"), unsafe_allow_html=True)
        payload = st.text_area("Raw source XML", SAMPLE_DEXPI, height=260,
                               label_visibility="collapsed")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        st.code(f"content_hash = sha256:{digest}", language="text")
        st.markdown(f'<span class="muted">file_size_bytes = {len(payload.encode())} · '
                    f'stored as binary (no derivable content_text) '
                    f'— real drawings reach ~13 MB</span>', unsafe_allow_html=True)
        st.markdown(specref("BR §3.3"), unsafe_allow_html=True)

    with right:
        st.subheader("The shallow header read")
        st.markdown("Bronze extracts a fixed, whitelisted set of fields — no deeper. The "
                    "format discriminator is **not** the originating system (both formats "
                    "export as `SPPID`) but `PlantInformation/@Application`.")
        st.markdown(specref("BR §4", "BR §9.9"), unsafe_allow_html=True)

        app_attr = "Dexpi" if 'Application="Dexpi"' in payload else "(absent)"
        fmt = "DEXPI" if app_attr == "Dexpi" else "PostProc / UNKNOWN"
        header = pd.DataFrame([
            ["originating_system", "SPPID", "lineage only — not a discriminator"],
            ["PlantInformation/@Application", app_attr, "the real format signal"],
            ["source_format (routed)", fmt, "chosen here, re-used by Silver — never re-sniffed"],
            ["document_number", "215777C-PID-0001", "OperationCenterDocNo (A) / Drawing@Name (B)"],
            ["project_code", "215777C", "leading token of the doc number"],
            ["drawing_revision", "C · 14SEP26", "valid-time axis source for Gold"],
        ], columns=["Field", "Value", "Note"])
        st.dataframe(header, use_container_width=True, hide_index=True)

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="isbox"><b>Bronze IS</b><br>Append-only, immutable, one row '
                    'per byte-version. Format is a column, not a second table. Flags, '
                    'never rejects — only a physically unreadable file fails to land.</div>',
                    unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="notbox"><b>Bronze is NOT</b><br>No GenericAttribute shred, '
                    'no topology reconstruction, no quality gate, no CDC. The moment it '
                    'shreds attributes it takes on a schema and stops being raw.</div>',
                    unsafe_allow_html=True)


# --------------------------------------------------------------------------------------
# Silver
# --------------------------------------------------------------------------------------

def silver():
    st.title("Silver — the canonical, use-case-neutral plant model")
    st.markdown(
        '<p class="lede">Silver turns raw XML into the one plant model every rule package '
        'reads. It is re-housing, not inventing: the validated attribute shred and the '
        'topology-first reconstruction are wrapped as per-drawing UDFs behind a Spark stage '
        'that parallelises over files — never re-expressed as row-wise SQL.</p>',
        unsafe_allow_html=True)
    st.markdown(specref("SI §10", "MAP §2"), unsafe_allow_html=True)
    st.divider()

    st.subheader("Why reconstruction is the crown jewel")
    st.markdown("Raw `<Connection>` records are incomplete — an SPPID adapter wires only each "
                "segment's two endpoints, so inline valves go missing. A boundary walk on the "
                "raw graph would miss almost every valve. Reconstruction repairs the topology "
                "first (skeleton from shared endpoints; inline order by centerline arc-length) "
                "and yields a **directed** graph.")
    st.markdown(specref("MAP §2", "ARCH §1"), unsafe_allow_html=True)

    m = st.columns(4)
    m[0].metric("Inline valves — raw", "0 / 35")
    m[1].metric("Inline valves — reconstructed", "35 / 35", "+35")
    m[2].metric("Distributed fluid AG", "135 / 135")
    m[3].metric("Graph becomes", "directed", "flow sense")
    st.markdown('<span class="muted">Figures from MAP §2, measured on a real steam sheet. '
                'The three directional guards depend on this direction existing.</span>',
                unsafe_allow_html=True)

    st.divider()
    st.subheader("Format parity — one code path, one schema")
    st.markdown("Both formats coexist in one Silver run with identical schema — the "
                "interoperability promise made concrete. Note **all four `flow_sense` states "
                "appear in both**, which is what justifies an enum over a boolean.")
    st.markdown(specref("SI §4", "SI #7"), unsafe_allow_html=True)

    rows = []
    for name, s in SILVER_STATS.items():
        rows.append([name, s["components"], s["valves"], s["segments"],
                     s["connections"], s["equipment"]])
    parity = pd.DataFrame(rows, columns=["Source", "Components", "Valves",
                                         "Segments", "Connections", "Equipment"])
    st.dataframe(parity, use_container_width=True, hide_index=True)

    pick = st.selectbox("Show flow_sense distribution for",
                        list(SILVER_STATS.keys()))
    flow = SILVER_STATS[pick]["flow"]
    fcols = st.columns(4)
    for col, (state, n) in zip(fcols, flow.items()):
        col.metric(f"flow_sense = {state}", n)
    st.markdown('<span class="muted">A bare boolean cannot distinguish <b>none</b> from '
                '<b>both</b> — and the guards read both. Hence the enum.</span>',
                unsafe_allow_html=True)

    st.divider()
    st.subheader("The oracle firewall")
    st.markdown("The reconstructed graph carries the source turnover assignment "
                "(`src_turnover`) as **quarantined lineage** — populated, but on no computed "
                "column. Nothing in Silver or above computes from it. That separation is what "
                "keeps the ~97% agreement a real cross-check, not a circular one. Reading it "
                "in a compute path is a hard-fail structural invariant.")
    st.markdown(specref("SI §5", "SI §9 risk 3"), unsafe_allow_html=True)

    st.subheader("Quality — flag and flow, don't reject")
    st.markdown("The gate is a declarative expectation suite as data. Every data-quality "
                "expectation flags-and-flows into the `silver_quality` punch-list; the object "
                "keeps a `quality_gate` enum (`clean` / `flagged` / `quarantined`). The only "
                "hard fails are the two structural invariants — this preserves honest, "
                "partial results.")
    st.markdown(specref("SI Stage D", "SI §3.4"), unsafe_allow_html=True)
    q = pd.DataFrame([
        ["segment completeness (fluid / class / diameter)", "flag → flow", "review signal"],
        ["unknown fluid / unit", "flag → flow", "reference-backed, skips if refdata absent"],
        ["seg_tag anchor-collision", "flag → flow", "confirmed real: 3 segments → one tag"],
        ["oracle leak", "HARD FAIL", "structural invariant"],
        ["unflagged derived edge", "HARD FAIL", "structural invariant"],
    ], columns=["Expectation", "Policy", "Note"])
    st.dataframe(q, use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------------------
# Gold
# --------------------------------------------------------------------------------------

def gold():
    st.title("Gold — bi-temporal history and semantic projection")
    st.markdown(
        '<p class="lede">Gold is where the two genuinely new, genuinely hard pieces live: '
        'bi-temporal history and the RDF/IDO serving layer. It computes provenance and time '
        'and projects meaning — but it does not compute commissioning systems. The global '
        'walk stays where it is validated, in Silver/Python.</p>', unsafe_allow_html=True)
    st.markdown(specref("GO §0", "GO §1.2"), unsafe_allow_html=True)
    st.divider()

    st.subheader("Two independent time axes")
    st.markdown("A single `validFrom`/`validTo` pair collapses two real axes into one. Gold "
                "keeps them apart: **valid time** (when the engineering reality held, from the "
                "drawing revision) and **transaction time** (when the system knew it). Nothing "
                "is physically deleted — *never delete, close the interval*.")
    st.markdown(specref("GO §3", "MAP §4"), unsafe_allow_html=True)

    demo = pd.DataFrame([
        ["V-1401 valve", "Rev B", "2026-05-01 → 2026-09-14", "2026-05-03 → 2026-09-15", "superseded"],
        ["V-1401 valve", "Rev C", "2026-09-14 → open", "2026-09-15 → open", "current truth"],
        ["P-2201 pump",  "Rev B", "2026-05-01 → open", "2026-05-03 → open", "unchanged"],
    ], columns=["Object", "Revision", "Valid time", "Transaction time", "State"])
    st.dataframe(demo, use_container_width=True, hide_index=True)
    st.markdown('<span class="muted">A retroactive correction raises rather than guesses; '
                'ordinary supersession and correction are kept distinct.</span>',
                unsafe_allow_html=True)

    st.divider()
    st.subheader("RDF/IDO projection — honest about the ontology")
    st.markdown("The current objects are projected to triples across four governed named "
                "graphs. IDO is used honestly: one real foundational class is asserted "
                "(`ido:PhysicalObject`); domain terms IDO doesn't ship are subclassed under "
                "it via `pidsys:` predicates with an explicit `rdlUriPending` marker rather "
                "than an invented `ido:FunctionalObject`.")
    st.markdown(specref("GO §4", "MAP §5"), unsafe_allow_html=True)

    ng = st.columns(4)
    for col, (g, d) in zip(ng, [
        ("graph:masterdata", "the plant, line-grain"),
        ("graph:refdata", "fluids, boundaries, tags"),
        ("graph:oracle", "turnover — never read by a rule"),
        ("graph:results", "classification predicates"),
    ]):
        col.markdown(f"**{g}**")
        col.markdown(f'<span class="muted">{d}</span>', unsafe_allow_html=True)

    st.write("")
    st.markdown("Connections are **reified** and carry `derived` plus an oriented `flowsTo`, "
                "so inferred topology can never masquerade as source truth.")
    st.markdown(specref("GO §4.2", "GO §9 risk 4"), unsafe_allow_html=True)

    st.divider()
    st.subheader("Node-local classification only")
    st.markdown("The Jena / rule engine gets the **local** half of the hybrid: fluid "
                "self-ownership, boundary-role lookup, and the three directional guards — read "
                "from refdata + masterdata, never from the oracle. The global "
                "connected-fragment partition is deliberately out of scope; a forward-chainer "
                "is awkward at 'transitive closure that stops at a node with a property', "
                "which is exactly what a boundary walk is.")
    st.markdown(specref("GO §5", "GO §5.1"), unsafe_allow_html=True)

    st.divider()
    st.subheader("RDL / PLM crosswalk — closing rdlUriPending")
    st.markdown("Real result on Project A: most RDS codes already resolve to the published PLM "
                "library; the gap is small, ranked, and human-reviewable. Genuine library "
                "gaps get a project-owned `TEN_RDL` extension namespace — kept structurally "
                "distinct from the pinned library, decided per candidate.")
    st.markdown(specref("GO §8", "GO §11"), unsafe_allow_html=True)
    rc = st.columns(4)
    rc[0].metric("RDS codes covered", RDL_CROSSWALK["covered"])
    rc[1].metric("Residual gap", RDL_CROSSWALK["gap"])
    rc[2].metric("Of which boundary-forming", RDL_CROSSWALK["boundary_forming"])
    rc[3].metric("Confirmed so far", RDL_CROSSWALK["confirmed"])


# --------------------------------------------------------------------------------------
# Through-line
# --------------------------------------------------------------------------------------

def throughline():
    st.title("The through-line — one file, three layers")
    st.markdown(
        '<p class="lede">Follow a single drawing through the stack. The point of the medallion '
        'shape is that every result above can be replayed, audited, and time-travelled against '
        'the exact bytes that produced it.</p>', unsafe_allow_html=True)
    st.divider()

    steps = [
        ("Bronze", "#B5763A",
         "215777C-PID-0001 lands verbatim. sha-256 becomes its version key; the header read "
         "records format=DEXPI, revision C/14SEP26, project 215777C. No interpretation.",
         "BR §5.2 / §4"),
        ("Silver", "#8A9199",
         "Adapter chosen from the stored source_format (not re-sniffed). Attributes shredded; "
         "topology reconstructed (valves 0/35 → 35/35); connections reified with flow_sense; "
         "quality punch-list written; oracle quarantined.",
         "SI §6 / §5"),
        ("Gold", "#C6A15B",
         "Object-grain deltas become bi-temporal rows (valid + transaction time). Current "
         "truth is projected to RDF/IDO across four named graphs; node-local rules classify; "
         "RDS codes resolve against the PLM crosswalk.",
         "GO §3 / §4 / §5"),
        ("Consumer", "#5B7A8C",
         "The systemization walk (Silver/Python) and, next, Test Packages read the same "
         "governed surface with different cut rules. One plant modelled once, many consumers.",
         "GO §9 / ARCH §6"),
    ]
    for i, (name, color, body, ref) in enumerate(steps):
        c = st.columns([0.06, 0.94])
        c[0].markdown(f'<div style="width:14px;height:14px;border-radius:50%;'
                      f'background:{color};margin-top:6px"></div>', unsafe_allow_html=True)
        with c[1]:
            st.markdown(f"**{name}**")
            st.markdown(body)
            st.markdown(specref(ref), unsafe_allow_html=True)
        if i < len(steps) - 1:
            st.markdown('<div style="border-left:2px solid #e0e0e0;height:14px;'
                        'margin-left:6px"></div>', unsafe_allow_html=True)

    st.divider()
    st.markdown('<div class="isbox"><b>Next step in the program:</b> the OWL/IDO semantic '
                'approach explores solving the same rule-application problem declaratively on '
                'base ontologies — the Gold layer is exactly where that work already begins, '
                'with the global walk held back in Python until a validated Jena-only path '
                'exists.</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------------------

PAGES = {
    "Overview": overview,
    "Bronze — raw landing": bronze,
    "Silver — canonical model": silver,
    "Gold — history & semantics": gold,
    "The through-line": throughline,
}
PAGES[page]()
