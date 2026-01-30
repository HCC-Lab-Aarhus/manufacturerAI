"""
Blender geometry generator (runs inside Blender).

Run via Blender, e.g.:
  blender -b --python src/blender/generate_blender.py -- configs/default_params.json outputs/run1

Args after `--`:
  1) <params.json>
  2) <output_dir>

Outputs:
  - remote_body.stl
  - battery_door.stl (if enabled)
  - ink_trace.stl    (if enabled)
  - generated_remote.blend
"""

import bpy #read notes
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
    # Display-only; internal coordinates are still Blender units.
    # We keep using "mm-like" numbers directly for modeling and STL export.
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

        # Merge-by-distance (Blender 4.x)
        try:
            bpy.ops.mesh.merge_by_distance(distance=0.0001)
        except Exception:
            pass

        # Recalculate normals
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
# Geometry primitives (mm-like)
# IMPORTANT: use size=2.0 cubes so scale=(dim/2) yields correct final dims
# ----------------------------
def add_mm_cube(size_x, size_y, size_z, location=(0, 0, 0), name="CUBE"):
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location)  # <-- key fix
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size_x / 2.0, size_y / 2.0, size_z / 2.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def add_base_body(length_mm, width_mm, thickness_mm, corner_radius_mm):
    # X=width, Y=length, Z=thickness. Origin at center.
    obj = add_mm_cube(width_mm, length_mm, thickness_mm, location=(0, 0, 0), name="REMOTE_SOLID")

    if corner_radius_mm > 0:
        bev = obj.modifiers.new(name="Bevel", type="BEVEL")
        bev.width = corner_radius_mm
        bev.segments = 6
        bev.profile = 0.7
        if hasattr(bev, "limit_method"):
            bev.limit_method = "ANGLE"
            bev.angle_limit = 0.785398  # 45 degrees
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

    dims = outer.dimensions  # same units as we modeled in
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
# Button layout
# ----------------------------
def button_positions(
    length_mm,
    width_mm,
    rows,
    cols,
    diam_mm,
    spacing_mm,
    margin_top_mm,
    margin_bottom_mm=None,
    margin_side_mm=None,
):
    # Centered grid in X. Y is placed from the top margin downward.
    grid_w = cols * diam_mm + (cols - 1) * spacing_mm
    x0 = -grid_w / 2.0 + diam_mm / 2.0

    y_top = length_mm / 2.0 - margin_top_mm - diam_mm / 2.0
    for r in range(rows):
        y = y_top - r * (diam_mm + spacing_mm)
        for c in range(cols):
            x = x0 + c * (diam_mm + spacing_mm)
            yield x, y


def cut_button_holes(remote_obj, params):
    r = params["remote"]
    b = params["buttons"]

    length_mm = float(r["length_mm"])
    width_mm = float(r["width_mm"])
    thickness_mm = float(r["thickness_mm"])

    rows = int(b["rows"])
    cols = int(b["cols"])
    diam_mm = float(b["diam_mm"])
    spacing_mm = float(b["spacing_mm"])
    margin_top_mm = float(b["margin_top_mm"])
    clearance = float(b.get("hole_clearance_mm", 0.25))

    holes = []
    for x, y in button_positions(length_mm, width_mm, rows, cols, diam_mm, spacing_mm, margin_top_mm):
        bpy.ops.mesh.primitive_cylinder_add(
            radius=(diam_mm / 2.0 + clearance),
            depth=thickness_mm * 2.0,
            location=(x, y, thickness_mm / 4.0),
        )
        holes.append(bpy.context.active_object)

    if not holes:
        return

    bpy.ops.object.select_all(action="DESELECT")
    for h in holes:
        h.select_set(True)
    bpy.context.view_layer.objects.active = holes[0]
    bpy.ops.object.join()
    hole_union = bpy.context.active_object
    hole_union.name = "BUTTON_HOLE_CUTTERS"

    boolean_difference(remote_obj, hole_union, name="ButtonHoles")
    apply_all_modifiers(remote_obj)
    cleanup_mesh(remote_obj)

    bpy.data.objects.remove(hole_union, do_unlink=True)


