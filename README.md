# Massing Explorer

Turn Rhino site models into structured JSON that LLMs and ML models can understand — then rebuild proposed massing back into editable 3D geometry.

## The problem

Rhino shows you a viewport, but there is no native "text view" of a model. LLMs cannot see your screen. **Massing Explorer** bridges that gap with a small, opinionated workflow:

1. Model site conditions in Rhino using a standard layer convention
2. Export a compact **SiteContext** JSON brief
3. Send it to an LLM (or any ML model) with a defined prompt
4. Receive a **MassingResponse** JSON with proposed building volumes
5. Import the response back into Rhino as extruded massing

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Rhino Site     │────▶│  SiteContext     │────▶│  LLM / ML       │
│  Model          │     │  JSON            │     │  Model          │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                        │
┌─────────────────┐     ┌──────────────────┐            │
│  Rhino Massing  │◀────│  MassingResponse │◀───────────┘
│  Geometry       │     │  JSON            │
└─────────────────┘     └──────────────────┘
```

## Quick start

### 1. Set up layers

In Rhino, run:

```
RunPythonScript → rhino/setup_layers.py
```

This creates: `site`, `context_buildings`, `setbacks`, `terrain`, `roads`, `massing`.

### 2. Model your site

| Layer | What to draw |
|-------|-------------|
| `site` | Closed curve — parcel boundary |
| `context_buildings` | Closed curves or breps — neighbor buildings |
| `setbacks` | Closed curve — buildable envelope |
| `roads` | Open curves — street centerlines (add user string `width`) |
| `terrain` | Mesh or points — optional ground |

**Zoning metadata** — set document user strings:
- `zoning_max_height` → e.g. `60`
- `zoning_far` → e.g. `4.0`
- `zoning_max_coverage` → e.g. `0.65`

### 3. Export site context

```
RunPythonScript → rhino/export_site_context.py
```

Saves `site-context.json` with footprints, heights, setbacks, roads, and zoning.

### 4. Generate massing with an LLM

Copy `prompts/system-prompt.md` as your system prompt. Paste the exported JSON as the user message. Ask for a massing proposal.

See `examples/` for sample input and output files.

### 5. Import massing back to Rhino

```
RunPythonScript → rhino/import_massing.py
```

Select your `massing-response.json`. Volumes appear on the `massing` layer.

## Project structure

```
massing-explorer/
├── rhino/
│   ├── setup_layers.py        # Create standard layers
│   ├── export_site_context.py # Rhino → SiteContext JSON
│   └── import_massing.py      # MassingResponse JSON → geometry
├── schemas/
│   ├── site-context.schema.json
│   └── massing-response.schema.json
├── examples/
│   ├── site-context.example.json
│   └── massing-response.example.json
├── prompts/
│   └── system-prompt.md       # LLM system prompt
├── grasshopper/
│   └── README.md              # GH integration notes
└── tools/
    └── validate_json.py         # Validate JSON without Rhino
```

## Validate JSON (no Rhino needed)

```bash
pip install -r requirements.txt
python tools/validate_json.py examples/site-context.example.json
python tools/validate_json.py examples/massing-response.example.json --type massing
```

## Design principles

- **Semantic over raw** — export footprints, heights, and zoning; not NURBS control points
- **Schema-first** — both sides of the LLM loop have JSON schemas
- **Editable output** — imported massing is native Rhino extrusions, not meshes
- **Layer convention** — predictable structure so exporters stay simple

## Roadmap

- [ ] Grasshopper `.gh` definition for parametric rebuild
- [ ] MCP server for live Rhino ↔ LLM loop
- [ ] Shadow / solar context in SiteContext
- [ ] Multi-proposal comparison dashboard
- [ ] Rhino.Compute headless endpoint

## Requirements

- **Rhino 7 or 8** (for export/import scripts)
- **Python 3** (for validation tool; optional)
- Any LLM API for the massing step

## License

MIT
