# Massing Explorer

Turn Rhino site models and architectural program schedules into dimensionally tested conceptual massing — with a local LLM as the design reasoner and a deterministic engine as the calculator.

---

## What you're actually asking for

You don't want a parametric slider tool that extrudes boxes from area ÷ floors. You want a **program-aware massing reasoner** that:

1. **Ingests** a program (spreadsheet and/or screenshot of a schedule)
2. **Reasons** about grouping, stacking, proportions, and tradeoffs
3. **Reacts** to conversational constraints ("keep HPE with dining", "max length 280 ft", "prefer 3 stories")
4. **Validates** continuously — GSF math, room fit, double-height voids, site limits
5. **Iterates** like a designer would, not one-shot generation

The school example is one instance of a **general workflow** that should work for offices, labs, hospitals, mixed-use, etc.

---

## How this differs from the original Massing Explorer scope

| Original repo scope | What we need now |
|---|---|
| Site boundary + context buildings | Program schedule + room dimensions |
| LLM outputs footprint polygons | LLM reasons; **solver** outputs dimensions |
| Single GSF/FAR zoning check | Per-department target GSF + running validation |
| One massing response | Multi-mass, stepped floors, voids, alternatives |
| Cloud LLM assumed | **Local LLM** (privacy, no API cost) |
| Text JSON in/out | **Screenshots** of program tables + chat |

The existing repo components are still useful for **site context** (Steps 12–13 in the manual workflow). The new piece is the **program → dimension study** engine.

---

## Core architectural insight: don't let the LLM do the math

Your 15-step workflow is heavy on **deterministic calculation** and **spatial feasibility**. LLMs are weak at both. The right split is:

```
┌─────────────────────────────────────────────────────────────┐
│  USER                                                        │
│  • Program spreadsheet / screenshot                        │
│  • Chat: "group art+music", "max 80 ft wide", "3 stories"  │
└──────────────┬──────────────────────────────┬──────────────┘
               │                              │
               ▼                              ▼
┌──────────────────────────┐    ┌────────────────────────────┐
│  LOCAL LLM (reasoner)     │    │  VISION MODEL (if screenshot)│
│  • Interpret program       │    │  • Extract table → structured │
│  • Suggest groupings       │    │    program JSON               │
│  • Propose alternatives    │    └──────────────┬─────────────┘
│  • Explain tradeoffs       │                   │
│  • Respond to prompts      │◀──────────────────┘
└──────────────┬───────────┘
               │ decisions (groupings, story counts, constraints)
               ▼
┌─────────────────────────────────────────────────────────────┐
│  DETERMINISTIC MASSING ENGINE (Python)                       │
│  • NFA → GSF calculation with grossing factors               │
│  • Footprint dimension solver (fixed side → other side)    │
│  • Paired-mass solver (shared width, combined length)        │
│  • Double-height void deduction                              │
│  • Stepped floor plate assignment                            │
│  • Room fit check (min width × length inside rectangle)      │
│  • Actual vs target GSF validation                           │
│  • Site constraint clipping                                  │
└──────────────┬──────────────────────────────────────────────┘
               │ validated MassingStudy JSON
               ▼
┌─────────────────────────────────────────────────────────────┐
│  RHINO (visualization)                                       │
│  • Import masses per department / floor                      │
│  • Site context from existing Massing Explorer export        │
└─────────────────────────────────────────────────────────────┘
```

The LLM is the **design partner** ("try splitting academic into two wings"). The engine is the **calculator and checker** ("that rectangle is 72 ft wide but the gym needs 100 ft clear").

This mirrors how architects actually work: judgment + spreadsheet.

---

## Manual workflow reference (15 steps)

This project automates the following manual process:

### Purpose of this task

Translate an architectural program spreadsheet into conceptual building masses.

- Convert room/program requirements into gross floor area targets.
- Test different footprint dimensions, story counts, and massing splits.
- Check whether proposed masses are both:
  - mathematically large enough, and
  - spatially plausible for the rooms they contain.
- Adjust masses in response to site limits, adjacency preferences, and dimensional constraints.
- Maintain a running GSF validation so massing changes do not accidentally reduce or inflate the required building area.

### Typical inputs

