"""Every mutation writes IF-AUDIT to SQLite and to the `audit` topic (C1 AC5)."""

from __future__ import annotations

from control_api.audit import AuditLog
from control_api.tables import AuditRow
from sqlmodel import select

from veyra_common.envelope import rfc3339_ns
from veyra_common.models import AuditRecord


def test_record_writes_sqlite_and_the_audit_topic(db, producer, clock) -> None:
    log = AuditLog(producer, clock)
    record = log.record(
        db, actor="admin@veyra", role="admin", action="source.create", target="src_a",
        tenant_id="t_a",
    )  # fmt: skip
    db.commit()
    assert record.at == rfc3339_ns(clock())
    row = db.exec(select(AuditRow)).one()
    assert (row.actor, row.action, row.target, row.tenant_id) == (
        "admin@veyra",
        "source.create",
        "src_a",
        "t_a",
    )
    topic, key, value = producer.messages[-1]
    assert (topic, key) == ("audit", "admin@veyra")
    assert value is not None and AuditRecord.model_validate_json(value) == record


def test_list_is_newest_first_and_scoped_by_tenant(db, producer, clock) -> None:
    log = AuditLog(producer, clock)
    for target, tenant in (("1", "t_a"), ("2", "t_b"), ("3", "*")):
        log.record(db, actor="x", role="admin", action="a", target=target, tenant_id=tenant)
        clock.advance(1)
    db.commit()
    assert [r.target for r in log.list(db, tenant_id=None, limit=10)] == ["3", "2", "1"]
    assert [r.target for r in log.list(db, tenant_id="t_a", limit=10)] == ["1"]
    assert len(log.list(db, tenant_id=None, limit=2)) == 2
