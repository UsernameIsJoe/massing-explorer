# Massing Explorer

A **program-aware massing explorer** that turns architectural program spreadsheets into dimensionally tested conceptual masses. A local LLM reasons about design decisions. A deterministic Python engine owns geometry, GSF, and checks.

The system's value is to make the architect smarter about the design problem: which legal strategies exist, which archive cells are empty and why, which brief clauses collapsed the feasible set. It is not merely "Option 17."

**Standalone project.** Algorithm-first. Constitution: [docs/BLUEPRINT-search.md](docs/BLUEPRINT-search.md). Concept and workflow: [docs/CONCEPT.md](docs/CONCEPT.md), [docs/WORKFLOW.md](docs/WORKFLOW.md).

---

## What it does

1. **Ingests** a program from Excel/CSV
2. **Interprets** a chat brief into requirements, limitations, and preferences
3. **Explores** legal strategies `S = (P, T, V, G, D)` — COVER / LEARN / REFINE over an archive, not a width enumerator
4. **Proves** each move through the massing engine (footprints, pairing, voids, GSF, floors)
5. **Reports** legal vs empty cells, a kept drawing, and an optional A/B pair among schemes that already fit
6. **Remembers** study state across the conversation

A requirement is a must. A limitation is a cap, never a length to draw. A preference may be met more than one way. A ground-floor note is a pin, not a mass. Partitions are proposed only when the brief did not require a grouping.

---

## Architecture

```
                  USER BRIEF
                      |
                      v
           Intent interpreter (reading + brief)
                      |
          Structured design state  S = (P, T, V, G, D)
                      |
           LLM strategy planner  (<=5 typed moves)
                      |
           Constraint / massing engine
           (solver, layout, allocate, pairing, GSF)
                      |
                 Performance
                      |
           Diverse design archive
             /         |         \
          COVER      LEARN      REFINE
```

MCTS sits around planner + engine. Bayesian optimization is a small evaluation-budget manager, not a shape generator. Neither sits on feet. `search.py` remains a width/story enumeration baseline.

**Rule:** the LLM proposes typed actions; the engine applies or rejects them. Never trust the LLM for arithmetic.

---

## Locked decisions

| Topic | Decision |
|-------|----------|
| Program input | **Excel/CSV** (not screenshots) |
| Column structure | **Assumed standard** (Room, Qty, Area, Department) — naming alignment not a priority |
| Room dimensions | **User prompt** or **project config database** |
| Grossing factors | **Vary per project** — set in config or chat |
| Units | **Feet** first |
| UI | **Not a priority** — CLI / minimal interface; algorithm first |
| Options | **One kept drawing** on the study; archive holds several distinct elites; LEARN may ask A/B |
| State | **Remember** across chat turns |
| Adjacency | **Conversational** |
| Output (v1) | **Text report** + plan/site PNGs; optional `.3dm` export |
| GSF tolerance | **±3%** default, user-adjustable |
| Room fit | **Anchor rooms** only; list what is compromised |
| Double-height | Identified in **chat** |
| Site context | **Stated rectangle and frontage** — streets, neighbors, topography wait |
| Repo | **Standalone** (not tied to prior Rhino site-export work) |
| Planning limits | **Chat** + **config file** (e.g. 80 ft academic width) |
| LLM | **Ollama**, local only, no paid APIs |
| Vision/OCR | **Out of scope** — not a priority |

Full rationale: [docs/DECISIONS.md](docs/DECISIONS.md)

---

## Phased plan

| Phase | Focus | Status |
|-------|-------|--------|
| **1** | Excel ingest, GSF engine, config file, CLI, text output | **Complete** |
| **2** | Ollama chat loop, grouping, story count, state memory | **Complete** |
| **3** | Footprint solver, anchor room fit, double-height, stepped floors | **Complete** |
| **4** | Paired masses, site limits from config/chat, resize loop | **Complete** |
| **5** | Floor-by-floor program allocation, floor pins | **Complete** |
| **6** | Width/story enumeration that fits a site envelope (`search.py`) | **Complete** (baseline) |
| **7** | Rhino massing geometry export | Optional / present as `rhino_export.py` |
| **Search 0–8** | Blueprint loop: archive, COVER/LEARN/REFINE, planner, CSP, drawable T, MCTS | **Complete** |
| **BO** | Bayesian optimization as evaluation-budget manager (not a generator) | **In code** |

Phases 3–5 *evaluate* a scheme you specify. Phase 6 enumerates widths and stories.
The blueprint search sits **on top** of that engine: a brief now COVER/LEARN/REFINE
over distinct legal strategies. Every drawing is still re-verified through
`solve_massing_study`.

