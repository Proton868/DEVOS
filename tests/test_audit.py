"""Test AuditLogger — immutable audit trail with querying and statistics."""
import pytest
from datetime import datetime, timezone

from governance.audit import (
    AuditLogger, AuditEventType, AuditEntry, get_audit_logger,
)


class TestAuditEntry:
    def test_audit_entry_to_dict(self):
        entry = AuditEntry(
            event_id="ev-1",
            event_type=AuditEventType.CAPABILITY_INVOKE,
            actor_id="user-1",
            tenant_id="tenant-1",
            timestamp=datetime.now(timezone.utc),
            action="write_python",
            target="script.py",
            outcome="success",
            details={"lines": 10},
            trace_id="trace-1",
        )
        d = entry.to_dict()
        assert d["event_id"] == "ev-1"
        assert d["event_type"] == "capability.invoke"
        assert d["actor_id"] == "user-1"
        assert d["details"]["lines"] == 10


class TestAuditLoggerSingleton:
    def test_singleton(self):
        a1 = AuditLogger()
        a2 = AuditLogger()
        assert a1 is a2

    def test_module_level_getter(self):
        a1 = get_audit_logger()
        a2 = get_audit_logger()
        assert a1 is a2


class TestAuditLoggerLog:
    def test_log_and_query(self):
        audit = get_audit_logger()
        event_id = audit.log(
            event_type=AuditEventType.CAPABILITY_INVOKE,
            actor_id="test-user",
            tenant_id="test-tenant",
            action="write_python",
            target="script.py",
            outcome="success",
        )
        assert event_id is not None
        assert len(event_id) > 0

        entries = audit.query(actor_id="test-user", limit=1)
        assert len(entries) > 0
        assert entries[0]["event_id"] == event_id
        assert entries[0]["event_type"] == "capability.invoke"

    def test_log_with_details(self):
        audit = get_audit_logger()
        event_id = audit.log(
            event_type=AuditEventType.RBAC_CHECK,
            actor_id="test-user",
            tenant_id="test-tenant",
            action="check_permission",
            outcome="allow",
            details={"capability": "ucip:execution.python", "tier": "tenant_user"},
            trace_id="trace-123",
        )
        entries = audit.query(actor_id="test-user", limit=1)
        assert len(entries) > 0
        assert entries[0]["details"].get("capability") == "ucip:execution.python"
        assert entries[0]["trace_id"] == "trace-123"


class TestAuditLoggerQuery:
    def test_query_by_event_type(self):
        audit = get_audit_logger()
        audit.log(
            event_type=AuditEventType.RBAC_CHECK,
            actor_id="test-user", tenant_id="test-tenant",
            action="check", outcome="allow",
        )
        entries = audit.query(event_type=AuditEventType.RBAC_CHECK, limit=10)
        assert len(entries) > 0
        for e in entries:
            assert e["event_type"] == "rbac.check"

    def test_query_with_offset(self):
        audit = get_audit_logger()
        # Get with offset beyond results
        entries = audit.query(offset=99999, limit=10)
        assert entries == []

    def test_query_no_filters(self):
        audit = get_audit_logger()
        entries = audit.query(limit=10)
        assert isinstance(entries, list)


class TestAuditLoggerStats:
    def test_stats(self):
        audit = get_audit_logger()
        stats = audit.stats()
        assert "total_entries" in stats
        assert "by_type" in stats
        assert isinstance(stats["total_entries"], int)
        assert isinstance(stats["by_type"], dict)

    def test_stats_by_tenant(self):
        audit = get_audit_logger()
        audit.log(
            event_type=AuditEventType.CAPABILITY_INVOKE,
            actor_id="user", tenant_id="tenant-stats",
            action="test", outcome="ok",
        )
        stats = audit.stats(tenant_id="tenant-stats")
        assert stats["total_entries"] >= 1