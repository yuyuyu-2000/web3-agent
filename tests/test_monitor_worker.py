from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from chaincloud_agent_service.monitoring.models import MonitorRule, TransactionRecord
from chaincloud_agent_service.monitoring.worker import MonitorWorker, PostgresTransactionSource, matches
from chaincloud_agent_service.notification.service import NotificationResult


def _rule(**changes):
    values = dict(rule_id="rule-1", user_id="user-1", rule_type="address_transaction",
                  address="0xabc", min_amount=None, min_amount_usd=Decimal("100000"),
                  chain="ethereum", token="usdt", enabled=True,
                  notification_channel="feishu", created_at=datetime.now(timezone.utc),
                  last_triggered_at=None)
    values.update(changes)
    return MonitorRule(**values)


def _tx(**changes):
    values = dict(transaction_id="101", transaction_hash="0xhash", from_address="0xabc",
                  to_address="0xdef", amount=Decimal("100001"), amount_usd=Decimal("100001"),
                  chain="ethereum", token="usdt", occurred_at=datetime.now(timezone.utc), raw={})
    values.update(changes)
    return TransactionRecord(**values)


def test_rule_match_combines_address_threshold_chain_and_token():
    assert matches(_rule(), _tx()) is True
    assert matches(_rule(), _tx(amount_usd=Decimal("99999"))) is False
    assert matches(_rule(), _tx(to_address="0x1", from_address="0x2")) is False


def test_worker_scans_once_and_batches_all_rules():
    class Store:
        persisted = []
        def list_enabled_rules(self): return [_rule(), _rule(rule_id="rule-2", address="0xnone")]
        def cursor(self, worker_name): return "100"
        def persist_matches_and_cursor(self, worker_name, cursor, found):
            self.persisted = found
            return len(found)
        def pending_events(self): return []

    class Source:
        calls = 0
        def scan(self, cursor):
            self.calls += 1
            return [_tx()], "101"

    class Notifications:
        def send(self, *args): return NotificationResult(True)

    store, source = Store(), Source()
    metrics = MonitorWorker(store, source, Notifications(), lambda *_: None).run_once()
    assert source.calls == 1
    assert len(store.persisted) == 1
    assert metrics["enabled_rules"] == 2
    assert metrics["matched_rules"] == 1


def test_justlend_profile_uses_composite_cursor_and_normalizes_schema():
    source = PostgresTransactionSource(
        "postgresql://unused", table="justlend", columns={}, batch_size=1000
    )
    cursor = source._justlend_cursor(89235009831089, 2)
    assert source.profile == "justlend"
    assert source._parse_justlend_cursor(cursor) == (89235009831089, 2)
    assert source._parse_justlend_cursor("89235009831089") == (89235009831089, -1)

    tx = source._justlend_transaction({
        "tx_seq": 89235009831089,
        "event_index": 2,
        "hash": "ABCDEF",
        "from_address": "TFrom",
        "to_address": "TTo",
        "amount": 81852.695,
        "amount_usd": 81852.695,
        "token": "USDT",
        "occurred_at": "2026-08-06 07:58:15.000 +0800",
    })
    assert tx.transaction_id == "89235009831089:2"
    assert tx.transaction_hash == "ABCDEF"
    assert tx.chain == "tron"
    assert tx.token == "usdt"
    assert tx.amount_usd == Decimal("81852.695")
    assert tx.occurred_at is not None
    assert tx.occurred_at.utcoffset() is not None


def test_justlend_composite_event_id_keeps_same_transaction_events_distinct():
    source = PostgresTransactionSource(
        "postgresql://unused", table="justlend", columns={}, batch_size=1000
    )
    base = {
        "tx_seq": 100,
        "hash": "HASH",
        "from_address": None,
        "to_address": None,
        "amount": 1,
        "amount_usd": 1,
        "token": "USDT",
        "occurred_at": None,
    }
    first = source._justlend_transaction({**base, "event_index": 1})
    second = source._justlend_transaction({**base, "event_index": 2})
    assert first.transaction_id == "100:1"
    assert second.transaction_id == "100:2"
