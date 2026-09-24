# Massing Explorer

A **self-aware strategic search system** for conceptual massing: given a
program spreadsheet, hard constraints, soft preferences, and limited compute,
it maps meaningful strategy regions, probes what must be understood, focuses
where evidence is strongest, and (increasingly) tracks what it deferred —
then proposes best-supported schemes with trade-offs, not merely “Option 17.”

It is **not** a parametric form generator, CAD automation toy, LLM driving
Rhino, GA of lookalikes, or brute-force optimizer. Form follows strategy;
`realize(s)` fills dimensions. A local LLM may propose typed actions; a
deterministic Python engine owns geometry, GSF, and checks.

**Standalone project.** Constitution: [docs/BLUEPRINT-search.md](docs/BLUEPRINT-search.md)
(purpose, direction, **fit/drift evaluation**). Concept:
[docs/CONCEPT.md](docs/CONCEPT.md). Workflow: [docs/WORKFLOW.md](docs/WORKFLOW.md).
Studio: [docs/UI.md](docs/UI.md).

---

## What it does

1. **Ingests** a program from Excel/CSV
2. **Interprets** a chat or UI brief into requirements, limitations, and preferences
3. **Explores** strategies `S = (P, T, V, G, D)` — COVER / REPAIR / LEARN / REFINE
   over an archive (expanding **P pool** + `realize(s)` for feet), not a width enumerator
4. **Proves** each move through the massing engine (footprints, pairing, voids, GSF, floors)
5. **Projects** near-illegal COVER ideas onto the same concept when a typed action can fix a hard miss
6. **Reports** legal vs empty cells, a kept drawing, optional A/B among schemes that already fit, and a near-feasible frontier
7. **Remembers** study state across the conversation / UI session

A requirement is a must. A limitation is a cap, never a length to draw. A
preference may be met more than one way. A ground-floor note is a pin, not a
mass. Partitions are proposed only when the brief did not require a grouping.

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
           Constraint / massing engine + realize(s)
           (solver, layout, allocate, pairing, GSF)
                      |
                 Performance
                      |
           Diverse design archive + P pool
                      |
         COVER → REPAIR → LEARN / REFINE
                      |
         illegal COVER idea → nearest legal twin
         or near-feasible frontier (MCTS start, not LEARN)
```

MCTS sits around planner + engine and may start from legal elites **or**
near-feasible frontier samples. Structural moves such as `APPLY_PARTITION`
are scored as leaves. Bayesian optimization is a small evaluation-budget
manager on **legal** actions. REPAIR owns “nudge this plate onto the cap.”
`search.py` remains a width/story enumeration baseline.

**Rule:** the LLM proposes typed actions; the engine applies or rejects them.
Never trust the LLM for arithmetic.

---

## Locked decisions

| Topic | Decision |
|-------|----------|
| Program input | **Excel/CSV** (not screenshots) |
| Column structure | **Assumed standard** (Room, Qty, Area, Department) — naming alignment not a priority |
| Room dimensions | **User prompt** or **project config database** |
| Grossing factors | **Vary per project** — set in config or chat |
| Units | **Feet** first |
| UI | **Studio UI** (`ui_app` / `run_ui.bat`) plus CLI chat — algorithm remains source of truth |
| Options | **One kept drawing** on the study; archive holds several distinct elites; LEARN may ask A/B (click card, max 5) |
| State | **Remember** across chat / UI turns |
| Adjacency | **Conversational** |
| Output (v1) | **Text report** + plan/site PNGs + live 3D preview; optional `.3dm` export |
| GSF tolerance | **±3%** default, user-adjustable |
| Room fit | **Anchor rooms** only; list what is compromised |
| Double-height | Identified in **chat** / brief |
| Site context | **Stated rectangle and frontage** — streets, neighbors, topography wait |
| Repo | **Standalone** |
| Planning limits | **Chat** + **config file** (e.g. 80 ft academic width) |
| LLM | **Ollama**, local only, no paid APIs |
| Vision/OCR | **Out of scope** |

Full rationale: [docs/DECISIONS.md](docs/DECISIONS.md)

---

## Phased plan

| Phase | Focus | Status |
|-------|-------|--------|
| **1–5** | Ingest, chat, footprints, pairing, floors | **Complete** |
| **6** | Width/story enumeration (`search.py`) | **Complete** (baseline) |
| **7** | Rhino massing export | Optional / present |
| **Search 0–8** | Blueprint loop: archive, COVER/LEARN/REFINE, planner, CSP, MCTS | **Complete** |
| **BO / REPAIR / realize / P pool** | Budget manager, near-miss projection, strategy→feet, expanding P | **In code** |
| **Studio UI** | Drop Excel, brief, Generate, pool, click A/B | **In code** |

Details: [docs/PHASES.md](docs/PHASES.md). Progress: [docs/PROGRESS.md](docs/PROGRESS.md).

---

## Project structure

```
massing-explorer/
├── src/massing_explorer/
│   ├── brief.py, reading.py, chat.py
│   ├── ui_app.py           # Studio UI
│   ├── explore/            # Strategy archive + COVER/REPAIR/LEARN/REFINE
│   │   ├── controller.py, cover.py, p_pool.py, realize.py
│   │   ├── csp.py, mcts.py, bayes.py, preference.py, …
│   ├── solver.py, layout.py, allocate.py, search.py
│   ├── preview3d.py, visual.py, rhino_export.py
│   └── parser/
├── tests/
├── docs/                   # BLUEPRINT, CONCEPT, WORKFLOW, UI, …
├── config/
├── examples/               # incl. Underwood tweaked GSF + 3-mass brief
└── run_ui.bat
```

---

## Workflow

A brief runs the [blueprint control loop](docs/WORKFLOW.md): interpret →
structured strategy → COVER (P pool) → REPAIR → planner / MCTS / BO / REFINE →
LEARN. Studio steps: [docs/UI.md](docs/UI.md).

The engine still automates the older program-to-massing checklist (GSF,
footprints, pairing, floors). See [docs/DECISIONS.md](docs/DECISIONS.md).

---

## Getting started

```bash
pip install -e .
python -m unittest discover -s tests

# Studio UI (recommended for briefs + A/B)
run_ui.bat
# → http://127.0.0.1:8765/  — restart after git pull so code reloads

# CLI ingest / chat / solve / search
python -m massing_explorer ingest examples/underwood_elementary_space_summary.xlsx -c config/project.example.yaml
python -m massing_explorer chat --study underwood -p examples/underwood_elementary_space_summary.xlsx -c config/project.example.yaml
```

Regression fixture for a tight three-mass brief:

- `examples/Underwood_Elementary_Space_Summary_GSF_Tweaked.xlsx`
- `examples/underwood_3mass_brief.txt`

### More CLI

```bash
python -m massing_explorer solve --study underwood_checkpoint ^
  -p examples/underwood_elementary_space_summary.xlsx ^
  -c config/project.example.yaml --demo-grouping --visual output/massing.png

python -m massing_explorer search --study underwood_checkpoint ^
  -c config/project.example.yaml --max-total-length 300 --max-length 200 ^
  --max-width 100 --max-stories 5 --preference low_rise --apply 0

python -m massing_explorer config config/project.example.yaml
```

The Excel parser auto-detects header rows and column names from keywords. It
handles hierarchical MSBA-style schedules, flat CSV, and footer NFA/GFA rows.

---

## License

MIT
