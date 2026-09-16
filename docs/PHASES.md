# Phased implementation plan

Step-by-step build plan. Do not skip validation gates between phases.

Phases 1–6 built the **engine** (ingest, chat, solver, pairing, floors, width
search). The **control loop** on that engine is [BLUEPRINT-search.md](BLUEPRINT-search.md):
COVER / REPAIR / LEARN / REFINE over a strategy archive. See [CONCEPT.md](CONCEPT.md)
and [WORKFLOW.md](WORKFLOW.md).

---

## Phase 1 — Program engine (no LLM)

**Goal:** Read a program file, compute GSF, verify totals, emit a text summary.

### Deliverables

- [x] `src/` package scaffold
- [x] Excel/CSV parser (flexible column + section detection)
- [x] Program verification (room areas × qty → department totals → grand total)
- [x] GSF calculator with configurable `area_adjustment` and `grossing_factor`
- [x] `config/project.yaml` loader (grossing, anchor rooms, planning limits)
- [x] CLI: `python -m massing_explorer ingest program.xlsx`
- [x] Text report: departments, NFA, target GSF per department

### Test criteria

| Test | Pass condition |
|------|----------------|
| Parse example CSV | All rooms loaded, departments grouped |
| Verify totals | Engine-detected sum matches declared department total (±0 SF) |
| GSF calc | School example: program × 1.15 × 1.50 matches hand calculation |
| Config load | Anchor room `gym: 60×100` readable from YAML |
| CLI runs | Exit 0, report printed |

### Example input

`examples/program.example.csv` — simplified school program.

---

## Phase 2 — LLM chat loop (Ollama)

**Goal:** Conversational grouping and story-count proposals with persistent study state.

### Deliverables

- [x] Ollama client wrapper (model configurable)
- [x] Study state object (program, groupings, constraints, messages)
- [x] State persistence to disk (`studies/<id>/state.json`)
- [x] System prompt: LLM role = reasoner, must call engine tools for math
- [x] Tool interface: `get_department_summary`, `set_grouping`, `set_story_count`, `set_constraint`, `add_adjacency_note`, `mark_double_height`
- [x] CLI chat: `python -m massing_explorer chat --study school_demo`

### Test criteria

| Test | Pass condition |
|------|----------------|
| Ollama connect | Ping local model, get response |
| Grouping proposal | LLM groups departments into 3+ masses from example program |
| State recall | Second message references prior grouping without re-stating |
| No LLM math | LLM never returns GSF numbers not produced by engine tool |

---

## Phase 3 — Dimension solver + validation

**Goal:** Given grouping + story count + constraints, solve footprints and validate.

### Deliverables

- [x] Footprint solver: `other_side = floor_area / fixed_side`
- [x] Per-floor area from story count: `avg_plate = target_gsf / floors`
- [x] Stepped floor support (unequal floor areas per level)
- [x] Double-height void deduction (chat-flagged rooms)
- [x] Anchor room fit check inside proposed rectangle
- [x] GSF validation with ±3% tolerance (configurable)
- [x] Text massing report + simple plan/elevation PNG checkpoint

### Test criteria

| Test | Pass condition |
|------|----------------|
| Fixed width 80 ft | Academic GSF 24,000 SF, 3 stories → plate 8,000 SF → L = 100 ft |
| Gym fit | 60×100 gym fails in 72×90 mass, passes in 100×120 mass |
| Double-height | Gym on floor 0 voids same footprint on floor 1 |
| GSF tolerance | 2.5% under target → pass; 5% under → fail |
| Compromise list | Report names gym when width insufficient |

---

## Phase 4 — Multi-mass + site limits

**Goal:** Paired masses, shared widths, combined length constraints from config/chat.

### Deliverables

- [x] Paired-mass solver: W = (A₁+A₂)/L_total, then Lᵢ = Aᵢ/W (`solve_paired_masses`)
- [x] Site limit enforcement from config + chat (max length, max width, combined length)
- [x] Resize loop: `resize_mass` changes width/stories, then re-solves and re-validates
- [x] Resize suggestions when a mass busts a limit (add stories, or widen)
- [x] Chat tools: `pair_masses`, `clear_pairings`, `resize_mass`
- [x] CLI: `--pair`, `--pair-length`, `--max-length`, `--max-total-length`
- [x] Site plan visual with pairing members drawn contiguous against the limit line

### Test criteria

