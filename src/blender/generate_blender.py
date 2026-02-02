"""
Parametric TV Remote generator for 3D printing (Blender bpy)

Creates:
- Remote body (2mm shell, open back with rim)
- Back cover (thicker to allow 2mm deep wiring grooves)
- Battery door (sliding-ish plate that covers a window in the back cover)
- Wiring grooves on the inside face of the back cover:
    width=1.2mm, depth=2.0mm
    2 traces battery->chip
    2 traces per button->chip (6 buttons => 12)
    2 traces led->chip

Run:
  blender -b --python generate_remote.py -- <output_dir>

If <output_dir> omitted, uses ./out next to this script.
"""

import bpy
import bmesh
import math
import os
import sys
import json


# ----------------------------
# Basic scene setup
# ----------------------------
def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    # purge orphan data blocks (safe-ish)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.curves):
        for datablock in list(block):
            if datablock.users == 0:
                block.remove(datablock)


def set_mm_units():
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001  # 1 BU = 1 mm
    scene.unit_settings.length_unit = "MILLIMETERS"


def apply_modifiers(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    for m in list(obj.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=m.name)
        except Exception:
            pass


def cleanup_mesh(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        # Use the older command for Blender 4.5 compatibility
        bpy.ops.mesh.remove_doubles(threshold=0.0005)
        bpy.ops.mesh.normals_make_consistent(inside=False)
    except:
        pass
    finally:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except:
            pass


def boolean_diff(target, cutter, name="DIFF"):
    mod = target.modifiers.new(name=name, type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    if hasattr(mod, "solver"):
        mod.solver = "EXACT"
    if hasattr(mod, "use_hole_tolerant"):
        mod.use_hole_tolerant = True
    return mod


def boolean_union(target, other, name="UNION"):
    mod = target.modifiers.new(name=name, type="BOOLEAN")
    mod.operation = "UNION"
    mod.object = other
    if hasattr(mod, "solver"):
        mod.solver = "EXACT"
    if hasattr(mod, "use_hole_tolerant"):
        mod.use_hole_tolerant = True
    return mod


def join_objects(objs, name="JOINED"):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    objs[0].name = name
    return objs[0]


# ----------------------------
# Geometry helpers
# ----------------------------
def add_cube(size_x, size_y, size_z, loc=(0, 0, 0), name="Cube"):
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=loc)
    o = bpy.context.active_object
    o.name = name
    o.scale = (size_x / 2.0, size_y / 2.0, size_z / 2.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return o


def add_cyl(radius, depth, loc=(0, 0, 0), rot=(0, 0, 0), name="Cyl"):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return o


def rounded_box(size_x, size_y, size_z, radius, segments=10, loc=(0, 0, 0), name="RoundedBox"):
    """
    Create a box and bevel edges for a rounded-rectangle look.
    """
    o = add_cube(size_x, size_y, size_z, loc=loc, name=name)
    bev = o.modifiers.new(name="Bevel", type="BEVEL")
    bev.width = max(0.001, radius)
    bev.segments = max(1, int(segments))
    bev.profile = 0.7
    # angle limit to avoid beveling tiny cutter artifacts too much (later)
    if hasattr(bev, "limit_method"):
        bev.limit_method = "ANGLE"
        bev.angle_limit = math.radians(60)
    return o


def export_stl_selected(filepath):
    filepath = os.path.abspath(filepath)
    if hasattr(bpy.ops.wm, "stl_export"):
        bpy.ops.wm.stl_export(filepath=filepath, export_selected_objects=True)
    else:
        bpy.ops.export_mesh.stl(filepath=filepath, use_selection=True)


def export_stl(obj, filepath):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    export_stl_selected(filepath)


# ----------------------------
# Parse parameters from JSON
# ----------------------------
def load_params_from_json(params_path):
    """Load parameters from JSON config file and convert to our format"""
    with open(params_path, 'r') as f:
        config = json.load(f)
    
    # Convert from our config format to the script's P format
    remote = config.get("remote", {})
    buttons = config.get("buttons", {})
    electronics = config.get("electronics", {})
    battery = config.get("battery", {})
    
    P = {
        # overall remote
        "remote": {
            "L": float(remote.get("length_mm", 200)),      # length (Y)
            "W": float(remote.get("width_mm", 50)),        # width  (X)
            "H": float(remote.get("thickness_mm", 20)),    # height (Z)
            "wall": float(remote.get("wall_mm", 2.0)),     # shell thickness
            "corner_r": float(remote.get("corner_radius_mm", 8.0)), # outer corner rounding radius
            "rim_h": 2.0,    # rim height that the back cover sits into
            "fit_clear": 0.30,  # cover clearance in rim (FDM: 0.25-0.4)
        },

        # components
        "battery": {
            "count": int(battery.get("count", 2)),
            "aaa_len": 44.5,
            "aaa_diam": 10.5,
            "clear": float(battery.get("clearance_mm", 0.6)),
            "spacing_x": float(battery.get("placement", {}).get("x_spacing_mm", 3.0)),
            "y_from_back": float(battery.get("placement", {}).get("margin_from_bottom_end_mm", 18.0)),  # distance from back end to start-ish
        },

        "chip": {
            "len": float(electronics.get("atmega328p", {}).get("length_mm", 35.0)),
            "wid": float(electronics.get("atmega328p", {}).get("width_mm", 15.2)),
            "h": float(electronics.get("atmega328p", {}).get("height_mm", 5.0)),
            "clear": float(electronics.get("atmega328p", {}).get("clearance_mm", 0.6)),
            "y_from_front": float(electronics.get("atmega328p", {}).get("placement", {}).get("margin_from_top_end_mm", 65.0)), # chip center offset from front end
        },

        "buttons": {
            "count": int(buttons.get("rows", 2)) * int(buttons.get("cols", 2)),
            "rows": int(buttons.get("rows", 2)),
            "cols": int(buttons.get("cols", 2)),
            "btn_body": 12.0,     # 12x12
            "btn_h": 7.3,
            "clear": 0.35,
            "pitch_x": float(buttons.get("spacing_mm", 10)) + 12.0,  # spacing + button size
            "pitch_y": float(buttons.get("spacing_mm", 10)) + 12.0,
            "block_y_from_front": float(buttons.get("margin_top_mm", 40)) + 20.0,  # grid center from front end
        },

        "led": {
            "diam": float(electronics.get("led", {}).get("diameter_mm", 5.0)),
            "clear": 0.3,
            "y_from_front": float(electronics.get("led", {}).get("placement", {}).get("margin_from_top_end_mm", 10.0)),  # near front tip
        },

        # wiring grooves on back cover INSIDE face
        "traces": {
            "w": 1.2,
            "d": 2.0,
            "pair_gap": 1.6,   # spacing between the two grooves in a pair
            "safe_inset": 6.0, # keep grooves away from rim edge
        },

        # back cover and battery door
        "cover": {
            "thickness": 3.0,  # must be > trace depth
            "door_thickness": 2.2,
            "door_clear": 0.35,
            "rail_h": 1.2,
            "rail_t": 1.2,
            "door_overlap": 3.0,  # how much door overlaps opening on each long side
        },
    }
    
    return P


# ----------------------------
# Layout helpers
# ----------------------------
def y_front(L): return +L / 2.0
def y_back(L):  return -L / 2.0


def button_centers(P):
    r = P["remote"]
    b = P["buttons"]
    L = r["L"]
    rows, cols = b["rows"], b["cols"]
    cx = 0.0
    cy = y_front(L) - b["block_y_from_front"]
    # grid extents
    w = (cols - 1) * b["pitch_x"]
    h = (rows - 1) * b["pitch_y"]
    x0 = cx - w / 2.0
    y0 = cy + h / 2.0
    pts = []
    for rr in range(rows):
        for cc in range(cols):
            pts.append((x0 + cc * b["pitch_x"], y0 - rr * b["pitch_y"]))
    return pts[: b["count"]]


def chip_center(P):
    r = P["remote"]
    c = P["chip"]
    return (0.0, y_front(r["L"]) - c["y_from_front"])


def led_center(P):
    r = P["remote"]
    l = P["led"]
    return (0.0, y_front(r["L"]) - l["y_from_front"])


def battery_centers(P):
    r = P["remote"]
    bat = P["battery"]
    L = r["L"]
    clear = bat["clear"]
    rad = (bat["aaa_diam"] / 2.0) + clear
    pack_y = y_back(L) + bat["y_from_back"] + (bat["aaa_len"] / 2.0)
    if bat["count"] == 1:
        return [(0.0, pack_y)], rad
    xoff = rad + bat["spacing_x"] / 2.0
    return [(-xoff, pack_y), (+xoff, pack_y)], rad


# ----------------------------
# Build: Remote body (shell + holes)
# ----------------------------
def build_body(P):
    r = P["remote"]
    L, W, H = r["L"], r["W"], r["H"]
    wall = r["wall"]
    rim_h = r["rim_h"]
    corner = r["corner_r"]

    # outer shell
    body = rounded_box(W, L, H, radius=corner, segments=12, loc=(0, 0, 0), name="REMOTE_BODY")

    # inner cavity cutter (leaves 2mm top+side walls and a bottom rim to seat the cover)
    inner_W = max(1.0, W - 2 * wall)
    inner_L = max(1.0, L - 2 * wall)
    inner_H = max(1.0, H - wall - rim_h)  # no full bottom wall; just rim
    # Position so top thickness is 'wall' and bottom has rim_h
    top_z = +H / 2.0
    inner_top_z = top_z - wall
    inner_bottom_z = -H / 2.0 + rim_h
    inner_center_z = (inner_top_z + inner_bottom_z) / 2.0

    inner = rounded_box(
        inner_W, inner_L, inner_H,
        radius=max(0.001, corner - wall),
        segments=10,
        loc=(0, 0, inner_center_z),
        name="CAVITY_CUT"
    )

    # Cut cavity
    boolean_diff(body, inner, name="CAVITY")
    apply_modifiers(body)
    bpy.data.objects.remove(inner, do_unlink=True)
    cleanup_mesh(body)

    # Button openings (square openings through top wall)
    b = P["buttons"]
    hole = b["btn_body"] + b["clear"]  # square opening ~12.35-12.5mm
    top_cut_depth = wall + 1.0
    z_cut_center = +H/2.0 - top_cut_depth/2.0

    cutters = []
    for i, (x, y) in enumerate(button_centers(P)):
        c = add_cube(hole, hole, top_cut_depth, loc=(x, y, z_cut_center), name=f"BTN_HOLE_{i+1}")
        cutters.append(c)

    # LED opening (cyl through top wall)
    led = P["led"]
    lx, ly = led_center(P)
    led_r = (led["diam"] / 2.0) + led["clear"]
    led_c = add_cyl(
        radius=led_r,
        depth=wall + 1.0,
        loc=(lx, ly, +H/2.0 - (wall+1.0)/2.0),
        rot=(0, 0, 0),
        name="LED_HOLE"
    )
    cutters.append(led_c)

    # Join cutters and boolean once
    if cutters:
        cutter_union = join_objects(cutters, name="TOP_OPENINGS_UNION")
        boolean_diff(body, cutter_union, name="TOP_OPENINGS")
        apply_modifiers(body)
        bpy.data.objects.remove(cutter_union, do_unlink=True)
        cleanup_mesh(body)

    # Slight outer bevel polish (after booleans)
    polish = body.modifiers.new(name="PolishBevel", type="BEVEL")
    polish.width = 0.8
    polish.segments = 6
    polish.profile = 0.7
    if hasattr(polish, "limit_method"):
        polish.limit_method = "ANGLE"
        polish.angle_limit = math.radians(70)
    apply_modifiers(body)
    cleanup_mesh(body)

    return body


# ----------------------------
# Build: Back cover + battery door window + rails
# ----------------------------
def build_back_cover(P):
    r = P["remote"]
    cov = P["cover"]
    wall = r["wall"]
    rim_h = r["rim_h"]
    fit = r["fit_clear"]

    L, W, H = r["L"], r["W"], r["H"]

    # Cover size fits inside the body opening (inner footprint)
    inner_W = W - 2 * wall
    inner_L = L - 2 * wall
    cover_W = inner_W - 2 * fit
    cover_L = inner_L - 2 * fit
    t = cov["thickness"]

    # Base cover plate (outside face is at z = -H/2)
    z0 = -H / 2.0 + t / 2.0
    cover = add_cube(cover_W, cover_L, t, loc=(0, 0, z0), name="BACK_COVER")

    # Add a small insertion flange (goes up into rim)
    flange_h = max(0.2, rim_h - 0.3)
    flange_t = 1.2  # how "wide" the flange ring is
    flange_W = cover_W - 2 * flange_t
    flange_L = cover_L - 2 * flange_t
    flange_z = -H/2.0 + t + flange_h/2.0  # sits on top of cover plate
    flange = add_cube(flange_W, flange_L, flange_h, loc=(0, 0, flange_z), name="COVER_FLANGE")
    boolean_union(cover, flange, name="FLANGE_UNION")
    apply_modifiers(cover)
    bpy.data.objects.remove(flange, do_unlink=True)
    cleanup_mesh(cover)

    return cover


def cut_battery_window_and_make_door(cover, P):
    r = P["remote"]
    bat = P["battery"]
    cov = P["cover"]

    L, W, H = r["L"], r["W"], r["H"]
    wall = r["wall"]

    (bats, rad) = battery_centers(P)
    # battery pack extents
    pack_y = bats[0][1]
    pack_len = bat["aaa_len"] + 2.0  # extra handling clearance
    pack_w = (2 * rad) * bat["count"] + (bat["spacing_x"] if bat["count"] == 2 else 0.0) + 2.0

    # window opening on cover (outer face)
    door_clear = cov["door_clear"]
    opening_W = pack_w + 2 * door_clear
    opening_L = pack_len + 2 * door_clear
    opening_t = cov["thickness"] + 2.0
    z_center = -H/2.0 + cov["thickness"]/2.0  # cut through cover thickness

    win = add_cube(opening_W, opening_L, opening_t, loc=(0.0, pack_y, z_center), name="BAT_WINDOW_CUT")
    boolean_diff(cover, win, name="BAT_WINDOW")
    apply_modifiers(cover)
    bpy.data.objects.remove(win, do_unlink=True)
    cleanup_mesh(cover)

    # rails on outside face (to guide door)
    rail_h = cov["rail_h"]
    rail_t = cov["rail_t"]
    overlap = cov["door_overlap"]

    # rails run along the long edges of the opening
    rail_len = opening_L + 2 * overlap
    rail_x = opening_W/2.0 + rail_t/2.0 + 0.2
    rail_z = -H/2.0 + rail_h/2.0  # sit on outside face

    rail1 = add_cube(rail_t, rail_len, rail_h, loc=(+rail_x, pack_y, rail_z), name="RAIL_R")
    rail2 = add_cube(rail_t, rail_len, rail_h, loc=(-rail_x, pack_y, rail_z), name="RAIL_L")
    boolean_union(cover, rail1, name="RAIL1")
    boolean_union(cover, rail2, name="RAIL2")
    apply_modifiers(cover)
    bpy.data.objects.remove(rail1, do_unlink=True)
    bpy.data.objects.remove(rail2, do_unlink=True)
    cleanup_mesh(cover)

    # door plate (separate part)
    door_t = cov["door_thickness"]
    # door slides under rails and covers opening with overlap on long axis
    door_W = opening_W + 2 * (cov["door_overlap"])
    door_L = opening_L + 2 * (cov["door_overlap"])
    # place door slightly below cover (for printing on plate)
    door = add_cube(door_W, door_L, door_t, loc=(0.0, pack_y, (-H/2.0) - (door_t/2.0) - 4.0), name="BATTERY_DOOR")

    # add a finger notch to the door (simple half cylinder cut)
    notch_r = 5.0
    notch = add_cyl(radius=notch_r, depth=door_t + 2.0,
                    loc=(0.0, pack_y + door_L/2.0 - 4.0, door.location.z),
                    rot=(math.radians(90), 0, 0),
                    name="DOOR_NOTCH_CUT")
    boolean_diff(door, notch, name="NOTCH")
    apply_modifiers(door)
    bpy.data.objects.remove(notch, do_unlink=True)
    cleanup_mesh(door)

    return door


# ----------------------------
# Wiring grooves on cover inside face
# ----------------------------
def add_groove_segment(x1, y1, x2, y2, z_top, width, depth, name="SEG"):
    """
    Creates a rectangular prism cutter for a groove segment along X or Y (Manhattan).
    z_top is the inside-face z (top surface) of the cover; cutter goes downward by 'depth'.
    """
    eps = 0.001
    if abs(x2 - x1) < eps and abs(y2 - y1) < eps:
        return None

    zc = z_top - depth/2.0
    if abs(x2 - x1) >= abs(y2 - y1):
        # X segment
        length = abs(x2 - x1)
        cx = (x1 + x2)/2.0
        cy = y1
        seg = add_cube(length, width, depth, loc=(cx, cy, zc), name=name)
    else:
        # Y segment
        length = abs(y2 - y1)
        cx = x1
        cy = (y1 + y2)/2.0
        seg = add_cube(width, length, depth, loc=(cx, cy, zc), name=name)
    return seg


def manhattan_path(a, b, mid_x=None, mid_y=None):
    """
    Returns polyline points (x,y) with 2 or 3 legs.
    """
    (x1,y1) = a
    (x2,y2) = b
    if mid_x is not None:
        return [(x1,y1), (mid_x,y1), (mid_x,y2), (x2,y2)]
    if mid_y is not None:
        return [(x1,y1), (x1,mid_y), (x2,mid_y), (x2,y2)]
    # default: single corner
    return [(x1,y1), (x1,y2), (x2,y2)]


def cut_wiring_grooves(cover, P):
    r = P["remote"]
    tr = P["traces"]
    cov = P["cover"]
    L, W, H = r["L"], r["W"], r["H"]

    width = tr["w"]
    depth = tr["d"]
    gap = tr["pair_gap"]

    # inside face z (top surface of cover plate part)
    z_top = (-H/2.0) + cov["thickness"]  # approx inside face
    # keep away from rim edge:
    inset = tr["safe_inset"]

    # anchor points (on the cover inside face)
    chip_x, chip_y = chip_center(P)
    led_x, led_y = led_center(P)
    btns = button_centers(P)
    bats, _ = battery_centers(P)
    # represent battery as two pads near battery region
    bat_y = bats[0][1]
    bat_pad1 = (-6.0, bat_y)
    bat_pad2 = (+6.0, bat_y)

    # chip "pin line" where traces terminate (spread to avoid stacking)
    # 16 traces total => spread endpoints across X
    end_xs = []
    n = 16
    span = 20.0
    for i in range(n):
        # map i to [-span/2 .. +span/2]
        if n == 1:
            end_xs.append(0.0)
        else:
            end_xs.append(-span/2 + (span * i/(n-1)))

    # build trace list as (start, end) pairs, each pair is duplicated with +/- gap/2
    trace_pairs = []

    # battery -> chip (2 traces)
    trace_pairs.append((bat_pad1, (chip_x + end_xs[0], chip_y)))
    trace_pairs.append((bat_pad2, (chip_x + end_xs[1], chip_y)))

    # buttons -> chip (2 each)
    idx = 2
    for (bx, by) in btns:
        # two traces per button end at different chip offsets
        trace_pairs.append(((bx - 1.0, by), (chip_x + end_xs[idx], chip_y))); idx += 1
        trace_pairs.append(((bx + 1.0, by), (chip_x + end_xs[idx], chip_y))); idx += 1

    # led -> chip (2 traces)
    trace_pairs.append(((led_x - 1.0, led_y), (chip_x + end_xs[idx], chip_y))); idx += 1
    trace_pairs.append(((led_x + 1.0, led_y), (chip_x + end_xs[idx], chip_y))); idx += 1

    cutters = []
    seg_id = 0

    # routing strategy:
    # - run everything toward a central "trunk" x=0, but keep it simple Manhattan
    trunk_x = 0.0
    for (s, e) in trace_pairs:
        # make a *pair* of grooves for each "edge pair" by offsetting in X
        for side in (-1, +1):
            sx, sy = s
            ex, ey = e
            sx2 = sx + side * gap/2.0
            ex2 = ex + side * gap/2.0

            # choose mid_x as trunk unless start/end already near trunk
            mid_x = trunk_x
            pts = manhattan_path((sx2, sy), (ex2, ey), mid_x=mid_x)

            # build segments
            for i in range(len(pts)-1):
                (x1,y1) = pts[i]
                (x2,y2) = pts[i+1]
                seg = add_groove_segment(x1,y1,x2,y2, z_top=z_top, width=width, depth=depth, name=f"GSEG_{seg_id}")
                if seg:
                    cutters.append(seg)
                    seg_id += 1

    if not cutters:
        return

    # Union all cutters then boolean once (much cleaner than 200 tiny booleans)
    cut_union = join_objects(cutters, name="GROOVE_CUTTERS")
    boolean_diff(cover, cut_union, name="GROOVES")
    apply_modifiers(cover)
    bpy.data.objects.remove(cut_union, do_unlink=True)
    cleanup_mesh(cover)


def cut_chip_cavity_in_cover(cover, P):
    """Cut ATmega328P-PU DIP chip cavity and pin holes in back cover for press-fit mounting"""
    r = P["remote"]
    c = P["chip"]
    cov = P["cover"]
    H = r["H"]
    
    chip_x, chip_y = chip_center(P)
    
    # Chip body cavity (press-fit socket)
    chip_w = c["wid"] + c["clear"]
    chip_l = c["len"] + c["clear"]
    chip_depth = 2.5  # Deep enough for chip to sit flush
    
    z_top = (-H/2.0) + cov["thickness"]
    chip_z = z_top - chip_depth/2.0
    
    chip_cavity = add_cube(chip_w, chip_l, chip_depth, 
                          loc=(chip_x, chip_y, chip_z), 
                          name="CHIP_CAVITY")
    boolean_diff(cover, chip_cavity, name="CHIP_SOCKET")
    apply_modifiers(cover)
    bpy.data.objects.remove(chip_cavity, do_unlink=True)
    cleanup_mesh(cover)
    
    # Pin holes for DIP-28 package (14 pins per side)
    pin_spacing = 2.54  # Standard DIP spacing
    pin_hole_diam = 1.0  # Pin hole diameter
    pin_hole_depth = cov["thickness"] + 1.0  # Through entire cover
    
    pin_cutters = []
    
    # Left side pins (1-14)
    for pin in range(14):
        pin_x = chip_x - chip_l/2.0 - 1.27  # Offset from chip center
        pin_y = chip_y + (13*pin_spacing/2.0) - (pin * pin_spacing)
        pin_z = z_top - pin_hole_depth/2.0
        
        pin_hole = add_cyl(radius=pin_hole_diam/2.0, depth=pin_hole_depth,
                          loc=(pin_x, pin_y, pin_z), 
                          name=f"PIN_L_{pin+1}")
        pin_cutters.append(pin_hole)
    
    # Right side pins (15-28)
    for pin in range(14):
        pin_x = chip_x + chip_l/2.0 + 1.27  # Offset from chip center
        pin_y = chip_y - (13*pin_spacing/2.0) + (pin * pin_spacing)
        pin_z = z_top - pin_hole_depth/2.0
        
        pin_hole = add_cyl(radius=pin_hole_diam/2.0, depth=pin_hole_depth,
                          loc=(pin_x, pin_y, pin_z),
                          name=f"PIN_R_{pin+15}")
        pin_cutters.append(pin_hole)
    
    if pin_cutters:
        pin_union = join_objects(pin_cutters, name="PIN_HOLES_UNION")
        boolean_diff(cover, pin_union, name="PIN_HOLES")
        apply_modifiers(cover)
        bpy.data.objects.remove(pin_union, do_unlink=True)
        cleanup_mesh(cover)


def cut_button_cavities_in_cover(cover, P):
    """Cut tactile button press-fit cavities in back cover"""
    r = P["remote"]
    b = P["buttons"]
    cov = P["cover"]
    H = r["H"]
    
    # Tactile button cavity (12x12x7.3mm)
    btn_size = b["btn_body"] + 0.4  # 12.4mm for press-fit clearance
    btn_depth = 3.5  # Depth for button body
    
    z_top = (-H/2.0) + cov["thickness"]
    btn_z = z_top - btn_depth/2.0
    
    btn_cutters = []
    for i, (bx, by) in enumerate(button_centers(P)):
        btn_cavity = add_cube(btn_size, btn_size, btn_depth,
                             loc=(bx, by, btn_z),
                             name=f"BTN_CAVITY_{i+1}")
        btn_cutters.append(btn_cavity)
    
    if btn_cutters:
        btn_union = join_objects(btn_cutters, name="BUTTON_CAVITIES_UNION")
        boolean_diff(cover, btn_union, name="BUTTON_CAVITIES")
        apply_modifiers(cover)
        bpy.data.objects.remove(btn_union, do_unlink=True)
        cleanup_mesh(cover)


def cut_led_cavity_in_cover(cover, P):
    """Cut LED press-fit cavity in back cover"""
    r = P["remote"]
    l = P["led"]
    cov = P["cover"]
    H = r["H"]
    
    led_x, led_y = led_center(P)
    
    # LED cavity (5mm LED)
    led_diam = l["diam"] + l["clear"]
    led_depth = 4.0  # Depth for LED body
    
    z_top = (-H/2.0) + cov["thickness"]
    led_z = z_top - led_depth/2.0
    
    led_cavity = add_cyl(radius=led_diam/2.0, depth=led_depth,
                        loc=(led_x, led_y, led_z),
                        name="LED_CAVITY")
    boolean_diff(cover, led_cavity, name="LED_SOCKET")
    apply_modifiers(cover)
    bpy.data.objects.remove(led_cavity, do_unlink=True)
    cleanup_mesh(cover)


# ----------------------------
# Print-plate arrangement + main
# ----------------------------
def arrange_for_one_print(body, cover, door):
    """
    Duplicate parts and place them side-by-side for one STL.
    """
    def dup(obj, name):
        o = obj.copy()
        o.data = obj.data.copy()
        o.name = name
        bpy.context.collection.objects.link(o)
        return o

    b2 = dup(body, "PRINT_BODY")
    c2 = dup(cover, "PRINT_COVER")
    d2 = dup(door, "PRINT_DOOR")

    # Move them so they don't overlap
    shift = 70.0  # spacing
    b2.location = (0, 0, 0)
    c2.location = (shift, 0, 0)
    d2.location = (2*shift, 0, 0)

    bpy.ops.object.select_all(action="DESELECT")
    b2.select_set(True)
    c2.select_set(True)
    d2.select_set(True)
    bpy.context.view_layer.objects.active = b2
    return [b2, c2, d2]


def parse_args():
    """Parse command line arguments for params file and output directory"""
    argv = sys.argv
    if "--" in argv:
        extra = argv[argv.index("--")+1:]
        if len(extra) >= 2:
            return extra[0], extra[1]  # params_file, output_dir
        elif len(extra) >= 1:
            # If only one arg, treat as params file, use default output
            script_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
            return extra[0], os.path.join(script_dir, "out")
    
    # Fallback defaults
    script_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    params_file = os.path.join(script_dir, "..", "..", "configs", "default_params.json")
    out_dir = os.path.join(script_dir, "..", "..", "outputs", "run1")
    return params_file, out_dir


def main():
    params_file, out_dir = parse_args()
    os.makedirs(out_dir, exist_ok=True)
    
    # Load parameters from JSON
    P = load_params_from_json(params_file)

    clear_scene()
    set_mm_units()

    body = build_body(P)
    cover = build_back_cover(P)
    door = cut_battery_window_and_make_door(cover, P)
    
    # Create PCB functionality in back cover
    cut_chip_cavity_in_cover(cover, P)
    cut_button_cavities_in_cover(cover, P)
    cut_led_cavity_in_cover(cover, P)
    cut_wiring_grooves(cover, P)

    # Exports
    export_stl(body,  os.path.join(out_dir, "remote_body.stl"))
    export_stl(cover, os.path.join(out_dir, "back_cover.stl"))
    export_stl(door,  os.path.join(out_dir, "battery_door.stl"))

    # Combined "one go" STL (all parts arranged next to each other)
    print_objs = arrange_for_one_print(body, cover, door)
    export_stl_selected(os.path.join(out_dir, "print_one_go_all_parts.stl"))

    # Save .blend
    try:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, "generated_remote.blend"))
    except Exception:
        pass

    print(f"Done. Files in: {out_dir}")


if __name__ == "__main__":
    main()