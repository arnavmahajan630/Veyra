"""The single publisher of the compacted ``control`` topic (IF-CONTROL).

Every call flushes before returning, so a caller can promise "consumers see the change
within 1 s" (C1) and roll back its SQLite transaction when the broker doesn't answer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol

from pydantic import BaseModel

from veyra_common.topics import TOPIC_CONTROL


class ProducerLike(Protocol):
    def produce(
        self,
        topic: str,
        value: bytes | None = ...,
        key: str | bytes | None = ...,
        on_delivery: Callable[[Any, Any], None] | None = ...,
    ) -> None: ...

    def flush(self, timeout: float = ...) -> int: ...


class PublishError(RuntimeError):
    """One or more control messages were not acknowledged."""


class ControlPublisher:
    def __init__(
        self, producer: ProducerLike, *, timeout_s: float, topic: str = TOPIC_CONTROL
    ) -> None:
        self._producer = producer
        self._timeout_s = timeout_s
        self._topic = topic

    def publish(self, key: str, message: BaseModel | None) -> None:
        """Publish one key; ``None`` is a tombstone."""
        self.publish_many([(key, message)])

    def publish_many(self, items: Iterable[tuple[str, BaseModel | None]]) -> int:
        errors: list[str] = []

        def on_delivery(err: Any, _msg: Any) -> None:
            if err is not None:
                errors.append(str(err))

        count = 0
        for key, message in items:
            value = None if message is None else message.model_dump_json().encode("utf-8")
            self._producer.produce(self._topic, value=value, key=key, on_delivery=on_delivery)
            count += 1
        pending = self._producer.flush(self._timeout_s)
        if pending or errors:
            raise PublishError(
                f"{pending} control message(s) unacknowledged; delivery errors: {errors}"
            )
        return count

    def flush(self) -> None:
        self._producer.flush(self._timeout_s)
