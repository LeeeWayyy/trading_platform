from __future__ import annotations

from apps.web_console_ng.components.depth_visualizer import DepthVisualizer


def test_depth_visualizer_orders_levels() -> None:
    visualizer = DepthVisualizer(levels=2)
    payload = visualizer.build_payload(
        {
            "S": "AAPL",
            "b": [{"p": 10.0, "s": 100}, {"p": 11.0, "s": 50}],
            "a": [{"p": 12.0, "s": 60}, {"p": 11.5, "s": 40}],
        }
    )

    assert payload is not None
    assert [level["price"] for level in payload["bids"]] == [11.0, 10.0]
    assert [level["price"] for level in payload["asks"]] == [11.5, 12.0]
    assert payload["mid"] == (11.0 + 11.5) / 2


def test_depth_visualizer_flags_large_orders() -> None:
    visualizer = DepthVisualizer(levels=1, history_size=4, large_multiplier=2.0)
    payload1 = visualizer.build_payload({"S": "AAPL", "b": [{"p": 10, "s": 10}]})
    payload2 = visualizer.build_payload({"S": "AAPL", "b": [{"p": 10, "s": 30}]})

    assert payload1 is not None
    assert payload2 is not None
    assert payload2["bids"][0]["is_large"] is True
