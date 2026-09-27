"""IF-AUDIT: every human action in the control plane, in SQLite and on the `audit` topic.

The SQLite row joins the caller's transaction. The topic message is produced now and
delivered by the next flush: every mutation's control publish flushes, and so does
shutdown.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlmodel import Session as DbSession
from sqlmodel import col, select

from control_api.publisher import ProducerLike
from control_api.tables import PLATFORM, AuditRow
from veyra_common.envelope import rfc3339_ns
from veyra_common.models import AuditRecord
from veyra_common.topics import TOPIC_AUDIT


class AuditLog:
    def __init__(
        self, producer: ProducerLike, clock: Callable[[], int], topic: str = TOPIC_AUDIT
    ) -> None:
        self._producer = producer
        self._clock = clock
        self._topic = topic

    def record(
        self,
        db: DbSession,
        *,
        actor: str,
        role: str,
        action: str,
        target: str,
        detail: str = "",
        tenant_id: str = PLATFORM,
    ) -> AuditRecord:
        record = AuditRecord(
            actor=actor,
            role=role,
            action=action,
            target=target,
            detail=detail,
            at=rfc3339_ns(self._clock()),
        )
        db.add(AuditRow(**record.model_dump(), tenant_id=tenant_id))
        self._producer.produce(
            self._topic, value=record.model_dump_json().encode("utf-8"), key=actor
        )
        return record

    def list(self, db: DbSession, *, tenant_id: str | None, limit: int) -> list[AuditRow]:
        query = select(AuditRow).order_by(col(AuditRow.id).desc()).limit(limit)
        if tenant_id is not None:
            query = query.where(col(AuditRow.tenant_id) == tenant_id)
        return list(db.exec(query).all())
