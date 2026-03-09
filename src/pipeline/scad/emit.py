"""emit.py — assemble shell-body lines and ScadFragments into a .scad file.

The final SCAD structure::

    difference() {
        union() {
            // shell body
            // addition fragments
        }
        // cutout fragments (merged by z-layer)
    }
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from shapely.geometry import MultiPolygon as _SMultiPoly
from shapely.geometry import Polygon as _SPoly
from shapely.ops import unary_union

from .fragment import (
    ScadFragment, RectGeometry, CylinderGeometry, PolygonGeometry,
    SegmentGeometry,
)

log = logging.getLogger(__name__)


# ── Geometry → polygon conversion ─────────────────────────────────


def _fragment_to_polygon(frag: ScadFragment) -> list[list[float]] | None:
    """Convert a fragment's geometry to a polygon point list, or None for cylinders."""
    g = frag.geometry
    if isinstance(g, CylinderGeometry):
        return None
    if isinstance(g, RectGeometry):
        return g.to_polygon()
    if isinstance(g, SegmentGeometry):
        return g.to_polygon()
    if isinstance(g, PolygonGeometry):
        return g.points
    return None


# ── Shapely merge (same z-layer optimization) ─────────────────────


def _merge_polygon_fragments(
    fragments: list[ScadFragment],
    outline_pts: list[list[float]] | None,
) -> list[tuple[str, list[str]]]:
    """Group polygon fragments by (z_base, depth), merge, clip, simplify."""
    outline_poly: _SPoly | None = None
    if outline_pts and len(outline_pts) >= 3:
        try:
            p = _SPoly(outline_pts)
            outline_poly = p if p.is_valid else p.buffer(0)
        except Exception:
            pass

    groups: dict[tuple[float, float], list[ScadFragment]] = defaultdict(list)
    for f in fragments:
        key = (round(f.z_base, 3), round(f.depth, 3))
        groups[key].append(f)

    results: list[tuple[str, list[str]]] = []

    for (z_base, depth), members in sorted(groups.items()):
        cats = sorted({m.label.split("\u2014")[0].split("—")[0].strip()
                       for m in members if m.label})
        label_str = ", ".join(cats[:4])
        if len(cats) > 4:
            label_str += f" (+{len(cats) - 4} more)"

        shapely_polys: list[_SPoly] = []
        for m in members:
            pts = _fragment_to_polygon(m)
            if pts is None or len(pts) < 3:
                continue
            try:
                sp = _SPoly(pts)
                if not sp.is_valid:
                    sp = sp.buffer(0)
                if sp.is_valid and not sp.is_empty:
                    shapely_polys.append(sp)
            except Exception:
                pass

        if not shapely_polys:
            continue

        merged = unary_union(shapely_polys)

        if outline_poly is not None and not merged.is_empty:
            try:
                clip = outline_poly.buffer(0.01, join_style="mitre", mitre_limit=5.0)
                merged = merged.intersection(clip)
            except Exception:
                pass

        if merged.is_empty:
            continue

        try:
            merged = merged.simplify(0.05, preserve_topology=True)
        except Exception:
            pass

        if not merged.is_valid:
            merged = merged.buffer(0)

        if isinstance(merged, _SPoly):
            geoms: list[_SPoly] = [merged]
        elif isinstance(merged, _SMultiPoly):
            geoms = list(merged.geoms)
        else:
            try:
                geoms = [g for g in merged.geoms if isinstance(g, _SPoly)]
            except Exception:
                continue

        if not geoms:
            continue

        # Round coordinates to output precision and re-validate.
        # Shapely may consider a polygon valid at float64 precision while
        # the 3dp-rounded version self-intersects.
        def _round_poly(p: _SPoly) -> _SPoly | None:
            """Round coords to 3dp and repair until valid or give up."""
            for _attempt in range(4):
                ext = [(round(x, 3), round(y, 3)) for x, y in p.exterior.coords]
                holes = [
                    [(round(x, 3), round(y, 3)) for x, y in h.coords]
                    for h in p.interiors
                ]
                try:
                    p = _SPoly(ext, holes)
                except Exception:
                    return None
                if p.is_valid:
                    return p
                repaired = p.buffer(0)
                if repaired.is_empty:
                    return None
                if isinstance(repaired, _SMultiPoly):
                    return repaired
                p = repaired
            return None

        rounded_geoms: list[_SPoly] = []
        for poly in geoms:
            result = _round_poly(poly)
            if result is None:
                continue
            if isinstance(result, _SMultiPoly):
                for part in result.geoms:
                    rp = _round_poly(part)
                    if rp is None or isinstance(rp, _SMultiPoly):
                        continue
                    if not rp.is_empty:
                        rounded_geoms.append(rp)
            elif not result.is_empty:
                rounded_geoms.append(result)

        if not rounded_geoms:
            continue

        all_pts: list[tuple[float, float]] = []
        paths: list[list[int]] = []
        for poly in rounded_geoms:
            ext = list(poly.exterior.coords)[:-1]
            start = len(all_pts)
            all_pts.extend(ext)
            paths.append(list(range(start, start + len(ext))))
            for hole in poly.interiors:
                hc = list(hole.coords)[:-1]
                h_start = len(all_pts)
                all_pts.extend(hc)
                paths.append(list(range(h_start, h_start + len(hc))))

        pts_str = ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in all_pts)
        paths_str = ", ".join(
            "[" + ", ".join(str(i) for i in p) + "]" for p in paths
        )

        comment = (
            f"  // z={z_base:.2f} d={depth:.2f}  "
            f"{len(members)} fragments \u2192 {len(geoms)} polygon(s)  "
            f"{len(all_pts)} verts  [{label_str}]"
        )
        _EPS = 0.001
        scad_lines = [
            f"  translate([0, 0, {z_base - _EPS:.3f}])",
            f"    linear_extrude(height = {depth + 2 * _EPS:.3f})",
            f"      polygon(points = [{pts_str}], paths = [{paths_str}]);",
        ]
        results.append((comment, scad_lines))

    return results


