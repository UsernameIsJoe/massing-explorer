"""
Massing Explorer — export site conditions from Rhino to SiteContext JSON.

Run in Rhino: RunPythonScript → select this file.

Layer convention (see setup_layers.py):
  site, context_buildings, setbacks, terrain, roads

Objects on matching layers are serialized into a compact JSON brief
suitable for LLM / ML massing workflows.
"""

from __future__ import print_function

import json
import math
import os
import sys

import rhinoscriptsyntax as rs
import scriptcontext as sc
import Rhino
from Rhino.Geometry import Point3d, Polyline, PolylineCurve


VERSION = "1.0"

LAYER_MAP = {
    "site": "site_boundary",
    "context_buildings": "buildings",
    "setbacks": "setbacks",
    "terrain": "terrain",
    "roads": "roads",
}


def _units_name():
    unit = sc.doc.ModelUnitSystem
    names = {
        Rhino.UnitSystem.Meters: "meters",
        Rhino.UnitSystem.Millimeters: "millimeters",
        Rhino.UnitSystem.Feet: "feet",
        Rhino.UnitSystem.Inches: "inches",
    }
    return names.get(unit, "meters")


def _pt_to_list(pt, include_z=True):
    if include_z:
        return [round(pt.X, 4), round(pt.Y, 4), round(pt.Z, 4)]
    return [round(pt.X, 4), round(pt.Y, 4)]


def _curve_to_vertices(curve, include_z=True):
    """Extract vertices from a curve; sample if not polyline."""
    if curve is None:
        return []

    if isinstance(curve, PolylineCurve):
        pl = curve.ToPolyline()
        if pl is not None:
            return [_pt_to_list(pl[i], include_z) for i in range(pl.Count)]

    if curve.TryGetPolyline()[0]:
        pl = curve.TryGetPolyline()[1]
        return [_pt_to_list(pl[i], include_z) for i in range(pl.Count)]

    # Sample closed/open curves at reasonable spacing
    length = curve.GetLength()
    count = max(8, int(math.ceil(length / 2.0)))
    params = curve.DivideByCount(count, True)
    if params is None:
        return []
    return [_pt_to_list(curve.PointAt(t), include_z) for t in params]


def _footprint_from_geometry(geom):
    """Bottom-face footprint for breps/extrusions/meshes; curve for 2D."""
    bbox = geom.GetBoundingBox(True)
    if bbox.IsValid:
        z = bbox.Min.Z
        return [
            [round(bbox.Min.X, 4), round(bbox.Min.Y, 4), round(z, 4)],
            [round(bbox.Max.X, 4), round(bbox.Min.Y, 4), round(z, 4)],
            [round(bbox.Max.X, 4), round(bbox.Max.Y, 4), round(z, 4)],
            [round(bbox.Min.X, 4), round(bbox.Max.Y, 4), round(z, 4)],
        ]

    if hasattr(geom, "ToPolyline"):
        pl = geom.ToPolyline(0.1, 0.1, 0.1, 1000)
        if pl is not None:
            return [_pt_to_list(pl[i]) for i in range(pl.Count)]
    return []


def _object_user_strings(rh_obj):
    result = {}
    keys = rh_obj.Attributes.GetUserStringKeys()
    if keys:
        for key in keys:
            result[key] = rh_obj.Attributes.GetUserString(key)
    return result


def _polygon_area_2d(vertices):
    if len(vertices) < 3:
        return 0.0
    area = 0.0
    n = len(vertices)
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0


def _collect_by_layer(layer_name):
    objects = rs.ObjectsByLayer(layer_name)
    if not objects:
        return []
    return objects


def _export_site_boundary():
    objs = _collect_by_layer("site")
    if not objs:
        return None

    for obj_id in objs:
        rh_obj = rs.coercerhinoobject(obj_id)
        if rh_obj is None:
            continue
        geom = rh_obj.Geometry
        if isinstance(geom, Rhino.Geometry.Curve):
            verts = _curve_to_vertices(geom)
        else:
            verts = _footprint_from_geometry(geom)

        if len(verts) >= 3:
            area = _polygon_area_2d([[v[0], v[1]] for v in verts])
            return {
                "layer": "site",
                "vertices": verts,
                "area": round(area, 2),
            }
    return None