- Program spreadsheet or schedule of accommodation.
- Net program areas by room and by department.
- Grossing factors.
- Additional area multipliers.
- Room dimension requirements.
- Double-height requirements.
- Preferred program adjacencies.
- Site-driven maximum lengths, widths, or combined dimensions.
- Preferred number of stories.
- Existing conceptual massing dimensions.

### Step 1 — Read and verify the program

Read the latest program schedule directly.

Record:

- room names,
- quantities,
- room areas,
- departmental totals,
- overall total.

Check that individual rooms add correctly to their department totals.

Do not reuse values from older schedules after the program has changed.

Treat the latest provided schedule as the source of truth.

### Step 2 — Establish the area calculation

Convert the listed program area into the required gross building area.

A typical calculation used in this study was:

$$\text{Target GSF} = \text{Program Area} \times \text{Area Adjustment} \times \text{Grossing Factor}$$

Example:

- Program adjustment = 1.15
- Grossing factor = 1.50
- Combined multiplier = 1.725

Convert square feet to square meters only after calculating the required area when practical.

### Step 3 — Separate room requirements from grossing

A grossing factor increases the overall area allowance, but does not automatically change a room's required clear dimensions.

Example:

- A gym may require a clear 60 ft × 100 ft playing space.
- That clear room remains approximately 18.29 × 30.48 m.

Additional gross area accounts for:

- circulation,
- walls,
- structure,
- support rooms,
- building services,
- shared space.

Always check both:

- clear room requirement, and
- gross program allowance.

### Step 4 — Create program groupings

Group departments into conceptual masses based on:

- operational relationships,
- circulation,
- shared use,
- site placement,
- building height.

Keep strongly related programs together when possible.

Avoid splitting one department across several masses without a clear reason.

Program grouping can change during the study as site constraints become clearer.

### Step 5 — Determine story count

Test whether each program group works better as:

- one story,
- two stories,
- three stories,
- four stories,
- multiple separate masses.

Basic calculation:

$$\text{Average Floor Plate} = \frac{\text{Target GSF}}{\text{Number of Floors}}$$

Use this only as a starting point.

Actual floors may be unequal or stepped.

### Step 6 — Generate footprint dimensions

Once the floor plate area is known, solve dimensions from site or planning constraints.

If one side is fixed:

$$\text{Other Side} = \frac{\text{Required Floor Area}}{\text{Fixed Side}}$$

Typical fixed constraints include:

- an 80 ft academic planning width,
- a maximum building length,
- alignment with another mass,
- a site setback,
- a courtyard width.

Generate several proportions where useful:

- long and narrow,
- balanced rectangle,
- compact rectangle.

### Step 7 — Test real room fit

Do not stop after matching total area.

Check whether major rooms actually fit inside the proposed rectangle.

Test:

- minimum room width,
- minimum room length,
- sensible aspect ratio,
- support room placement,
- circulation.

A mass can meet GSF mathematically and still be unusable.

### Step 8 — Handle double-height spaces

Identify spaces that occupy one floor area but extend through two levels.

Common examples:

- gym,
- cafeteria,
- auditorium,
- stage.

Count the room floor area once in actual GSF.

Treat the level above as a void where applicable.

Upper floors can occupy only the portions of the footprint that are not double-height.

### Step 9 — Allow stepped floors

Upper floors do not need to match the ground-floor footprint.

Size each floor according to the program assigned to it.

This often creates a more efficient mass:

- large ground floor,
- smaller second floor,
- smaller third floor.

Stepped massing is especially useful when:

- only one department occupies an upper floor,
- large double-height rooms dominate the ground floor,
- site constraints affect only part of the mass.

### Step 10 — Test program distribution by floor

Assign programs to floors and check each floor independently.

Prefer keeping a complete department on one floor.

If a department must span floors:

- divide it logically,
- avoid tiny fragments,
- use sensible proportions.

Group rooms with their functional support spaces.

### Step 11 — Solve paired or linked masses

Sometimes two masses must:

- align,
- share a width,
- fit within a combined length.

If two adjacent programs share a width and have a fixed combined length:

$$W = \frac{A_1 + A_2}{L_{\text{total}}}$$

then:

$$L_1=\frac{A_1}{W}$$

$$L_2=\frac{A_2}{W}$$

This is useful for coordinated massing along a site edge.

