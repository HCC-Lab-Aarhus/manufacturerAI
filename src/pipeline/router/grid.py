"""Discretized routing grid — marks cells as free, blocked, or high-cost.

The grid covers the bounding box of the outline polygon.  Cells outside
the polygon (plus edge clearance) are permanently blocked.  Component
bodies with blocks_routing=True get permanent blocks.  Routed traces
get temporary blocks that can be cleared for rip-up.
"""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import Polygon, Point
from shapely.prepared import prep
from shapely import contains_xy

from .models import GRID_RESOLUTION_MM, EDGE_CLEARANCE_MM, TRACE_CLEARANCE_MM, TRACE_WIDTH_MM


# Cell states
FREE = 0
BLOCKED = 1
PERMANENTLY_BLOCKED = 2
TRACE_PATH = 3     # Occupied by an actual trace (not just clearance)


class RoutingGrid:
    """A 2-D grid for Manhattan routing inside a polygonal outline.

    World coordinates (mm) are mapped to grid cells.  The grid origin
    is at (origin_x, origin_y) in world space — the lower-left corner
    of the outline bounding box.
    """

    def __init__(
        self,
        outline_poly: Polygon,
        resolution: float = GRID_RESOLUTION_MM,
        edge_clearance: float = EDGE_CLEARANCE_MM,
        trace_width_mm: float = TRACE_WIDTH_MM,
        trace_clearance_mm: float = TRACE_CLEARANCE_MM,
    ) -> None:
        self.resolution = resolution
        self.edge_clearance = edge_clearance
        self.trace_width_mm = trace_width_mm
        self.trace_clearance_mm = trace_clearance_mm
        self.outline_poly = outline_poly

        # Bounding box of the outline
        xmin, ymin, xmax, ymax = outline_poly.bounds
        self.origin_x = xmin
        self.origin_y = ymin
        self.width = int(math.ceil((xmax - xmin) / resolution)) + 1
        self.height = int(math.ceil((ymax - ymin) / resolution)) + 1

        # Cell state: 0=free, 1=blocked(temp), 2=perm blocked, 3=trace path
        self._cells = bytearray(self.width * self.height)

        # Protected cells: pin pad positions that trace clearance must not block.
        # These are set by the engine after component blocking. Traces can
        # still *pass through* protected cells, but block_trace() will skip
        # them so nearby pads stay reachable.
        self._protected: set[tuple[int, int]] = set()

        # Trace ownership: flat index → net_id that placed the trace
        self._trace_owner: dict[int, str] = {}
        # Clearance ownership: flat index → set of net_ids whose clearance covers this cell
        self._clearance_owner: dict[int, set[str]] = {}

        # Block cells outside polygon or too close to its edges
        inset_poly = outline_poly.buffer(-edge_clearance)
        W = self.width
        H = self.height
        ox, oy = self.origin_x, self.origin_y
        gx_arr = np.arange(W)
        gy_arr = np.arange(H)
        wx_arr = ox + (gx_arr + 0.5) * resolution
        wy_arr = oy + (gy_arr + 0.5) * resolution
        xx, yy = np.meshgrid(wx_arr, wy_arr)
        inside = contains_xy(inset_poly, xx.ravel(), yy.ravel())
        blocked_indices = np.where(~inside)[0]
        for flat in blocked_indices:
            self._cells[flat] = PERMANENTLY_BLOCKED

    # ── Coordinate conversion ──────────────────────────────────────

    def world_to_grid(self, wx: float, wy: float) -> tuple[int, int]:
        """Convert world mm to grid cell (clamped to bounds)."""
        gx = int(round((wx - self.origin_x) / self.resolution - 0.5))
        gy = int(round((wy - self.origin_y) / self.resolution - 0.5))
        gx = max(0, min(self.width - 1, gx))
        gy = max(0, min(self.height - 1, gy))
        return (gx, gy)

    def grid_to_world(self, gx: int, gy: int) -> tuple[float, float]:
        """Convert grid cell to world mm (cell centre)."""
        wx = self.origin_x + (gx + 0.5) * self.resolution
        wy = self.origin_y + (gy + 0.5) * self.resolution
        return (wx, wy)

    # ── Cell queries ───────────────────────────────────────────────

    def in_bounds(self, gx: int, gy: int) -> bool:
        return 0 <= gx < self.width and 0 <= gy < self.height

    def is_free(self, gx: int, gy: int) -> bool:
        if not self.in_bounds(gx, gy):
            return False
        return self._cells[gy * self.width + gx] == FREE

    def is_blocked(self, gx: int, gy: int) -> bool:
        if not self.in_bounds(gx, gy):
            return True
        return self._cells[gy * self.width + gx] != FREE

    def cell_owner_at(self, gx: int, gy: int) -> set[str]:
        """Return all net_ids that own this cell (trace or clearance)."""
        if not self.in_bounds(gx, gy):
            return set()
        flat = gy * self.width + gx
        owners: set[str] = set()
        trace = self._trace_owner.get(flat)
        if trace is not None:
            owners.add(trace)
        clearance = self._clearance_owner.get(flat)
        if clearance is not None:
            owners.update(clearance)
        return owners

    def is_permanently_blocked(self, gx: int, gy: int) -> bool:
        if not self.in_bounds(gx, gy):
            return True
        return self._cells[gy * self.width + gx] == PERMANENTLY_BLOCKED

    def is_protected(self, gx: int, gy: int) -> bool:
        """Return True if the cell is a protected pin-pad position."""
        return (gx, gy) in self._protected

    # ── Cell mutation ──────────────────────────────────────────────

    def block_cell(self, gx: int, gy: int) -> None:
        """Temporarily block a cell (can be freed later)."""
        if self.in_bounds(gx, gy) and self._cells[gy * self.width + gx] == FREE:
            self._cells[gy * self.width + gx] = BLOCKED

    def permanently_block_cell(self, gx: int, gy: int) -> None:
        if self.in_bounds(gx, gy):
            self._cells[gy * self.width + gx] = PERMANENTLY_BLOCKED

    def free_cell(self, gx: int, gy: int) -> None:
        """Free a temporarily-blocked cell.  Permanent blocks are untouched."""
        if self.in_bounds(gx, gy) and self._cells[gy * self.width + gx] == BLOCKED:
            self._cells[gy * self.width + gx] = FREE

    def force_free_cell(self, gx: int, gy: int) -> None:
        """Force a cell to FREE, even if permanently blocked.

        Used to ensure component pin positions are always reachable,
        even when the component body blocks routing.
        """
        if self.in_bounds(gx, gy):
            self._cells[gy * self.width + gx] = FREE

    # ── Area blocking ──────────────────────────────────────────────

    def block_rect_world(
        self,
        cx_mm: float, cy_mm: float,
        half_w_mm: float, half_h_mm: float,
        permanent: bool = False,
    ) -> None:
        """Block all cells whose centres fall inside a world-space rectangle.

        Uses the cell-centre test (consistent with outline polygon
        checking) so that edge cells whose centres lie outside the
        rectangle are not over-blocked.
        """
        left = cx_mm - half_w_mm
        right = cx_mm + half_w_mm
        bottom = cy_mm - half_h_mm
        top = cy_mm + half_h_mm

        res = self.resolution
        ox, oy = self.origin_x, self.origin_y

        gx_min = max(0, int(math.floor((left - ox) / res)))
        gx_max = min(self.width - 1, int(math.ceil((right - ox) / res)))
        gy_min = max(0, int(math.floor((bottom - oy) / res)))
        gy_max = min(self.height - 1, int(math.ceil((top - oy) / res)))

        for gy in range(gy_min, gy_max + 1):
            wy = oy + (gy + 0.5) * res
            if wy < bottom or wy > top:
                continue
            for gx in range(gx_min, gx_max + 1):
                wx = ox + (gx + 0.5) * res
                if wx < left or wx > right:
                    continue
                if permanent:
                    self.permanently_block_cell(gx, gy)
                else:
                    self.block_cell(gx, gy)

    def protect_cell(self, gx: int, gy: int) -> None:
        """Mark a cell as a protected pin pad position.

        Protected cells are not blocked by block_trace(), ensuring
        that pin pads remain reachable even when adjacent traces are
        placed nearby.
        """
        if self.in_bounds(gx, gy):
            self._protected.add((gx, gy))

    def block_raised_floor(self, outline, enclosure) -> int:
        """Permanently block cells where the floor is raised above the trace zone.

        Any cell whose ``blended_bottom_height >= threshold`` is impassable
        because the conductive-ink trace layer (at z = FLOOR_MM) would sit
        inside the raised shell material.

        Parameters
        ----------
        outline, enclosure
            Design-spec objects passed through to ``blended_bottom_height``.

        Returns
        -------
        int
            Number of additionally blocked cells.
        """
        from src.pipeline.design.height_field import blended_bottom_height
        from src.pipeline.config import FLOOR_MM

        # Fast check: skip entirely if no vertex has z_bottom and no bottom surface
        has_raised = False
        for pt in outline.points:
            if pt.z_bottom is not None and pt.z_bottom > 0:
                has_raised = True
                break
        if not has_raised:
            bs = enclosure.bottom_surface
            if bs is None or bs.type == "flat":
                return 0

        threshold = FLOOR_MM - 0.1   # small tolerance
        count = 0
        res = self.resolution

        for gy in range(self.height):
            wy = self.origin_y + (gy + 0.5) * res
            for gx in range(self.width):
                idx = gy * self.width + gx
                if self._cells[idx] == PERMANENTLY_BLOCKED:
                    continue   # already blocked (outside outline)
                wx = self.origin_x + (gx + 0.5) * res
                z = blended_bottom_height(wx, wy, outline, enclosure)
                if z >= threshold:
                    self._cells[idx] = PERMANENTLY_BLOCKED
                    count += 1
        return count

    def block_trace(
        self,
        path: list[tuple[int, int]],
        clearance_cells: int | None = None,
        *,
        net_id: str,
    ) -> None:
        """Block cells along a trace path, including clearance radius.

        Path cells are marked TRACE_PATH and recorded in ``_trace_owner``.
        Clearance-zone cells are marked BLOCKED and recorded in
        ``_clearance_owner`` so that ``free_trace`` can remove only the
        requesting net's contribution without damaging other nets.
        Protected pin-pad cells are skipped for clearance.
        """
        if clearance_cells is None:
            clearance_cells = max(
                1,
                int(math.ceil(
                    (self.trace_width_mm / 2 + self.trace_clearance_mm) / self.resolution
                ))
            )
        if not path:
            return

        W = self.width
        H = self.height
        path_set = set(path)
        path_coords = np.array(list(path_set), dtype=np.int32)
        path_flat = path_coords[:, 1] * W + path_coords[:, 0]

        cells_np = np.frombuffer(self._cells, dtype=np.uint8)

        states = cells_np[path_flat]
        cells_np[path_flat[(states == FREE) | (states == BLOCKED)]] = TRACE_PATH
        for f in path_flat:
            self._trace_owner[int(f)] = net_id

        r = clearance_cells
        d1 = np.arange(-r, r + 1, dtype=np.int32)
        ox, oy = np.meshgrid(d1, d1)
        offsets = np.column_stack([ox.ravel(), oy.ravel()])

        all_nb = (path_coords[:, None, :] + offsets[None, :, :]).reshape(-1, 2)
        mask = (
            (all_nb[:, 0] >= 0) & (all_nb[:, 0] < W)
            & (all_nb[:, 1] >= 0) & (all_nb[:, 1] < H)
        )
        valid_flat = (all_nb[mask, 1] * W + all_nb[mask, 0]).astype(np.int32)
        clearance_flat = np.setdiff1d(valid_flat, path_flat)

        if self._protected:
            prot_arr = np.array(
                [(gx, gy) for gx, gy in self._protected], dtype=np.int32,
            )
            prot_flat = prot_arr[:, 1] * W + prot_arr[:, 0]
            clearance_flat = np.setdiff1d(clearance_flat, prot_flat)

        cl_states = cells_np[clearance_flat]
        cells_np[clearance_flat[cl_states == FREE]] = BLOCKED

        for f in clearance_flat[(cl_states == FREE) | (cl_states == BLOCKED)]:
            self._clearance_owner.setdefault(int(f), set()).add(net_id)

    def free_trace(
        self,
        path: list[tuple[int, int]],
        clearance_cells: int | None = None,
        *,
        net_id: str,
    ) -> None:
        """Free cells belonging to *net_id* along a trace path.

        Path cells (TRACE_PATH) are freed unconditionally.
        Clearance cells (BLOCKED) are freed only when no other net
        still claims them, preventing collateral damage to neighbours.
        Permanently-blocked cells are never touched.
        """
        if clearance_cells is None:
            clearance_cells = max(
                1,
                int(math.ceil(
                    (self.trace_width_mm / 2 + self.trace_clearance_mm) / self.resolution
                ))
            )
        if not path:
            return

        W = self.width
        H = self.height
        path_set = set(path)
        path_coords = np.array(list(path_set), dtype=np.int32)
        path_flat = path_coords[:, 1] * W + path_coords[:, 0]

        cells_np = np.frombuffer(self._cells, dtype=np.uint8)

        trace_mask = cells_np[path_flat] == TRACE_PATH
        cells_np[path_flat[trace_mask]] = FREE
        for f in path_flat:
            self._trace_owner.pop(int(f), None)

        r = clearance_cells
        d1 = np.arange(-r, r + 1, dtype=np.int32)
        ox, oy = np.meshgrid(d1, d1)
        offsets = np.column_stack([ox.ravel(), oy.ravel()])

        all_nb = (path_coords[:, None, :] + offsets[None, :, :]).reshape(-1, 2)
        mask = (
            (all_nb[:, 0] >= 0) & (all_nb[:, 0] < W)
            & (all_nb[:, 1] >= 0) & (all_nb[:, 1] < H)
        )
        valid_flat = (all_nb[mask, 1] * W + all_nb[mask, 0]).astype(np.int32)
        clearance_flat = np.setdiff1d(valid_flat, path_flat)

        cl_states = cells_np[clearance_flat]
        blocked_flat = clearance_flat[cl_states == BLOCKED]

        for f in blocked_flat:
            fi = int(f)
            owners = self._clearance_owner.get(fi)
            if owners is None:
                self._cells[fi] = FREE
                continue
            owners.discard(net_id)
            if not owners:
                del self._clearance_owner[fi]
                self._cells[fi] = FREE


