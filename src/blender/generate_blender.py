"""Blender geometry generator (runs inside Blender).

Usage:
  blender -b --python generate_blender.py -- <params.json> <output_dir>

MVP output:
- remote_top.stl
- remote_bottom.stl
"""

import bpy
import json
import sys
import re

def parse_args():
    argv = sys.argv
    if "--" not in argv:
        raise RuntimeError("Expected '-- <params.json> <output_dir>'")
    idx = argv.index("--")
    extra = argv[idx+1:]
    if len(extra) != 2:
        raise RuntimeError("Expected two args: <params.json> <output_dir>")
    return extra[0], extra[1]

def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

def set_mm_units():
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 0.001  # 1 Blender unit = 1 mm
    scene.unit_settings.length_unit = 'MILLIMETERS'

def apply_all_modifiers(obj):
    bpy.context.view_layer.objects.active = obj
    for mod in list(obj.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception:
            pass

def add_base_body(length_mm, width_mm, thickness_mm, corner_radius_mm):
    # X=width, Y=length, Z=thickness, origin at center
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0,0,0))
    obj = bpy.context.active_object
    obj.name = "REMOTE_SOLID"
    obj.scale = (width_mm/2.0, length_mm/2.0, thickness_mm/2.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    if corner_radius_mm > 0:
        bev = obj.modifiers.new(name="Bevel", type='BEVEL')
        bev.width = corner_radius_mm
        bev.segments = 6
        bev.profile = 0.7
    return obj

def boolean_difference(target, cutter, name="BoolDiff"):
    mod = target.modifiers.new(name=name, type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter
    return mod

def boolean_intersect(target, cutter, name="BoolIntersect"):
    mod = target.modifiers.new(name=name, type='BOOLEAN')
    mod.operation = 'INTERSECT'
    mod.object = cutter
    return mod

def make_hollow_shell(solid, wall_mm):
    outer = solid
    outer.name = "REMOTE_OUTER"

    inner = outer.copy()
    inner.data = outer.data.copy()
    bpy.context.collection.objects.link(inner)
    inner.name = "REMOTE_INNER"

    dims = outer.dimensions  # mm because of unit scale
    inner_w = max(1.0, dims.x - 2*wall_mm)
    inner_l = max(1.0, dims.y - 2*wall_mm)
    inner_t = max(1.0, dims.z - 2*wall_mm)

    inner.scale = (inner_w/dims.x, inner_l/dims.y, inner_t/dims.z)
    bpy.ops.object.select_all(action='DESELECT')
    inner.select_set(True)
    bpy.context.view_layer.objects.active = inner
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    boolean_difference(outer, inner, name="Hollow")
    apply_all_modifiers(outer)

    bpy.data.objects.remove(inner, do_unlink=True)
    return outer

def split_top_bottom(shell, thickness_mm):
    big = 10000.0
    half = thickness_mm/2.0

    # top half-space cutter
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0,0, half/2.0))
    top_cutter = bpy.context.active_object
    top_cutter.scale = (big, big, big)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    # bottom half-space cutter
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0,0, -half/2.0))
    bottom_cutter = bpy.context.active_object
    bottom_cutter.scale = (big, big, big)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    top = shell.copy()
    top.data = shell.data.copy()
    bpy.context.collection.objects.link(top)
    top.name = "REMOTE_TOP"

    bottom = shell.copy()
    bottom.data = shell.data.copy()
    bpy.context.collection.objects.link(bottom)
    bottom.name = "REMOTE_BOTTOM"

    boolean_intersect(top, top_cutter, name="KeepTop")
    boolean_intersect(bottom, bottom_cutter, name="KeepBottom")
    apply_all_modifiers(top)
    apply_all_modifiers(bottom)

    bpy.data.objects.remove(top_cutter, do_unlink=True)
    bpy.data.objects.remove(bottom_cutter, do_unlink=True)
    bpy.data.objects.remove(shell, do_unlink=True)

    return top, bottom

def button_positions(length_mm, rows, cols, diam_mm, spacing_mm, margin_top_mm):
    grid_w = cols*diam_mm + (cols-1)*spacing_mm
    x0 = -grid_w/2.0 + diam_mm/2.0
    y_top = length_mm/2.0 - margin_top_mm - diam_mm/2.0
    for r in range(rows):
        y = y_top - r*(diam_mm + spacing_mm)
        for c in range(cols):
            x = x0 + c*(diam_mm + spacing_mm)
            yield x, y

def cut_button_holes(top_obj, params):
    r = params["remote"]
    b = params["buttons"]
    length_mm = float(r["length_mm"])
    thickness_mm = float(r["thickness_mm"])
    rows = int(b["rows"])
    cols = int(b["cols"])
    diam_mm = float(b["diam_mm"])
    spacing_mm = float(b["spacing_mm"])
    margin_top_mm = float(b["margin_top_mm"])
    clearance = float(b.get("hole_clearance_mm", 0.25))

    holes = []
    for x, y in button_positions(length_mm, rows, cols, diam_mm, spacing_mm, margin_top_mm):
        bpy.ops.mesh.primitive_cylinder_add(
            radius=(diam_mm/2.0 + clearance),
            depth=thickness_mm*2.0,
            location=(x, y, thickness_mm/4.0)
        )
        cyl = bpy.context.active_object
        holes.append(cyl)

    if not holes:
        return

    # Join holes into one object
    bpy.ops.object.select_all(action='DESELECT')
    for h in holes:
        h.select_set(True)
    bpy.context.view_layer.objects.active = holes[0]
    bpy.ops.object.join()
    hole_union = bpy.context.active_object

    boolean_difference(top_obj, hole_union, name="ButtonHoles")
    apply_all_modifiers(top_obj)
    bpy.data.objects.remove(hole_union, do_unlink=True)

def export_stl(obj, filepath):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_mesh.stl(filepath=filepath, use_selection=True)

def main():
    params_path, out_dir = parse_args()
    params = json.loads(open(params_path, "r", encoding="utf-8").read())

    clear_scene()
    set_mm_units()

    r = params["remote"]
    length_mm = float(r["length_mm"])
    width_mm = float(r["width_mm"])
    thickness_mm = float(r["thickness_mm"])
    wall_mm = float(r["wall_mm"])
    corner_radius_mm = float(r["corner_radius_mm"])

    solid = add_base_body(length_mm, width_mm, thickness_mm, corner_radius_mm)
    apply_all_modifiers(solid)

    shell = make_hollow_shell(solid, wall_mm)
    top, bottom = split_top_bottom(shell, thickness_mm)

    cut_button_holes(top, params)

    export_stl(top, f"{out_dir}/remote_top.stl")
    export_stl(bottom, f"{out_dir}/remote_bottom.stl")

if __name__ == "__main__":
    main()