### Step 12 — Add site constraints

Site rules are applied in addition to program area requirements.

Examples:

- maximum long side,
- maximum short side,
- combined length of two masses,
- setback line,
- angled property boundary,
- courtyard dimension.

When a mass is resized for the site, compensate by changing:

- the other dimension,
- the floor count,
- the floor plate above,
- or the program distribution.

### Step 13 — Compare alternatives

Produce multiple massing options when the site allows different strategies.

Typical comparisons:

- 3 stories vs. 4 stories,
- one mass vs. two masses,
- longer/narrower vs. shorter/wider,
- equal floors vs. stepped floors.

Evaluate each option against:

- GSF,
- program fit,
- room dimensions,
- site constraints,
- simplicity of stacking.

### Step 14 — Recalculate whenever dimensions change

Every dimensional adjustment should trigger a new area check.

Do not assume a modified rectangle still meets the target.

Recalculate actual area directly from the current dimensions.

### Step 15 — Final GSF verification

For each mass, compare:

$$\text{Actual GSF} \quad\text{vs.}\quad \text{Target GSF}$$

Report the difference:

$$\text{Fit} = \text{Actual GSF} - \text{Target GSF}$$

Small differences caused by rounding are acceptable.

Large differences indicate that:

- a floor is missing,
- a void was counted incorrectly,
- a dimension was rounded too aggressively,
- or a program area was omitted.

### Example — School Program Massing Study

A school program schedule was divided into several major program groups.

Each listed area was multiplied by:

1. 1.15 program adjustment, then
2. 1.50 grossing factor.

Program groups included:

- academic classrooms and special education,
- health/physical education,
- dining and food service,
- art and music,
- administration and medical,
- media center,
- custodial and maintenance.

The study tested:

- one-, two-, three-, and four-story masses,
- fixed academic widths,
- maximum building lengths,
- multiple academic wings,
- stepped upper floors,
- shared-width support masses.

Major room constraints were checked separately.

For example, the gym's clear dimensional requirement was preserved independently from its grossed program allowance.

Double-height spaces were identified and treated as voids on upper floors.

Several program arrangements were tested to determine whether:

- departments could remain together,
- classrooms could be distributed reasonably,
- site dimensions could be respected.

Masses were repeatedly resized as site constraints changed.

When one dimension became fixed, the other was recalculated.

When the building became too long, additional stories or a second mass were tested.

When two adjacent masses needed to align, their dimensions were solved together.

Final massing dimensions were checked by comparing:

- calculated model floor area,
- required GSF from the program,
- and any double-height deductions.

### Task types included in this workflow

- Program schedule interpretation
- Area verification
- NFA-to-GSF conversion
- Metric / imperial conversion
- Program grouping
- Adjacency-based massing
- Story-count studies
- Footprint dimension studies
- Classroom / major-room fit checks
- Double-height volume studies
- Stepped floor-plate studies
- Program stacking
- Multi-mass distribution
- Site-constraint testing
- Shared-dimension optimization
- Alternative massing generation
- Iterative resizing
- Actual-vs-target GSF verification

**In short:** this workflow turns a program spreadsheet plus site constraints into dimensionally tested conceptual massing options, while continuously checking that the geometry remains consistent with the required building area.

---

## Mapping your 15 steps to system components

| Your step | Who handles it | Notes |
|---|---|---|
| 1. Read & verify program | Vision/OCR **or** Excel parser → **engine verifies totals** | Never trust LLM arithmetic for sums |
| 2. Area calculation (×1.15 ×1.50) | **Engine** | Formula is fixed; factors may vary per project |
| 3. Separate room dims from grossing | **Schema** + **engine** | Room has `clear_width`, `clear_length` separate from `program_area` |
| 4. Program groupings | **LLM proposes**, user confirms | This is real design reasoning |
| 5. Story count | **LLM proposes**, **engine validates** | Engine computes floor plate = GSF / floors |
| 6. Footprint dimensions | **Engine solves**, LLM picks proportions | `other = area / fixed_side` |
| 7. Room fit check | **Engine** | Rectangle packing / constraint check |
| 8. Double-height voids | **Engine** | Deduct upper-floor area where void exists |
| 9. Stepped floors | **Engine** + LLM assignment | Per-floor area independent |
| 10. Program distribution by floor | **LLM proposes**, engine validates each floor |
| 11. Paired/linked masses | **Engine** | Your W = (A1+A2)/L formula |
| 12. Site constraints | **Engine** (from Massing Explorer site JSON) |
| 13. Compare alternatives | **LLM generates options**, engine scores each |
| 14–15. Recalculate & verify GSF | **Engine**, always on every change |

