# Progress log

Running record of work, test results, problems, and design discussions.  
Update this file at the end of every work session.

---

## 2026-09-03 — Phase 4 complete: paired masses + site limits

### Done

- **Paired-mass solver** (`solve_paired_masses`): masses that sit side by side share
  a width solved from their combined floor plate area and a stated total length.
  `W = (P₁ + P₂ + …) / L_total`, then `Lᵢ = Pᵢ / W`. Lengths sum back to `L_total`
  exactly, and each mass keeps its own required area.
- **`plate_area()` extracted** so pairing and single-mass solving share one
  definition of "floor plate needed," including the void carry on multi-story masses.
- **`MassPairing` state** on `StudySession`, persisted with the study.
- **Site limits** (`_site_limits`) merged from config (`planning_limits`, `site`)
  and chat constraints, with chat taking priority. Checks per-mass length, per-mass
  width, per-pairing combined length, and site-wide combined length.
- **Resize suggestions** (`ResizeSuggestion`): when a mass busts a length limit the
  engine offers two concrete outs — a story count that fits, or a width that fits.
- **Resize loop** (`resize_mass` tool): change width and/or stories, then re-solve
  and re-validate in a single call so the LLM can apply a suggestion directly.
- **LLM tools:** `pair_masses`, `clear_pairings`, `resize_mass`. System prompt updated
  to route "fit two masses in 280 ft" to the engine instead of LLM arithmetic.
- **CLI:** `--pair`, `--pair-length`, `--max-length`, `--max-total-length`.
- **Site plan visual** (`render_site_plan`): masses laid end-to-end with per-mass
  dimension lines and the site limit as a pass/fail line. Pairing members are drawn
  contiguous so a shared width reads as a single bar.

### Test results

| Test | Result | Notes |
|------|--------|-------|
| Paired width/lengths | pass | 12,000 + 8,000 SF in 280 ft → W 71.43, L 168 / 112 |
| Areas preserved | pass | `W × Lᵢ` returns each input area |
| Three-mass pairing | pass | 9,000 + 6,000 + 3,000 in 300 ft → W 60 |
| Underwood pairing | pass | academic + support in 280 ft → W 68.1, 164.9 + 115.1 = 280.0 |
| GSF held under pairing | pass | both paired masses still within ±3% |
| Length over limit | pass | suggestion offers stories and width |
| Story suggestion sanity | pass | never suggests the current story count |
| Width over limit | pass | suggests narrowing, reports resulting length |
| Resize width | pass | 2× width → half length, GSF unchanged |
| Resize stories | pass | 3 → 2 stories lengthens plate, GSF unchanged |
| Clear pairings | pass | falls back to fixed-width constraint chain |
| Pairing persistence | pass | survives save/load |
| Site plan visual | pass | PNG written |
| Full suite | pass | 40 tests |

### Problems

- **Resize story suggestion was under-counting.** It divided target GSF by the plate
  cap, but a mass with a double-height void carries extra area in its plate, so the
  HPE/Dining mass was told to "use 2 stories" when it was already 2. Fixed by sizing
  from actual footprint (`plate × stories`) and floor-clamping to `current + 1`.
- **A resize test passed for the wrong reason.** Changing academic from 3 stories at
  80 ft to 2 stories at 120 ft leaves the plate identical (3 × 80 = 2 × 120 = 240),
  so length correctly did not move. Replaced with two tests that isolate width and
  story changes separately.
- **Site plan ordering.** With academic and support paired but HPE unpaired, the
  unpaired mass was drawn between the two paired ones, hiding the shared width.
  Masses are now sorted so pairing members stay adjacent.

### Discussions

- Site-wide combined length sums *all* masses end-to-end. That is a deliberately
  conservative single-row reading of the site; L-shaped or courtyard arrangements
  would need a real 2D packing step, which is out of scope here.
- Pairings live in chat state rather than config, since mass ids only exist after
  grouping. Config carries the site envelope; chat carries which masses share it.

### Next steps

- Phase 5 (optional): Rhino export via rhino3dm — layers per mass/program,
  extruded volumes per floor plate.

---

## 2026-09-03 — Phase 3 complete: dimension solver + checkpoint visual

### Done

- Footprint solver: `other_side = area / fixed_width`
- Equal floor plates from `target_gsf / stories`
- Stepped floors helper (`solve_stepped_floors`)
- Double-height void deduction on floor above (gym etc.)
- Anchor room fit (rotation allowed) + compromised list
- GSF validation with configurable +/-3% tolerance
- CLI: `python -m massing_explorer solve --study ... --demo-grouping --visual ...`
- Chat tool: `solve_dimensions` + `/solve` command
- Checkpoint PNG: plan footprints + schematic elevation
- 6 Phase 3 tests (21 total suite)

### Test results

| Test | Result | Notes |
|------|--------|-------|
| Fixed width 80 ft → L=100 | pass | 24k GSF / 3 stories |
| Gym fit 60x100 | pass | fails in 72x90, passes in 100x120 |
| Double-height void | pass | usable floor1 = plate - void |
| GSF tolerance 2.5% vs 5% | pass | |
| Compromise list | pass | names gym when too small |
| Underwood solve + PNG | pass | 3 masses, GSF exact, gym fits |

