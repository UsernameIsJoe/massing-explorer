# Concept

Recorded 9 Sep 2026. Project law is [BLUEPRINT-search.md](BLUEPRINT-search.md). This page is the product idea in plain language.

---

## What this is

Massing Explorer turns a program spreadsheet into conceptual masses that have been dimensionally tested. It exists to make the architect smarter about the **design problem**: which legal strategies exist, which regions of the archive are empty and why, which brief clauses collapsed the feasible set.

It is not merely “Option 17.” It is not `prompt → AI shape`. It is not `parameters → optimizer → shape`.

Architecture is a sequence of semantic decisions an LLM can reason about. Computation proves whether those decisions can become buildings:

```
language reasoning
    → design actions
    → constraint / massing engine
    → performance evidence
    → reasoning again
```

The LLM proposes. The engine owns geometry, GSF, pairing, L-shape/voids, and checks. Never trust the LLM for arithmetic.

---

## Three roles in every brief clause

| Role | Meaning | Example |
|------|---------|---------|
| **Requirement** | Must happen. Locked. | Four masses. Gym with dining. |
| **Limitation** | A cap to check. Never a length to draw. | Max 400 ft frontage. At most 3 stories. |
| **Preference** | Desired. May be met more than one way. | Thin bars. Art on the ground floor. |

A ground-floor note is a **pin**, not its own mass. A later chat turn still cannot undo a required wing, pair masses so the length lands on the cap, or invent a dimension.

---

## A strategy is not a width

A strategy is a sequence of architectural decisions:

```
S = (P, T, V, G, D)
```

- **P — Program organization.** Mass count, who is together, who is apart. Alternative partitions are proposed **only when the brief did not require a grouping**.
- **T — Topological strategy.** Only what layout can draw: independent bars, paired bars, L leftover around a void. Courtyard, podium, and perpendicular wings are named but unsupported until the drawing can realize them.
- **V — Vertical strategy.** Ground pins, double-height, stacking from allocation.
- **G — Geometric realization.** Stories, shape family, loading, ratio. Feet are not coordinates. A length cap is a filter.
- **D — Site disposition.** The stated rectangle and frontage. Streets, neighbors, topography, and EnergyPlus wait until those inputs exist.

Two different partitions (`academic + arts / gym + dining` vs `academic / arts + gym + dining`) are different strategies **before** geometry changes. They are different archive cells only when P was open.

---

## Value of a run

A useful run reports:

- which **legal** strategies were found
- which cells are **empty**, and why (unsupported vs locked vs cap-miss vs infeasible)
- whether the kept strategy **survived** a program-area shock
- a pending **A/B** pair among drawings that already fit, if LEARN has two elites

It does not invent a courtyard, fill a cap, or call three widths of the same bar three concepts.

---

## What Bayesian optimization is (and is not)

Bayesian optimization is an **evaluation-budget manager** for expensive simulations. A small Gaussian process ranks unevaluated typed actions by expected improvement, then spends a few engine evaluations. It is not an architectural generator. It does not sit on feet. It is not on the critical path until a run costs minutes.

`search.py` (`balanced` / `low_rise` / `compact`) remains an experimental enumeration baseline, not “quality.”