# ── Helpers ────────────────────────────────────────────────────────


def _indent(lines: list[str], prefix: str) -> list[str]:
    return [prefix + line for line in lines]


# ── Public API ─────────────────────────────────────────────────────


def generate_scad(
    shell_body_lines: list[str],
    fragments: list[ScadFragment],
    session_id: str = "",
    metadata: dict | None = None,
    outline_pts: list[list[float]] | None = None,
) -> str:
    """Return a complete OpenSCAD source string.

    Parameters
    ----------
    shell_body_lines : list[str]
        Lines produced by ``layers.shell_body_lines()``.
    fragments : list[ScadFragment]
        All geometry contributions (cutouts + additions).
    session_id : str
        Written into the header comment.
    metadata : dict, optional
        Extra key-value pairs for the header comment.
    outline_pts : list of [x, y], optional
        The 2-D enclosure footprint polygon for clipping.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    meta_str = ""
    if metadata:
        meta_str = "".join(f"//   {k}: {v}\n" for k, v in metadata.items())

    header = (
        "// ============================================================\n"
        "// manufacturerAI -- auto-generated enclosure\n"
        f"// Session  : {session_id}\n"
        f"// Generated: {now}\n"
        + meta_str
        + "// ============================================================\n"
        "\n"
        "$fn = 16;\n"
        "\n"
    )

    cutouts = [f for f in fragments if f.type == "cutout"]
    additions = [f for f in fragments if f.type == "addition"]

    # No cutouts and no additions — just emit the shell body
    if not cutouts and not additions:
        return header + "\n".join(shell_body_lines) + "\n"

    # Split cutouts into cylinders vs. polygons
    cylinder_cuts: list[ScadFragment] = []
    polygon_cuts: list[ScadFragment] = []
    for f in cutouts:
        if isinstance(f.geometry, CylinderGeometry):
            cylinder_cuts.append(f)
        else:
            polygon_cuts.append(f)

    merged_groups = _merge_polygon_fragments(polygon_cuts, outline_pts)

    log.info(
        "Cutout merging: %d polygon → %d groups  |  %d cylinder  |  %d additions",
        len(polygon_cuts), len(merged_groups), len(cylinder_cuts), len(additions),
    )

    out_lines: list[str] = []

    has_cutouts = bool(cutouts)
    has_additions = bool(additions)

    if has_cutouts:
        out_lines.append("difference() {")
        out_lines.append("")

    # Shell body + additions wrapped in union()
    if has_additions:
        out_lines.append("  union() {")
        out_lines.append("    // --- Shell body ---")
        out_lines.append("    render(convexity = 10)")
        out_lines += _indent(shell_body_lines, "    ")
        out_lines.append("")
        out_lines.append("    // --- Additions ---")
        for a in additions:
            out_lines.append(f"    // {a.label}")
            out_lines += _indent(_fragment_scad_lines(a), "    ")
        out_lines.append("  }")
    else:
        prefix = "  " if has_cutouts else ""
        if has_cutouts:
            out_lines.append("  // --- Shell body ---")
            out_lines.append("  render(convexity = 10)")
        out_lines += _indent(shell_body_lines, prefix)

    if has_cutouts:
        out_lines.append("")
        out_lines.append("    // --- Polygon cutouts (merged by z-layer) ---")

        for comment, scad_lines in merged_groups:
            out_lines.append("")
            out_lines.append("    " + comment.lstrip())
            out_lines += _indent(scad_lines, "    ")

        # Cylinder cutouts
        if cylinder_cuts:
            out_lines.append("")
            out_lines.append(f"    // --- Cylindrical holes ({len(cylinder_cuts)}) ---")
            _EPS = 0.001
            for c in cylinder_cuts:
                cg = c.geometry
                assert isinstance(cg, CylinderGeometry)
                if c.label:
                    out_lines.append(f"    // {c.label}")
                out_lines += [
                    f"    translate([{cg.cx:.3f}, {cg.cy:.3f}, {c.z_base - _EPS:.3f}])",
                    f"      cylinder(h = {c.depth + 2 * _EPS:.3f}, r = {cg.r:.3f});",
                ]

        out_lines += ["", "}"]     # close difference()

    return header + "\n".join(out_lines) + "\n"


def _fragment_scad_lines(frag: ScadFragment) -> list[str]:
    """Convert a single fragment to OpenSCAD lines (for additions or standalone use)."""
    g = frag.geometry
    if isinstance(g, CylinderGeometry):
        return [
            f"translate([{g.cx:.3f}, {g.cy:.3f}, {frag.z_base:.3f}])",
            f"  cylinder(h = {frag.depth:.3f}, r = {g.r:.3f});",
        ]
    pts = _fragment_to_polygon(frag)
    if pts and len(pts) >= 3:
        pts_str = ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in pts)
        return [
            f"translate([0, 0, {frag.z_base:.3f}])",
            f"  linear_extrude(height = {frag.depth:.3f})",
            f"    polygon(points = [{pts_str}]);",
        ]
    return [f"// empty fragment: {frag.label}"]
