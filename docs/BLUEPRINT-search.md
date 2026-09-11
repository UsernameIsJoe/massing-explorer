# Blueprint: hierarchical strategy search

Recorded 9 Sep 2026; REPAIR / near-feasible frontier added 11 Sep 2026. This is
the constitution of the search. Older notes in
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
which legal strategies exist, which near-feasible ideas can be projected onto
the legal set, which regions of the archive are empty and why, which brief
clauses collapsed the feasible set. It is not merely "Option 17."
Architectural feasibility is often a boundary you can navigate toward, not
only a cliff.

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
         └────────────┴──────────┘
                    │
                    ▼
            next search actions
```

COVER explores **architectural ideas**. Illegal COVER samples are not all
worthless. A small deterministic **REPAIR** layer asks how far a sample is
from feasibility and, using existing typed actions, finds the nearest legal
twin of the *same idea* (same partition, topology, loading, plate profile,
envelope). Repair optimizes constraint violation, not taste.

```
COVER → SOLVE ── legal ──────────────→ ARCHIVE
              │                           ↓
              └─ illegal → REPAIR ─ legal
                         │
                         └─ still illegal
                                ↓
                        NEAR-FEASIBLE FRONTIER
                                ↓
                   DIAGNOSE / selected MCTS roots
```

MCTS sits around the **planner + engine** loop. It starts from several diverse
**legal COVER elites plus a few near-feasible frontier** samples, not one
current scheme, and searches typed action sequences. Nearby width is a local
`delta_ft` step from the current plate, not a width enumerator. Bayesian
optimization spends a sequential evaluation budget on high-EI **legal**
actions and refits after each result. Neither sits on an invented foot target.
LEARN pairwise taste stays among legal schemes only.

**DIAGNOSE** runs when COVER+REPAIR still has **zero legal cells**, or when
legal yield is low (fewer than three) and the recoverable frontier is large.
It does not run when the archive already has plenty of legal cells.

```
COVER → REPAIR → legal or frontier?
  YES → planner / MCTS (legal ∪ frontier) / BO+REFINE if legal / LEARN
  also DIAGNOSE if zero legal, or low legal yield + large frontier
  NO  → DIAGNOSE (search | conflict | model)
          search → one targeted COVER batch, then re-check
          still none / conflict / model → minimal relaxation probes → USER
