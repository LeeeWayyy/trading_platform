"""Inline SVG sparkline renderer for position P&L history."""

from __future__ import annotations

from collections.abc import Iterable


def _coerce_floats(values: Iterable[float]) -> list[float]:
    return [float(v) for v in values]


def create_sparkline_svg(
    data: Iterable[float],
    *,
    width: int = 80,
    height: int = 20,
    stroke_width: float = 1.5,
) -> str:
    """Create a small inline SVG sparkline.

    Args:
        data: Sequence of numeric values (chronological)
        width: SVG width in pixels
        height: SVG height in pixels
        stroke_width: Polyline stroke width

    Returns:
        SVG markup string. Returns empty string if no data.
    """
    values = _coerce_floats(data)
    if not values:
        return ""

    if len(values) == 1:
        values = [values[0], values[0]]

    min_v = min(values)
    max_v = max(values)
    span = max_v - min_v
    if span == 0:
        span = 1.0

    count = len(values)
    if count == 1:
        step = 0.0
    else:
        step = width / (count - 1)

    points = []
    for idx, value in enumerate(values):
        x = step * idx
        # Invert Y (higher values at top)
        y = height - ((value - min_v) / span) * height
        points.append(f"{x:.2f},{y:.2f}")

    trend_up = values[-1] >= values[0]
    color = "var(--profit)" if trend_up else "var(--loss)"

    return (
        f"<svg class=\"sparkline\" width=\"{width}\" height=\"{height}\" "
        f"viewBox=\"0 0 {width} {height}\" "
        f"xmlns=\"http://www.w3.org/2000/svg\" aria-hidden=\"true\" focusable=\"false\" "
        f"style=\"color: {color};\">"
        f"<polyline fill=\"none\" stroke=\"currentColor\" "
        f"stroke-width=\"{stroke_width}\" points=\"{' '.join(points)}\" />"
        "</svg>"
    )


__all__ = ["create_sparkline_svg"]
