from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class GridMetrics:
    grid_width_mm: float
    grid_height_mm: float
    usable_width_mm: float
    usable_length_mm: float

def compute_grid_metrics(length_mm: float, width_mm: float,
                         rows: int, cols: int,
                         diam_mm: float, spacing_mm: float,
                         margin_top_mm: float, margin_bottom_mm: float, margin_side_mm: float) -> GridMetrics:
    grid_w = cols * diam_mm + (cols - 1) * spacing_mm
    grid_h = rows * diam_mm + (rows - 1) * spacing_mm
    usable_w = width_mm - 2 * margin_side_mm
    usable_l = length_mm - margin_top_mm - margin_bottom_mm
    return GridMetrics(grid_w, grid_h, usable_w, usable_l)

def choose_rows_cols(button_count: int) -> tuple[int, int]:
    # Factorization heuristic: near-square, then orient long axis along rows
    best = (button_count, 1)
    best_score = float("inf")
    for r in range(1, button_count + 1):
        if button_count % r != 0:
            continue
        c = button_count // r
        score = abs(r - c)
        if score < best_score:
            best_score = score
            best = (r, c)
    r, c = best
    if c > r:
        r, c = c, r
    return (r, c)