### Checkpoint demo (Underwood)

- Academic: 80 x 140.4 ft, 3 stories, 33,689 GSF
- HPE/Dining: 100 x 132.2 ft, 2 stories, gym void on L1 (-6,000 SF)
- Support: 80 x 98.0 ft, 2 stories
- Visual: `output/massing_checkpoint.png`

### Current phase

**Phase 4 — Multi-mass + site limits** — not started

### Next steps

1. Paired-mass shared-width solver
2. Combined length / site limit enforcement
3. Resize loop when one dimension is fixed

---

## 2026-09-02 — Phase 2 complete: Ollama chat loop

### Done

- `StudySession` with masses, constraints, adjacency notes, double-height rooms, chat history
- State persistence to `studies/<id>/state.json`
- Engine tools: `get_department_summary`, `get_grouping_summary`, `set_grouping`, `set_story_count`, `set_constraint`, `add_adjacency_note`, `mark_double_height`
- Ollama HTTP client (stdlib, no extra deps) with tool-calling loop
- CLI: `python -m massing_explorer chat --study NAME --program FILE.xlsx`
- System prompt: `prompts/chat_system.txt`
- In-chat commands: `/status`, `/grouping`, `/quit`
- 8 new unit tests (15 total across Phase 1+2)

### Test results

| Test | Result | Notes |
|------|--------|-------|
| Department summary from engine | pass | 9 depts, NFA 40,462 |
| Set grouping (2 masses) | pass | All departments assigned |
| Set story count | pass | Updates mass |
| Set constraint | pass | academic_width_ft etc. |
| Unknown department rejected | pass | |
| State persistence roundtrip | pass | masses + constraints survive reload |
| Tool dispatch | pass | |
| Phase 1 regression | pass | 7/7 still passing |

### Problems

- Ollama tool-calling requires a model that supports tools (llama3.1+, qwen2.5, etc.). Older models may ignore tools.
- Tool result messages use Ollama `tool_name` field — verify with your model if tool loop stalls.

### Current phase

**Phase 3 — Dimension solver + validation** — not started

### Next steps

1. Footprint solver: `other_side = floor_area / fixed_side`
2. Per-floor area from story count
3. Anchor room fit check
4. GSF validation (±3%)
5. Text massing report with dimensions

---

## 2026-09-02 — Phase 1 complete: program engine

### Done

- Built `src/massing_explorer/` package with flexible Excel/CSV parser
- Column detection via keyword matching (not hardcoded to one file layout)
- Hierarchical section parsing (department header rows + room rows)
- Flat CSV support with `department` column
- Footer extraction: declared NFA, GFA, grossing factor from file
- GSF engine: `Target GSF = NFA × area_adjustment × grossing_factor`
- Config loader (`config/project.example.yaml`)
- CLI: `ingest` and `config` commands
- Text report with department table, verification, room detail
- Added `examples/underwood_elementary_space_summary.xlsx` (user-provided MSBA format)
- Unit tests in `tests/test_phase1.py` (7 tests, all passing)

### Test results

| Test | Result | Notes |
|------|--------|-------|
| Parse Underwood Excel | pass | 41 rooms, 9 departments |
| Department NFA totals | pass | All 9 departments match declared ±0 SF |
| Building NFA | pass | Computed 40,462 = declared 40,462 SF |
| File GFA | pass | Declared 60,693 = NFA × 1.5 grossing |
| Config GSF | pass | Target 69,797 = NFA × 1.15 × 1.50 |
| CSV example | pass | 14 rooms from flat CSV |
| Config anchor rooms | pass | gym 60×100 ft loaded |
| CLI exit 0 | pass | Report + JSON written |

### Problems

- **Two GSF conventions:** MSBA files use `GFA = NFA × grossing_factor` (1.5). Config adds `area_adjustment` (1.15) for massing target. Report shows both; massing study should use config values unless user overrides in chat.
- **Qty = 0 rooms:** Parsed but contribute 0 SF (e.g. Assistant Principal's Office). Correct behavior.
- **Windows console encoding:** Replaced em-dash in report header with ASCII hyphen.

### Discussions

- Parser picks best Excel sheet by room count among sheets matching space/program/summary keywords.
- Department rows detected when row has area total but no per-room NFA.
- Not hardcoded to Underwood — keyword column mapper handles similar MSBA-style layouts.

### Current phase

**Phase 2 — LLM chat loop (Ollama)** — not started

### Next steps

1. Ollama client wrapper
2. Study state object + disk persistence
3. System prompt (reasoner only, calls engine tools)
4. CLI `chat` command
5. Tools: `get_department_summary`, `set_grouping`, `set_story_count`

---

## 2026-09-02 — Project reset & planning locked

### Done

- Removed outdated Rhino site-context scripts
- Locked design decisions → [DECISIONS.md](DECISIONS.md)
- Wrote phased plan → [PHASES.md](PHASES.md)

### Current phase

Superseded by Phase 1 completion above.

---

## Template for future entries

```markdown
## YYYY-MM-DD — [Phase N] [short title]

### Done
- ...

### Test results
| Test | Result | Notes |
|------|--------|-------|
| ... | pass/fail | ... |

### Problems
- ...

### Discussions
- ...

### Next steps
- ...
```
