# Progress log

Running record of work, test results, problems, and design discussions.  
Update this file at the end of every work session.

---

## 2026-09-02 — Project reset & planning locked

### Done

- Removed outdated Rhino site-context scripts (untested, out of scope)
- Locked design decisions from user Q&A → [DECISIONS.md](DECISIONS.md)
- Wrote phased plan → [PHASES.md](PHASES.md)
- Rewrote [README.md](../README.md) for standalone program-massing direction
- Added example program CSV and project config template
- Added JSON schemas for `ProgramStudy` and `MassingStudy`

### Decisions recorded

- Excel/CSV input; no screenshots
- Feet / SF; grossing factors per project
- Ollama local only; LLM reasons, engine calculates
- Text output v1; Rhino later
- Single option per study; state remembered across chat
- ±3% GSF tolerance (adjustable)
- Anchor room fit only; list compromises
- Site constraints via chat + config (no Rhino site model yet)

### Current phase

**Phase 1 — Program engine** — not started

### Next steps

1. Scaffold `src/massing_explorer/`
2. Implement CSV/Excel parser
3. Implement GSF calculator + verification
4. CLI `ingest` command
5. Run Phase 1 test criteria

### Open problems / discussions

_None yet — awaiting Phase 1 implementation._

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