| Test | Pass condition | Result |
|------|----------------|--------|
| Paired masses | Academic 12,000 SF + Support 8,000 SF in 280 ft → W, L₁, L₂ correct | PASS — W = 71.43, L₁ = 168, L₂ = 112 |
| Max length | Mass exceeding limit triggers resize suggestion | PASS — suggests added stories or wider plate |
| Recalc on change | Changing width recalculates GSF and re-validates | PASS — 2× width halves length, GSF held |
| Pairing persistence | Pairings survive save/load | PASS |

**Status: complete** — 40 tests passing.

---

## Phase 5 — Floor-by-floor program allocation

**Goal:** Answer "what program on which layer" with real area math, not a
keyword heuristic that listed every department on every floor.

### Deliverables

- [x] `ProgramAllocation` model; `FloorPlate.allocations` + `utilization`
- [x] Allocator (`allocate.py`): department area poured over levels, cut only
      where a floor runs out, so departments stay contiguous
- [x] Quantity expansion — a program row of qty 18 is 18 placeable rooms
- [x] Vertical position driven by `floor_preferences` config keywords
- [x] Double-height room owners forced to grade (the void is cut above them)
- [x] Token-fragment rule: no meaningless department slivers unless the area
      has nowhere else to go
- [x] Conservation + floor capacity validation checks
- [x] `pin_department_to_floor` / `unpin_department` chat tools
- [x] Report shows area + utilization per level; elevation drawing is split by
      program share

### Test criteria

| Test | Pass condition | Result |
|------|----------------|--------|
| Conservation | Every department's GSF fully placed | PASS |
| Capacity | No floor allocated beyond usable area | PASS — 100% on all Underwood floors |
| Contiguity | A department is not scattered to back-fill gaps | PASS — Core Academic L0–L1, SpEd L2 |
| Qty expansion | 18 classrooms distribute across floors | PASS |
| Ground affinity | Dining/media land low, academic/art land high | PASS |
| Void owner | Gym department sits at grade, void on floor above | PASS |
| Pin | `pin_department_to_floor` overrides affinity | PASS |
| Fragment rule | Sliver avoided when possible, kept when forced | PASS (both branches) |

**Status: complete** — 68 tests passing.

---

## Phase 6 — Scheme search (auto-fit) [COMPLETE]

**Goal:** Answer "what would fit?", not just "does this fit?". Phases 3-5 only
ever *evaluated* a scheme the user specified; the site limits were checks that
never influenced the geometry. This phase searches for the geometry.

Free variables are story count and width per mass. Everything else follows
(`plate = target GSF / stories`, `length = plate / width`), so a mass is
feasible when one width satisfies both caps at once:
`plate / max_length <= width <= max_width`. For a pairing the shared frontage
fixes the width outright, so only the story counts vary.

### Deliverables

- [x] `SiteEnvelope` / `MassOption` / `SchemeCandidate` models
- [x] Feasible width-and-story enumeration per mass, pairing-aware
- [x] Combination search with frontage pruning and a hard candidate bound
- [x] Ranking by `balanced` / `low_rise` / `compact`, plus a daylight-depth
      penalty so the search does not simply max out every width
- [x] **Every returned candidate re-verified through `solve_massing_study`**
- [x] `search_site_schemes` + `apply_scheme` tools, `search` CLI command

### The trust rule

The search only *proposes*. Each candidate is applied to a throwaway copy of the
session and run through the same `solve_massing_study` that writes the reports;
anything it rejects is discarded. There is deliberately no second dimension or
validation path that could disagree with the solver, and the search is kept at
least as strict as the solver on every shared limit.

### Test criteria

| Test | Pass condition | Result |
|------|----------------|--------|
| Finds what hand-picked widths missed | 70/100 ft needs 324.7 ft; under a 300 ft cap the search returns a verified scheme | PASS |
| Search/solver agreement | all 98 candidates in a full enumeration pass the solver | PASS |
| Caps respected | no candidate exceeds width, length, story or frontage caps | PASS |
| Infeasible envelope | returns no candidates plus a note naming the shortest possible layout | PASS |
| Preference matters | `low_rise` is shorter and longer than `compact` | PASS |
| Daylight | classroom bar stays <= 90 ft even when 120 ft is allowed | PASS |
| Score stability | a scheme's score does not change with `top_n` | PASS |
| Pairing | members share one width and sum to the stated frontage | PASS |
| Scenario C | the case that needed manual reasoning now solves automatically | PASS |

