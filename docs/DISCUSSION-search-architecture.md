# Search architecture discussion

Recorded 8 Sep 2026. This is the discussion as it stood. It is not an implementation spec and it does not change the current search code.

**Project law is now `docs/BLUEPRINT-search.md` (9 Sep 2026).** That file turns the three jobs below into COVER / LEARN / REFINE modes over an archive, with a strategy planner and a locked grouping rule. This note stays as the earlier argument.

---

## Proposal

For stage one, generate feasible schemes until additional sampling produces diminishing gains in design-space coverage.

Stage two: a Bradley–Terry or Thurstone-style model is already enough to give a rigorous foundation.

And it lets you discover something more valuable than a ranking:

What characteristics are associated with the user's preference?

For example, the system might infer that the user tends to prefer:

- more distributed massing
- moderate courtyard enclosure
- lower height variance
- stronger street edge

without the user ever explicitly specifying those criteria.

That makes the system a preference-learning design explorer, rather than just a scoring interface.

You could even select which pairs to show based on uncertainty:

- very obvious comparison → low information
- two ambiguous schemes → high information

So stage two can become an active-learning problem.

For stage three, keep several elites. Each becomes a small local exploration basin.

Sequential Monte Carlo / adaptive importance sampling is worth investigating as the conceptual backbone.

Not necessarily because a complicated SMC algorithm is required, but because its logic almost perfectly describes the search already invented.

---

## Response

The idea is the right architecture, with three corrections so it stays a design explorer and does not become a story the model tells about the user.

The useful shift is that each stage has a different job. Stage one maps the legal space. Stage two learns a taste from choices. Stage three spends the remaining tries inside a few weighted regions. What exists now is only the skeleton of that: a coverage sample, a score, and one-step neighbors. The missing middle is the user as the source of preference, and a way to stop concentrating on one idea too soon.

### Stage one

Stopping when further samples stop adding coverage is better than a fixed try count. Sequential experiment design does the same thing: keep adding points while they still change what you know, then stop when the next batch mostly repeats cells already seen.

On this project that coverage is not a smooth cloud of lengths and widths. It is the legal lever space the brief left open, after requirements and caps. A new sample has diminishing value when it lands in a typology already in hand, not when its score is similar. Score can plateau while a whole organization is still unsampled. So the stop rule should watch new legal typologies, not the ranking. Illegal schemes do not count as coverage. They are discarded before the map is drawn.

### Stage two

A Bradley–Terry or Thurstone model is the right foundation, and the valuable output really is the weights, not the ranking. Bradley–Terry says the chance that A beats B is a logistic function of the difference in their utilities. Thurstone is the same idea with a normal noise term. Neither invents "courtyard" or "street edge." They estimate a utility. To discover characteristics, utility has to be a weighted sum of traits the engine measures on schemes it already built. Then the learned weights are the sentence wanted: this user, on this brief, is favoring more spread, less height variance, and so on, even if they never said those words.

Two limits.

First, pairwise choices are the data. A written brief is not a Bradley–Terry sample. Stated requirements and caps stay locked. The model learns only the residual taste among schemes that already fit. If the user never compares, stage two has nothing to fit and should stay a ranking of stated preferences.

Second, a trait that is not measured cannot be inferred. "Moderate courtyard enclosure" is only allowed if some scheme actually has a court and the engine can report an enclosure number. Otherwise the system will narrate a preference it did not observe. Start from a small measured vocabulary that exists across briefs — spread, height variance, alignment, edge, enclosure when a court exists — and treat an unnamed trait as a hypothesis until the geometry can report it.

The active-learning part is also right, with one guard. Information is highest when two feasible schemes are close, because an obvious win barely updates the weights. The literature also warns that only asking near-ties can leave disconnected clusters, so the ratings never sit on one scale. Keep a few connecting comparisons, including an obvious pair when a new basin appears, and spend the rest on ambiguous feasible pairs. Never ask the user to prefer a scheme that broke a requirement or a cap.

### Stage three

Several elites, each a basin, is the right use of what stage two learned. Sequential Monte Carlo is a good description of that, and a bad thing to implement as a physics sampler. The logic is: each scheme is a particle, its weight is how well it matches the learned taste, resampling keeps several elites instead of one winner, and a local move is a one-lever edit inside that basin. Tempering is just the schedule already wanted — start wide, then concentrate, and do not let one particle eat the budget. Effective sample size tells you when the cloud has collapsed and you should stop or reopen a basin. Adaptive importance sampling, in this discrete space, means the next proposals are drawn toward the traits the weights favor, while a few low-weight particles stay so a dismissed direction can come back if a later comparison changes the taste. Do not put a continuous importance density on feet. The engine still owns geometry. The sampler only edits open levers.

### Line to hold

Stage one stops when new legal typologies stop appearing. Stage two is a choice loop that learns weights on measured traits, and uses those weights to pick the next pair. Stage three keeps several weighted elites and explores locally until the weights stop moving. That is a preference-learning explorer. It is not a license to override a must, fill a cap, or invent a criterion the drawings cannot show.
