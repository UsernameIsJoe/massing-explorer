# Blueprint: hierarchical strategy search

Updated 24 Sep 2026. This is the **constitution** of the search: purpose,
direction, and how engines serve a self-aware controller. Older notes in
`DISCUSSION-search-architecture.md` and `PLAN-search-architecture.md` describe
the three *jobs* (cover, learn, refine). Product idea: [CONCEPT.md](CONCEPT.md).
Workflow: [WORKFLOW.md](WORKFLOW.md). Studio: [UI.md](UI.md).

The engine stays the engine. What must change next is the **control loop**:
from a fixed procession of engines toward a shared search-state ledger that
decides probe / deepen / step back / reopen / stop.

---

## What this project is

Massing Explorer is a **self-aware strategic search system** over a finite
strategy space. Architecture is a sequence of semantic decisions; computation
proves whether those decisions can become buildings.

```
language reasoning
    → design actions
    → constraint / massing engine + realize(s)
    → performance evidence
    → update belief / next decision
```

**Not** `prompt → AI shape`. **Not** `parameters → optimizer → shape`. **Not**
a GA of lookalikes, CAD automation, or brute-force enumeration of every form.

Value: make the architect smarter about the design problem — which legal
strategies exist, which near-feasible ideas can be projected onto the legal
set, which regions were deferred and why, which brief clauses collapsed the
feasible set, and how stable the finalists are. It is not merely "Option 17."

