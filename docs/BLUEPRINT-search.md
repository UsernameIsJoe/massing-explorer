# Blueprint: hierarchical strategy search

Recorded 9 Sep 2026. This is the constitution of the search. Older notes in
`DISCUSSION-search-architecture.md` and `PLAN-search-architecture.md` describe
the three *jobs* (cover, learn, refine). This file says how those jobs sit in
the system: they are modes over an archive, not a one-way Stage 1 → 2 → 3
pipeline, and not a width enumerator.

The engine stays the engine. What changes is the control loop.

---

## What this project is

Architecture is a sequence of semantic decisions an LLM can reason about.
Computation proves whether those decisions can become buildings.

```
language reasoning
    → design actions
    → constraint / massing engine
    → performance evidence
    → reasoning again
```

Not `prompt → AI shape`. Not `parameters → optimizer → shape`.

The system's value is to make the architect smarter about the design problem:
which legal strategies exist, which regions of the archive are empty and why,
which brief clauses collapsed the feasible set. It is not merely "Option 17."

---

## The loop

```
                  USER BRIEF
                      │
                      ▼
           ┌────────────────────┐
           │ Intent Interpreter │
           │       LLM          │
           └─────────┬──────────┘
                     │
            requirements
            limitations
            preferences
            uncertainties
                     │
                     ▼
          STRUCTURED DESIGN STATE
                     │
                     ▼
        ┌────────────────────────┐
        │  LLM STRATEGY PLANNER  │
        │ proposes design moves  │
        └───────────┬────────────┘
                    │
             semantic actions
                    │
                    ▼
        ┌────────────────────────┐
        │ CONSTRAINT / MASSING   │
        │ ENGINE                 │
        │                        │
        │ program assignment     │
        │ floor allocation       │
        │ dimensions             │
        │ anchor fit             │
        │ site feasibility       │
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
         │            │          │
         └────────────┴──────────┘
                    │
                    ▼
            next search actions
```

MCTS, when it exists, sits around the **planner + engine** loop. It does not
sit on feet.

---

## Boxes in the code

| Box | Module today |
|-----|----------------|
| Intent interpreter | `reading.py`, `brief.py` |
| Structured design state | `StudySession`, `explore/strategy.py` as `S = (P, T, V, G, D)` |
| Strategy planner | `explore/planner.py` |
| Semantic actions | `explore/actions.py` |
| Constraint / massing engine | `solver.py`, `layout.py`, `allocate.py`, pairing, GSF |
| Performance vector | `explore/performance.py` |
| Design archive | `explore/archive.py` |
| COVER / LEARN / REFINE | `explore/controller.py`, `explore/cover.py` |
| LEARN (pairwise) | `explore/preference.py` |
| Open P | `explore/csp.py`, `explore/partitions.py` |
| Drawable T and stated D | `explore/topology.py` |
| MCTS around planner + engine | `explore/mcts.py` |
| Bayesian budget manager | `explore/bayes.py` |
| Width/story enumeration baseline | `search.py` |

Chat entry: `brief.apply_parsed_brief` → `explore.controller.run_search`. Product
pages: [CONCEPT.md](CONCEPT.md), [WORKFLOW.md](WORKFLOW.md).

---

## Structured design state

A strategy is a sequence of architectural decisions, not a width:

```
S = (P, T, V, G, D)
```

- **P — Program organization.** Mass count, who is together, who is apart.
  Propose alternative partitions **only when the brief did not require a
  grouping**. Named masses, keep-together, keep-apart, alone, same-mass, and a
  stated mass count stay locked. They are not Monte Carlo coordinates.
- **T — Topological strategy.** Only topologies the solver can draw: independent
  bars, paired bars, L leftover around a void. Courtyard, podium, and
  perpendicular wings are named but **unsupported** until layout can realize
  them. An unsupported cell is not a coverage failure.
- **V — Vertical strategy.** Ground pins, double-height, stacking implied by
  allocation. A ground-floor preference is still a pin, not its own mass.
- **G — Geometric realization.** Stories, shape family, loading, ratio. These
  are today's open levers. Feet are not coordinates. A length cap is a filter.
- **D — Site disposition.** The stated rectangle and frontage. Streets,
  neighbors, topography, and EnergyPlus wait until those inputs exist.

---

## Semantic design actions

The LLM does not emit geometry. It emits moves. The engine applies or rejects.

First wave (maps onto tools that already exist):

- `COLOCATE` — departments share a mass
- `KEEP_APART` — departments do not
- `PIN_GROUND` / `PIN_FLOOR`
- `SET_LOADING`
- `PAIR_MASSES`
- `SET_STORIES`
- `SET_SHAPE`

`SPLIT_MASS` is legal only when mass count was not a requirement. A later chat
turn still cannot undo a required wing, pair masses so the length lands on the
cap, or invent a dimension.

Unknown departments and invented sizes are dropped, as in `reading.py`.

