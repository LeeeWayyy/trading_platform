"""Unit tests for FallbackBuffer."""

from libs.core.redis_client.fallback_buffer import FallbackBuffer


def test_buffer_preserves_fifo_order_on_replay():
    buffer = FallbackBuffer(max_size=10)

    buffer.buffer_message("signals.generated", "first")
    buffer.buffer_message("signals.generated", "second")
    buffer.buffer_message("signals.generated", "third")

    published: list[tuple[str, str]] = []

    def publish(channel: str, payload: str) -> None:
        published.append((channel, payload))

    replayed = buffer.replay(publish)

    assert replayed == 3
    assert published == [
        ("signals.generated", "first"),
        ("signals.generated", "second"),
        ("signals.generated", "third"),
    ]
    assert buffer.size == 0


def test_buffer_drops_oldest_when_full():
    buffer = FallbackBuffer(max_size=2)

    buffer.buffer_message("signals.generated", "first")
    buffer.buffer_message("signals.generated", "second")
    outcome = buffer.buffer_message("signals.generated", "third")

    assert outcome.dropped == 1
    snapshot = [entry.payload for entry in buffer.snapshot()]
    assert snapshot == ["second", "third"]


def test_replay_stops_on_publish_failure():
    buffer = FallbackBuffer(max_size=5)

    buffer.buffer_message("signals.generated", "first")
    buffer.buffer_message("signals.generated", "second")
    buffer.buffer_message("signals.generated", "third")

    published: list[str] = []

    def publish(channel: str, payload: str) -> None:
        if payload == "second":
            raise RuntimeError("publish failed")
        published.append(payload)

    replayed = buffer.replay(publish)

    assert replayed == 1
    assert published == ["first"]
    snapshot = [entry.payload for entry in buffer.snapshot()]
    assert snapshot == ["second", "third"]


def test_persistence_round_trip(tmp_path):
    persist_path = tmp_path / "buffer.json"

    buffer = FallbackBuffer(max_size=10, persist_path=persist_path)
    buffer.buffer_message("signals.generated", "persisted")

    reloaded = FallbackBuffer(max_size=10, persist_path=persist_path)
    snapshot = [entry.payload for entry in reloaded.snapshot()]

    assert snapshot == ["persisted"]