Project law (full statement): [CONCEPT.md](CONCEPT.md#project-law).

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

## Direction: from engine chain to search intelligence

### Target control loop

```
1. MAP      — meaningful strategic regions of the finite space
2. FILTER   — hard organizational / geometric proofs
3. PROBE    — representative evaluations of important regions
4. UPDATE   — feasibility, quality, uncertainty, failure patterns
5. FOCUS    — deepen strong evidence and improving near-misses
6. STEP BACK — audit neglected / uncertain / structurally different regions
7. MANEUVER — repair or change strategic actions when progress stalls
8. STOP OR REOPEN — stop when stable; reopen when inputs or evidence change
```

CSP, COVER, REPAIR, MCTS, and BO are **mechanisms serving this loop** — not a
fixed parade:

```
CSP → COVER → REPAIR → MCTS → BO → REFINE   ← current (procedural)
```

Desired:

```
        Region ledger (search state)
                 ↕
   CSP / COVER / REPAIR / MCTS / BO / REFINE
                 ↕
   Controller: probe | deepen | step back | predict | stop
```

### Region ledger (required next structural concept)

A region is a meaningful strategic cell (e.g. 4 masses + Arts separated +
Media with Academic + Gym/Dining isolated + cohesive Academic). For each
region record at least:

| State | Meaning |
|-------|---------|
| Unseen | No representative evaluated |
| Probed | Some representatives evaluated |
| Feasible | At least one legal basin |
| Near-miss | Close to legality and improving |
| Flat | Probes not improving |
| Saturated | Extra depth adds little |
| Impossible | Eliminated by organization-wide proof |
| Deferred | Not worth cost now; may reopen |

**Impossible, tested-and-weak, and not-yet-tested must never collapse into one
bucket.** Each deferred region should carry: probes done vs owed, best
feasibility distance, shared failure, why deferred, reopen if …

Today’s `p_pool` sticky status and archive cells are a **partial** ledger
(evaluated outcomes). They are not yet a complete map of skipped / deferred /
reopen conditions.

### Priority (conceptual)

\[
\operatorname{Priority}(R) =
\text{potential} + \text{uncertainty} + \text{coverage debt}
+ \text{failure relevance} - \text{saturation} - \text{cost}
\]

Local budget knobs (probe floors, deepen fractions, story-library biases) are
provisional only if they approximate this priority. Prefer one search policy
over accumulating benchmark-specific patches.

### Step back (when)

Widen deliberately when: winners stop improving; focused regions share one
limitation; another region stays highly uncertain; a mass count or relationship
pattern is underrepresented; the winner is fragile under input changes; too
much budget sits in one organization; preferences change; a constraint change
invalidates prior rejection reasons. Step-back is **evidence-based retreat**,
not random diversification.

### What confidence should mean

Not: “we evaluated every scheme.”

Yes: selected strategies are the strongest supported under current inputs;
meaningful regions received representative testing; promising basins were
deepened; neglected regions were audited; leaders stayed stable under more
budget; **remaining uncertainty is named**.

---

## Evaluation: fit and drift

Judgment as of 24 Sep 2026. Core thesis **aligned**; orchestration and
self-awareness **incomplete**; recent yield work risked **benchmark-heuristic
drift**.

| Area | Current behavior | Fit | Drift / weakness | Judgment |
|------|------------------|-----|------------------|----------|
| Strategy as search object | Searches P, stories, T, loading, envelope, plate | Form follows strategy | — | **Strong fit** |
| Strategy–dimension split | `realize(s)` fills feet without changing P/T/stories | Deterministic form | Some case-sensitive width lore | **Strong fit** |
| Hard vs soft | Limitations gate; prefs rank / bias only | Hierarchy correct | School-prior / motif proxies hardcoded | **Strong fit** |
| CSP organizational map | Enumerates valid P; feature coverage seats | Structured P-space | Shortlist ≠ persistent explored/deferred map | **Foundation; incomplete map** |
| Relationship ontology | Art / Media / Admin / Gym+Dining / Academic | Meaningful differences | School-specific law, not pluggable domain knowledge | **Fit domain; generality drift** |
| Feas in coverage cells | \(S=-i+\lambda S_{\mathrm{feas}}\) (\(\lambda=0.5\)) | Diversity + realizability | Local score, not region belief | **Idea fits** |
| COVER breadth | Joint samples; round-robin per-P story floor | Representative probes | List schedule ≠ coverage obligations + reopen | **Partial fit** |
| COVER qualification | Probe floors, flat-pause, impossible, deepen saturate | Probe → assess → focus | Status ≠ full ledger with defer/reopen reasons | **Logic fits; memory thin** |
| Depth | Deepen unsaturated feasible; discovery share | Evidence-based focus | Constants patched from last failure | **Mostly fit; policy drift** |
| Step-back | Expand / discover when yield thin | Seeds of retreat | Scattered rules, not global shared-failure policy | **Partial** |
| Failure maneuvering | Feasibility distance, REPAIR, MCTS, near-miss | Move through constraints | Failures not accumulated into region models | **Mechanism good; memory weak** |
| MCTS / BO | After COVER; BO on legal actions | Right capabilities | Fixed order; BO value unproven by ablation | **Fit engines; isolated** |
| Archive | Cells, legal/frontier, sticky P status | Shared evidence | Knows evaluated; weak on skipped/deferred | **Critical partial fit** |
| Adaptive budget | Start/step/stagnation; discovery/deepen/expand | Compute as resource | Many local knobs vs one Priority(R) | **Beginning to drift** |
| Search sequence | CSP→COVER→REPAIR→MCTS→BO→REFINE | Has the parts | Procedural chain ≠ controller | **Main architectural drift** |
| Skipped-space knowledge | CSP counts, attempts, some statuses | Partial transparency | Cannot answer “what remains under-tested and why” | **Major gap** |
| Finalist confidence | Legal counts, org counts, scores | Evidence on found schemes | No stability / residual-opportunity report | **Major gap** |
| Benchmarking | Underwood GSF vs 53c | Stable regression | `school_critical` / unlock patterns overfit risk | **Overfitting risk** |
| Recent yield | Full pipeline breadth above 53c on org count; COVER-only recovered | Engines can recover | Success from scheduling patches, not stated Priority(R) | **Good scoreboard; weak causal story** |

### Strongest fits to protect

Strategy–dimension separation; hard vs soft; CSP as organizational reasoning;
shared Archive; feasibility distance / near-miss; MCTS over strategic actions;
BO as possible uncertainty-aware budget manager.

### Most important drifts

1. **Pipeline as engine chain** instead of ledger ↔ controller.
2. **Budget rules as patches** (floors, deepen fractions, school-critical
   first) without a general Priority(R).
3. **Underwood overfitting** — school ontology and unlock stacks as if they
   were universal search law.
4. **Incomplete knowledge of skipped space.**
5. **Confidence = yield metrics** rather than stability + residual opportunity.

### Next architectural work (not more local knobs)

1. **Region ledger** — shared record of regions, probes, failures, deferral,
   reopen conditions.
2. **Search controller** — reads the ledger; chooses CSP expansion, COVER
   probe, repair, deepen, MCTS, BO, step-back, or stop.

Do not add many more Underwood-specific scheduling heuristics without pulling
them under those two concepts.

---

## Boxes in the code

| Box | Module today |
|-----|----------------|
| Intent interpreter | `reading.py`, `brief.py` |
| Structured design state | `StudySession`, `explore/strategy.py` as `S = (P, T, V, G, D)` |
| Strategy planner | `explore/planner.py` |
| Semantic actions | `explore/actions.py` |
| Constraint / massing engine | `solver.py`, `layout.py`, `allocate.py`, pairing, GSF |
| Realize widths for a frozen `s` | `explore/realize.py` |
| Performance vector | `explore/performance.py` |
| Distance to feasibility | `explore/feasibility.py` |
| Design archive + frontier | `explore/archive.py` |
| COVER / LEARN / REFINE | `explore/controller.py`, `explore/cover.py` |
| Expanding P pool | `explore/p_pool.py` |
| REPAIR (project onto feasible set) | `explore/repair.py` |
| DIAGNOSE (zero legal or low yield) | `explore/diagnose.py` |
| LEARN (pairwise) | `explore/preference.py` |
| Open P | `explore/csp.py`, `explore/partitions.py` |
| Drawable T and stated D | `explore/topology.py` |
| MCTS around planner + engine | `explore/mcts.py` |
| Bayesian budget manager | `explore/bayes.py` |
| Caps and saturation stop | `explore/saturate.py` |
| Studio UI | `ui_app.py`, `explore/ui_payload.py` |
| Width/story enumeration baseline | `search.py` |

Chat / UI entry: `brief.apply_parsed_brief` → `explore.controller.run_search`.
Product pages: [CONCEPT.md](CONCEPT.md), [WORKFLOW.md](WORKFLOW.md), [UI.md](UI.md).

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
*patterns* (not one-mass increments), drawable topology (T), loading,
envelope family (balanced / compact / elongated), and plate profile — rather
than a product of story margins around the baseline.

Open P uses an expanding **P pool** (`p_pool.py`): status is on the
organization (feasible / unresolved / impossible), not one failed width.
CSP shortlists with fair |P| shares and a family-aware shape prior so
school-shaped bars are not lost among hundreds of flat-ranked partitions.
Soft `preferred_stories` biases story order without locking every mass to N.
Changing P clears stale mass-width locks before `realize(s)`.

**Adaptive budget:** start ~40 evaluations → measure new legal regions *and*
feature-space novelty → if still discovering, add +10 or +20 → stop when
stagnant → hard cap ~100–120. If the cap hits while regions are still
opening, mark the map incomplete. The P pool may expand mid-run when
outcomes repeat or gaps remain.

### REPAIR

Given this COVER idea, what is the nearest legal version of the *same* idea?

When a sample misses a hard limit, rank it by a **violation vector** (edge
overrun, min-edge shortfall, hard ratio, awkward program split, required
width, other hard kinds). Soft prefs do not enter that vector. Illegal
`search_reward` stays 0 so LEARN / BO do not treat near-misses as good
architecture.

A short **realize(s)** pass fills widths / ratio projection for the restored
idea (stories and P stay frozen). Soft prefs are not the objective. Repair
does **not** regroup programs (`APPLY_PARTITION`), invent courtyards, bump
stories, or optimize preference scores. Story ±1 stays an MCTS / BO lever.
Budget is separate from COVER (~8 ideas). Realized plates stay on the same
idea cell (`cell_key` = `idea_key`).

### LEARN

What comparison would teach us the most about what the designer values?

Pairwise A/B among feasible schemes only — **at most five** questions.
Prefer pairs that are **visibly different** (P, T, envelope, loading,
stories / silhouette) so a short questionnaire still spans the pool. A
written brief is not a sample. Bradley–Terry on **measured** traits. Taste
steers where MCTS / BO / REFINE spend effort; it does not override a must.
In the studio UI the user **clicks the preferred scheme card**; intermediate
picks stay snappy (full REFINE waits until LEARN completes). Chat still
accepts typed `A` / `B`.

### REFINE

Given what we currently know, which lineages deserve deeper exploration?

Keep several architecturally distinct elites (different P or T, not three
widths of the same bar). Allocate local tries by weight, with a floor so a
light lineage is not deleted. Local neighbors include story ±1, envelope /
loading, small grouping, and drawable topology — not `SET_WIDTH`. Exact feet
are filled by `realize(s)`. After a choice, reweight. Stop when
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
the same idea’s legal twin, to remember what was deferred, and to say why
other strategies are empty or illegal — with explicit confidence about what
remains uncertain. It is not a license to override a must, fill a cap, invent
a courtyard, call a width a concept, or substitute a growing pile of
benchmark heuristics for a search-state policy. Repair does not shop for a
different COVER idea. Local scheduling knobs are provisional until they sit
under a region ledger and controller.
