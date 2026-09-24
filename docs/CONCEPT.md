# Concept

Updated 24 Sep 2026. Project law and search constitution:
[BLUEPRINT-search.md](BLUEPRINT-search.md). Day-to-day steps:
[WORKFLOW.md](WORKFLOW.md). Studio UI: [UI.md](UI.md). Fit / drift evaluation:
[BLUEPRINT-search.md § Evaluation](BLUEPRINT-search.md#evaluation-fit-and-drift).

---

## Purpose

Massing Explorer is a **self-aware strategic search system**.

Given a finite but exhausting design space, hard constraints, soft preferences,
and limited computation, it decides **where to look**, **how deeply to search**,
**when to step back**, and **what uncertainty remains** — then proposes the
best-supported strategies with **explicit trade-offs and confidence**.

It does not need to see every point. It needs to **understand the structure of
the space**:

```
All possible schemes
        ↓
Organize into meaningful strategic regions
        ↓
Eliminate regions contradicted by hard constraints
        ↓
Probe representatives from every important region
        ↓
Learn which regions are feasible and promising
        ↓
Focus computation inside strong regions
        ↓
Periodically audit neglected regions
        ↓
Return to them when evidence or inputs change
```

It should not merely ask “what is the highest score I have seen?” It should
ask: **what evidence supports this direction, what alternatives remain
plausible, what did I skip, and what could overturn my current conclusion?**

---

## Novelty — what this is not

| Not this | Why |
|----------|-----|
| Parametric form generator | Feet are not the chromosome |
| CAD automation / Rhino driver | Engine proves strategy; export is secondary |
| LLM controlling geometry | LLM proposes typed actions; never owns arithmetic |
| GA of visually varied shapes | Population ≠ structured strategic regions |
| Brute-force optimizer | Finite space is mapped and sampled, not exhaustively scored |

The pioneering claim is **search intelligence over a structured strategy
space** — map, probe, focus, step back, explain — not any single algorithm
(CSP, COVER, MCTS, BO) in isolation.

---

## Project law

> Massing Explorer does not attempt to inspect every possible form. It
> structures the finite strategy space, eliminates what can be disproven,
> samples what must be understood, focuses where evidence is strongest, and
> continually monitors what it has deferred. It proposes the best-supported
> strategies while explaining their trade-offs, search confidence, and the
> conditions under which unexplored alternatives should be reopened.

A probe floor, CSP quota, MCTS prior, BO kernel, or budget split is **good only
if** it improves one of: understand the map; search it intelligently; remember
what was skipped; know when to focus; know when to step back; explain why the
conclusion is credible.

---

## Form follows strategy

The search object is not arbitrary geometry. A strategy is a sequence of
architectural decisions. In code and product language:

```
S = (P, T, V, G, D)
```

COVER’s joint sample axes (stories, loading, envelope, plate profile) sit
inside **G** (and related vertical / geometric levers). Equivalently, a
realized search point can be written \(s = (P, S, T, L, E, F)\) then

\[
D^* = \operatorname{Realize}(s)
\]

Form and dimensions are the **deterministic realization** of the strategic
decision. That preserves identity: **strategy first → form follows →
performance tests the result.**

Two different partitions are different strategies **before** geometry changes.

---

## Hard vs soft

| Role | Meaning | Role in search |
|------|---------|----------------|
| **Requirement** | Must happen. Locked. | Shapes admissible organizations |
| **Limitation** | A cap to check. Never a length to draw. | \(\operatorname{legal}(s)=\bigwedge_i C_i(s)\); prefs cannot compensate |
| **Preference** | Desired; may be met more than one way. | Guides attention, depth, ranking, LEARN A/B — never a gate |

Hard constraints eliminate impossible regions, diagnose failure, supply
feasibility distance for near-misses, and support repair. Soft preferences
decide which legal regions get more depth and which trade-offs LEARN asks
about.

---

## Value of a run (today and target)

**Today** a useful run reports legal strategies, near-miss / REPAIR twins,
empty cells and why, DIAGNOSE when yield collapses, robustness under
program-area shock, and A/B among drawings that already fit.

**Target confidence** (not yet fully implemented — see BLUEPRINT evaluation):

- Why each finalist was selected
- Which hard constraints it satisfies and which preferences it sacrifices
- What competing strategy came closest
- Which meaningful regions remain under-explored
- What input change might cause another strategy to win
- How stable the result was under additional budget

The tool must not claim “we evaluated every scheme.” It should claim the
selected strategies are the **strongest supported** under current inputs, with
residual uncertainty named.

---

## LLM + engine

```
language reasoning
    → design actions
    → constraint / massing engine + realize(s)
    → performance evidence
    → reasoning again
```

The LLM proposes. The engine owns geometry, GSF, pairing, voids, and checks.
Never trust the LLM for arithmetic.

Drive the same loop from **chat** or the **studio UI**.

---

## Bayesian optimization (and what it is not)

BO is an evaluation-budget manager over **legal** strategy actions already in
the archive’s feature space. It does not invent courtyards, fill length caps,
or replace COVER’s map of organizations. Its long-term role is uncertainty-aware
allocation under a shared search controller — value still to be proven by
ablation.
