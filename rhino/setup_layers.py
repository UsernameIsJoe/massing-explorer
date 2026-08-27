"""
Massing Explorer — create standard layer convention in Rhino.

Run in Rhino: RunPythonScript → select this file.

Layers:
  site              — parcel boundary (closed curve or surface)
  context_buildings — existing neighboring buildings
  setbacks          — required setback / buildable envelope
  terrain           — terrain mesh or sample points
  roads             — street centerlines
  massing           — generated proposal geometry (output)
"""

import rhinoscriptsyntax as rs
import scriptcontext as sc
import Rhino


LAYER_SPEC = [
    ("site", (0, 128, 0)),              # green
    ("context_buildings", (128, 128, 128)),  # gray
    ("setbacks", (255, 165, 0)),        # orange
    ("terrain", (139, 90, 43)),         # brown
    ("roads", (64, 64, 64)),            # dark gray
    ("massing", (0, 100, 255)),         # blue
]


def setup_layers():
    doc = sc.doc
    for name, color in LAYER_SPEC:
        index = doc.Layers.Find(name, True)
        if index < 0:
            layer = Rhino.DocObjects.Layer()
            layer.Name = name
            layer.Color = Rhino.Display.Color4f(color[0], color[1], color[2])
            doc.Layers.Add(layer)
            print("Created layer: {}".format(name))
        else:
            print("Layer exists: {}".format(name))

    doc.Views.Redraw()
    print("Done. Assign geometry to layers before exporting site context.")


if __name__ == "__main__":
    setup_layers()