Chat no longer *stops* at this enumerator. `search.py` remains the width/story
**baseline**. A brief now runs COVER / REPAIR / LEARN / REFINE over distinct
strategies in `explore/` (next section).

---

## Strategy search (blueprint, 9 Sep 2026) [COMPLETE]

**Goal:** Drive the existing engine with the control loop in
[BLUEPRINT-search.md](BLUEPRINT-search.md). COVER / LEARN / REFINE are modes
over a design archive, not Stage 1 → 2 → 3, and not a width enumerator.

Concept: [CONCEPT.md](CONCEPT.md). Workflow: [WORKFLOW.md](WORKFLOW.md).

### Deliverables

- [x] Constitution — this loop is project law
- [x] Spine — `explore/`: strategy `S = (P, T, V, G, D)`, typed actions, archive, performance vector, controller
- [x] Modes — COVER / LEARN / REFINE; pairwise LEARN; lineage REFINE
- [x] Open P — legal partitions only when grouping was not required
- [x] Planner — one LLM, ≤5 typed actions, engine apply/reject
- [x] Explain and robustness — empty-cell sentences; program-area shock on the same strategy
- [x] CP/CSP — distinct feasible partitions; the LLM does not invent P
- [x] T and D — only drawable topologies and stated site; courtyard unsupported
- [x] MCTS — over design actions from several COVER elites; planner is expansion prior; `search.py` stays the baseline
- [x] Bayesian optimization in `explore/bayes.py` — sequential EI budget manager, not a generator, not on invented feet
- [x] REPAIR / frontier — violation distance, project illegal COVER ideas onto the same concept, MCTS from legal ∪ frontier, DIAGNOSE on low yield

### The trust rule (unchanged)

The engine stays the engine. Search only *proposes*. Each candidate still goes
through `solve_massing_study`. An unsupported topology is not a coverage failure.
A required grouping is not a Monte Carlo coordinate.

### Test criteria

| Test | Pass condition |
|------|----------------|
| Locked P | Four named masses + gym with dining → one partition |
| Open P | No grouping required → CSP lists several legal organizations |
| Split illegal | `SPLIT_MASS` rejected when mass count was required |
| Courtyard | Named, unsupported, not a failed sample |
| Planner cap | At most five typed actions; invented feet dropped |
| LEARN | A/B among feasible elites; a written brief is not a choice |
| REPAIR | Slight site-length miss can become legal without regrouping P |
| MCTS | Starts from COVER elites and frontier near-misses; typed actions; nearby width is a local step |
| Chat path | `apply_brief` still solves and reports failed checks |

---

## Phase 7 — Rhino export (optional)

**Goal:** Generate editable massing geometry from MassingStudy JSON.

### Deliverables

- [ ] MassingStudy → Rhino script or `.3dm` via rhino3dm
- [ ] Layers per mass / per program (allocations now make this meaningful)
- [ ] Extruded volumes per floor plate

### Test criteria

| Test | Pass condition |
|------|----------------|
| Import | Open in Rhino 8, masses match reported dimensions |
| Layers | Programs identifiable by layer/name |

---

## Phase dependency graph

```
Phase 1 (engine)
    ↓
Phase 2 (LLM chat)
    ↓
Phase 3 (solver + validation)
    ↓
Phase 4 (multi-mass + site)
    ↓
Phase 5 (program per level)
    ↓
Phase 6 (scheme search / auto-fit) — enumeration baseline
    ↓
Strategy search (blueprint 0–8) — archive + COVER/REPAIR/LEARN/REFINE
    ↓
Phase 7 (Rhino) — optional
```

---

## Out of scope (all phases for now)

- Screenshot / vision program ingest
- Room-level 2D layout within a floor plate (allocation is by area, and room
  names per floor are indicative only)
- Searching over *groupings when the brief already required them* — open P is
  enumerated only when grouping was not a must
- Courtyard / podium / perpendicular site packing until layout can draw them
- Streets, neighbors, topography, EnergyPlus (D waits for those inputs)
- Cloud LLM APIs
- Angled site boundaries
- Treating Bayesian optimization as a shape generator or putting it on feet

The **studio UI** (`ui_app.py` / `run_ui.bat`) is in scope — see [UI.md](UI.md).
It drives the same COVER / REPAIR / LEARN loop as chat; it is not a second
search engine.

---

## How to advance a phase

1. Implement deliverables
2. Run all test criteria — document results in [PROGRESS.md](PROGRESS.md)
3. Note problems / design discussions in PROGRESS.md
4. Get user review before starting next phase