def cut_switch_pockets(remote_obj, params):
    """Subtract rectangular switch pockets under each button hole."""
    sw = params.get("switches") or {}
    pocket_depth = float(sw.get("pocket_depth_mm", 0.0))
    pocket_xy = float(sw.get("pocket_xy_mm", 0.0))
    pocket_clear = float(sw.get("pocket_clearance_mm", 0.0))
    if pocket_depth <= 0 or pocket_xy <= 0:
        return

    r = params["remote"]
    b = params["buttons"]

    length_mm = float(r["length_mm"])
    width_mm = float(r["width_mm"])
    thickness_mm = float(r["thickness_mm"])
    wall_mm = float(r["wall_mm"])

    rows = int(b["rows"])
    cols = int(b["cols"])
    diam_mm = float(b["diam_mm"])
    spacing_mm = float(b["spacing_mm"])
    margin_top_mm = float(b["margin_top_mm"])
    margin_bottom_mm = float(b.get("margin_bottom_mm", 0.0))
    margin_side_mm = float(b.get("margin_side_mm", 0.0))

    # Pocket placed under the top inner surface
    top_inner_z = (thickness_mm / 2.0) - wall_mm
    pocket_center_z = top_inner_z - pocket_depth / 2.0

    cutters = []
    for (x, y) in button_positions(
        length_mm, width_mm, rows, cols, diam_mm, spacing_mm, margin_top_mm, margin_bottom_mm, margin_side_mm
    ):
        c = add_mm_cube(
            pocket_xy + 2 * pocket_clear,
            pocket_xy + 2 * pocket_clear,
            pocket_depth,
            location=(x, y, pocket_center_z),
            name="SW_POCKET",
        )
        cutters.append(c)

    bpy.ops.object.select_all(action="DESELECT")
    for c in cutters:
        c.select_set(True)
    bpy.context.view_layer.objects.active = cutters[0]
    bpy.ops.object.join()
    pocket_union = bpy.context.active_object
    pocket_union.name = "SW_POCKET_UNION"

    boolean_difference(remote_obj, pocket_union, name="SwitchPockets")
    apply_all_modifiers(remote_obj)
    cleanup_mesh(remote_obj)

    bpy.data.objects.remove(pocket_union, do_unlink=True)


# ----------------------------
# Battery cavity + door
# ----------------------------
def cut_battery_compartment_and_make_door(remote_obj, params, out_dir: str):
    """
    Creates 1x or 2x AAA cavity, bottom opening, and a simple ledge.
    Generates a door object in the assembled position, exports it, then offsets for viewing.
    """
    bat = params.get("battery") or {}
    door_cfg = bat.get("door") or {}
    if bat.get("type", "AAA") != "AAA":
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

    # AAA with clearance
    batt_len = 44.5 + 2.0
    batt_rad = (10.5 / 2.0) + clearance

    # Place along Y (length axis)
    y_center = -length_mm / 2.0 + margin_bottom_end + batt_len / 2.0

    # Battery cutters (cylinders)
    cutters = []
    x_positions = [0.0]
    if count == 2:
        x_positions = [-(batt_rad + x_spacing / 2.0), (batt_rad + x_spacing / 2.0)]

    for x in x_positions:
        bpy.ops.mesh.primitive_cylinder_add(
            radius=batt_rad,
            depth=batt_len,
            location=(x, y_center, z_center),
            rotation=(math.radians(90.0), 0.0, 0.0),
        )
        cyl = bpy.context.active_object
        cyl.name = "BAT_CUT"
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

    # Door opening + ledge
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
# Ink trace volume (optional)
# ----------------------------
def _cylinder_between(p1, p2, radius):
    from mathutils import Vector

    v1 = Vector(p1)
    v2 = Vector(p2)
    d = v2 - v1
    dist = d.length
    if dist <= 1e-6:
        return None

    mid = (v1 + v2) / 2.0
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=dist, location=mid)
    obj = bpy.context.active_object

    # Rotate so cylinder Z aligns with segment direction
    z_axis = Vector((0, 0, 1))
    rot = z_axis.rotation_difference(d.normalized())
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = rot
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    obj.rotation_mode = "XYZ"
    return obj


