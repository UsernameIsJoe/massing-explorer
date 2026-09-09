# Workflow

Recorded 9 Sep 2026. This is what happens when you chat a brief. Constitution: [BLUEPRINT-search.md](BLUEPRINT-search.md). Concept: [CONCEPT.md](CONCEPT.md).

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
        │ ENGINE                 │
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

MCTS sits around the **planner + engine** loop. Bayesian optimization, when it runs, spends a small evaluation budget on high-EI legal actions. Neither sits on feet.

---

## First brief (default COVER)

1. **Ingest.** Excel/CSV program is already on the study (or loaded with the chat command).
2. **Interpret.** `reading.py` + `brief.py` split the message into requirements, limitations, and preferences. Unknown departments and invented sizes are dropped.
3. **Lock what was stated.** Named masses, keep-together, keep-apart, alone, same-mass, and a stated mass count stay locked. Pairing from the brief locks topology. A length cap is stored as a filter, not a target.
4. **COVER the archive.**
   - Realize the stated strategy through the engine.
   - If T is open, sample drawable topologies only (independent bars, or paired bars when a frontage cap is stated).
   - If P is open, CSP enumerates distinct legal partitions. The LLM does not invent P.
   - The planner proposes at most five typed actions. The engine applies or rejects them.
   - MCTS searches over those actions, using the planner as an expansion prior.
   - Bayesian optimization may spend a few more evaluations on high expected-improvement actions.
   - REFINE then allocates local tries among several kept lineages (different P or T, not three widths of the same bar).
5. **LEARN prepares a pair** when two feasible elites exist. A written brief is not a comparison.
6. **Keep one drawing** on the study. Report legal vs infeasible cells, empty-cell reasons, robustness survive/collapse, applied vs illegal vs unsupported planner moves, the scheme kept, any pending A/B, and every failed check.

Chat entry: `apply_parsed_brief` → `explore.controller.run_search(mode="cover")`.

---

## Follow-up turns

| User says | What happens |
|-----------|----------------|
| `A` or `B` after a pending pair | LEARN comparison. Bradley–Terry on **measured** traits. Not a new brief. Not a dimension to invent. |
| New limit, pin, pair, or resize | Recorded on the session. Do not regroup a stated must. |
| Explicit regroup | Human-controlled grouping change. Required wings stay required. |
| “What is on floor 2?” | Read from the last solve. Do not guess. |

A later turn can switch mode (`cover` / `learn` / `refine`). The three jobs are modes over one archive, not Stage 1 → 2 → 3.

---

## Semantic actions

The LLM does not emit geometry. It emits moves. The engine applies or rejects.

| Action | Meaning |
|--------|---------|
| `COLOCATE` | Departments share a mass |
| `KEEP_APART` | Departments do not |
| `SPLIT_MASS` | Legal only when mass count was not a requirement |
| `PIN_GROUND` / `PIN_FLOOR` | Floor pin, not a new mass |
| `SET_LOADING` | Double / single loaded |
| `PAIR_MASSES` | Shared-width pairing inside a stated frontage |
| `SET_STORIES` | Story count on a mass |
| `SET_SHAPE` | Shape family the layout can draw |
| `COURTYARD` | Named, **unsupported** until layout can draw it |

`COLOCATE` / `SPLIT` / `KEEP_APART` are illegal while P is locked.

---

## Three modes, one archive

The archive holds one elite per **behavior cell**, not per width. A cell is a legal typology: organization (and partition id when P was open), shape family, story band, loading. Illegal evaluations are attempts, not coverage.

**COVER** — What fundamentally different feasible strategies have we not investigated? Fill empty **supported** cells. An unsupported cell is not a coverage failure.

**LEARN** — What comparison would teach us the most about what the designer values? Pairwise A/B among feasible schemes only. Bradley–Terry on measured traits (spread, height variance, likeness, street edge if a frontage exists).

**REFINE** — Given what we currently know, which lineages deserve deeper exploration? Keep several architecturally distinct elites. Allocate local tries by weight, with a floor so a light lineage is not deleted. After a choice, reweight. Do not invent feet.

---

## Engine vs search

| Layer | Owns |
|-------|------|
| `solver.py`, `layout.py`, `allocate.py` | Footprints, pairing, voids, GSF, floors |
| `search.py` | Width/story enumeration baseline for experiments |
| `explore/` | Strategy, actions, archive, modes, planner, CSP, MCTS, BO budget |

Every scheme written onto the study is still verified through `solve_massing_study`. Search cannot claim something the solver rejects.

---

## Grouping rule

Propose partitions only when the brief did not require a grouping. Never undo a stated must.

If the user said four masses and gym with dining, that is one P. If they said only a length cap, COVER may illuminate several partitions.
