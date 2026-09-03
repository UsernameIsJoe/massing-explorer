# Phased implementation plan

Step-by-step build plan. Do not skip validation gates between phases.

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

## Phase 6 — Rhino export (optional)

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
Phase 6 (Rhino) — optional
```

---

## Out of scope (all phases for now)

- Screenshot / vision program ingest
- Multiple simultaneous massing options
- Room-level 2D layout within a floor plate (allocation is by area, and room
  names per floor are indicative only)
- L-shaped / courtyard site packing (combined length is read as a single row)
- Rhino site context import
- Web UI
- Cloud LLM APIs
- Angled site boundaries

---

## How to advance a phase

1. Implement deliverables
2. Run all test criteria — document results in [PROGRESS.md](PROGRESS.md)
3. Note problems / design discussions in PROGRESS.md
4. Get user review before starting next phase
