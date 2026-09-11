# Plan: implement the search architecture

Recorded 8 Sep 2026. This is the implementation plan for `docs/DISCUSSION-search-architecture.md`. It is not a rewrite of the solver, the brief roles, or the L-shape.

**Superseded as project law by `docs/BLUEPRINT-search.md` (9 Sep 2026; REPAIR / frontier 11 Sep 2026).** The three jobs below (cover, learn, refine) remain. They are modes over a design archive, not a one-way Stage 1 → 2 → 3 pipeline. Illegal COVER samples are ranked by distance-to-feasibility and may be projected onto the same idea. Build order, grouping rule, and the control loop live in the blueprint. Product pages: [CONCEPT.md](CONCEPT.md), [WORKFLOW.md](WORKFLOW.md).

---

## What stays fixed

The new stages sit on top of the pipeline that already classifies the brief and draws the masses. They do not reopen those decisions.

- The three roles stay as they are. A requirement is still a must. A limitation is still a cap, never a length to draw. A preference is still desired, and may be met in more than one way. "On the ground floor" is still a floor pin, not its own mass. A per-mass preference, such as a thin bar or a 1:1 box, stays a preference under those musts.
- The L-shape stays as it is. Voids, leftover around a double-height room, arm depth, and the Rhino export are the solver's job. Stage one only asks the solver for another legal scheme. It does not draw a new footprint.
- Brief lock stays as it is. A later chat turn still cannot undo a required wing, pair masses so the length lands on the cap, or invent a dimension.

A scheme is legal only if every requirement holds and every limitation is a cap that was checked, never a length to draw. The new preference model learns only among schemes that already fit. If the user never compares two drawings, there is nothing to learn, and stage two stays a ranking of what they already said.

What exists now is a coverage sample, a score, and one-step neighbors. The work is to make those three stages do what the discussion actually says.

---

## Stage one — cover the legal space, then stop

Today stage one stops at a try cap after each open lever setting has been seen once. That is a minimum sample, not a coverage stop.

Change the stop rule. Keep generating feasible schemes while a new one still opens a typology that is not already on the map. A typology is the lever pattern already used for a basin: organization, shape family, story band, loading. A new sample is worthless for coverage when it lands in a cell already seen, even if its score is different. Stop when a batch adds no new legal typology, or when the safety cap hits. If the cap hits first, say the map is incomplete and do not pretend stage two has seen the space. Illegal schemes never count toward coverage.

This stays in `try_loop.py`. No new model. The output of stage one is a set of feasible schemes plus the typology map, not a winner.

## Stage two — learn a taste from choices

This is the missing middle, and it is a conversation, not another solver pass.

First, measure traits on schemes the engine already built. Start with numbers the geometry can report on any brief: how spread the masses are, height variance, how alike the footprints are, edge alignment. Enclosure or street edge only appear when the scheme actually has a court or a frontage the engine can measure. An unmeasured trait is a hypothesis, not a preference. Put those measurements in a small module, for example `traits.py`, computed from a solved study. Do not ask the model to invent them.

Then a Bradley–Terry fit on those traits. The chance that A beats B is a logistic function of the difference in their utilities, and utility is a weighted sum of the measured traits. Thurstone is the same idea if a normal noise term is easier later. The useful result is the weight vector: which measured characteristics this user is favoring on this brief. The ranking is a byproduct.

The data is pairwise choices among feasible schemes only. A written brief is not a sample. Never show a pair that broke a requirement or a cap. Pick the next pair by information: an obvious win teaches almost nothing; two feasible schemes the weights cannot separate teach a lot. Also keep a few connecting comparisons, including an obvious pair when a new basin appears, so the ratings stay on one scale. Stop asking when the weights stop moving, or when the user stops choosing.

This needs a small choice loop in the chat path: show two schemes, accept "A" or "B", update the weights, pick the next pair. Persist the weights and the comparisons on the study. Until that loop exists, stage two must not claim it discovered a taste.

## Stage three — several weighted elites, local moves

Do not implement a particle-physics sampler. Use the sequential Monte Carlo idea as the schedule.

Each feasible typology is a particle. Its weight is how well it matches the learned taste, or the stated preferences if the user has not compared yet. Keep several elites, not one winner. A move is one open lever, one step, inside that basin. After a comparison, reweight. Resample so a heavy basin gets more local tries and a light basin is not deleted on one vote. Effective sample size says when the cloud has collapsed: stop, or reopen a basin if the user is still unsure. A few low-weight elites stay so a dismissed direction can come back.

This replaces the current "pick the best basin and take eight neighbors" ending in `improve_scheme`. Stage three does not run until stage one has a finished map, and it does not invent dimensions or fill a cap.

---

## Order

1. Stage one stop rule on new legal typologies, with an explicit incomplete-map report.
2. Trait measurements on a solved scheme, with a test that a known drawing produces the expected numbers.
3. Bradley–Terry weights from stored pairwise choices, still no chat. Tests on fake choices.
4. The choice loop: two feasible drawings, a choice, an updated weight sentence the user can read.
5. Stage three as several weighted elites and local moves, using stated preferences until weights exist, then the weights.

Do not start with stage three. Without a coverage stop and a measured utility, it is just the neighbor walk already in place, with a more expensive name.

---

## Line to hold

This is a way to explore among legal schemes. It is not a license to undo a requirement, fill a cap, or redraw a mass the solver already owns.
