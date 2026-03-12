"""Discretized routing grid — marks cells as free, blocked, or high-cost.

The grid covers the bounding box of the outline polygon.  Cells outside
the polygon (plus edge clearance) are permanently blocked.  Component
bodies with blocks_routing=True get permanent blocks.  Routed traces
get temporary blocks that can be cleared for rip-up.
"""

from __future__ import annotations

import math

from shapely.geometry import Polygon, Point

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
        for gy in range(self.height):
            wy = self.origin_y + (gy + 0.5) * resolution
            for gx in range(self.width):
                wx = self.origin_x + (gx + 0.5) * resolution
                pt = Point(wx, wy)
                if not inset_poly.contains(pt):
                    self._cells[gy * self.width + gx] = PERMANENTLY_BLOCKED

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
        path_set = set(path)
        protected = self._protected
        W = self.width

        for gx, gy in path_set:
            if self.in_bounds(gx, gy):
                flat = gy * W + gx
                v = self._cells[flat]
                if v == FREE or v == BLOCKED:
                    self._cells[flat] = TRACE_PATH
                self._trace_owner[flat] = net_id

        for gx, gy in path:
            for dy in range(-clearance_cells, clearance_cells + 1):
                for dx in range(-clearance_cells, clearance_cells + 1):
                    nx, ny = gx + dx, gy + dy
                    if (nx, ny) in path_set or (nx, ny) in protected:
                        continue
                    if not self.in_bounds(nx, ny):
                        continue
                    flat = ny * W + nx
                    if self._cells[flat] == FREE:
                        self._cells[flat] = BLOCKED
                    if self._cells[flat] == BLOCKED:
                        self._clearance_owner.setdefault(flat, set()).add(net_id)

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
        W = self.width
        path_set = set(path)

        for gx, gy in path_set:
            if self.in_bounds(gx, gy):
                flat = gy * W + gx
                self._trace_owner.pop(flat, None)
                if self._cells[flat] == TRACE_PATH:
                    self._cells[flat] = FREE

        for gx, gy in path:
            for dy in range(-clearance_cells, clearance_cells + 1):
                for dx in range(-clearance_cells, clearance_cells + 1):
                    nx, ny = gx + dx, gy + dy
                    if (nx, ny) in path_set or not self.in_bounds(nx, ny):
                        continue
                    flat = ny * W + nx
                    if self._cells[flat] != BLOCKED:
                        continue
                    owners = self._clearance_owner.get(flat)
                    if owners is None:
                        self._cells[flat] = FREE
                        continue
                    owners.discard(net_id)
                    if not owners:
                        del self._clearance_owner[flat]
                        self._cells[flat] = FREE