---

## Three search modes, one archive

```
                DESIGN ARCHIVE
                     │
          ┌──────────┼──────────┐
          ↓          ↓          ↓
       COVER       LEARN      REFINE
          ↑          ↑          ↑
          └──────────┼──────────┘
                     │
                 SEARCH
```

The archive holds one elite per **behavior cell**, not per width. A cell is a
legal typology: organization (and partition id when P was open), story band,
loading, topology, and envelope family. Illegal evaluations are attempts, not
coverage.

### COVER

What fundamentally different feasible strategies have we not investigated?

Sample **joint** points across open axes — program organization (P), story
*patterns* (not one-mass increments), drawable topology (T), loading, and
envelope family (balanced / compact / elongated) — rather than a product of
story margins around the baseline.

**Adaptive budget:** start ~40 evaluations → measure new legal regions → if
still discovering, add +10 or +20 → stop when stagnant → hard cap ~100–120.
If the cap hits while regions are still opening, mark the map incomplete.
Do not call a truncated story product a joint sample.

### LEARN

What comparison would teach us the most about what the designer values?

Pairwise A/B among feasible schemes only. A written brief is not a sample.
Bradley–Terry on **measured** traits. Pick ambiguous pairs; keep connecting
comparisons so ratings stay on one scale. "Why B?" may propose the next pair
or explain; it does not invent an unmeasured coordinate.

### REFINE

Given what we currently know, which lineages deserve deeper exploration?

Keep several architecturally distinct elites (different P or T, not three
widths of the same bar). Allocate local tries by weight, with a floor so a
light lineage is not deleted. After a choice, reweight; do not open a new
sample by inventing feet.

Default schedule for a fresh brief:

```
Intent extraction → COVER → LLM planner → MCTS → Bayesian optimization
→ REFINE → LEARN pair preparation
```

COVER fills the archive first so planner / MCTS / BO see a real multi-axis map.
A later turn can switch mode (`cover` / `learn` / `refine`). The three jobs
from the earlier plan remain; they are no longer the identity of the algorithm.

---

## Evaluation

Hard feasibility first. Then a **performance vector**, not one quality score:

- feasibility (requirements and caps)
- measured traits (spread, height variance, likeness, street edge if a
  frontage exists)
- stated-preference distance until the user compares
- novelty versus the archive
- later: anchor-room fit, leftover area, public-on-grade, fragmentation,
  robustness under program perturbation

The weighted sum in `search.py` (`balanced` / `low_rise` / `compact`) is an
experimental baseline, not "quality." Courtyard enclosure and daylight
simulation are omitted until the drawing or site can report them.

Robustness: grow or shrink a department, re-solve **the same strategy**,
record survive or collapse. That is not a license to regroup a must.

---

## LLM and agents

One Strategy planner: at most five typed actions given the brief, failures,
and empty cells. The engine falsifies them. Count illegal proposals.

No Architect / Engineer / Client / Critic role-play. A Critic is added only if
experiments show the planner repeats the same illegal move.

Bayesian optimization is an evaluation-budget manager for expensive
simulations. It is not an architectural generator. It is not on the critical
path until a run costs minutes.

---

## Grouping rule

Propose partitions only when the brief did not require a grouping. Never undo
a stated must.

If the user said four masses and gym with dining, that is one P. If they said
only a length cap, COVER may illuminate several partitions. `academic + arts /
gym + dining` is a different strategy from `academic / arts + gym + dining`
before geometry changes. It is a different archive cell only when P was open.

---

## Build order

Each phase ships with tests and a still-runnable chat/solve path. Stay in this
repository. Do not rewrite the solver to "start over."

0. **Constitution** — this file. The three jobs are modes.
1. **Spine** — `explore/`: strategy, typed actions, archive, performance
   vector, controller. `improve_scheme` is a policy the controller may call,
   not the architecture.
2. **Modes** — COVER / LEARN / REFINE over the archive. Reuse `sample_space`,
   `preference`, elite allocation.
3. **Open P** — enumerate legal partitions only when grouping was not
   required.
4. **Planner** — one LLM, ≤5 typed actions, engine apply/reject.
5. **Explain and robustness** — empty-cell sentences; program perturbation.
6. **CP/CSP** — distinct feasible partitions; the LLM does not invent P.
7. **T and D** — only drawable topologies and stated site. Courtyard stays
   unsupported until layout can draw it.
8. **MCTS** — over design actions, planner as expansion prior.
   `search.py` remains the enumeration baseline for experiments.

Phases 0–8 are in. Bayesian optimization sits beside COVER as an
evaluation-budget manager; it is not a generator and is not on feet.
MCTS still does not sit on feet. Do not put either on widths.

---

## Line to hold

This is a way to explore among legal strategies and to say why other
strategies are empty or illegal. It is not a license to override a must, fill
a cap, invent a courtyard, or call a width a concept.
