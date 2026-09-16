# Studio UI

How to run and use the local Massing Explorer UI. Search behavior is defined in
[BLUEPRINT-search.md](BLUEPRINT-search.md) and [WORKFLOW.md](WORKFLOW.md).

---

## Launch

```bash
# From the repo root
run_ui.bat
# or
python -m massing_explorer.ui_app
```

Opens `http://127.0.0.1:8765/`. Uses `src/` on `PYTHONPATH` (editable install).

**Restart after pulling code.** Python loads modules once at process start. Old
`ui_app` processes on port 8765 will keep serving yesterday’s search. Kill every
Massing UI python, then start **one** fresh server.

---

## Typical session

1. **Drop** a program Excel/CSV (or pick a file).
2. **Type** a brief (mass count, caps, pins, prefer floors, keep-together, …).
3. **Generate** — full explore runs COVER → REPAIR → planner / MCTS / BO / REFINE,
   then prepares LEARN if two legal schemes exist.
4. Browse **Sample pool** and **Candidates** (legal cells; elites first).
5. Answer **A/B** by **clicking the scheme card** you prefer (max five questions;
   Skip is allowed). Soft taste only — requirements and caps stay locked.
6. Keep working from the study on disk under `studies/ui_<id>/`.

Card thumbnails are fast envelope previews. The main 3D view swaps to the full
**department-colored** mesh after the study apply finishes.

---

## What you should see

| Panel | Meaning |
|-------|---------|
| Process → clauses | Requirements / limitations / preferences from the brief |
| Sample pool | Unique drawings from the archive (near-twin plates may be hidden) |
| Candidates | Elites first, then other legal cells (up to a dozen) |
| A/B modal | Click A or B’s image; not Prefer buttons |
| Main viewport | Kept / selected scheme with program distribution |

Legal count in the process note can be higher than pool cards when several
legals share nearly the same silhouette.

---

## Tips

- Use the **tweaked** GSF example for tight three-mass regression:
  `examples/Underwood_Elementary_Space_Summary_GSF_Tweaked.xlsx` with
  `examples/underwood_3mass_brief.txt`.
- CLI chat (`python -m massing_explorer chat …`) still works; A/B there is typed
  `A` / `B` instead of a click.
- Do not leave multiple UI servers running on the same port.