Details: [docs/PHASES.md](docs/PHASES.md). Progress: [docs/PROGRESS.md](docs/PROGRESS.md).
Decisions: [docs/DECISIONS.md](docs/DECISIONS.md).

---

## Project structure

```
massing-explorer/
├── src/massing_explorer/
│   ├── brief.py            # Intent: roles, grouping, apply brief → search
│   ├── reading.py          # LLM reading of a brief
│   ├── chat.py             # Chat turn; A/B is a LEARN choice
│   ├── explore/            # Strategy archive + COVER/LEARN/REFINE
│   │   ├── controller.py   # Modes over the archive
│   │   ├── strategy.py     # S = (P, T, V, G, D)
│   │   ├── actions.py      # Typed moves; engine apply/reject
│   │   ├── archive.py      # One elite per legal cell
│   │   ├── planner.py      # ≤5 typed actions
│   │   ├── csp.py          # Legal partitions when P is open
│   │   ├── topology.py     # Drawable T; stated D
│   │   ├── mcts.py         # Search over actions, not feet
│   │   ├── bayes.py        # Evaluation-budget manager
│   │   ├── preference.py   # Bradley–Terry on measured traits
│   │   └── performance.py  # Feasibility + measured vector
│   ├── solver.py           # Footprint, pairing, void, site limit solver
│   ├── layout.py           # Drawable plates (bars, L leftover)
│   ├── search.py           # Width/story enumeration baseline
│   ├── allocate.py         # Which department sits on which level
│   ├── visual.py           # Plan/elevation, site, archive boards
│   ├── rhino_export.py     # Optional .3dm solids
│   └── parser/
├── tests/                  # test_phase1 … test_phase6, test_explore
├── docs/
│   ├── BLUEPRINT-search.md # Constitution
│   ├── CONCEPT.md
│   ├── WORKFLOW.md
│   ├── PHASES.md
│   ├── DECISIONS.md
│   └── PROGRESS.md
├── config/
└── examples/
```

---

## Workflow

A chat brief now runs the [blueprint control loop](docs/WORKFLOW.md): interpret →
structured strategy → planner actions → engine → performance → archive →
COVER / LEARN / REFINE.

The older 15-step program-to-massing checklist (GSF, footprints, pairing, floors)
is still what the **engine** automates. See [docs/DECISIONS.md](docs/DECISIONS.md#manual-workflow-reference).
Step 13 (compare alternatives) is no longer deferred: LEARN compares two legal
drawings; the archive keeps several distinct elites.

---

## Getting started

```bash
pip install -e .
python -m massing_explorer ingest examples/underwood_elementary_space_summary.xlsx -c config/project.example.yaml
python -m unittest discover -s tests
```

### CLI commands

```bash
# Parse program file and print report
python -m massing_explorer ingest <program.xlsx|program.csv> -c config/project.example.yaml

# Save report and JSON
python -m massing_explorer ingest program.xlsx -o output/report.txt -j output/program.json

# Start a chat study (requires Ollama running locally)
python -m massing_explorer chat --study underwood -p examples/underwood_elementary_space_summary.xlsx -c config/project.example.yaml

# Resume an existing study
python -m massing_explorer chat --study underwood

# Solve footprints + validation (+ optional PNG visual)
python -m massing_explorer solve --study underwood_checkpoint ^
  -p examples/underwood_elementary_space_summary.xlsx ^
  -c config/project.example.yaml ^
  --demo-grouping ^
  --visual output/massing_checkpoint.png

# Two masses sharing a width inside a total length, with site caps
python -m massing_explorer solve --study underwood_checkpoint ^
  --pair academic,support --pair-length 280 ^
  --max-length 200 --max-total-length 420 ^
  --visual output/massing_checkpoint.png

# Ask what WOULD fit, instead of checking a scheme you already picked.
# `solve` validates widths you supply; `search` finds the widths and story counts.
python -m massing_explorer search --study underwood_checkpoint ^
  -c config/project.example.yaml ^
  --max-total-length 300 --max-length 200 --max-width 100 ^
  --max-stories 5 --preference low_rise

# Then write one of the listed schemes into the study
python -m massing_explorer search --study underwood_checkpoint ^
  --max-total-length 300 --max-length 200 --max-width 100 ^
  --apply 0 --visual output/searched.png

# In-chat commands: /status  /grouping  /solve  /quit

# Show project config (anchor rooms, grossing factors)
python -m massing_explorer config config/project.example.yaml
```

The Excel parser auto-detects header rows and column names from keywords — it is not hardcoded to any single file format. It handles:

- Hierarchical schedules (department header rows + room rows), like MSBA space summaries
- Flat CSV files with a `department` column
- Footer rows with declared NFA, GFA, and grossing factor

---

## License

MIT