```

Probes are never applied automatically. Failure patterns are stored as
knowledge about why regions are empty.

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
| Distance to feasibility | `explore/feasibility.py` |
| Design archive + frontier | `explore/archive.py` |
| COVER / LEARN / REFINE | `explore/controller.py`, `explore/cover.py` |
| REPAIR (project onto feasible set) | `explore/repair.py` |
| DIAGNOSE (zero legal or low yield) | `explore/diagnose.py` |
| LEARN (pairwise) | `explore/preference.py` |
| Open P | `explore/csp.py`, `explore/partitions.py` |
| Drawable T and stated D | `explore/topology.py` |
| MCTS around planner + engine | `explore/mcts.py` |
| Bayesian budget manager | `explore/bayes.py` |
| Caps and saturation stop | `explore/saturate.py` |
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
coverage — except a small **near-feasible frontier** (closest illegal idea
keys under a distance cut). That frontier is for REPAIR and MCTS starts, not
for LEARN taste or “best scheme” display.

### COVER

What fundamentally different feasible strategies have we not investigated?

Sample **joint** points across open axes — program organization (P), story
*patterns* (not one-mass increments), drawable topology (T), loading, and
envelope family (balanced / compact / elongated) — rather than a product of
story margins around the baseline.

**Adaptive budget:** start ~40 evaluations → measure new legal regions *and*
feature-space novelty → if still discovering, add +10 or +20 → stop when
stagnant (no meaningful new cells or encodings) → hard cap ~100–120.
If the cap hits while regions are still opening, mark the map incomplete.
Do not call a truncated story product a joint sample.

COVER also samples floor-plate profile (uniform vs two-type step) on
multi-story masses.

### REPAIR

Given this COVER idea, what is the nearest legal version of the *same* idea?

When a sample misses a hard limit, rank it by a **violation vector** (edge
overrun, min-edge shortfall, hard ratio, awkward program split, required
width, other hard kinds). Soft prefs do not enter that vector. Illegal
`search_reward` stays 0 so LEARN / BO do not treat near-misses as good
architecture.

A short catalog of existing actions then projects toward feasibility: apply a
solver `ResizeSuggestion` width, `SET_WIDTH`, `SET_STORIES` ±1 if unlocked,
project onto a hard ratio band, or `CLEAR_PAIRINGS` if topology is unlocked.
Repair does **not** regroup programs (`APPLY_PARTITION`), invent courtyards,
or optimize preference scores. Budget is separate from COVER (~8 ideas × ≤3
moves). A successful width repair may land in a new behavior cell; that is
intended.

### LEARN

What comparison would teach us the most about what the designer values?

Pairwise A/B among feasible schemes only. Prefer pairs that differ in P, T,
envelope, or loading so the preference engine sees distinct legal schemes. A
written brief is not a sample. Bradley–Terry on **measured** traits. Taste
steers where MCTS / BO / REFINE spend effort; it does not override a must.
Pick ambiguous pairs; keep connecting comparisons so ratings stay on one
scale. "Why B?" may propose the next pair or explain; it does not invent an
unmeasured coordinate.

### REFINE

Given what we currently know, which lineages deserve deeper exploration?

Keep several architecturally distinct elites (different P or T, not three
widths of the same bar). Allocate local tries by weight, with a floor so a
light lineage is not deleted. Local neighbors include story ±1, nearby width
(`delta_ft` from the current plate), envelope / loading, small grouping, and
drawable topology — not invented feet. After a choice, reweight. Stop when
further neighbors stop improving or adding cells.

Default schedule for a fresh brief:

```
Intent extraction → COVER → REPAIR → LLM planner → MCTS (legal ∪ frontier)
→ Bayesian optimization → REFINE → LEARN pair preparation
→ DIAGNOSE if zero legal or low yield + large frontier
```

COVER fills the archive first so planner / MCTS / BO see a real multi-axis map.
REPAIR then projects promising illegal samples onto the feasible set. MCTS
exploits several COVER elites and a few frontier starts (~40–80 sims, depth
3–4) until reward or cell novelty saturates. BO spends ~10–15 sequential
proposals, refitting the GP after each evaluation, and also stops on
saturation. BO remains blind to exact feet by design; REPAIR owns dimension
projection.
A later turn can switch mode (`cover` / `learn` / `refine`). The three jobs
from the earlier plan remain; they are no longer the identity of the algorithm.

---

## Evaluation

Hard feasibility first (requirements + limitations). Among illegal samples, a
separate **feasibility distance** ranks how close a scheme is to the legal
set. Soft ranking among legal schemes uses four evaluation composites — not
one quality score:

1. **Program coherence** — grouping / distribution sense; penalize awkward
   floor splits and fragmentation
2. **Preference alignment** — stated soft prefs (pins, ratio, low-rise,
   preferred stories)
3. **Performance efficiency** — leftover, footprint likeness, frontage use,
   anchor fit
4. **Robustness / flexibility** — survive vs collapse under same-strategy
   program area probes (proxy when no probe yet)

**Probe / novelty encoding** (COVER feature space, BO distance) is a strategy
vector: a **pairwise program-organization block** (same-mass bits, not a SHA
scalar) plus mass count, distribution balance, topology, loading, mean height,
height articulation, vertical organization, and geometric character. Courtyard /
podium stay `unsupported` — named, not COVER targets. GP distance is isotropic
RBF for now (ARD later). Exact feet are not GP coordinates; they are filled by
`realize(s)` — see [PLAN-realize-strategy.md](PLAN-realize-strategy.md).

Raw geometry signals (spread, leftover, …) remain diagnostic inputs.
`search.py`'s weighted sum is an experimental baseline only. Daylight /
EnergyPlus / courtyard enclosure stay omitted until the drawing can report them.

Robustness: grow or shrink a department, re-solve **the same strategy**,
record survive or collapse. That is not a license to regroup a must.

---

## LLM and agents

One Strategy planner: at most five typed actions given the brief, failures,
and empty cells. The engine falsifies them. Count illegal proposals.

No Architect / Engineer / Client / Critic role-play. A Critic is added only if
experiments show the planner repeats the same illegal move.

Bayesian optimization is an evaluation-budget manager for expensive
simulations. It ranks unevaluated **strategies** by expected improvement on
`F(s)` — the quality of `s` after `realize` fills width/length. It does not
propose `SET_WIDTH`. It is not an architectural generator. It is not on the
critical path until a run costs minutes.

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
9. **REPAIR / frontier** — violation vector, project illegal COVER ideas onto
   the feasible set, seed MCTS from legal ∪ frontier, DIAGNOSE on low yield.

Phases 0–8 are in. REPAIR sits between COVER and exploit so feasibility is a
boundary, not only a cliff. Bayesian optimization sits beside COVER as an
evaluation-budget manager; it is not a generator and is not on invented feet.
MCTS exploits COVER elites and near-feasible starts over typed actions.
Nearby width is a local step, not a width product. Do not put either on an
invented foot target.

---

## Line to hold

This is a way to explore among legal strategies, to project a near-miss onto
the same idea’s legal twin, and to say why other strategies are empty or
illegal. It is not a license to override a must, fill a cap, invent a
courtyard, or call a width a concept. Repair does not shop for a different
COVER idea.