---

## What needs a new data model (beyond current schemas)

A **ProgramStudy** object, roughly:

```json
{
  "rooms": [{ "name", "qty", "area_each", "department", "clear_dims?", "double_height?" }],
  "grossing": { "program_adjustment": 1.15, "grossing_factor": 1.50 },
  "departments": [{ "name", "nfa_total", "target_gsf", "rooms": [...] }],
  "adjacencies": [{ "a", "b", "strength": "required|preferred|avoid" }],
  "room_constraints": [{ "room", "min_width", "min_length" }]
}
```

And a **MassingStudy** object:

```json
{
  "masses": [{
    "id", "departments": [...],
    "floors": [
      { "level": 0, "footprint": [w, l], "area", "programs": [...], "voids": [...] },
      { "level": 1, "footprint": [w, l], "area": ... }
    ],
    "target_gsf", "actual_gsf", "fit_delta"
  }],
  "alternatives": [...],
  "validation": [{ "check", "pass", "message" }]
}
```

The LLM never outputs raw dimensions without the engine re-validating them.

---

## Hard problems (ranked)

### Tractable now (deterministic engine)

- GSF calculation with configurable multipliers
- Footprint solving from fixed dimension
- Paired-mass shared-width solver
- Double-height void area deduction
- Stepped floor plates with independent areas
- Actual vs target GSF diff report
- Site max length/width clipping

### Medium difficulty

- Room fit inside rectangle (major rooms like gym, auditorium — not full bin packing)
- Program screenshot → structured table (local vision models work for clean tables; messy Excel exports are harder)
- Maintaining study state across chat turns ("make the academic wing shorter" → which mass? recalc all)

### Hard / likely needs human-in-the-loop

- Optimal department grouping from adjacency alone
- Full classroom distribution across floors (combinatorial)
- Angled site boundaries affecting mass orientation
- Knowing when "close enough" GSF fit is acceptable vs needs another floor

### Don't automate (keep as user judgment)

- Whether to split a department across masses for political/operational reasons
- Aesthetic proportion preferences beyond simple aspect ratio bounds
- Final selection among alternatives

---

## Local LLM stack (research summary)

For your constraints (local, screenshots, reasoning):

| Role | Candidate | Tradeoff |
|---|---|---|
| Reasoning / chat | `qwen2.5:14b`, `llama3.1:8b`, `mistral-nemo` via Ollama | 14B+ better for multi-step reasoning; needs 16GB+ VRAM |
| Vision / table OCR | `qwen2.5vl:7b`, `olmocr2:7b`, `granite3.2-vision` | Good for clean screenshots; always verify totals in engine |
| Structured output | Tool-calling pattern (LLM calls engine functions) | More reliable than free-form JSON |

**Recommendation:** Excel/CSV direct parse when available (100% accurate), vision as fallback for screenshots. Never rely on vision alone for area totals.

---

## Interaction model brainstorm

Three modes that could coexist:

1. **Study mode** — "Here's my program [file/screenshot]. Propose groupings." → LLM returns grouping proposal → user adjusts in chat → engine calculates.

2. **Constraint mode** — "Academic max 80 ft wide, 3 stories, keep HPE with dining." → engine generates 2–3 dimensioned options → LLM explains tradeoffs.

3. **Resize mode** — "Make the support building shorter." → engine recalculates paired dimensions, re-validates GSF, updates Rhino.

Each response should include a **validation panel**: target GSF, actual GSF, fit delta, room fit pass/fail, site constraint pass/fail.

---

## Questions for you

Grouped by what blocks design decisions most.

### A. Inputs & program data

1. **What format will programs usually arrive in?** Native Excel `.xlsx`, CSV export, PDF, or screenshot only? Is a direct Excel parser a must-have for v1, or is screenshot-first acceptable?

