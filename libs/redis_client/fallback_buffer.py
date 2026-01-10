"""
Fallback buffer for Redis publish failures.

Provides a FIFO in-memory buffer with optional disk persistence so that
signals are not lost when Redis is unavailable. Buffered messages can be
replayed in order when Redis connectivity is restored.
"""

import json
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BufferedMessage:
    """A buffered Redis pub/sub message."""

    channel: str
    payload: str
    created_at: str


@dataclass(frozen=True)
class BufferOutcome:
    """Result of buffering a message."""

    buffered: int
    dropped: int
    size: int


class FallbackBuffer:
    """
    FIFO buffer for Redis publish failures.

    Attributes:
        max_size: Maximum number of messages to retain
        persist_path: Optional file path for disk persistence
    """

    def __init__(self, max_size: int = 1000, persist_path: str | Path | None = None):
        if max_size <= 0:
            raise ValueError("max_size must be positive")

        self._max_size = max_size
        self._buffer: deque[BufferedMessage] = deque()
        self._lock = Lock()
        self._persist_path = Path(persist_path) if persist_path else None

        if self._persist_path is not None:
            try:
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(
                    "Failed to create fallback buffer directory '%s': %s. "
                    "Disk persistence disabled.",
                    self._persist_path.parent,
                    exc,
                )
                self._persist_path = None
            else:
                self._load_persisted()

    @property
    def max_size(self) -> int:
        """Return the configured maximum buffer size."""

        return self._max_size

    @property
    def size(self) -> int:
        """Return the current buffer size."""

        with self._lock:
            return len(self._buffer)

    def buffer_message(self, channel: str, payload: str) -> BufferOutcome:
        """
        Buffer a message when Redis publish fails.

        Returns:
            BufferOutcome with counts for buffered and dropped messages.
        """

        dropped = 0
        entry = BufferedMessage(
            channel=channel,
            payload=payload,
            created_at=datetime.now(UTC).isoformat(),
        )

        with self._lock:
            if len(self._buffer) >= self._max_size:
                self._buffer.popleft()
                dropped = 1
            self._buffer.append(entry)
            size = len(self._buffer)

        if dropped:
            logger.warning("Fallback buffer full; dropped oldest buffered message")

        self._persist()
        return BufferOutcome(buffered=1, dropped=dropped, size=size)

    def replay(self, publish: Callable[[str, str], None]) -> int:
        """
        Replay buffered messages in FIFO order.

        Args:
            publish: Callable that publishes (channel, payload). Raises on failure.

        Returns:
            Number of messages replayed successfully.
        """

        replayed = 0
        while True:
            with self._lock:
                if not self._buffer:
                    break
                entry = self._buffer[0]

            try:
                publish(entry.channel, entry.payload)
            except Exception as exc:
                logger.warning("Fallback replay halted after %s messages: %s", replayed, exc)
                break

            replayed += 1
            with self._lock:
                if self._buffer and self._buffer[0] == entry:
                    self._buffer.popleft()
                else:
                    try:
                        self._buffer.remove(entry)
                    except ValueError:
                        # Entry already removed by concurrent operation
                        logger.debug("Buffer entry already removed during replay")
            self._persist()

        return replayed

    def snapshot(self) -> list[BufferedMessage]:
        """Return a snapshot of buffered messages for inspection/testing."""

        with self._lock:
            return list(self._buffer)

    def _load_persisted(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return

        try:
            raw = self._persist_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load fallback buffer from '%s': %s", self._persist_path, exc)
            return

        if not isinstance(data, list):
            logger.warning(
                "Fallback buffer file '%s' has invalid format (expected list)",
                self._persist_path,
            )
            return

        loaded: list[BufferedMessage] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            channel = item.get("channel")
            payload = item.get("payload")
            created_at = item.get("created_at")
            if not isinstance(channel, str) or not isinstance(payload, str):
                continue
            if not isinstance(created_at, str):
                created_at = datetime.now(UTC).isoformat()
            loaded.append(
                BufferedMessage(
                    channel=channel,
                    payload=payload,
                    created_at=created_at,
                )
            )

        if len(loaded) > self._max_size:
            logger.warning(
                "Fallback buffer file '%s' has %s entries; truncating to %s",
                self._persist_path,
                len(loaded),
                self._max_size,
            )
            loaded = loaded[-self._max_size :]

        with self._lock:
            self._buffer = deque(loaded)

    def _persist(self) -> None:
        if self._persist_path is None:
            return

        with self._lock:
            data = [
                {
                    "channel": entry.channel,
                    "payload": entry.payload,
                    "created_at": entry.created_at,
                }
                for entry in self._buffer
            ]

        tmp_path = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
        try:
            tmp_path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
            tmp_path.replace(self._persist_path)
        except OSError as exc:
            logger.warning("Failed to persist fallback buffer to '%s': %s", tmp_path, exc)
