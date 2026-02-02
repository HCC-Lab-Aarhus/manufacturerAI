"""
Blender geometry generator (runs inside Blender).

Run via Blender, e.g.:
  blender -b --python src/blender/generate_blender.py -- configs/default_params.json outputs/run1

Args after `--`:
  1) <params.json>
  2) <output_dir>

Outputs:
  - remote_body.stl
  - generated_remote.blend
"""

import bpy
import json
import sys
import math
import os


# ----------------------------
# CLI args
# ----------------------------
def parse_args():
    argv = sys.argv
    if "--" not in argv:
        raise RuntimeError("Expected '-- <params.json> <output_dir>'")
    idx = argv.index("--")
    extra = argv[idx + 1 :]
    if len(extra) != 2:
        raise RuntimeError("Expected two args: <params.json> <output_dir>")
    return extra[0], extra[1]


# ----------------------------
# Scene helpers
# ----------------------------
def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def set_mm_units():
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001
    scene.unit_settings.length_unit = "MILLIMETERS"


def apply_all_modifiers(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    for mod in list(obj.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception:
            pass


def cleanup_mesh(obj):
    """Basic cleanup after booleans."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")

        try:
            bpy.ops.mesh.merge_by_distance(distance=0.0001)
        except Exception:
            pass

        try:
            bpy.ops.mesh.normals_make_consistent(inside=False)
        except Exception:
            pass

    finally:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass


# ----------------------------
# Geometry primitives
# ----------------------------
def add_mm_cube(size_x, size_y, size_z, location=(0, 0, 0), name="CUBE"):
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size_x / 2.0, size_y / 2.0, size_z / 2.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def add_base_body(length_mm, width_mm, thickness_mm, corner_radius_mm):
    obj = add_mm_cube(width_mm, length_mm, thickness_mm, location=(0, 0, 0), name="REMOTE_SOLID")

    if corner_radius_mm > 0:
        bev = obj.modifiers.new(name="Bevel", type="BEVEL")
        bev.width = corner_radius_mm
        bev.segments = 6
        bev.profile = 0.7
        if hasattr(bev, "limit_method"):
            bev.limit_method = "ANGLE"
            bev.angle_limit = 0.785398
    return obj


# ----------------------------
# Booleans
# ----------------------------
def _set_boolean_options(mod):
    if hasattr(mod, "solver"):
        mod.solver = "EXACT"
    if hasattr(mod, "use_hole_tolerant"):
        mod.use_hole_tolerant = True


def boolean_difference(target, cutter, name="BoolDiff"):
    mod = target.modifiers.new(name=name, type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    _set_boolean_options(mod)
    return mod


# ----------------------------
# Shell
# ----------------------------
def make_hollow_shell(solid, wall_mm):
    outer = solid
    outer.name = "REMOTE_OUTER"

    inner = outer.copy()
    inner.data = outer.data.copy()
    bpy.context.collection.objects.link(inner)
    inner.name = "REMOTE_INNER"

    dims = outer.dimensions
    inner_w = max(1.0, dims.x - 2 * wall_mm)
    inner_l = max(1.0, dims.y - 2 * wall_mm)
    inner_t = max(1.0, dims.z - 2 * wall_mm)

    inner.scale = (inner_w / dims.x, inner_l / dims.y, inner_t / dims.z)
    bpy.ops.object.select_all(action="DESELECT")
    inner.select_set(True)
    bpy.context.view_layer.objects.active = inner
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    boolean_difference(outer, inner, name="Hollow")
    apply_all_modifiers(outer)

    bpy.data.objects.remove(inner, do_unlink=True)
    cleanup_mesh(outer)
    return outer


# ----------------------------
# Battery compartment
# ----------------------------
def cut_battery_compartment_and_make_door(remote_obj, params, out_dir: str):
    """
    Creates AAA battery cavity with bottom opening and door.
    """
    bat = params.get("battery") or {}
    if not bool(bat.get("enabled", True)):
        return None

    count = int(bat.get("count", 2))
    if count not in (1, 2):
        count = 2

    r = params["remote"]
    length_mm = float(r["length_mm"])
    width_mm = float(r["width_mm"])
    thickness_mm = float(r["thickness_mm"])
    wall_mm = float(r["wall_mm"])

    clearance = float(bat.get("clearance_mm", 0.6))
    placement = bat.get("placement") or {}
    margin_bottom_end = float(placement.get("margin_from_bottom_end_mm", 12.0))
    z_center = float(placement.get("z_center_mm", 0.0))
    x_spacing = float(placement.get("x_spacing_mm", 3.0))

    # AAA dimensions with clearance
    batt_len = 44.0 + clearance
    batt_rad = (10.1 / 2.0) + clearance

    # Place along Y (length axis)
    y_center = -length_mm / 2.0 + margin_bottom_end + batt_len / 2.0

    # Battery cutters (curved recesses for better fit)
    cutters = []
    x_positions = [0.0]
    if count == 2:
        x_positions = [-(batt_rad + x_spacing / 2.0), (batt_rad + x_spacing / 2.0)]

    # Create form-fitting semicircular recesses
    for x in x_positions:
        # Create a cylinder that extends from bottom to center height for cradle effect
        recess_depth = batt_len
        recess_height = batt_rad * 1.2  # Slightly more than radius for good fit
        
        bpy.ops.mesh.primitive_cylinder_add(
            radius=batt_rad,
            depth=recess_depth,
            location=(x, y_center, -thickness_mm/2 + recess_height/2),
            rotation=(math.radians(90.0), 0.0, 0.0),
        )
        cyl = bpy.context.active_object
        cyl.name = "BAT_CRADLE"
        cutters.append(cyl)

    bpy.ops.object.select_all(action="DESELECT")
    for c in cutters:
        c.select_set(True)
    bpy.context.view_layer.objects.active = cutters[0]
    bpy.ops.object.join()
    batt_union = bpy.context.active_object
    batt_union.name = "BAT_CUT_UNION"

    boolean_difference(remote_obj, batt_union, name="BatteryCavity")
    apply_all_modifiers(remote_obj)
    cleanup_mesh(remote_obj)
    bpy.data.objects.remove(batt_union, do_unlink=True)

    # Battery door
    door_cfg = bat.get("door") or {}
    if not bool(door_cfg.get("enabled", True)):
        return None

    open_clear = float(door_cfg.get("opening_clearance_mm", 0.3))
    ledge_w = float(door_cfg.get("ledge_width_mm", 1.0))
    ledge_depth = float(door_cfg.get("ledge_depth_mm", 1.2))
    door_thick = float(door_cfg.get("door_thickness_mm", 1.8))

    pack_w = (2 * batt_rad) * count + (x_spacing if count == 2 else 0.0)
    pack_l = batt_len

    open_w = min(width_mm - 2 * wall_mm, pack_w + 2 * open_clear)
    open_l = min(length_mm - 2 * wall_mm, pack_l + 2 * open_clear)

    bottom_z = -thickness_mm / 2.0

    # Outer opening: through bottom wall
    cut_depth_outer = wall_mm + 0.4
    outer_open = add_mm_cube(
        open_w,
        open_l,
        cut_depth_outer,
        location=(0.0, y_center, bottom_z + cut_depth_outer / 2.0),
        name="DOOR_OUTER_CUT",
    )
    boolean_difference(remote_obj, outer_open, name="DoorOuter")
    apply_all_modifiers(remote_obj)
    cleanup_mesh(remote_obj)
    bpy.data.objects.remove(outer_open, do_unlink=True)

    # Inner opening deeper but smaller -> ledge ring remains
    inner_w = max(1.0, open_w - 2 * ledge_w)
    inner_l = max(1.0, open_l - 2 * ledge_w)
    cut_depth_inner = wall_mm + ledge_depth + 0.4
    inner_open = add_mm_cube(
        inner_w,
        inner_l,
        cut_depth_inner,
        location=(0.0, y_center, bottom_z + cut_depth_inner / 2.0),
        name="DOOR_INNER_CUT",
    )
    boolean_difference(remote_obj, inner_open, name="DoorInner")
    apply_all_modifiers(remote_obj)
    cleanup_mesh(remote_obj)
    bpy.data.objects.remove(inner_open, do_unlink=True)

    # Door object (assembled position: flush with bottom exterior)
    door_clear = open_clear
    door_w = max(1.0, open_w - 2 * door_clear)
    door_l = max(1.0, open_l - 2 * door_clear)

    door_center_z = bottom_z + (door_thick / 2.0)  # flush with bottom
    door_obj = add_mm_cube(
        door_w,
        door_l,
        door_thick,
        location=(0.0, y_center, door_center_z),
        name="BATTERY_DOOR",
    )

    # Export door STL
    export_stl(door_obj, os.path.join(out_dir, "battery_door.stl"))

    # For viewing in the .blend, offset the door slightly downward after export
    door_obj.location.z -= (door_thick + 2.0)

    return door_obj


# ----------------------------
# Export
# ----------------------------
def export_stl(obj, filepath: str):
    filepath = os.path.abspath(filepath)

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    if hasattr(bpy.ops.wm, "stl_export"):
        bpy.ops.wm.stl_export(filepath=filepath, export_selected_objects=True)
        return

    if hasattr(bpy.ops.export_mesh, "stl"):
        bpy.ops.export_mesh.stl(filepath=filepath, use_selection=True)
        return

    raise RuntimeError("No STL export operator found on this Blender build.")


# ----------------------------
# Main
# ----------------------------
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

    # 1) Base solid
    solid = add_base_body(length_mm, width_mm, thickness_mm, corner_radius_mm)
    apply_all_modifiers(solid)
    cleanup_mesh(solid)

    # 2) Hollow shell
    shell = make_hollow_shell(solid, wall_mm)
    cleanup_mesh(shell)

    # 3) Battery compartment
    door_obj = cut_battery_compartment_and_make_door(shell, params, out_dir)

    # Export the hollow body
    export_stl(shell, os.path.join(out_dir, "remote_body.stl"))

    # Save a .blend for inspection
    try:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, "generated_remote.blend"))
    except Exception:
        pass


if __name__ == "__main__":
    main()