def _export_buildings():
    buildings = []
    objs = _collect_by_layer("context_buildings")
    for i, obj_id in enumerate(objs):
        rh_obj = rs.coercerhinoobject(obj_id)
        if rh_obj is None:
            continue

        geom = rh_obj.Geometry
        bbox = geom.GetBoundingBox(True)
        height = round(bbox.Max.Z - bbox.Min.Z, 4) if bbox.IsValid else 0.0

        if isinstance(geom, Rhino.Geometry.Curve):
            footprint = _curve_to_vertices(geom, include_z=True)
        else:
            footprint = _footprint_from_geometry(geom)

        name = rh_obj.Attributes.Name or "building_{}".format(i + 1)
        obj_id_str = rh_obj.Attributes.Id.ToString()

        entry = {
            "id": obj_id_str[:8],
            "name": name,
            "layer": "context_buildings",
            "footprint": footprint,
            "height": height,
            "bbox": {
                "min": _pt_to_list(bbox.Min),
                "max": _pt_to_list(bbox.Max),
            },
            "footprint_area": round(
                _polygon_area_2d([[v[0], v[1]] for v in footprint]), 2
            ),
        }

        user_strings = _object_user_strings(rh_obj)
        if user_strings:
            entry["user_strings"] = user_strings

        floor_height = user_strings.get("floor_height")
        if floor_height:
            try:
                entry["floors"] = int(round(height / float(floor_height)))
            except (ValueError, ZeroDivisionError):
                pass

        buildings.append(entry)
    return buildings


def _export_setbacks():
    setbacks = []
    objs = _collect_by_layer("setbacks")
    for i, obj_id in enumerate(objs):
        rh_obj = rs.coercerhinoobject(obj_id)
        if rh_obj is None:
            continue
        geom = rh_obj.Geometry
        if isinstance(geom, Rhino.Geometry.Curve):
            verts = _curve_to_vertices(geom)
        else:
            verts = _footprint_from_geometry(geom)
        if len(verts) >= 3:
            name = rh_obj.Attributes.Name or "setback_{}".format(i + 1)
            setbacks.append({
                "name": name,
                "layer": "setbacks",
                "vertices": verts,
            })
    return setbacks


def _export_roads():
    roads = []
    objs = _collect_by_layer("roads")
    for i, obj_id in enumerate(objs):
        rh_obj = rs.coercerhinoobject(obj_id)
        if rh_obj is None:
            continue
        geom = rh_obj.Geometry
        if not isinstance(geom, Rhino.Geometry.Curve):
            continue

        centerline = _curve_to_vertices(geom)
        name = rh_obj.Attributes.Name or "road_{}".format(i + 1)
        entry = {
            "name": name,
            "layer": "roads",
            "centerline": centerline,
        }

        width_str = rh_obj.Attributes.GetUserString("width")
        if width_str:
            try:
                entry["width"] = float(width_str)
            except ValueError:
                pass

        roads.append(entry)
    return roads


def _export_terrain():
    objs = _collect_by_layer("terrain")
    if not objs:
        return None

    sample_points = []
    for obj_id in objs:
        rh_obj = rs.coercerhinoobject(obj_id)
        if rh_obj is None:
            continue
        geom = rh_obj.Geometry

        if isinstance(geom, Rhino.Geometry.Mesh):
            for i in range(min(geom.Vertices.Count, 200)):
                v = geom.Vertices[i]
                sample_points.append([round(v.X, 4), round(v.Y, 4), round(v.Z, 4)])
        elif isinstance(geom, Rhino.Geometry.PointCloud):
            for pt in geom:
                sample_points.append(_pt_to_list(pt.Location))
        elif isinstance(geom, Rhino.Geometry.Point):
            sample_points.append(_pt_to_list(geom.Location))

    if not sample_points:
        return None

    return {"layer": "terrain", "sample_points": sample_points}


def _export_zoning():
    """Read zoning from document user strings (set via DocumentProperties → Notes/UserText)."""
    zoning = {}
    keys = ["max_height", "far", "max_coverage", "notes"]
    for key in keys:
        val = sc.doc.Strings.GetValue("zoning_" + key)
        if val:
            if key == "notes":
                zoning[key] = val
            else:
                try:
                    zoning[key] = float(val)
                except ValueError:
                    zoning[key] = val
    return zoning if zoning else None


def export_site_context():
    site_boundary = _export_site_boundary()
    if site_boundary is None:
        print("WARNING: No site boundary found on layer 'site'.")
        site_boundary = {"layer": "site", "vertices": [], "area": 0}

    context = {
        "version": VERSION,
        "units": _units_name(),
        "north_vector": [0, 1],
        "site_boundary": site_boundary,
        "buildings": _export_buildings(),
        "setbacks": _export_setbacks(),
        "roads": _export_roads(),
    }

    terrain = _export_terrain()
    if terrain:
        context["terrain"] = terrain

    zoning = _export_zoning()
    if zoning:
        context["zoning"] = zoning

    context["metadata"] = {
        "exported_from": "massing-explorer",
        "object_count": sc.doc.Objects.Count,
    }

    return context


def main():
    context = export_site_context()

    filter_str = "JSON file (*.json)|*.json|All Files (*.*)|*.*||"
    path = rs.SaveFileName("Export Site Context", filter_str, "site-context.json")
    if not path:
        print("Export cancelled.")
        return

    with open(path, "w") as f:
        json.dump(context, f, indent=2)

    print("Exported site context to: {}".format(path))
    print("  Buildings: {}".format(len(context["buildings"])))
    print("  Setbacks:  {}".format(len(context["setbacks"])))
    print("  Roads:     {}".format(len(context["roads"])))


if __name__ == "__main__":
    main()
