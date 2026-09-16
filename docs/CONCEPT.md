# Concept

Updated 16 Sep 2026. Project law is [BLUEPRINT-search.md](BLUEPRINT-search.md).
This page is the product idea in plain language. Day-to-day steps:
[WORKFLOW.md](WORKFLOW.md). Studio UI: [UI.md](UI.md).

---

## What this is

Massing Explorer turns a program spreadsheet into conceptual masses that have
been dimensionally tested. It exists to make the architect smarter about the
**design problem**: which legal strategies exist, which near-feasible ideas can
be repaired into the same concept, which regions of the archive are empty and
why, which brief clauses collapsed the feasible set.

It is not merely “Option 17.” It is not `prompt → AI shape`. It is not
`parameters → optimizer → shape`.

Architecture is a sequence of semantic decisions an LLM can reason about.
Computation proves whether those decisions can become buildings:

```
language reasoning
    → design actions
    → constraint / massing engine
    → performance evidence
    → reasoning again
```

The LLM proposes. The engine owns geometry, GSF, pairing, L-shape/voids, and
checks. Never trust the LLM for arithmetic.

You can drive the same loop from **chat** or the **studio UI** (drop Excel,
type a brief, Generate, browse the archive, click A/B preferences).

---

## Three roles in every brief clause

| Role | Meaning | Example |
|------|---------|---------|
| **Requirement** | Must happen. Locked. | Three masses. Gym with dining. |
| **Limitation** | A cap to check. Never a length to draw. | Max 60 m edge. At most 3 stories. |
| **Preference** | Desired. May be met more than one way. | Prefer 3 floors. Art on the ground floor. |

A ground-floor note is a **pin**, not its own mass. A later turn still cannot
undo a required wing, pair masses so the length lands on the cap, or invent a
dimension. Soft preferences (including “prefer N floors”) bias search; they do
not replace hard gates.

---

## A strategy is not a width

A strategy is a sequence of architectural decisions:

```
S = (P, T, V, G, D)
```

- **P — Program organization.** Mass count, who is together, who is apart.
  Alternative partitions are proposed **only when the brief did not require a
  grouping**. Search keeps a growing **P pool** (feasible / unresolved /
  impossible on the organization), not a one-shot list of five.
- **T — Topological strategy.** Only what layout can draw: independent bars,
  paired bars, L leftover around a void. Courtyard, podium, and perpendicular
  wings are named but unsupported until the drawing can realize them.
- **V — Vertical strategy.** Ground pins, double-height, stacking from
  allocation.
- **G — Geometric realization.** Stories, shape family, loading, ratio. Feet
  are filled by **`realize(s)`** after the strategy is set. A length cap is a
  filter.
- **D — Site disposition.** The stated rectangle and frontage. Streets,
  neighbors, topography, and EnergyPlus wait until those inputs exist.

Two different partitions are different strategies **before** geometry changes.

---

## Value of a run

A useful run reports:

- which **legal** strategies were found (and which P organizations paid off)
- which COVER ideas were **almost legal** and whether REPAIR found a same-idea twin
- which cells are **empty**, and why (unsupported vs locked vs cap-miss vs infeasible)
- when COVER+REPAIR finds **no** legal cells, or very few plus a large frontier:
  a **DIAGNOSE** class and **relaxation probes the architect must choose**
- whether the kept strategy **survived** a program-area shock
- a pending **A/B** among drawings that already fit (click the preferred scheme
  in the UI; at most five questions; pairs chosen to be visibly different)

It does not invent a courtyard, fill a cap, or call three widths of the same
bar three concepts.

---

## What Bayesian optimization is (and is not)

Bayesian optimization is an **evaluation-budget manager** for expensive
simulations. After COVER and REPAIR have filled the archive, a small Gaussian
process ranks unevaluated typed actions by expected improvement among **legal**
schemes, evaluates one, refits, and repeats. It is not an architectural
generator. It does not sit on invented feet — **REPAIR** and **`realize(s)`**
own plates. It is not on the critical path until a run costs minutes.

`search.py` (`balanced` / `low_rise` / `compact`) remains an experimental
enumeration baseline, not “quality.”
