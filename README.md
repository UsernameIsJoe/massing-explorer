# Massing Explorer

A **program-aware massing thinker** that turns architectural program spreadsheets into dimensionally tested conceptual massing — using a local LLM for design reasoning and a deterministic Python engine for all math and validation.

**Standalone project.** Algorithm-first. No Rhino dependency in early phases.

---

## What it does

1. **Ingests** a program from Excel/CSV
2. **Reasons** via local LLM (Ollama) about grouping, stacking, and constraints from chat
3. **Calculates** footprints, story counts, GSF — deterministically, never via LLM math
4. **Validates** anchor-room fit, double-height voids, GSF tolerance
5. **Outputs** a text massing study (dimensions, programs per mass, programs per floor)
6. **Remembers** study state across the conversation

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  USER                                                        │
│  • Excel / CSV program file                                  │
│  • Chat: "group art+music", "max 80 ft wide", "3 stories"  │
│  • Config file: anchor rooms, grossing factors, site limits  │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────┐
│  LOCAL LLM (Ollama)       │
│  • Interpret program       │
│  • Suggest groupings       │
│  • Apply conversational    │
│    constraints             │
│  • Explain tradeoffs       │
└──────────────┬───────────┘
               │ decisions (groupings, stories, constraints)
               ▼
┌─────────────────────────────────────────────────────────────┐
│  DETERMINISTIC MASSING ENGINE (Python)                       │
│  • Parse & verify program totals                             │
│  • NFA → GSF (configurable factors)                          │
│  • Footprint dimension solver                                │
│  • Paired-mass solver                                        │
│  • Double-height void deduction                              │
│  • Stepped floor plates                                      │
│  • Anchor room fit check                                     │
│  • Actual vs target GSF (±3% default, adjustable)            │
└──────────────┬──────────────────────────────────────────────┘
               │ MassingStudy (text / JSON)
               ▼
┌─────────────────────────────────────────────────────────────┐
│  OUTPUT                                                      │
│  • Dimensions per mass and per floor                         │
│  • Program assignment per mass / floor                       │
│  • Validation report (GSF fit, compromised anchor rooms)     │
│  • (Later) Rhino massing geometry                            │
└─────────────────────────────────────────────────────────────┘
```

**Rule:** the LLM proposes; the engine calculates and validates. Never trust the LLM for arithmetic.

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
| Options | **One massing at a time** for now; multi-option later |
| State | **Remember** across chat turns |
| Adjacency | **Conversational** |
| Output (v1) | **Text report** — dimensions, program-in-mass, program-on-floor |
| GSF tolerance | **±3%** default, user-adjustable |
| Room fit | **Anchor rooms** only; list what is compromised |
| Double-height | Identified in **chat** |
| Site context | **Not in scope yet** — user states length/width/stacking in chat or config |
| Repo | **Standalone** (not tied to prior Rhino site-export work) |
| Planning limits | **Chat** + **config file** (e.g. 80 ft academic width) |
| LLM | **Ollama**, local only, no paid APIs |
| Vision/OCR | **Out of scope** — not a priority |

Full rationale: [docs/DECISIONS.md](docs/DECISIONS.md)

---

## Phased plan

| Phase | Focus | Status |
|-------|-------|--------|
| **1** | Excel ingest, GSF engine, config file, CLI, text output | Not started |
| **2** | Ollama chat loop, grouping, story count, state memory | Not started |
| **3** | Footprint solver, anchor room fit, double-height, stepped floors | Not started |
| **4** | Paired masses, site limits from config/chat | Not started |
| **5** | Rhino massing geometry export | Not started |

Details and test criteria: [docs/PHASES.md](docs/PHASES.md)

Live progress log: [docs/PROGRESS.md](docs/PROGRESS.md)

---

## Project structure

```
massing-explorer/
├── docs/
│   ├── DECISIONS.md      # Locked design decisions
│   ├── PHASES.md         # Phase plan + test criteria
│   └── PROGRESS.md       # Running log of work, problems, discussions
├── config/
│   └── project.example.yaml
├── schemas/
│   ├── program-study.schema.json
│   └── massing-study.schema.json
├── examples/
│   └── program.example.csv
└── README.md
```

Code will land under `src/` as each phase is implemented.

---

## Manual workflow reference

The 15-step program-to-massing study workflow this project automates is documented in full in [docs/DECISIONS.md](docs/DECISIONS.md#manual-workflow-reference).

---

## Getting started

_Not yet — Phase 1 in progress. See [docs/PROGRESS.md](docs/PROGRESS.md)._

---

## License

MIT
