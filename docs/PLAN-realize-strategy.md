# Plan: strategy-level BO + deterministic realize(s)

Recorded 11 Sep 2026. Constitution: [BLUEPRINT-search.md](BLUEPRINT-search.md).
This is the next search-control pass: BO and MCTS stay on architectural
strategy; exact feet are filled by one cheap inner solver.

---

## Split to freeze

**Strategy `s`** (search may change): partition P, topology T, loading,
envelope, plate profile, **story counts**.

**Dimensions `d`** (only `realize` may change): width, length, ratio
projection, plate sizes inside the chosen profile.

`realize` must **not** change stories, regroup P, or flip topology. A +1
story remains an MCTS/BO action. If the plate cannot become legal at the
stated stories, return the best `d` plus **feasibility distance**, not a
silent story bump.

Inner objective is two-stage, not one max Quality:

1. Minimize feasibility distance (get legal).
2. Among legal `d`, maximize existing `search_reward` / four eval axes
   (LEARN taste if weights exist).

```
BO / MCTS / COVER / REPAIR
            │
            ▼
        realize(s)   ← width / length / ratio only
            │
            ▼
         measure → archive
```

BO learns `F(s)` = quality of that realized drawing, not a width coordinate.

---

## 1. Pairwise program organization (GP)

Replace the SHA scalar in `explore/axes.py`.

- Canonical department order = sorted names on the study (stable within a run).
- For each pair `i<j`: `1` if same mass, else `0`.
- Pad to a fixed block (`C(12,2)=66`) so all GP rows share a length.
- `encode_strategy` becomes `[p_block..., mass_count, balance, topology,
  loading, mean_height, artic, vertical, geometric]`.
- Product language still calls this **one** program-organization axis;
  internally it is a P-block. Do **not** add fake adjacency the engine cannot
  draw (pairing stays T; floor pins stay V).

Isotropic RBF stays. Hamming-like distance on 0/1 bits is enough; no ARD
this pass.

## 2. `realize(s)` — cheap inner dimension solver

New `explore/realize.py`. **Not** a full `search.py` grid (that enumerates
stories + widths and is too expensive per MCTS sim).

Given frozen stories / P / T / loading / plate / envelope:

- Plate area from GSF / stories (voids as today).
- Short width candidate list per unlocked mass: loading/classroom default,
  locked required width, `ResizeSuggestion` width, width that sits on the
  length cap, hard ratio-band projection, current width. Clamp to min/max
  edge. Tiny inward margin so float length is not 0.00005 ft over the cap.
- Paired masses: width is not independent; use the solver’s pairing relation.
- Evaluate a handful of combinations (cap ~6–8 solves).
- Pick legal with best reward; else lowest feasibility distance.
- Write chosen `mass_id_width_ft`, solve once more, measure.

Envelope is a **preference among legal `d`** (compact vs elongated vs
balanced), not extra COVER width ranks.

## 3. Every evaluation goes through realize

`controller._evaluate`, BO apply-then-solve, and MCTS rollout score call
`realize` then `measure` then `insert`.

COVER: after a sample sets `s`, call `realize` instead of padding the pool
with `geom_rank` / alternate widths. Keep envelope as a strategy axis.

REPAIR: restore idea → `realize` → insert. Stories stay an MCTS lever.

## 4. Strip dimensional actions from BO and MCTS

Remove `SET_WIDTH` ±10 from the **search catalog**. Keep `SET_WIDTH` as an
engine action for `realize` to apply internally.

BO candidates: `SET_STORIES`, `SET_LOADING`, `SET_ENVELOPE`, `PAIR_MASSES` /
`CLEAR_PAIRINGS`, `APPLY_PARTITION` when P is open, `PIN_GROUND`.

Archive identity (`cell_key`) matches `idea_key`: no exact feet, no geom
ranks. Store realized plates on the entry as today.

## 5. Scoring

Do **not** change `search_reward`: illegal still 0. Realize is what turns a
0.5% miss into a legal `F(s)`. Dual GP (quality | feasibility) and
width-band robustness are out of scope.

## Explicitly deferred

- COVER max-distance sampler
- GP ARD / dual output
- Robustness over a width band
- Letting `realize` change story count