2. **Is there a standard column structure** across your projects (Room Name | Qty | Area | Department), or does every client spreadsheet look different?

3. **Where do room dimension requirements live?** In the same spreadsheet, a separate "room standards" sheet, or only in the designer's head / chat? (Your gym 60×100 ft example — is that ever tabulated?)

4. **Are grossing factors always the same** (1.15 × 1.50), or do they vary by building type / client? Should the user be able to say "use 1.40 grossing for this study" in chat?

5. **Units:** Do you work primarily in feet, meters, or mixed? Should the system hold one canonical unit internally?

### B. Reasoning & interaction

6. **How do you want to talk to it?** Chat panel inside Rhino? Standalone desktop app? CLI? Something else?

7. **When you say "react to prompts,"** is the typical loop: propose → you critique → revise → repeat? Or batch: "give me 4 options" and pick one?

8. **Should the system remember the current study state** across messages (active masses, last dimensions, which alternative is selected), or start fresh each time?

9. **Adjacency preferences** — do you ever have formal adjacency matrices/diagrams, or is it always conversational ("keep art near music")?

### C. Output & validation

10. **What does "done" look like for one study?** A set of labeled mass rectangles with dimensions per floor? A report table? Geometry in Rhino? All three?

11. **How strict is GSF fit?** Is ±2% acceptable? Must it be exact? Should the system auto-add a partial floor if under?

12. **Room fit checks** — which rooms need dimensional validation? Only "anchor" rooms (gym, auditorium, cafeteria), or every classroom?

13. **Double-height** — is this flagged in the program data, or does the user identify them in chat ("gym is double height")?

### D. Site & Rhino

14. **Is site context always in Rhino** when you do these studies, or is program-to-massing often done before a site exists?

15. **Should this extend the existing Massing Explorer repo**, or become a separate project that imports site context from it?

16. **Fixed planning dimensions** (like the 80 ft academic width) — are these org-wide standards you'd want in a config file, or always stated per project in chat?

### E. Local LLM / hardware

17. **What machine will run this?** GPU model and VRAM? (Determines whether 7B vision + 14B reasoning is feasible simultaneously.)

18. **Privacy requirement** — must *everything* stay local, or is "program data local, optional cloud for hard reasoning" acceptable?

19. **Tolerance for OCR errors** — if a screenshot misreads a room area, is a "please verify these 3 numbers" confirmation step acceptable?

### F. Scope for v1

20. **What's the minimum useful v1?** My instinct: Excel ingest + GSF calc + single-mass dimension solver + chat grouping + GSF validation report. No Rhino yet. Does that match your priority, or is Rhino visualization required from day one?

---

## Current recommendation (pending your answers)

**Phase 1 — "Program Thinker" (no Rhino)**

- Parse program (Excel + vision fallback)
- Deterministic GSF engine with your formula
- Chat-driven grouping and story-count proposals
- Footprint dimension solver with fixed-side constraints
- Validation report (actual vs target, room fit for anchor rooms)

**Phase 2 — "Massing Study"**

- Multi-mass, paired dimensions, stepped floors, double-height voids
- Alternative comparison
- Export to Massing Explorer's Rhino import format

**Phase 3 — "Live in Rhino"**

- MCP or panel: chat + viewport + auto-update masses
- Site constraints from existing site-context export

---

The biggest design decision is **#20 (v1 scope)** and **#1 (input format)**. Those two answers will determine whether we build a spreadsheet parser first or a vision pipeline first, and whether Rhino is in the first release or the second.

---

## Existing repo components (implemented)

The following are already in this repository and remain part of the site-context side of the workflow:

| Component | Purpose |
|-----------|---------|
| `rhino/setup_layers.py` | Creates standard layers (`site`, `context_buildings`, `setbacks`, `terrain`, `roads`, `massing`) |
| `rhino/export_site_context.py` | Walks the Rhino doc → exports **SiteContext** JSON |
| `rhino/import_massing.py` | Reads **MassingResponse** JSON → builds extruded massing |
| `schemas/` | JSON schemas for site context and massing response |
| `examples/` | Sample site + massing JSON |
| `prompts/system-prompt.md` | LLM system prompt (site massing; to be extended for program studies) |
| `tools/validate_json.py` | Validate JSON without Rhino |

## License

MIT
