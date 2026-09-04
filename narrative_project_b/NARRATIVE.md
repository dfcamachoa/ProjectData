# Stage-E CDC narrative — Project B, Unit 22 (Steam & BFW), Rev C → Rev D

Synthetic INGR/ISO-15926 **PostProc** P&ID exports that reproduce a real EPC
event: a set of P&IDs is **issued at Rev C for HAZOP**, and after the HAZOP a
**Rev D is re-issued for design**. Between the two issues, engineering changed
some lines, valves, instruments and equipment — and, as SmartPlant always does on
re-export, **every element's persistent UID is re-minted**. The point of the
exercise is that the platform must see *past* the churn to the *real* change.

Two drawings, two revisions:

```
rev1_C/  216097C-A22-PID-0021-0015-001.xml   BFW SUPPLY TO STEAM DRUM      (Rev C)
         216097C-A22-PID-0021-0016-001.xml   STEAM GENERATION & BLOWDOWN   (Rev C)
rev2_D/  216097C-A22-PID-0021-0015-001.xml   BFW SUPPLY TO STEAM DRUM      (Rev D)
         216097C-A22-PID-0021-0016-001.xml   STEAM GENERATION & BLOWDOWN   (Rev D)
```

Every element ID differs between Rev C and Rev D (the re-export churn). Nothing but
the anchors (line number, equipment tag, the `(line, class)` bucket) carries over.

---

## The engineering change, by discipline

### Piping

| Line | Rev C → Rev D | CDC verdict |
|---|---|---|
| `WBF-2215101` 2″ BFW pump discharge | **Insulation thickness 25 → 50 mm** after heat-loss review (same class, same purpose) | **Segment Modified** (`insul_thick`) |
| `WBF-2215104` ¾″ recirc | **Line removed** — recirc rerouted | **Segment Deleted** |
| `WBF-2215106` ¾″ warm-up bypass | **New line** around the level-control valve | **Segment New** |
| `WBF-2215102`, `WBF-2215103` | Unchanged, **re-drawn** (new UIDs) | *nothing* — churn ignored |
| `SM-2203910` 2″ safety vent (Dwg 16) | **New line** to atmosphere for the relief valve | **Segment New** |
| `SM-2203906`, `SC-2204101` (Dwg 16) | Unchanged, **re-drawn** | *nothing* |

### Instrumentation & valves (piping components)

| Item | Rev C → Rev D | CDC verdict |
|---|---|---|
| Check valve on `WBF-2215101` | **Removed** — consolidated to one common check valve downstream | **Component Deleted** (CheckValve) |
| Isolation gate on `WBF-2215101` | **Added** for maintenance isolation | **Component New** (GateValve) |
| Gate valve retained on `WBF-2215101` | Unchanged, but its neighbour (the check valve) was removed | **Component Modified** — *connectivity* (see note) |
| Manual globe on `WBF-2215102` | **Replaced by a level control valve** (LV-2201) | **Deleted** (GlobeValve) + **New** (ControlValve) |
| `PT-2201` on `WBF-2215102` | **New pressure transmitter** (HAZOP overpressure indication) | **Component New** (PressureTransmitter) |
| `PSV-2201` on `SM-2203910` | **New relief valve** on the new safety vent | **Component New** (SafetyValveOrFitting) |
| Gate on `WBF-2215103`, valves on Dwg 16 | Unchanged, **re-drawn** | *nothing* |

> **Connectivity note.** A valve that did not itself change is still flagged
> *Modified — connectivity* when the valving next to it changes (here: the check
> valve was removed beside the retained gate). That is deliberate — a line's
> configuration changing around a component is a real engineering change, and the
> hash keys adjacency on the neighbour's **anchor**, never its UID, so a mere
> re-draw of the neighbour would *not* trip it.

### Mechanical (equipment)