def make_ink_trace(params):
    wiring = params.get("wiring") or {}
    if not bool(wiring.get("enabled", False)):
        return None
    if wiring.get("mode", "ink_volume") != "ink_volume":
        return None

    r = params["remote"]
    b = params["buttons"]

    length_mm = float(r["length_mm"])
    width_mm = float(r["width_mm"])
    thickness_mm = float(r["thickness_mm"])
    wall_mm = float(r["wall_mm"])

    rows = int(b["rows"])
    cols = int(b["cols"])
    diam_mm = float(b["diam_mm"])
    spacing_mm = float(b["spacing_mm"])
    margin_top_mm = float(b["margin_top_mm"])
    margin_bottom_mm = float(b.get("margin_bottom_mm", 0.0))
    margin_side_mm = float(b.get("margin_side_mm", 0.0))

    trace_w = float(wiring.get("trace_width_mm", 1.2))
    trace_h = float(wiring.get("trace_height_mm", 0.4))
    z_from_bottom_inner = float(wiring.get("z_from_bottom_inner_mm", 0.8))

    bottom_inner_z = -thickness_mm / 2.0 + wall_mm
    z = bottom_inner_z + z_from_bottom_inner + trace_h / 2.0

    trunk_x = float(wiring.get("trunk_x_mm", 0.0))

    led_cfg = wiring.get("led") or {}
    led_enabled = bool(led_cfg.get("enabled", True))
    led_y = length_mm / 2.0 - float(led_cfg.get("y_from_top_end_mm", 8.0))
    led_x = float(led_cfg.get("x_mm", 0.0))

    button_pos = list(
        button_positions(length_mm, width_mm, rows, cols, diam_mm, spacing_mm, margin_top_mm, margin_bottom_mm, margin_side_mm)
    )
    if not button_pos:
        return None

    y_vals = [p[1] for p in button_pos]
    trunk_y0 = min(y_vals) - 8.0
    trunk_y1 = led_y if led_enabled else (max(y_vals) + 10.0)

    segs = []
    segs.append(((trunk_x, trunk_y0, z), (trunk_x, trunk_y1, z)))
    for (x, y) in button_pos:
        segs.append(((x, y, z), (trunk_x, y, z)))
    if led_enabled:
        segs.append(((trunk_x, led_y, z), (led_x, led_y, z)))

    radius = trace_w / 2.0
    pieces = []
    for p1, p2 in segs:
        obj = _cylinder_between(p1, p2, radius=radius)
        if obj:
            obj.name = "INK_SEG"
            pieces.append(obj)

    if not pieces:
        return None

    bpy.ops.object.select_all(action="DESELECT")
    for o in pieces:
        o.select_set(True)
    bpy.context.view_layer.objects.active = pieces[0]
    bpy.ops.object.join()
    ink = bpy.context.active_object
    ink.name = "INK_TRACE"

    cleanup_mesh(ink)
    return ink


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

    # 1) Base + shell
    solid = add_base_body(length_mm, width_mm, thickness_mm, corner_radius_mm)
    apply_all_modifiers(solid)
    cleanup_mesh(solid)

    shell = make_hollow_shell(solid, wall_mm)
    cleanup_mesh(shell)

    # 2) Buttons + switch pockets
    cut_button_holes(shell, params)
    cut_switch_pockets(shell, params)

    # 3) Battery cavity + door
    door_obj = cut_battery_compartment_and_make_door(shell, params, out_dir)

    # 4) Ink trace (optional)
    ink_obj = make_ink_trace(params)

    # Export body + optional ink
    export_stl(shell, os.path.join(out_dir, "remote_body.stl"))
    if ink_obj is not None:
        export_stl(ink_obj, os.path.join(out_dir, "ink_trace.stl"))
        # After export, move ink up for easier viewing in the .blend
        ink_obj.location.z += 10.0

    # Save a .blend for inspection
    try:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, "generated_remote.blend"))
    except Exception:
        pass


if __name__ == "__main__":
    main()
