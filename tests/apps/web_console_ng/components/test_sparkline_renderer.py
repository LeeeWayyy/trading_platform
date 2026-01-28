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
