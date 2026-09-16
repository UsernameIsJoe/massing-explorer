# Workflow

Updated 16 Sep 2026. What happens when you run a brief (chat or studio UI).
Constitution: [BLUEPRINT-search.md](BLUEPRINT-search.md). Concept: [CONCEPT.md](CONCEPT.md).
Studio steps: [UI.md](UI.md).

---

## Control loop

```
                  USER BRIEF
                      │
                      ▼
           ┌────────────────────┐
           │ Intent Interpreter │
           │  reading + brief   │
           └─────────┬──────────┘
                     │
            requirements
            limitations
            preferences
            uncertainties
                     │
                     ▼
          STRUCTURED DESIGN STATE
                  S = (P, T, V, G, D)
                     │
                     ▼
        ┌────────────────────────┐
        │  LLM STRATEGY PLANNER  │
        │  ≤5 typed design moves │
        └───────────┬────────────┘
                    │
             semantic actions
                    │
                    ▼
        ┌────────────────────────┐
        │ CONSTRAINT / MASSING   │
        │ ENGINE + realize(s)    │
        │ solver, layout,        │
        │ allocate, pairing, GSF │
        └───────────┬────────────┘
                    │
                    ▼
              PERFORMANCE
                    │
                    ▼
           DIVERSE DESIGN ARCHIVE
             /       |       \
            /        |        \
       COVER        LEARN      REFINE
         │            │          │
         │      user comparison  │
         └────────────┴──────────┘
                    │
                    ▼
            next search actions
```

COVER explores ideas over a **growing P pool** (not a frozen shortlist of five).
Illegal samples get a **REPAIR** projection onto the same idea. Near-feasible
leftovers sit on a frontier. MCTS starts from legal elites plus frontier
samples; structural moves (e.g. `APPLY_PARTITION`) are scored as leaves so a
regrouping can become a legal archive cell. LEARN A/B stays among schemes that
already fit (at most five questions; prefer visibly different pairs).

Every evaluation fills feet through **`realize(s)`** — strategy first, widths
second. Stale mass-width locks are cleared when P changes so a new organization
is not judged with the previous org’s feet.

MCTS sits around the **planner + engine** loop. Nearby width is a local step
from the current plate. Bayesian optimization spends ~10–15 sequential high-EI
evaluations on **legal** actions. Neither invents a foot target. REPAIR owns
“this edge is over — move the plate,” not BO.

---

## First brief (default COVER)

1. **Ingest.** Excel/CSV on the study (chat load, or UI upload).
2. **Interpret.** `brief.py` + `reading.py` split the message into requirements,
   limitations, and preferences. Numbers must land on a lever or trigger an ask.
3. **Lock what was stated.** Named masses, keep-together / apart / alone, mass
   count, pairing, floor pins. A length cap is a filter, not a target.
4. **COVER** (`explore/cover.py` + `explore/p_pool.py`).
   - Seed an initial band of partitions from CSP (family-aware shortlist so
     school-shaped bars are not lost in hundreds of flat-ranked partitions).
   - Stratified joint samples: P × story patterns × drawable T × loading ×
     envelope × plate profile. Soft `preferred_stories` biases story order.
   - Adaptive budget: start ~40 → +10/+20 while discovering → stagnant stop →
     hard cap ~100–120. P pool may expand mid-run when outcomes repeat or gaps
     remain (status: feasible / unresolved / impossible on the **organization**).
5. **REPAIR** — nearest same-idea legal twin via typed actions + `realize(s)`.
6. **Planner** — ≤5 typed actions; engine apply/reject.
7. **MCTS** — multi-root from elites (+ frontier); structural APPLY scored as a
   leaf; local partition catalog preferred; clear widths on APPLY.
8. **Bayesian optimization** — sequential EI on legal actions.
9. **REFINE** — local neighbors among legal lineages; LEARN taste steers effort.
10. **LEARN** — pending A/B when two diverse legal elites exist (max 5).
11. **DIAGNOSE** if still no (or very few) legal cells with a large frontier.
12. **Keep one drawing**; report legal / frontier / empty-cell reasons.

**Chat entry:** `apply_parsed_brief` → `run_search(mode="cover")`.  
**UI entry:** upload + Generate → same controller path (`ui_app.py`).

---

## Follow-up turns

| User says / does | What happens |
|------------------|--------------|
| Click scheme A or B (UI) or type `A` / `B` (chat) | LEARN comparison. Bradley–Terry on measured traits. Intermediate picks do not re-run full REFINE; a short refine runs when LEARN completes. |
| New limit, pin, pair, or resize | Recorded. Do not regroup a stated must. |
| Explicit regroup | Human-controlled; required wings stay required. |
| Click a pool / candidate card | Instant envelope preview, then full program-colored mesh when apply finishes. |

Modes (`cover` / `learn` / `refine`) are modes over one archive, not Stage 1→2→3.

---

## Semantic actions

The LLM does not emit geometry. It emits moves. The engine applies or rejects.

| Action | Meaning |
|--------|---------|
| `COLOCATE` / `KEEP_APART` / `SPLIT_MASS` | Program organization (illegal while P is locked) |
| `APPLY_PARTITION` | Jump to another CSP / pool organization |
| `PIN_GROUND` / `PIN_FLOOR` | Floor pin, not a new mass |
| `SET_LOADING` | Double / single loaded |
| `PAIR_MASSES` / `CLEAR_PAIRINGS` | Shared-width pairing under a stated frontage |
| `SET_STORIES` | Story count on a mass |
| `SET_SHAPE` / envelope / plate profile | Shape family the layout can draw |
| `COURTYARD` | Named, **unsupported** until layout can draw it |

---

## COVER, LEARN, REFINE — plus REPAIR and P pool

The archive holds one elite per **behavior cell**, not per width. Illegal
evaluations are attempts, except a small **near-feasible frontier**.

**COVER** — Joint multi-axis map with adaptive budget and an expandable **P pool**.

**REPAIR** — Nearest legal twin of the same idea. Hard-limit distance only.

**LEARN** — Up to five A/B questions among feasible, architecturally different
schemes. Click the preferred drawing in the UI. Taste steers later search; it
cannot unlock a must.

**REFINE** — Deeper exploration of legal lineages under current taste.

---

## Engine vs search

| Layer | Owns |
|-------|------|
| `solver.py`, `layout.py`, `allocate.py` | Footprints, pairing, voids, GSF, floors |
| `explore/realize.py` | Fill widths/ratio for a frozen strategy `s` |
| `search.py` | Width/story enumeration baseline |
| `explore/` | Strategy, actions, archive, COVER, P pool, REPAIR, MCTS, BO, LEARN |
| `ui_app.py` | Studio UI over the same controller |

Every scheme on the study is verified through `solve_massing_study`.

---

## Grouping rule

Propose partitions only when the brief did not require a grouping. Never undo
a stated must. If the user said three masses and gym with dining, that is one
P constraint set — COVER still samples other legal organizations inside it.
