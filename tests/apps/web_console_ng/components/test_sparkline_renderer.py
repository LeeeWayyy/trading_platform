from apps.web_console_ng.components.sparkline_renderer import create_sparkline_svg


def test_sparkline_empty_returns_empty() -> None:
    assert create_sparkline_svg([]) == ""


def test_sparkline_trend_up_color() -> None:
    svg = create_sparkline_svg([1.0, 2.0, 3.0], width=30, height=10)
    assert "sparkline" in svg
    assert "var(--profit)" in svg
    assert "polyline" in svg


def test_sparkline_trend_down_color() -> None:
    svg = create_sparkline_svg([3.0, 2.0, 1.0], width=30, height=10)
    assert "sparkline" in svg
    assert "var(--loss)" in svg
    assert "polyline" in svg


def test_sparkline_single_point_duplicates() -> None:
    svg = create_sparkline_svg([5.0], width=20, height=10)
    assert "polyline" in svg
    assert svg.count(",") >= 2


def test_sparkline_flat_series_has_span() -> None:
    svg = create_sparkline_svg([2.0, 2.0, 2.0], width=30, height=10)
    assert "polyline" in svg
