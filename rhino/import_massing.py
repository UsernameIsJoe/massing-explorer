"""
Massing Explorer — import MassingResponse JSON and build geometry in Rhino.

Run in Rhino: RunPythonScript → select this file.

Creates extruded volumes on the 'massing' layer from LLM/ML output.
"""

from __future__ import print_function

import json

import rhinoscriptsyntax as rs
import scriptcontext as sc
import Rhino
from Rhino.Geometry import Point3d, Polyline, PolylineCurve, Extrusion, Brep


MASSING_LAYER = "massing"
COLORS = {
    "residential": (100, 149, 237),
    "office": (70, 130, 180),
    "retail": (255, 140, 0),
    "mixed": (186, 85, 211),
    "parking": (105, 105, 105),
    "other": (0, 100, 255),
}


def _ensure_layer(name, color=None):
    index = sc.doc.Layers.Find(name, True)
    if index < 0:
        layer = Rhino.DocObjects.Layer()
        layer.Name = name
        if color:
            layer.Color = Rhino.Display.Color4f(color[0], color[1], color[2])
        sc.doc.Layers.Add(layer)
    return name


def _vertices_to_polyline(vertices):
    pts = []
    for v in vertices:
        if len(v) == 2:
            pts.append(Point3d(v[0], v[1], 0))
        else:
            pts.append(Point3d(v[0], v[1], v[2]))

    if len(pts) > 1 and pts[0].DistanceTo(pts[-1]) < 0.001:
        pts = pts[:-1]

    if len(pts) < 3:
        return None

    pl = Polyline(pts)
    pl.Add(pts[0])
    return PolylineCurve(pl)


def _extrude_volume(volume):
    footprint = volume.get("footprint", [])
    height = volume.get("height", 0)
    base_z = volume.get("base_elevation", 0)

    if height <= 0 or len(footprint) < 3:
        return None

    curve = _vertices_to_polyline(footprint)
    if curve is None:
        return None

    if abs(base_z) > 0.001:
        xform = Rhino.Geometry.Transform.Translation(0, 0, base_z)
        curve.Transform(xform)

    extrusion = Extrusion.Create(curve, height, True)
    if extrusion is None:
        brep = Brep.CreateFromOffsetFace(
            Brep.CreatePlanarBreps(curve)[0].Faces[0], 0, True, True
        )
        if brep:
            vec = Rhino.Geometry.Vector3d(0, 0, height)
            brep = brep.Faces[0].CreateExtrusion(vec, True)
        return brep

    return extrusion


def _color_for_use(use):
    return COLORS.get(use, COLORS["other"])


def import_massing(data, proposal_index=0):
    proposals = data.get("proposals", [])
    if not proposals:
        print("No proposals in JSON.")
        return []

    if proposal_index >= len(proposals):
        proposal_index = 0

    proposal = proposals[proposal_index]
    print("Importing proposal: {} ({})".format(
        proposal.get("name", "?"), proposal.get("id", "?")
    ))

    created_ids = []
    for vol in proposal.get("volumes", []):
        geom = _extrude_volume(vol)
        if geom is None:
            print("  Skipped invalid volume: {}".format(vol.get("name", "?")))
            continue

        use = vol.get("use", "other")
        color = _color_for_use(use)
        layer = _ensure_layer(MASSING_LAYER, color)

        attrs = Rhino.DocObjects.ObjectAttributes()
        attrs.LayerIndex = sc.doc.Layers.Find(layer, True)
        attrs.Name = vol.get("name", "massing_volume")
        attrs.SetUserString("use", use)
        if "floors" in vol:
            attrs.SetUserString("floors", str(vol["floors"]))

        obj_id = sc.doc.Objects.Add(geom, attrs)
        if obj_id != Rhino.DocObjects.ObjRef.Empty:
            created_ids.append(obj_id)
            print("  Created: {} (h={})".format(
                vol.get("name", "?"), vol.get("height", 0)
            ))

    sc.doc.Views.Redraw()
    return created_ids


def main():
    filter_str = "JSON file (*.json)|*.json|All Files (*.*)|*.*||"
    path = rs.OpenFileName("Import Massing Response", filter_str)
    if not path:
        print("Import cancelled.")
        return

    with open(path, "r") as f:
        data = json.load(f)

    if data.get("rationale"):
        print("Rationale: {}".format(data["rationale"]))

    proposals = data.get("proposals", [])
    if len(proposals) > 1:
        names = [p.get("name", p.get("id", str(i))) for i, p in enumerate(proposals)]
        choice = rs.GetString(
            "Select proposal",
            names[0],
            names,
        )
        index = names.index(choice) if choice in names else 0
    else:
        index = 0

    ids = import_massing(data, index)
    print("Done. Created {} objects on layer '{}'.".format(len(ids), MASSING_LAYER))


if __name__ == "__main__":
    main()
