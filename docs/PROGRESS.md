# Progress log

Running record of work, test results, problems, and design discussions.  
Update this file at the end of every work session.

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
