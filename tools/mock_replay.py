"""Publish `replay.raw` messages the way control-api's replay job does (A5 scaffolding).

**A-owned scaffolding, not production code.** C2's `POST /replay` is the real thing, but it resolves
events through B4's evidence-api, so it cannot run until B is up. This tool reads envelopes straight
off `raw.*` and republishes them with the IF-ENVELOPE ``replay`` block, which is exactly what the
normalizer sees either way — so A5's replay semantics can be tested standalone.

    uv run python tools/mock_replay.py --source src_authsrv_01 --limit 8
    uv run python tools/mock_replay.py --event-uid 0192a4f0-… --revision 3
    uv run python tools/mock_replay.py --contains a.sharma --limit 20 --list

``--contains`` matches the **decoded** raw bytes, not the envelope JSON, because the bytes travel
base64-encoded — which is also why "replay this user's events" is not a grep over the topic.

The ``revision`` matters: control-api sends ``latest + 1`` per event, computed from the lineage
index, and ``supersedes`` is ``"<event_uid>@<latest>"``. This tool defaults to revision 2 (a first
replay of a never-replayed event) and takes ``--revision`` for later ones. Nothing here invents an
``event_uid`` — a replay must re-emit the *same* event under a higher revision, which is the whole
point of the mechanism.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from typing import Any

from confluent_kafka import TopicPartition

from veyra_common.kafka import make_consumer, make_producer
from veyra_common.models import Envelope, ReplayEnvelope
from veyra_common.settings import Settings
from veyra_common.topics import RAW_PREFIX, TOPIC_REPLAY_RAW


def collect(
    cfg: Settings,
    *,
    source_id: str | None,
    event_uid: str | None,
    contains: str | None,
    limit: int,
) -> list[Envelope]:
    """Read `raw.*` from the beginning and return the newest matching envelopes, in order.

    Newest-wins per ``event_uid``: replaying the same bytes twice from one command would be a
    duplicate, not a replay.
    """
    consumer = make_consumer(f"mock-replay-{uuid.uuid4().hex[:8]}", cfg=cfg)
    found: dict[str, Envelope] = {}
    try:
        metadata = consumer.list_topics(timeout=20)
        positions = []
        for topic, meta in metadata.topics.items():
            if not topic.startswith(RAW_PREFIX):
                continue
            for partition in meta.partitions:
                low, _ = consumer.get_watermark_offsets(
                    TopicPartition(topic, partition), timeout=10, cached=False
                )
                positions.append(TopicPartition(topic, partition, low))
        if not positions:
            return []
        consumer.assign(positions)

        idle_until = time.monotonic() + 5
        while time.monotonic() < idle_until and len(found) < limit:
            message = consumer.poll(1.0)
            if message is None or message.error():
                continue
            idle_until = time.monotonic() + 5
            try:
                envelope = Envelope.model_validate_json(message.value())
            except Exception:
                continue
            if source_id is not None and envelope.source_id != source_id:
                continue
            if event_uid is not None and envelope.event_uid != event_uid:
                continue
            if contains is not None and contains.encode() not in envelope.raw_bytes:
                continue
            found[envelope.event_uid] = envelope
    finally:
        consumer.close()
    return list(found.values())[:limit]


def replay_message(envelope: Envelope, *, job_id: str, revision: int) -> ReplayEnvelope:
    """The same envelope with a replay block — every other field byte-for-byte unchanged.

    In particular ``custody`` and ``hec_meta`` are carried through: a batch-uploaded event that came
    back as ``realtime`` would have lost the one field that says it was back-filled.
    """
    return ReplayEnvelope.model_validate(
        {
            **envelope.model_dump(mode="json"),
            "replay": {
                "job_id": job_id,
                "revision": revision,
                "supersedes": f"{envelope.event_uid}@{revision - 1}",
            },
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="only replay this source's events")
    parser.add_argument("--event-uid", help="replay exactly this event")
    parser.add_argument(
        "--contains",
        metavar="TEXT",
        help="only events whose raw bytes contain this text (e.g. one user's events)",
    )
    parser.add_argument("--limit", type=int, default=8, help="how many events (default 8)")
    parser.add_argument("--revision", type=int, default=2, help="revision to stamp (default 2)")
    parser.add_argument("--job-id", help="replay job id (default: a fresh one)")
    parser.add_argument("--list", action="store_true", help="print what would be published")
    args = parser.parse_args()

    cfg = Settings()
    job_id = args.job_id or f"rj_{uuid.uuid4().hex[:8]}"
    envelopes = collect(
        cfg,
        source_id=args.source,
        event_uid=args.event_uid,
        contains=args.contains,
        limit=max(args.limit, 0),
    )
    if not envelopes:
        print("no matching envelopes on raw.* — is anything flowing?")
        return 1

    messages: list[ReplayEnvelope] = [
        replay_message(envelope, job_id=job_id, revision=args.revision) for envelope in envelopes
    ]
    if args.list:
        for message in messages:
            block: dict[str, Any] = message.replay.model_dump()
            print(f"{message.event_uid}  rev {block['revision']}  supersedes {block['supersedes']}")
        return 0

    producer = make_producer(cfg=cfg)
    for message in messages:
        producer.produce(
            TOPIC_REPLAY_RAW,
            key=message.source_id,
            value=json.dumps(message.model_dump(mode="json")).encode(),
        )
    remaining = producer.flush(30)
    if remaining:
        print(f"{remaining} message(s) unacknowledged — replay.raw did not accept them")
        return 1
    print(f"published {len(messages)} event(s) to {TOPIC_REPLAY_RAW} as job {job_id}")
    for message in messages:
        print(f"  {message.event_uid} -> revision {message.replay.revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
