"""Shared helpers for ./veyra.sh's in-network tools (veyra_check, demo/walkthrough, bench/load_raw).

They run inside the compose ``tools`` container, so services are reached by their compose
names (kafka:9092, control-api:8000, caddy:8080 ...). Nothing here is used by the services.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Callable, Iterable
from typing import Any

import httpx
from confluent_kafka import Consumer, TopicPartition

from veyra_common.kafka import make_consumer

COLOR = sys.stdout.isatty() or os.environ.get("VEYRA_COLOR") == "1"


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def bold(text: str) -> str:
    return _c("1", text)


def dim(text: str) -> str:
    return _c("2", text)


def green(text: str) -> str:
    return _c("32", text)


def red(text: str) -> str:
    return _c("31", text)


def yellow(text: str) -> str:
    return _c("33", text)


def cyan(text: str) -> str:
    return _c("36", text)


class Results:
    """PASS / WARN / FAIL lines with a final tally and an exit code."""

    def __init__(self) -> None:
        self.counts = {"PASS": 0, "WARN": 0, "FAIL": 0}

    def report(self, status: str, name: str, detail: str = "") -> bool:
        self.counts[status] += 1
        badge = {"PASS": green, "WARN": yellow, "FAIL": red}[status](f"{status:<4}")
        print(f"  {badge}  {name}" + (f"  {dim(detail)}" if detail else ""), flush=True)
        return status != "FAIL"

    def check(self, name: str, ok: bool, detail: str = "", *, warn_only: bool = False) -> bool:
        return self.report("PASS" if ok else ("WARN" if warn_only else "FAIL"), name, detail)

    def summary(self) -> int:
        c = self.counts
        line = f"{c['PASS']} passed, {c['WARN']} warnings, {c['FAIL']} failed"
        print("\n  " + (red(line) if c["FAIL"] else green(line)))
        return 1 if c["FAIL"] else 0


def http(timeout: float = 10.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True)


def wait_until(
    predicate: Callable[[], Any], *, timeout: float, interval: float = 1.0, label: str = ""
) -> Any:
    """Poll ``predicate`` until it returns something truthy; show a countdown when labelled."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = predicate()
        except Exception:
            value = None
        if value:
            if label:
                print(" " * 70, end="\r", flush=True)
            return value
        left = deadline - time.monotonic()
        if left <= 0:
            if label:
                print(" " * 70, end="\r", flush=True)
            return None
        if label:
            print(f"    {dim(label)} {int(left):>3}s", end="\r", flush=True)
        time.sleep(interval)


def tail_consumer(prefixes: Iterable[str], exact: Iterable[str] = ()) -> Consumer:
    """A consumer pinned to the current end of every matching topic: only new records."""
    prefixes, exact = tuple(prefixes), set(exact)
    consumer = make_consumer(f"veyra-tools-{os.getpid()}-{time.time_ns()}", None)
    metadata = consumer.list_topics(timeout=20)
    positions = []
    for topic, meta in metadata.topics.items():
        if not (topic.startswith(prefixes) or topic in exact):
            continue
        for partition in meta.partitions:
            _, high = consumer.get_watermark_offsets(
                TopicPartition(topic, partition), timeout=10, cached=False
            )
            positions.append(TopicPartition(topic, partition, high))
    consumer.assign(positions)
    return consumer


def collect(
    consumer: Consumer,
    match: Callable[[str, dict[str, Any]], bool],
    *,
    timeout: float,
    want: int = 1,
) -> list[tuple[str, dict[str, Any]]]:
    """Records (topic, payload) for which ``match`` is true, until ``want`` or the timeout.

    Batched on purpose. A one-message-per-call ``poll(0.5)`` spends most of its budget on the
    end-of-partition signals that an assigned-at-the-watermark consumer gets from every
    partition, so a 60 s window saw only a couple of dozen records out of thousands — which
    reads as "the normalizer produced nothing" when it produced plenty.
    """
    found: list[tuple[str, dict[str, Any]]] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(found) < want:
        for message in consumer.consume(num_messages=500, timeout=0.2):
            if len(found) >= want:
                break
            if message is None or message.error():
                continue
            topic, value = message.topic(), message.value()
            if topic is None or value is None:  # a tombstone, or a built-not-consumed message
                continue
            try:
                payload = json.loads(value)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict) and match(topic, payload):
                found.append((topic, payload))
    return found


def topic_totals(prefixes: Iterable[str] = ("",)) -> dict[str, int]:
    """End offset summed over partitions, per topic (a cheap "how many records ever")."""
    prefixes = tuple(prefixes)
    consumer = make_consumer(f"veyra-tools-count-{os.getpid()}", None)
    try:
        totals: dict[str, int] = {}
        for topic, meta in consumer.list_topics(timeout=20).topics.items():
            if topic.startswith("__") or not topic.startswith(prefixes):
                continue
            total = 0
            for partition in meta.partitions:
                _, high = consumer.get_watermark_offsets(
                    TopicPartition(topic, partition), timeout=10, cached=False
                )
                total += high
            totals[topic] = total
        return totals
    finally:
        consumer.close()


def pause(auto: bool, prompt: str = "press Enter to continue") -> None:
    if auto or not sys.stdin.isatty():
        return
    with contextlib.suppress(EOFError):
        input(dim(f"    ... {prompt} "))
