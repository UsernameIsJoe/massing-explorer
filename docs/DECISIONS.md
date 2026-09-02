# Design decisions

Decisions locked from the initial planning discussion (Sep 2026). Update this file when a decision changes.

---

## Product scope

**Massing Explorer** is a standalone **program-to-massing dimension study** tool. It is not a parametric slider toy and not a site-context exporter.

Primary goal: automate the manual workflow of translating a program spreadsheet into tested conceptual masses while continuously validating GSF.

Secondary goal (later): export geometry to Rhino.

---

## Input

### Program file

- **Format:** Excel (`.xlsx`) or CSV preferred over screenshots.
- **Reason:** Accuracy; OCR/vision is explicitly out of scope.
- **Assumed columns** (fixed for v1 to reduce complexity):

  | Column | Required | Notes |
  |--------|----------|-------|
  | `room_name` | yes | |
  | `qty` | yes | Default 1 if missing |
  | `area_sf` | yes | Net area per room (SF) |
  | `department` | yes | Used for grouping |

  Column naming alignment across clients is **not** a priority. We normalize on import.

### Room dimension constraints

- Provided via **user chat** during a study, and/or
- Stored in **project config** (`config/project.yaml`) as anchor room definitions.

Example anchor room entry:

```yaml
anchor_rooms:
  gym:
    min_width_ft: 60
    min_length_ft: 100
```

### Grossing factors

- **Not fixed** across projects.
- Set per study in config or chat:

  $$\text{Target GSF} = \text{Program Area} \times \text{Area Adjustment} \times \text{Grossing Factor}$$

### Site / planning limits

- **No Rhino site model** in early phases.
- User states constraints in chat or config file:
  - max length / width per mass
  - fixed planning widths (e.g. 80 ft academic)
  - how many masses fit in a given dimension
  - which programs stack in which mass

---

## Reasoning layer

### LLM

- **Ollama** (local). User may switch models later.
- **No paid/cloud APIs** — privacy and cost.
- **No vision/OCR** — not a project priority.

### LLM responsibilities

- Interpret program structure
- Propose department → mass groupings
- Propose story counts
- Apply conversational constraints ("keep HPE with dining")
- Explain tradeoffs in plain language

### LLM must NOT

- Perform arithmetic (GSF, areas, dimensions)
- Output final dimensions without engine validation

---

## Calculation engine

All math is deterministic Python.

### Units

- **Feet** internally for v1.
- SF for areas. Display conversions to metric optional later.

### GSF validation

- Default tolerance: **±3%** (`actual_gsf` vs `target_gsf`).
- User-adjustable per study.

### Room fit

- Check **anchor rooms** only (gym, auditorium, cafeteria, etc. — defined in config).
- When a mass cannot fit an anchor room, **list what is compromised** in the validation report.
- Do not attempt full classroom bin-packing in v1.

### Double-height

- Identified in **chat** (not assumed from spreadsheet).
- Engine deducts upper-floor area over void footprint.

### State

- **Remember** current study across chat turns: active masses, dimensions, assignments, last validation.

### Options

- **One massing proposal at a time** for v1.
- Multi-option comparison deferred to a later phase.

---

## Output (v1)

Text-based massing study:

- Mass ID and label
- Footprint dimensions (W × L) per floor
- Programs assigned to each mass
- Programs assigned to each floor / level
- Target GSF, actual GSF, fit delta
- Validation pass/fail per check
- List of compromised anchor rooms

Later: Rhino geometry export.

---

## UI

- **Not a priority.**
- CLI first. Chat via terminal + Ollama.
- Rich UI deferred.

---

## Manual workflow reference

The following 15-step process is the source workflow this tool automates.

### Purpose

Translate an architectural program spreadsheet into conceptual building masses:

- Convert room requirements → gross floor area targets
- Test footprint dimensions, story counts, massing splits
- Verify masses are mathematically large enough **and** spatially plausible
- Adjust for site limits, adjacency, dimensional constraints
- Maintain running GSF validation

### Steps (summary)

| Step | Task |
|------|------|
| 1 | Read & verify program (latest schedule is source of truth) |
| 2 | Calculate target GSF = Program Area × Adjustment × Grossing |
| 3 | Separate clear room dims from gross allowance |
| 4 | Group departments into conceptual masses |
| 5 | Determine story count; avg floor plate = GSF / floors |
| 6 | Solve footprint: other side = area / fixed side |
| 7 | Test anchor room fit inside rectangle |
| 8 | Handle double-height voids on upper floors |
| 9 | Allow stepped (unequal) floor plates |
| 10 | Assign programs to floors; keep departments together |
| 11 | Solve paired masses: W = (A₁+A₂)/L, L₁ = A₁/W, L₂ = A₂/W |
| 12 | Apply site constraints; compensate via other dim / floors / distribution |
| 13 | Compare alternatives (deferred — single option for v1) |
| 14 | Recalculate on every dimension change |
| 15 | Final GSF verification: Fit = Actual − Target |

### Component ownership

| Step | Handler |
|------|---------|
| 1 | Excel parser + engine verification |
| 2 | Engine |
| 3 | Schema + engine |
| 4 | LLM proposes, user confirms |
| 5 | LLM proposes, engine validates |
| 6 | Engine solves, LLM picks proportion preference |
| 7 | Engine (anchor rooms) |
| 8 | Engine |
| 9 | Engine + LLM assignment |
| 10 | LLM proposes, engine validates per floor |
| 11 | Engine |
| 12 | Engine (from config/chat limits) |
| 13 | Deferred |
| 14–15 | Engine (always) |

---

## Open questions (resolved)

| # | Question | Answer |
|---|----------|--------|
| 1 | Input format | Excel/CSV |
| 2 | Column structure | Assume standard; naming not priority |
| 3 | Room dimensions | Chat + config database |
| 4 | Fixed grossing factors? | No — per project |
| 5 | Units | Feet first |
| 6 | UI | Algorithm over UI; CLI |
| 7 | Multiple options | One at a time for now |
| 8 | Remember state | Yes |
| 9 | Adjacency | Conversational |
| 10 | Output | Text for now; Rhino later |
| 11 | GSF tolerance | ±3%, adjustable |
| 12 | Room fit scope | Anchor rooms; list compromises |
| 13 | Double-height | In chat |
| 14 | Site context | Chat/config only for now |
| 15 | Extend old repo? | No — standalone |
| 16 | Planning limits | Chat + config file |
| 17 | LLM runtime | Ollama |
| 18 | Local only? | Yes |
| 19 | Screenshots? | No |
| 20 | v1 scope | Phased; see PHASES.md |

---

## Deprecated / removed

The initial repo prototype (Rhino site-context export/import scripts) was **removed**. It was untested and out of scope for this standalone program-massing project.
