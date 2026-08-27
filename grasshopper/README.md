# Grasshopper integration

Massing Explorer is designed to work with or without Grasshopper. The Rhino Python scripts are sufficient for the full export → LLM → import loop. Grasshopper adds parametric control and live iteration.

## Recommended workflow

```
Site model (Rhino) → export_site_context.py → site-context.json
                                              ↓
                                         LLM / API
                                              ↓
                                    massing-response.json
                                              ↓
                         import_massing.py OR Grasshopper rebuild
```

## Grasshopper components (manual setup)

### 1. JSON Export (optional GH wrapper)

- **Read File** → path to `site-context.json`
- **JSON Tree** or **Python Script** component to parse and display key values
- Use for dashboards: site area, context building count, zoning limits

### 2. Massing Rebuild from JSON

Add a **Python 3** script component with inputs:
- `json_path` (str) — path to `massing-response.json`
- `proposal_index` (int) — which option to build

Paste the core logic from `rhino/import_massing.py` (`_extrude_volume`, `import_massing`).

Outputs:
- `breps` — generated volumes
- `names` — volume labels

Bake to the `massing` layer or preview in GH.

### 3. Parametric massing (future)

For LLM-generated **parameters** instead of fixed footprints:
- Footprint from slider-driven rectangle / polygon
- Height from number slider
- Setbacks as offset curves from site boundary

The LLM would return parameters; Grasshopper rebuilds geometry parametrically.

## Layer convention

Run `rhino/setup_layers.py` once before modeling. All GH-baked geometry should respect the same layers:

| Layer | Purpose |
|-------|---------|
| `site` | Parcel boundary |
| `context_buildings` | Neighbors / existing |
| `setbacks` | Buildable envelope |
| `terrain` | Ground mesh / points |
| `roads` | Street centerlines |
| `massing` | Generated proposals |

## User strings for metadata

Attach to objects or document:

| Key | Where | Example |
|-----|-------|---------|
| `width` | road object | `12` (meters) |
| `floor_height` | context building | `3.5` |
| `zoning_max_height` | document strings | `60` |
| `zoning_far` | document strings | `4.0` |
| `zoning_max_coverage` | document strings | `0.65` |

Set document strings via Rhino: `DocumentProperties` or programmatically in the exporter.