| Equipment | Rev C → Rev D | CDC verdict |
|---|---|---|
| Steam Drum `V-2201` | **New BFW distributor nozzle** added (4 → 5 nozzles) | **Equipment Modified** (`nozzle_count`) |
| BFW Pump `P-2201B` | **New spare pump** for N+1 availability | **Equipment New** |
| BFW Pump `P-2201A` | Unchanged, **re-drawn** | *nothing* |
| Blowdown Drum `V-2202` (Dwg 16) | Unchanged, **re-drawn** | *nothing* |

---

## What the platform reports (`silver_cdc`)

**15 real deltas, 0 false positives**, even though *every* element UID changed:

- **Drawing 0015:** 3 segment deltas, 8 component deltas, 2 equipment deltas
- **Drawing 0016:** 1 segment delta, 1 component delta, 0 equipment deltas

And the headline for the audience: **Drawing 0016 was fully re-issued** — new title
block, new revision, every element re-drawn — yet the platform flags **only the one
new PSV vent line**. No engineer has to eyeball a re-issued sheet to find what
actually moved.

Why this holds (spec §3.5): identity is an **anchor-match** (equipment tag /
`(drawing, seg tag)` / `(line, component-class)` bucket), never the volatile UID.
Three separated hashes keep the books honest — `content_hash_eng` (attrs +
neighbour **anchors**) decides Modified; `content_hash_audit` (adds the UID + the
quarantined turnover fields) keeps the re-mint and any turnover reassignment
*visible to audit* but *inert for engineering change*.

---

## Why it matters downstream — the Gold layer

Each `silver_cdc` delta is exactly one **bi-temporal interval event** Gold consumes:

- **New** → open a validity interval `[validFrom = Rev D issue date, validTo = ∞)`.
- **Deleted** → close the prior interval (`validTo = Rev D issue date`) — never a
  hard delete, so the Rev-C plant is still queryable.
- **Modified** → close the old version's interval and open the new one.

That gives an engineering audience three things a pile of re-issued XMLs cannot:

1. **A defensible "current truth"** (objects with `validTo = ∞`) *and* the ability
   to ask *"what was the plant at Rev C, for HAZOP?"* vs *"at Rev D, for design?"* —
   a real dual timeline (valid-time from the revision, transaction-time from when
   the platform ingested it).
2. **Automatic scoping of change-driven work.** The New/Deleted valves and lines
   are an **MTO delta** for procurement; the new `PSV-2201` is a new **ITR / test
   record** and a relief-scenario to register; the new drum nozzle is a **vessel
   datasheet / mechanical interface** change; the removed check valve is a
   **redline** to confirm. Precommissioning systemization only needs to re-run for
   the drawings that actually changed.
3. **An audit trail that proves a re-issue was clean.** "Rev D touched 30 elements;
   14 were pure re-draws; here are the 15 engineering changes and who they affect."

---

## How to run it through the platform

Ingest the two revisions as two Bronze versions of the same drawings (Bronze is
append-only and versions by content hash; Stage E diffs the two most recent
versions per drawing, ordered by ingest time):

```python
# in the medallion_concepts notebook (named-metastore build), after Cell 1 & 2:
from bronze.notebook import ingest_folder
from silver.notebook import reconstruct, assemble, quality, changes

# --- Rev C (issue for HAZOP) ---
ingest_folder(spark, source_dir="rev1_C", table_name=bronze_table)
reconstruct(spark, bronze_table=bronze_table, silver_schema="silver")

# --- Rev D (re-issue for design) — appends the second version ---
ingest_folder(spark, source_dir="rev2_D", table_name=bronze_table)
reconstruct(spark, bronze_table=bronze_table, silver_schema="silver")

# --- Stage E: the change report ---
print(changes(spark, bronze_table=bronze_table, silver_schema="silver"))
spark.table("silver.silver_cdc").orderBy("grain", "change_type").show(60, False)
```

`changes(...)` should report `drawings_with_two_versions: 2` and the delta counts
above. Filter `silver_cdc` by `grain` / `change_type` to drive each discipline's
worklist.

> These are **synthetic, scrubbed** fixtures — the structure is illustrative
> (fluid codes WBF/SM/SC, Unit 22, classes B242A/D341H/G400S, insulation H/N).
> Confirm names against the real Project-B exports before using the numbers.
