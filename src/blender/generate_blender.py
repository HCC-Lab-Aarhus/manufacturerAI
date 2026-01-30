"""Blender geometry generator (runs inside Blender).

IMPORTANT:
- This script imports `bpy`, which is only available inside Blender's bundled Python.
- Run via Blender, e.g.:
  blender -b --python src/blender/generate_blender.py -- configs/default_params.json outputs/run1

Args after `--`:
  1) <params.json>
  2) <output_dir>

Outputs:
  - remote_top.stl
  - remote_bottom.stl
  - generated_remote.blend  (open this in Blender to inspect the result)
"""

import bpy # cant import this on windows, used pip install fake-bpy-module-4.5 
import json
import sys
import os

def parse_args():
    argv = sys.argv
    if "--" not in argv:
        raise RuntimeError("Expected '-- <params.json> <output_dir>'")
    idx = argv.index("--")
    extra = argv[idx + 1:]
    if len(extra) != 2:
        raise RuntimeError("Expected two args: <params.json> <output_dir>")
    return extra[0], extra[1]

def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

def set_mm_units():
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    # 1 Blender unit = 1 mm
    scene.unit_settings.scale_length = 0.001
    scene.unit_settings.length_unit = 'MILLIMETERS'

def apply_all_modifiers(obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    for mod in list(obj.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception:
            # In background mode some applies can fail if context isn't perfect; ignore for MVP.
            pass

def cleanup_mesh(obj):
    """Basic cleanup to reduce boolean artifacts."""
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        # Remove doubles / merge by distance
        try:
            bpy.ops.mesh.remove_doubles(threshold=0.0001)  # ~0.0001 mm in our mm-units convention
        except Exception:
            bpy.ops.mesh.merge_by_distance(distance=0.0001)
        bpy.ops.mesh.normals_make_consistent(inside=False)
    finally:
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

def add_base_body(length_mm, width_mm, thickness_mm, corner_radius_mm):
    # X=width, Y=length, Z=thickness. Origin at center.
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = "REMOTE_SOLID"
    obj.scale = (width_mm / 2.0, length_mm / 2.0, thickness_mm / 2.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    if corner_radius_mm > 0:
        bev = obj.modifiers.new(name="Bevel", type='BEVEL')
        bev.width = corner_radius_mm
        bev.segments = 6
        bev.profile = 0.7
        # Limit bevel to edges (keeps it stable)
        if hasattr(bev, "limit_method"):
            bev.limit_method = 'ANGLE'
            bev.angle_limit = 0.785398  # 45 degrees
    return obj

def _set_boolean_options(mod):
    # Make booleans more robust where available.
    if hasattr(mod, "solver"):
        mod.solver = 'EXACT'
    if hasattr(mod, "use_self"):
        mod.use_self = True
    if hasattr(mod, "use_hole_tolerant"):
        mod.use_hole_tolerant = True

def boolean_difference(target, cutter, name="BoolDiff"):
    mod = target.modifiers.new(name=name, type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter
    _set_boolean_options(mod)
    return mod

def boolean_intersect(target, cutter, name="BoolIntersect"):
    mod = target.modifiers.new(name=name, type='BOOLEAN')
    mod.operation = 'INTERSECT'
    mod.object = cutter
    _set_boolean_options(mod)
    return mod

def make_hollow_shell(solid, wall_mm):
    outer = solid
    outer.name = "REMOTE_OUTER"

    inner = outer.copy()
    inner.data = outer.data.copy()
    bpy.context.collection.objects.link(inner)
    inner.name = "REMOTE_INNER"

    dims = outer.dimensions  # in our mm convention
    inner_w = max(1.0, dims.x - 2 * wall_mm)
    inner_l = max(1.0, dims.y - 2 * wall_mm)
    inner_t = max(1.0, dims.z - 2 * wall_mm)

    # Scale inner cavity down per axis
    inner.scale = (inner_w / dims.x, inner_l / dims.y, inner_t / dims.z)
    bpy.ops.object.select_all(action='DESELECT')
    inner.select_set(True)
    bpy.context.view_layer.objects.active = inner
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    boolean_difference(outer, inner, name="Hollow")
    apply_all_modifiers(outer)

    bpy.data.objects.remove(inner, do_unlink=True)
    cleanup_mesh(outer)
    return outer

def split_top_bottom(shell, thickness_mm):
    # Split at mid-plane z=0 by intersecting with big half-space cubes.
    big = 10000.0
    half = thickness_mm / 2.0

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, half / 2.0))
    top_cutter = bpy.context.active_object
    top_cutter.name = "TOP_CUTTER"
    top_cutter.scale = (big, big, big)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, -half / 2.0))
    bottom_cutter = bpy.context.active_object
    bottom_cutter.name = "BOTTOM_CUTTER"
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

    cleanup_mesh(top)
    cleanup_mesh(bottom)

    bpy.data.objects.remove(top_cutter, do_unlink=True)
    bpy.data.objects.remove(bottom_cutter, do_unlink=True)
    bpy.data.objects.remove(shell, do_unlink=True)

    return top, bottom

def button_positions(length_mm, rows, cols, diam_mm, spacing_mm, margin_top_mm):
    # Centered grid. Margins are enforced by the validator, not by this placer.
    grid_w = cols * diam_mm + (cols - 1) * spacing_mm
    x0 = -grid_w / 2.0 + diam_mm / 2.0
    y_top = length_mm / 2.0 - margin_top_mm - diam_mm / 2.0
    for r in range(rows):
        y = y_top - r * (diam_mm + spacing_mm)
        for c in range(cols):
            x = x0 + c * (diam_mm + spacing_mm)
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
            radius=(diam_mm / 2.0 + clearance),
            depth=thickness_mm * 2.0,
            location=(x, y, thickness_mm / 4.0),
        )
        holes.append(bpy.context.active_object)

    if not holes:
        return

    # Join hole cutters
    bpy.ops.object.select_all(action='DESELECT')
    for h in holes:
        h.select_set(True)
    bpy.context.view_layer.objects.active = holes[0]
    bpy.ops.object.join()
    hole_union = bpy.context.active_object
    hole_union.name = "BUTTON_HOLE_CUTTERS"

    boolean_difference(top_obj, hole_union, name="ButtonHoles")
    apply_all_modifiers(top_obj)
    cleanup_mesh(top_obj)

    bpy.data.objects.remove(hole_union, do_unlink=True)

def export_stl(obj, filepath: str):
    import os
    filepath = os.path.abspath(filepath)

    # Select only the object
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Blender 4.x STL exporter
    if hasattr(bpy.ops.wm, "stl_export"):
        # This flag name is correct for 4.5
        bpy.ops.wm.stl_export(filepath=filepath, export_selected_objects=True)
        return

    # Fallbacks for older versions
    if hasattr(bpy.ops.export_mesh, "stl"):
        bpy.ops.export_mesh.stl(filepath=filepath, use_selection=True)
        return

    raise RuntimeError("No STL export operator found on this Blender build.")


def main():
    params_path, out_dir = parse_args()
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
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

    # Save a .blend so you can open and inspect the result.
    try:
        bpy.ops.wm.save_as_mainfile(filepath=f"{out_dir}/generated_remote.blend")
    except Exception:
        pass

if __name__ == "__main__":
    main()
